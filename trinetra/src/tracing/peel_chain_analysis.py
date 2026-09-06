# src/tracing/peel_chain_analysis.py
#
# Two specific analyses on top of the trace tree:
#
# 1. DOMINANT PEEL-CHAIN SEQUENCE: at every hop, the largest outgoing
#    transfer is followed as the "main chain" -- exactly the pattern
#    described where ~85-90% continues forward and ~10-15% is peeled
#    off at each step. This produces a readable narrative:
#    "Victim -> Suspect (100%) -> peeled 12% -> Relay-1 (88%) ->
#     peeled 9% -> Relay-2 (79%) -> ... -> Exchange"
#
# 2. CONVERGENCE DETECTION: separate branches (even ones that started
#    as different "peels") may all eventually route to the SAME final
#    wallet. This is a strong signal -- it means multiple seemingly
#    independent paths are actually controlled by one operation.


def trace_dominant_peel_chain(root_node):
    """
    Walk the tree, at every hop following only the child that carried
    the LARGEST share of the incoming value (the "dominant" branch).
    Every other child at that hop is logged as a "peel" -- money
    skimmed off to the side.

    Returns a list of steps describing the main chain, each with the
    percentage peeled off before continuing.
    """
    steps = []
    node = root_node
    depth = 0

    while node is not None:
        step = {
            'depth': depth,
            'address': node.address,
            'amount_at_this_hop': node.incoming_tx['amount'] if node.incoming_tx else None,
            'status': node.status,
        }

        if not node.children:
            step['peeled_here'] = None
            step['peeled_amount'] = None
            step['dominant_next'] = None
            steps.append(step)
            break

        # Only consider children that actually carried traced value
        valued_children = [c for c in node.children if c.incoming_tx]
        if not valued_children:
            step['peeled_here'] = None
            step['peeled_amount'] = None
            step['dominant_next'] = None
            steps.append(step)
            break

        total_out = sum(c.incoming_tx['amount'] for c in valued_children)
        dominant_child = max(valued_children, key=lambda c: c.incoming_tx['amount'])
        peeled_amount = total_out - dominant_child.incoming_tx['amount']
        peeled_pct = (peeled_amount / total_out) * 100 if total_out > 0 else 0

        step['peeled_here'] = round(peeled_pct, 1)
        step['peeled_amount'] = round(peeled_amount, 2)
        step['dominant_next'] = dominant_child.address

        steps.append(step)
        node = dominant_child
        depth += 1

    return steps


def format_peel_chain_narrative(steps):
    """
    Turn the raw step list into a readable sentence-by-sentence
    narrative, e.g.:
    "Victim -> Suspect (100.00 USDT) -> peeled 12.0% (12.00 USDT) ->
     Relay-1 (88.00 USDT) -> peeled 9.0% (7.92 USDT) -> ... -> Exchange"
    """
    parts = []
    for i, step in enumerate(steps):
        label = "Victim" if i == 0 else f"Hop {i}"
        amount_text = f"{step['amount_at_this_hop']:.2f} USDT" if step['amount_at_this_hop'] else "start"
        parts.append(f"{label} ({step['address'][:8]}...) [{amount_text}]")

        if step['peeled_here'] is not None and step['peeled_here'] > 0:
            parts.append(f"--peeled {step['peeled_here']}% ({step['peeled_amount']} USDT)-->")
        elif step['dominant_next'] is not None:
            parts.append("-->")

    return " ".join(parts)


def detect_convergence_points(root_node):
    """
    Scan the ENTIRE tree (every branch, not just the dominant one) and
    find any address that is reached from MORE THAN ONE distinct parent
    branch -- meaning separate paths in the fund flow eventually route
    to the same wallet. This is a strong signal of a single operation
    behind multiple seemingly independent mule chains.
    """
    address_sources = {}   # address -> list of (parent_address, depth, amount)

    def walk(node, parent_address=None, depth=0):
        if node.address not in address_sources:
            address_sources[node.address] = []
        if parent_address is not None:
            address_sources[node.address].append({
                'from_parent': parent_address,
                'depth': depth,
                'amount': node.incoming_tx['amount'] if node.incoming_tx else None
            })
        for child in node.children:
            walk(child, parent_address=node.address, depth=depth + 1)

    walk(root_node)

    convergence_points = []
    for address, sources in address_sources.items():
        distinct_parents = set(s['from_parent'] for s in sources)
        if len(distinct_parents) > 1:
            total_amount = sum(s['amount'] for s in sources if s['amount'])
            convergence_points.append({
                'address': address,
                'number_of_incoming_paths': len(distinct_parents),
                'source_addresses': list(distinct_parents),
                'total_converged_amount': round(total_amount, 2),
                'significance': (
                    "HIGH -- multiple independent-looking chains all route here, "
                    "suggesting one operation controls all of them."
                    if len(distinct_parents) >= 3 else
                    "MODERATE -- two separate paths converge here."
                )
            })

    return sorted(convergence_points, key=lambda c: c['number_of_incoming_paths'], reverse=True)


if __name__ == "__main__":
    from src.tracing.tree_trace import run_full_trace

    suspect_address = "TWXLTtvZKonEmYA2NNLSw5goGooeWT7Vj9"
    tree = run_full_trace(suspect_address)

    print("=== Dominant Peel-Chain Sequence ===")
    steps = trace_dominant_peel_chain(tree)
    print(format_peel_chain_narrative(steps))

    print("\n=== Convergence Points ===")
    convergence = detect_convergence_points(tree)
    if convergence:
        for c in convergence:
            print(f"{c['address']} <- {c['number_of_incoming_paths']} paths "
                  f"({c['total_converged_amount']} USDT) -- {c['significance']}")
    else:
        print("No convergence detected -- all traced branches remained separate.")