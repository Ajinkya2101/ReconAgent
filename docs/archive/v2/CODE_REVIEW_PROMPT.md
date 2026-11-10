# Code Review Prompt — Reconciliation Agent

## Role
You are a senior reviewer with backend Python (FastAPI/Starlette/Jinja/HTMX), data-reconciliation,
event-sourcing, and LLM-integration experience. You are reviewing the Reconciliation Agent as it
stands in the working tree. Judge it against the specification in `ReconAgent.txt`, not against
its own docs or commit history. Do not take a comment's or a docstring's word for what code does:
read the code.

## What the system must do (spec contract to verify)
1. **Inputs**: 5 sources per sales day — MIPS Order Data, ODI Finished Goods, ODI Batch, WMS Fill
   Rate Report, WMS Order Summary by Item.
2. **Reconciled position** per (SKU, sales day):
   `Net = Total demand (ALL MIPS lines) − Inventory applied − Completed production (wrapped, all runs)
   + Returns/adjustments`. Positive = still to make; negative = surplus carried to next sales day.
   Demand must be summed across all lines *before* inventory is applied.
3. **17 rules R01–R17** with the stated default severities, and quantity-scaled severity
   (e.g. R05 High ≥ 1,000 units else Medium). R18/R19 must be absent.
4. **Priority** = (retail value at risk + base value) × severity weight (H3/M2/L1) × rule confidence;
   confidence starts at 1.0; base value gives $0 flags (e.g. R12) a place in the queue.
5. **Corrections**, not just differences, naming the source system to fix.
6. **Accountability** computed by rules (never by AI): impact, started-in system, failure mode,
   attribution (Certain/Likely/Undetermined), per-system role.
7. **AI layer** (Gemini 2.5 Pro on OCI GenAI): optional; agent works identically without it.
   Every number in AI text is checked against engine numbers; unmatched numbers flagged
   "not in the engine's data". Each AI output has an audit number in `ai_audit.jsonl`;
   **if the audit write fails, the AI text must not be shown**.
8. **Human-in-the-loop**: Phase 1 only proposes. Accept/Reject/Change decision; Reject lowers rule
   confidence and suppresses the same flag if quantity is unchanged. Claims, acknowledgements
   (High flags within 15 min, then overdue), notes for next shift, carry-forward for R05/R06/R08.
   Every action records the signed-in user's name and role.
9. **UI flows**: sign-in (name + role), briefing (mark read persists per machine until new data),
   filters/tabs, search, j/k/Enter/Esc, assistant (≤ 500 chars), sign-out.

## Review dimensions (in priority order)
1. **Correctness vs. spec** — does each formula, rule, threshold, severity, and priority computation
   match items 2–4? Off-by-one in date/sales-day logic, sign errors, unit/container conversions,
   rounding, double counting across lines/runs/reports, SKU normalisation (R15).
2. **Data integrity & state** — event log / projector consistency, idempotency, concurrent writes
   (two users deciding the same flag), file-based persistence atomicity, what happens on restart,
   decision/feedback persistence keyed correctly so suppression is right.
3. **Security** — authN/authZ on every mutating route (sign-in is name-only: what stops forging
   another user or role?), CSRF on HTMX POSTs, session/cookie flags, input validation, template
   autoescaping / XSS (especially AI text and user notes rendered into HTML), path traversal in
   uploads/ingest, secrets handling (.env, OCI keys), prompt injection via source data or notes
   reaching the LLM, DoS via unbounded inputs.
4. **AI safety guarantees** — numeric check robustness (formats like "1,200", "1.2k", percentages,
   dates), audit-before-display ordering, graceful fallback, timeouts, no AI influence on
   accountability or priority.
5. **Reliability & ops** — error handling, timeouts, logging, config validation, Docker/Compose/Caddy
   setup, CI coverage, pinned dependencies (verify pins exist and are sane).
6. **Testing** — what the spec requires that tests do not cover; tests that assert implementation
   rather than behaviour.
7. **Maintainability** — module boundaries, duplication, dead code, magic numbers that should be
   config, naming, complexity hot spots.
8. **Frontend** — HTMX swaps, keyboard handling, accessibility basics, an empty/broken asset.

## Method
1. Read `ReconAgent.txt` (done) and skim README/TECHNICAL_OVERVIEW only to find entry points.
2. Read every Python module in `recon_agent/` fully; read templates and JS where they render user/AI
   text or post state changes.
3. For each spec item, trace it to code and mark Implemented / Deviates / Missing.
4. Run the test suite; record result.
5. For every suspected defect, verify by re-reading the exact lines (and, where cheap, by running a
   snippet). Drop anything not verified, or label it clearly as a risk/question.

## Output format
- **Summary**: overall verdict, top 5 issues.
- **Spec conformance matrix**: spec item → status → evidence (file:line).
- **Findings**, grouped by severity — Critical / High / Medium / Low / Nit. Each finding:
  `ID · Title · file:line · What is wrong · Concrete failure scenario · Recommended fix`.
- **Test gaps**.
- **Positive observations** (brief — what to keep).
- **Recommended next steps**, ordered.

Rules for the reviewer: cite file:line for every claim; no speculative findings presented as fact;
prefer fewer, verified findings over many guesses; be direct.
