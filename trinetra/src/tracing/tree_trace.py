# src/tracing/tree_trace.py

import os
import requests
import time
from dotenv import load_dotenv
import pandas as pd

from src.heuristics.deposit_heuristic import run_deposit_heuristic
from src.heuristics.pattern_detection import run_pattern_detection
from src.features.feature_engineering import build_address_features
from src.tracing.priority_engine import compute_priority_score, priority_level

load_dotenv()
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY")
USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

MIN_VALUE_SHARE_TO_FOLLOW = 0.05   # ignore branches carrying <5% of parent value
MAX_BRANCHES_PER_HOP = 8            # compute/API budget guardrail
MAX_TOTAL_NODES = 500               # overall trace budget for the whole tree
MAX_DEPTH = 5


class HopNode:
    def __init__(self, address, incoming_tx=None):
        self.address = address
        self.incoming_tx = incoming_tx      # hash, amount, timestamp of tx that brought funds here
        self.children = []                   # list of HopNode - THIS is what makes it a tree
        self.status = None                   # 'followed' | 'pruned' | 'terminus' | 'dead_end'
        self.reason = None                   # WHY this status was assigned
        self.confidence = None               # this branch's own kappa score
        self.value_share = None              # what % of parent's value flowed into this branch
        self.priority_score = None           # combined money+network investigation priority (0-1)
        self.priority_level = None           # 'HIGH' | 'MEDIUM' | 'LOW'


def fetch_transfers(address, limit=200):
    """Fetch and normalise USDT-TRC20 transfers for one address from TronGrid."""
    url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
    headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY}
    params = {"contract_address": USDT_CONTRACT, "limit": limit}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        raw = resp.json().get('data', [])
    except requests.exceptions.RequestException:
        return []

    edges = []
    for tx in raw:
        edges.append({
            'from': tx['from'],
            'to': tx['to'],
            'amount': int(tx['value']) / 1_000_000,
            'timestamp': int(tx['block_timestamp']) / 1000,
            'hash': tx['transaction_id'],
        })
    time.sleep(0.3)
    return edges


def decide_branches(parent_node, outgoing_edges, total_incoming_value, budget_state,
                     cumulative_path_amount=None, original_stolen_amount=None, transactions_df=None):
    """
    Called at every hop. Returns a list of HopNode children,
    each tagged with status + reason -- followed AND not-followed both logged --
    PLUS a priority score (money-flow + network-behavior) so the caller
    knows which branch to explore first, best-first-search style.
    """
    decisions = []
    sorted_edges = sorted(outgoing_edges, key=lambda e: e['amount'], reverse=True)

    original_stolen_amount = original_stolen_amount or total_incoming_value
    cumulative_path_amount = cumulative_path_amount if cumulative_path_amount is not None else total_incoming_value

    for edge in sorted_edges:
        value_share = edge['amount'] / total_incoming_value
        child = HopNode(edge['to'], incoming_tx=edge)
        child.value_share = value_share

        # --- Compute priority score regardless of follow/prune outcome ---
        # (a pruned branch still gets a score, so it can be picked up later
        # from the "monitor" queue if it becomes relevant)
        if transactions_df is not None:
            feats = build_address_features(edge['to'], transactions_df)
            pattern_result = run_pattern_detection(edge['to'], transactions_df)
            deposit_score = run_deposit_heuristic(edge['to'], transactions_df)
        else:
            feats, pattern_result, deposit_score = {}, {'pattern_label': 'NORMAL', 'mixer_pattern_score': 0}, 0

        score_result = compute_priority_score(
            value_share=value_share,
            cumulative_path_amount=cumulative_path_amount * value_share,
            original_stolen_amount=original_stolen_amount,
            features=feats,
            pattern_result=pattern_result,
            deposit_score=deposit_score
        )
        child.priority_score = score_result['priority_score']
        child.priority_level, _ = priority_level(child.priority_score)

        if value_share < MIN_VALUE_SHARE_TO_FOLLOW:
            child.status = 'pruned'
            child.reason = (f"Only {value_share:.1%} of parent value "
                             f"({edge['amount']} USDT) -- below "
                             f"{MIN_VALUE_SHARE_TO_FOLLOW:.0%} threshold. "
                             f"Priority score {child.priority_score} ({child.priority_level}) -- "
                             f"logged for monitoring, not actively traced now.")
            decisions.append(child)
            continue

        if len(decisions) >= MAX_BRANCHES_PER_HOP:
            child.status = 'pruned'
            child.reason = (f"Hop already has {MAX_BRANCHES_PER_HOP} branches "
                             f"under active trace (compute/API budget). "
                             f"Priority score {child.priority_score} ({child.priority_level}) -- "
                             f"queued as a follow-up lead, not dropped.")
            decisions.append(child)
            continue

        if budget_state['nodes_visited'] >= MAX_TOTAL_NODES:
            child.status = 'pruned'
            child.reason = f"Global trace budget ({MAX_TOTAL_NODES} nodes) reached for this case."
            decisions.append(child)
            continue

        child.status = 'followed'
        child.reason = (f"Carries {value_share:.1%} of traced value "
                         f"({edge['amount']} USDT) -- above threshold. "
                         f"Priority score {child.priority_score} ({child.priority_level}) -- "
                         f"actively traced forward.")
        budget_state['nodes_visited'] += 1
        decisions.append(child)

    return decisions


def trace_tree(address, incoming_tx, total_value, budget_state, depth=0, max_depth=MAX_DEPTH,
                cumulative_path_amount=None, original_stolen_amount=None):
    """
    Recursively build the full multi-branch trace tree starting from
    one address, following every branch above the value threshold,
    scored by investigation priority (money-flow + network behavior).
    """
    node = HopNode(address, incoming_tx)

    if depth >= max_depth:
        node.status = 'dead_end'
        node.reason = f"Hop limit ({max_depth}) reached."
        return node

    edges = fetch_transfers(address)

    if not edges:
        node.status = 'dead_end'
        node.reason = "No further outbound transfers found -- funds appear to rest here."
        return node

    all_rows = [{'from_address': e['from'], 'to_address': e['to'],
                 'amount': e['amount'], 'timestamp': e['timestamp'], 'hash': e['hash']}
                for e in edges]
    transactions_df = pd.DataFrame(all_rows)

    pattern_result = run_pattern_detection(address, transactions_df)
    if pattern_result['pattern_label'] == 'SUSPECTED_MIXER':
        node.status = 'dead_end'
        node.reason = "Mixer-like fan-in/fan-out pattern detected -- tracing boundary."
        return node

    deposit_score = run_deposit_heuristic(address, transactions_df)
    if deposit_score > 0.6:
        node.status = 'terminus'
        node.confidence = deposit_score
        node.reason = f"Matches deposit-address pattern (confidence {deposit_score:.2f})"
        return node

    outgoing_edges = [e for e in edges if e['from'] == address]
    if not outgoing_edges:
        node.status = 'dead_end'
        node.reason = "No outgoing transfers from this address."
        return node

    children = decide_branches(node, outgoing_edges, total_value, budget_state,
                                cumulative_path_amount=cumulative_path_amount,
                                original_stolen_amount=original_stolen_amount,
                                transactions_df=transactions_df)
    for child in children:
        if child.status == 'followed':
            child_value = child.incoming_tx['amount']
            child_cumulative = (cumulative_path_amount or total_value) * child.value_share
            child = trace_tree(child.address, child.incoming_tx, child_value,
                                budget_state, depth + 1, max_depth,
                                cumulative_path_amount=child_cumulative,
                                original_stolen_amount=original_stolen_amount)
        node.children.append(child)

    return node


def run_full_trace(victim_address):
    """Entry point: kick off a full multi-branch trace from a victim address."""
    budget_state = {'nodes_visited': 0}
    root = HopNode(victim_address)
    root.status = 'followed'
    root.children = []

    first_edges = fetch_transfers(victim_address)
    if not first_edges:
        root.status = 'dead_end'
        root.reason = "No outgoing transactions found for this address."
        return root

    total_value = sum(e['amount'] for e in first_edges)
    all_rows = [{'from_address': e['from'], 'to_address': e['to'],
                 'amount': e['amount'], 'timestamp': e['timestamp'], 'hash': e['hash']}
                for e in first_edges]
    transactions_df = pd.DataFrame(all_rows)

    children = decide_branches(root, first_edges, total_value, budget_state,
                                cumulative_path_amount=total_value,
                                original_stolen_amount=total_value,
                                transactions_df=transactions_df)

    for child in children:
        if child.status == 'followed':
            child_value = child.incoming_tx['amount']
            child_cumulative = total_value * child.value_share
            child = trace_tree(child.address, child.incoming_tx, child_value, budget_state, depth=1,
                                cumulative_path_amount=child_cumulative,
                                original_stolen_amount=total_value)
        root.children.append(child)

    return root


def summarise_trace_status(root_node):
    """
    Walk the completed trace tree and answer: how much of the traced
    value reached a confirmed exchange terminus, how much hit a dead
    end (e.g. suspected mixer), and how much is still mid-chain?
    """
    terminus_nodes = []
    dead_ends = []
    still_active = []

    def walk(node):
        if node.status == 'terminus':
            terminus_nodes.append(node)
        elif node.status == 'dead_end':
            dead_ends.append(node)
        elif node.status == 'followed' and not node.children:
            still_active.append(node)   # followed but recursion hasn't resolved yet
        for child in node.children:
            walk(child)

    walk(root_node)

    all_valued_nodes = [n for n in (terminus_nodes + dead_ends + still_active) if n.incoming_tx]
    total_value = sum(n.incoming_tx['amount'] for n in all_valued_nodes)
    traced_to_exchange = sum(n.incoming_tx['amount'] for n in terminus_nodes if n.incoming_tx)

    percent_traced = round((traced_to_exchange / total_value) * 100, 1) if total_value > 0 else 0.0

    return {
        'overall_status': 'PARTIALLY_RESOLVED' if (dead_ends or still_active) else 'FULLY_RESOLVED',
        'percent_traced_to_exchange': percent_traced,
        'terminus_count': len(terminus_nodes),
        'dead_end_count': len(dead_ends),
        'mid_chain_count': len(still_active),
        'terminus_details': [
            {'address': n.address, 'amount': n.incoming_tx['amount'], 'confidence': n.confidence, 'reason': n.reason}
            for n in terminus_nodes
        ],
        'dead_end_details': [
            {'address': n.address, 'amount': n.incoming_tx['amount'] if n.incoming_tx else 0, 'reason': n.reason}
            for n in dead_ends
        ]
    }


def get_priority_worklist(root_node):
    """
    Walk the ENTIRE tree -- including pruned/monitored branches -- and
    build the ranked "investigate this first" worklist. This is the
    actual deliverable of the adaptive prioritization engine: not just
    a graph, but an ordered list of where an investigator's limited
    time is best spent.
    """
    from src.tracing.priority_engine import build_priority_report

    all_branches = []

    def walk(node, is_root=False):
        if not is_root and node.priority_score is not None:
            all_branches.append({
                'address': node.address,
                'priority_score': node.priority_score,
                'money_score': None,   # recomputed below if needed
                'network_score': None,
                'amount': node.incoming_tx['amount'] if node.incoming_tx else None,
                'reason': node.reason,
                'status': node.status,
            })
        for c in node.children:
            walk(c, is_root=False)

    walk(root_node, is_root=True)

    # Fill in money/network sub-scores as 0 placeholders if not tracked separately
    for b in all_branches:
        b['money_score'] = b['money_score'] or 0
        b['network_score'] = b['network_score'] or 0

    return build_priority_report(all_branches)


def assign_role(node, is_root=False):
    """
    Turn a HopNode's status + reason into a plain-English role label
    (Victim / Mule / Mixer / Exchange), for use in tables and UI --
    independent of which graph visualisation is being rendered.
    """
    if is_root:
        return 'Victim Wallet'
    if node.status == 'terminus':
        return 'Exchange / Deposit Wallet'
    if node.status == 'dead_end':
        if node.reason and 'mixer' in node.reason.lower():
            return 'Mixer Wallet'
        return 'Dead End Wallet'
    if node.status == 'followed':
        return 'Mule Wallet'
    return None   # pruned nodes


def print_tree(node, indent=0):
    """Simple debug print of the trace tree."""
    prefix = "  " * indent
    amount = f"{node.incoming_tx['amount']:.2f} USDT" if node.incoming_tx else "ROOT"
    print(f"{prefix}- {node.address[:10]}... [{node.status}] {amount} :: {node.reason}")
    for child in node.children:
        print_tree(child, indent + 1)


if __name__ == "__main__":
    victim_address = "trinetra/src/ingestion/fetch_trongrid.py"
    tree = run_full_trace(victim_address)
    print_tree(tree)

    print("\n--- Trace Summary ---")
    summary = summarise_trace_status(tree)
    print(f"Overall status: {summary['overall_status']}")
    print(f"Percent traced to exchange: {summary['percent_traced_to_exchange']}%")
    print(f"Terminus count: {summary['terminus_count']}")
    print(f"Dead end count: {summary['dead_end_count']}")
    print(f"Mid-chain (still tracing) count: {summary['mid_chain_count']}")