# src/heuristics/deposit_heuristic.py

import pandas as pd
import numpy as np

# --- Parameters (tune these by hand during testing, as the research recommends) ---
ALPHA = 0            # max allowed amount deviation -- 0 for tokens like USDT
TAU_SECONDS = 3600    # max allowed time window between receipt and forward (1 hour)


def compute_kappa(amount_diff, time_diff, alpha=ALPHA, tau=TAU_SECONDS):
    """
    Tutela-style confidence score.
    kappa = 1 - [ 0.5*(|af-ar|/alpha) + 0.5*((tf-tr)/tau) ]
    Guards against divide-by-zero when alpha=0 and amount matches exactly.
    """
    if alpha == 0 and amount_diff == 0:
        amount_term = 0
    else:
        amount_term = amount_diff / max(alpha, 1e-9)

    time_term = time_diff / tau

    kappa = 1 - (0.5 * amount_term + 0.5 * time_term)
    return max(0, min(1, kappa))   # clamp between 0 and 1


def run_deposit_heuristic(address, transactions_df, alpha=ALPHA, tau=TAU_SECONDS):
    """
    Returns a single deposit-address confidence score (0 to 1) for this address.

    Logic: many-in, one-out pattern. Check if incoming transfers to this
    address are matched by an outgoing transfer of a near-identical amount,
    within a short time window, going to a SINGLE consistent destination.
    """
    incoming = transactions_df[transactions_df['to_address'] == address]
    outgoing = transactions_df[transactions_df['from_address'] == address]

    if incoming.empty or outgoing.empty:
        return 0.0

    # Rule 1: must forward to only ONE destination (single-outbound-target rule)
    unique_destinations = outgoing['to_address'].nunique()
    if unique_destinations != 1:
        return 0.0   # feeder paying multiple destinations -- probably not a deposit address

    best_kappa = 0.0

    for _, in_tx in incoming.iterrows():
        # look for an outgoing transaction that happened AFTER this incoming one
        candidates = outgoing[outgoing['timestamp'] >= in_tx['timestamp']]
        if candidates.empty:
            continue

        out_tx = candidates.sort_values('timestamp').iloc[0]

        amount_diff = abs(out_tx['amount'] - in_tx['amount'])
        time_diff = out_tx['timestamp'] - in_tx['timestamp']

        if time_diff < 0 or time_diff > tau:
            continue   # outside the allowed time window

        kappa = compute_kappa(amount_diff, time_diff, alpha, tau)
        best_kappa = max(best_kappa, kappa)

    return best_kappa


if __name__ == "__main__":
    # Quick manual test
    transactions_df = pd.read_csv("data/raw/all_transactions.csv")
    test_address = transactions_df['to_address'].iloc[0]
    score = run_deposit_heuristic(test_address, transactions_df)
    print(f"Deposit heuristic score for {test_address}: {score:.3f}")