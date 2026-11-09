# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/). The version lives in
`recon_agent/__init__.py`; release by tagging the commit `vX.Y.Z` (see docs/DEPLOYMENT.md).

## [Unreleased]

### Changed (alignment with the client requirement brief and extracts)
- **Valid demand only.** MIPS lines marked `Closed` are no longer counted as demand. They stay on
  the position as `closed_lines` (workbook column "Closed Lines (excluded)"). On the event feed,
  closing a line after its run was ordered reads as demand removed, not as inventory. Every line in
  the 12 Sep 2026 extract is open, so its results do not change.
- **R04 finds every kind of run move** named in scenario 3: to another line, another production day,
  or another run slot on the same line. The flag names the kind of move (`moved_to_another`
  evidence), and so does its accountability record.
- **R09 uses the WMS report's own evidence of an order change** (scenario 6). When
  `Original Ord Qty` ≠ `Order Qty` and MIPS still plans the original total, the flag lists the
  changed customer orders and says the change never reached MIPS, with attribution *certain*. On
  the client extract this applies to SKUs 76108, 950507 and 951008.
- **R02's correction states the resulting requirement**, as in the client's example ("Apply the 1,200
  units to the active sales-order lines… production requirement at 800 units"). It also has a
  `requirement_after_inventory` evidence value.
- **R19 is tagged "Returns & Adjustments"**, one of the client's challenge areas, instead of
  "Warehouse Traceability".

### Added
- **System accountability on every flag** (`recon_agent/accountability.py`). The rules now say
  which system misfired, not only that the systems disagree: the impact (over-production,
  under-production, stranded surplus, short shipment, overstated inventory, traceability gap,
  financial posting gap, data integrity), the system or interface where it started, the failure
  type and step, how certain the attribution is, and the role of MIPS, ODI, WMS and (where
  involved) Oracle: Misfired, Affected, Reference, Conflicting, To check or Not involved. Each role
  quotes the engine's figures. It is computed from the rule's own values, so it is shown with or
  without the AI. KPIs total the flags `by_origin` and `by_impact`. The expanded briefing shows
  "Where today's flags started", and the workbook has an Accountability sheet and per-system
  columns.
- **More detailed, auditable AI explanations.** The model gets each flag's accountability record
  and returns, besides a longer explanation, a cause chain (one system per step, in time order), the
  impact, the record that would confirm the attribution, the evidence keys it used and linked flags.
  It cannot change the attribution.
  - Every figure the model writes is checked against the engine's numbers. Unmatched figures are
    listed on the flag as unverified. Unknown evidence keys, flag links and systems are removed and
    counted, and the flag says when the model's account starts somewhere other than the rules'
    origin. Briefings and assistant answers get the same figure check.
  - Every model call (narration and the Ask assistant, including failures) is appended to an
    audit log, `ai_audit.jsonl` next to `feedback.json` (`RECON_AI_AUDIT_FILE`). An entry holds the
    prompt hash, the full request and response, the checks, and for the assistant the user and its
    tool calls. Each explanation, briefing and answer shows its audit ID. Text that could not be
    logged is not shown. `/api/flags` and the JSON report include `accountability` and the AI detail.
- **Reset simulation** button in the worklist header (event feed only): back to the synthetic
  starting day for the next demo. Clears the event log and re-publishes the seed, and clears the
  simulation's decisions, claims and acknowledgments. Other open screens are told to reload.
  `POST /simulation/reset`.
- Simulation data documentation (`docs/simulation_data/`) and the application guide PDF.

### Fixed
- R09 when MIPS plans more than WMS has on order (found on the demo by raising 900102's
  supply-plan line ML-0005 from 5,000 to 7,000):
  - The proposed write-back cut the largest sales-order line, clamped at zero: it would have set
    customer order ML-0004 from 1,000 to 0 and still left MIPS 1,000 above WMS, while saying MIPS
    would then match. It now takes the whole excess off the most recently changed line that can
    absorb it (here ML-0005, 7,000 → 5,000), and proposes nothing when no single line can.
  - The accountability record printed signed quantities ("-2,000 more") and called a change made in
    MIPS stale data from WMS. When MIPS demand moved after production was ordered, in the direction
    of the gap, the record now says Planning change in MIPS. ODI is "To check" rather than
    "Affected", because its runs were ordered against the earlier demand.
- The reconciled position stays arithmetically consistent when a source row is edited
  (`tests/test_consistency.py` edits every MIPS line and ODI run of the starting day):
  - A MIPS demand change after production was ordered read as inventory. Inventory is inferred as
    demand less the first run's order, so raising 900102 from 6,000 to 7,200 showed 1,200 of
    inventory, kept the 1,200 surplus and R05, and added a false R01. The projector now keeps the
    demand each SKU's first run was ordered against (`SourceData.demand_at_order`). A later change
    moves the net requirement one for one, and the card shows it ("+1,200 since production was
    ordered"). Re-ordering the first run takes up the new demand.
  - R05 measured the over-build as wrapped beyond the requirement, and fired on total orders
    above demand. A right-sized recovery run raised a 0-unit flag, and an over-sized one that
    had not wrapped yet showed 0 units. It now takes the larger of the recovery order beyond the
    shortfall and the units wrapped beyond the requirement.
  - The card's bar counted returned units as usable supply, so S5 showed "Still to produce 120"
    with no shortfall on the bar. The bar now draws the equation. R07's text quotes usable supply.
  - A return booked only on the WMS ticket (Returned Qty) did not reach the position. Returns are
    now the larger of the return transactions and the ticket's figure, per ticket.
- `/robots.txt` (`Disallow: /`) and an `X-Robots-Tag: noindex, nofollow` header on both apps:
  search crawlers were fetching the public demo's sign-in page. `/favicon.ico` redirects to the
  app's SVG icon instead of answering 404. Both answer HEAD as well as GET.
- Scenarios commit each step as one source transaction (`EventLog.publish_many`). Steps were
  published change by change, so a worklist that reloaded mid-step showed flags that vanished a
  moment later: S7 flashed R02-900111, R09-900111 and R13-FILL-VS-ITEM (its item line landed
  before its order header) before settling on R07-900111.

### Changed
- Tests use `httpx2` (Starlette 1.9's test client deprecates plain `httpx`).

## [0.2.0] - 2026-11-03

Deployment readiness: the worklist can be shared with several people on a server.

### Added
- Username and password sign-in for the worklist and the source simulator. Accounts live in
  `output/users.json` (PBKDF2 hashes) with the role set per account. `python -m recon_agent users`
  lists, seeds, adds, removes accounts and changes passwords and roles.
- Demo accounts (`planner1`, `planner2`, `supervisor1`, `supervisor2`, `manager1`), created on first
  start with random passwords printed to the log once, or `RECON_DEMO_PASSWORD` for all of them.
- Signed, expiring session cookies (`RECON_SECRET_KEY`); a password change or removed account ends
  that person's sessions, a role change applies at once.
- Failed sign-in throttling (5 per username, 50 per network address, per 15 minutes).
- `/healthz` on both apps; `--version`; the version in the API and the image labels.
- `docker-compose.yml` with optional TLS (Caddy) and simulator profiles, read-only containers.
- GitHub Actions: tests and image build on every push and pull request; on a `vX.Y.Z` tag, the image
  is published to GitHub Container Registry with a GitHub release.
- `pyproject.toml`, `requirements-dev.txt`, `docs/DEPLOYMENT.md`.

### Changed
- The sign-in screen asks for a username and password; the role is no longer chosen there.
- `/api/docs` is off unless `RECON_API_DOCS=on`.
- Dependencies are pinned.
- The assistant form no longer uses an inline `hx-on` handler (the page runs without `eval`).

### Removed
- `RECON_SIM_PASSCODE`: simulator operators sign in with accounts.
- Trust in `X-Remote-User` / `X-Remote-Role` request headers, which any client could send.

### Security
- Session cookies can no longer be edited to become another person or role.
- Error fragments escape their text (a crafted flag ID could inject HTML).
- Sign-in redirects stay on the site (`/\host` and control characters are refused).
- Security headers on every response: Content-Security-Policy without `unsafe-eval`, frame
  denial, `nosniff`, referrer policy, HSTS over HTTPS, `no-store` on pages.
- Cross-site POST/PUT/PATCH/DELETE requests are refused.
- The corrections token is compared in constant time.
- Planner decisions and corrections are written atomically, so a crash cannot corrupt them.

## [0.1.0] - 2026-10-29

First version in the repository: reconciliation engine (R01-R19), exception worklist, simulated
event feed and source simulator, write-back POC, OCI Generative AI narration and assistant.
