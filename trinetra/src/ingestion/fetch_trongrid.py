# src/ingestion/fetch_trongrid.py

import os
import json
import time
import requests
import pandas as pd
from dotenv import load_dotenv

# --- Setup ---
load_dotenv()
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY")
USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"

RAW_JSON_DIR = "data/raw/trongrid_transactions"
RAW_CSV_PATH = "data/raw/all_transactions.csv"

MAX_HOPS = 5                 # how many hops forward to auto-expand
MAX_ADDRESSES_PER_HOP = 10   # safety cap so one victim case doesn't explode API usage


def fetch_transactions(address, limit=200):
    """Fetch USDT-TRC20 transactions for a single address from TronGrid."""
    url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
    headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY}
    params = {"contract_address": USDT_CONTRACT, "limit": limit}
    resp = requests.get(url, headers=headers, params=params)
    resp.raise_for_status()
    return resp.json().get('data', [])


def save_raw_json(address, data):
    """Save the untouched API response for this address (audit trail)."""
    os.makedirs(RAW_JSON_DIR, exist_ok=True)
    filepath = os.path.join(RAW_JSON_DIR, f"{address}.json")
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)


def normalise_transactions(address, raw_data):
    """Convert raw JSON transactions into clean rows for the CSV."""
    rows = []
    for tx in raw_data:
        rows.append({
            'from_address': tx['from'],
            'to_address': tx['to'],
            'amount': int(tx['value'] )/ 1_000_000,
            'timestamp':int(tx['block_timestamp']) / 1000,
            'hash': tx['transaction_id'],
            'queried_address': address,
            'hop_level': None   # filled in by the traversal loop below
        })
    return rows


def get_next_hop_addresses(rows, from_address):
    """
    From this address's outgoing transactions, find where the money went next.
    These become the seed addresses for the NEXT hop.
    """
    outgoing = [r for r in rows if r['from_address'] == from_address]
    outgoing_sorted = sorted(outgoing, key=lambda r: r['amount'], reverse=True)
    next_addresses = [r['to_address'] for r in outgoing_sorted[:MAX_ADDRESSES_PER_HOP]]
    return next_addresses


def trace_from_victim(victim_address, max_hops=MAX_HOPS):
    """
    Start from ONE victim-reported address and automatically expand
    outward hop by hop, collecting every address touched along the way.
    This replaces manually typing a seed_addresses list.
    """
    all_transactions = []
    visited = set()                      # avoid re-fetching the same address twice
    current_hop_addresses = [victim_address]

    for hop in range(max_hops):
        next_hop_addresses = []

        for addr in current_hop_addresses:
            if addr in visited:
                continue
            visited.add(addr)

            print(f"Hop {hop} -- fetching: {addr}")
            try:
                raw_data = fetch_transactions(addr)
            except requests.exceptions.RequestException as e:
                print(f"  Failed for {addr}: {e}")
                continue

            save_raw_json(addr, raw_data)

            rows = normalise_transactions(addr, raw_data)
            for r in rows:
                r['hop_level'] = hop      # tag which hop this transaction was found at
            all_transactions.extend(rows)

            # discover where THIS address sent money -> next hop's addresses
            discovered = get_next_hop_addresses(rows, addr)
            next_hop_addresses.extend(discovered)

            time.sleep(0.3)   # respect rate limits

        current_hop_addresses = list(set(next_hop_addresses) - visited)
        if not current_hop_addresses:
            print(f"No further addresses to trace after hop {hop}. Stopping early.")
            break

    os.makedirs(os.path.dirname(RAW_CSV_PATH), exist_ok=True)
    df = pd.DataFrame(all_transactions)
    df.to_csv(RAW_CSV_PATH, index=False)
    print(f"\nSaved {len(df)} transactions across {len(visited)} addresses to {RAW_CSV_PATH}")
    return df


if __name__ == "__main__":
    victim_address = "TWXLTtvZKonEmYA2NNLSw5goGooeWT7Vj9" #real address add krna h
    trace_from_victim(victim_address, max_hops=MAX_HOPS)