"""
Offline retraining script — runs once when the encoding or models change.

What it does, in plain English
------------------------------
1. Loads the training data and cleans the case-mismatch in Elevation
   ("moderate" → "Moderate").
2. Applies a *mixed* encoding strategy:
      - Ordered features (Elevation, Drainage Density, Tree Height,
        Tree Vigour) → OrdinalEncoder with the order specified
        explicitly, in physical/geological order.
      - Unordered features (Soil Texture, Geological Features) →
        OneHotEncoder, so the model treats them as distinct categories
        rather than as numbers on a line.
3. Trains three candidate classifiers — SVM, Random Forest, Gradient
   Boosting — with light hyperparameter tuning, all evaluated by
   stratified 5-fold cross-validation.  The dataset is imbalanced
   (~2:1 Low : High), so we score primarily on F1, not accuracy.
4. Picks the best one by mean cross-validated F1 score, refits it on
   the full training set, and saves the complete pipeline (encoder +
   scaler + classifier) to artifacts/pipeline.pkl.
5. As a sanity check, also evaluates Benson's *original* SVM on the
   same cross-validation splits so we have an honest before/after.

Run it with:
    python retrain_model.py

Re-run it whenever:
    - The dataset is updated.
    - The orderings below need correcting (Benson is the right person to
      decide; current values are reasonable defaults marked as TODO).
    - You want to try different model families.
"""

import os
import sys
import warnings
import joblib
import numpy as np
import pandas as pd

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import f1_score
from sklearn.model_selection import GridSearchCV, StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler
from sklearn.svm import SVC

warnings.filterwarnings("ignore")  # keeps the comparison output readable

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE          = os.path.dirname(os.path.abspath(__file__))
ARTIFACTS_DIR = os.path.join(HERE, "artifacts")
DATA_PATH     = os.path.join(ARTIFACTS_DIR, "augmented_data.csv")
PIPELINE_OUT  = os.path.join(ARTIFACTS_DIR, "pipeline.pkl")
FEATURES_OUT  = os.path.join(ARTIFACTS_DIR, "selected_features.pkl")
METADATA_OUT  = os.path.join(ARTIFACTS_DIR, "encoding_metadata.pkl")

TARGET_COL = "Decision"
POSITIVE_LABEL = "High Potential"   # → encoded as 1; "Low Potential" → 0

# ---------------------------------------------------------------------------
# Feature configuration — THIS IS THE HEART OF THE NEW ENCODING.
# ---------------------------------------------------------------------------
# Boruta selected these six features in the original work; we keep that
# selection unchanged.  (Re-running Boruta on the new encoding is a sensible
# next step but is out of scope for this change.)
SELECTED_FEATURES = [
    "Soil.Texture",
    "Geological.Features",
    "Elevation",
    "Natural.vegetation..tree..vigour",
    "Natural.vegetation..tree..height",
    "Drainage.Density",
]

# Ordered features — the categories have a real physical/geological
# progression, so OrdinalEncoder with the order specified is correct.
#
# TODO(BENSON): these orderings are a defensible default but should be
# confirmed by the domain expert.  The pipeline can be re-trained any
# time these change — just edit and re-run this script.
ORDERED_FEATURES = {
    "Elevation":                         ["Gentle", "Moderate", "Steep"],
    "Drainage.Density":                  ["Low", "Medium", "High"],
    "Natural.vegetation..tree..height":  ["Short", "Medium", "Tall"],
    "Natural.vegetation..tree..vigour":  [
        "Absent", "Low Water Demand", "Moderate Water Demand", "High Water Demand",
    ],
}

# Unordered features — one-hot encoded, no fake numerical relationships.
UNORDERED_FEATURES = ["Soil.Texture", "Geological.Features"]


# ---------------------------------------------------------------------------
# Data loading + cleaning
# ---------------------------------------------------------------------------
def load_clean_data():
    df = pd.read_csv(DATA_PATH)

    # Fix the "Moderate" / "moderate" case-mismatch in Elevation.  This was
    # silently being treated as two separate categories by the original encoder.
    df["Elevation"] = df["Elevation"].str.strip().replace({"moderate": "Moderate"})

    # Sanity-check that every observed value matches the configured ordering,
    # otherwise the OrdinalEncoder will throw a confusing error during fit.
    for col, order in ORDERED_FEATURES.items():
        observed = set(df[col].dropna().unique())
        unknown  = observed - set(order)
        if unknown:
            raise ValueError(
                f"Column {col!r} has values {sorted(unknown)} not present in "
                f"its configured ordering {order}.  Edit ORDERED_FEATURES."
            )

    X = df[SELECTED_FEATURES].copy()
    y = (df[TARGET_COL] == POSITIVE_LABEL).astype(int)
    return X, y


# ---------------------------------------------------------------------------
# Build the encoder pipeline
# ---------------------------------------------------------------------------
def build_preprocessor():
    """
    A single ColumnTransformer that:
      - ordinal-encodes the ordered features in the correct physical order
      - one-hot-encodes the unordered features
      - then standard-scales everything (so SVM sees comparable magnitudes)

    Pickling this whole transformer guarantees identical encoding at
    train and predict time — no risk of drift between the two sites.
    """
    ordered_in_pipeline = list(ORDERED_FEATURES.keys())
    ordered_categories  = [ORDERED_FEATURES[c] for c in ordered_in_pipeline]

    encoder = ColumnTransformer(
        transformers=[
            (
                "ordered",
                OrdinalEncoder(categories=ordered_categories),
                ordered_in_pipeline,
            ),
            (
                "unordered",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                UNORDERED_FEATURES,
            ),
        ],
        remainder="drop",
    )
    return encoder


# ---------------------------------------------------------------------------
# Model candidates with light hyperparameter grids
# ---------------------------------------------------------------------------
def candidate_models():
    """
    Each entry returns: (display_name, sklearn_estimator, param_grid).
    The grids are intentionally small — the dataset has 252 rows, and
    over-tuning on a tiny dataset is just memorising noise.
    """
    return [
        (
            "SVM (RBF)",
            SVC(probability=True, class_weight="balanced", random_state=42),
            {
                "clf__C":      [0.1, 1, 10],
                "clf__kernel": ["rbf", "linear"],
                "clf__gamma":  ["scale", "auto"],
            },
        ),
        (
            "Random Forest",
            RandomForestClassifier(class_weight="balanced", random_state=42, n_jobs=1),
            {
                "clf__n_estimators":     [100, 300],
                "clf__max_depth":        [None, 5, 10],
                "clf__min_samples_leaf": [1, 2],
            },
        ),
        (
            "Gradient Boosting",
            GradientBoostingClassifier(random_state=42),
            {
                "clf__n_estimators":  [100, 200],
                "clf__learning_rate": [0.05, 0.1],
                "clf__max_depth":     [3, 5],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Comparison runner
# ---------------------------------------------------------------------------
def evaluate_candidates(X, y):
    """
    Run a grid search per candidate and return a results table plus the
    refitted best pipeline.
    """
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    results = []

    for name, estimator, grid in candidate_models():
        pipe = Pipeline(steps=[
            ("encode", build_preprocessor()),
            ("scale",  StandardScaler(with_mean=False)),  # works for sparse + dense
            ("clf",    estimator),
        ])

        gs = GridSearchCV(
            pipe, grid,
            scoring={"f1": "f1", "accuracy": "accuracy", "roc_auc": "roc_auc"},
            refit="f1",
            cv=cv, n_jobs=1, return_train_score=False,
        )
        gs.fit(X, y)

        best_idx = gs.best_index_
        results.append({
            "name":     name,
            "best_params": gs.best_params_,
            "f1_mean":  gs.cv_results_["mean_test_f1"][best_idx],
            "f1_std":   gs.cv_results_["std_test_f1"][best_idx],
            "acc_mean": gs.cv_results_["mean_test_accuracy"][best_idx],
            "acc_std":  gs.cv_results_["std_test_accuracy"][best_idx],
            "auc_mean": gs.cv_results_["mean_test_roc_auc"][best_idx],
            "auc_std":  gs.cv_results_["std_test_roc_auc"][best_idx],
            "pipeline": gs.best_estimator_,
        })

    # Pick best by mean CV F1
    best = max(results, key=lambda r: r["f1_mean"])
    return results, best


# ---------------------------------------------------------------------------
# Honest baseline: how does Benson's ORIGINAL SVM score on the same splits?
# ---------------------------------------------------------------------------
def evaluate_original_baseline(X_raw_df, y):
    """
    Apply the original encoding logic (OrdinalEncoder over all dataset
    columns minus 'Decision', then subset to selected features, then
    StandardScaler, then SVC) and cross-validate on the same folds.
    Returns mean F1 or None if the original artifacts can't be loaded.
    """
    try:
        original_encoder = joblib.load(os.path.join(ARTIFACTS_DIR, "encoder.pkl"))
        original_scaler  = joblib.load(os.path.join(ARTIFACTS_DIR, "scaler.pkl"))
        original_model   = joblib.load(os.path.join(ARTIFACTS_DIR, "svm_model.pkl"))
    except Exception as exc:
        print(f"[baseline] Could not load original artifacts: {exc}")
        return None

    df_full = pd.read_csv(DATA_PATH)
    full_features = [c for c in df_full.columns if c != TARGET_COL]
    X_full = df_full[full_features]

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    f1s = []
    for tr_idx, te_idx in cv.split(X_full, y):
        # Just evaluate the pretrained model (don't re-fit) — that's what's
        # actually deployed in the current Flask app.
        X_te = X_full.iloc[te_idx]
        y_te = y.iloc[te_idx]
        try:
            encoded = original_encoder.transform(X_te)
            encoded_df = pd.DataFrame(encoded, columns=full_features)
            selected = encoded_df[SELECTED_FEATURES]
            scaled   = original_scaler.transform(selected)
            preds    = original_model.predict(scaled)
            f1s.append(f1_score(y_te, preds))
        except Exception as exc:
            print(f"[baseline] Fold failed: {exc}")
            return None

    return float(np.mean(f1s)), float(np.std(f1s))


# ---------------------------------------------------------------------------
# Pretty-print
# ---------------------------------------------------------------------------
def print_report(results, best, baseline):
    bar = "─" * 78
    print()
    print(bar)
    print("MODEL COMPARISON  —  stratified 5-fold cross-validation, scored by F1")
    print(bar)
    print(f"{'Model':<22} {'F1 mean ± std':<20} {'Accuracy':<18} {'ROC-AUC':<14}")
    print(bar)
    for r in results:
        marker = "★" if r is best else " "
        print(
            f"{marker} {r['name']:<20} "
            f"{r['f1_mean']:.3f} ± {r['f1_std']:.3f}      "
            f"{r['acc_mean']:.3f} ± {r['acc_std']:.3f}    "
            f"{r['auc_mean']:.3f} ± {r['auc_std']:.3f}"
        )
    print(bar)

    if baseline is not None:
        print(f"\nBaseline — Benson's original SVM on the same folds:")
        print(f"  F1 mean = {baseline[0]:.3f} ± {baseline[1]:.3f}")
        delta = best['f1_mean'] - baseline[0]
        sign = "+" if delta >= 0 else ""
        print(f"  Improvement of new winner over baseline: {sign}{delta:.3f} F1")

    print(f"\nWinner: {best['name']}")
    print(f"Best hyperparameters: {best['best_params']}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Loading and cleaning data…")
    X, y = load_clean_data()
    print(f"  rows={len(X)}, class balance: 1 (High)={int(y.sum())}, 0 (Low)={int((1-y).sum())}")

    print("\nEvaluating SVM, Random Forest, Gradient Boosting…")
    results, best = evaluate_candidates(X, y)

    print("\nEvaluating Benson's ORIGINAL SVM as a baseline on the same splits…")
    baseline = evaluate_original_baseline(X, y)

    print_report(results, best, baseline)

    print(f"Saving winning pipeline → {os.path.relpath(PIPELINE_OUT, HERE)}")
    joblib.dump(best["pipeline"], PIPELINE_OUT)

    # Re-save selected_features.pkl (unchanged content, but it's in the
    # artifacts folder so we keep it in sync).
    joblib.dump(SELECTED_FEATURES, FEATURES_OUT)

    # Save metadata so the frontend knows which features are ordered, in
    # what order, vs unordered — used by ml.predictor_options() to render
    # dropdowns in physical order rather than alphabetical.
    metadata = {
        "ordered":   ORDERED_FEATURES,
        "unordered": {f: sorted(X[f].dropna().unique().tolist()) for f in UNORDERED_FEATURES},
        "winner":    best["name"],
        "winner_params": best["best_params"],
        "winner_f1": best["f1_mean"],
    }
    joblib.dump(metadata, METADATA_OUT)
    print(f"Saved metadata     → {os.path.relpath(METADATA_OUT, HERE)}")
    print(f"Saved feature list → {os.path.relpath(FEATURES_OUT, HERE)}")

    print("\nDone.  Restart the Flask app to use the new pipeline.")


if __name__ == "__main__":
    main()
