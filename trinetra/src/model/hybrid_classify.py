# src/model/hybrid_classify.py

import pandas as pd

from src.heuristics.deposit_heuristic import run_deposit_heuristic
from src.heuristics.pattern_detection import run_pattern_detection
from src.model.predict import predict_address_type, load_model


def get_heuristic_result(address, transactions_df):
    """
    Package your existing heuristic outputs into the same
    {'label': ..., 'confidence': ...} shape as the ML result,
    so both can be compared directly in hybrid_classify().
    """
    deposit_score = run_deposit_heuristic(address, transactions_df)
    pattern_result = run_pattern_detection(address, transactions_df)

    if deposit_score > 0.8:
        label = 'likely_deposit_address'
        confidence = deposit_score
    elif pattern_result['mixer_pattern_score'] > 0.7:
        label = 'likely_mixer'
        confidence = pattern_result['mixer_pattern_score']
    else:
        label = 'likely_mule_or_normal'
        confidence = 0.5

    return {'label': label, 'confidence': confidence}


def hybrid_classify(address, heuristic_result, ml_result):
    """
    Combine the explainable heuristic with the ML prediction.
    ML never overrides the heuristic -- it either corroborates it
    (raising combined confidence) or is flagged as a disagreement
    for manual review, while the heuristic label stays authoritative.
    """
    if heuristic_result['label'] == ml_result['predicted_label']:
        return {
            'label': heuristic_result['label'],
            'confidence': (heuristic_result['confidence'] + ml_result['confidence']) / 2,
            'method': 'heuristic + ML agreement'
        }
    else:
        return {
            'label': heuristic_result['label'],
            'confidence': heuristic_result['confidence'] * 0.7,   # lower confidence
            'method': 'heuristic only (ML disagreed, flagged for manual review)',
            'ml_alternative': ml_result['predicted_label']
        }


def classify_address(address, transactions_df, model=None):
    """
    End-to-end: run both the heuristic and the ML model on one address,
    then combine them. This is the single function your FastAPI/Streamlit
    app should actually call.
    """
    if model is None:
        model = load_model()

    heuristic_result = get_heuristic_result(address, transactions_df)
    ml_result = predict_address_type(address, transactions_df, model)

    return hybrid_classify(address, heuristic_result, ml_result)


if __name__ == "__main__":
    transactions_df = pd.read_csv("data/raw/all_transactions.csv")
    test_address = transactions_df['to_address'].iloc[0]

    result = classify_address(test_address, transactions_df)

    print(f"\nAddress: {test_address}")
    print(f"Final label: {result['label']}")
    print(f"Confidence: {round(result['confidence'], 3)}")
    print(f"Method: {result['method']}")
    if 'ml_alternative' in result:
        print(f"ML suggested instead: {result['ml_alternative']}")