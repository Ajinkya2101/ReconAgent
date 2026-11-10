# Event Contract v4 and Simulated Feed

How source-system changes reach the reconciliation agent as events during the POC, and what each event must contain. This document covers Phases 0–5 of the real-time plan:

- **Phase 0:** the contract, the simulated clock and the starting dataset.
- **Phase 1:** the event-fed engine.
- **Phase 2:** the source simulator.
- **Phase 3:** server push, latency and debounced AI.
- **Phase 4:** the scenario library.
- **Phase 5:** integration faults and write-back.

**Status:** POC. All event data is dummy data, built from the business team's 9 Nov mapping workbook ([DATA_MODEL.md](DATA_MODEL.md)). [Section 6](#6-assumptions-to-confirm-with-bimbo-it) lists every assumption about BUSS, Oracle, MIPS, ODI and WMS that the real integrations must confirm. The code holds the authoritative version: [recon_agent/events/contract.py](../../recon_agent/events/contract.py).

## 1. Why events

The source workbook shows one end-of-day picture. They can't show how the worklist reacts when a source system changes during a shift, in what order, or how fast. In production, GoldenGate (MIPS change data capture), Oracle Integration 3 (BUSS, WMS and Oracle business events) and OCI Streaming will deliver row-level changes. The POC simulates that whole path, including how it fails:

```
 System B: source simulator (:8100)  · CLI · seed · scenarios         ◀── POST /oic/corrections
        │  source changes, validated against this contract                 (approved write-back)
        ▼                                                                         ▲
 source_changes  ──▶  integration (deliveries + injected faults)  ──▶  events     │
 (the systems' truth)   delay · drop · duplicate · reorder            (delivered) │
                                                                          │       │
                                       consumer commits (lag, latency) ◀──┤       │
        ▼                                                                 ▼       │
 System A: worklist (:8000)  watcher → projector → SourceData → engine → push → approve
```

The two systems share only the event log file. The worklist never reads the simulator's state, and the simulator never reads the worklist's. Two things flow back:

- **The worklist's consumer commit:** how far it has reconciled, and when. A Kafka consumer commits offsets the same way.
- **Approved corrections:** these go to the simulator's stand-in for Oracle Integration, which turns each one into a source change.

When a real connector arrives, it writes the same events and nothing downstream changes.

## 2. Event envelope

| Field | Type | Meaning |
|---|---|---|
| `seq` | integer | Delivery position in the log. Readers process events in `seq` order. |
| `source_seq` | integer | Commit position in the source (as a GoldenGate CSN would be). Every copy of a change carries it, so it orders changes to the same row however they were delivered. |
| `event_id` | UUID | One per source change. A duplicate delivery repeats it, so the consumer can ignore the copy. |
| `copy` | integer | 0 for the first delivery, 1 or more for a duplicate. |
| `source` | `MIPS` \| `ODI` \| `WMS` \| `ORACLE` | System that owns the row. Fixed per entity. |
| `entity` | string | Row type, e.g. `buss.sales_order` (section 4). |
| `op` | `I` \| `U` \| `D` | Insert, update or delete, as GoldenGate `op_type`. |
| `key` | string | Row key: the value of the entity's key column. |
| `before` | object \| null | Full row image before the change (null for an insert). |
| `after` | object \| null | Full row image after the change (null for a delete). |
| `source_ts` | datetime | When the change happened in the source system, in plant time. |
| `emitted_ts` | datetime | Wall-clock time the integration delivered it. |
| `producer` | string | Who made the change: `seed`, `cli`, `simulator:<operator>`, `scenario:<id>`, `oic:corrections`. Later `goldengate` or `oic`. |
| `scenario` | string \| null | Tag of the scripted scenario that made it. |
| `note` | string \| null | Free text for the operator ("Route 27 cut back"). |

Row images are JSON objects keyed by the **business team's own workbook column names** (section 4). Dates are `YYYY-MM-DD`, datetimes `YYYY-MM-DDTHH:MM:SS`, numbers are JSON numbers, and an empty value is `null`.

## 3. Semantics

- **Two streams.** `source_changes` is every change the source systems committed: the truth. Producers validate against it, and the simulator shows it. `events` is what the integration delivered, and the worklist reads only that. Without faults the two are identical and a change is delivered in the same transaction that commits it.
- **Full images.** Every insert and update carries the complete `after` row, so a consumer never needs earlier events to read one. A producer may send only the changed columns; the log merges them into the current source row before it validates.
- **Validation on commit.** The log rejects:
  - unknown columns, values of the wrong type, and empty required columns;
  - inserting a key that already exists;
  - updating or deleting a key that doesn't exist;
  - changing a key.

  A batch is all-or-nothing. An update that changes nothing commits nothing, as CDC doesn't capture a no-op.
- **Append-only.** Database triggers block every UPDATE and DELETE on `events`. `events reset` clears everything and issues a new `log_id`, and readers that see a new `log_id` rebuild from the start. A log file from an older contract version is recreated empty, with a notice.
- **Consumer protection.** The projector ignores two kinds of event, and counts both (`/api/feed`):
  - **duplicates:** an `event_id` it has already applied;
  - **stale images:** an event whose `source_seq` is not newer than the one already applied to that row. Deletes leave a tombstone, so a late copy can't bring a row back.

  So duplicated and reordered delivery can never show an old value. Delayed and dropped delivery do show: the worklist is behind the source until the change arrives. Because images are full, the next update to the same row heals a dropped one.
- **Projection.** Rows are read in key order (the source's own line sequence), so the order in which events for *different* rows arrive never changes the result.
- **Re-reconciliation.** A watcher thread in the worklist checks every 0.5 s whether the log moved. When it has, it re-runs the engine, marks the rows whose flags changed, and commits. Open screens hear about it by server push (`/stream`).
- **Latency.** Event-to-worklist latency is the first commit that covers an event minus its `emitted_ts`, over the last 100 non-seed events. Integration delay is separate: it's what the faults inject.

## 4. Entities

v3 has one entity per sheet of the business team's workbook `BIMBO_MIPS_WMS_ODI_Data_9th_NOV.xlsx`. It
replaces the v2 entities, which followed the five daily extracts. v4 follows the workbook's 10 Nov revision:
the BUSS column `Price` is renamed `Price in $` (`price_in_usd` in the SQL exports). A log written under v3 is
recreated empty on first start: publish the seed again.

| Entity | Source | Workbook sheet | Key |
|---|---|---|---|
| `buss.sales_order` | BUSS | Sales Orders | `SO Line ID` (synthetic: Order Number-Order Line) |
| `oracle.forecast` | ORACLE | Oracle Planning & Forecast | `Forecast ID` |
| `oracle.item` | ORACLE | ITEM Master | `Item Number` |
| `mips.work_order` | MIPS | MIPS | `Work Order Number` |
| `odi.batch_mapping` | ODI | ODI Batch Data Entry Mapping | `ODI Entry ID` (synthetic) |
| `wms.inventory` | WMS | WMS | `Inventory ID` (synthetic) |

Column names are the workbook's own, with whitespace normalised (`Quantity ` → `Quantity`,
`Allocated quantity  for SO ` → `Allocated quantity for SO`). The workbook's spelling stays (`Forcast`).
The synthetic keys are the only additions. The Excel feed reads the workbook with the same mappers, so the
workbook and the event log reconcile identically (`tests/test_events.py`).

<!-- columns:begin (generated from contract.py) -->
**`buss.sales_order`**: Customer order line received from BUSS (Sales Orders).

`SO Line ID` (str, **key** (synthetic)) · `Order Number` (str, required) · `Order Line` (number) · `Order Type` (str) · `Order Date` (date) · `Customer Name` (str) · `Ship-To Location (USA)` (str) · `FG BUSS Code` (str) · `Item Number` (str, required) · `Item Description` (str) · `UOM` (str) · `Order Quantity` (number, required) · `Requested Ship Date` (date) · `Sales Date` (date) · `Price in $` (number)

**`oracle.forecast`**: Expected demand planned in Oracle (Oracle Planning & Forecast).

`Forecast ID` (str, **key**) · `Forecast Date` (date) · `Item Number` (str, required) · `Item Description` (str) · `UOM` (str) · `Item Type` (str) · `Plant` (str) · `Forecast / Required Quantity` (number, required)

**`oracle.item`**: Product catalogue: finished goods (FG) and raw materials (RM) (ITEM Master).

`Item Number` (str, **key**) · `Item Description` (str) · `UOM` (str) · `Item Type` (str)

**`mips.work_order`**: Production work order planned in MIPS for the demand left after allocated stock (MIPS).

`Production Date` (date) · `Work Order Number` (str, **key**) · `FG Oracle Ref` (str, required) · `FG BUSS Code` (str) · `FG Description` (str) · `Forecast / Order Quantity` (number) · `Prod Line` (str) · `Shift` (str) · `Scheduling Errors` (str) · `Work Order Quantity` (number, required) · `Work order start date` (date) · `Work Order Completion Date` (date) · `Work Order Status` (str)

**`odi.batch_mapping`**: ODI's join of one demand line (BUSS order or Oracle forecast) to its work order and MIPS batches (ODI Batch Data Entry Mapping).

`ODI Entry ID` (str, **key** (synthetic)) · `Plant Code` (str) · `Schedule Date` (date) · `Source System` (str, required) · `BUSS Sales / Forcast Date` (date) · `Product Sales Date` (date) · `Requested Ship Date` (date) · `Sales Order / Forcast Date` (date) · `Sales Order Number / Forcast` (str, required) · `SO Line number` (number) · `Quantity` (number, required) · `Allocated quantity for SO` (number) · `Work Order Number` (str) · `Work Order Date` (date) · `Work Order Quantity` (number) · `Item Number` (str) · `Wrapping Start Date Time` (datetime) · `Wrapping End Date Time` (datetime) · `Plant Name` (str) · `Prod Line` (str) · `Shift` (str) · `Oracle Type` (str) · `Oracle Formula` (str) · `MIPS Description` (str) · `MIPS Sched Batches` (number) · `Act Batches Mixed` (number) · `Batch Status` (str)

**`wms.inventory`**: Warehouse stock for one item and locator; all on-hand stock is kept in WMS only (WMS).

`Inventory ID` (str, **key** (synthetic)) · `Item Number` (str, required) · `Item Description` (str) · `On hand` (number, required) · `Available quantity` (number) · `Allocated quantity` (number) · `UOM` (str) · `Item Type` (str) · `Facility` (str) · `Company / Inventory Organization` (str) · `Locator` (str)
<!-- columns:end -->

Run `python -m recon_agent events entities` for the same list in the terminal.

No column is derived. In particular WMS `On hand`, `Available quantity` and `Allocated quantity` are kept
independent, because in the real system they can drift apart, and that drift is rule R10.

## 5. Simulated clock

SLAs depend on time: the 15-minute High acknowledgment. The plant clock is stored in the log file, so every process reads the same time:

```
sim_now = anchor_sim + (wall_now − anchor_wall) × rate     while running
sim_now = anchor_sim                                       while paused
```

- **Controls:** set, advance, rate, pause and resume. Each one re-anchors at the current wall time, so simulated time never jumps unless asked to. With no clock stored, simulated time equals wall time.
- **Who uses it:** in event mode the worklist uses this clock for "loaded at", claims, acknowledgments and the High SLA.
- **Speed:** `--rate 60` makes 15 plant minutes pass in 15 real seconds.

## 6. Assumptions to confirm with Bimbo IT

| # | Assumption | Why it matters |
|---|---|---|
| A1 | BUSS order lines have a stable key (stood in for by `SO Line ID` = Order Number-Order Line). | Updates and deletes must identify the row. Without a key there is no CDC. |
| A2 | Every ODI mapping row has a stable ID (stood in for by `ODI Entry ID`) and is updated in place. | The same reason, for the ODI join. |
| A3 | ODI's `Sales Order Number / Forcast` holds the BUSS order number or the Oracle forecast ID, and `SO Line number` the BUSS order line. | R01, R02 and R03 match demand to ODI on it. |
| A4 | WMS exposes stock per item and locator with a key (stood in for by `Inventory ID`), and keeps on hand, available and allocated as separate figures. | R10 compares them; the shortfall uses available and allocated. |
| A5 | MIPS `Forecast / Order Quantity` is the demand less allocated stock, and `Work Order Quantity` is the shortfall, as in the workbook's worked example. | R04 and R05 check these two quantities. |
| A6 | An item with only an Oracle forecast (no order, work order, ODI row or stock) is planning, not yet scheduled. | Such items get no position, so they raise no flags. |
| A7 | Each source system can give a change timestamp per row (`source_ts`) and a commit position (`source_seq`). | Latency and consumer ordering need them. |
| A8 | Full row images are available (GoldenGate `GETUPDATEBEFORES` / full supplemental logging). | Without them the consumer can't rebuild a row, and a dropped update can't heal. |
| A9 | Oracle Integration can accept a correction as a change to one or more rows of one entity, applied in one transaction, with optimistic concurrency (the values it expects each row to still have). | Write-back (section 10) depends on it. |

## 7. Starting dataset (seed)

`python -m recon_agent events seed` publishes 74 insert events for sales date Saturday 12 September 2026,
plant UBD:

- the item master (10 FG and 9 RM items) and the ten Oracle forecasts, as in the workbook;
- 18 BUSS order lines, 21 ODI mapping rows, 3 WMS stock rows and 3 MIPS work orders, for three SKUs.

The clock starts at 20:00 the evening before the sales day. The seed bypasses injected faults.

| SKU | Built from | Seeded state | Expected flags |
|---|---|---|---|
| 76108 BP WHITE HOT DOG BUNS 8P 14Z | The workbook, value for value | Reconciles: (14,339 + 41,769) − 473 − 7,000 = 48,635 = the work order | none (the control) |
| 950513 BP BUTTER HOT DOG BUNS 8P 15Z | FC002 from the workbook; the rest invented | MIPS sized the work order to the 4,840 demand left and did not subtract 1,140 available; one extra batch mixed | R05 (High), R11 |
| 76105 BP WHITE HAMBURGER BUNS 8P 15Z | FC004 from the workbook; the rest invented | ODI maps the Colonie order at 800, BUSS has 900; WMS on hand 1,900 against 1,800; Albany's ship date is before the wrap | R02, R10, R12 (High) |

The test suite asserts that the seed raises exactly these five flags (`seed.EXPECTED`).

## 8. Scenario library

Ten scenarios as scripts ([events/scenarios.py](../../recon_agent/events/scenarios.py)), each breaking one step of
the demand-to-delivery flow for the clean SKU 76108, so its flags visibly appear on the worklist. Steps go
through the normal producer path, so active faults apply.

| ID | Story | Raises |
|---|---|---|
| S1 | Albany raises order 2263245 from 7,200 to 7,800 in BUSS; ODI keeps 7,200 | R02-2263245-1, R04-76108, R05-76108 |
| S2 | A new 900-unit BUSS order never reaches ODI | R01-2263260-1, R04-76108, R05-76108 |
| S3 | Wilmington cancels its 352-unit order in BUSS; ODI keeps the line | R03-ODI-0012, R04-76108, R05-76108 |
| S4 | WMS allocates 660 more to an order; ODI and MIPS keep the old allocation | R09-76108, R04-76108 |
| S5 | A planner raises the work order to 50,000 in MIPS; ODI keeps 48,635 | R05-76108, R07-WO-UBD-20260912-001 |
| S6 | A cycle count changes WMS on hand only | R10-INV-0001 |
| S7 | Line 1 runs late: wrapping ends after six orders' ship dates | R12-WO-UBD-20260912-001 |
| S8 | The work order is deleted in MIPS | R06-76108, R07-WO-UBD-20260912-001 |
| S9 | An ODI reload drops the work order from Albany's 7,200-unit line | R08-ODI-0002 |
| S10 | Chicopee's order arrives with BUSS code 2717 instead of 2771 | R13-76108 |

Changes are relative to the row as it is when the step runs (S1 adds 600 to the order, whatever it holds),
so the "Raises" column above is what each scenario raises **on the untouched starting day**. That is the
regression oracle: the test suite plays every scenario there and checks exactly those flags. On any other
data, the simulator's **If you run it now** (and `events scenario list`) plays the scenario on a scratch copy
of the log and shows what it would really raise, change or clear; a run reports the flags it actually changed.
Work-order numbers carry the sales date (`{date}` in a scenario key). Run one from the simulator's
**Scenarios** tab, or with `events scenario run S5 [--reset]`.

## 9. Integration faults

The faults are injected between `source_changes` and `events`, where a real integration fails. The source system keeps the change either way.

| Fault | Effect | Consumer outcome |
|---|---|---|
| `delay` *seconds* | The change is delivered that much later (the simulator's pump delivers it when due) | The worklist is behind until it lands, e.g. a stale WMS screen |
| `drop` | The change is never delivered (kept for *Replay dropped*) | Missing until replayed or healed by the next update to the row; a dropped BUSS order raises nothing until it arrives |
| `duplicate` *seconds* | A second copy is delivered later | Ignored (same `event_id`) |
| `reorder` *batch* | Changes are held until a batch is full, then delivered newest first | Older images are ignored (`source_seq`) |

A fault can be scoped to a system and/or entity, and limited to a number of changes. The simulator's **Integration** tab has presets (stale WMS feed, lose the next BUSS order, at-least-once delivery, out-of-order delivery), a custom form, *Deliver waiting now*, *Replay dropped* and *Remove all faults*. The CLI equivalents are `events fault add delay --source WMS --seconds 60`, `events fault list|remove --id N|clear` and `events deliver [--now] [--replay-dropped]`. Reset clears all faults.

## 10. Write-back (corrections)

This closes the product's Phase 2 loop in the POC: **Flagged → Proposed → Approved → Sent → Confirmed**, with **Failed → Retry** and **Cancelled**. See [recon_agent/corrections.py](../../recon_agent/corrections.py).

**1. Proposed.** In event mode, a flag whose fix is clear shows a *Proposed correction* on the worklist,
computed from the rows the worklist currently sees:

| Rule | Correction |
|---|---|
| R01 | Insert the missing ODI mapping row, on the SKU's work order |
| R02 | Set the ODI row's quantity to the BUSS order or Oracle forecast |
| R03 | Delete the ODI row whose order or forecast no longer exists |
| R04 | Set the MIPS `Forecast / Order Quantity` to the demand left (one work order only) |
| R05 | Set the MIPS `Work Order Quantity` to the shortfall (one work order only) |
| R06 | Create the MIPS work order for the shortfall, under the number ODI still maps if there is one |
| R07 | Set `Work Order Quantity` on every ODI row of the work order to the MIPS quantity (one transaction) |
| R08 | Link the uncovered ODI row to the SKU's work order |

There are deliberately no automatic corrections where the fix needs a fact only a person can establish: R09
(which order holds the stock), R10 (which WMS figure the count proves wrong), R11 (why the plant mixed more),
R12 (move the run or the ship date), R13 (which code is right), and R04/R05 when a SKU has several work orders.

**2. Approved.** Only a planner or manager can approve. Approving also accepts the flag.

**3. Sent.** The worklist POSTs the correction to `RECON_CORRECTIONS_URL`, by default the simulator's `/oic/corrections`, standing in for Oracle Integration. The endpoint applies it only if the row still has the expected values; otherwise it answers 409. Its mode can be set to *reject every correction* (422) or *unavailable* (503) to test failure.

**4. Confirmed.** The applied correction becomes a source change (`oic:corrections`) and travels the integration like any other, so faults delay or drop it too. The worklist marks it **Confirmed** only when its own feed shows the corrected values, not when the API says yes.

**Failed and Retry.** A failed correction can be retried; it is recomputed from what the worklist shows then. If no correction applies any more, it becomes **Cancelled**.

Every correction keeps its ID, before and after values and status history in `corrections.json`, next to the event log, and is available at `GET /api/corrections[/{id}]`. Corrections follow the flow. For example, after S1: R02 fixes ODI, R04 then fixes the MIPS demand, R05 the work order, and the new work-order quantity raises R07 until it is re-sent to every ODI row.

## 11. Running it

```bash
python -m recon_agent events seed --reset                     # fresh starting day (3 SKUs, 5 flags)
RECON_SOURCE=events python -m recon_agent serve               # System A: worklist (:8000)
python -m recon_agent simulate                                # System B: simulator (:8100)

python -m recon_agent events scenario run S1                  # or use the simulator's Scenarios tab
python -m recon_agent events fault add delay --source WMS --seconds 60
python -m recon_agent events set buss.sales_order 2263245-1 "Order Quantity=7800" --note "Albany raised its order"
python -m recon_agent events clock --rate 60                  # 1 real second = 1 plant minute
python -m recon_agent events status
python -m recon_agent events export --out output/export   # the source systems now: Excel, SQLite, Postgres
```

On PowerShell, set `$env:RECON_SOURCE="events"` first. Write-back needs the simulator running at `RECON_CORRECTIONS_URL`. The simulator's delivery pump is what delivers delayed changes, so keep it running when faults are active (CLI commands also deliver what is due when they run).

**Simulator:** sign in with a worklist account (username and password; set `RECON_CORRECTIONS_TOKEN` before sharing the URL). Dummy data only; never run it next to a production worklist. It has:
- **Tables:** one tab per entity, showing source truth, with an editor. No column is derived in v3 ([events/derive.py](../../recon_agent/events/derive.py)).
- **Source changes:** each change's column diff and its delivery state (event #, delayed, held, dropped, duplicate copy, on worklist).
- **Clock controls** and **worklist lag and latency**.
- **Scenarios** and **Integration** tabs.

**AI narration on a moving feed.** The worklist never calls the model once per event. It reuses cached explanations for flags whose ID, units and evidence haven't changed. It makes one call for the rest after 20 s of quiet (at most 120 s after the first change). The briefing is reused only for an identical run. `RECON_AI=off` disables every model call.

**Docker:**
- `start_container.bat sim` starts the simulator container on :8101.
- `start_container.bat events …` runs the CLI inside the worklist container.
- The log is on the `recon-sim` named volume. The worklist container reaches the simulator's corrections endpoint through `host.docker.internal`.
- Run `start_container.bat build` once after pulling.

**Files:** event mode keeps its log, `corrections.json`, decisions and reports in `output/sim/`. Simulated decisions never change the rule confidence learned from real data.

**Tests:** `python -m unittest discover -s tests -v` (106 tests). Each test module isolates itself: no model calls, and no real decisions file.

## 12. Later

- **Kafka-compatible log** (Phase 6): OCI Streaming replaces SQLite behind the same publish/read interface, and real connectors replace the simulator one system at a time.
- **Automatic write-back** for rules at ≥98% acceptance over 8 weeks (product Phase 2), enabled by the productivity manager.
- **Raw materials**: a bill of materials and RM stock and inbound in WMS, for the workbook's "if FG is not available, check components, then inbound RM" step.
