"""
Central application configuration.

Why a separate config file?
    Flask-SQLAlchemy connects to whatever database URL it finds in
    SQLALCHEMY_DATABASE_URI.  Keeping that URL here (and only here) means
    the migration to PostgreSQL later becomes a one-line change in this
    file — no other code needs to be touched.  See ROADMAP_POSTGRES.md.
"""

import os

# Project root — used to anchor paths regardless of where Flask is launched from
BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    # SQLite default — the file is created automatically on first run
    # under instance/groundwater.db.  No external service required.
    #
    # To switch to PostgreSQL later, set the DATABASE_URL environment
    # variable to something like:
    #   postgresql+psycopg2://user:pass@localhost:5432/groundwater
    # …and that's literally the only change needed.
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///" + os.path.join(BASE_DIR, "instance", "groundwater.db"),
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ------------------------------------------------------------------
    # Paths to the trained model artifacts
    # ------------------------------------------------------------------
    ARTIFACTS_DIR = os.path.join(BASE_DIR, "artifacts")

    MODEL_PATH    = os.path.join(ARTIFACTS_DIR, "svm_model.pkl")
    SCALER_PATH   = os.path.join(ARTIFACTS_DIR, "scaler.pkl")
    ENCODER_PATH  = os.path.join(ARTIFACTS_DIR, "encoder.pkl")
    FEATURES_PATH = os.path.join(ARTIFACTS_DIR, "selected_features.pkl")
    DATASET_PATH  = os.path.join(ARTIFACTS_DIR, "augmented_data.csv")
