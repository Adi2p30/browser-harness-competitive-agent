// Table zoom on Knowledge pages: scales the comparison tables (text, padding and column widths are all
// sized in em of --okf-zoom), remembered per browser. Buttons in .zoom-bar, or the - / + / 0 keys.
(() => {
  const body = document.querySelector(".okf-body");
  const bar = document.querySelector(".zoom-bar");
  if (!body || !bar) return;
  const KEY = "okf-zoom", MIN = 0.6, MAX = 1.6, STEP = 0.1;
  const out = bar.querySelector("output");
  const zoomOut = bar.querySelector('[data-zoom="out"]');
  const zoomIn = bar.querySelector('[data-zoom="in"]');
  let zoom = 1;
  try { zoom = parseFloat(localStorage.getItem(KEY)) || 1; } catch (e) { /* storage blocked: start at 100% */ }

  function set(value) {
    zoom = Math.min(MAX, Math.max(MIN, Math.round(value * 10) / 10));
    body.style.setProperty("--okf-zoom", zoom);
    out.textContent = Math.round(zoom * 100) + "%";
    zoomOut.disabled = zoom <= MIN;
    zoomIn.disabled = zoom >= MAX;
    try { localStorage.setItem(KEY, String(zoom)); } catch (e) { /* not remembered, still works */ }
  }

  bar.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-zoom]");
    if (!b) return;
    set(b.dataset.zoom === "in" ? zoom + STEP : b.dataset.zoom === "out" ? zoom - STEP : 1);
  });
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey || /^(input|textarea|select)$/i.test(e.target.tagName)) return;
    if (e.key === "-" || e.key === "_") set(zoom - STEP);
    else if (e.key === "=" || e.key === "+") set(zoom + STEP);
    else if (e.key === "0") set(1);
    else return;
    e.preventDefault();
  });
  set(zoom);
})();
