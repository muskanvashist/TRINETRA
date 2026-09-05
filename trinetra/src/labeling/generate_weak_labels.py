# src/labeling/generate_weak_labels.py

import pandas as pd

# --- Known label sources (update these from TronScan / Chainabuse) ---
KNOWN_EXCHANGES = {"address_xyz": "Binance", "address_abc": "WazirX"}
KNOWN_SCAMS = {"address_scam1"}


def assign_known_labels(transactions_df):
    """
    Step 1: Pull all unique addresses from the transaction data and
    assign labels for anything we already have external confirmation for.
    Everything else gets marked 'unknown' for now.
    """
    unique_addresses = pd.concat(
        [transactions_df['from_address'], transactions_df['to_address']]
    ).unique()

    address_labels = []
    for addr in unique_addresses:
        if addr in KNOWN_EXCHANGES:
            label, confidence = "exchange_hot_wallet", 1.0
        elif addr in KNOWN_SCAMS:
            label, confidence = "scam_reported", 1.0
        else:
            label, confidence = "unknown", 0.0

        address_labels.append({
            'address': addr,
            'label': label,
            'label_confidence': confidence
        })

    return pd.DataFrame(address_labels)


def apply_heuristic_labels(labels_df, address_features_df):
    """
    Step 2: For every address still marked 'unknown', use the precomputed
    heuristic scores (deposit_heuristic_score, mixer_pattern_score) to
    assign a weak label instead of leaving it unlabeled.

    address_features_df must already contain, per address:
      - 'deposit_heuristic_score'  (from your deposit-address heuristic)
      - 'mixer_pattern_score'      (from your mixer/pattern detector)
    """
    merged = labels_df.merge(address_features_df, on='address', how='left')

    for idx, row in merged.iterrows():
        if row['label'] != 'unknown':
            continue   # already confirmed via known list, don't overwrite

        deposit_score = row.get('deposit_heuristic_score', 0)
        mixer_score = row.get('mixer_pattern_score', 0)

        if deposit_score > 0.8:
            merged.at[idx, 'label'] = 'likely_deposit_address'
            merged.at[idx, 'label_confidence'] = deposit_score
        elif mixer_score > 0.7:
            merged.at[idx, 'label'] = 'likely_mixer'
            merged.at[idx, 'label_confidence'] = mixer_score
        else:
            merged.at[idx, 'label'] = 'likely_mule_or_normal'
            merged.at[idx, 'label_confidence'] = 0.5   # weakest label, default bucket

    return merged[['address', 'label', 'label_confidence']]


if __name__ == "__main__":
    # Load the transaction CSV built by fetch_trongrid.py
    transactions_df = pd.read_csv("data/raw/all_transactions.csv")

    # Load precomputed per-address features (built by feature_engineering.py)
    address_features_df = pd.read_csv("data/processed/address_features.csv")

    # Step 1: known labels (exchange / scam, from external sources)
    labels_df = assign_known_labels(transactions_df)

    # Step 2: fill unknowns using heuristic scores (deposit + mixer detection)
    labels_df = apply_heuristic_labels(labels_df, address_features_df)

    # Save final labeled file
    labels_df.to_csv("data/labels/address_labels.csv", index=False)
    print(f"Saved {len(labels_df)} labeled addresses to data/labels/address_labels.csv")
    print(labels_df['label'].value_counts())