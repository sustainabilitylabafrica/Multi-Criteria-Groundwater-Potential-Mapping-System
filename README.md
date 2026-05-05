# Groundwater Potential Mapping System — Flask edition

Decision-support web app for identifying groundwater potential in
Zimbabwe. Combines a Boruta-selected SVM classifier with the BGS Africa
Groundwater Atlas as a regional reference.

## Quick start

```bash
# 1. Set up a virtual environment
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
python app.py
# → http://localhost:5000
```

If you're upgrading from a previous version of the app, your existing
`instance/groundwater.db` will be migrated in place on first start
(new columns are added automatically).

## What's in this project

```
groundwater_flask/
├── app.py                  Flask app + page and API routes
├── config.py               Single place for configuration (DB URL, paths)
├── timeutil.py             UTC ↔ CAT (Zimbabwean local time, GMT+2) helpers
├── models.py               SQLAlchemy schema (SavedLocation table)
├── ml.py                   Loads the SVM pipeline at request time
├── hydrogeology.py         BGS shapefile loader + decoder + GeoJSON export
├── confidence.py           TCS, LUPS modifier, BGS cross-check, expert-review triggers
├── retrain_model.py        Offline script to (re-)build the ML pipeline
├── feature_selection.py    Offline script to (re-)run Boruta
├── requirements.txt
│
├── README.md                    (this file)
├── PREDICTION_ALGORITHM_GUIDE.md  Plain-language explanation of the full pipeline
├── PREDICTION_ALGORITHM_CHANGELOG.md  Log of every algorithm change made
├── ROADMAP_POSTGRES.md           How to switch SQLite → PostgreSQL later
│
├── artifacts/
│   ├── pipeline.pkl                ★ Full sklearn Pipeline
│   ├── selected_features.pkl       List of features the model uses
│   ├── encoding_metadata.pkl       Which features are ordered, in what order
│   ├── augmented_data.csv          Training data
│   └── hydrogeology/               BGS Zimbabwe shapefile + licence + readme
│
├── instance/groundwater.db (auto-created on first run)
│
├── static/
│   ├── css/
│   │   ├── style.css       Soothing blue + light-gray theme
│   │   └── report.css      Light/print theme for site-survey + history reports
│   └── js/
│       ├── geo.js          GPS detect + map + hydrogeology lookup + auto-fill
│       ├── predict.js      Predictor form + supplementary obs + LUPS + prediction
│       ├── save.js         Save-survey panel
│       ├── saved.js        Saved Locations page (delete + history modal)
│       └── expert_review.js Expert Review decision modal
│
└── templates/              Jinja templates (one per page)
    ├── base.html
    ├── home.html
    ├── predict.html
    ├── saved.html
    ├── expert_review.html
    ├── data_sources.html
    ├── report_site.html    Per-survey printable report
    ├── report_history.html Date-range printable report
    ├── model_info.html
    ├── feature_guide.html
    └── about.html
```

## What's new in this version

The pipeline now goes well beyond the SVM's binary output. After the
classifier runs, three more passes refine the result:

1. **BGS regional-baseline cross-check.** Every GPS point is spatial-
   joined against the BGS Africa Groundwater Atlas Zimbabwe extract.
   The model's binary output is compared to the BGS yield class for the
   polygon — disagreement raises a flag.

2. **Total Confidence Score (TCS).** A 0–10 score with four components:
   data completeness, geology match quality, biophysical indicator
   convergence, BGS baseline alignment.

3. **Land Use Pressure (LUPS) modifier.** The surveyor ticks land-use
   factors within 1 km. Moderate pressure reduces TCS; severe pressure
   downgrades a High prediction to Low.

If any of TCS &lt; 5, LUPS ≤ −3, low geology confidence, no supportive
indicators, or BGS disagreement fire, the prediction is routed to the
Expert Review queue.

See `PREDICTION_ALGORITHM_GUIDE.md` for a plain-language walkthrough,
and `PREDICTION_ALGORITHM_CHANGELOG.md` for the full change log.

## v1 release notes

A few presentation-level details worth knowing before deploying:

- **All timestamps in the UI and reports are Central Africa Time (CAT,
  GMT+2) — Zimbabwean local time.** The database itself stores naive
  CAT timestamps (no DST in Zimbabwe, so the +02:00 offset is fixed and
  unambiguous). The JSON API surfaces ISO-8601 strings with explicit
  `+02:00` offsets, so consumers always know what they're looking at.
- **Predictor labels in the UI are friendly names** (e.g. "Soil Type",
  "Vegetation Vigour", "Drainage Density"). The trained model's internal
  feature names (e.g. `Soil.Texture`, `Natural.vegitation..tree..vigour`)
  are unchanged — the friendly names are a thin Jinja-side layer so we
  don't risk silently breaking the encoder.

## Hydrogeology data

The system ships with the BGS Africa Groundwater Atlas Zimbabwe extract
under `artifacts/hydrogeology/`. See the README and LICENCE in that
folder. Attribution required: CC BY-SA 4.0.

## Endpoints

| Path | Purpose |
|---|---|
| `/` | Home |
| `/predict` | Main prediction workflow |
| `/saved` | Saved surveys table |
| `/expert-review` | Expert review queue |
| `/data-sources` | BGS attribution + dataset status |
| `/saved/<id>/report` | Per-survey report (HTML) |
| `/saved/<id>/report.pdf` | Per-survey report (PDF) |
| `/history-report?start=…&end=…` | Date-range report |
| `/api/predict` (POST) | Run the full pipeline |
| `/api/locations` (POST/GET/DELETE) | Save / list / delete surveys |
| `/api/locations/<id>/expert-review` (POST) | Record expert decision |
| `/api/hydrogeology?lat=…&lon=…` | Geology lookup at a point |
| `/api/hydrogeology.geojson` | The BGS shapefile as GeoJSON (used by the Model Info choropleth) |
| `/api/saved.geojson` | Export all surveys as GeoJSON |

## Testing

```bash
python -c "from app import app; print('App OK')"
```

Should print `App OK` after the hydrogeology layer loads.
