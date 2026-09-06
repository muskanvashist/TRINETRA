# src/reporting/generate_report.py

from datetime import datetime, timezone


def current_timestamp():
    """ISO-format UTC timestamp for the report's 'generated_at' field."""
    return datetime.now(timezone.utc).isoformat()


def serialise_tree(node):
    """
    Convert one HopNode (and all its children recursively) into a
    plain dictionary -- this is what makes the tree exportable as
    JSON for the final evidence report.
    """
    return {
        'address': node.address,
        'transaction_hash': node.incoming_tx['hash'] if node.incoming_tx else None,
        'amount_usdt': node.incoming_tx['amount'] if node.incoming_tx else None,
        'timestamp': node.incoming_tx['timestamp'] if node.incoming_tx else None,
        'status': node.status,
        'reason': node.reason,
        'confidence': node.confidence,
        'children': [serialise_tree(c) for c in node.children]
    }


def collect_termini(node, freeze_notice_candidates):
    """
    Walk the tree and pull out every high-confidence terminus --
    each one becomes a candidate for its own freeze notice, since a
    fan-out trace can legitimately end at multiple different exchanges.
    """
    if node.status == 'terminus' and node.confidence and node.confidence > 0.7:
        freeze_notice_candidates.append({
            'exchange': node.reason,
            'address': node.address,
            'confidence': node.confidence,
            'amount': node.incoming_tx['amount'] if node.incoming_tx else None,
            'evidence_hash': node.incoming_tx['hash'] if node.incoming_tx else None
        })
    for c in node.children:
        collect_termini(c, freeze_notice_candidates)


def generate_report(root_node, suspect_address, summarise_trace_status_fn):
    """
    Build the full evidence report: summary stats, the complete
    serialised hop tree, and a list of freeze-notice candidates
    (one per high-confidence exchange terminus reached).

    suspect_address is the victim-reported wallet being traced --
    there is no separate "victim address" anywhere in this system.

    summarise_trace_status_fn is passed in from tree_trace.py to avoid
    a circular import between the tracing and reporting modules.
    """
    freeze_notice_candidates = []
    collect_termini(root_node, freeze_notice_candidates)

    report = {
        'suspect_address': suspect_address,
        'generated_at': current_timestamp(),
        'summary': summarise_trace_status_fn(root_node),
        'full_hop_tree': serialise_tree(root_node),
        'freeze_notice_candidates': freeze_notice_candidates
    }

    return report


if __name__ == "__main__":
    import json
    from src.tracing.tree_trace import run_full_trace, summarise_trace_status

    suspect_address = "TWXLTtvZKonEmYA2NNLSw5goGooeWT7Vj9"
    tree = run_full_trace(suspect_address)

    report = generate_report(tree, suspect_address, summarise_trace_status)

    print(json.dumps(report, indent=2, default=str))

    with open("data/processed/trace_report.json", "w") as f:
        json.dump(report, f, indent=2, default=str)
    print("\nReport saved to data/processed/trace_report.json")