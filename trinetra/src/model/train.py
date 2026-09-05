# src/model/train.py

import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
import joblib
import os


def load_dataset():
    """
    Load features (from feature_engineering.py output) and labels
    (from generate_weak_labels.py output), merge them into one dataset.
    """
    features_df = pd.read_csv("data/processed/address_features.csv")
    labels_df = pd.read_csv("data/labels/address_labels.csv")

    dataset = features_df.merge(labels_df, on='address')
    return dataset


def train_model(dataset):
    """
    Split into train/test, train a Random Forest, and return the
    fitted model along with the test split for evaluation.
    """
    # Drop non-numeric / identifier columns before training
    drop_cols = ['address', 'label', 'label_confidence']
    if 'pattern_label' in dataset.columns:
        drop_cols.append('pattern_label')   # text column, not a numeric feature

    X = dataset.drop(columns=[c for c in drop_cols if c in dataset.columns])
    y = dataset['label']

    # Clean infinities and extreme values BEFORE filling NaNs.
    # Some addresses can produce astronomically large numbers (e.g. a
    # scam/dust token with a malformed 'value' field, or a divide-by-
    # near-zero in forward_ratio) which overflow float32 during training.
    X = X.replace([np.inf, -np.inf], np.nan)

    # Report which columns had bad values, for debugging
    problem_cols = X.columns[X.isna().any() & (dataset[X.columns].apply(
        lambda col: col.map(lambda v: v in [np.inf, -np.inf])).any())]
    if len(problem_cols) > 0:
        print(f"Warning: replaced inf/-inf values in columns: {list(problem_cols)}")

    # Clip anything still absurdly large (protects against overflow even
    # without literal inf, e.g. values like 1e30)
    X = X.clip(lower=-1e10, upper=1e10)

    # Fill any missing feature values (e.g. avg_forward_delay_sec can be None)
    X = X.fillna(0)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=8,
        class_weight='balanced',   # important -- your classes are imbalanced
        random_state=42
    )
    model.fit(X_train, y_train)

    return model, X_test, y_test, X.columns


def evaluate_model(model, X_test, y_test, feature_names):
    """Print classification metrics and feature importances."""
    y_pred = model.predict(X_test)
    print("\n--- Classification Report ---")
    print(classification_report(y_test, y_pred))

    print("--- Feature Importances ---")
    importances = pd.Series(model.feature_importances_, index=feature_names)
    print(importances.sort_values(ascending=False))


def save_model(model, path="models/deposit_classifier_v1.pkl"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    joblib.dump(model, path)
    print(f"\nModel saved to {path}")


if __name__ == "__main__":
    dataset = load_dataset()
    print(f"Loaded {len(dataset)} labeled addresses for training.")

    model, X_test, y_test, feature_names = train_model(dataset)
    evaluate_model(model, X_test, y_test, feature_names)
    save_model(model)