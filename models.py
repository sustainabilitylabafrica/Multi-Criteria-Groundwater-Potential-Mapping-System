"""
Database models.

The SavedLocation table represents a full site survey, not just a GPS
fix. Each row captures everything from the Phase 5.4 'complete prediction
record' in the decision-making roadmap:

    1. WHERE                  - latitude, longitude, optional human label
    2. WHAT (geo)             - the hydrogeology shapefile attributes
                                for the polygon at this point, plus the
                                decoded BGS class and remap log entry,
                                snapshotted at save time
    3. WHAT (predictors)      - the six predictor values the surveyor
                                observed (with per-predictor 'inferred?'
                                flags)
    4. WHAT (supplementary)   - Phase 2 \u00a73.1.2 supplementary observations
                                that are not yet model inputs but are
                                recorded for future retraining
    5. WHAT (land use)        - Phase 3 land-use checklist used to
                                compute the LUPS modifier
    6. RESULT (raw model)     - the SVM's raw output before any post-
                                processing
    7. RESULT (final)         - the LUPS-adjusted final class plus the
                                full TCS breakdown, BGS cross-check
                                outcome, all flags, and expert-review
                                triggers
    8. EXPERT REVIEW          - reviewer decision, override, rationale

We use JSON columns for the structured 'WHAT' and 'RESULT' pieces. The
exact set of fields will evolve with the roadmap (and a future model
retrain), and JSON columns let the schema absorb that without a migration
every time. Plain Float / String / DateTime columns are used only where
indexing, filtering, or sorting matters.

A small lightweight migration helper at the bottom of this file uses
SQLite's `ALTER TABLE ADD COLUMN` to add any new columns to an existing
DB at startup. It is a no-op on a fresh install (db.create_all() does
the work) and on already-up-to-date databases.
"""

import json
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text

from timeutil import now_cat, format_cat_iso

db = SQLAlchemy()


# ---------------------------------------------------------------------------
# Helpers - JSON encode/decode that never raises in either direction
# ---------------------------------------------------------------------------
def _json_loads(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _json_dumps(value):
    if value is None:
        return None
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# SavedLocation
# ---------------------------------------------------------------------------
class SavedLocation(db.Model):
    """One full site-survey record."""

    __tablename__ = "saved_locations"

    id          = db.Column(db.Integer, primary_key=True)
    latitude    = db.Column(db.Float,        nullable=False)
    longitude   = db.Column(db.Float,        nullable=False)
    label       = db.Column(db.String(200),  nullable=True)

    # Geology / hydrogeology snapshot - the rich shapefile lookup result
    # at save time, including raw attributes + BGS decoding + remap entry.
    hydrogeology_json    = db.Column(db.Text, nullable=True)

    # Field observations
    predictors_json      = db.Column(db.Text, nullable=True)   # the 6 model inputs + per-feature 'inferred' flags
    supplementary_json   = db.Column(db.Text, nullable=True)   # Phase 2 \u00a73.1.2 observations
    land_use_json        = db.Column(db.Text, nullable=True)   # Phase 3 checklist + computed LUPS

    # Prediction outcome
    prediction_json      = db.Column(db.Text, nullable=True)   # raw model output + final adjusted + TCS + BGS check + flags

    # Expert review
    expert_review_json   = db.Column(db.Text, nullable=True)

    # Indexed plain columns for fast filtering / sorting
    final_class          = db.Column(db.String(40),  nullable=True, index=True)   # "High Potential Area" or "Low Potential Area"
    tcs                  = db.Column(db.Integer,     nullable=True, index=True)
    needs_expert_review  = db.Column(db.Boolean,     nullable=False, default=False, index=True)

    # Stored as a NAIVE datetime in CAT (Zimbabwean local time, UTC+02:00).
    # We keep the column naive because SQLAlchemy + SQLite would otherwise
    # require all reads/writes to be timezone-aware, which is awkward for
    # an in-place migration. The fixed +02:00 offset means there's no
    # ambiguity and no DST gotcha.
    created_at           = db.Column(db.DateTime, default=now_cat,
                                     nullable=False, index=True)

    # ------------------------------------------------------------------
    # JSON helpers
    # ------------------------------------------------------------------
    def get_hydrogeology(self):    return _json_loads(self.hydrogeology_json)    or {}
    def set_hydrogeology(self, v): self.hydrogeology_json    = _json_dumps(v)

    def get_predictors(self):      return _json_loads(self.predictors_json)      or {}
    def set_predictors(self, v):   self.predictors_json      = _json_dumps(v)

    def get_supplementary(self):   return _json_loads(self.supplementary_json)   or {}
    def set_supplementary(self, v):self.supplementary_json   = _json_dumps(v)

    def get_land_use(self):        return _json_loads(self.land_use_json)        or {}
    def set_land_use(self, v):     self.land_use_json        = _json_dumps(v)

    def get_prediction(self):      return _json_loads(self.prediction_json)      or {}
    def set_prediction(self, v):   self.prediction_json      = _json_dumps(v)

    def get_expert_review(self):   return _json_loads(self.expert_review_json)   or {}
    def set_expert_review(self, v):self.expert_review_json   = _json_dumps(v)

    # ------------------------------------------------------------------
    # Output shapes
    # ------------------------------------------------------------------
    def to_dict(self):
        """Full JSON-serializable representation for API + report templates."""
        supp = self.get_supplementary()
        # `notable_features` is bundled into the supplementary JSON on save
        # (to avoid a DB migration). Extract it here so templates can read
        # `loc.notable_features` directly without poking at the supplementary
        # dict.
        notable = ""
        if isinstance(supp, dict):
            notable = supp.get("notable_features", "") or ""
        return {
            "id":             self.id,
            "latitude":       self.latitude,
            "longitude":      self.longitude,
            "label":          self.label,
            "hydrogeology":   self.get_hydrogeology(),
            "predictors":     self.get_predictors(),
            "supplementary":  supp,
            "land_use":       self.get_land_use(),
            "prediction":     self.get_prediction(),
            "expert_review":  self.get_expert_review(),
            "notable_features": notable,
            "final_class":    self.final_class,
            "tcs":            self.tcs,
            "needs_expert_review": bool(self.needs_expert_review),
            "created_at":     format_cat_iso(self.created_at),
        }

    def to_summary_dict(self):
        """Lighter representation for the list view."""
        pred = self.get_prediction()
        raw  = (pred or {}).get("raw_model") or {}
        return {
            "id":            self.id,
            "latitude":      self.latitude,
            "longitude":     self.longitude,
            "label":         self.label,
            "prediction":    self.final_class or pred.get("final_label") or raw.get("label") or pred.get("label", "\u2014"),
            "high_pct":      raw.get("high_potential_pct") or pred.get("high_potential_pct"),
            "tcs":           self.tcs,
            "needs_review":  bool(self.needs_expert_review),
            "created_at":    format_cat_iso(self.created_at),
        }

    def __repr__(self):
        return f"<SavedLocation #{self.id} ({self.latitude}, {self.longitude})>"


# ---------------------------------------------------------------------------
# Lightweight schema migration for SQLite
# ---------------------------------------------------------------------------
# This handles the case where someone is upgrading from the previous
# version of the app and already has an instance/groundwater.db on disk.
# db.create_all() does NOT add new columns to an existing table.
#
# We add any missing columns ourselves with ALTER TABLE. Each ALTER is
# idempotent because we check the current column list first.
#
# For PostgreSQL deployments we recommend using Alembic instead - set
# DATABASE_URL and run `flask db migrate` once. But for SQLite dev work,
# this keeps the upgrade path zero-friction.

NEW_COLUMNS_SQLITE = [
    ("supplementary_json",  "TEXT"),
    ("land_use_json",       "TEXT"),
    ("expert_review_json",  "TEXT"),
    ("final_class",         "VARCHAR(40)"),
    ("tcs",                 "INTEGER"),
    ("needs_expert_review", "BOOLEAN NOT NULL DEFAULT 0"),
]


def run_simple_migrations(app):
    """Add any new columns that the deployed schema doesn't yet have. SQLite-friendly."""
    with app.app_context():
        engine = db.engine
        if engine.dialect.name != "sqlite":
            # PostgreSQL: rely on Alembic / flask-migrate. No-op here.
            return

        insp = inspect(engine)
        if "saved_locations" not in insp.get_table_names():
            return  # db.create_all() will build it from scratch

        existing = {col["name"] for col in insp.get_columns("saved_locations")}
        with engine.begin() as conn:
            for col_name, col_type in NEW_COLUMNS_SQLITE:
                if col_name in existing:
                    continue
                conn.execute(text(
                    f"ALTER TABLE saved_locations ADD COLUMN {col_name} {col_type}"
                ))
                print(f"[migrations] added saved_locations.{col_name}")
