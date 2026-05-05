/* ============================================================================
   Expert review page — decision modal + POST.
   ============================================================================ */

(() => {
  const modal       = document.getElementById("review-modal");
  const titleEl     = document.getElementById("review-modal-title");
  const noteEl      = document.getElementById("review-modal-decision-note");
  const overrideRow = document.getElementById("review-override-row");
  const overrideClass = document.getElementById("review-override-class");
  const rationaleEl = document.getElementById("review-rationale");
  const reviewerEl  = document.getElementById("review-reviewer");
  const errBox      = document.getElementById("review-error");
  const cancelBtn   = document.getElementById("review-cancel");
  const submitBtn   = document.getElementById("review-submit");

  if (!modal) return;

  let currentId = null;
  let currentDecision = null;

  const NOTES = {
    confirmed:  "Confirm the model's prediction as-is. Provide your basis below.",
    overridden: "Override the model's class. Pick the corrected class and provide rationale.",
    resurvey:   "Mark this location as needing a fresh field survey before any decision.",
    geophysics: "Recommend a targeted geophysical investigation (e.g. electrical resistivity).",
  };

  document.querySelectorAll(".btn-review-decide").forEach((btn) => {
    btn.addEventListener("click", () => {
      currentId = btn.dataset.id;
      currentDecision = btn.dataset.decision;
      titleEl.textContent = `Survey #${currentId} — ${prettyDecision(currentDecision)}`;
      noteEl.textContent  = NOTES[currentDecision] || "";
      overrideRow.style.display = (currentDecision === "overridden") ? "block" : "none";
      rationaleEl.value = "";
      reviewerEl.value  = "";
      errBox.style.display = "none";
      modal.classList.add("open");
    });
  });

  cancelBtn.addEventListener("click", () => modal.classList.remove("open"));
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.classList.remove("open");
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modal.classList.contains("open")) {
      modal.classList.remove("open");
    }
  });

  submitBtn.addEventListener("click", async () => {
    const rationale = rationaleEl.value.trim();
    if (!rationale) {
      errBox.textContent = "Rationale is required.";
      errBox.style.display = "block";
      return;
    }
    submitBtn.disabled = true;
    submitBtn.textContent = "Submitting…";
    try {
      const body = {
        decision:  currentDecision,
        rationale: rationale,
        reviewer:  reviewerEl.value.trim(),
      };
      if (currentDecision === "overridden") {
        body.override_class = overrideClass.value;
      }
      const res = await fetch(`/api/locations/${currentId}/expert-review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Server returned ${res.status}`);
      }
      // Success — reload to refresh the queue.
      window.location.reload();
    } catch (e) {
      errBox.textContent = "Could not submit: " + e.message;
      errBox.style.display = "block";
      submitBtn.disabled = false;
      submitBtn.textContent = "Submit decision";
    }
  });

  function prettyDecision(d) {
    return ({ confirmed: "Confirm",
              overridden: "Override",
              resurvey: "Resurvey",
              geophysics: "Geophysics" })[d] || d;
  }
})();
