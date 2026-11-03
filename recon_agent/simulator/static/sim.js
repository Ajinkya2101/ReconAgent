// Source simulator: the little client behaviour htmx doesn't cover.
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];

  // The plant clock ticks between server refreshes, at the clock's own rate.
  function tick() {
    const c = $("#clock"), out = $("[data-clock-now]");
    if (!c || !out) return;
    if (!c._t0) { c._t0 = performance.now(); c._sim = Number(c.dataset.sim); }
    if (c.dataset.paused === "true") return;
    const t = new Date(c._sim + (performance.now() - c._t0) * Number(c.dataset.rate));
    const p = (n) => String(n).padStart(2, "0");
    const day = t.toLocaleDateString("en-US", { weekday: "short" });
    out.textContent = `${day} ${p(t.getMonth() + 1)}/${p(t.getDate())} ${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`;
  }
  setInterval(tick, 250);

  // The row open in the editor is marked in the table, also after the table is refreshed.
  function markCurrent() {
    const key = $(".editor")?.dataset.key || "";
    $$(".grid tbody tr").forEach((tr) => tr.classList.toggle("is-current", !!key && tr.dataset.key === key));
  }

  // Derived columns are read-only while the source system computes them.
  document.addEventListener("change", (e) => {
    if (!e.target.matches("[data-derive]")) return;
    $$("[data-derived]", e.target.form).forEach((i) => { i.readOnly = e.target.checked; });
  });

  document.addEventListener("htmx:afterSettle", markCurrent);
  document.addEventListener("DOMContentLoaded", markCurrent);
})();
