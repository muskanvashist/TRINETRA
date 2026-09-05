# src/model/evaluate.py

import pandas as pd
import numpy as np
import joblib
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split


def load_saved_model(path="models/deposit_classifier_v1.pkl"):
    return joblib.load(path)


def load_test_split():
    """
    Rebuild the same train/test split used during training, so we
    evaluate on the same held-out data (not data the model already saw).
    """
    features_df = pd.read_csv("data/processed/address_features.csv")
    labels_df = pd.read_csv("data/labels/address_labels.csv")
    dataset = features_df.merge(labels_df, on='address')

    drop_cols = ['address', 'label', 'label_confidence']
    if 'pattern_label' in dataset.columns:
        drop_cols.append('pattern_label')

    X = dataset.drop(columns=[c for c in drop_cols if c in dataset.columns])
    y = dataset['label']

    X = X.replace([np.inf, -np.inf], np.nan).clip(lower=-1e10, upper=1e10).fillna(0)

    _, X_test, _, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    return X_test, y_test, X.columns


if __name__ == "__main__":
    model = load_saved_model()
    X_test, y_test, feature_names = load_test_split()

    y_pred = model.predict(X_test)
    print("\n--- Classification Report ---")
    print(classification_report(y_test, y_pred))

    print("--- Feature Importances ---")
    importances = pd.Series(model.feature_importances_, index=feature_names)
    print(importances.sort_values(ascending=False))