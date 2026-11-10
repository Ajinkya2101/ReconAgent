# Changelog

All notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/). The version lives in
`recon_agent/__init__.py`; release by tagging the commit `vX.Y.Z` (see docs/engineering/DEPLOYMENT.md).

## [Unreleased]

## [0.5.0] - 2026-11-10

**Upgrading from 0.4.0:** publish the seed again (`python -m recon_agent events seed --reset`) to get the new starting day.

### Changed: a richer starting day (five SKUs to work on)
- Three more seeded SKUs, each with one discrepancy the starting day did not show yet: 950514 (an order the BUSS -> ODI
  interface dropped, R01), 150080 (WMS holds more allocated stock than ODI records, R09) and 77442 (ODI keeps the old
  work-order quantity after MIPS re-planned, R07). Their forecasts are the workbook's (FC005, FC007, FC008); each
  carries eight weeks of history, so the ML layer stays quiet on them.
- The worklist now opens with 5 SKUs, 8 open flags and $32,810 at risk (was 2 SKUs, 5 flags, $16,945); 76108 still
  reconciles as the control. The seed publishes 508 events.
- The seed supports two more discrepancies by configuration: an order left out of ODI (`odi_missing`) and an ODI
  work-order quantity that differs from MIPS (`odi_wo_qty`).

## [0.4.0] - 2026-11-10

**Upgrading from 0.3.1:** the event contract is unchanged (v4), but the seed now carries 8 earlier sales days of
history for the ML layer. Publish the seed again (`python -m recon_agent events seed --reset`), or the ML layer has no
history and raises nothing.

### Added: the ML layer (R14-R17) and its scenarios (S11-S14)
- **Anomaly detection against each item's own history** (`recon_agent/anomaly.py`), robust z-scores (median/MAD):
  R14 unusual order quantity for the customer, R15 forecast bias, R16 production anomaly (wrap duration), R17 inventory
  drift (unexplained change in WMS on hand). Flagged at |z| >= 3.5 (High at 7) with at least 6 earlier values; both are
  settings (`RECON_ML_Z`, `RECON_ML_MIN_HISTORY`). They catch what the rules cannot: a wrong value every system agrees on.
- **History.** Reconciliation reads only the current sales day (`ingest.split_current`); earlier days in the same tables
  are the ML history. WMS history is the row's change log in the event feed. The seed adds 8 earlier Saturdays (314 events).
  A single-day workbook raises no ML flags.
- **Scenarios S11-S14** (labelled "ML layer" in the simulator): an order keyed with an extra zero, a doubled forecast, a
  line at half speed, stock vanishing overnight; each keeps the systems consistent, so only the ML flag fires.
- ML flags carry accountability (origin likely, failure type Statistical anomaly) and are labelled "From the ML layer".
- **Fixed:** corrections and the exported `v_sku_position` view read the current sales day only, not earlier days.
- **System user guide** ([docs/guides/System_User_Guide.pdf](docs/guides/System_User_Guide.pdf)): every feature with
  screenshots, all 14 scenarios, the ML layer, the command line, the API, and the full user test report.

## [0.3.1] - 2026-11-10

### Changed: source changes wait for Update again
- 0.3.0 redrew the worklist by itself whenever the source data changed. The top bar again shows
  "N rows changed by others · Update" (or "AI explanations ready · Update", "Figures changed · Update"),
  and nothing on screen moves until Update is clicked; the as-of time says when the screen last checked. Update redraws
  the queue, the day totals, the briefing and the open detail from the current run, as the automatic
  redraw did; an unsent note still holds the detail back. A person's own actions never show the pill.

## [0.3.0] - 2026-11-10

The business team's 9 Nov data model (v3) replaces the five-extract model (v2), and `main` now holds it
as the only data schema. See [docs/engineering/REPOSITORY_HISTORY.md](docs/engineering/REPOSITORY_HISTORY.md)
for the overwrite of `main` and the deleted branches.

**Upgrading from 0.2.0** (breaking):
- The agent reads one workbook, `BIMBO_MIPS_WMS_ODI_Data*.xlsx`, anywhere under `data/`. The five daily
  extracts are no longer read.
- Event contract v4: an existing event log is recreated empty on first start. Publish the seed again
  (`python -m recon_agent events seed --reset`) and clear planner decisions made on v2 flags.
- Rules are R01-R13 (was R01-R19); flag IDs from 0.2.0 do not carry over.

### Changed: the docs folder, by audience
- `docs/data-model/` (data model, event contract, data inconsistencies), `docs/guides/` (user and
  systems guides, screenshots), `docs/engineering/` (technical overview, deployment, repository
  history). `DATA_MODEL_9NOV.md` is now `docs/data-model/DATA_MODEL.md`.
- The v2 documents (five-extract model) moved to `docs/archive/v2/`. `docs/README.md` indexes what is
  current. Every link and code reference follows the new paths.

### Removed: the last of the v2 carry-forward
- The v3 data model keeps all surplus in WMS, so no rule carries units to the next sales day. The empty
  `CARRY_FORWARD_RULES` and `SALES_WEEKDAYS` settings and everything that read them are gone: the
  "carried in" day card, the carry chip, hint and line, the re-tag animation, and `next_day` in the
  `/api/positions` ledger. The codebase now has one data schema.

### Added: the worklist tells the truth about progress and hand-off
- **Decisions move the money.** Each sales day shows what is still at risk (each SKU's largest open flag)
  and what has been accepted and decided so far; a decision updates the shelf at once. The Decided tab
  counts flags, not rows.
- **Rank moves are explained.** After a decision the detail says why the row moved: it now ranks on its
  next open flag, and the rule's new confidence re-ranks its flags on other rows.
- **Handed over.** A new view collects open work someone has already touched: shift notes, other
  people's claims, and High flags acknowledged but not yet decided.
- **Since your last sign-in.** The briefing lists new flags and what others decided, noted, claimed or
  acknowledged since the reader's previous sign-in.
- **Acknowledge first.** While a High flag on a SKU waits for acknowledgment, every Accept on that SKU
  steps back to an outline. No empty view or filter says "Every SKU reconciles" while flags are open.
- Top actions are grouped one line per SKU; the view tabs have short labels on phones.

### Fixed: counts read "1 unit", never "1 units"
- Recommendations, accountability records and write-back proposals put the noun in the right number
  (`detectors._units`).

### Changed: the 10 Nov revision of the business workbook (event contract v4)
- **Price.** The BUSS column `Price` is now `Price in $` and carries $4.69 on every 76108 line. The contract
  renames the column, so the dollar ranking uses real prices instead of the $3.99 default. The reader still
  accepts a workbook with the old `Price` header. An event log written under v3 is recreated empty on first
  start: publish the seed again.
- **Seed.** 76108 matches the revised workbook value for value, including its price and the blank batch
  counts on the order served from allocated stock (now checked against the workbook when it is present).
  The invented SKUs get prices: 76105 $4.69, 950513 $4.99. The same five flags are raised, with dollar values
  at these prices. Scenario S2's new order carries $4.69.
- **Data questions.** [docs/data-model/Data_Inconsistencies_9Nov.pdf](docs/data-model/Data_Inconsistencies_9Nov.pdf) lists the
  workbook's own inconsistencies, with values, for the business team. No rule changes until they answer.

### Fixed: every figure and explanation follows the source data live
- **Change detection.** The worklist used to announce a change only when a row's flag IDs or units moved,
  so an edit that changed a batch count, a wrap time, a name or an explanation never reached open screens,
  and the first row of an empty worklist was never announced. Each row is now fingerprinted on everything
  it shows: every flag's figures, evidence, rule text, AI text and accountability, and every source record
  behind its reconciled position.
- **Live redraw.** A change in the data redraws the queue, the day totals, the briefing and the open detail
  by itself; the "rows changed · Update" pill is gone. An unsent note holds the detail back until it is sent,
  cleared or left; an expanded briefing stays open; open sections and the scroll position survive. A row
  whose last flag clears while open says so, with the agreed figures.
- **Every ODI row.** R07, R11 and R12 read every ODI row of a work order, not only the first (a stale row
  further down was missed or reported as 0 units). Rows that disagree on batch counts are named.
- **No result is written down.** Scenario changes are relative to the row as it is (`add(600)`), stories
  describe only the action, and what a scenario will raise is a live preview played on a copy of the log
  (simulator *If you run it now*, `events scenario list`); a run reports the flags it actually raised. The
  assistant's example questions come from the current queue; the AI prompt quotes no fixed figures; the AI
  cache also keys on the rule text and attribution; the R06 correction takes the plant code from the data.
- **Live export.** The simulator's *Export now* (Excel, SQLite with the `v_sku_position` view, Postgres
  script) and `events export` build the files from the current source rows on every download.

### Changed: data model v3 (the business team's 9 Nov mapping)
The agent no longer replicates the five client extracts. It uses the business team's demand-to-delivery
mapping (`BIMBO_MIPS_WMS_ODI_Data_9th_NOV.xlsx`; see [docs/data-model/DATA_MODEL.md](docs/data-model/DATA_MODEL.md)).
- **Sources and contract v3.** Six entities, one per workbook sheet: `oracle.item`, `buss.sales_order`,
  `oracle.forecast`, `odi.batch_mapping`, `wms.inventory` and `mips.work_order`. BUSS is now a source system
  of its own. The Excel feed reads the one workbook from anywhere under `data/`. An event log written under
  contract v2 is recreated empty on first start: publish the seed again.
- **Reconciled position.** `Work Order Qty = (Sales Orders + Forecast) − Allocated − Available`. For 76108
  that is (14,339 + 41,769) − 473 − 7,000 = 48,635, as in the business team's worked example.
- **Rules.** 13 rules, one per step of the flow (R01–R13), replace the 19 v2 rules. The statistical outlier
  check (old R17) is removed. Accountability lists BUSS and Oracle when a rule involves them.
- **Dummy data.** Three SKUs. 76108 is the workbook's example and reconciles. 950513 and 76105 carry five
  designed flags. Ten scenarios (S1–S10) each break one step for 76108.
- **Write-back.** Proposals for R01–R08. A correction can update several rows in one transaction (R07: every
  ODI row of a work order) or delete a row (R03). The simulator's `/oic/corrections` accepts `rows` and `op: "D"`.
- **Worklist.** The detail draws the new equation and bar (allocated, available, work order against demand),
  checks the MIPS demand and work order against it, and lists BUSS, Oracle, ODI, WMS and MIPS source records.
  Carry-forward is off: surplus stays in WMS.
- **Removed:** `anomaly.py`, the WMS order-totals rebuild in the simulator, and the v2 tests
  (`test_consistency.py`, `test_client_alignment.py`). 106 tests pass.

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
- Simulation data documentation (`docs/archive/v2/simulation_data/`) and the application guide PDF.

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
- `pyproject.toml`, `requirements-dev.txt`, `docs/engineering/DEPLOYMENT.md`.

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
