# src/ingestion/fetch_transaction.py
#
# Given a specific transaction hash (which a victim typically has from
# their own wallet app's transaction history/receipt), fetch the exact
# amount, timestamp, sender, and receiver for that ONE transaction.
# This is more precise than starting from an address alone, since an
# address may have hundreds of unrelated transactions -- a transaction
# ID pinpoints exactly which transfer the victim is reporting.

import os
import requests
from dotenv import load_dotenv

load_dotenv()
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY")
USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


def fetch_transaction_by_id(tx_hash):
    """
    Fetch a single TRC-20 (USDT) transaction's decoded details by its
    transaction hash. Returns from/to/amount/timestamp/hash, or None
    if the transaction isn't found or isn't a USDT transfer.

    Endpoint: TronGrid's transaction events endpoint decodes the
    Transfer event parameters (from, to, value) directly, since raw
    TRC-20 transfers are logged as smart contract events rather than
    plain native transfers.
    """
    url = f"https://api.trongrid.io/v1/transactions/{tx_hash}/events"
    headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY}

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json().get('data', [])
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch transaction {tx_hash}: {e}")
        return None

    for event in data:
        if event.get('contract_address') != USDT_CONTRACT:
            continue
        if event.get('event_name') != 'Transfer':
            continue

        result = event.get('result', {})
        return {
            'from': result.get('from'),
            'to': result.get('to'),
            'amount': int(result.get('value', 0)) / 1_000_000,   # 6 decimals for USDT-TRC20
            'timestamp': event.get('block_timestamp', 0) / 1000,  # ms -> seconds
            'hash': tx_hash
        }

    print(f"No USDT Transfer event found for transaction {tx_hash}. "
          f"It may not be a USDT-TRC20 transfer, or the hash may be incorrect.")
    return None


if __name__ == "__main__":
    test_hash = "072ca2449c203fbaafe16138ab1f1496756ac54b0d070f190436a3279092ce6d"
    result = fetch_transaction_by_id(test_hash)
    if result:
        print(f"From: {result['from']}")
        print(f"To: {result['to']}")
        print(f"Amount: {result['amount']:.2f} USDT")
        print(f"Timestamp: {result['timestamp']}")