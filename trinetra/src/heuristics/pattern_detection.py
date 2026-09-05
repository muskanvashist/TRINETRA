import pandas as pd
import numpy as np

# --- Thresholds (from the pattern catalogue discussed earlier) ---
MIXER_MIN_IN_DEGREE = 10
MIXER_MIN_OUT_DEGREE = 10
MIXER_MAX_DOMINANT_SHARE = 0.3

PEEL_CHAIN_MIN_DOMINANT_SHARE = 0.85
FAN_OUT_MAX_AMOUNT_VARIANCE_RATIO = 0.2
SMURFING_MIN_OUT_DEGREE = 5
SMURFING_MAX_AMOUNT_VARIANCE_RATIO = 0.05


def run_pattern_detection(address, transactions_df):
    """
    Returns a mixer_pattern_score (0 to 1) for this address, along with
    the detected pattern label for reference.

    Higher score = stronger evidence this address behaves like a mixer
    (many-in AND many-out, with no single dominant destination -- unlike
    a deposit address which is many-in but ONE-out).
    """
    incoming = transactions_df[transactions_df['to_address'] == address]
    outgoing = transactions_df[transactions_df['from_address'] == address]

    in_degree = incoming['from_address'].nunique()
    out_degree = outgoing['to_address'].nunique()

    if outgoing.empty:
        return {'mixer_pattern_score': 0.0, 'pattern_label': 'NO_OUTFLOW'}

    amounts = outgoing['amount'].values
    total_out = amounts.sum()
    dominant_share = amounts.max() / total_out if total_out > 0 else 0

    # --- Mixer check: many-in AND many-out, no dominant destination ---
    if (in_degree >= MIXER_MIN_IN_DEGREE and
            out_degree >= MIXER_MIN_OUT_DEGREE and
            dominant_share < MIXER_MAX_DOMINANT_SHARE):

        # score scales with how extreme the fan-in/fan-out is
        score = min(1.0, (in_degree / (MIXER_MIN_IN_DEGREE * 2)) *
                          (out_degree / (MIXER_MIN_OUT_DEGREE * 2)))
        return {'mixer_pattern_score': round(score, 3), 'pattern_label': 'SUSPECTED_MIXER'}

    # --- Peel chain check: one dominant + one small "peel" ---
    if out_degree == 2 and dominant_share >= PEEL_CHAIN_MIN_DOMINANT_SHARE:
        return {'mixer_pattern_score': 0.1, 'pattern_label': 'PEEL_CHAIN'}

    # --- Smurfing check: many small, near-identical outgoing amounts ---
    if out_degree >= SMURFING_MIN_OUT_DEGREE:
        variance_ratio = (amounts.max() - amounts.min()) / amounts.max() if amounts.max() > 0 else 1
        if variance_ratio < SMURFING_MAX_AMOUNT_VARIANCE_RATIO:
            return {'mixer_pattern_score': 0.2, 'pattern_label': 'SMURFING'}

    # --- Fan-out / layering check ---
    if out_degree > 3:
        variance_ratio = (amounts.max() - amounts.min()) / amounts.max() if amounts.max() > 0 else 1
        if variance_ratio < FAN_OUT_MAX_AMOUNT_VARIANCE_RATIO:
            return {'mixer_pattern_score': 0.15, 'pattern_label': 'FAN_OUT_LAYERING'}

    return {'mixer_pattern_score': 0.0, 'pattern_label': 'NORMAL'}


def check_round_trip(address, visited_addresses):
    """
    Flags if this address has already appeared earlier in the current trace --
    a sign of funds looping back, used to avoid infinite traversal loops.
    """
    if address in visited_addresses:
        return {'flag': 'ROUND_TRIP', 'reason': 'Address already appeared earlier in this trace.'}
    return None


if __name__ == "__main__":
    transactions_df = pd.read_csv("data/raw/all_transactions.csv")
    test_address = transactions_df['from_address'].iloc[0]
    result = run_pattern_detection(test_address, transactions_df)
    print(f"Pattern result for {test_address}: {result}")