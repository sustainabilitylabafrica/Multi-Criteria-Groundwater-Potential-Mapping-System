"""
Groundwater Potential Mapping System - Flask edition.

Page routes
-----------
    /                      Home
    /predict               Predict (GPS + hydrogeology + predictors +
                           supplementary obs + land use + prediction +
                           save)
    /saved                 Saved Locations table view
    /saved/<id>/report     Site Survey report for one location
    /history-report        Location History report
    /expert-review         Predictions flagged for expert review
    /data-sources          BGS attribution and licence info
    /model-info            Model Info
    /feature-guide         Feature Guide
    /about                 About

JSON API routes
---------------
    POST   /api/predict             run the model + scoring + cross-checks
    POST   /api/locations           save a full site survey
    GET    /api/locations           list saved surveys
    DELETE /api/locations/<id>      delete a saved survey
    POST   /api/locations/<id>/expert-review  record an expert decision
    GET    /api/hydrogeology        look up shapefile attributes for lat/lon
    GET    /api/saved.geojson       export all surveys as GeoJSON for QGIS

PDF download routes
-------------------
    /saved/<id>/report.pdf
    /history-report.pdf
"""

import logging
import os
from datetime import datetime, timedelta
from io import BytesIO

from flask import (
    Flask, abort, jsonify, render_template, request, send_file,
)

from config import Config, BASE_DIR
from models import db, SavedLocation, run_simple_migrations
import ml
import hydrogeology
import confidence
from timeutil import now_cat, format_cat, format_cat_iso


# ---------------------------------------------------------------------------
# PDF rendering (unchanged from previous version)
# ---------------------------------------------------------------------------
def _render_pdf_from_html(html_string: str) -> bytes:
    """Render an HTML string to PDF bytes using WeasyPrint."""
    from weasyprint import HTML, CSS

    css_path = os.path.join(BASE_DIR, "static", "css", "report.css")
    stylesheets = []
    if os.path.exists(css_path):
        stylesheets.append(CSS(filename=css_path))

    return HTML(string=html_string, base_url=BASE_DIR).write_pdf(stylesheets=stylesheets)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(Config)

    os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)

    db.init_app(app)
    with app.app_context():
        db.create_all()

    # Run any column-add migrations against an existing SQLite DB so users
    # upgrading from the previous version don't have to delete it.
    run_simple_migrations(app)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    app.logger.info("ML pipeline ready: %s", ml.is_ready())
    app.logger.info("Hydrogeology ready: %s", hydrogeology.is_ready())
    app.logger.info("BGS schema detected: %s", hydrogeology.has_bgs_schema())

    # Make a friendly-name lookup available to every template. The model's
    # internal feature names are dotted (e.g. "Soil.Texture",
    # "Natural.vegitation..tree..vigour") because that's what the trained
    # pipeline expects — we never rename them at the Python layer because
    # that would break the model. This map is *display only*.
    PREDICTOR_DISPLAY_NAMES = {
        "Soil.Texture":                     "Soil Type",
        "Geological.Features":              "Geological Feature",
        "Natural.vegitation..tree..vigour": "Vegetation Vigour",
        "Natural.vegitation..tree..height": "Vegetation Height",
        "Drainage.Density":                 "Drainage Density",
        "Elevation":                        "Elevation",
        "Soil.Colour":                      "Soil Colour",
    }

    def _pretty_predictor(name: str) -> str:
        """Return the human-friendly label for a predictor, or the
        original name if no mapping exists."""
        if not name:
            return name
        return PREDICTOR_DISPLAY_NAMES.get(name, name)

    @app.context_processor
    def _inject_helpers():
        return {"pretty_predictor": _pretty_predictor}

    register_routes(app)
    return app


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
def register_routes(app: Flask) -> None:

    # ============== Page routes ==========================================

    @app.route("/")
    def home():
        return render_template("home.html", page="home")

    @app.route("/predict")
    def predict_page():
        return render_template(
            "predict.html",
            page="predict",
            ml_ready=ml.is_ready(),
            hydro_ready=hydrogeology.is_ready(),
            predictors=ml.predictor_options(),
            model_name=(ml.encoding_metadata or {}).get("winner", ""),
        )

    @app.route("/saved")
    def saved_page():
        rows = (SavedLocation.query
                .order_by(SavedLocation.created_at.desc())
                .all())
        return render_template(
            "saved.html",
            page="saved",
            rows=[r.to_summary_dict() for r in rows],
        )

    @app.route("/saved/<int:loc_id>/report")
    def site_survey_report(loc_id):
        loc = SavedLocation.query.get_or_404(loc_id)
        return render_template(
            "report_site.html",
            loc=loc.to_dict(),
            generated_at=format_cat(now_cat()),
        )

    @app.route("/saved/<int:loc_id>/report.pdf")
    def site_survey_report_pdf(loc_id):
        loc = SavedLocation.query.get_or_404(loc_id)
        html = render_template(
            "report_site.html",
            loc=loc.to_dict(),
            generated_at=format_cat(now_cat()),
            for_pdf=True,
        )
        pdf_bytes = _render_pdf_from_html(html)
        return send_file(BytesIO(pdf_bytes), mimetype="application/pdf",
                         as_attachment=True, download_name=f"site_survey_{loc_id}.pdf")

    @app.route("/history-report")
    def history_report():
        start_str = request.args.get("start", "")
        end_str   = request.args.get("end", "")
        rows, start_dt, end_dt, error = _query_history(start_str, end_str)
        return render_template(
            "report_history.html",
            rows=[r.to_dict() for r in rows] if rows else [],
            count=len(rows) if rows else 0,
            start_str=start_str, end_str=end_str,
            start_dt=start_dt, end_dt=end_dt, error=error,
            generated_at=format_cat(now_cat()),
        )

    @app.route("/history-report.pdf")
    def history_report_pdf():
        start_str = request.args.get("start", "")
        end_str   = request.args.get("end", "")
        rows, start_dt, end_dt, error = _query_history(start_str, end_str)
        if error:
            abort(400, error)
        html = render_template(
            "report_history.html",
            rows=[r.to_dict() for r in rows], count=len(rows),
            start_str=start_str, end_str=end_str,
            start_dt=start_dt, end_dt=end_dt, error=None,
            generated_at=format_cat(now_cat()),
            for_pdf=True,
        )
        pdf_bytes = _render_pdf_from_html(html)
        return send_file(BytesIO(pdf_bytes), mimetype="application/pdf",
                         as_attachment=True,
                         download_name=f"history_{start_str}_to_{end_str}.pdf")

    @app.route("/expert-review")
    def expert_review_page():
        rows = (SavedLocation.query
                .filter(SavedLocation.needs_expert_review.is_(True))
                .order_by(SavedLocation.created_at.desc())
                .all())
        return render_template(
            "expert_review.html",
            page="expert-review",
            rows=[r.to_dict() for r in rows],
        )

    @app.route("/data-sources")
    def data_sources_page():
        return render_template(
            "data_sources.html",
            page="data-sources",
            hydro_status=hydrogeology.status(),
        )

    @app.route("/model-info")
    def model_info_page():
        return render_template("model_info.html", page="model-info",
                               selected_features=ml.selected_features or [])

    @app.route("/feature-guide")
    def feature_guide_page():
        return render_template("feature_guide.html", page="feature-guide",
                               features=ml.feature_guide())

    @app.route("/about")
    def about_page():
        return render_template("about.html", page="about")

    # ============== JSON API ==========================================

    @app.route("/api/predict", methods=["POST"])
    def api_predict():
        """
        Run the full prediction pipeline.

        Request body (JSON):
            {
                "lat":             -20.13,
                "lon":              28.62,
                "predictors":     {feature_name: value, ...},   # the 6 model inputs
                "inferred_count":  0,                            # how many of the 6 were inferred not observed
                "supplementary":  {flag_name: bool, ...},        # Phase 2 \u00a73.1.2 supplementary observations
                "land_use":       {flag_name: bool, ...}         # Phase 3 land-use checklist
            }

        Response (JSON):
            {
                "raw_model": {label, prediction, high_potential_pct, low_potential_pct, model_used},
                "geology":   {raw_glg, model_class, remap_confidence, remap_flag, ...},
                "tcs":       {tcs, c1, c2, c3, c4, explanations},
                "lups":      {score, level, components},
                "modifier":  {final_label, final_class_int, tcs_adjusted, downgrade_applied, lups_flags, advisory},
                "bgs_check": {status, model_binary, bgs_binary, message},
                "flags":     [str, ...],
                "expert_review": {needs_review, reasons}
            }

        On geology lookup status != 'ok', returns 422 with a clear message.
        """
        payload = request.get_json(silent=True) or {}
        try:
            lat = float(payload["lat"])
            lon = float(payload["lon"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "lat and lon are required and must be numeric"}), 400
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return jsonify({"error": "lat/lon out of range"}), 400

        predictors        = payload.get("predictors")        or {}
        inferred_count    = payload.get("inferred_count", 0)
        supplementary     = payload.get("supplementary")     or {}
        land_use          = payload.get("land_use")          or {}

        # ---- 1. Geology lookup ------------------------------------------------
        geo = hydrogeology.lookup(lat, lon)

        if geo["status"] == "not_configured":
            return jsonify({
                "error":   "hydrogeology_not_configured",
                "message": geo.get("message", "Hydrogeology data is not configured on this server."),
            }), 503

        if geo["status"] == "out_of_coverage":
            return jsonify({
                "error":   "out_of_coverage",
                "message": geo.get("message", "GPS point is outside the dataset coverage."),
            }), 422

        if geo["status"] == "surface_water":
            return jsonify({
                "error":   "surface_water",
                "message": geo.get("message", "GPS point is inside a surface-water body."),
                "geology": geo,
            }), 422

        # ---- 2. Run the SVM model --------------------------------------------
        try:
            raw_model = ml.predict(predictors)
        except Exception as exc:  # noqa: BLE001
            app.logger.exception("Prediction failed")
            return jsonify({"error": "prediction_failed", "message": str(exc)}), 400

        # ---- 3. BGS regional baseline cross-check ----------------------------
        bgs_check = confidence.bgs_baseline_check(
            model_label=raw_model.get("label", ""),
            bgs_yield_class=geo.get("bgs_baseline_yield"),
        )

        # ---- 4. Total Confidence Score ---------------------------------------
        # Count supplementary indicators that "support" the prediction. Any
        # non-empty supplementary entry counts as supportive in v1; future
        # work can encode direction (supports High vs Low) explicitly.
        biophys_support = sum(1 for v in (supplementary or {}).values() if v)
        tcs = confidence.compute_tcs(
            inferred_predictors_count=inferred_count,
            remap_confidence=geo.get("remap_confidence"),
            biophysical_support_count=biophys_support,
            bgs_check_status=bgs_check["status"],
        )

        # ---- 5. LUPS modifier ------------------------------------------------
        lups = confidence.compute_lups(land_use)
        modifier = confidence.apply_lups_modifier(
            raw_label=raw_model.get("label", ""),
            raw_class_int=int(raw_model.get("prediction", 0)),
            lups=lups,
            tcs=tcs["tcs"],
        )

        # ---- 6. Expert-review triggers ---------------------------------------
        triggers = confidence.expert_review_triggers(
            tcs_adjusted=modifier["tcs_adjusted"],
            lups_flags=modifier["lups_flags"],
            remap_confidence=geo.get("remap_confidence"),
            c3=tcs["c3"],
            bgs_check_status=bgs_check["status"],
        )

        # ---- 7. Aggregate flags ----------------------------------------------
        all_flags = []
        if geo.get("remap_flag"):
            all_flags.append(geo["remap_flag"])
        all_flags.extend(modifier.get("lups_flags", []))

        return jsonify({
            "raw_model":     raw_model,
            "geology":       geo,
            "tcs":           tcs,
            "lups":          lups,
            "modifier":      modifier,
            "bgs_check":     bgs_check,
            "flags":         all_flags,
            "expert_review": triggers,
        }), 200

    @app.route("/api/locations", methods=["POST"])
    def api_save_location():
        """
        Save a full site-survey record. The body comes straight from the
        front-end after a successful /api/predict, so we trust its shape
        and just persist what we got.
        """
        p = request.get_json(silent=True) or {}
        try:
            lat = float(p["latitude"])
            lon = float(p["longitude"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "latitude and longitude are required and must be numeric"}), 400
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return jsonify({"error": "latitude/longitude out of range"}), 400

        # Required structured pieces. Front end is responsible for filling these.
        predictors    = p.get("predictors")
        prediction    = p.get("prediction")
        if not isinstance(predictors, dict) or not predictors:
            return jsonify({"error": "predictors are required"}), 400
        if not isinstance(prediction, dict):
            return jsonify({"error": "prediction is required"}), 400

        # Optional structured pieces
        hydrogeology_data = p.get("hydrogeology") or {}
        supplementary     = p.get("supplementary") or {}
        land_use          = p.get("land_use") or {}

        label = p.get("label")
        if label is not None:
            label = str(label).strip()
            if label == "":
                label = None
            elif len(label) > 200:
                label = label[:200]

        # Indexed columns for fast filtering / sorting in the saved table
        final_class = (prediction.get("modifier", {}) or {}).get("final_label") \
                      or prediction.get("final_label") \
                      or prediction.get("label")
        tcs_adj = (prediction.get("modifier", {}) or {}).get("tcs_adjusted")
        if tcs_adj is None:
            tcs_adj = (prediction.get("tcs") or {}).get("tcs")
        needs_review = bool((prediction.get("expert_review") or {}).get("needs_review"))

        loc = SavedLocation(
            latitude=lat,
            longitude=lon,
            label=label,
            final_class=final_class,
            tcs=int(tcs_adj) if tcs_adj is not None else None,
            needs_expert_review=needs_review,
        )
        loc.set_hydrogeology(hydrogeology_data)
        loc.set_predictors(predictors)
        loc.set_supplementary(supplementary)
        loc.set_land_use(land_use)
        loc.set_prediction(prediction)
        # Expert review starts empty - filled in via /api/locations/<id>/expert-review
        loc.set_expert_review({})

        db.session.add(loc)
        db.session.commit()
        app.logger.info("Saved survey #%s - (%.6f, %.6f) label=%r final=%r tcs=%s review=%s",
                        loc.id, loc.latitude, loc.longitude, loc.label,
                        final_class, tcs_adj, needs_review)
        return jsonify(loc.to_dict()), 201

    @app.route("/api/locations", methods=["GET"])
    def api_list_locations():
        rows = (SavedLocation.query.order_by(SavedLocation.created_at.desc())
                .limit(500).all())
        return jsonify([r.to_summary_dict() for r in rows]), 200

    @app.route("/api/locations/<int:loc_id>", methods=["DELETE"])
    def api_delete_location(loc_id):
        loc = SavedLocation.query.get_or_404(loc_id)
        db.session.delete(loc)
        db.session.commit()
        app.logger.info("Deleted survey #%s", loc_id)
        return jsonify({"deleted": loc_id}), 200

    @app.route("/api/locations/<int:loc_id>/expert-review", methods=["POST"])
    def api_expert_review(loc_id):
        """Record an expert review decision against a saved survey."""
        loc = SavedLocation.query.get_or_404(loc_id)
        p = request.get_json(silent=True) or {}

        decision = p.get("decision")
        if decision not in ("confirmed", "overridden", "resurvey", "geophysics"):
            return jsonify({"error": "decision must be one of: confirmed, overridden, resurvey, geophysics"}), 400

        review = {
            "decision":         decision,
            "override_class":   p.get("override_class"),         # optional
            "rationale":        (p.get("rationale") or "").strip(),
            "reviewer":         (p.get("reviewer")  or "").strip()[:60],
            "reviewed_at":      format_cat_iso(now_cat()),
        }
        loc.set_expert_review(review)

        # Once reviewed, remove from the queue.
        loc.needs_expert_review = False

        # If the reviewer overrode the class, reflect it in the indexed column.
        if decision == "overridden" and review["override_class"]:
            loc.final_class = review["override_class"]

        db.session.commit()
        app.logger.info("Expert review on #%s: %s by %r", loc.id, decision, review["reviewer"])
        return jsonify(loc.to_dict()), 200

    @app.route("/api/hydrogeology", methods=["GET"])
    def api_hydrogeology_lookup():
        """Used by the Predict page to populate the geology context card."""
        try:
            lat = float(request.args.get("lat", ""))
            lon = float(request.args.get("lon", ""))
        except ValueError:
            return jsonify({"error": "lat and lon are required and must be numeric"}), 400
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return jsonify({"error": "lat/lon out of range"}), 400

        result = hydrogeology.lookup(lat, lon)
        result["app_status"] = hydrogeology.status()
        return jsonify(result), 200

    @app.route("/api/hydrogeology.geojson", methods=["GET"])
    def api_hydrogeology_geojson():
        """
        Serve the loaded BGS hydrogeology shapefile as GeoJSON, enriched
        with the decoded model class. Used by the Model Info page to
        render the underlying source data on a Leaflet map.
        """
        return jsonify(hydrogeology.to_geojson()), 200, {
            "Content-Type": "application/geo+json",
        }

    @app.route("/api/saved.geojson", methods=["GET"])
    def api_saved_geojson():
        """Export every saved survey as a GeoJSON FeatureCollection for QGIS."""
        rows = SavedLocation.query.order_by(SavedLocation.created_at.asc()).all()
        features = []
        for r in rows:
            d = r.to_dict()
            pred = d.get("prediction") or {}
            # Properties: keep it flat where possible so QGIS attribute table is usable.
            props = {
                "id":                 r.id,
                "label":              r.label,
                "final_class":        r.final_class,
                "tcs":                r.tcs,
                "needs_review":       bool(r.needs_expert_review),
                "raw_model_label":    (pred.get("raw_model") or {}).get("label"),
                "high_pct":           (pred.get("raw_model") or {}).get("high_potential_pct"),
                "lups":               (pred.get("lups") or {}).get("score"),
                "lups_level":         (pred.get("lups") or {}).get("level"),
                "bgs_check":          (pred.get("bgs_check") or {}).get("status"),
                "model_class":        (d.get("hydrogeology") or {}).get("model_class"),
                "raw_glg":            (d.get("hydrogeology") or {}).get("raw_glg"),
                "raw_hg_code":        (d.get("hydrogeology") or {}).get("raw_hg_code"),
                "remap_confidence":   (d.get("hydrogeology") or {}).get("remap_confidence"),
                "remap_flag":         (d.get("hydrogeology") or {}).get("remap_flag"),
                "created_at":         d.get("created_at"),
            }
            features.append({
                "type":       "Feature",
                "geometry":   {"type": "Point", "coordinates": [r.longitude, r.latitude]},
                "properties": props,
            })
        return jsonify({
            "type":     "FeatureCollection",
            "features": features,
        }), 200, {"Content-Type": "application/geo+json"}


def _query_history(start_str: str, end_str: str):
    if not start_str or not end_str:
        return None, None, None, "Pick a start and end date."
    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        end_dt   = datetime.strptime(end_str,   "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        return None, None, None, "Invalid date format - use YYYY-MM-DD."
    if end_dt <= start_dt:
        return None, None, None, "End date must be on or after start date."
    rows = (SavedLocation.query
            .filter(SavedLocation.created_at >= start_dt)
            .filter(SavedLocation.created_at <  end_dt)
            .order_by(SavedLocation.created_at.asc())
            .all())
    return rows, start_dt, datetime.strptime(end_str, "%Y-%m-%d"), None


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
