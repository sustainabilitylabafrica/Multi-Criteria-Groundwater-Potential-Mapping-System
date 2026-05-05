"""
Hydrogeology shapefile loader + BGS Africa Groundwater Atlas decoder.

What this module does
---------------------
1. Loads a hydrogeological shapefile once at import time, builds a spatial
   index, exposes a fast point lookup.
2. Decodes the BGS Africa Groundwater Atlas attribute scheme (ZimGLG /
   ZimHGComb) into something the rest of the app can reason about:
       * a model class (Granite / Limestone) with remap confidence,
       * a human-readable BGS aquifer type and baseline yield class,
       * status flags for surface water, out-of-coverage, etc.
3. Returns BOTH the raw shapefile attributes and the decoded structure on
   every lookup, so saved survey records keep an exact snapshot of what
   the source data said at the time of survey.

This module knows about the BGS scheme specifically. If a future
deployment uses a different shapefile (e.g. BGS Botswana, Geological
Survey of Zimbabwe 1:1M, etc.), only the REMAP and DECODE tables below
need editing — the loader and lookup logic are generic.

Public API
----------
    is_ready()                  -> bool
    status()                    -> dict
    lookup(lat, lon)            -> dict   (rich, with decoded fields)
    legacy_lookup(lat, lon)     -> dict   (the older slim format, kept
                                           for one or two internal callers)
"""

import glob
import os
from typing import Optional

from config import Config


# ---------------------------------------------------------------------------
# BGS Africa Groundwater Atlas — Zimbabwe decoding tables
# ---------------------------------------------------------------------------
# These tables are the authoritative source for how the shapefile's BGS
# attributes map onto the rest of the app. They live here, in one place,
# on purpose. If the source dataset changes (e.g. you swap to a different
# country's BGS extract), edit these — nothing else.
#
# References:
#   - Ó Dochartaigh, B.É. (2021). User Guide v1.2: Africa Groundwater Atlas
#     Country Hydrogeology Maps. BGS Open Report OR/21/063.
#   - Decision-Making Algorithm Procedure Roadmap v1.0 (May 2026), §2.2.

GEOLOGY_REMAP = {
    "Precambrian Basement Complex and Metavolcanics": {
        "model_class": "Granite",
        "confidence": "High",
        "flag":       None,
        "rationale":  "Crystalline basement — fractured / weathered flow regime, "
                      "matches the model's 'Granite' class.",
    },
    "Great Dyke": {
        "model_class": "Granite",
        "confidence": "Medium",
        "flag":       "Great_Dyke_review",
        "rationale":  "Mafic-ultramafic intrusion. Roadmap maps Dolerite/Basalt → "
                      "Granite (Medium confidence, flag for review).",
    },
    "Precambrian Metasediments": {
        "model_class": "Granite",
        "confidence": "Medium",
        "flag":       None,
        "rationale":  "Metasediments / schist behaviour — closer to the basement "
                      "family than to porous sedimentary.",
    },
    "Igneous - Upper Karoo Basalt": {
        "model_class": "Granite",
        "confidence": "Medium",
        "flag":       "Basalt_review",
        "rationale":  "Karoo basalts. Roadmap maps Dolerite/Basalt → Granite "
                      "(Medium confidence). Vesicular / weathered upper layers "
                      "can be productive — flag for review.",
    },
    "Sedimentary - Mesozoic-Palaeozoic (Upper and Lower Karoo)": {
        "model_class": "Limestone",
        "confidence": "Medium",
        "flag":       None,
        "rationale":  "Karoo Supergroup is sandstone-dominated. Roadmap maps "
                      "Sandstone → Limestone (Medium confidence).",
    },
    "Sedimentary - Cretaceous": {
        "model_class": "Limestone",
        "confidence": "Medium",
        "flag":       None,
        "rationale":  "Cretaceous sandstones — porous sedimentary, mapped to "
                      "Limestone class.",
    },
    "Sedimentary Kalahari Basin": {
        "model_class": "Limestone",
        "confidence": "Medium",
        "flag":       "Kalahari_sands",
        "rationale":  "Kalahari Group sands. Excellent aquifers where saturated; "
                      "flag so reviewers know this is unconsolidated material, "
                      "not true limestone.",
    },
    "Unconsolidated sedimentary": {
        "model_class": "Limestone",
        "confidence": "Medium",
        "flag":       "Alluvial",
        "rationale":  "Quaternary alluvium. Roadmap explicitly flags this case "
                      "as Alluvial — typically the highest-potential locations.",
    },
    "Surface water": {
        "model_class": None,
        "confidence":  None,
        "flag":        "Surface_water",
        "rationale":   "Lake or reservoir — no groundwater prediction is "
                       "generated for surface-water polygons.",
    },
}


# Decode ZimHGComb. Format is "AquiferType-Productivity".
HG_CODE_DECODE = {
    "B-L":     {"aquifer_type": "Precambrian Basement",
                "yield_class":  "Low",      "yield_lps": "0.1\u20130.5"},
    "CSF-M/H": {"aquifer_type": "Cons. Sedimentary Fracture",
                "yield_class":  "Mod\u2013High", "yield_lps": "2\u201320"},
    "CSI-L":   {"aquifer_type": "Cons. Sedimentary Intergranular",
                "yield_class":  "Low",      "yield_lps": "0.1\u20130.5"},
    "CSIF-H":  {"aquifer_type": "Cons. Sed. Intergranular+Fracture",
                "yield_class":  "High",     "yield_lps": "5\u201320"},
    "I-M":     {"aquifer_type": "Igneous (volcanic)",
                "yield_class":  "Moderate", "yield_lps": "2\u20135"},
    "U-H":     {"aquifer_type": "Unconsolidated",
                "yield_class":  "High",     "yield_lps": "5\u201320"},
    "n/a":     {"aquifer_type": "Surface water",
                "yield_class":  None,       "yield_lps": None},
}


# Which shapefile attribute holds the geology class, and which holds the
# hydrogeology code. Keep these as constants so swapping in a different
# country's BGS file is a one-line change.
GLG_FIELD = "ZimGLG"
HG_FIELD  = "ZimHGComb"


# ---------------------------------------------------------------------------
# Auto-discover the shapefile
# ---------------------------------------------------------------------------
HYDRO_DIR = os.path.join(Config.ARTIFACTS_DIR, "hydrogeology")


def _discover_shapefile() -> Optional[str]:
    """Find the first .shp in artifacts/hydrogeology/, if any."""
    if not os.path.isdir(HYDRO_DIR):
        return None
    candidates = sorted(glob.glob(os.path.join(HYDRO_DIR, "*.shp")))
    return candidates[0] if candidates else None


SHAPEFILE_PATH = _discover_shapefile()


# ---------------------------------------------------------------------------
# Module-level state — loaded once at import.
# ---------------------------------------------------------------------------
_gdf       = None        # GeoDataFrame in WGS84 (lat/lon)
_sindex    = None        # spatial index for fast lookups
_load_error: Optional[str] = None
_columns   = []          # attribute columns we expose (geometry excluded)
_is_bgs_zim = False      # True if loaded shapefile has the BGS Zimbabwe schema


def _load():
    """Load the shapefile once, reproject to WGS84, build the spatial index."""
    global _gdf, _sindex, _load_error, _columns, _is_bgs_zim

    if SHAPEFILE_PATH is None:
        _load_error = "no shapefile found in artifacts/hydrogeology/"
        return

    try:
        # Lazy import so the rest of the app loads even if geopandas is
        # unavailable (the user gets a clear "not configured" message).
        import geopandas as gpd

        gdf = gpd.read_file(SHAPEFILE_PATH)

        # Reproject to WGS84 if needed. GPS coordinates are always WGS84,
        # but the shapefile may be in any projection.
        if gdf.crs is None:
            print("[hydrogeology] WARNING: shapefile has no CRS \u2014 assuming EPSG:4326")
            gdf = gdf.set_crs("EPSG:4326")
        elif gdf.crs.to_epsg() != 4326:
            print(f"[hydrogeology] reprojecting from {gdf.crs} to EPSG:4326")
            gdf = gdf.to_crs("EPSG:4326")

        # Clean up shapefile-truncated column names ESRI imposes. Add
        # mappings here as new shapefiles surface new oddities.
        rename_map = {"trans_clas": "trans_class"}
        gdf = gdf.rename(columns={k: v for k, v in rename_map.items() if k in gdf.columns})

        _gdf = gdf
        _sindex = gdf.sindex   # geopandas builds this on first access
        _columns = [c for c in gdf.columns if c != "geometry"]
        _is_bgs_zim = (GLG_FIELD in _columns and HG_FIELD in _columns)

        print(f"[hydrogeology] loaded {len(gdf)} features from "
              f"{os.path.basename(SHAPEFILE_PATH)}; columns: {_columns}; "
              f"BGS-Zimbabwe schema: {_is_bgs_zim}")

    except Exception as exc:                 # noqa: BLE001
        _load_error = str(exc)
        print(f"[hydrogeology] FAILED to load {SHAPEFILE_PATH}: {exc}")


_load()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def is_ready() -> bool:
    """True if a shapefile loaded successfully and is queryable."""
    return _gdf is not None and not _gdf.empty


def has_bgs_schema() -> bool:
    """True if the loaded shapefile is the BGS Zimbabwe extract (or has the same fields)."""
    return _is_bgs_zim


def status() -> dict:
    """Quick diagnostic for templates / API."""
    return {
        "ready":          is_ready(),
        "path":           os.path.basename(SHAPEFILE_PATH) if SHAPEFILE_PATH else None,
        "feature_count":  int(len(_gdf)) if _gdf is not None else 0,
        "columns":        list(_columns),
        "bgs_schema":     _is_bgs_zim,
        "error":          _load_error,
    }


# ---------------------------------------------------------------------------
# Rich lookup \u2014 used by /api/predict and /api/hydrogeology
# ---------------------------------------------------------------------------
def lookup(lat: float, lon: float) -> dict:
    """
    Look up shapefile attributes at a GPS point AND decode them into the
    structure the rest of the app needs.

    Always returns a dict with a `status` key. Possible values:

        "not_configured"   \u2014 no shapefile loaded; nothing else useful in the dict
        "out_of_coverage"  \u2014 point falls outside the country (no polygon match)
        "surface_water"    \u2014 point lies inside a lake / reservoir polygon
        "ok"               \u2014 normal case; all fields populated

    On status == "ok" the dict contains:

        raw_glg                : str  (ZimGLG value)
        raw_hg_code            : str  (ZimHGComb value)
        model_class            : "Granite" | "Limestone"
        remap_confidence       : "High" | "Medium"
        remap_flag             : str | None  (e.g. 'Alluvial', 'Basalt_review')
        remap_rationale        : str
        bgs_aquifer_type       : str
        bgs_baseline_yield     : "Low" | "Moderate" | "Mod\u2013High" | "High" | None
        bgs_baseline_yield_lps : str  (e.g. '5\u201320')
        features               : list of dicts of every raw shapefile attribute,
                                 kept for the saved-record snapshot
        summary                : short human-readable one-liner
    """
    if not is_ready():
        return {
            "status":  "not_configured",
            "message": "Hydrogeology data not configured on this server.",
            "features": [],
            "summary":  "Hydrogeology data not configured",
        }

    from shapely.geometry import Point
    pt = Point(lon, lat)        # shapely is (x=lon, y=lat) \u2014 always

    # Spatial-index prefilter, then exact intersection test.
    candidate_idx = list(_sindex.intersection(pt.bounds))
    matches = []
    for i in candidate_idx:
        geom = _gdf.geometry.iloc[i]
        if geom is None:
            continue
        if geom.intersects(pt):
            row = _gdf.iloc[i]
            matches.append({col: _to_native(row[col]) for col in _columns})

    if not matches:
        return {
            "status":   "out_of_coverage",
            "message":  "GPS point falls outside the dataset coverage. "
                        "The current dataset covers Zimbabwe only.",
            "features": [],
            "summary":  "Out of coverage",
        }

    # If the loaded file is NOT the BGS Zimbabwe schema, fall back to the
    # legacy slim shape so the rest of the app keeps working.
    if not _is_bgs_zim:
        return {
            "status":   "ok",
            "features": matches,
            "summary":  _legacy_summarise(matches),
        }

    # ---- BGS Zimbabwe schema: decode the codes ---------------------------
    # If ANY matching polygon is Surface water, treat the location as
    # surface water and bail out \u2014 lake / reservoir overlays sit on top
    # of the underlying geology in this dataset, and a point on a lake
    # should never produce a groundwater prediction even if a deeper
    # geology polygon also intersects.
    sw_match = next((m for m in matches if m.get(GLG_FIELD) == "Surface water"), None)
    if sw_match is not None:
        return {
            "status":      "surface_water",
            "raw_glg":     sw_match.get(GLG_FIELD),
            "raw_hg_code": sw_match.get(HG_FIELD),
            "message":     "GPS point falls in a surface-water polygon "
                           "(lake or reservoir). No groundwater prediction "
                           "is generated for surface water.",
            "features":    matches,
            "summary":     "Surface water \u2014 no prediction",
        }

    # Pick the row with the most decoded information.
    primary = matches[0] if len(matches) == 1 else _pick_primary(matches)

    raw_glg = primary.get(GLG_FIELD)
    raw_hg  = primary.get(HG_FIELD)

    remap   = GEOLOGY_REMAP.get(raw_glg, {
        "model_class": None, "confidence": None,
        "flag": "Unknown_formation",
        "rationale": f"Formation '{raw_glg}' is not in the remapping table \u2014 "
                     f"flag for review.",
    })
    decoded = HG_CODE_DECODE.get(raw_hg, {})

    return {
        "status":                "ok",
        "raw_glg":               raw_glg,
        "raw_hg_code":           raw_hg,
        "model_class":           remap["model_class"],
        "remap_confidence":      remap["confidence"],
        "remap_flag":            remap["flag"],
        "remap_rationale":       remap["rationale"],
        "bgs_aquifer_type":      decoded.get("aquifer_type"),
        "bgs_baseline_yield":    decoded.get("yield_class"),
        "bgs_baseline_yield_lps": decoded.get("yield_lps"),
        "features":              matches,
        "summary":               _bgs_summarise(raw_glg, raw_hg, remap, decoded),
    }


def legacy_lookup(lat: float, lon: float) -> dict:
    """
    Slimmer lookup that mirrors the older `lookup()` shape: {found, features,
    summary}. Kept for a couple of internal callers that only need raw
    attributes.
    """
    rich = lookup(lat, lon)
    if rich["status"] in ("not_configured", "out_of_coverage"):
        return {"found": False, "features": [], "summary": rich.get("summary", "")}
    return {
        "found":    True,
        "features": rich.get("features", []),
        "summary":  rich.get("summary", ""),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_native(v):
    """Convert pandas/numpy scalars to plain Python so JSON encoding works."""
    if v is None:
        return None
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:                    # noqa: BLE001
            pass
    if isinstance(v, (int, float, bool, str)):
        return v
    return str(v)


def _pick_primary(matches: list) -> dict:
    """
    Used when a point is contained by multiple polygons. The BGS Zimbabwe
    polygons mostly do NOT overlap, but border seams produce occasional
    duplicates. We pick the row that is least 'n/a' \u2014 i.e. the one with
    the most decoded information.
    """
    def score(row):
        glg = row.get(GLG_FIELD) or ""
        hg  = row.get(HG_FIELD)  or ""
        s = 0
        if glg and glg != "Surface water":
            s += 1
        if hg and hg != "n/a":
            s += 1
        return s

    return max(matches, key=score)


def _bgs_summarise(raw_glg, raw_hg, remap, decoded) -> str:
    parts = []
    if raw_glg:
        parts.append(raw_glg)
    yield_class = decoded.get("yield_class")
    if yield_class:
        parts.append(f"BGS yield: {yield_class}")
    if remap.get("model_class"):
        parts.append(f"Model class: {remap['model_class']}")
    return " \u00b7 ".join(parts) if parts else "BGS feature at this point"


def _legacy_summarise(matches: list) -> str:
    """Fallback for non-BGS shapefiles."""
    if len(matches) == 1:
        m = matches[0]
        for key in ("unit_name", "name", "NAME", "aquifer", "AQUIFER"):
            if key in m and m[key]:
                return f"In {m[key]}"
        return "1 hydrogeological feature at this point"
    return f"{len(matches)} overlapping hydrogeological features at this point"


# ---------------------------------------------------------------------------
# GeoJSON export \u2014 used by /api/hydrogeology.geojson on the Model Info page.
# ---------------------------------------------------------------------------
# We compute it once and cache the result. The shapefile has ~hundreds of
# polygons \u2014 small enough to ship to the browser, big enough that we
# don't want to recompute every time the page loads.
_geojson_cache: Optional[dict] = None


def to_geojson(simplify_tolerance: float = 0.005) -> dict:
    """
    Serialise the loaded shapefile as a GeoJSON FeatureCollection in WGS84,
    enriched with the decoded model class for client-side colouring.

    Args:
        simplify_tolerance: Douglas\u2013Peucker tolerance in degrees. ~0.005
            (~500 m at the equator) gives a noticeable file-size reduction
            while keeping polygon shapes recognisable at country scale.

    Returns:
        A GeoJSON dict, or {"type": "FeatureCollection", "features": []}
        if the shapefile didn't load. Result is cached.
    """
    global _geojson_cache
    if _geojson_cache is not None:
        return _geojson_cache

    if not is_ready():
        _geojson_cache = {"type": "FeatureCollection", "features": []}
        return _geojson_cache

    try:
        # Simplify polygons in-place on a copy. shapefile is already in
        # EPSG:4326 (Leaflet/web-friendly) by the time we get here.
        gdf = _gdf.copy()
        try:
            gdf["geometry"] = gdf.geometry.simplify(
                simplify_tolerance, preserve_topology=True
            )
        except Exception:                        # noqa: BLE001
            # If simplification fails (very rare), just use the raw geoms.
            pass

        features = []
        for _, row in gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue

            props = {}
            for col in _columns:
                props[col] = _to_native(row[col])

            # Attach decoded model_class for browser-side colouring.
            if _is_bgs_zim:
                raw_glg = props.get(GLG_FIELD)
                remap = GEOLOGY_REMAP.get(raw_glg, {})
                props["__model_class"] = remap.get("model_class")
                if raw_glg == "Surface water":
                    props["__model_class"] = "Surface water"

            try:
                from shapely.geometry import mapping
                geom_json = mapping(geom)
            except Exception:                    # noqa: BLE001
                continue

            features.append({
                "type":       "Feature",
                "properties": props,
                "geometry":   geom_json,
            })

        _geojson_cache = {
            "type":     "FeatureCollection",
            "features": features,
        }
        return _geojson_cache

    except Exception as exc:                     # noqa: BLE001
        print(f"[hydrogeology] to_geojson failed: {exc}")
        _geojson_cache = {"type": "FeatureCollection", "features": []}
        return _geojson_cache
