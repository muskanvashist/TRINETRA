# src/ingestion/fetch_labels.py

import os
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

TRONSCAN_API_KEY = os.getenv("TRONSCAN_API_KEY")   # optional, higher rate limits with a key
CHAINABUSE_API_KEY = os.getenv("CHAINABUSE_API_KEY")  # check current Chainabuse docs for auth requirements


def fetch_known_exchanges(addresses_to_check):
    """
    Check a list of addresses against TronScan's account detail endpoint
    and keep any that carry a public tag (exchange/service label).

    Note: TronScan does not expose a single "give me all exchange
    addresses" endpoint -- you check addresses ONE AT A TIME via
    /api/accountv2?address=... and see if publicTag is present.
    This is why you need a starting list of candidate addresses
    (e.g. hot wallets discovered by your own in-degree heuristic)
    to check against, rather than pulling a bulk list.
    """
    tagged = []
    headers = {"TRON-PRO-API-KEY": TRONSCAN_API_KEY} if TRONSCAN_API_KEY else {}

    for addr in addresses_to_check:
        url = "https://apilist.tronscanapi.com/api/accountv2"
        try:
            resp = requests.get(url, headers=headers, params={"address": addr}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            print(f"Failed to check {addr}: {e}")
            continue

        public_tag = data.get("publicTag") or data.get("accountType")
        if public_tag:
            tagged.append({"address": addr, "label": public_tag, "type": "exchange"})

    return tagged


def fetch_scam_labels():
    """
    Pull Chainabuse community-reported scam addresses (TRM Labs, free).

    IMPORTANT: Chainabuse uses HTTP Basic Authentication -- your API key
    goes in BOTH the username and password fields, not a Bearer token.
    Free tier is limited to 10 calls/month (1 call = up to 50 reports),
    so don't call this repeatedly during testing.
    """
    url = "https://api.chainabuse.com/v0/reports"

    if not CHAINABUSE_API_KEY:
        print("No CHAINABUSE_API_KEY set -- skipping scam label fetch.")
        return []

    try:
        resp = requests.get(
            url,
            auth=(CHAINABUSE_API_KEY, CHAINABUSE_API_KEY),   # Basic Auth: key as both user & pass
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch scam labels: {e}")
        return []

    # Response is typically {"data": [...]} -- adjust if the shape differs
    reports = data.get("data", data) if isinstance(data, dict) else data

    return [
        {"address": r["address"], "label": "scam", "type": "scam"}
        for r in reports if r.get("address")
    ]


def save_labels_to_csv(exchanges, scams, path="data/external/known_exchanges.csv"):
    """Combine both label sources and save as one reference CSV."""
    all_labels = exchanges + scams
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df = pd.DataFrame(all_labels)
    df.to_csv(path, index=False)
    print(f"Saved {len(df)} known labels to {path}")
    return df


def get_candidate_addresses_from_features(top_n=20):
    """
    Instead of manually typing addresses, pull the top N addresses by
    in-degree from your own feature_engineering.py output -- these are
    your system's own "exchange-class infrastructure" candidates,
    exactly the ones worth checking against TronScan's public tags.
    """
    try:
        features_df = pd.read_csv("data/processed/address_features.csv")
    except FileNotFoundError:
        print("address_features.csv not found -- run feature_engineering.py first.")
        return []

    top_candidates = features_df.sort_values("in_degree", ascending=False).head(top_n)
    return top_candidates["address"].tolist()


if __name__ == "__main__":
    candidate_addresses = get_candidate_addresses_from_features(top_n=20)

    if not candidate_addresses:
        # fallback if features file isn't ready yet
        candidate_addresses = ["TWXLTtvZKonEmYA2NNLSw5goGooeWT7Vj9"]

    exchanges = fetch_known_exchanges(candidate_addresses)
    scams = fetch_scam_labels()

    save_labels_to_csv(exchanges, scams)