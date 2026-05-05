"""
Confidence scoring, Land Use Pressure modifier, and BGS regional-baseline
cross-check.

What this module does
---------------------
After the SVM has produced a raw prediction, the decision-making roadmap
calls for three more passes:

    1. A Total Confidence Score (TCS) out of 10, broken into four
       components (C1\u2013C4).
    2. A Land Use Pressure Score (LUPS) modifier that can downgrade the
       final class and reduce the TCS based on what the surveyor saw
       around the location.
    3. A cross-check against the BGS regional baseline for that polygon
       \u2014 a substitute for the recharge / lineament / aquifer-extent
       overlays the original roadmap called for, which the available BGS
       dataset does not contain.

This module is the single home for those three passes. Each function is
deliberately small, pure, and unit-testable. The Flask route just calls
them in order and stuffs the results into the response.

Binary vs three-class
---------------------
The deployed SVM is a binary classifier (Low Potential / High Potential),
not the three-class Low/Medium/High the original roadmap assumed. This
means:

    * BGS cross-check: the BGS yield class is collapsed onto the binary
      scale (Low/Mod -> Low; Mod\u2013High/High -> High; Moderate is treated
      as 'either').
    * LUPS modifier: there is no Medium to step through. We treat
      moderate pressure as an advisory flag (no class flip), and severe
      pressure as a hard downgrade from High to Low \u2014 still capped at
      one class step from where the model started, never two.
"""

from typing import Optional


# ---------------------------------------------------------------------------
# Land Use Pressure Score (LUPS) \u2014 from the surveyor's land-use checklist
# ---------------------------------------------------------------------------
# Sums to a single integer. Negative = pressure (negative impact on
# groundwater). Positive = beneficial (e.g. nearby small dam aiding
# recharge).
#
# These weights come straight from the decision-making roadmap, Phase 3.4.
LUPS_WEIGHTS = {
    "active_mining_within_500m":          -3,
    "artisanal_mining_or_wetland_drain":  -2,
    "urban_impervious_over_30pct":        -2,
    "deforestation":        -1,
    "irrigated_ag_borehole_source":       -1,
    # "no_significant_pressure":             0,
    "farm_dam_or_weir_within_300m":       +1,
}


def compute_lups(land_use_flags: dict) -> dict:
    """
    Sum the chosen flags into a single LUPS integer.

    Args:
        land_use_flags: {flag_name: bool} \u2014 the keys must be a subset of
            LUPS_WEIGHTS. Unknown keys are silently ignored so the front
            end can evolve without breaking server-side scoring.

    Returns:
        {
            "score":      int,
            "components": [(flag, weight), ...],
            "level":      "low" | "moderate" | "severe",
        }
    """
    if not isinstance(land_use_flags, dict):
        land_use_flags = {}

    components = []
    score = 0
    for key, weight in LUPS_WEIGHTS.items():
        if land_use_flags.get(key):
            components.append((key, weight))
            score += weight

    if score >= 0:
        level = "low"
    elif score >= -2:
        level = "moderate"
    else:
        level = "severe"

    return {"score": score, "components": components, "level": level}


# ---------------------------------------------------------------------------
# BGS regional-baseline cross-check
# ---------------------------------------------------------------------------
# Map the BGS yield class onto the model's binary scale.
BGS_TO_BINARY = {
    "Low":      "Low",     # B-L, CSI-L      -> Low
    "Moderate": "either",  # I-M             -> ambiguous, neither agrees nor disagrees
    "Mod\u2013High": "High",    # CSF-M/H         -> High
    "High":     "High",    # CSIF-H, U-H     -> High
}


def bgs_baseline_check(
    model_label: str,                    # "Low Potential Area" or "High Potential Area"
    bgs_yield_class: Optional[str],      # "Low" | "Moderate" | "Mod\u2013High" | "High" | None
) -> dict:
    """
    Compare the SVM's binary output against the BGS regional yield class.

    Returns:
        {
            "status":           "agree" | "review" | "flag" | "no_baseline",
            "model_binary":     "Low" | "High",
            "bgs_binary":       "Low" | "High" | "either" | None,
            "message":          short explanation,
        }
    """
    model_binary = "High" if "High" in (model_label or "") else "Low"

    if not bgs_yield_class:
        return {
            "status":       "no_baseline",
            "model_binary": model_binary,
            "bgs_binary":   None,
            "message":      "BGS baseline yield not available for this polygon.",
        }

    bgs_binary = BGS_TO_BINARY.get(bgs_yield_class)
    if bgs_binary is None:
        return {
            "status":       "no_baseline",
            "model_binary": model_binary,
            "bgs_binary":   None,
            "message":      f"Unrecognised BGS yield class: {bgs_yield_class}",
        }

    if bgs_binary == "either":
        return {
            "status":       "review",
            "model_binary": model_binary,
            "bgs_binary":   "either",
            "message":      "BGS baseline is Moderate \u2014 supports neither Low nor "
                            "High strongly. Worth a second look.",
        }

    if bgs_binary == model_binary:
        return {
            "status":       "agree",
            "model_binary": model_binary,
            "bgs_binary":   bgs_binary,
            "message":      f"Model and BGS baseline both indicate {model_binary}.",
        }

    return {
        "status":       "flag",
        "model_binary": model_binary,
        "bgs_binary":   bgs_binary,
        "message":      f"Model predicts {model_binary} but BGS regional baseline "
                        f"indicates {bgs_binary}. Recommend on-the-ground "
                        f"information (e.g. electromagnetic survey) to refine.",
    }


# ---------------------------------------------------------------------------
# Total Confidence Score (TCS) \u2014 four components, sum to /10
# ---------------------------------------------------------------------------
# C1: Data Completeness (max 3) \u2014 how many predictors were directly
#     observed vs inferred? Computed from how many of the 6 predictors the
#     surveyor flagged as 'inferred'.
#
# C2: Geology Match Quality (max 3) \u2014 from hydrogeology.GEOLOGY_REMAP
#     confidence. High = 3, Medium = 2, Low = 1, unknown = 0.
#
# C3: Biophysical Indicator Convergence (max 2) \u2014 how many supplementary
#     observations support the model's prediction direction. The surveyor
#     ticks supportive indicators in the form; we count them.
#
# C4: BGS Regional Baseline Alignment (max 2) \u2014 from bgs_baseline_check
#     above. agree=2, review=1, flag/no_baseline=0.

C2_FROM_CONFIDENCE = {"High": 3, "Medium": 2, "Low": 1}


def compute_tcs(
    inferred_predictors_count: int,        # 0..6 \u2014 how many of the 6 inputs were inferred not observed
    remap_confidence:          Optional[str],   # 'High' | 'Medium' | 'Low' | None
    biophysical_support_count: int,        # 0..N supplementary indicators that support the prediction
    bgs_check_status:          str,        # 'agree' | 'review' | 'flag' | 'no_baseline'
) -> dict:
    """
    Compute the Total Confidence Score and return the breakdown.

    Returns:
        {
            "tcs":   int (0..10),
            "c1":    int (0..3),
            "c2":    int (0..3),
            "c3":    int (0..2),
            "c4":    int (0..2),
            "explanations": {c1: str, c2: str, c3: str, c4: str},
        }
    """
    inferred = max(0, int(inferred_predictors_count or 0))
    if inferred == 0:
        c1, c1_msg = 3, "All 6 predictors directly observed."
    elif inferred == 1:
        c1, c1_msg = 2, "5 of 6 directly observed; 1 inferred."
    elif inferred == 2:
        c1, c1_msg = 1, "4 of 6 directly observed; 2 inferred."
    else:
        c1, c1_msg = 0, f"{inferred} of 6 inferred \u2014 weak data completeness."

    c2 = C2_FROM_CONFIDENCE.get(remap_confidence or "", 0)
    c2_msg = f"Geology remap confidence: {remap_confidence or 'unknown'}."

    sup = max(0, int(biophysical_support_count or 0))
    if sup >= 3:
        c3, c3_msg = 2, f"{sup} supplementary indicators support the prediction."
    elif sup >= 1:
        c3, c3_msg = 1, f"{sup} supplementary indicator(s) support the prediction."
    else:
        c3, c3_msg = 0, "No supplementary indicators recorded, or they contradict the prediction."

    if bgs_check_status == "agree":
        c4, c4_msg = 2, "BGS regional baseline agrees with the model."
    elif bgs_check_status == "review":
        c4, c4_msg = 1, "BGS regional baseline neither confirms nor contradicts."
    else:
        c4, c4_msg = 0, "BGS baseline contradicts the model OR was unavailable."

    return {
        "tcs": c1 + c2 + c3 + c4,
        "c1":  c1,
        "c2":  c2,
        "c3":  c3,
        "c4":  c4,
        "explanations": {"c1": c1_msg, "c2": c2_msg, "c3": c3_msg, "c4": c4_msg},
    }


# ---------------------------------------------------------------------------
# Apply LUPS modifier to a binary class + TCS
# ---------------------------------------------------------------------------
def apply_lups_modifier(
    raw_label: str,        # "Low Potential Area" or "High Potential Area"
    raw_class_int: int,    # 0 = Low, 1 = High (from the model)
    lups: dict,            # output of compute_lups
    tcs: int,              # the TCS score before adjustment
) -> dict:
    """
    Apply the Land Use Pressure modifier per the roadmap, adapted for the
    deployed binary classifier.

    Behaviour:
        level == 'low'      -> no change
        level == 'moderate' -> advisory flag, TCS \u2212 1 (no class flip)
        level == 'severe'   -> if model said High, flip to Low; flag
                               'Land Use Risk'; TCS \u2212 2

    Returns:
        {
            "final_label":        str,    final class label
            "final_class_int":    int,    0 or 1
            "tcs_adjusted":       int,    TCS after LUPS adjustment, floored at 0
            "downgrade_applied":  bool,
            "lups_flags":         [str],
            "advisory":           str,    short human-readable note
        }
    """
    flags = []
    advisory = ""
    final_label = raw_label
    final_int   = raw_class_int
    tcs_adj     = tcs
    downgraded  = False

    level = lups.get("level", "low")

    if level == "moderate":
        flags.append("Land_Use_Pressure_Advisory")
        tcs_adj  = max(0, tcs - 1)
        advisory = ("Moderate land-use pressure (LUPS "
                    f"{lups.get('score')}) reduces confidence by 1.")
    elif level == "severe":
        flags.append("Land_Use_Risk")
        tcs_adj  = max(0, tcs - 2)
        advisory = ("Severe land-use pressure (LUPS "
                    f"{lups.get('score')}) reduces confidence by 2 and "
                    "downgrades the prediction.")
        # Only flip class if the raw class was High; if already Low we
        # just carry the flag.
        if raw_class_int == 1:
            final_label = "Low Potential Area"
            final_int   = 0
            downgraded  = True
            advisory   += " High \u2192 Low based on land-use pressure."
    else:
        advisory = "Low or beneficial land-use pressure \u2014 no adjustment."

    return {
        "final_label":       final_label,
        "final_class_int":   final_int,
        "tcs_adjusted":      tcs_adj,
        "downgrade_applied": downgraded,
        "lups_flags":        flags,
        "advisory":          advisory,
    }


# ---------------------------------------------------------------------------
# Expert-review trigger logic
# ---------------------------------------------------------------------------
# Phase 6.3 of the roadmap. A prediction needs expert review if ANY of:
#
#   * TCS (post-LUPS adjustment) is below 5
#   * Severe land-use pressure ('Land_Use_Risk' flag)
#   * Geology was remapped with Low confidence
#   * The biophysical indicator convergence (C3) scored 0
#   * The BGS baseline check returned 'flag' (model contradicts BGS)
#
# We return BOTH the boolean AND the list of triggering reasons so reports
# and dashboards can show the surveyor *why* a record needs review.

def expert_review_triggers(
    tcs_adjusted:        int,
    lups_flags:          list,
    remap_confidence:    Optional[str],
    c3:                  int,
    bgs_check_status:    str,
) -> dict:
    reasons = []
    if tcs_adjusted < 5:
        reasons.append(f"TCS below 5 (adjusted={tcs_adjusted}).")
    if "Land_Use_Risk" in (lups_flags or []):
        reasons.append("Severe land-use pressure flagged.")
    if (remap_confidence or "") == "Low":
        reasons.append("Geology remapped with Low confidence.")
    if c3 == 0:
        reasons.append("No biophysical indicators support the prediction.")
    if bgs_check_status == "flag":
        reasons.append("BGS regional baseline contradicts the model.")

    return {
        "needs_review": bool(reasons),
        "reasons":      reasons,
    }
