# src/features/feature_engineering.py
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import pandas as pd
import numpy as np

from src.heuristics.deposit_heuristic import run_deposit_heuristic
from src.heuristics.pattern_detection import run_pattern_detection


def compute_avg_delay(incoming, outgoing):
    """
    Average time (in seconds) between when this address RECEIVED funds
    and when it FORWARDED them onward. This is the tau signal used in
    the deposit-address heuristic.
    """
    if incoming.empty or outgoing.empty:
        return None

    delays = []
    for _, in_tx in incoming.iterrows():
        # find outgoing transactions that happened AFTER this incoming one
        later_out = outgoing[outgoing['timestamp'] >= in_tx['timestamp']]
        if not later_out.empty:
            next_out = later_out.sort_values('timestamp').iloc[0]
            delay = next_out['timestamp'] - in_tx['timestamp']
            delays.append(delay)

    return np.mean(delays) if delays else None


def compute_dominant_share(outgoing):
    """
    What fraction of this address's total outgoing value went to its
    SINGLE biggest destination? Close to 1.0 means many-in-one-out
    (deposit-address-like). Close to 0 means value is spread across
    many destinations (fan-out / peel-chain-like).
    """
    if outgoing.empty:
        return None

    total_out = outgoing['amount'].sum()
    if total_out == 0:
        return None

    # FIX: column is 'to_address', not 'to'
    per_destination = outgoing.groupby('to_address')['amount'].sum()
    dominant_amount = per_destination.max()

    return dominant_amount / total_out


def compute_address_age(address, transactions_df):
    """
    Days between this address's FIRST seen transaction and its LAST
    seen transaction in our collected data. A freshly created mule
    wallet will show a very small age; a long-lived hot wallet won't.
    """
    involved = transactions_df[
        (transactions_df['from_address'] == address) |
        (transactions_df['to_address'] == address)
    ]

    if involved.empty:
        return None

    first_seen = involved['timestamp'].min()
    last_seen = involved['timestamp'].max()

    age_seconds = last_seen - first_seen
    return age_seconds / 86400   # convert seconds -> days


def build_address_features(address, transactions_df):
    """
    Build the full feature row for one address, including live scores
    from the deposit-address heuristic and mixer/pattern detector.
    """
    incoming = transactions_df[transactions_df['to_address'] == address]
    outgoing = transactions_df[transactions_df['from_address'] == address]

    pattern_result = run_pattern_detection(address, transactions_df)

    features = {
        'address': address,
        'in_degree': incoming['from_address'].nunique(),
        'out_degree': outgoing['to_address'].nunique(),
        'total_in_value': incoming['amount'].sum(),
        'total_out_value': outgoing['amount'].sum(),
        'avg_forward_delay_sec': compute_avg_delay(incoming, outgoing),
        'forward_ratio': outgoing['amount'].sum() / max(incoming['amount'].sum(), 1),
        'amount_variance_out': outgoing['amount'].std(),
        'dominant_destination_share': compute_dominant_share(outgoing),
        'deposit_heuristic_score': run_deposit_heuristic(address, transactions_df),
        'mixer_pattern_score': pattern_result['mixer_pattern_score'],
        'pattern_label': pattern_result['pattern_label'],
        'address_age_days': compute_address_age(address, transactions_df),
    }
    return features


def build_all_features(transactions_df):
    """
    Run build_address_features() for every unique address found in
    the transaction data, and return one combined DataFrame.
    """
    unique_addresses = pd.concat(
        [transactions_df['from_address'], transactions_df['to_address']]
    ).unique()

    all_features = [build_address_features(addr, transactions_df) for addr in unique_addresses]
    return pd.DataFrame(all_features)


if __name__ == "__main__":
    transactions_df = pd.read_csv("data/raw/all_transactions.csv")
    features_df = build_all_features(transactions_df)
    features_df.to_csv("data/processed/address_features.csv", index=False)
    print(f"Saved features for {len(features_df)} addresses to data/processed/address_features.csv")