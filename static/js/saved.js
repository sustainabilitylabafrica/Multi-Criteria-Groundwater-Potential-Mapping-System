/* ============================================================================
   Saved Locations page
   --------------------------------------------------------------------------
   Two interactions:
     1. Delete-row buttons on each survey
     2. History Report modal — pick a date range, redirect to the report
   ============================================================================ */

(() => {
  // --------- Delete buttons ----------------------------------------------
  document.querySelectorAll(".btn-delete").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      if (!id) return;
      if (!confirm(`Delete survey #${id}? This cannot be undone.`)) return;

      btn.disabled = true;
      try {
        const res = await fetch(`/api/locations/${id}`, { method: "DELETE" });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.error || `Server returned ${res.status}`);
        }
        const row = document.querySelector(`tr[data-id="${id}"]`);
        if (row) row.remove();
      } catch (e) {
        alert("Could not delete: " + e.message);
        btn.disabled = false;
      }
    });
  });

  // --------- History Report modal ----------------------------------------
  const modal      = document.getElementById("history-modal");
  const btnOpen    = document.getElementById("btn-history");
  const btnCancel  = document.getElementById("btn-history-cancel");
  const btnGo      = document.getElementById("btn-history-go");
  const inputStart = document.getElementById("hist-start");
  const inputEnd   = document.getElementById("hist-end");
  const errBox     = document.getElementById("history-error");

  if (!modal || !btnOpen) return;

  function openModal() {
    // Default range: last 30 days, ending today
    const today = new Date();
    const thirtyDaysAgo = new Date(today.getTime() - 30 * 24 * 60 * 60 * 1000);
    inputStart.value = isoDate(thirtyDaysAgo);
    inputEnd.value   = isoDate(today);
    errBox.style.display = "none";
    modal.classList.add("open");
  }
  function closeModal() {
    modal.classList.remove("open");
  }
  function isoDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  btnOpen.addEventListener("click", openModal);
  btnCancel.addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();   // click backdrop to close
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modal.classList.contains("open")) closeModal();
  });

  btnGo.addEventListener("click", () => {
    const start = inputStart.value;
    const end   = inputEnd.value;
    if (!start || !end) {
      errBox.textContent = "Please pick both dates.";
      errBox.style.display = "block";
      return;
    }
    if (end < start) {
      errBox.textContent = "End date must be on or after start date.";
      errBox.style.display = "block";
      return;
    }
    const url = `/history-report?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
    window.location.href = url;
  });
})();
