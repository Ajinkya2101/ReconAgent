// Exception worklist: the small amount of client behaviour htmx doesn't cover.
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const wide = () => window.matchMedia("(min-width: 1100px)").matches;
  const visible = (el) => !!el && el.getClientRects().length > 0;

  function markOpen(key) {
    const layout = $("#layout"), rows = $("#rows"), open = $("#open-key");
    if (!layout) return;
    $$(".row.is-current").forEach((r) => r.classList.remove("is-current"));
    $$(".row-link[aria-current]").forEach((a) => a.removeAttribute("aria-current"));
    if (key) {
      const row = $("#row-" + CSS.escape(key));
      row?.classList.add("is-current");
      $(".row-link", row || document.createElement("i"))?.setAttribute("aria-current", "true");
    }
    rows?.classList.toggle("has-open", !!key);
    layout.classList.toggle("has-detail", !!key);
    if (open) open.value = key || "";
  }

  function closeDetail() {
    const key = $("#open-key")?.value;
    markOpen("");
    const q = new URLSearchParams(location.search);
    q.delete("open");
    history.pushState({}, "", "/" + (q.toString() ? "?" + q : ""));
    // Back to where the supervisor left the queue.
    if (key) $("#row-" + CSS.escape(key) + " .row-link")?.focus({ preventScroll: false });
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
    // Marked read on the server for this person (hx-post); folded here at once.
    if (e.target.closest("[data-briefing-read]")) $("#briefing")?.classList.add("is-read");
    if (e.target.closest("[data-briefing-reopen]")) $("#briefing")?.classList.remove("is-read");
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

  // After an action the button that was pressed is gone (the detail re-renders), so remember
  // which flag it belonged to and put focus back on that flag's heading.
  let refocus = null;
  document.addEventListener("htmx:beforeRequest", (e) => {
    if (e.detail.target?.id !== "detail") return;   // the background poll must not clear it
    const flag = e.detail.elt.closest?.(".flag");
    refocus = flag ? flag.getAttribute("aria-labelledby") : null;
  });

  // New detail content: bring it to the top, and move focus into it at every width so the
  // keyboard and screen reader follow the supervisor from the queue to the decision.
  document.addEventListener("htmx:afterSwap", (e) => {
    if (e.detail.target.id !== "detail") return;
    const key = $(".detail", e.detail.target)?.dataset.key;
    if (e.detail.requestConfig?.verb === "get") {
      e.detail.target.scrollTop = 0;
      if (key) markOpen(key);
      $("#detail-h")?.focus({ preventScroll: true });
    } else {
      const target = (refocus && document.getElementById(refocus)) || $("#detail-h");
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ block: "nearest" });
    }
    refocus = null;
  });
  // A question that was answered clears the box for the next one.
  document.addEventListener("htmx:afterRequest", (e) => {
    if (e.detail.successful && e.detail.elt.matches?.("form.ask")) e.detail.elt.reset();
  });
  window.addEventListener("popstate", () => {
    const m = location.pathname.match(/^\/row\/(.+)$/);
    markOpen(m ? decodeURIComponent(m[1]) : "");
  });

  // The flag a shortcut acts on: the one holding focus, else the first one still open.
  function currentFlag() {
    const here = document.activeElement?.closest?.("#detail .flag:not(.is-decided)");
    return here || $("#detail .flag:not(.is-decided)");
  }
  function press(el) { if (el && !el.disabled) el.click(); return !!el; }
  function goTo(el) {
    if (!el) return false;
    el.scrollIntoView({ block: "center" });
    el.focus({ preventScroll: true });
    return true;
  }

  // Keyboard: / search, j/k move through rows, h acknowledge, c claim, a/r go to Accept or
  // Reject (Enter records it: a decision is never one stray keystroke), ? shortcuts, Esc closes.
  document.addEventListener("keydown", (e) => {
    const typing = e.target.closest("input, textarea, select, [contenteditable]");
    const keys = $("#keys");
    if (e.key === "Escape") {
      if (keys?.matches(":popover-open")) return;   // the popover closes itself
      if (!$("#assistant")?.hasAttribute("hidden")) return toggleAssistant(false);
      if ($("#layout")?.classList.contains("has-detail") && !typing) return closeDetail();
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    const detailOpen = !!$("#detail .detail");
    if (e.key === "/") { e.preventDefault(); $("#q")?.focus(); }
    else if (e.key === "?") { e.preventDefault(); keys?.togglePopover?.(); }
    else if (e.key === "j" || e.key === "k") {
      const links = $$(".row-link");
      if (!links.length) return;
      let i = links.indexOf(document.activeElement);
      if (i < 0) i = links.findIndex((a) => a.hasAttribute("aria-current"));
      const step = e.key === "j" ? 1 : -1;
      const next = i < 0 ? links[0] : links[Math.max(0, Math.min(links.length - 1, i + step))];
      next.focus();
      next.scrollIntoView({ block: "nearest" });
    }
    else if (e.key === "h" && detailOpen) { if (press($$("[data-ack-row]").find(visible))) e.preventDefault(); }
    else if (e.key === "c" && detailOpen) { if (press($("#detail [data-claim]"))) e.preventDefault(); }
    else if (e.key === "a" && detailOpen) { if (goTo($("[data-accept]", currentFlag() || document.createElement("i")))) e.preventDefault(); }
    else if (e.key === "r" && detailOpen) { if (goTo($("[data-reject]", currentFlag() || document.createElement("i")))) e.preventDefault(); }
  });

  // Server push: hear about changes the moment the feed moves. The poll on #changes stays as a fallback.
  const changes = $("#changes");
  if (changes && "EventSource" in window) {
    const since = new URL(changes.getAttribute("hx-get"), location.href).searchParams.get("since") || "0";
    const stream = new EventSource("/stream?since=" + since);
    stream.addEventListener("changed", () => document.body.dispatchEvent(new CustomEvent("recon:changed")));
  }
})();
