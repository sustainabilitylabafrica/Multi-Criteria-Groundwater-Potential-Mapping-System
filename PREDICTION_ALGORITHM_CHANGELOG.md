# Prediction Algorithm — Changelog

This document is a working log of every change made to the prediction
algorithm of the Groundwater Potential Mapping System, in the order the
changes were made. Its purpose is reference and accountability — anyone
reading this should be able to understand both what changed and *why*,
without needing to read the code.

Each entry is structured the same way: what the system was doing before,
what was wrong with that, what we changed, and what the change did or
did not improve.

---

## Starting point — Benson's original system

The system Benson built was a textbook tabular-classification pipeline
running inside a Streamlit app. The flow, in plain English:

1. The user picked one value for each of six features from dropdowns.
2. The chosen values were combined with mode-fill defaults for every
   other column in the dataset, producing a single full-width row.
3. That row was passed through a single OrdinalEncoder, which converted
   every categorical value to an integer based on the alphabetical
   order of categories within each column.
4. The encoded row was then narrowed down to the six features Boruta
   had selected during an offline run.
5. Those six values were standardised by a StandardScaler.
6. A Support Vector Machine made the prediction and produced confidence
   percentages.

The pipeline worked end-to-end and produced predictions, but it had
several quiet problems that we addressed in the changes below.

---

## Change 1 · Encoding overhaul (April 2026)

### What was wrong

The original OrdinalEncoder treated every categorical feature as if it
had a numerical ordering — including features where no such ordering
exists. "Clay", "Loam", and "Sand" were assigned 0, 1, and 2
respectively, alphabetically, which made the model treat Sand as
"twice as much" as Loam in some mathematical sense. There is no
geological reading under which that statement is true. The same
problem applied to Geological Features (Granite vs Limestone).

For features that *do* have a real ordering, the alphabetical
assignment was sometimes correct by accident and sometimes wrong.
"Drainage Density" was a clear example of the latter — alphabetical
order encoded it as `High=0, Low=1, Medium=2`, which scrambles the
physical progression entirely. The model was being asked to learn a
nonsensical ordering as a stand-in for the real one.

There was also a subtle data-quality bug: the Elevation column
contained both "Moderate" and "moderate" (different cases) which the
encoder treated as two separate categories. Roughly the same physical
condition was getting two different encodings depending on how the
original data was typed.

Finally, the mode-fill behaviour for non-selected features was a
hidden input to every prediction. The user's six choices contributed
to the prediction, and so did a frozen "average row" that reflected
the most common value of every other column in the training data —
without the user being aware of it.

### What we changed

Each feature now gets the encoding that matches its physical reality:

| Feature                 | Treatment       | Reason                                                  |
| ----------------------- | --------------- | ------------------------------------------------------- |
| Elevation               | Ordinal-ordered | Gentle < Moderate < Steep is a real progression         |
| Drainage Density        | Ordinal-ordered | Low < Medium < High is a real progression               |
| Tree Height             | Ordinal-ordered | Short < Medium < Tall is a real progression             |
| Tree Vigour             | Ordinal-ordered | Absent < Low < Moderate < High water demand             |
| Soil Texture            | One-hot         | Clay/Loam/Sand are categories, no ordering              |
| Geological Features     | One-hot         | Granite/Limestone are categories, no ordering           |

For ordered features we now specify the order explicitly rather than
letting it default to alphabetical, so the encoded numbers reflect the
physical progression. For unordered features we use one-hot encoding,
which gives each category its own yes/no column instead of forcing
them onto a number line.

The Elevation case-mismatch is fixed during data load — "moderate"
becomes "Moderate" before any encoding happens.

The mode-fill behaviour is gone. The pipeline now operates on exactly
the six selected features. There is no longer a hidden layer of
"average row" silently shaping every prediction.

The encoder, scaler, and classifier are now bundled into a single
sklearn `Pipeline` object and saved as one file (`pipeline.pkl`)
instead of three. This makes it impossible for them to drift out of
sync with each other — a class of bug that was theoretically possible
in the original setup, even if it hadn't bitten yet.

The dropdowns on the Predict page now show ordered features in their
correct physical sequence rather than alphabetical, which is a small
but real UX improvement on top of the underlying fix.

### What this changed in measurable terms

We re-evaluated the original SVM (with old encoding) against the new
encoding under stratified 5-fold cross-validation on the same data
splits. The old configuration scored F1 = 0.926. The new encoding,
holding the SVM constant, scored F1 = 0.942. That's a +0.016 F1 lift
purely from fixing the encoding, not from any model change.

The improvement is modest in absolute terms because Benson's original
system was already doing reasonably well — the encoding flaws were
shaving off a couple of percentage points, not destroying performance.
The bigger benefits are not on the scoreboard:

- **Predictions are now defensible scientifically.** No fake numerical
  relationships between unordered categories.
- **Predictions are now reproducible from inputs alone.** Removing the
  mode-fill means two identical inputs produce identical outputs in a
  way that doesn't depend on hidden training-data statistics.
- **The training-time / predict-time encoding is provably identical.**
  Both sites use the same pickled `Pipeline` object.

---

## Change 2 · Model comparison and selection (April 2026)

### What was wrong

There was nothing wrong with the original SVM choice — SVMs are a
reasonable default for small, clean tabular datasets. But the choice
was made once at the start of the project and never revisited. With
the encoding now changed, the *shape* of the data the classifier sees
is different (one-hot expansion has added columns), and that's a
natural moment to ask whether the SVM is still the best fit.

### What we changed

The retraining script (`retrain_model.py`) now compares three
candidate model families — Support Vector Machine, Random Forest,
Gradient Boosting — on the new encoding under stratified 5-fold
cross-validation, with light hyperparameter tuning per model. The
winner is selected by mean F1 score across folds (not accuracy, since
the dataset is imbalanced ~2:1 toward Low Potential, and accuracy
alone would reward a model that just always predicts "Low").

### Result

The three new models clustered tightly together: SVM at F1 = 0.942,
Random Forest at 0.929, Gradient Boosting at 0.945. Gradient Boosting
narrowly won, with notably lower variance across folds (±0.024 vs
±0.029–0.035 for the other two), suggesting more stable predictions.

The fact that all three new-encoding models beat the old-encoding
baseline (F1 = 0.926) is the more important finding — it means the
encoding improvement is doing most of the work, and the choice of
classifier is secondary. This is a healthy result; it suggests the
predictions are not heavily dependent on any one model family.

The current production model is Gradient Boosting with hyperparameters
`learning_rate=0.05, max_depth=3, n_estimators=100`. The Predict page
now displays "Powered by: Gradient Boosting" as a small confidence
signal that a real model comparison has happened.

---

## Change 3 · Feature selection re-run (April 2026)

### Why we did it

Benson's original Boruta run was performed on the OLD encoding. With
the new mixed encoding the feature space the algorithm sees is
genuinely different — Soil Texture has expanded from one column to
three, Geological Features from one to two — so it was an open
question whether the original six features still held up, or whether
some should be dropped or added. We re-ran Boruta on the new encoding
to find out.

We deliberately gave Boruta access to all seven raw features in the
dataset, including `Soil.Colour` which Benson had originally dropped.
Otherwise we wouldn't have been re-evaluating — we'd just have been
confirming the prior choice on a pre-filtered set.

### Result

Boruta confirmed the original six features unchanged, and rejected
`Soil.Colour` again on its own merits. Specifically:

- **Confirmed (highest confidence):** Soil Texture, Geological
  Features, Elevation, Tree Vigour, Tree Height, Drainage Density.
  All six original features.
- **Rejected:** Soil Colour. Independent of Benson's original
  decision, the algorithm rejects it again on the new encoding.
- **Tentative:** None. The signal in the dataset is clean enough
  that every feature lands clearly in either the Confirmed or
  Rejected bucket.

### What this changes

Nothing in the code. The feature selection stays exactly as it was.
But the result is itself valuable: we now have independent
confirmation that the feature set is correct on the new encoding, not
just inherited from a prior decision made under different conditions.

This is the kind of "no-op" finding that's worth doing precisely
because the alternative — an unspoken assumption that the old
selection still held — would have been a real risk. We've now closed
that question.

---

## Change 4 · Hydrogeology shapefile integration (April 2026)

### What was wrong

The system relied entirely on six categorical dropdowns the user fills
in by visual inspection of their site.  This works, but it ignores a
huge source of signal that exists in published hydrogeological survey
maps — aquifer type, lithology, transmissivity, expected borehole
yield, depth to water table, and so on.  All of that information is
attached to the user's location in a survey shapefile, and we were not
using it.

### What we did, and what we deliberately did not do

This change is split into three layers, only two of which are active
today.  The third is scaffolded but cannot be activated yet — see
"The training-data constraint" below.

**Layer 1 — Shapefile loader (active).**  A new module
(`hydrogeology.py`) loads a shapefile once at app startup, reprojects
it to WGS84, builds a spatial index, and exposes a `lookup(lat, lon)`
function.  It returns *all* matching features at the point — which
handles future shapefiles cleanly whether they are single non-overlapping
polygons, layered overlapping polygons, or mixed geometries (polygon
plus fault line plus borehole).  When no shapefile is configured the
module loads cleanly with `is_ready()=False`; nothing else in the app
breaks.

**Layer 2 — Context display (active).**  After a successful GPS fix on
the Predict page, a new "🗺️ Hydrogeological context" card pulls the
attributes for the user's location and renders them.  It is clearly
marked as reference information ("for reference only, not used by the
model") so the user knows the prediction itself does not yet depend on
this data.  We chose to show it next to the map rather than buried in
the result panel because it is genuinely useful information that
helps a surveyor interpret the prediction in context — even if there
is a small risk of it subtly biasing their dropdown choices.

**Layer 3 — Model features (scaffolded, inactive).**  The end-to-end
plumbing is wired: when the user submits a prediction, the frontend
forwards their GPS coordinates as reserved underscored keys
(`_lat`, `_lon`); the API route looks up shapefile attributes and
passes them to `ml.predict()` as a `geo_features` dict.  The function
accepts the dict and currently ignores it.  A clearly-labelled merge
point in `predict()` shows where it would be combined into the model
input row, and the docstring explains the activation steps.

### The training-data constraint

The reason Layer 3 cannot be activated yet is simple but absolute: the
existing 252-row training dataset has no per-row coordinates.  To
retrain the model with shapefile features, every training example
needs values for those new columns, and we cannot produce those values
without knowing where each example was collected.

Two things were considered and rejected:

- *Assuming a regional homogeneity (one shapefile unit for all rows).*
  This adds a constant column to the training data, which carries
  zero information and would not help the model.

- *Imputing coordinates from rough region descriptions.*  This is
  fabrication.  Predictions made on imputed training data would be
  defensible only as far as the imputation, which would be a soft spot
  in the algorithm.

The honest path forward is to wait until new training data is collected
*with* coordinates, then activate Layer 3.  Until then, the context
display is genuinely useful and the inference-time plumbing is in
place, so the activation will be a small change rather than a rebuild.

### What this changes in measurable terms

Nothing in the prediction scores yet — the model is unchanged.
Layer 3 activation will require a model retraining round and that
round's results will be reported as a new entry in this log.

What this *does* change for the user is the amount of contextual
information they see at decision time.  A surveyor in the field who
clicks Detect now sees not just their coordinates and a map marker,
but the published hydrogeological characterisation of their site.
That alone is meaningful even before the model uses any of it.

### Operational notes

- A synthetic sample shapefile ships with the project
  (`artifacts/hydrogeology/sample_hydrogeology.shp`) so the wiring is
  tested end-to-end and the user can see the feature working.
  Replacement procedure is documented in `HYDROGEOLOGY_README.md`.
- The loader handles CRS reprojection automatically.
- ESRI's 10-character field-name truncation is handled by a small
  rename map at the top of `hydrogeology.py`.

---

## Change 5 · Site-survey workflow, saved-locations page, and reports (May 2026)

### What was wrong

The system could *predict* but not *record*. A surveyor in the field
who collected GPS coordinates, looked at the hydrogeological context,
filled in the predictor values, and got a prediction had no way to
keep that complete picture together. Save was a separate, earlier
action that captured only the GPS coordinates and an optional label —
the predictor values, the model's prediction, and the hydrogeology
were all lost the moment the page was reloaded.

For a tool meant to support fieldwork over time and across multiple
sites, this was a meaningful gap. There was also no way to retrieve
historical surveys, no way to produce a written record of any single
site visit, and no way to summarise activity over a date range — all
things real survey work needs.

### What we changed

The save action is now a *complete site survey*. It is gated behind a
strict four-piece readiness check:

    1. GPS location captured
    2. Hydrogeological context loaded (or confirmed unavailable)
    3. Predictor values selected
    4. Prediction generated

The Save button at the bottom of the Predict page only enables when
all four are in place; a small checklist on the page lights up green
as each piece becomes available, so the user can see at a glance what
is missing. On save, all four pieces are captured together as one
record — including a snapshot of the hydrogeology attributes at the
time of survey, so historical records remain accurate even if the
shapefile is later updated.

A new **Saved Locations** page (`/saved`) lists every saved survey
newest-first, with two actions per row: "View Report" and a delete
button. From the same page, a "📅 History Report" button opens a
date-range picker and produces a multi-survey summary report.

### Reports

Two report types are now available, both as styled HTML pages with a
"Download PDF" button:

**Site Survey report** (`/saved/<id>/report`) — a per-survey
record showing location, hydrogeology snapshot, observed predictor
values, and the model's prediction. Print-clean styling so it works
either on screen or as a PDF.

**Location History report** (`/history-report?start=&end=`) — covers
every survey within a chosen date range. Includes a summary section
(total surveys, breakdown of High vs Low predictions) and a
per-survey card with the headline details.

PDFs are generated server-side using WeasyPrint, so the output is
identical regardless of browser. The HTML pages also work for
browser-print-to-PDF if WeasyPrint is unavailable.

### Schema migration

The schema gained three JSON columns (`hydrogeology_json`,
`predictors_json`, `prediction_json`) to capture the full survey
record. JSON was chosen over a wider tabular schema because the set
of hydrogeology attributes depends on the shapefile and the set of
predictor features depends on the feature-selection round — both can
change without breaking the database.

The existing saved-location rows from the old schema were wiped on
first run of the new code, by explicit choice: the old rows had only
GPS coordinates and would have appeared as nearly-empty records on
the new page, more confusing than useful.

### What this changes in measurable terms

Nothing in the prediction algorithm itself. This change is about
*record-keeping* and *user workflow* rather than model accuracy. But
it materially changes how the system can be used in the field — from
"a calculator that gives a number" to "a survey tool that builds a
useful body of records over time."

### What's intentionally not done yet

- **Editing saved surveys.** Records are insert-or-delete only.
  Allowing edits opens up audit-trail questions (who changed what
  when, do reports re-render with old or new data) that we should
  answer before adding the feature.

- **Per-user accounts.** All saves go into one shared table.
  Multi-user support belongs in the same conversation as a real
  authentication layer, which is on the roadmap but not in this
  change.

- **Export to CSV / GeoJSON.** Probably the next obvious feature, but
  not included here.

---

## What's still on the table

These are improvements we have *not* made yet but that would be
sensible next steps, listed roughly in order of expected impact:

**Confirming the feature orderings with the domain expert.** The
orderings I committed to in `retrain_model.py` (e.g. Tree Vigour =
Absent < Low < Moderate < High water demand) are defensible defaults
but should be reviewed by Benson. There's a `TODO(BENSON)` comment in
the code marking this. Re-training is a one-command operation if any
ordering needs correction.

**Adding continuous predictors.** The pipeline currently operates only
on categorical features. If continuous measurements become available —
elevation in metres, slope angle in degrees, distance to nearest
stream, annual rainfall, soil moisture readings — they would add real
signal that no amount of better encoding of the existing features can
recover. The `ColumnTransformer` design makes adding them
straightforward.

**More training data.** With 252 rows the cross-validation gives us
honest estimates but the standard deviations across folds are still
large enough (±0.024 F1 in the best case) that small differences
between models are hard to distinguish from noise. Doubling the
dataset would tighten those error bars meaningfully and let us
discriminate between candidate models more sharply.

**Calibration of the confidence percentages.** The system currently
shows the model's raw probabilities (e.g. "84.3% High Potential
Confidence"). For Gradient Boosting these are reasonably
well-calibrated out of the box, but for production deployment it
would be worth running a calibration check (reliability diagram) and
applying isotonic regression if needed, so that "70% confident" really
does mean "right 70% of the time" in the field.

**Per-prediction explanations.** Gradient Boosting can produce SHAP
values explaining why any individual prediction came out the way it
did — for example, "this came out High mainly because of the
combination of Loam soil and Moderate elevation." Surfacing these in
the UI would meaningfully improve trust in the system among
non-technical users.

**Re-running this whole exercise once new data arrives.** Every change
in this changelog is a one-script-run-away from being re-evaluated. If
the dataset grows or changes, both `feature_selection.py` and
`retrain_model.py` should be re-run, and any shifts in the result
should be added as new entries to this log.

---

## Cumulative impact

Comparing the system as we found it to the system as it stands today,
under stratified 5-fold cross-validation on the same data:

| Stage                                      | F1 mean | F1 std  |
| ------------------------------------------ | ------- | ------- |
| Original SVM, original encoding            | 0.926   | ±0.025  |
| Original SVM, new encoding                 | 0.942   | ±0.035  |
| Gradient Boosting, new encoding (current)  | 0.945   | ±0.024  |

A +0.019 F1 lift end-to-end, roughly two percentage points. The
qualitative improvements — defensible encoding, removal of the
mode-fill, single-artifact pipeline, model comparison framework, and
independent confirmation of feature selection — are the larger story
even if the numerical change is moderate.

The system as it stands is in better scientific footing than where we
started, and is positioned cleanly for the next round of improvements
when domain input or new data becomes available.

---

## Change N · Shapefile-first decision pipeline (May 2026)

### Context

Up to this point, the prediction algorithm was the SVM classifier and
nothing else. The SVM took six biophysical inputs and returned a
binary class with a probability. The hydrogeology shapefile loader
existed but was inert — it could look up attributes for a GPS point,
but its results were not used in any decision.

In May 2026 the **Decision-Making Algorithm Procedure Roadmap v1.0**
(Majawa) defined a six-phase workflow that wraps the SVM in pre-
prediction quality checks, post-prediction confidence scoring,
land-use modifiers, regional cross-checks, and an expert-review loop.
Around the same time, the BGS Africa Groundwater Atlas Zimbabwe
extract became the primary hydrogeology reference layer, replacing
the synthetic sample shapefile that had been used for testing.

This change implements that roadmap end-to-end against the BGS
dataset.

### What was wrong

Three structural problems with the previous pipeline:

1. **The model's output had no calibrated confidence story.** It
   produced "82% High Potential," but that percentage came from the
   SVM's own probability estimate — not from any independent check
   that the prediction was self-consistent, supported by surveyor
   observations, or aligned with established regional baselines.

2. **The shapefile carried information the system never used.** The
   `ZimHGComb` field encodes a BGS-derived baseline yield class for
   every polygon in Zimbabwe. The model never saw it; the UI never
   displayed it; there was no cross-check between the model's claim
   and the BGS regional answer.

3. **There was no human-in-the-loop fallback.** When the model was
   uncertain or the inputs were weak, there was no queue, no flag,
   no signal to a hydrogeologist that a particular prediction
   deserved a second look. Every prediction was treated as equally
   actionable.

### What we changed

A new module, `confidence.py`, runs three passes after the SVM:

1. **BGS regional-baseline cross-check.** The polygon at the GPS
   point has a published yield class (Low, Moderate, Mod–High, or
   High). We collapse it onto the model's binary scale and compare.
   Three outcomes: `agree`, `review` (Moderate baseline matches
   neither), `flag` (model and BGS contradict).

2. **Total Confidence Score (TCS).** A 0–10 score from four
   components:
   - C1 (max 3): how many of the six predictors were directly
     observed vs. inferred (the surveyor ticks "inferred" per input).
   - C2 (max 3): how confident the BGS-class to model-class
     remapping is. The remap table in `hydrogeology.py` records this
     per BGS class.
   - C3 (max 2): how many supplementary surveyor observations
     support the prediction (feeder streams, indicator plants, dark
     soil, etc.).
   - C4 (max 2): the BGS baseline alignment outcome from above —
     2 for agree, 1 for review, 0 for flag or no_baseline.

3. **Land Use Pressure (LUPS) modifier.** The surveyor's land-use
   checklist (active mining, urban impervious, dams, etc.) sums to a
   single integer. Moderate pressure (−1 to −2) reduces TCS by 1
   and adds an advisory flag. Severe pressure (≤ −3) reduces TCS by
   2 and downgrades a High prediction to Low.

A new module-level decoding table in `hydrogeology.py` maps every
BGS `ZimGLG` value present in the Zimbabwe dataset to one of the
two model classes (Granite / Limestone) with a confidence column
and a flag column. Surface-water polygons short-circuit the
prediction entirely — no groundwater prediction is generated for a
GPS point inside a lake.

Five expert-review triggers were added: `tcs_adjusted < 5`, severe
land-use pressure, low geology remap confidence, zero indicator
support, BGS baseline contradiction. Any one of them adds the
prediction to a queue, surfaced through a new `/expert-review` page
with confirm / override / resurvey / geophysics decision actions.

The saved-survey schema gained six new columns and three new JSON
blobs (`supplementary`, `land_use`, `expert_review`) to carry
everything the roadmap's Phase 5.4 "complete prediction record"
calls for. A small SQLite migration helper applies the column
additions on first start so existing databases upgrade in place
without manual intervention.

The `/predict` page UI was rebuilt to collect all the new inputs:
inferred-flag toggles per predictor, a supplementary-observations
checklist, a land-use checklist with a live LUPS preview, and a
geology override row in the Hydrogeology card. The prediction
result panel now shows the full TCS breakdown, the LUPS modifier
advisory, the BGS cross-check outcome, the flag list, and the
expert-review status alongside the model's raw confidence.

A new `/data-sources` page carries the BGS attribution and licence
information.

### What this changes about the system

The model itself is **unchanged** — the SVM classifier is loaded and
called the same way it always was. What changed is everything that
happens before and after the model.

The "before" change is light: the geology input is now auto-suggested
from the BGS shapefile rather than picked manually, which reduces
operator error.

The "after" change is the substance:

- A prediction now carries a **graded confidence** that reflects
  data quality, geological consistency, indicator agreement, and
  agreement with an external authoritative reference — not just the
  SVM's internal probability estimate.
- Human activity around the location can **change the final class**
  if it's severe enough to undermine the prediction in practice.
- Predictions that fall short on any of those checks are
  **automatically routed to expert review** rather than being
  treated as actionable.

### Limitations and what's deferred

The roadmap originally called for spatial overlay validation against
recharge zones, fault / lineament features, and confirmed aquifer
extents. The BGS Atlas dataset does not contain any of those layers
— it is a single-polygon-layer shapefile with two attributes. Those
checks are explicitly **deferred** in this change. The BGS regional-
baseline cross-check (C4 of the TCS) is a substitute that uses what
the dataset actually provides.

When supplementary layers become available (Geological Survey of
Zimbabwe maps, ZINWA borehole records, etc.), the C4 component can
be replaced or supplemented without disrupting the rest of the
pipeline.

### Cumulative impact

Pre-change: SVM produces a probability, the user sees it.

Post-change: SVM produces a probability, the system grades it
against four independent dimensions, applies a land-use adjustment,
flags concerns, routes uncertain results to a human, persists the
full audit trail, exports the lot as GeoJSON.

The model's accuracy is unchanged because the model is unchanged.
The **decision quality** of the system is structurally different:
predictions now come with the metadata required for them to be
trusted, contested, or escalated.
