# How the Groundwater Prediction Works

A plain-language walkthrough — from the moment a surveyor opens the app
to the moment the system says "this place probably has good
groundwater" or "probably not." No mathematics required.

This guide is written for people who use the system or rely on its
output, not for developers. If you want the code-level details, see
`PREDICTION_ALGORITHM_CHANGELOG.md`.

---

## What we are trying to do

Drilling a borehole is expensive. Drilling one in the wrong place is
worse — you spend the money and end up with a dry hole or a borehole
that runs out of water in the dry season.

Some clues about whether a location has good groundwater are visible
on the surface: the type of rock, the slope, the trees that grow there,
the streams nearby. A trained hydrogeologist can read those clues. The
goal of this system is to help non-specialists do something similar,
quickly, in the field, with a phone or laptop. **It is not a
replacement for a hydrogeologist or a geophysical survey before
drilling.** It is an early-stage decision aid.

---

## The seven things the system does, in order

When a surveyor uses the system, this is what happens behind the
scenes. We'll go through each step plainly.

```
  1. Pin the location on a map                 (GPS)
  2. Look up what kind of rock is underneath   (BGS shapefile)
  3. Surveyor records six observations         (the field form)
  4. Surveyor records supplementary clues      (helpful but not used by the model directly)
  5. Surveyor records what's around the area   (mining, dams, urban, etc.)
  6. The trained model makes a prediction      (Low or High potential)
  7. The system grades how confident we should be (TCS + cross-checks + flags)
```

By the time all seven are done, the surveyor has not just an answer but
a **graded** answer — with a confidence number, a list of warnings, and
a clear recommendation about whether a specialist should look at the
location before any decision is taken.

---

## Step 1 — Pin the location

The surveyor taps a button. The phone or laptop reports the GPS
coordinates. A pin appears on the map. The system stores the latitude
and longitude as the **primary key** of this whole survey — every other
piece of information will be attached to that location.

If the GPS is not accurate enough or the surveyor wants to test from a
desk, they can drop the pin manually. The whole flow works the same
either way.

---

## Step 2 — Find out what kind of rock is underneath

We use a published map called the **BGS Africa Groundwater Atlas**
(British Geological Survey, 2019/2021), which divides Zimbabwe into 219
zones based on the geology underneath. Each zone tells us:

- the **type of rock** — granite, sandstone, basalt, alluvium, etc.
- a **typical groundwater yield** — how much water you would expect
  from a properly-sited borehole in that kind of rock, expressed as a
  class: **Low** (~0.5 L/s or less), **Moderate**, or **High** (5 L/s
  or more).

The system finds which zone the surveyor's GPS point falls into and
records both pieces of information. It then groups the geology into one
of two categories that the trained model understands — **Granite-like**
(crystalline / fractured) or **Limestone-like** (porous / sedimentary).
This grouping is rough on purpose; it matches how the underlying model
was trained.

> **Important:** the BGS map is at very large scale (1 cm on the map =
> 50 km on the ground). It tells us the **regional** rock character,
> not what's exactly under the surveyor's feet. Think of it as
> "this is granite country" rather than "you are standing on a granite
> outcrop." The surveyor can override the auto-suggestion if they see
> a rock outcrop at the site that contradicts the regional map.

If the GPS lands on a **lake or reservoir** (Lake Kariba, etc.), the
system stops and says so — there's no point predicting groundwater
under a lake.

If the GPS lands **outside Zimbabwe**, the system stops and says so —
the data only covers Zimbabwe.

---

## Step 3 — The six observations

The surveyor fills in a form with six pieces of information. Each one
relates to whether groundwater is likely to be present.

| What | What we're looking for | Why it matters |
|---|---|---|
| **Soil Type** | Clay, Loam, or Sand | Sand and loam let water soak in; heavy clay tends to shed water |
| **Geological features** | Granite-like or Limestone-like | Already filled in from the BGS lookup; can be overridden |
| **Elevation / slope** | Gentle, Moderate, or Steep | Gentle ground holds water; steep ground sheds it |
| **Vegetation vigour** | Absent / Low / Moderate / High water demand | Lush water-loving trees suggest a high water table |
| **Vegetation height** | Short / Medium / Tall | Tall, well-fed trees often indicate access to deep water |
| **Drainage density** | How many stream channels are visible within 500 m | Many channels usually mean the area gets and holds water |

A surveyor who can't directly observe one of these — say, they can't
see the slope clearly because of fog, or the drainage density isn't
clear — can tick **"inferred"** next to that input. This honestly
records that the value was estimated rather than observed, and it
lowers the confidence score later.

These six observations are the **only inputs that the trained model
itself uses**. The next two steps (4 and 5) feed the confidence and
adjustment passes, not the model.

---

## Step 4 — Supplementary observations

These are extra clues the surveyor can record but the model doesn't
use directly. They support the **confidence score** later.

Examples:
- **Feeder stream present** — surface water arrives from upland
- **Indicator plants** — phreatophytes like figs or wild date palm
  whose deep roots reach groundwater
- **Seasonal water evidence** — wet-season pans, dry riverbeds nearby
- **Shallow water table evidence** — wet patches, hand-dug wells that
  reach water quickly
- **Dark soil** — typically richer in organic matter, often near
  wetter zones
- **Shallow soil over fractured rock** — productive in basement
  geology

Every tick a surveyor adds is one more piece of evidence that the
location truly does (or doesn't) match what the model is predicting.

---

## Step 5 — What's happening around the area

Within roughly 1 km of the location, the surveyor records human
activities that affect groundwater. Each one carries a weight; the
weights add up to a single number called **LUPS** (Land Use Pressure
Score).

| What's there | Weight |
|---|---|
| Active hard-rock or large-scale mining within 500 m | **−3** |
| Artisanal mining or wetland drainage | **−2** |
| Urban/peri-urban development > 30% impervious | **−2** |
| Deforestation | **−1** |
| Functional irrigated agriculture using borehole water | **−1** |
| No significant pressure | **0** |
| Active farm dam or small weir within 300 m | **+1** |

For example, a site with a dam nearby (+1) but also urban development
(−2) and one Eucalyptus plantation (−1) would have a LUPS of −2.

Three brackets matter for the next step:
- **0 or positive**: low pressure — no adjustment
- **−1 or −2**: moderate pressure — confidence drops a little
- **−3 or worse**: severe pressure — this is significant enough to
  change the outcome

---

## Step 6 — The model makes its prediction

Now the trained machine-learning model takes the six observations from
Step 3 and produces a result: **High Potential Area** or **Low
Potential Area**, plus a percentage confidence for each (e.g. "82%
High, 18% Low").

The model itself is a Support Vector Machine — a well-understood
classifier from machine learning. It was trained on labelled data with
the six observations as inputs and the outcome (High / Low) as the
label. Boruta feature selection narrowed the original set of inputs
down to those six because, statistically, they were the ones actually
helping the prediction.

This raw output is the model's best guess. The next step grades how
much we should trust it.

---

## Step 7 — Confidence, cross-checks, and flags

This is where the system goes beyond a typical model. After the SVM
produces its prediction, the system runs three more passes:

### 7a. The BGS regional-baseline cross-check

The model said the location has High (or Low) groundwater potential.
The BGS atlas already says, for the regional zone, what yield class to
expect. Do they agree?

- **They agree** → confidence score gets the full 2 points for this
  component. The prediction looks consistent with the regional
  picture.
- **They disagree** → this is a flag. The model and BGS are saying
  different things. That doesn't mean the model is wrong — local
  conditions may genuinely differ from the regional baseline — but it
  does mean a human should look at this prediction before any
  decisions are made.

### 7b. The Total Confidence Score (TCS)

The TCS is a single number out of 10. It's the sum of four components:

| Component | What it measures | Max points |
|---|---|---|
| **C1 — Data completeness** | How many of the 6 inputs were directly observed (vs. ticked "inferred") | 3 |
| **C2 — Geology match** | How confident we are that the BGS regional rock type maps onto the model's two-class system | 3 |
| **C3 — Indicator convergence** | How many supplementary observations from Step 4 support the prediction | 2 |
| **C4 — BGS baseline** | Does the model agree with the BGS regional yield class? (Step 7a) | 2 |
| **Total** | | **10** |

A TCS of 9 or 10 is a strong, well-supported prediction. A TCS of 4 or
below is the system telling you "the model produced an answer, but
nobody should bet on it without more evidence."

### 7c. The Land Use Pressure modifier

Now the LUPS from Step 5 enters the picture:

- **LUPS 0 or positive**: nothing changes.
- **LUPS −1 or −2** (moderate pressure): TCS drops by 1.
  *Advisory only — the prediction class doesn't change.*
- **LUPS −3 or worse** (severe pressure): TCS drops by 2, and a
  warning called **Land_Use_Risk** is added. **If the model said High
  Potential, the system downgrades it to Low.**

The reasoning: a location with a productive aquifer beneath an active
hard-rock mine that's pumping out groundwater day and night is, in
practice, no longer a good place to drill — even though the rock type
itself is favourable.

### 7d. Flags and the expert review queue

Throughout the process, the system attaches **flags** wherever
something needs attention. Common ones:

- `Alluvial` — alluvial soil, treat the geology guess with care
- `Basalt_review` — the geology is basalt; the auto-mapping is approximate
- `Great_Dyke_review` — the rare Great Dyke geology; specialist should look
- `Kalahari_sands` — Kalahari sands behave specially
- `Land_Use_Risk` — severe land-use pressure
- `Land_Use_Pressure_Advisory` — moderate land-use pressure
- `Surface_water` — point is on a lake (handled at Step 2)
- `Unknown_formation` — geology not in the remap table

If **any** of the following apply, the prediction is routed to the
Expert Review queue:

1. Final TCS is below 5
2. Severe land-use pressure flagged
3. Geology was remapped with low confidence
4. No supplementary observations support the prediction
5. The BGS baseline contradicts the model

The queue is just a list of predictions a hydrogeologist should look at
before any drilling decision. The reviewer can:
- **Confirm** the prediction as-is
- **Override** it (with rationale)
- Recommend a **Resurvey** with more careful field work
- Recommend a targeted **Geophysical investigation** before any drilling

Every expert decision is logged with its rationale and reviewer name,
so the record builds up over time and becomes useful training data for
the next version of the model.

---

## What the surveyor sees

By the end of the process, the surveyor sees:

- A clear **final class** (Low Potential Area or High Potential Area)
- The model's raw confidence (e.g. "82% High")
- The TCS out of 10, broken down by component
- The LUPS and what it did to the prediction
- The BGS regional baseline and whether it agrees
- All flags raised
- Whether expert review is required and *why*

They can save the survey, generate a printable PDF report, and export
all surveys as GeoJSON for QGIS or ArcGIS work.

---

## What the system is not

It's worth being honest about the limits.

- **It is not a borehole-yield predictor.** The model gives a binary
  Low / High potential. It does not tell you "this site will yield
  3 L/s at 65 m depth." That requires drilling or geophysics.
- **It is not a substitute for a hydrogeologist.** It's a triage tool
  — it surfaces the prediction, the confidence, the conflicts, the
  warnings, and routes uncertain cases to a human.
- **The BGS data is regional.** Two GPS points 5 km apart will almost
  always return the same regional rock type. The system relies on the
  surveyor's biophysical observations (Steps 3–5) for site-level
  discrimination.
- **Predictions are not validated against real boreholes yet.** Once
  the system is used in the field and predicted High locations are
  drilled, the actual yields can be fed back into the model to retrain
  it. Until then, the TCS and expert-review queue are the system's
  honest acknowledgment that it is making informed but unverified
  guesses.

---

## When to trust a prediction, when not to

A simple rule of thumb:

| Situation | Action |
|---|---|
| TCS ≥ 8, BGS agrees, no flags | Strong evidence — proceed with normal site assessment |
| TCS 5–7, BGS agrees, no severe flags | Reasonable signal — gather more field evidence before drilling |
| Any TCS, BGS disagrees | Mandatory expert review before any commitment |
| TCS &lt; 5 | Expert review or resurvey — don't act on the prediction alone |
| `Land_Use_Risk` flag | Expert review — competing groundwater demand or contamination risk |
| Geology remapped with Low confidence | Expert review — regional geology may not represent site |

---

## In one sentence

> The system collects what the surveyor can see at a location, looks
> up what the regional geology should look like there, runs a trained
> classifier, then **grades how much to trust the answer** by checking
> whether the model agrees with the regional baseline, whether the
> surveyor's other clues support it, whether human activity in the
> area is going to interfere, and how much of the input was
> well-observed versus inferred — and routes anything uncertain to a
> human for review.

That's the whole pipeline.
