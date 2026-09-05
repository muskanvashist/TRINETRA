# src/model/predict.py

import joblib
import pandas as pd
import numpy as np

from src.features.feature_engineering import build_address_features


MODEL_PATH = "models/deposit_classifier_v1.pkl"


def load_model(path=MODEL_PATH):
    return joblib.load(path)


def predict_address_type(address, transactions_df, model=None):
    """
    Given a single address and the transactions data it appears in,
    compute the same features used during training and predict its
    label using the saved model.
    """
    if model is None:
        model = load_model()

    # Build the same feature dict used during training
    features = build_address_features(address, transactions_df)

    # Drop columns that aren't model inputs (address itself, text labels)
    drop_keys = ['address', 'pattern_label']
    feature_row = {k: v for k, v in features.items() if k not in drop_keys}

    # Convert to a DataFrame with the SAME column order the model expects
    feature_df = pd.DataFrame([feature_row])
    feature_df = feature_df.reindex(columns=model.feature_names_in_, fill_value=0)

    # Same cleaning as train.py -- some addresses produce extreme/infinite
    # values (e.g. a malformed token amount, or a near-zero divide in
    # forward_ratio) that overflow float32 during prediction.
    feature_df = feature_df.replace([np.inf, -np.inf], np.nan)
    feature_df = feature_df.clip(lower=-1e10, upper=1e10)
    feature_df = feature_df.fillna(0)

    proba = model.predict_proba(feature_df)[0]
    predicted_class = model.classes_[proba.argmax()]
    confidence = proba.max()

    return {
        'address': address,
        'predicted_label': predicted_class,
        'confidence': round(float(confidence), 3),
        'all_class_probabilities': dict(zip(model.classes_, proba.round(3)))
    }


if __name__ == "__main__":
    transactions_df = pd.read_csv("data/raw/all_transactions.csv")
    model = load_model()

    # Test on the first address found in the data
    test_address = transactions_df['to_address'].iloc[0]
    result = predict_address_type(test_address, transactions_df, model)

    print(f"\nAddress: {result['address']}")
    print(f"Predicted label: {result['predicted_label']}")
    print(f"Confidence: {result['confidence']}")
    print(f"All class probabilities: {result['all_class_probabilities']}")