"""
Machine-learning pipeline (post-encoding-overhaul).

What changed vs the previous version
------------------------------------
The previous version loaded three artifacts separately — encoder, scaler,
model — and stitched them together at predict time. That was a holdover
from how the original Streamlit code was structured.

This version loads ONE artifact: a complete sklearn Pipeline created by
retrain_model.py.  The pipeline contains:

    ColumnTransformer  ─┬─ OrdinalEncoder (with explicit ordering)
                        └─ OneHotEncoder  (for unordered categoricals)
            ↓
    StandardScaler
            ↓
    Classifier  (whichever of SVM / RF / GB scored best in retraining)

Why this is a real improvement
------------------------------
1. The encoding now matches the physical reality of each feature.
   Ordered features (Elevation, Drainage Density, …) are encoded as
   ordered numbers; unordered features (Soil Texture, Geological
   Features) are one-hot encoded so the model doesn't see fake
   numerical relationships between, say, Clay and Sand.

2. The "moderate" / "Moderate" case-mismatch bug in the dataset is
   fixed during data load — those used to be treated as two separate
   categories.

3. The mode-fill behaviour is gone.  The pipeline operates on exactly
   the six selected features, so there is no longer a hidden layer of
   "fill in the missing columns with the most common value from
   training" silently shaping every prediction.

4. One artifact instead of three — encoder/scaler/classifier can never
   drift out of sync with each other, because they are pickled
   together.
"""

import os
import joblib
import pandas as pd

from config import Config


# ---------------------------------------------------------------------------
# Load artifacts at import time.
# ---------------------------------------------------------------------------
def _safe_load(path: str, name: str):
    """Best-effort loader; returns None on failure rather than crashing import."""
    try:
        return joblib.load(path)
    except Exception as exc:                 # noqa: BLE001
        print(f"[ml] WARNING: could not load {name} from {path}: {exc}")
        return None


PIPELINE_PATH = os.path.join(Config.ARTIFACTS_DIR, "pipeline.pkl")
METADATA_PATH = os.path.join(Config.ARTIFACTS_DIR, "encoding_metadata.pkl")

pipeline           = _safe_load(PIPELINE_PATH,           "pipeline")
selected_features  = _safe_load(Config.FEATURES_PATH,    "selected_features")
encoding_metadata  = _safe_load(METADATA_PATH,           "encoding_metadata")

# Dataset is still useful for the Feature Guide page.
try:
    dataset = pd.read_csv(Config.DATASET_PATH)
    # Mirror the same case-normalization the training script applied — so
    # the Feature Guide doesn't show "Moderate" and "moderate" as two values.
    if "Elevation" in dataset.columns:
        dataset["Elevation"] = dataset["Elevation"].str.strip().replace({"moderate": "Moderate"})
except Exception as exc:                     # noqa: BLE001
    print(f"[ml] WARNING: could not load dataset: {exc}")
    dataset = pd.DataFrame()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def is_ready() -> bool:
    """All artifacts loaded?"""
    return (
        pipeline is not None
        and selected_features is not None
        and encoding_metadata is not None
    )


def predictor_options() -> dict:
    """
    Return {feature: [options]} for each Boruta-selected feature.

    For ORDERED features the options are returned in their physical
    order (Gentle → Moderate → Steep), not alphabetical, so the user
    sees the natural progression in the dropdown.

    For UNORDERED features the options are returned alphabetically.
    """
    out = {}
    if not is_ready():
        return out

    ordered_meta   = encoding_metadata.get("ordered",   {})
    unordered_meta = encoding_metadata.get("unordered", {})

    for feature in selected_features:
        if feature in ordered_meta:
            out[feature] = list(ordered_meta[feature])    # physical order
        elif feature in unordered_meta:
            out[feature] = list(unordered_meta[feature])  # alphabetical
        else:
            # Defensive fallback — shouldn't happen if metadata is in sync
            out[feature] = sorted(dataset[feature].dropna().unique().tolist()) \
                           if feature in dataset.columns else []
    return out


def feature_guide() -> list:
    """
    For the Feature Guide page.  Returns:
        [
          (feature_name, kind, [values]),
          ...
        ]
    where `kind` is "ordered" or "unordered" — useful so the page can
    explain to users that, e.g., Elevation has a real ordering whereas
    Soil Texture is just a category.
    """
    out = []
    if dataset.empty:
        return out

    ordered_meta   = encoding_metadata.get("ordered",   {}) if encoding_metadata else {}
    unordered_meta = encoding_metadata.get("unordered", {}) if encoding_metadata else {}

    for col in dataset.columns:
        if col == "Decision":
            continue
        if col in ordered_meta:
            out.append((col, "ordered", list(ordered_meta[col])))
        elif col in unordered_meta:
            out.append((col, "unordered", list(unordered_meta[col])))
        else:
            try:
                vals = sorted(dataset[col].dropna().unique().tolist())
            except Exception:                # noqa: BLE001
                vals = []
            out.append((col, "unordered", vals))   # default labelling
    return out


def predict(user_inputs: dict, geo_features: dict = None) -> dict:
    """
    Run the prediction pipeline.

    Args:
        user_inputs:   {feature_name: chosen_value} for each of the six
                       selected features (the dropdown values).
        geo_features:  Optional dict of attributes pulled from the
                       hydrogeology shapefile at the user's location
                       (Layer 3 of the shapefile integration plan).
                       --------------------------------------------------
                       FUTURE WORK — currently ignored by the model.
                       --------------------------------------------------
                       To activate: (1) gather training data WITH
                       coordinates, (2) extend retrain_model.py to
                       extract shapefile attributes for each row and
                       merge them into X, (3) extend the
                       ColumnTransformer in build_preprocessor() to
                       handle the new columns (continuous attributes
                       like yield_lps go through StandardScaler;
                       categoricals like aquifer/lithology go through
                       OneHotEncoder), (4) re-run feature_selection.py,
                       (5) merge geo_features into `row` here before
                       building the DataFrame.  See
                       PREDICTION_ALGORITHM_CHANGELOG.md for the full
                       plan.

    Returns:
        {
            "prediction":         0 or 1,
            "label":              human-readable label,
            "high_potential_pct": float,
            "low_potential_pct":  float,
            "model_used":         e.g. "Gradient Boosting",
        }
    """
    if not is_ready():
        raise RuntimeError(
            "Model artifacts are not all loaded.  Run `python retrain_model.py` "
            "to (re-)create them."
        )

    # Validate that every required feature is present in the input.
    missing = [f for f in selected_features if f not in user_inputs]
    if missing:
        raise ValueError(f"Missing required feature(s): {missing}")

    # Build a single-row DataFrame with columns in the exact order the
    # ColumnTransformer expects.  No mode-fill, no extra columns — only
    # the six features the pipeline was trained on.
    row = {f: user_inputs[f] for f in selected_features}

    # Layer 3 merge point — currently inert.  When the model is retrained
    # with shapefile features, uncomment and align with retrain_model.py:
    # if geo_features:
    #     for k, v in geo_features.items():
    #         row[k] = v

    df  = pd.DataFrame([row], columns=selected_features)

    pred  = int(pipeline.predict(df)[0])
    probs = pipeline.predict_proba(df)[0]   # [low_prob, high_prob]

    return {
        "prediction":         pred,
        "label":              "High Potential Area" if pred == 1 else "Low Potential Area",
        "high_potential_pct": float(probs[1] * 100.0),
        "low_potential_pct":  float(probs[0] * 100.0),
        "model_used":         encoding_metadata.get("winner", "model"),
    }
