/* ============================================================================
   Save site survey.
   --------------------------------------------------------------------------
   Watches window.__surveyState (mutated by geo.js + predict.js), lights
   up the readiness checklist as each piece becomes available, enables
   the Save button only when ALL four are in place, and POSTs the full
   survey to /api/locations on click.

   Hydro readiness rule: the ground-truth map is binary — we have a
   non-null hydrogeology lookup OR we don't. Status of "ok" satisfies
   readiness; "surface_water" or "out_of_coverage" do NOT (because in
   those cases we never produced a prediction in the first place).
   ============================================================================ */

(() => {
  const btnSave    = document.getElementById("btn-save-survey");
  const labelInput = document.getElementById("save-label");
  const toast      = document.getElementById("save-toast");
  const checks = {
    gps:        document.getElementById("ready-gps"),
    hydro:      document.getElementById("ready-hydro"),
    predictors: document.getElementById("ready-predictors"),
    prediction: document.getElementById("ready-prediction"),
  };

  if (!btnSave) return;

  function evaluateReadiness() {
    const s = window.__surveyState;

    const gps_ok = !!(s.gps && Number.isFinite(s.gps.latitude) && Number.isFinite(s.gps.longitude));

    // Hydro is ready only if we got a successful "ok" status. Surface
    // water and out-of-coverage explicitly DON'T satisfy readiness.
    const hydro_ok = !!(s.hydro && s.hydro.status === "ok");

    const predictors_ok = !!(s.predictors && Object.keys(s.predictors).length);

    // Prediction must contain the rich raw_model object from /api/predict.
    const prediction_ok = !!(s.prediction && s.prediction.raw_model);

    toggle(checks.gps,        gps_ok);
    toggle(checks.hydro,      hydro_ok);
    toggle(checks.predictors, predictors_ok);
    toggle(checks.prediction, prediction_ok);

    btnSave.disabled = !(gps_ok && hydro_ok && predictors_ok && prediction_ok);
  }

  function toggle(el, on) {
    if (!el) return;
    el.classList.toggle("done", !!on);
  }

  evaluateReadiness();
  if (window.__surveyEvents) {
    window.__surveyEvents.addEventListener("change", evaluateReadiness);
  }
  setInterval(evaluateReadiness, 500);

  btnSave.addEventListener("click", async () => {
    const s = window.__surveyState;
    if (btnSave.disabled) return;

    btnSave.disabled = true;
    const oldText = btnSave.textContent;
    btnSave.textContent = "Saving…";
    toast.textContent = "";
    toast.style.color = "var(--text-mute)";

    try {
      // Combine predictors with their per-feature 'inferred' flags
      // and any geology override into a single audit-friendly payload.
      const predictorsPayload = {
        values:           s.predictors || {},
        inferred:         s.inferred   || {},
        inferred_count:   s.inferred_count || 0,
      };

      // Bundle the optional notable-features free-text note in with
      // supplementary so it is persisted on the saved record.
      const supplementaryPayload = Object.assign({}, s.supplementary || {});
      if (s.notable_features) {
        supplementaryPayload.notable_features = s.notable_features;
      }

      const res = await fetch("/api/locations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          latitude:      s.gps.latitude,
          longitude:     s.gps.longitude,
          label:         labelInput.value || null,
          hydrogeology:  s.hydro,
          predictors:    predictorsPayload,
          supplementary: supplementaryPayload,
          land_use:      s.land_use      || {},
          prediction:    s.prediction    || {},
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || `Server returned ${res.status}`);

      toast.style.color = "var(--success)";
      toast.innerHTML =
        `✓ Saved as survey #${body.id}. ` +
        `<a href="/saved/${body.id}/report">View report →</a>`;

      labelInput.value = "";
    } catch (e) {
      toast.style.color = "var(--danger)";
      toast.textContent = "Could not save: " + e.message;
    } finally {
      btnSave.textContent = oldText;
      evaluateReadiness();
    }
  });
})();
