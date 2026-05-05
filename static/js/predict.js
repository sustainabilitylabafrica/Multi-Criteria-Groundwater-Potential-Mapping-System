/* ============================================================================
   Predictor form + supplementary obs + vegetation/recharge/seasonal +
   notable features + land use + prediction.
   --------------------------------------------------------------------------
   Mirrors all UI state into window.__surveyState. The save-survey panel
   reads it; this file calls /api/predict, renders the result, and writes
   the full server response back into state for save.js to persist.

   The endpoint returns a richer object:
       { raw_model, geology, tcs, lups, modifier, bgs_check, flags,
         expert_review }

   Notes
   -----
   * Geological Features is no longer a user-facing dropdown — it is
     auto-inferred from the BGS map data and rendered read-only in the
     predictor card. The hidden <select> still exists in the form so the
     value flows into the predict request unchanged.
   * BGS-vs-model disagreement is intentionally rendered as a calm
     informational note (not a red error). It simply means the regional
     baseline differs from the model and we recommend on-the-ground
     equipment to refine.
   ============================================================================ */

(() => {
  const btn      = document.getElementById("btn-predict");
  const grid     = document.getElementById("predictor-grid");
  const result   = document.getElementById("result");
  const detail   = document.getElementById("result-detail");
  const errorBox = document.getElementById("predict-error");

  if (!btn || !grid) return;

  // -------- Predictor + inferred-flag sync --------------------------------
  function syncPredictors() {
    const current = {};
    const inferred = {};
    grid.querySelectorAll("[data-feature]").forEach((el) => {
      current[el.dataset.feature] = el.value;
    });
    grid.querySelectorAll("[data-inferred]").forEach((el) => {
      inferred[el.dataset.inferred] = !!el.checked;
    });
    window.__surveyState.predictors       = Object.keys(current).length ? current : null;
    window.__surveyState.inferred         = inferred;
    window.__surveyState.inferred_count   = Object.values(inferred).filter(Boolean).length;
    invalidatePrediction();
    window.__surveyEmit && window.__surveyEmit();
  }
  grid.querySelectorAll("[data-feature]").forEach((el) => {
    el.addEventListener("change", syncPredictors);
  });
  grid.querySelectorAll("[data-inferred]").forEach((el) => {
    el.addEventListener("change", syncPredictors);
  });
  syncPredictors();

  // -------- Supplementary observations (checkboxes + dropdowns) -----------
  function syncSupplementary() {
    const supp = {};
    document.querySelectorAll("[data-supp]").forEach((el) => {
      supp[el.dataset.supp] = !!el.checked;
    });
    document.querySelectorAll("[data-supp-select]").forEach((el) => {
      // Only include if the user picked a non-empty value
      if (el.value) supp[el.dataset.suppSelect] = el.value;
    });
    window.__surveyState.supplementary = supp;
    invalidatePrediction();
    window.__surveyEmit && window.__surveyEmit();
  }
  document.querySelectorAll("[data-supp]").forEach((el) => {
    el.addEventListener("change", syncSupplementary);
  });
  document.querySelectorAll("[data-supp-select]").forEach((el) => {
    el.addEventListener("change", syncSupplementary);
  });
  syncSupplementary();

  // -------- Notable geological features (free-text) ------------------------
  const notable = document.getElementById("notable-features");
  function syncNotableFeatures() {
    window.__surveyState.notable_features =
      notable && notable.value ? notable.value.trim() : "";
    window.__surveyEmit && window.__surveyEmit();
  }
  if (notable) {
    notable.addEventListener("input", syncNotableFeatures);
    notable.addEventListener("change", syncNotableFeatures);
    syncNotableFeatures();
  }

  // -------- Land use + live LUPS preview ----------------------------------
  // Weights MUST match server (confidence.LUPS_WEIGHTS).
  const LUPS_WEIGHTS = {
    active_mining_within_500m:         -3,
    artisanal_mining_or_wetland_drain: -2,
    urban_impervious_over_30pct:       -2,
    deforestation:                     -1,
    irrigated_ag_borehole_source:      -1,
    farm_dam_or_weir_within_300m:      +1,
  };

  function syncLandUse() {
    const lu = {};
    let total = 0;
    document.querySelectorAll("[data-lups]").forEach((el) => {
      const k = el.dataset.lups;
      lu[k] = !!el.checked;
      if (el.checked && (k in LUPS_WEIGHTS)) total += LUPS_WEIGHTS[k];
    });
    window.__surveyState.land_use = lu;
    const display = document.getElementById("lups-display");
    const level   = document.getElementById("lups-level");
    if (display) display.textContent = (total > 0 ? "+" : "") + total;
    if (level) {
      let lvl = "low";
      if (total < 0 && total >= -2) lvl = "moderate";
      else if (total < -2)          lvl = "severe";
      level.textContent = `(${lvl})`;
      level.style.color = (lvl === "severe") ? "var(--danger)"
                          : (lvl === "moderate") ? "var(--warning)"
                          : "var(--text-mute)";
    }
    invalidatePrediction();
    window.__surveyEmit && window.__surveyEmit();
  }
  document.querySelectorAll("[data-lups]").forEach((el) => {
    el.addEventListener("change", syncLandUse);
  });
  syncLandUse();

  function invalidatePrediction() {
    if (window.__surveyState.prediction) {
      window.__surveyState.prediction = null;
      result.style.display = "none";
      if (detail) detail.style.display = "none";
    }
  }

  // -------- Run prediction -----------------------------------------------
  btn.addEventListener("click", async () => {
    errorBox.textContent = "";
    errorBox.style.display = "none";
    result.style.display = "none";
    if (detail) detail.style.display = "none";

    const s = window.__surveyState;
    if (!s.gps) {
      showError("Detect your location first (Step 1).");
      return;
    }
    if (!s.predictors || Object.keys(s.predictors).length === 0) {
      showError("Fill in the predictor inputs (Step 3).");
      return;
    }

    // Geology is auto-inferred by geo.js from the BGS map data and
    // already lives in s.predictors — nothing extra to do here.
    const predictorsToSend = { ...s.predictors };

    btn.disabled = true;
    const oldText = btn.textContent;
    btn.textContent = "Predicting…";

    try {
      const res = await fetch("/api/predict", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lat:            s.gps.latitude,
          lon:            s.gps.longitude,
          predictors:     predictorsToSend,
          inferred_count: s.inferred_count || 0,
          supplementary:  s.supplementary || {},
          land_use:       s.land_use || {},
        }),
      });
      const body = await res.json().catch(() => ({}));

      if (res.status === 422 && body.error === "surface_water") {
        showError("This GPS point falls inside a surface-water body (lake or reservoir). " +
                  "No groundwater prediction is generated for surface water.");
        return;
      }
      if (res.status === 422 && body.error === "out_of_coverage") {
        showError("This GPS point is outside the BGS Zimbabwe coverage. " +
                  "The current dataset covers Zimbabwe only.");
        return;
      }
      if (!res.ok) throw new Error(body.message || body.error || `Server returned ${res.status}`);

      window.__surveyState.prediction = body;
      window.__surveyEmit && window.__surveyEmit();
      renderResult(body);
    } catch (e) {
      showError("Prediction failed: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = oldText;
    }
  });

  function showError(msg) {
    errorBox.textContent = msg;
    errorBox.style.display = "block";
  }

  // -------- Render ---------------------------------------------------------
  function renderResult(r) {
    const raw = r.raw_model || {};
    const mod = r.modifier  || {};
    const tcs = r.tcs       || {};
    const lups = r.lups     || {};

    const finalLabel = mod.final_label || raw.label || "—";
    const isHigh = (mod.final_class_int === 1) ||
                   (mod.final_class_int === undefined && raw.prediction === 1);

    result.classList.remove("high", "low");
    result.classList.add(isHigh ? "high" : "low");

    const icon = isHigh ? "🌱" : "⚠️";
    const downgradedNote = mod.downgrade_applied
      ? `<p class="muted" style="margin: 0.4rem 0 0;"><strong>Downgraded</strong> from High to Low by Land Use Pressure modifier.</p>`
      : "";

    result.innerHTML = `
      <h3>${icon} ${escapeHtml(finalLabel)}</h3>
      ${downgradedNote}
      <div class="metrics">
        <div class="metric">
          <div class="label">High potential confidence</div>
          <div class="value">${(raw.high_potential_pct ?? 0).toFixed(2)}%</div>
        </div>
        <div class="metric">
          <div class="label">Low potential confidence</div>
          <div class="value">${(raw.low_potential_pct ?? 0).toFixed(2)}%</div>
        </div>
        <div class="metric">
          <div class="label">Total Confidence Score</div>
          <div class="value">${mod.tcs_adjusted ?? tcs.tcs ?? "—"}<span style="font-size: 1rem; color: var(--text-mute);">/10</span></div>
        </div>
        <div class="metric">
          <div class="label">Land Use Pressure</div>
          <div class="value">${(lups.score >= 0 ? "+" : "")}${lups.score ?? 0}<span style="font-size: 1rem; color: var(--text-mute);"> (${lups.level || "—"})</span></div>
        </div>
      </div>
    `;
    result.style.display = "block";

    detail.innerHTML = renderDetail(r);
    detail.style.display = "block";
    detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function renderDetail(r) {
    const mod    = r.modifier   || {};
    const tcs    = r.tcs        || {};
    const bgs    = r.bgs_check  || {};
    const review = r.expert_review || {};
    const flags  = r.flags || [];
    const ex     = tcs.explanations || {};

    const tcsBars = [
      tcsRow("C1 — Data completeness",        tcs.c1, 3, ex.c1),
      tcsRow("C2 — Geology match quality",    tcs.c2, 3, ex.c2),
      tcsRow("C3 — Indicator convergence",    tcs.c3, 2, ex.c3),
      tcsRow("C4 — BGS baseline alignment",   tcs.c4, 2, ex.c4),
    ].join("");

    const flagPills = flags.length
      ? flags.map(f => `<span class="pill pill-bad" style="margin-right: 0.3rem;">${escapeHtml(f)}</span>`).join("")
      : `<span class="muted">No flags raised.</span>`;

    // BGS baseline rendering — explicitly NOT a red alert. When the model
    // and the BGS regional baseline disagree, we show a calm informational
    // note explaining what that disagreement actually means.
    let bgsBlock = "";
    if (bgs.status === "flag") {
      bgsBlock = `
        <div class="bgs-note">
          <p style="margin: 0 0 0.4rem 0;">
            <strong>Regional BGS baseline differs from the model prediction.</strong>
          </p>
          <p style="margin: 0;">
            The BGS Africa Groundwater Atlas indicates a
            <strong>${escapeHtml(bgs.bgs_binary || "—")}</strong>
            potential at the regional polygon scale, while the model predicts
            <strong>${escapeHtml(bgs.model_binary || "—")}</strong>
            for this specific point. This is not a contradiction so much as
            a sign that more on-the-ground information is needed — for
            example, an electromagnetic survey or other targeted geophysical
            measurements — to refine the picture and reconcile the regional
            baseline with the local conditions.
          </p>
        </div>
      `;
    } else {
      bgsBlock = `<p>${escapeHtml(bgs.message || "—")}</p>
        <p class="muted" style="font-size: 0.9rem;">
          Status: <strong>${escapeHtml(bgs.status || "—")}</strong>
          · Model says: ${escapeHtml(bgs.model_binary || "—")}
          · BGS baseline: ${escapeHtml(bgs.bgs_binary || "—")}
        </p>`;
    }

    // Expert review block. Filter the BGS-disagreement reason out of the
    // red banner — it's already explained calmly above. Show the banner
    // only if there are OTHER reasons.
    const otherReasons = (review.reasons || []).filter(
      (rsn) => !/BGS regional baseline/i.test(rsn)
    );
    const reviewBox = (review.needs_review && otherReasons.length)
      ? `
        <div class="alert info" style="margin-top: 0.75rem; border-color: rgba(201, 138, 46, 0.35); background: rgba(201, 138, 46, 0.05); color: var(--warning);">
          <strong>Expert review suggested.</strong>
          <ul style="margin: 0.4rem 0 0 1.2rem; color: var(--text);">
            ${otherReasons.map(rsn => `<li>${escapeHtml(rsn)}</li>`).join("")}
          </ul>
        </div>
      `
      : (review.needs_review
          ? `<p class="muted" style="margin-top: 0.5rem;">No additional expert-review triggers beyond the BGS baseline note above.</p>`
          : `<p class="muted" style="margin-top: 0.5rem;">No expert review triggers raised.</p>`);

    return `
      <div class="card" style="margin-top: 1rem;">
        <h3>Confidence breakdown</h3>
        <div class="tcs-table">${tcsBars}</div>

        <h3 style="margin-top: 1rem;">BGS regional baseline cross-check</h3>
        ${bgsBlock}

        <h3 style="margin-top: 1rem;">Modifier advisory</h3>
        <p>${escapeHtml(mod.advisory || "—")}</p>

        <h3 style="margin-top: 1rem;">Flags</h3>
        <div>${flagPills}</div>

        ${reviewBox}
      </div>
    `;
  }

  function tcsRow(label, score, max, explanation) {
    const pct = (max > 0) ? Math.round((score || 0) / max * 100) : 0;
    return `
      <div class="tcs-row">
        <div class="tcs-label">${escapeHtml(label)}</div>
        <div class="tcs-bar"><div class="tcs-bar-fill" style="width: ${pct}%;"></div></div>
        <div class="tcs-score">${score ?? 0}/${max}</div>
        <div class="tcs-explain muted">${escapeHtml(explanation || "")}</div>
      </div>
    `;
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
})();
