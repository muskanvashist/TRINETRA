import pandas as pd

def normalise_transactions(raw_json_list):
    rows = []
    for tx in raw_json_list:
        rows.append({
            'from': tx['from'],
            'to': tx['to'],
            'amount': tx['value'] / 1_000_000,       # 6 decimals for USDT-TRC20
            'timestamp': tx['block_timestamp'] / 1000, # ms -> seconds
            'hash': tx['transaction_id']
        })
    return pd.DataFrame(rows)