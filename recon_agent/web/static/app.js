// Exception worklist: the small amount of client behaviour htmx doesn't cover.
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const wide = () => window.matchMedia("(min-width: 1100px)").matches;

  function markOpen(key) {
    const layout = $("#layout"), rows = $("#rows"), open = $("#open-key");
    if (!layout) return;
    $$(".row.is-current").forEach((r) => r.classList.remove("is-current"));
    if (key) $("#row-" + CSS.escape(key))?.classList.add("is-current");
    rows?.classList.toggle("has-open", !!key);
    layout.classList.toggle("has-detail", !!key);
    if (open) open.value = key || "";
  }

  function closeDetail() {
    markOpen("");
    const q = new URLSearchParams(location.search);
    q.delete("open");
    history.pushState({}, "", "/" + (q.toString() ? "?" + q : ""));
  }

  // Opening a row (queue, briefing actions, assistant answers).
  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-open-row]");
    if (opener) {
      markOpen(opener.dataset.openRow);
      if (!wide()) $("#assistant")?.setAttribute("hidden", "");
    }
    if (e.target.closest("[data-close-detail]")) {
      e.preventDefault();
      closeDetail();
    }
    const asker = e.target.closest("[data-ask]");
    if (asker) {
      const box = $("#question");
      box.value = asker.dataset.ask;
      box.form.requestSubmit();
    }
    if (e.target.closest("[data-assistant]")) toggleAssistant();
    if (e.target.closest("[data-assistant-close]")) toggleAssistant(false);
    if (e.target.closest("[data-briefing-read]")) {
      const b = $("#briefing");
      try { localStorage.setItem("briefing-read", b.dataset.briefingId); } catch {}
      b.classList.add("is-read");
    }
    if (e.target.closest("[data-briefing-reopen]")) $("#briefing").classList.remove("is-read");
  });

  function toggleAssistant(force) {
    const panel = $("#assistant"), btn = $("[data-assistant]");
    if (!panel) return;
    const show = force ?? panel.hasAttribute("hidden");
    panel.toggleAttribute("hidden", !show);
    btn?.setAttribute("aria-expanded", String(show));
    if (show) $("#question")?.focus();
    else btn?.focus();
  }

  // New detail content: bring it to the top and move focus there on small screens.
  document.addEventListener("htmx:afterSwap", (e) => {
    if (e.detail.target.id === "detail") {
      const key = $(".detail", e.detail.target)?.dataset.key;
      if (e.detail.requestConfig?.verb === "get") {
        e.detail.target.scrollTop = 0;
        if (key) markOpen(key);
        if (!wide()) e.detail.target.focus({ preventScroll: true });
      }
    }
  });
  window.addEventListener("popstate", () => {
    const m = location.pathname.match(/^\/row\/(.+)$/);
    markOpen(m ? decodeURIComponent(m[1]) : "");
  });

  // Keyboard: / search, j/k move through rows, Esc closes.
  document.addEventListener("keydown", (e) => {
    const typing = e.target.closest("input, textarea, select, [contenteditable]");
    if (e.key === "Escape") {
      if (!$("#assistant")?.hasAttribute("hidden")) return toggleAssistant(false);
      if ($("#layout")?.classList.contains("has-detail")) return closeDetail();
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "/") { e.preventDefault(); $("#q")?.focus(); }
    if (e.key === "j" || e.key === "k") {
      const links = $$(".row-link");
      if (!links.length) return;
      const i = links.indexOf(document.activeElement);
      const next = links[Math.max(0, Math.min(links.length - 1, i + (e.key === "j" ? 1 : -1)))] || links[0];
      next.focus();
      next.scrollIntoView({ block: "nearest" });
    }
  });

  // Server push: hear about changes the moment the feed moves. The poll on #changes stays as a fallback.
  const changes = $("#changes");
  if (changes && "EventSource" in window) {
    const since = new URL(changes.getAttribute("hx-get"), location.href).searchParams.get("since") || "0";
    const stream = new EventSource("/stream?since=" + since);
    stream.addEventListener("changed", () => document.body.dispatchEvent(new CustomEvent("recon:changed")));
  }

  // A briefing already marked read for this run stays folded away.
  try {
    const b = $("#briefing");
    if (b && localStorage.getItem("briefing-read") === b.dataset.briefingId) b.classList.add("is-read");
  } catch {}
})();
