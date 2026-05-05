# Zimbabwe BGS Hydrogeology dataset

This folder contains the Zimbabwe extract of the **BGS Africa Groundwater
Atlas Country Hydrogeology Maps** (1:5,000,000 scale, CC BY-SA 4.0). It is
the single hydrogeological reference layer the app uses.

See `LICENCE.txt` for full attribution and licence terms.

## Files

| File | What it is |
|---|---|
| `Zimbabwe_HG.shp` (+ `.shx`, `.dbf`, `.prj`, `.cpg`, `.sbn`, `.sbx`) | The shapefile bundle — 219 polygons covering Zimbabwe |
| `Zimbabwe_HG.shp.xml` | ISO/FGDC metadata (informational only) |
| `LICENCE.txt` | CC BY-SA 4.0 attribution statement |
| `README.md` | This file |

## What's in the data

**Geometry.** 219 polygons (205 single-part, 14 multipart). EPSG:4326 (WGS84
geographic). Total mapped area ≈ Zimbabwe (391,580 km²).

**Attributes (only two — the BGS Atlas is deliberately compact):**

- `ZimGLG` — geological description (9 classes; lithostratigraphic):

| Class | Polygons | Area share |
|---|---:|---:|
| Precambrian Basement Complex and Metavolcanics | 73 | ~60% |
| Sedimentary - Mesozoic-Palaeozoic (Upper and Lower Karoo) | 68 | ~13% |
| Sedimentary Kalahari Basin | 23 | ~10% |
| Igneous - Upper Karoo Basalt | 22 | ~7% |
| Unconsolidated sedimentary | 13 | ~1% |
| Sedimentary - Cretaceous | 9 | ~1% |
| Precambrian Metasediments | 5 | ~5% |
| Surface water | 4 | ~1% |
| Great Dyke | 1 | ~1% |

- `ZimHGComb` — BGS hydrogeology code, format `AquiferType-Productivity`:

| Code | Aquifer type | Yield class | Typical L/s |
|---|---|---|---|
| `B-L` | Precambrian Basement | Low | 0.1–0.5 |
| `CSF-M/H` | Cons. Sedimentary Fracture | Mod–High | 2–20 |
| `CSI-L` | Cons. Sedimentary Intergranular | Low | 0.1–0.5 |
| `CSIF-H` | Cons. Sed. Intergranular+Fracture | High | 5–20 |
| `I-M` | Igneous (volcanic) | Moderate | 2–5 |
| `U-H` | Unconsolidated | High | 5–20 |
| `n/a` | Surface water (lake/reservoir) | — | — |

These codes are decoded automatically by `hydrogeology.py` so the rest of the
app works with human-readable labels.

## Important limitations (from BGS user guide)

- **National scale only** — 1:5M means polygon boundaries are accurate to
  ~km, not to the metre. A spatial join on a single GPS point returns the
  *region* the point sits in, not a site assessment.
- **Topmost aquifer only** — deeper aquifers are not represented.
- **No supplementary layers** — this dataset does *not* contain faults,
  lineaments, recharge zones, aquifer extents, or borehole points. The
  Phase 6 spatial validation in the decision-making roadmap that depends
  on those layers is currently deferred (see
  `PREDICTION_ALGORITHM_GUIDE.md`).

## How the app uses this data

1. **Geology auto-suggest at the GPS point** — the surveyor's location is
   spatial-joined against the polygons. The matched `ZimGLG` is
   re-classified into the model's two-class system (Granite or Limestone)
   using a remapping table that records confidence ("High", "Medium") and
   any flags ("Alluvial", "Basalt_review", etc.). The user is shown the
   suggestion and can override it from visible field outcrops.
2. **BGS regional-baseline cross-check after prediction** — the model's
   binary output (Low / High Potential) is compared against the BGS
   yield class for the polygon. Disagreement raises a flag and is one of
   the triggers for expert review.

The remapping table and the decoding tables both live in `hydrogeology.py`
as the single source of truth — change them there if the BGS source data
ever changes.
