"""
Feature selection — re-run Boruta on the new encoding.

Why this exists
---------------
Benson's original Boruta run was performed against the OLD encoding, where
every categorical feature was forced through a single OrdinalEncoder.
With the new mixed encoding (ordinal-with-order for ordered features,
one-hot for unordered ones) the feature space the algorithm sees is
genuinely different, so the selection might be too.

We give Boruta access to ALL seven raw features in the dataset, including
the one Benson originally dropped (Soil.Colour). Otherwise we wouldn't
really be re-evaluating — we'd just be confirming the prior choice on a
pre-filtered set.

How the aggregation works
-------------------------
After encoding, a raw feature can correspond to multiple columns
(Soil.Type → 3 one-hot columns, for example). Boruta operates at the
column level, so we aggregate: a raw feature is "selected" if Boruta
confirms or marks tentative ANY of its encoded columns.

Tentative vs Confirmed
----------------------
Boruta has three verdicts: Confirmed, Tentative, Rejected. With only 252
rows, "Tentative" is common and often reflects real signal that just
needs more data to firm up. We keep both Confirmed and Tentative, and
only drop the outright Rejects.

Reproducibility
---------------
random_state=42 throughout. Re-running this script on unchanged data
will give the same answer.
"""

import os
import warnings

import joblib
import numpy as np
import pandas as pd
from boruta import BorutaPy
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HERE          = os.path.dirname(os.path.abspath(__file__))
ARTIFACTS_DIR = os.path.join(HERE, "artifacts")
DATA_PATH     = os.path.join(ARTIFACTS_DIR, "augmented_data.csv")

TARGET_COL     = "Decision"
POSITIVE_LABEL = "High Potential"

# All 7 raw candidate features.  Note that Soil.Colour is included —
# Benson dropped it on the old encoding, but we want Boruta to make that
# call again on the new one.
ALL_CANDIDATES = [
    "Soil.Type",
    "Soil.Colour",
    "Geological.Features",
    "Elevation",
    "Natural.vegetation..tree..vigour",
    "Natural.vegetation..tree..height",
    "Drainage.Density",
]

# Same orderings as retrain_model.py — keep them in sync.
ORDERED_FEATURES = {
    "Elevation":                         ["Gentle", "Moderate", "Steep"],
    "Drainage.Density":                  ["Low", "Medium", "High"],
    "Natural.vegetation..tree..height":  ["Short", "Medium", "Tall"],
    "Natural.vegetation..tree..distribution":  ["Sparse", "Dense", "Medium"], 
    "Natural.vegetation..tree..distribution":  ["Bare Grasslands", "Shrubs", "Combination (Wooded Grasslands)"], 
    
    "Natural.vegetation..tree..vigour":  [
        "Absent", "Low Water Demand", "Moderate Water Demand", "High Water Demand",
    ],
}
UNORDERED_FEATURES = ["Soil.Type", "Geological.Features", "Soil.Colour"]


# ---------------------------------------------------------------------------
# Data loading + cleaning (mirrors retrain_model.py)
# ---------------------------------------------------------------------------
def load_clean_data():
    df = pd.read_csv(DATA_PATH)
    df["Elevation"] = df["Elevation"].str.strip().replace({"moderate": "Moderate"})
    X = df[ALL_CANDIDATES].copy()
    y = (df[TARGET_COL] == POSITIVE_LABEL).astype(int).values
    return X, y


# ---------------------------------------------------------------------------
# Encoding — same shape as the production pipeline
# ---------------------------------------------------------------------------
def build_preprocessor():
    ordered_in_pipeline = list(ORDERED_FEATURES.keys())
    ordered_categories  = [ORDERED_FEATURES[c] for c in ordered_in_pipeline]
    return ColumnTransformer(
        transformers=[
            ("ordered",   OrdinalEncoder(categories=ordered_categories), ordered_in_pipeline),
            ("unordered", OneHotEncoder(handle_unknown="ignore", sparse_output=False), UNORDERED_FEATURES),
        ],
        remainder="drop",
    )


def encoded_to_raw_map(preprocessor):
    """
    Returns {encoded_column_name: raw_feature_name} so we can aggregate
    Boruta's per-column verdicts back to per-feature verdicts.
    """
    out = {}
    for transformer_name, _, columns in preprocessor.transformers_:
        if transformer_name == "ordered":
            for col in columns:
                out[f"ordered__{col}"] = col
        elif transformer_name == "unordered":
            ohe = preprocessor.named_transformers_["unordered"]
            ohe_names = ohe.get_feature_names_out(columns)
            # ohe_names look like "Soil.Type_Clay" — the prefix before
            # the first underscore-with-known-category is the raw feature.
            for ohe_name in ohe_names:
                # Find which raw column this name was generated from
                for col in columns:
                    if ohe_name.startswith(col + "_"):
                        out[f"unordered__{ohe_name}"] = col
                        break
    return out


# ---------------------------------------------------------------------------
# Run Boruta
# ---------------------------------------------------------------------------
def run_boruta(X_encoded, y):
    # Boruta wraps Random Forest. n_estimators='auto' means Boruta picks
    # a sensible value based on the data dimensions.
    rf = RandomForestClassifier(
        n_jobs=1, class_weight="balanced", max_depth=5, random_state=42,
    )
    selector = BorutaPy(
        rf,
        n_estimators="auto",
        verbose=0,
        random_state=42,
        max_iter=100,           # default is 100; plenty for a 252-row dataset
        perc=100,               # 100 = strictest; lower = more permissive
    )
    selector.fit(X_encoded, y)
    return selector


# ---------------------------------------------------------------------------
# Aggregate per-column verdicts back to per-feature verdicts
# ---------------------------------------------------------------------------
def aggregate_per_feature(selector, encoded_names, name_to_raw):
    """
    Returns:
        {raw_feature: {"verdict": "confirmed"|"tentative"|"rejected",
                       "columns": [(encoded_name, verdict), ...]}}

    A raw feature's verdict is the strongest verdict among its columns:
        any "confirmed"  → confirmed
        else any "tentative" → tentative
        else                  → rejected
    """
    confirmed_mask = selector.support_           # bool array, len = n_encoded_cols
    tentative_mask = selector.support_weak_

    per_column = {}
    for i, ename in enumerate(encoded_names):
        if confirmed_mask[i]:
            per_column[ename] = "confirmed"
        elif tentative_mask[i]:
            per_column[ename] = "tentative"
        else:
            per_column[ename] = "rejected"

    grouped = {}
    for ename, verdict in per_column.items():
        raw = name_to_raw[ename]
        grouped.setdefault(raw, []).append((ename, verdict))

    out = {}
    for raw, items in grouped.items():
        verdicts = {v for _, v in items}
        if "confirmed" in verdicts:
            top = "confirmed"
        elif "tentative" in verdicts:
            top = "tentative"
        else:
            top = "rejected"
        out[raw] = {"verdict": top, "columns": items}
    return out


# ---------------------------------------------------------------------------
# Pretty-print
# ---------------------------------------------------------------------------
def print_report(per_feature, original_selection):
    bar = "─" * 78
    print()
    print(bar)
    print("BORUTA RESULTS  —  re-run on the new mixed encoding")
    print(bar)

    confirmed  = [f for f, info in per_feature.items() if info["verdict"] == "confirmed"]
    tentative  = [f for f, info in per_feature.items() if info["verdict"] == "tentative"]
    rejected   = [f for f, info in per_feature.items() if info["verdict"] == "rejected"]

    def label(name):
        in_orig = "(was in original selection)" if name in original_selection else "(NEW — was dropped originally)"
        return f"  {name:<40} {in_orig}"

    print(f"\nCONFIRMED ({len(confirmed)}) — clearly useful:")
    for f in sorted(confirmed):
        print(label(f))

    print(f"\nTENTATIVE ({len(tentative)}) — borderline, keep them:")
    for f in sorted(tentative):
        print(label(f))

    print(f"\nREJECTED ({len(rejected)}) — drop these:")
    for f in sorted(rejected):
        print(label(f))

    selection = sorted(confirmed + tentative)
    print(f"\n{bar}")
    print(f"NEW SELECTION ({len(selection)} features): {selection}")
    print(f"OLD SELECTION ({len(original_selection)} features): {sorted(original_selection)}")

    if set(selection) == set(original_selection):
        print("\n→ No change.  Boruta confirms the original selection on the new encoding.")
    else:
        added   = set(selection) - set(original_selection)
        dropped = set(original_selection) - set(selection)
        if added:
            print(f"\n→ Add to selection: {sorted(added)}")
        if dropped:
            print(f"→ Remove from selection: {sorted(dropped)}")
    print(bar)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Loading and cleaning data…")
    X, y = load_clean_data()
    print(f"  rows={len(X)}, candidates={len(ALL_CANDIDATES)}")

    print("\nEncoding with the production preprocessor…")
    preprocessor = build_preprocessor()
    X_encoded = preprocessor.fit_transform(X)
    encoded_names = list(preprocessor.get_feature_names_out())
    name_to_raw   = encoded_to_raw_map(preprocessor)
    print(f"  encoded shape: {X_encoded.shape}  ({len(encoded_names)} columns)")

    print("\nRunning Boruta (this can take a minute on small data)…")
    selector = run_boruta(X_encoded, y)
    print(f"  finished in {selector.n_features_} confirmed columns and "
          f"{selector.support_weak_.sum()} tentative columns out of {len(encoded_names)}")

    per_feature = aggregate_per_feature(selector, encoded_names, name_to_raw)

    # Original selection from Benson's prior Boruta run
    original_selection = list(joblib.load(os.path.join(ARTIFACTS_DIR, "selected_features.pkl")))

    print_report(per_feature, original_selection)


if __name__ == "__main__":
    main()
