# src/tracing/priority_engine.py
#
# THE ADAPTIVE INVESTIGATION PRIORITIZATION ENGINE
#
# This is Trinetra's differentiator layer. Basic wallet tracing, graphing,
# and entity attribution already exist in commercial tools (Chainalysis,
# Elliptic, TRM Labs). What none of them expose openly is WHICH branch an
# investigator should look at FIRST when stolen funds fan out into many
# paths. This module scores every branch by a combination of money-flow
# importance and network/behavioral risk, then traverses in that priority
# order using a best-first (priority queue) search instead of a plain
# depth-first walk.

import heapq
import itertools

# Baseline weights -- explicitly an experimental starting point, not a
# scientifically fixed rule (per the research brief). Tune these against
# real traced cases if time allows.
MONEY_WEIGHT = 0.70
NETWORK_WEIGHT = 0.30

HIGH_PRIORITY_THRESHOLD = 0.65
MEDIUM_PRIORITY_THRESHOLD = 0.35


def compute_money_score(value_share, cumulative_path_amount, original_stolen_amount):
    """
    Money-flow importance component (70% of the baseline weight).
    Combines: how much of the PARENT's value this branch carries, and
    how much of the ORIGINAL stolen amount is still represented by this
    path overall (a branch far downstream that has been heavily peeled
    should score lower than one still carrying most of the original sum).
    """
    parent_share_score = value_share if value_share is not None else 0

    if original_stolen_amount and original_stolen_amount > 0:
        original_share_score = min(cumulative_path_amount / original_stolen_amount, 1.0)
    else:
        original_share_score = 0

    # Average the two signals -- both matter, neither alone is sufficient
    return (parent_share_score + original_share_score) / 2


def compute_network_score(features, pattern_result, deposit_score):
    """
    Network/behavioral risk component (30% of the baseline weight).
    High in-degree, mixer-like behavior, or strong deposit-heuristic
    matches all raise investigative urgency independent of raw amount.
    """
    score = 0.0

    in_degree = features.get('in_degree', 0) or 0
    out_degree = features.get('out_degree', 0) or 0

    # Normalize degree signals with a soft cap (diminishing returns past 20)
    degree_score = min((in_degree + out_degree) / 40, 1.0)
    score += degree_score * 0.4

    # Mixer / suspicious pattern raises urgency sharply
    if pattern_result.get('pattern_label') not in (None, 'NORMAL'):
        score += pattern_result.get('mixer_pattern_score', 0) * 0.4

    # Strong deposit-address match means "this might be the exchange
    # terminus" -- also investigatively urgent, just for a different reason
    score += deposit_score * 0.2

    return min(score, 1.0)


def compute_priority_score(value_share, cumulative_path_amount, original_stolen_amount,
                            features, pattern_result, deposit_score,
                            money_weight=MONEY_WEIGHT, network_weight=NETWORK_WEIGHT):
    """
    Combined priority score (0 to 1) for one branch, using the baseline
    70:30 money-flow / network-behavior split.
    """
    money_score = compute_money_score(value_share, cumulative_path_amount, original_stolen_amount)
    network_score = compute_network_score(features, pattern_result, deposit_score)

    combined = (money_score * money_weight) + (network_score * network_weight)

    return {
        'priority_score': round(combined, 3),
        'money_score': round(money_score, 3),
        'network_score': round(network_score, 3),
    }


def priority_level(score):
    """Map a numeric priority score to an investigator-facing action label."""
    if score >= HIGH_PRIORITY_THRESHOLD:
        return 'HIGH', 'Investigate immediately'
    elif score >= MEDIUM_PRIORITY_THRESHOLD:
        return 'MEDIUM', 'Queue for review'
    else:
        return 'LOW', 'Monitor only (not actively investigated yet)'


class PriorityTraceQueue:
    """
    A best-first (priority queue) traversal manager. Instead of exploring
    branches in whatever order they were discovered (plain recursion),
    the highest-priority branch is always explored next -- this directly
    implements the "investigate this path first" positioning.

    Python's heapq is a MIN-heap, so priorities are negated on insertion
    to get max-priority-first behavior.
    """

    def __init__(self):
        self._heap = []
        self._counter = itertools.count()   # tie-breaker so equal scores don't error on comparison

    def push(self, priority_score, item):
        count = next(self._counter)
        heapq.heappush(self._heap, (-priority_score, count, item))

    def pop(self):
        if not self._heap:
            return None
        neg_priority, _, item = heapq.heappop(self._heap)
        return -neg_priority, item

    def __len__(self):
        return len(self._heap)

    def is_empty(self):
        return len(self._heap) == 0


def build_priority_report(all_scored_branches):
    """
    Given every scored branch encountered during a trace, produce the
    investigator-facing prioritized list -- this is the actual
    deliverable of the engine: not just a graph, but an ordered
    "look here first" worklist.
    """
    sorted_branches = sorted(all_scored_branches, key=lambda b: b['priority_score'], reverse=True)

    report = []
    for rank, branch in enumerate(sorted_branches, start=1):
        level, action = priority_level(branch['priority_score'])
        report.append({
            'rank': rank,
            'address': branch['address'],
            'priority_score': branch['priority_score'],
            'priority_level': level,
            'recommended_action': action,
            'money_score': branch['money_score'],
            'network_score': branch['network_score'],
            'amount': branch.get('amount'),
            'reason': branch.get('reason'),
        })
    return report