# src/app/dashboard.py

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
import streamlit.components.v1 as components

from src.tracing.tree_trace import run_full_trace, run_full_trace_from_tx_id, summarise_trace_status, assign_role
from src.tracing.plotly_fundflow import build_plotly_fundflow
from src.heuristics.deposit_heuristic import run_deposit_heuristic
from src.heuristics.pattern_detection import run_pattern_detection
from src.features.feature_engineering import build_address_features
from src.model.hybrid_classify import classify_address, get_heuristic_result
from src.model.predict import predict_address_type, load_model
from src.utils.cleanup import clear_live_trace_temp_data
from src.reporting.generate_report import generate_report
from src.reporting.freeze_notice import generate_all_freeze_notices
import json

st.set_page_config(page_title="Trinetra — Fund Intelligence", layout="wide", page_icon="🔍")

# ---------------- Custom dark theme styling ----------------
st.markdown("""
<style>
    .stApp { background-color: #0e1117; }
    .metric-card {
        background-color: #1a1d24; padding: 16px; border-radius: 10px;
        border: 1px solid #2a2e37;
    }
    .risk-badge-high { background-color: #e74c3c; padding: 4px 10px; border-radius: 6px; color: white; font-weight: bold; }
    .risk-badge-medium { background-color: #f39c12; padding: 4px 10px; border-radius: 6px; color: white; font-weight: bold; }
    .risk-badge-low { background-color: #2ecc71; padding: 4px 10px; border-radius: 6px; color: white; font-weight: bold; }
</style>
""", unsafe_allow_html=True)


# ---------------- Header ----------------
col_logo, col_title = st.columns([1, 8])
with col_logo:
    st.markdown("### 🛡️")
with col_title:
    st.title("Trinetra — Fund Intelligence")
    st.caption("Enter the victim-reported SUSPECT wallet address to trace fund flow, detect laundering patterns, and get a risk score.")

st.divider()

# ---------------- Input form ----------------
st.subheader("🔎 Start a Trace")

entry_mode = st.radio(
    "How do you want to start the trace?",
    options=["Suspect wallet address", "Exact transaction ID (more precise)"],
    horizontal=True,
    help="A transaction ID pinpoints the exact payment being reported -- "
         "no ambiguity about which of the suspect's many transactions is relevant, "
         "and it also reveals the true victim sender address."
)

input_col1, input_col2, input_col3, input_col4 = st.columns([3, 2, 2, 1])

with input_col1:
    if entry_mode == "Suspect wallet address":
        address_input = st.text_input("Suspect wallet address (victim-reported)", placeholder="TXYZ...")
    else:
        address_input = st.text_input("Transaction hash / ID", placeholder="e.g. a1b2c3...")
with input_col2:
    payment_datetime = st.datetime_input(
        "Victim payment date & time",
        value=datetime.now() - timedelta(days=1)
    ) if hasattr(st, 'datetime_input') else st.text_input("Victim payment date & time (YYYY-MM-DD HH:MM)", value=str(datetime.now() - timedelta(days=1)))
with input_col3:
    day_limit = st.slider("Trace window (days after payment)", 1, 30, 7)
with input_col4:
    st.write("")
    st.write("")
    run_trace_btn = st.button("🚀 Run Trace", use_container_width=True)

# Quick-action buttons (like the reference image)
st.write("")
btn_col1, btn_col2, btn_col3, btn_col4, btn_col5 = st.columns(5)
show_fundflow = btn_col1.button("Show fund flow", use_container_width=True)
detect_patterns = btn_col2.button("Detect patterns", use_container_width=True)
show_incoming = btn_col3.button("Show incoming sources", use_container_width=True)
show_outgoing = btn_col4.button("Show outgoing destinations", use_container_width=True)
explain_risk = btn_col5.button("Explain risk", use_container_width=True)

any_action = run_trace_btn or show_fundflow or detect_patterns or show_incoming or show_outgoing or explain_risk

st.divider()


# ---------------- Helper: risk score calculation ----------------
def compute_risk_score(summary, flagged_count, deposit_score, tree=None, entered_address_pattern=None):
    """
    Risk score (0-100) combining multiple independent signals so the
    score actually varies meaningfully case to case, instead of
    saturating quickly to a narrow band.

    Previous version: base=40, capped bonuses that maxed out with as
    few as 3 flagged addresses -- most real traces hit that cap
    immediately, producing scores clustered in a narrow ~79-85 band
    regardless of how different two cases actually were.
    """
    score = 20.0   # lower base so there's more room to differentiate upward

    # 1. How much of the traced value reached a confirmed exchange?
    # Counterintuitively, LOWER traceability can mean either a dead
    # trail (less certain) or successful evasion -- we treat unresolved
    # money as raising risk, since it suggests active evasion.
    percent_traced = summary.get('percent_traced_to_exchange', 0) or 0
    score += (100 - percent_traced) * 0.25

    # 2. Overall resolution status
    if summary.get('overall_status') == 'PARTIALLY_RESOLVED':
        score += 10

    # 3. Dead ends / mixers -- NOT capped as low as before, scales
    # more gradually so 1 vs 5 dead ends actually looks different
    dead_end_count = summary.get('dead_end_count', 0) or 0
    score += min(dead_end_count * 6, 25)

    # 4. Reaching a terminus is itself notable (an actual cash-out lead)
    terminus_count = summary.get('terminus_count', 0) or 0
    score += min(terminus_count * 4, 12)

    # 5. The ENTERED address's own deposit-heuristic score
    score += deposit_score * 12

    # 6. If the entered address itself matches a specific laundering
    # pattern (mixer/peel-chain/fan-out/smurfing), that's a strong
    # standalone risk signal
    high_risk_patterns = ('SUSPECTED_MIXER', 'PEEL_CHAIN', 'FAN_OUT_LAYERING', 'SMURFING')
    if entered_address_pattern in high_risk_patterns:
        score += 15

    return round(min(max(score, 0), 100), 1)


def risk_label(score):
    if score >= 70:
        return "HIGH RISK", "risk-badge-high", "#e74c3c"
    elif score >= 40:
        return "MEDIUM RISK", "risk-badge-medium", "#f39c12"
    else:
        return "LOW RISK", "risk-badge-low", "#2ecc71"


def draw_risk_gauge(score):
    color = risk_label(score)[2]
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        number={'suffix': "/100", 'font': {'color': 'white', 'size': 36}},
        gauge={
            'axis': {'range': [0, 100], 'tickcolor': "white"},
            'bar': {'color': color},
            'bgcolor': "#1a1d24",
            'steps': [
                {'range': [0, 40], 'color': '#1e3a2f'},
                {'range': [40, 70], 'color': '#4a3a1e'},
                {'range': [70, 100], 'color': '#4a1e1e'},
            ],
        }
    ))
    fig.update_layout(height=250, margin=dict(l=20, r=20, t=20, b=20),
                       paper_bgcolor="#0e1117", font={'color': "white"})
    return fig


# ---------------- Main trace logic ----------------
if any_action and address_input:

    with st.spinner("Fetching blockchain data and running trace..."):
        # Treat every new address as fresh -- wipe any leftover temp
        # data from the previous trace before starting this one, so
        # disk usage never accumulates and speed stays consistent.
        clear_live_trace_temp_data()

        if entry_mode == "Exact transaction ID (more precise)":
            tree = run_full_trace_from_tx_id(address_input)
        else:
            tree = run_full_trace(address_input)

        summary = summarise_trace_status(tree)

        # Build a PROPERLY connected transactions_df from the tree --
        # each edge needs the real parent (from_address), not None.
        # The earlier version hardcoded from_address=None, which meant
        # every address's out_degree was always 0, breaking the
        # deposit heuristic and ML features for every single address.
        all_rows = []
        def collect_rows(node, parent_address=None):
            if node.incoming_tx and parent_address is not None:
                all_rows.append({
                    'from_address': parent_address,
                    'to_address': node.address,
                    'amount': node.incoming_tx['amount'],
                    'timestamp': node.incoming_tx['timestamp'],
                    'hash': node.incoming_tx['hash']
                })
            for c in node.children:
                collect_rows(c, parent_address=node.address)
        collect_rows(tree, parent_address=None)
        transactions_df = pd.DataFrame(all_rows) if all_rows else pd.DataFrame(
            columns=['from_address', 'to_address', 'amount', 'timestamp', 'hash'])

        deposit_score = run_deposit_heuristic(address_input, transactions_df) if not transactions_df.empty else 0
        pattern_result = run_pattern_detection(address_input, transactions_df) if not transactions_df.empty else {
            'mixer_pattern_score': 0, 'pattern_label': 'NORMAL'}

        # Collect all flagged nodes across the tree
        flagged = []
        def collect_flagged(node, is_root=False):
            role = assign_role(node, is_root=is_root)
            if role in ('Mixer Wallet', 'Dead End Wallet') or node.status == 'terminus':
                flagged.append((node, role))
            for c in node.children:
                collect_flagged(c, is_root=False)
        collect_flagged(tree, is_root=True)

        risk_score = compute_risk_score(summary, len(flagged), deposit_score,
                                         entered_address_pattern=pattern_result.get('pattern_label'))
        label, badge_class, color = risk_label(risk_score)

    # ---------------- Top summary row ----------------
    st.subheader("📊 Trace Summary")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Overall Status", summary['overall_status'].replace('_', ' ').title())
    m2.metric("% Traced to Exchange", f"{summary['percent_traced_to_exchange']}%")
    m3.metric("Terminus Found", summary['terminus_count'])
    m4.metric("Dead Ends / Mixers", summary['dead_end_count'])

    st.write("")

    # ---------------- Risk score + fund flow graph ----------------
    risk_col, graph_col = st.columns([1, 2])

    with risk_col:
        st.subheader("⚠️ Risk Score")
        st.plotly_chart(draw_risk_gauge(risk_score), use_container_width=True)
        st.markdown(f'<span class="{badge_class}">{label}</span>', unsafe_allow_html=True)

        st.write("")
        st.subheader("Detected Patterns")
        if pattern_result['pattern_label'] != 'NORMAL':
            st.error(f"**{pattern_result['pattern_label'].replace('_', ' ').title()}** — score {pattern_result['mixer_pattern_score']:.2f}")
        if deposit_score > 0.6:
            st.success(f"**Deposit-address behavior** — confidence {deposit_score:.2f}")
        if pattern_result['pattern_label'] == 'NORMAL' and deposit_score <= 0.6:
            st.info("No strong suspicious pattern detected for this address directly.")

    with graph_col:
        st.subheader("🕸️ Fund Flow Graph")
        fig, convergence_points = build_plotly_fundflow(tree)
        if fig:
            st.plotly_chart(
                fig, use_container_width=True,
                config={'scrollZoom': True, 'displaylogo': False,
                        'modeBarButtonsToAdd': ['resetScale2d']}
            )
        else:
            st.info("No addresses to display -- this address may have no outgoing transactions.")

        if convergence_points:
            st.markdown("**⭐ Convergence Points Detected**")
            st.caption("These addresses receive funds from more than one separate branch -- "
                       "suggesting multiple mule chains lead back to one operation.")
            st.dataframe(pd.DataFrame(convergence_points), use_container_width=True, hide_index=True)

    st.divider()

    # ---------------- Why flagged table ----------------
    st.subheader("🚩 Why These Addresses Were Flagged")
    if flagged:
        flagged_data = []
        for node, role in flagged:
            flagged_data.append({
                'Address': node.address,
                'Role': role,
                'Reason Flagged': node.reason,
                'Confidence': f"{node.confidence:.2f}" if node.confidence else "N/A",
                'Amount (USDT)': f"{node.incoming_tx['amount']:.2f}" if node.incoming_tx else "N/A"
            })
        st.dataframe(pd.DataFrame(flagged_data), use_container_width=True, hide_index=True)
    else:
        st.info("No addresses were flagged as mixer, dead-end, or terminus in this trace.")

    st.divider()

    # ---------------- Per-address detail table ----------------
    st.subheader("📋 Per-Address Details")

    detail_rows = []
    def collect_details(node, is_root=False):
        role = assign_role(node, is_root=is_root)
        if role:
            feats = build_address_features(node.address, transactions_df) if not transactions_df.empty else {}
            detail_rows.append({
                'Address': node.address,
                'Role': role,
                'Amount Received (USDT)': node.incoming_tx['amount'] if node.incoming_tx else None,
                'Fan-in (in-degree)': feats.get('in_degree', 'N/A'),
                'Fan-out (out-degree)': feats.get('out_degree', 'N/A'),
                'Avg Forward Delay (sec)': round(feats.get('avg_forward_delay_sec'), 1) if feats.get('avg_forward_delay_sec') else 'N/A',
                'Address Age (days)': round(feats.get('address_age_days'), 1) if feats.get('address_age_days') else 'N/A',
                'Status': node.status,
            })
        for c in node.children:
            collect_details(c, is_root=False)
    collect_details(tree, is_root=True)

    st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)

    st.download_button(
        "⬇️ Download full trace report (CSV)",
        pd.DataFrame(detail_rows).to_csv(index=False),
        file_name=f"trinetra_trace_{address_input[:10]}.csv"
    )

    st.divider()

    # ---------------- Hop decision reasoning (WHY followed / pruned) ----------------
    st.subheader("🧭 Why Each Hop Was Followed or Not")
    st.caption("Every branch encountered during tracing is listed here -- including ones "
               "that were NOT actively traced further, with the exact reason.")

    reasoning_rows = []
    def collect_reasoning(node, depth=0, is_root=False):
        reasoning_rows.append({
            'Hop Depth': depth,
            'Address': node.address,
            'Amount (USDT)': f"{node.incoming_tx['amount']:.2f}" if node.incoming_tx else "—",
            'Value Share of Parent': f"{node.value_share:.1%}" if node.value_share is not None else "—",
            '% of Original Amount': f"{node.percent_of_original}%" if getattr(node, 'percent_of_original', None) is not None else "—",
            'Decision': node.status,
            'Reason': node.reason if node.reason else "Starting point of the trace."
        })
        for c in node.children:
            collect_reasoning(c, depth + 1, is_root=False)
    collect_reasoning(tree, is_root=True)

    reasoning_df = pd.DataFrame(reasoning_rows)

    decision_filter = st.multiselect(
        "Filter by decision type:",
        options=reasoning_df['Decision'].dropna().unique().tolist(),
        default=reasoning_df['Decision'].dropna().unique().tolist()
    )
    filtered_reasoning = reasoning_df[reasoning_df['Decision'].isin(decision_filter)]

    def highlight_decision(row):
        color_map = {
            'terminus': 'background-color: #1e3a2f',
            'dead_end': 'background-color: #3a1e1e',
            'followed': 'background-color: #1e2a3a',
            'pruned': 'background-color: #2a2a2a',
        }
        return [color_map.get(row['Decision'], '')] * len(row)

    st.dataframe(
        filtered_reasoning.style.apply(highlight_decision, axis=1),
        use_container_width=True, hide_index=True
    )

    st.divider()

    # ---------------- ML Model Insights (interactive) ----------------
    st.subheader("🤖 Machine Learning Predictions")
    st.caption("The ML layer never overrides the heuristic -- it either corroborates it, "
               "or gets flagged for manual review if they disagree.")

    ml_target_addresses = [row['Address'] for row in detail_rows]

    selected_ml_address = st.selectbox(
        "Choose an address to inspect its ML prediction:",
        options=ml_target_addresses,
        index=0
    )

    if selected_ml_address:
        try:
            model = load_model()
            ml_result = predict_address_type(selected_ml_address, transactions_df, model)
            heuristic_result = get_heuristic_result(selected_ml_address, transactions_df)
            hybrid_result = classify_address(selected_ml_address, transactions_df, model)

            ml_col1, ml_col2 = st.columns([1, 1])

            with ml_col1:
                st.markdown("**Class Probabilities (ML Model)**")
                proba_df = pd.DataFrame({
                    'Label': list(ml_result['all_class_probabilities'].keys()),
                    'Probability': list(ml_result['all_class_probabilities'].values())
                }).sort_values('Probability', ascending=True)

                fig_bar = go.Figure(go.Bar(
                    x=proba_df['Probability'], y=proba_df['Label'],
                    orientation='h', marker_color='#00d9ff'
                ))
                fig_bar.update_layout(
                    height=250, margin=dict(l=10, r=10, t=10, b=10),
                    paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
                    font={'color': "white"}, xaxis=dict(range=[0, 1])
                )
                st.plotly_chart(fig_bar, use_container_width=True)

            with ml_col2:
                st.markdown("**Final Combined Verdict**")
                st.metric("Heuristic Label", heuristic_result['label'], f"conf: {heuristic_result['confidence']:.2f}")
                st.metric("ML Model Label", ml_result['predicted_label'], f"conf: {ml_result['confidence']:.2f}")
                st.write("")
                if hybrid_result['method'] == 'heuristic + ML agreement':
                    st.success(f"✅ **Agreement** — Final: {hybrid_result['label']} "
                               f"(confidence: {hybrid_result['confidence']:.2f})")
                else:
                    st.warning(f"⚠️ **Disagreement flagged for review** — Heuristic says "
                               f"{hybrid_result['label']}, ML suggests {hybrid_result.get('ml_alternative', 'N/A')} "
                               f"(confidence: {hybrid_result['confidence']:.2f})")
        except FileNotFoundError:
            st.info("No trained ML model found yet. Run `python -m src.model.train` first to enable this section.")
        except Exception as e:
            st.warning(f"Could not compute ML prediction for this address: {e}")

    st.divider()

    # ---------------- Report & Freeze Notice generation ----------------
    st.subheader("📄 Generate Case Report & Freeze Notice")
    st.caption("Generates a structured evidence report and, for every high-confidence "
               "exchange terminus found, a ready-to-send PDF freeze/preservation notice.")

    report_col1, report_col2 = st.columns(2)

    with report_col1:
        if st.button("📋 Generate Evidence Report (JSON)", use_container_width=True):
            with st.spinner("Building structured report..."):
                report = generate_report(tree, address_input, summarise_trace_status)
                report_json = json.dumps(report, indent=2, default=str)

            st.success(f"Report generated -- {len(report['freeze_notice_candidates'])} "
                       f"high-confidence terminus candidate(s) found.")
            st.download_button(
                "⬇️ Download Evidence Report (JSON)",
                report_json,
                file_name=f"trinetra_report_{address_input[:10]}.json",
                use_container_width=True
            )
            with st.expander("Preview report"):
                st.json(report)

    with report_col2:
        if st.button("🧊 Generate Freeze Notice PDF(s)", use_container_width=True):
            with st.spinner("Generating freeze notice PDF(s)..."):
                report = generate_report(tree, address_input, summarise_trace_status)
                generated_files = generate_all_freeze_notices(report, tree)

            if generated_files:
                st.success(f"Generated {len(generated_files)} freeze notice PDF(s).")
                for filepath in generated_files:
                    with open(filepath, 'rb') as f:
                        st.download_button(
                            f"⬇️ Download {os.path.basename(filepath)}",
                            f.read(),
                            file_name=os.path.basename(filepath),
                            mime="application/pdf",
                            use_container_width=True,
                            key=filepath
                        )
            else:
                st.info("No high-confidence exchange terminus was found in this trace, "
                        "so no freeze notice was generated.")

elif any_action and not address_input:
    st.warning("Please enter a wallet address first.")

else:
    st.info("👆 Enter a wallet address above and click **Run Trace** (or any quick-action button) to begin.")