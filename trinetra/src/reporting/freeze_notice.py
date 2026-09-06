# src/reporting/freeze_notice.py

import os
from datetime import datetime, timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

OUTPUT_DIR = "data/processed/freeze_notices"


def build_hop_path_to_terminus(root_node, terminus_address):
    """
    Walk the tree and find the exact sequence of hops from the victim
    address down to this specific terminus -- this becomes the
    'full hop path with transaction hashes' evidence trail in the PDF.
    """
    path = []

    def dfs(node, trail):
        trail = trail + [node]
        if node.address == terminus_address:
            path.extend(trail)
            return True
        for child in node.children:
            if dfs(child, trail):
                return True
        return False

    dfs(root_node, [])
    return path


def generate_freeze_notice_pdf(candidate, suspect_address, hop_path, case_id=None):
    """
    Generate one freeze/preservation notice PDF for a single exchange
    terminus. One PDF is created per terminus, since a fan-out trace
    can legitimately end at multiple exchanges.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    case_id = case_id or f"TRINETRA-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    filename = f"{OUTPUT_DIR}/freeze_notice_{case_id}_{candidate['address'][:10]}.pdf"

    doc = SimpleDocTemplate(filename, pagesize=A4,
                             topMargin=2*cm, bottomMargin=2*cm,
                             leftMargin=2*cm, rightMargin=2*cm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('TitleStyle', parent=styles['Title'], fontSize=16, spaceAfter=12)
    heading_style = ParagraphStyle('HeadingStyle', parent=styles['Heading2'], spaceBefore=14, spaceAfter=6)
    body_style = styles['BodyText']
    disclaimer_style = ParagraphStyle(
        'Disclaimer', parent=styles['BodyText'],
        fontSize=9, textColor=colors.HexColor("#555555"), spaceBefore=6
    )

    elements = []

    # --- Header ---
    elements.append(Paragraph("FUND PRESERVATION / FREEZE REQUEST", title_style))
    elements.append(Paragraph(f"Case Reference: {case_id}", body_style))
    elements.append(Paragraph(f"Generated: {datetime.now(timezone.utc).isoformat()}", body_style))
    elements.append(Spacer(1, 12))

    # --- Case summary ---
    elements.append(Paragraph("1. Case Summary", heading_style))
    elements.append(Paragraph(
        f"This notice concerns funds originating from the victim-reported "
        f"suspect wallet <b>{suspect_address}</b>, traced forward on-chain to "
        f"a deposit address associated with the platform identified below.", body_style
    ))
    elements.append(Spacer(1, 8))

    # --- Terminus details ---
    elements.append(Paragraph("2. Identified Terminus", heading_style))
    terminus_table_data = [
        ["Field", "Value"],
        ["Deposit / Hot Wallet Address", candidate['address']],
        ["Platform Label", str(candidate['exchange'])],
        ["Traced Amount (USDT)", f"{candidate['amount']:.2f}" if candidate['amount'] else "N/A"],
        ["Confidence Score", f"{candidate['confidence']:.2f}"],
        ["Evidence Transaction Hash", candidate['evidence_hash'] or "N/A"],
    ]
    terminus_table = Table(terminus_table_data, colWidths=[6*cm, 10*cm])
    terminus_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
    ]))
    elements.append(terminus_table)
    elements.append(Spacer(1, 8))

    # --- Full hop path evidence ---
    elements.append(Paragraph("3. Full Hop Path (Reproducible Evidence)", heading_style))
    elements.append(Paragraph(
        "Every hop below is independently verifiable against the public TRON "
        "blockchain using the listed transaction hash.", body_style
    ))
    elements.append(Spacer(1, 6))

    hop_table_data = [["Hop", "Address", "Amount (USDT)", "Transaction Hash"]]
    for i, node in enumerate(hop_path):
        amount = f"{node.incoming_tx['amount']:.2f}" if node.incoming_tx else "-"
        tx_hash = node.incoming_tx['hash'] if node.incoming_tx else "-"
        hop_table_data.append([str(i), node.address, amount, tx_hash])

    hop_table = Table(hop_table_data, colWidths=[1.5*cm, 6*cm, 3*cm, 5.5*cm])
    hop_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
    ]))
    elements.append(hop_table)
    elements.append(Spacer(1, 12))

    # --- Requested action ---
    elements.append(Paragraph("4. Requested Action", heading_style))
    elements.append(Paragraph(
        "The receiving platform is requested to preserve all records and, "
        "where legally permissible, place a hold on the identified deposit "
        "address / associated account pending formal law enforcement request "
        "or subpoena.", body_style
    ))
    elements.append(Spacer(1, 12))

    # --- Legal disclaimer (critical -- matches the honesty-layer discussed earlier) ---
    elements.append(Paragraph("5. Important Notice", heading_style))
    elements.append(Paragraph(
        "This report identifies a wallet address exhibiting deposit-address "
        "behavior with the stated confidence score. It does NOT constitute "
        "confirmed identification of any individual. A deposit address "
        "identifies an account, not a person. Final identity attribution "
        "requires a formal request to the platform's compliance team or a "
        "law enforcement subpoena. This is a lead for investigation, not a "
        "legal accusation.", disclaimer_style
    ))

    doc.build(elements)
    print(f"Freeze notice saved: {filename}")
    return filename


def generate_all_freeze_notices(report, root_node):
    """
    Loop through every freeze_notice_candidate in the report and
    generate a separate PDF for each one.
    """
    generated_files = []
    for candidate in report['freeze_notice_candidates']:
        hop_path = build_hop_path_to_terminus(root_node, candidate['address'])
        filepath = generate_freeze_notice_pdf(candidate, report['suspect_address'], hop_path)
        generated_files.append(filepath)

    if not generated_files:
        print("No high-confidence terminus found -- no freeze notices generated.")

    return generated_files


if __name__ == "__main__":
    from src.tracing.tree_trace import run_full_trace, summarise_trace_status
    from src.reporting.generate_report import generate_report

    suspect_address = "TWXLTtvZKonEmYA2NNLSw5goGooeWT7Vj9"
    tree = run_full_trace(suspect_address)
    report = generate_report(tree, suspect_address, summarise_trace_status)

    generate_all_freeze_notices(report, tree)