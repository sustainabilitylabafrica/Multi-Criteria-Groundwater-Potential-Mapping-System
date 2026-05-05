# Roadmap: SQLite → PostgreSQL

The system was built on SQLite for fast local testing and zero-setup
development. Every architectural decision was made with this migration
in mind, so the day you actually switch is genuinely a small day.

This document covers (1) why this is easy, (2) the actual steps, and
(3) what to watch out for.

---

## 1. Why this is easy

Two design choices do almost all the work:

**SQLAlchemy as the database layer.** Every line of code in `models.py`
and every database call in `app.py` goes through SQLAlchemy. SQLAlchemy
generates the right SQL dialect for whatever database it's connected
to — SQLite, PostgreSQL, MySQL, or others. The application code does
not know or care which one is underneath.

**A single configuration entry point.** The database URL lives in
exactly one place: `Config.SQLALCHEMY_DATABASE_URI` in `config.py`. It
already reads from a `DATABASE_URL` environment variable, so the
deployment can be flipped between SQLite and PostgreSQL with no code
edit at all.

The schema (`SavedLocation` in `models.py`) uses only column types that
exist identically in SQLite and PostgreSQL: `Integer`, `Float`,
`String`, `DateTime`. There are no SQLite-specific quirks.

---

## 2. The migration steps

### Step 1 — Stand up a PostgreSQL instance

Pick one:

- **Local development**: install PostgreSQL 15 or newer, create a
  database called `groundwater`, and create a user that owns it.
- **Managed in production**: any of Render, Railway, Supabase, Neon,
  AWS RDS, Google Cloud SQL, DigitalOcean Managed Databases. They all
  give you a connection string of the form:
  ```
  postgresql://username:password@host:port/database
  ```

### Step 2 — Add the PostgreSQL driver

Add one line to `requirements.txt`:

```
psycopg2-binary>=2.9
```

Then `pip install -r requirements.txt`.

### Step 3 — Point the application at PostgreSQL

Set the `DATABASE_URL` environment variable. SQLAlchemy needs the URL
prefix to match the driver, so the format is:

```
postgresql+psycopg2://username:password@host:port/database
```

On Linux/macOS:
```bash
export DATABASE_URL="postgresql+psycopg2://groundwater_user:secret@localhost:5432/groundwater"
```

On Windows (PowerShell):
```powershell
$env:DATABASE_URL = "postgresql+psycopg2://groundwater_user:secret@localhost:5432/groundwater"
```

In production, set it through the hosting provider's dashboard — never
commit credentials to the repository.

### Step 4 — Create the schema in PostgreSQL

The first time the application starts against PostgreSQL it will create
the `saved_locations` table automatically (`db.create_all()` runs at
startup). No manual SQL required.

### Step 5 — Migrate the existing SQLite data (if there is any worth keeping)

If the SQLite database has rows you want to preserve, export them as a
CSV and import into PostgreSQL:

```bash
# 1. Export from SQLite
sqlite3 instance/groundwater.db -header -csv \
   "SELECT id, latitude, longitude, label, created_at FROM saved_locations;" \
   > saved_locations.csv

# 2. Import into PostgreSQL (psql shell)
psql "$DATABASE_URL"
\copy saved_locations(id, latitude, longitude, label, created_at) \
   FROM 'saved_locations.csv' CSV HEADER;

# 3. Reset the auto-increment sequence to the max id (so new inserts don't collide)
SELECT setval(pg_get_serial_sequence('saved_locations', 'id'),
              COALESCE((SELECT MAX(id) FROM saved_locations), 1));
```

If the SQLite database is just developer test data, skip this step.

### Step 6 — Done

Restart the application. It will now read and write against PostgreSQL.
Nothing else in the codebase needs to change.

---

## 3. What to watch out for

**Heroku/legacy hosts hand back `postgres://` URLs.** SQLAlchemy 1.4+
requires `postgresql://` (or `postgresql+psycopg2://`). If you're given
a `postgres://...` URL, either rename it manually or rewrite at startup:
```python
url = os.environ["DATABASE_URL"]
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql+psycopg2://", 1)
```

**Add a connection pool tune for serious traffic.** SQLite ignores this
entirely; PostgreSQL benefits from explicit settings. In `config.py`,
add:
```python
SQLALCHEMY_ENGINE_OPTIONS = {
    "pool_pre_ping": True,
    "pool_recycle": 280,        # avoid stale connections behind some proxies
}
```
(`pool_pre_ping` alone is the single most useful setting — it costs
almost nothing and prevents the "server has gone away" class of bug.)

**When the schema starts to evolve, introduce Alembic.** `db.create_all()`
is fine for a single, never-changing table. As soon as you start adding
columns or new tables, switch to **Flask-Migrate** (a thin wrapper
around Alembic):
```bash
pip install flask-migrate
```
This gives you versioned schema changes (`flask db migrate`,
`flask db upgrade`) so the production database can be evolved safely.

**Geographic queries.** If the project ever needs queries like "saved
locations within 5 km of point X", install the **PostGIS** extension
on the PostgreSQL database and switch the latitude/longitude columns
to a `Geography(POINT)` column. SQLite has no equivalent — this is a
feature only PostgreSQL gives you, and one of the better reasons to
make the move when geographic analysis becomes a real requirement.

**Backups.** SQLite backups are "copy the file"; PostgreSQL backups
are `pg_dump`. Most managed providers do this automatically. If
self-hosting, schedule `pg_dump` nightly.

---

## Summary in one paragraph

SQLite is doing a real job today: it's keeping the project to a single
`pip install` and a single file. The day a second user, a remote
deployment, or a geographic query enters the picture, set
`DATABASE_URL`, add `psycopg2-binary` to requirements, restart, and
the application is on PostgreSQL. The schema, the routes, the
prediction logic, and the frontend stay exactly as they are.
