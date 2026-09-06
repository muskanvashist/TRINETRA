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
MIN_INDEGREE_FOR_TERMINUS = 3   # a real exchange deposit address receives from MANY senders --
                                  # requiring this prevents a simple pass-through mule wallet
                                  # (1 in, 1 out, matching amount/time by coincidence) from being
                                  # misclassified as an exchange and cutting the trace short too early


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
        self.pattern_label = None            # 'SUSPECTED_MIXER' | 'FAN_OUT_LAYERING' | 'PEEL_CHAIN' | 'SMURFING' | 'NORMAL_MULE' -- this node's OWN forwarding behavior
        self.forced_role = None              # overrides the computed role -- used when we KNOW a node is the true Victim sender (from a transaction-ID entry point)
        self.percent_of_original = None      # what % of the ORIGINAL traced amount this hop still carries (not just % of its immediate parent)


def fetch_transfers(address, limit=200):
    """Fetch and normalise USDT-TRC20 transfers for one address from TronGrid."""
    url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
    headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY}
    params = {"contract_address": USDT_CONTRACT, "limit": limit}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        raw = resp.json().get('data', [])
    except requests.exceptions.RequestException as e:
        # IMPORTANT: previously this silently returned [] on ANY failure
        # (rate limit, network error, bad address) -- making a rate-limit
        # hit indistinguishable from a genuine "no transactions" dead end.
        # Now the real reason is printed so it's visible during a trace.
        print(f"[fetch_transfers] Failed for {address}: {e}")
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

    IMPORTANT: if the PARENT itself is exhibiting fan-out or smurfing
    behavior, individual branches carrying a small value share are NOT
    pruned -- a small share per branch is the defining evidence of these
    patterns, so pruning them would hide the exact addresses that matter.
    """
    decisions = []
    sorted_edges = sorted(outgoing_edges, key=lambda e: e['amount'], reverse=True)

    original_stolen_amount = original_stolen_amount or total_incoming_value
    cumulative_path_amount = cumulative_path_amount if cumulative_path_amount is not None else total_incoming_value

    parent_pattern = getattr(parent_node, 'pattern_label', None)
    relax_value_threshold = parent_pattern in ('FAN_OUT_LAYERING', 'SMURFING')
    effective_min_value_share = 0.0 if relax_value_threshold else MIN_VALUE_SHARE_TO_FOLLOW

    for edge in sorted_edges:
        value_share = edge['amount'] / total_incoming_value
        child = HopNode(edge['to'], incoming_tx=edge)
        child.value_share = value_share

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

        child_cumulative_amount = cumulative_path_amount * value_share
        child.percent_of_original = round((child_cumulative_amount / original_stolen_amount) * 100, 2) if original_stolen_amount > 0 else None

        if value_share < effective_min_value_share:
            child.status = 'pruned'
            child.reason = (f"Only {value_share:.1%} of parent value "
                             f"({edge['amount']} USDT) -- below "
                             f"{effective_min_value_share:.0%} threshold. "
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
        reason_prefix = (f"Part of parent's {parent_pattern} pattern -- " if relax_value_threshold else "")
        child.reason = (f"{reason_prefix}Carries {value_share:.1%} of traced value "
                         f"({edge['amount']} USDT). "
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
    node.pattern_label = pattern_result['pattern_label']   # store regardless of outcome, for graph display

    if pattern_result['pattern_label'] == 'SUSPECTED_MIXER':
        node.status = 'dead_end'
        node.reason = "Mixer-like fan-in/fan-out pattern detected -- tracing boundary."
        return node

    deposit_score = run_deposit_heuristic(address, transactions_df)
    in_degree = transactions_df[transactions_df['to_address'] == address]['from_address'].nunique()

    if deposit_score > 0.6 and in_degree >= MIN_INDEGREE_FOR_TERMINUS:
        node.status = 'terminus'
        node.confidence = deposit_score
        node.reason = (f"Matches deposit-address pattern (confidence {deposit_score:.2f}, "
                        f"received from {in_degree} distinct senders)")
        return node
    elif deposit_score > 0.6:
        # Looks like a one-off match (single-outbound-target, amount/time
        # aligned) but doesn't yet show the many-in fan-in pattern of a
        # real exchange -- treat as a normal mule hop and keep tracing,
        # rather than falsely stopping here.
        node.reason_note = (f"Deposit-pattern score was {deposit_score:.2f} but only "
                             f"{in_degree} distinct sender(s) seen so far -- "
                             f"not yet enough to confirm an exchange; continuing trace.")

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

    # CRITICAL: this node continued tracing (wasn't dead_end/terminus/pruned
    # above), so its own status must be explicitly 'followed'. Without this,
    # the node's status stays None, which assign_style_role() doesn't
    # recognize -- causing it to silently disappear from the graph even
    # though it was legitimately traced.
    node.status = 'followed'
    if not node.reason:
        node.reason = getattr(node, 'reason_note', None) or "Continued tracing forward through this hop."

    return node


def run_full_trace_from_tx_id(tx_hash):
    """
    Entry point (Option 2 -- more precise): start from a SPECIFIC
    transaction hash instead of a suspect address. This reveals the
    TRUE victim sender address (the transaction's 'from' field) as a
    bonus, so the graph can show a real Victim node distinct from the
    Suspect wallet that received the funds.

    IMPORTANT FIX: the suspect node is built DIRECTLY via trace_tree()
    -- exactly the same call pattern address-mode uses for its entry
    address -- starting at depth=0 so it gets the FULL hop budget
    going forward. An earlier version built two separate node objects
    and copied fields between them starting at depth=1, which both
    risked losing data in the copy and cut the trace one hop short.
    """
    from src.ingestion.fetch_transaction import fetch_transaction_by_id

    tx = fetch_transaction_by_id(tx_hash)
    if tx is None:
        error_root = HopNode(tx_hash)
        error_root.status = 'dead_end'
        error_root.reason = "Could not fetch or decode this transaction. Check the hash and try again."
        return error_root

    budget_state = {'nodes_visited': 0}

    # The TRUE victim -- the sender of this exact transaction
    victim_node = HopNode(tx['from'])
    victim_node.status = 'followed'
    victim_node.forced_role = 'Victim'

    exact_amount = tx['amount']

    # Build the suspect node and its ENTIRE downstream trace in one
    # call -- depth=0 so subsequent hops (bridge wallets, mixers,
    # peel-chains, fan-out, smurfing, right up to the final exchange
    # terminus or dead end) all get traced, exactly as address-mode does.
    suspect_node = trace_tree(
        tx['to'], tx, exact_amount, budget_state, depth=0,
        cumulative_path_amount=exact_amount,
        original_stolen_amount=exact_amount
    )
    suspect_node.forced_role = 'Suspect'

    victim_node.children.append(suspect_node)
    return victim_node


def run_full_trace(suspect_address):
    """
    Entry point: kick off a full multi-branch trace from the
    victim-reported SUSPECT wallet address. There is no separate
    "victim address" anywhere in this system -- the victim reports a
    suspect's wallet, and tracing starts from that single address.
    """
    budget_state = {'nodes_visited': 0}
    root = HopNode(suspect_address)
    root.status = 'followed'
    root.children = []

    first_edges = fetch_transfers(suspect_address)
    if not first_edges:
        root.status = 'dead_end'
        root.reason = "No outgoing transactions found for this address."
        return root

    total_value = sum(e['amount'] for e in first_edges)
    all_rows = [{'from_address': e['from'], 'to_address': e['to'],
                 'amount': e['amount'], 'timestamp': e['timestamp'], 'hash': e['hash']}
                for e in first_edges]
    transactions_df = pd.DataFrame(all_rows)

    root_pattern_result = run_pattern_detection(suspect_address, transactions_df)
    root.pattern_label = root_pattern_result['pattern_label']

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
    (Suspect / Mule / Mixer / Exchange), for use in tables and UI.

    If entry was via a transaction ID, node.forced_role will already be
    set to the TRUE 'Victim' or 'Suspect' and takes priority. If entry
    was via address alone, the root is labeled Suspect (there is no
    separate "victim address" known in that mode).
    """
    if getattr(node, 'forced_role', None):
        label_map = {'Victim': 'Victim Wallet (true sender)', 'Suspect': 'Suspect Wallet'}
        return label_map.get(node.forced_role, node.forced_role)

    if is_root:
        return 'Suspect Wallet (entered address)'
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
    suspect_address = "TWXLTtvZKonEmYA2NNLSw5goGooeWT7Vj9"
    tree = run_full_trace(suspect_address)
    print_tree(tree)

    print("\n--- Trace Summary ---")
    summary = summarise_trace_status(tree)
    print(f"Overall status: {summary['overall_status']}")
    print(f"Percent traced to exchange: {summary['percent_traced_to_exchange']}%")
    print(f"Terminus count: {summary['terminus_count']}")
    print(f"Dead end count: {summary['dead_end_count']}")
    print(f"Mid-chain (still tracing) count: {summary['mid_chain_count']}")