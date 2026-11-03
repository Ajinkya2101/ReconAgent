# Event Contract v2 and Simulated Feed

How source-system changes reach the reconciliation agent as events during the POC, and what each event must contain. This document covers Phases 0–5 of the real-time plan:

- **Phase 0:** the contract, the simulated clock and the starting dataset.
- **Phase 1:** the event-fed engine.
- **Phase 2:** the source simulator.
- **Phase 3:** server push, latency and debounced AI.
- **Phase 4:** the scenario library, plus R18 and R19.
- **Phase 5:** integration faults and write-back.

**Status:** POC. All event data is synthetic. [Section 6](#6-assumptions-to-confirm-with-bimbo-it) lists every assumption about MIPS, ODI, WMS and Oracle Fusion that the real integrations must confirm. The code holds the authoritative version: [recon_agent/events/contract.py](../recon_agent/events/contract.py).

## 1. Why events

The daily Excel extracts show one end-of-day picture. They can't show how the worklist reacts when a source system changes during a shift, in what order, or how fast. In production, GoldenGate (MIPS change data capture), Oracle Integration 3 (WMS, BUSS and Fusion business events) and OCI Streaming will deliver row-level changes. The POC simulates that whole path, including how it fails:

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
| `entity` | string | Row type, e.g. `wms.item_order` (section 4). |
| `op` | `I` \| `U` \| `D` | Insert, update or delete, as GoldenGate `op_type`. |
| `key` | string | Row key: the value of the entity's key column. |
| `before` | object \| null | Full row image before the change (null for an insert). |
| `after` | object \| null | Full row image after the change (null for a delete). |
| `source_ts` | datetime | When the change happened in the source system, in plant time. |
| `emitted_ts` | datetime | Wall-clock time the integration delivered it. |
| `producer` | string | Who made the change: `seed`, `cli`, `simulator:<operator>`, `scenario:<id>`, `oic:corrections`. Later `goldengate` or `oic`. |
| `scenario` | string \| null | Tag of the scripted scenario that made it. |
| `note` | string \| null | Free text for the operator ("Route 27 cut back"). |

Row images are JSON objects keyed by the **client's own extract column names**. For the v2 entities (section 4), the names follow WMS report style and Oracle Fusion REST style. Dates are `YYYY-MM-DD`, datetimes `YYYY-MM-DDTHH:MM:SS`, numbers are JSON numbers, and an empty value is `null`.

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
- **Re-reconciliation.** A watcher thread in the worklist checks every 0.5 s whether the log moved. It also checks whether plant time has passed the next R18 deadline, because time alone can raise a flag. When either is true, it re-runs the engine, marks the rows whose flags changed, and commits. Open screens hear about it by server push (`/stream`).
- **Latency.** Event-to-worklist latency is the first commit that covers an event minus its `emitted_ts`, over the last 100 non-seed events. Integration delay is separate: it's what the faults inject.

## 4. Entities

| Entity | Source | Replaces / adds | Key |
|---|---|---|---|
| `mips.order_line` | MIPS | MIPS Order Data | `Order Line ID` (synthetic) |
| `odi.finished_good` | ODI | ODI Finished Good Data | `FGEntryID` (synthetic) |
| `odi.batch` | ODI | ODI Batch Data | `BatchEntryID` (synthetic) |
| `wms.order` | WMS | WMS Fill Rate Report | `Order Nbr` |
| `wms.item_order` | WMS | WMS Order Summary Report by Item | `Item Line ID` (synthetic) |
| `wms.ticket` | WMS | *v2*: receipt tickets (plant to warehouse or freezer) | `Ticket Nbr` |
| `wms.transaction` | WMS | *v2*: receipts, shipments, returns to plant, freezer moves | `Txn Nbr` |
| `oracle.inventory_txn` | ORACLE | *v2*: Fusion inventory postings of WMS transactions | `TransactionId` |

The first five follow the extracts in name and order. The synthetic key columns, plus `Sales Date` on `wms.item_order` (which the Excel report prints once in the header), are the only additions. The three v2 entities carry detail that no extract has. R18, R19 and the returns term of the net-requirement equation need it.

<!-- columns:begin (generated from contract.py) -->
**`mips.order_line`**: MIPS schedule line: a BUSS sales order or an OCloud supply-plan line (MIPS Order Data).

`Order Line ID` (str, **key** (synthetic)) · `Plant Code` (str) · `Plant Name` (str) · `Schedule Date` (date, required) · `Offset Sales Date` (date, required) · `BUSS Sales Date` (date) · `Order Moved` (bool) · `Product Sales Date` (date) · `Requested Ship Date` (date) · `Production Date` (date) · `FG Oracle Ref` (str, required) · `FG BUSS Code` (str) · `FG Description` (str) · `Order Quantity` (number, required) · `Container Code` (str) · `Units per Container` (number) · `Units Per Carton` (number) · `Containers per Rack` (number) · `Retail Price` (number) · `Closed` (str) · `Source System` (str, required) · `Last Modify Date Time` (datetime) · `Scheduling Errors` (str)

**`odi.finished_good`**: ODI / Oracle production result for one SKU in one run (ODI Finished Good Data).

`FGEntryID` (str, **key** (synthetic)) · `PlantName` (str) · `SalesDate` (date, required) · `ProductionStartDateTime` (datetime) · `ProductionEndDateTime` (datetime) · `LineCode` (str, required) · `ShiftName` (str) · `OracleTypeCode` (str, required) · `LegacyFGNumber` (str) · `OracleFGNumber` (str, required) · `OCloudFGItemStructure` (str) · `OracleFGDescription` (str) · `NetUnitsOrdered` (number) · `ActualWrappedUnits` (number) · `ExpectedEndingInventoryQty` (number) · `MIPSBeginningInventoryQty` (number) · `MIPSInventoryAdjustmentQty` (number) · `MIPSOrderQty` (number) · `MIPSOrderAdjustmentQty` (number) · `ActualDamagedWeightWithWrap` (number) · `ActualDamagedUnitsWithWrap` (number) · `ActualDamagedWeightWithoutWrap` (number) · `ActualDamagedUnitsWithoutWrap` (number) · `TotalActualDamagedUnits` (number) · `TheoreticalRunTime` (number) · `TotalPieces` (number) · `OraclePiecesPerPkg` (number)

**`odi.batch`**: ODI batch (mixing) record for one formula run (ODI Batch Data).

`BatchEntryID` (str, **key** (synthetic)) · `PlantName` (str) · `SalesDate` (date, required) · `WrappingStartDateTime` (datetime) · `WrappingEndDateTime` (datetime) · `Prod Line` (str, required) · `Shift` (str) · `Oracle Type` (str, required) · `Oracle Formula` (str) · `MIPS Description` (str) · `MIPS Sched Batches` (number) · `Scrap Dough Weight` (number) · `Act Batches Mixed` (number) · `Total Raw Ingr Adjust Weight` (number) · `Total Other Batch Weight` (number) · `Total Baked Ingr Weight` (number) · `Total Remix Batch Weight` (number) · `Tot Batch Pieces Counted Qty` (number) · `Actual Damaged Weight w/o Wrap Weight` (number) · `Theo Batch Pieces Qty` (number) · `Batch Piece Var Qty` (number) · `Batch Piece Var %` (number) · `FG & FG Scrap Pieces Qty` (number) · `Final Piece Variance Qty` (number) · `Final Piece Variance %` (number)

**`wms.order`**: WMS customer order header with fill and shipping progress (WMS Fill Rate Report).

`Facility` (str) · `Order Nbr` (str, **key**) · `Ship to Name` (str) · `Progress` (str) · `Route Nbr` (str) · `Order Type` (str, required) · `Sales Date` (date) · `Order Qty` (number, required) · `Allocated Qty` (number) · `Packed Qty` (number) · `Loaded Qty` (number) · `Shipped Qty` (number) · `Short Qty` (number) · `% Allocated` (number) · `% Loaded` (number) · `% Shipped` (number)

**`wms.item_order`**: WMS order quantity and allocation for one item and customer (WMS Order Summary Report by Item, one row per customer line).

`Item Line ID` (str, **key** (synthetic)) · `Sales Date` (date) · `Item Nbr` (str, required) · `Units per Container` (number) · `Description` (str) · `Cust Name` (str, required) · `Original Ord Qty` (number) · `Order Qty` (number, required) · `Order Stack Qty` (number) · `Order Tray Qty` (number) · `Order Units Qty` (number) · `Allocated Stacks` (number) · `Allocated Trays` (number) · `Allocated Units Qty` (number) · `Stacks Remaining` (number) · `Trays Remaining` (number) · `Completion%` (str)

**`wms.ticket`**: WMS receipt ticket: finished goods received from a plant run into the warehouse or freezer.

`Ticket Nbr` (str, **key**) · `Sales Date` (date) · `Item Nbr` (str, required) · `Description` (str) · `Lot Nbr` (str) · `Run Code` (str) · `Location` (str) · `Received Date Time` (datetime) · `Received Qty` (number, required) · `Returned Qty` (number) · `Net Received Qty` (number) · `Status` (str)

**`wms.transaction`**: WMS inventory transaction (receipt, ship, return to plant, freezer move) that Oracle must post.

`Txn Nbr` (str, **key**) · `Txn Type` (str, required) · `Txn Date Time` (datetime, required) · `Item Nbr` (str, required) · `Qty` (number, required) · `Ticket Nbr` (str) · `Order Nbr` (str) · `Lot Nbr` (str) · `Reason Code` (str) · `User` (str)

**`oracle.inventory_txn`**: Oracle Fusion inventory transaction posted from a WMS transaction (SourceReference = Txn Nbr).

`TransactionId` (str, **key**) · `SourceReference` (str, required) · `TransactionTypeName` (str) · `ItemNumber` (str, required) · `TransactionQuantity` (number, required) · `TransactionUnitOfMeasure` (str) · `Subinventory` (str) · `LotNumber` (str) · `TransactionDate` (datetime) · `PostedDate` (datetime) · `ProcessStatus` (str, required) · `ErrorMessage` (str)
<!-- columns:end -->

Run `python -m recon_agent events entities` for the same list in the terminal.

**Rules on the v2 detail**
- **R18, WMS transaction not posted in Oracle within 10 minutes (High).** A `wms.transaction` with no `oracle.inventory_txn` in `ProcessStatus = POSTED` whose `SourceReference` is its `Txn Nbr`, 10 or more plant minutes after `Txn Date Time`. A posting in `ERROR` counts as not posted, and its message is shown.
- **R19, return to plant did not reduce the received quantity (High at 500 units or more, otherwise Medium).** `RETURN_TO_PLANT` transactions against a ticket add up to more than the ticket's `Returned Qty`. WMS derives `Net Received Qty = Received Qty − Returned Qty`, so the ticket still shows the returned units as received.
- **Returns term.** Each SKU's `RETURN_TO_PLANT` units are added back to the net requirement. Returned product was wrapped, but it isn't usable supply.

**Live vs report differences.** A live feed has no printed report totals, so R13's per-item subtotal check doesn't apply in event mode. The plant-level R13 check stays: it compares the `wms.order` headers with the `wms.item_order` lines.

## 5. Simulated clock

Rules and SLAs depend on time: the 15-minute High acknowledgment and R18's 10-minute posting window. The plant clock is stored in the log file, so every process reads the same time:

```
sim_now = anchor_sim + (wall_now − anchor_wall) × rate     while running
sim_now = anchor_sim                                       while paused
```

- **Controls:** set, advance, rate, pause and resume. Each one re-anchors at the current wall time, so simulated time never jumps unless asked to. With no clock stored, simulated time equals wall time.
- **Who uses it:** in event mode the worklist uses this clock for "loaded at", claims, acknowledgments, the High SLA and R18.
- **Speed:** `--rate 60` makes 15 plant minutes pass in 15 real seconds.

## 6. Assumptions to confirm with Bimbo IT

| # | Assumption | Why it matters |
|---|---|---|
| A1 | MIPS schedule lines have a stable primary key that GoldenGate can capture (stood in for by `Order Line ID`). | Updates and deletes must identify the row. Without a key there is no CDC. |
| A2 | MIPS's line sequence for a SKU is stable and orderable (the POC orders by key). | R01 (inventory applied to the first line only) depends on which line is first. |
| A3 | ODI finished-good and batch records have stable IDs (stood in for by `FGEntryID` and `BatchEntryID`) and are updated in place as a run progresses. | The same reason as A1, for production results. |
| A4 | WMS exposes order headers and per-item order lines as separate entities, each with a key (`Order Nbr` is native; `Item Line ID` stands in for an order-line ID). | R09, R10 and R13 compare the two. |
| A5 | Each source system can give a change timestamp per row (`source_ts`) and a commit position (`source_seq`, e.g. the GoldenGate CSN). | Latency and R18 need when a change happened. Consumer ordering needs the commit position. |
| A6 | Column names and meanings match the extract headers the plant sends today. | The adapters map by column name. |
| A7 | Full row images are available (GoldenGate `GETUPDATEBEFORES` / full supplemental logging). | Without them the consumer can't rebuild a row, and a dropped update can't heal. |
| A8 | Oracle Fusion inventory transactions carry the WMS transaction number in a reference field (`SourceReference` here) and expose posting status and error text. Fusion REST-style names are assumed. | R18 matches WMS to Oracle on this reference. |
| A9 | WMS receipt tickets keep a returned quantity, and a return-to-plant transaction names the ticket it reduces. | R19 compares the two. |
| A10 | The 10-minute posting SLA is measured from the WMS transaction time, in plant time. | It decides when R18 fires. |
| A11 | Oracle Integration can accept a correction as a row-level change, with optimistic concurrency: the values it expects the row to still have. | Write-back (section 10) depends on it. |

## 7. Starting dataset (seed)

`python -m recon_agent events seed` publishes 97 insert events for one Saturday sales day:

- 17 MIPS lines, 14 ODI runs and 6 batches;
- 23 WMS item lines and 4 WMS orders, across 12 SKUs and 4 customers;
- 11 receipt tickets (one per run that wrapped product), each with its WMS receipt transaction and its Oracle posting. Nothing is late or returned.

The seed then starts the clock at 20:00 the evening before the sales day: production is wrapped and WMS is loading trailers. All values are invented. SKUs are numbered 900101–900112, and every description and customer name starts with `SIM`. The seed bypasses injected faults. Each SKU is built to show one scenario:

| SKU | Scenario | Expected flag |
|---|---|---|
| 900101 | 1: inventory applied to the first of three order lines | R01 (High) |
| 900102 | 4: recovery run over-built by 1,200 | R05 (High) |
| 900103 | 2: inventory netted with no beginning inventory recorded | R02 |
| 900104 | 2: zero-quantity line in a live run | R03 |
| 900105 | 3: run moved from Line 1 to Line 2, ghost left behind | R04 |
| 900106 | 7: WMS filled 700 units from stock MIPS cannot see | R07 (High) |
| 900107 | 6 and 5: WMS order raised after MIPS synced, allocation short | R09, R10 |
| 900108 | 400 surplus not carried to the next sales day | R06 |
| 900109 | units per container 10 in MIPS, 12 in WMS | R11 |
| 900110 | negative net order: inventory exceeds demand | R08 |
| 900111, 900112 | clean controls (the scenarios below use them) | none |
| plant | 8: Route 12 shipped with no trailer load scan; recovery batch mixed above schedule; BUSS pads the FG reference | R12 (High), R14, R15 |

The test suite asserts that the seed raises exactly these 14 flags (`seed.EXPECTED`).

## 8. Scenario library

The eight requirement scenarios as scripts ([events/scenarios.py](../recon_agent/events/scenarios.py)). Each plays a short story against the starting day, mostly on the clean SKUs, so its flag visibly appears on the worklist. Steps go through the normal producer path: derived columns are recomputed, and active faults apply.

| ID | Scenario | Story | Raises |
|---|---|---|---|
| S1 | 1 | MIPS nets 2,000 units for 900111 against the first of two lines only | R01-900111 |
| S2 | 2 | 200 units netted for 900112 through a new zero-quantity line, scheduled as a blind run | R02-900112, R03-SU900220-900112 |
| S3 | 3 | 900111's run moves from Line 2 to Line 1; a zero copy with no shift stays | R04-SU900210-900111 |
| S4 | 4 | 900112 wraps 100 short; 90 plant minutes later a recovery run makes the full 900 again | R05-900112 |
| S5 | 5 | 120 damaged units of 900111 returned to the plant against TK-0010; the ticket isn't reduced | R19-TK-0010, R07-900111 |
| S6 | 6 | Route 12 raises 900112 from 900 to 1,100 in WMS; MIPS hasn't refreshed | R09-900112, R10-900112 |
| S7 | 7 | 300 more 900111 filled from the freezer; MIPS plans them, the plant makes nothing extra | R07-900111 |
| S8 | 8 | South's trailer ships; Oracle rejects one posting and never sees another; 10 plant minutes pass | R18-WT-S8A, R18-WT-S8F |

Each scenario declares the flags it must raise (and clear). On the untouched starting day, the test suite plays every one and checks exactly that, so the library is also the regression suite for the rules. Run one from the simulator's **Scenarios** tab (optionally "Reset first", with 0, 3 or 10 s between steps), or with `events scenario run S5 [--reset]`.

## 9. Integration faults

The faults are injected between `source_changes` and `events`, where a real integration fails. The source system keeps the change either way.

| Fault | Effect | Consumer outcome |
|---|---|---|
| `delay` *seconds* | The change is delivered that much later (the simulator's pump delivers it when due) | The worklist is behind until it lands, e.g. a stale WMS screen (scenario 6) |
| `drop` | The change is never delivered (kept for *Replay dropped*) | Missing until replayed or healed by the next update to the row; a dropped Oracle posting raises R18 |
| `duplicate` *seconds* | A second copy is delivered later | Ignored (same `event_id`) |
| `reorder` *batch* | Changes are held until a batch is full, then delivered newest first | Older images are ignored (`source_seq`) |

A fault can be scoped to a system and/or entity, and limited to a number of changes. The simulator's **Integration** tab has presets (stale WMS feed, lose the next Oracle posting, at-least-once delivery, out-of-order delivery), a custom form, *Deliver waiting now*, *Replay dropped* and *Remove all faults*. The CLI equivalents are `events fault add delay --source WMS --seconds 60`, `events fault list|remove --id N|clear` and `events deliver [--now] [--replay-dropped]`. Reset clears all faults.

## 10. Write-back (corrections)

This closes the product's Phase 2 loop in the POC: **Flagged → Proposed → Approved → Sent → Confirmed**, with **Failed → Retry** and **Cancelled**. See [recon_agent/corrections.py](../recon_agent/corrections.py).

**1. Proposed.** In event mode, a flag whose fix is one clear row change shows a *Proposed correction* on the worklist, computed from the rows the worklist currently sees:

| Rule | Correction |
|---|---|
| R09 | MIPS follows WMS: the SKU's largest sales-order line takes the difference |
| R13 | Rebuild the most-mismatched WMS order header from its item lines |
| R16 | Re-send the MIPS sales-order quantity to the SKU's first ODI run |
| R18 | Resubmit the Oracle posting (repost an ERROR, or insert the missing one) |
| R19 | Book the returned quantity on the WMS ticket |

There are deliberately no automatic corrections where the fix needs a fact only a person can establish: R02 (where netted inventory physically is; recording it blindly could hide a real shortfall), R05/R06 (which day takes a surplus), R07 (which freezer lot was used), R10 and R11.

**2. Approved.** Only a planner or manager can approve. Approving also accepts the flag.

**3. Sent.** The worklist POSTs the correction to `RECON_CORRECTIONS_URL`, by default the simulator's `/oic/corrections`, standing in for Oracle Integration. The endpoint applies it only if the row still has the expected values; otherwise it answers 409. Its mode can be set to *reject every correction* (422) or *unavailable* (503) to test failure.

**4. Confirmed.** The applied correction becomes a source change (`oic:corrections`) and travels the integration like any other, so faults delay or drop it too. The worklist marks it **Confirmed** only when its own feed shows the corrected values, not when the API says yes.

**Failed and Retry.** A failed correction can be retried; it is recomputed from what the worklist shows then. If no correction applies any more, it becomes **Cancelled**.

Every correction keeps its ID, before and after values and status history in `corrections.json`, next to the event log, and is available at `GET /api/corrections[/{id}]`. A correction can cascade. For example, R09's fix raises the MIPS sales orders, which can then raise R16 (ODI's copy is behind) and R02.

## 11. Running it

```bash
python -m recon_agent events seed --reset                     # fresh synthetic day
RECON_SOURCE=events python -m recon_agent serve               # System A: worklist (:8000)
python -m recon_agent simulate                                # System B: simulator (:8100)

python -m recon_agent events scenario run S8                  # or use the simulator's Scenarios tab
python -m recon_agent events fault add delay --source WMS --seconds 60
python -m recon_agent events set wms.item_order WL-0015 "Order Qty=800" --note "Route 27 back to original"
python -m recon_agent events clock --rate 60                  # 1 real second = 1 plant minute
python -m recon_agent events status
```

On PowerShell, set `$env:RECON_SOURCE="events"` first. Write-back needs the simulator running at `RECON_CORRECTIONS_URL`. The simulator's delivery pump is what delivers delayed changes, so keep it running when faults are active (CLI commands also deliver what is due when they run).

**Simulator:** sign in with a worklist account (username and password; set `RECON_CORRECTIONS_TOKEN` before sharing the URL). Synthetic data only; never run it next to a production worklist. It has:
- **Tables:** one tab per entity, showing source truth, with an editor whose derived columns are recomputed as the source system would ([events/derive.py](../recon_agent/events/derive.py)).
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

**Tests:** `python -m unittest discover -s tests -v` (49 tests). Each test module isolates itself: no model calls, and no real decisions file.

## 12. Later

- **Kafka-compatible log** (Phase 6): OCI Streaming replaces SQLite behind the same publish/read interface, and real connectors replace the simulator one system at a time.
- **Automatic write-back** for rules at ≥98% acceptance over 8 weeks (product Phase 2), enabled by the productivity manager.
- **Freezer lots** (`wms.freezer_lot`), so R07 can name the lot used.
