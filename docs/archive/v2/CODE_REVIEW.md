# Code Review — Reconciliation Agent

> **Superseded.** This review covers the v2 data model (the five client extracts). The agent now uses the business team's 9 Nov mapping: see [DATA_MODEL.md](../../data-model/DATA_MODEL.md).

**Scope:** the full working tree (`recon_agent/`, `tests/`, Docker/Compose/Caddy, CI), checked against the
specification in `ReconAgent.txt`. Method and checklist: [CODE_REVIEW_PROMPT.md](CODE_REVIEW_PROMPT.md).
**Test run:** `RECON_AI=off python -m unittest discover -s tests` → **112 tests, all pass** (≈23 s).
Every finding marked *verified* was reproduced with a script against a seeded event log, or with a direct call.

---

## 1. Summary

The core design is sound and matches the spec's main principles. Rules compute every quantity. The AI layer
only explains, and it cannot change accountability or priority. Every AI output is audited before it is
shown. Security basics are better than usual for a POC: PBKDF2 hashes, HMAC sessions that can be revoked,
an Origin/Sec-Fetch-Site CSRF guard, a strict CSP, autoescaped templates and a hardened container.

The most serious problems are in **state scoping and persistence**, not in the rule maths:

1. **Decisions, acknowledgments and SLA timers are keyed by flag ID with no sales date.** Yesterday's
   Accept, Reject or Acknowledge applies to today's flag. *(verified)*
2. **The simulator's `/oic/corrections` write endpoint has no authentication when `RECON_CORRECTIONS_TOKEN`
   is empty**, and empty is the default in `.env.example`.
3. **Claims, acknowledgments and High-flag timers exist only in memory.** A restart loses them, and an
   acknowledgment leaves no audit record.
4. **The headline "$ at risk" counts the same exposure twice when two rules fire on one SKU.** *(verified:
   $17,500 reported vs $16,782 de-duplicated on the seed data)*
5. **Ingest silently coerces bad values to zero or None** (for example `"1,200"` → 0). This produces wrong
   positions, or crashes later. *(verified)*

**Checked against the client sources:** the requirement brief, the challenges deck and the extracts in
`data/` (full detail in [CLIENT_ALIGNMENT_REVIEW.md](CLIENT_ALIGNMENT_REVIEW.md)). The brief requires the
R18/R19 conditions, so M6 is withdrawn. The client extracts are well typed, so M2 drops to a guard for
future extracts. Where `ReconAgent.txt` disagrees with the client sources, the client sources win.

---

## 2. Spec conformance matrix

| # | Spec item | Status | Evidence |
|---|---|---|---|
| 1 | Read 5 sources per sales day | ✅ Implemented, ⚠ no same-day check | [ingest.py:402-414](../../../recon_agent/ingest.py#L402-L414), see M3 |
| 2 | Net = Demand(all lines) − Inventory − Wrapped + Returns | ✅ Matches | [position.py:104-127](../../../recon_agent/position.py#L104-L127) (`usable_supply = inv + wrapped − returns`) |
| 2a | Sum all demand before applying inventory | ✅ | `demand_total` sums every line; R01 compares against first-line-only netting |
| 2b | Positive = make, negative = surplus to carry forward | ✅ | `net_requirement`; carry-forward shown in [store.py:374-381](../../../recon_agent/web/store.py#L374-L381) (display only) |
| 2c | Per SKU **and per sales day** | ⚠ Per SKU only | [position.py:200-232](../../../recon_agent/position.py#L200-L232) groups by SKU; one sales day per load assumed |
| 3 | 17 rules R01–R17, default severities | ✅ Present; ⚠ some quantity downgrades go below the spec default (the client gives no thresholds) | [detectors.py:20-40](../../../recon_agent/detectors.py#L20-L40), see L1 |
| 3a | R18/R19 not in current version | ✅ The client brief requires both conditions; they stay dormant on the extracts (no ticket or posting data). Reword `ReconAgent.txt` | [detectors.py:507-508](../../../recon_agent/detectors.py#L507-L508), see M6 |
| 4 | Priority = ($ at risk + base) × weight × confidence | ✅ Exact (our design; the client specifies no formula) | [detectors.py:84-87](../../../recon_agent/detectors.py#L84-L87) |
| 4a | Confidence starts at 1.0 | ✅ (Beta(1,1) normalised; range 0–2) | [feedback.py:50-53](../../../recon_agent/feedback.py#L50-L53) |
| 5 | Corrections, not just differences | ✅ Every rule's recommendation states an action | detectors.py |
| 6 | Accountability by rules, never by AI | ✅ | [accountability.py](../../../recon_agent/accountability.py); AI receives it as a fixed input |
| 7a | AI optional; agent correct without it | ✅ | [ai.py:294-303](../../../recon_agent/ai.py#L294-L303), [store.py:129](../../../recon_agent/web/store.py#L129) |
| 7b | Numbers in AI text checked | ⚠ Gaps (sign, "1.2k", words, ≤10) | [ai.py:220-263](../../../recon_agent/ai.py#L220-L263), see M4 |
| 7c | No audit write → no AI text | ✅ | `audit(strict=True)` raises; callers fall back to rule text |
| 8a | Accept/Reject/Change; Reject lowers confidence and suppresses same qty | ✅, ❌ not scoped to sales day | [feedback.py:32-67](../../../recon_agent/feedback.py#L32-L67), see H1 |
| 8b | High flag acknowledgment within 15 min, then overdue | ✅ logic, ❌ not durable | [store.py:397-404](../../../recon_agent/web/store.py#L397-L404), see H3 |
| 8c | Phase 1: Accept never changes source systems | ✅ Accept alone; ❌ "Approve correction" writes back | [store.py:204-217](../../../recon_agent/web/store.py#L204-L217), see M7 |
| 9 | Sign-in with name and role | ⚠ Differs: username and password, role from account (safer) | [web/app.py:159-175](../../../recon_agent/web/app.py#L159-L175), see L11 |
| 9a | Briefing "Mark read" persists per machine until new data | ✅ localStorage keyed by briefing id | [app.js:43-47](../../../recon_agent/web/static/app.js#L43-L47) |
| 9b | Search, tabs, j/k/Enter/Esc, assistant ≤ 500 chars, sign-out | ✅ | app.js, [web/app.py:421](../../../recon_agent/web/app.py#L421), `_assistant.html` maxlength |

---

## 3. Findings

Format: **ID · Title** — location · problem · failure scenario · fix.

### High

**H1 · Planner decisions, acknowledgments and first-seen times leak across sales days** *(verified)*
- **Where:** [detectors.py:64-66](../../../recon_agent/detectors.py#L64-L66) (`id = rule-key`, no date);
  [feedback.py:56-67](../../../recon_agent/feedback.py#L56-L67); [store.py:150](../../../recon_agent/web/store.py#L150),
  [store.py:400-404](../../../recon_agent/web/store.py#L400-L404), [store.py:442](../../../recon_agent/web/store.py#L442).
- **Problem:** A flag ID is `R01-150080`, whatever the sales day. The decisions in `feedback.json`, the
  in-memory `acks` and `first_seen` are all keyed by that ID.
- **Failure scenarios (reproduced):**
  - A planner accepts `R01-150080` on Monday. On Tuesday a *new* R01 for 150080 (900 units, not 500) opens
    already marked **"Accepted – awaiting correction"**, and nobody reviews it.
  - A planner rejects `R14-F1-L1` (units are always 0 for R14). Every later R14 on that formula and line is
    **suppressed permanently**, even when the batch gap grows from 2 to 20. R03, R04, R11 and R15 also use
    units = 0, so they behave the same way.
  - The server runs across two days in Excel mode. Today's High flag inherits yesterday's `first_seen` and
    shows **overdue at once**, or it inherits yesterday's acknowledgment and shows as **acked**.
- **Fix:** Put the sales date in the decision key, for example `f"{sales_date}:{f.id}"`, or put the date in
  `Finding.key`. For suppression, compare a fingerprint of the evidence (units *and* the key evidence
  values), not units alone. Reset `first_seen` and `acks` when the sales day changes. Keep the rule-level
  confidence counters global; that part is correct.

**H2 · Unauthenticated source-system write endpoint when no token is set**
- **Where:** [simulator/app.py:521-531](../../../recon_agent/simulator/app.py#L521-L531); `.env.example`
  (`RECON_CORRECTIONS_TOKEN=` empty).
- **Problem:** `if config.CORRECTIONS_TOKEN and not same_secret(...)` skips the check when the token is
  empty. The endpoint applies an arbitrary `changes` dict to any entity row, and the CSRF middleware allows
  requests that carry no Origin header (curl).
- **Failure scenario:** The simulator runs with `simulate --host 0.0.0.0` on the shared demo VM, so anyone
  who can reach port 8100 can rewrite MIPS, WMS or Oracle rows in the event log, and the worklist then
  reconciles against them. Compose binds the port to 127.0.0.1, which reduces the risk, but the CLI default
  path does not.
- **Fix:** Fail closed. Refuse `/oic/corrections` when no token is configured, unless the request comes
  from loopback. Alternatively, generate a token on first start and share it through the output volume.
  Also log a startup warning when `--host` is not loopback and no token is set.

**H3 · Claims, acknowledgments and SLA timers are in memory only**
- **Where:** [store.py:97-99](../../../recon_agent/web/store.py#L97-L99) (`claims`, `acks`, `first_seen`).
- **Problem:** Decisions persist to `feedback.json`, but claims and acknowledgments do not, and no audit
  record is kept for them. The spec says the agent records the user's name on "each claim, acknowledgment
  and decision", and that overdue High flags go to the productivity manager.
- **Failure scenario:** The container restarts mid-shift (a deploy, an OOM, the healthcheck). Every
  acknowledgment is lost, every 15-minute timer restarts from the reload time, and overdue flags look
  "due" again. Afterwards nobody can show who acknowledged what, or when.
- **Fix:** Persist `claims`, `acks` and `first_seen` the way `feedback.json` is persisted (atomic
  `write_json`), or move all worklist state to SQLite. Append each action to an audit log, as is already
  done for AI calls. Single-process mode is documented ([__main__.py:80](../../../recon_agent/__main__.py#L80)),
  but persisting this state is also what multi-worker mode would need later.

### Medium

**M1 · "$ at risk" counts overlapping rules twice** *(verified)*
- **Where:** [engine.py:42-43](../../../recon_agent/engine.py#L42-L43).
- **Problem:** `dollar_exposure` sums `f.dollars` over every flag of eight rules. When R09 and R10 fire on
  the same SKU, the same shortage is counted twice. [store.py:69-72](../../../recon_agent/web/store.py#L69-L72)
  already says that summing a SKU's flags "would count overlapping rules twice", and uses the maximum for
  row dollars. The KPI does not.
- **Failure scenario:** On the seed data the briefing, the day tile and the offline narration report
  **$17,500**. The figure without double counting (the largest flag per SKU) is **$16,782**. The AI briefing
  quotes the inflated number as fact.
- **Fix:** Aggregate per SKU first (the maximum, or a rule-precedence order), then sum. Use the same helper
  in the store and in the engine.

**M2 · Ingest silently zeroes or drops malformed values, then crashes later** *(verified)*. *Client data
check:* every number and date in the five client extracts is properly typed, so this does not affect
today's data. It stays as a guard for future extracts and can be treated as Low.
- **Where:** [ingest.py:30-39](../../../recon_agent/ingest.py#L30-L39).
- **Problem:** `num("1,200") → 0.0` and `as_date("2026-09-12") → None`. A text-formatted Excel cell, which
  is common in extracts, becomes zero demand or zero wrapped units without any warning. A `None` date then
  causes `AttributeError` in `compute_kpis` (`sales_date.isoformat()`,
  [engine.py:28-33](../../../recon_agent/engine.py#L28-L33)) or `TypeError` when `propose_allocation` sorts
  ([position.py:179](../../../recon_agent/position.py#L179)).
- **Failure scenario:** One MIPS line has `Order Quantity` stored as text. Its demand reads as 0, R07 and R09
  raise false flags with real dollar values, and planners act on them.
- **Fix:** Parse numeric strings (strip, remove thousands separators) and ISO date strings. Collect every
  value that cannot be parsed into a data-quality finding (R15 or a new ingest flag) instead of coercing it.
  Validate required columns per extract and raise a clear error for a missing header row (`_table` raises a
  bare `StopIteration` today, [ingest.py:217](../../../recon_agent/ingest.py#L217)).

**M3 · No check that the five extracts are for the same sales day; file choice is fragile**
- **Where:** [ingest.py:200-204](../../../recon_agent/ingest.py#L200-L204) (latest by *name*);
  [sources.py:24-26](../../../recon_agent/sources.py#L24-L26) (change detection by *mtime*).
- **Problem:** The loader takes the file that sorts last by name for each pattern. Nothing compares the
  `Schedule Date`, `SalesDate` and `Sales Date` across the five files.
- **Failure scenario:** The WMS export for Tuesday lands before the MIPS export. The agent reconciles
  Monday's MIPS against Tuesday's WMS, and R09, R10 and R13 fire on every SKU. Lexical sorting of names such
  as `… 9-12.xlsx` and `… 10-01.xlsx` can also pick the older file.
- **Fix:** Read the sales date from each extract and refuse, or flag, a mixed set. Choose files by that
  sales date, not by name.

**M4 · Gaps in the AI figure check** *(verified)*
- **Where:** [ai.py:220-263](../../../recon_agent/ai.py#L220-L263), [ai.py:519](../../../recon_agent/ai.py#L519).
- **Problem:**
  - `abs()` is applied to both sides, so "reduce by **−1,200**" passes when the engine said +1,200.
  - "1.2k" and "twelve hundred" are never checked.
  - Integers ≤ 10 are skipped.
  - In `ask`, figures from the *user's question* count as "known", so the question "is it 99,999?" turns
    99,999 into a verified number.
- **Fix:** Keep the sign where the text has one, and match direction words ("more", "less", "short") against
  the sign of the units. Flag any k/M suffix or spelled-out number as unverified. Remove the question from
  the known set, or show its figures as "from your question".

**M5 · Any signed-in user can reset the simulation**
- **Where:** [web/app.py:311-320](../../../recon_agent/web/app.py#L311-L320) (`require_user`, not a role check);
  [store.py:311-330](../../../recon_agent/web/store.py#L311-L330).
- **Problem:** A reset deletes the event log and **all planner decisions** (`feedback.save({...})`),
  claims and acknowledgments. A supervisor can do this by mistake in the middle of a demo.
- **Fix:** Allow only the manager role, and ask for confirmation in the UI. The simulator's `/reset`
  ([simulator/app.py:409-414](../../../recon_agent/simulator/app.py#L409-L414)) needs the same check.

**M6 · R18/R19 implemented and active, against the spec** — *withdrawn after checking the client
documents.* The client brief requires both conditions: scenario 5 (a return does not reduce the received
quantity) and scenario 8 (a WMS transaction missing in Oracle), and both appear in its detection list. The
rules correctly stay silent on the extracts, which have no ticket or posting data. Reword
`ReconAgent.txt`, and fix the AI tool description that says "R01-R17". The original text follows for the
record.
- **Where:** [detectors.py:38-39](../../../recon_agent/detectors.py#L38-L39), [detectors.py:507-508](../../../recon_agent/detectors.py#L507-L508);
  R19 counted in `dollar_exposure` ([engine.py:43](../../../recon_agent/engine.py#L43)); the tool description
  says "R01-R17" ([ai.py:418](../../../recon_agent/ai.py#L418)).
- **Problem:** The spec says R18 and R19 "are not in the current version". They fire whenever the event
  feed carries tickets and transactions.
- **Fix:** Decide which way to go. Either put the two rules behind a config flag (`RECON_REALTIME_RULES`,
  off by default) or update the spec and user guide. Make the AI tool description match the decision.

**M7 · Write-back goes beyond the Phase 1 "propose only" principle**
- **Where:** [corrections.py](../../../recon_agent/corrections.py), [store.py:204-217](../../../recon_agent/web/store.py#L204-L217),
  [web/app.py:292-299](../../../recon_agent/web/app.py#L292-L299).
- **Problem:** "Approve correction" accepts the flag *and* sends a change to the source system. It is
  limited to event mode and to planners and managers, and it is well built: optimistic `expected` values
  and confirmation through the feed. It still contradicts "Accept does not change MIPS, WMS or Oracle".
- **Fix:** Put it behind an explicit feature flag that is off by default, label it "Phase 2 preview" in the
  UI, and record the scope change in the spec.
- **Client baseline:** the brief asks the agent to *recommend* corrections ("identifies mismatches,
  recommends corrections"). It never asks the agent to change MIPS, WMS or Oracle, so write-back has no
  client requirement behind it. Keep it off unless the client asks for it.

**M8 · The assistant can block a worker thread for a very long time**
- **Where:** [ai.py:141-147](../../../recon_agent/ai.py#L141-L147) (`DEFAULT_RETRY_STRATEGY`, 300 s read
  timeout), [ai.py:494-514](../../../recon_agent/ai.py#L494-L514) (up to 6 turns),
  [web/app.py:327-330](../../../recon_agent/web/app.py#L327-L330) (sync handler).
- **Problem:** Each turn can retry several times with a 300 s timeout. A slow OCI region can hold one
  `/ask` request for tens of minutes, and a few of them exhaust the threadpool that serves the whole
  worklist.
- **Fix:** Give interactive calls a shorter timeout and no retries or one retry, and set an overall deadline
  for `ask_audited` (for example 60 s). Keep the long timeout for background narration only.

**M9 · An unhandled tool-argument error turns into a 500 with no audit entry**
- **Where:** [ai.py:465](../../../recon_agent/ai.py#L465) (`int(args.get("limit") or 20)`), and only `AIError`
  is caught at [ai.py:515](../../../recon_agent/ai.py#L515).
- **Failure scenario:** The model sends `{"limit": "ten"}`. `ValueError` propagates, `/ask` returns 500,
  and nothing reaches `ai_audit.jsonl`. A negative limit also slices the wrong end of the list.
- **Fix:** Validate tool arguments (clamp `limit` to 1–50), wrap `run_tool` so that it returns
  `{"error": ...}` to the model, and catch `Exception` in the audit wrapper.

### Low

**L1 · Quantity-based severity drops below the spec defaults** *(verified)*. The client sources give no
severities or thresholds, so these are our proposals and the client must confirm them
([CLIENT_ALIGNMENT_REVIEW.md §4](CLIENT_ALIGNMENT_REVIEW.md)). R01 is High in the spec but
becomes Medium below 1,000 units ([detectors.py:123](../../../recon_agent/detectors.py#L123)). R06 is Medium in
the spec but becomes **Low** below 1,000 units ([detectors.py:198](../../../recon_agent/detectors.py#L198)); on the
seed data, `R06-900108` (400 units) is Low. The spec gives quantity scaling only for R05. Confirm each
threshold with the business and record it in the spec.

**L2 · Magic thresholds sit in the rule code.** Examples: 20 units (R07), 500 (R07, R19) and 1,000 (R01,
R05, R06), at [detectors.py:123,180,198,204,221,493](../../../recon_agent/detectors.py#L123). Move them to
`config.py` with the other detection thresholds so they can be tuned and audited.

**L3 · Invented or inconsistent prices.** A SKU with no MIPS retail price silently gets $3.99
([position.py:228](../../../recon_agent/position.py#L228)), and R13 always uses that default instead of the item
price ([detectors.py:368,379](../../../recon_agent/detectors.py#L368)). Both change the priority. Mark
`price_source: default` in the evidence, and use the SKU price when one is known.

**L4 · Possible ID collisions.** R14 and R17 `PIECEVAR` IDs are `formula-line` with no shift or date
([detectors.py:393,404](../../../recon_agent/detectors.py#L393)). If the same formula runs twice on one line,
`store.find` ([store.py:390-395](../../../recon_agent/web/store.py#L390-L395)) returns the first match, so a
decision on the second flag lands on the first. This does not occur in the seed data (verified: no
duplicates), but it is plausible in real extracts. Add the shift or wrap-start time to the key.

**L5 · A claim can be taken over without notice.** `Store.claim` overwrites another user's claim
([store.py:423-428](../../../recon_agent/web/store.py#L423-L428)). Refuse the claim, or record the takeover
explicitly.

**L6 · Failed sign-ins can lock out a real user.** Five failed attempts against a known username block that
user for 15 minutes ([web/app.py:163](../../../recon_agent/web/app.py#L163)). Use a backoff, or count failures per
(username, address) pair.

**L7 · A contract-version change wipes the event log.** On a version mismatch the log drops every table
([events/log.py:179-183](../../../recon_agent/events/log.py#L179-L183)). This is acceptable for simulated data,
but rename the old file to a backup instead of dropping it.

**L8 · The `commits` table grows without limit.** One row is added per reload
([events/log.py:480-484](../../../recon_agent/events/log.py#L480-L484)), and `latencies()` uses a correlated
subquery over it. Prune it, or keep only the latest commit per consumer plus a bounded history.

**L9 · Race in `/oic/corrections`.** The `expected` check
([simulator/app.py:550-556](../../../recon_agent/simulator/app.py#L550-L556)) runs outside the
`publish` write transaction, so a concurrent edit between the check and the publish is overwritten. Move
the comparison into `publish_many` under `BEGIN IMMEDIATE`.

**L10 · CLI decisions have no author.** `feedback.record(...)` is called without `by=`
([__main__.py:64](../../../recon_agent/__main__.py#L64), [__main__.py:75](../../../recon_agent/__main__.py#L75)),
so decisions from the CLI or a workbook carry no name. Add a `--by` argument and make it required. The CLI
and the server also do separate read-modify-write cycles on `feedback.json` with no lock shared between
processes, so a decision can be lost.

**L11 · The sign-in flow differs from the spec.** The spec describes a name and role picker ("Who's on
shift?"). The code uses accounts with passwords, and the role comes from the account. The code is the better
design, so update `ReconAgent.txt` and `docs/guides/USER_GUIDE.md`.

### Nits

- [ingest.py:381](../../../recon_agent/ingest.py#L381): one `elif` line has a long run of spaces in the middle,
  which looks like a lost line continuation. Reformat it.
- [detectors.py:5-6](../../../recon_agent/detectors.py#L5-L6): the docstring says the AI layer "explains and
  prioritises" the findings. The AI does not rank anything; fix the wording so it does not mislead later
  maintainers.
- [feedback.py:50-53](../../../recon_agent/feedback.py#L50-L53): confidence ranges from 0 to 2, with 1.0 as
  neutral. Document this; "confidence" reads as a probability.
- [ai.py:445](../../../recon_agent/ai.py#L445) and [web/app.py:336](../../../recon_agent/web/app.py#L336) use
  `.strip().lstrip("0")` instead of `ingest.norm_sku`. Use one normaliser everywhere.
- [web/app.py:360](../../../recon_agent/web/app.py#L360): `await request.json()` with a non-object body raises
  `AttributeError`, which returns 500. Validate the body with a Pydantic model.

---

## 4. Test gaps

The suite is strong on event-log semantics, scenarios (each scenario raises exactly its flags), security
headers and CSRF, write-back and accountability. Missing:

1. **Excel path in CI.** The tests that need client extracts are skipped in CI
   ([ci.yml](../../../.github/workflows/ci.yml)), so the *production* ingest path is untested on every PR. Add a
   small synthetic `.xlsx` fixture set built with openpyxl inside the tests.
2. **Decision scoping across sales days** (H1): accept, reject and ack on day 1, then load day 2.
3. **KPI aggregation** (M1): no test covers `dollar_exposure`.
4. **Ingest of malformed values** (M2): text numbers, text dates, a missing header row.
5. **AI check edge cases** (M4): sign, "1.2k", question figures. The current tests exercise the success
   path.
6. **Persistence across a restart** (H3): build a Store, acknowledge a flag, build a new Store, assert the
   acknowledgment survives.
7. **Role checks** on `/simulation/reset` and on `/oic/corrections` without a token (H2, M5).
8. **Severity thresholds per rule** (L1): a table-driven test locks the business decision in place.

---

## 5. What to keep

- The split between rules and AI is strict. Accountability is computed before narration and passed to the
  model as fixed input.
- Audit before display (`strict=True`), with fallback to rule text on any `AIError`.
- The projector is idempotent: duplicate events and stale images are ignored, and deletes cannot come back.
  The event log is append-only, enforced by triggers.
- Write-back uses optimistic concurrency (`expected` values) and counts a correction as confirmed only when
  it comes back through the feed.
- Security: PBKDF2 at 600k iterations, constant-time compares, a session counter for revocation, `safe_next`
  for redirects, a CSP with no inline scripts, no `|safe` in any template, and atomic JSON writes. The
  container runs as non-root and read-only, with `cap_drop: ALL`.
- Dependencies are pinned exactly, and CI runs the tests and builds the image.

---

## 6. Recommended next steps (in order)

1. **H1:** add the sales date to decision, ack and first-seen keys, and match suppression on an evidence
   fingerprint. Add tests.
2. **H2:** make `/oic/corrections` fail closed without a token.
3. **H3:** persist claims, acks and `first_seen`, and add an action audit log.
4. **M1 and M2:** de-duplicate the KPI; parse and validate ingest values, with a data-quality flag.
5. **M3:** check that all five extracts are for the same sales day.
6. **M5 to M7:** role-gate the resets; put R18/R19 and write-back behind flags, or update the spec.
7. **M4, M8, M9:** tighten the AI checks and the timeouts, and validate tool arguments.
8. Add a synthetic Excel fixture so CI covers the production ingest path.
9. Clear the Low findings and nits as cleanup.
