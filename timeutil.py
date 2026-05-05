"""
Time utilities — single home for UTC↔CAT (Central Africa Time, GMT+2)
conversion.

Why this module exists
----------------------
The application stores timestamps in UTC in the database (which is the
right thing to do — it's locale-agnostic and won't shift if the server
moves), but the UI and reports for this deployment present Zimbabwean
local time, which is Central Africa Time (CAT, UTC+02:00).

CAT does **not** observe daylight saving time, so a fixed +02:00 offset
is the correct conversion year-round and we don't need pytz / zoneinfo.

Functions
---------
    now_cat()              -> datetime, naive, in CAT (used for default
                              column values and for "report generated at"
                              labels)
    utc_to_cat(dt)         -> datetime, naive, in CAT
    format_cat(dt)         -> "YYYY-MM-DD HH:MM CAT" (used in templates)
    format_cat_iso(dt)     -> "YYYY-MM-DDTHH:MM:SS+02:00" (machine readable)
"""

from datetime import datetime, timedelta, timezone

# Central Africa Time — fixed UTC+02:00, no DST.
CAT = timezone(timedelta(hours=2), name="CAT")


def now_cat() -> datetime:
    """
    Current time in CAT, returned as a *naive* datetime so it can be
    stored in our SQLite DateTime columns without provoking the
    "naive vs aware" ValueError SQLAlchemy raises on mixed types.

    The DB column is treated as "wall-clock CAT" — see models.py.
    """
    return datetime.now(CAT).replace(tzinfo=None)


def utc_to_cat(dt: datetime) -> datetime:
    """
    Convert a UTC datetime (naive or aware) to a naive CAT datetime.

    Naive inputs are assumed to be UTC, which is how older rows in the
    database (saved before this migration) were written.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CAT).replace(tzinfo=None)


def format_cat(dt: datetime) -> str:
    """
    Render a datetime as "YYYY-MM-DD HH:MM CAT" for display in templates
    and reports. Accepts UTC or CAT input.

    Heuristic: if the input is naive, we assume it is already in CAT
    (because models.py now stores CAT-naive timestamps). Aware inputs
    are converted from whatever timezone they're in to CAT.
    """
    if dt is None:
        return "—"
    if dt.tzinfo is not None:
        dt = dt.astimezone(CAT).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M") + " CAT"


def format_cat_iso(dt: datetime) -> str:
    """
    Render a datetime as a CAT-aware ISO 8601 string, e.g.
    "2026-05-04T13:45:30+02:00". Used for the JSON API and any place
    we need a machine-readable timestamp.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Already CAT wall-clock — re-attach the offset.
        dt = dt.replace(tzinfo=CAT)
    else:
        dt = dt.astimezone(CAT)
    return dt.isoformat()
