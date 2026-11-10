# Data model v3: the 9 Nov business mapping

**Status:** the data model of `main` since v0.3.0 (10 Nov 2026), and the only one in the codebase. It
replaces the v2 data model built from the five
client extracts of 12 Sep 2026 (MIPS Order Data, ODI Finished Good / Batch Data, WMS Fill Rate and
Order Summary by Item).

**Reference:** `data/new_dummy_data/` (client material, not committed):
- `BIMBO_MIPS_WMS_ODI_Data_9th_NOV (1).xlsx`: the six source sheets for one SKU (76108). Revised on
  10 Nov: the BUSS price column is now `Price in $` and holds $4.69 on every line, and the order served
  from allocated stock no longer carries batch counts in ODI.
- `BIMBO_Column_Meanings.docx`: what each column means.
- `Demand_to_Delivery_Flow_76108.docx`: the flow and the worked example.

## 1. Why the model changed

The old model followed the client's raw extracts. It had 157 MIPS lines, 86 ODI runs, inferred
inventory, recovery runs, ghost runs and 19 rules. The business team has replaced it with one simple
demand-to-delivery chain:

```
Work Order Qty = (Sales Orders + Forecast) − Allocated Stock − Available Stock
76108:  (14,339 + 41,769) − 473 − 7,000 = 48,635
```

The agent now checks that every system carries its part of that chain:
- BUSS has the orders.
- Oracle has the forecast.
- ODI maps each demand line to a work order.
- WMS holds the stock.
- MIPS plans the demand that is left and sizes the work order to the shortfall.

The model stays as simple as the workbook. The only additions are synthetic row keys, wherever a sheet has
no natural one. The dollar ranking uses the BUSS `Price in $` of the SKU's orders ($4.69 for 76108), and the
default retail price ($3.99 a unit) only when a SKU's orders carry no price.

The workbook has inconsistencies of its own that need answers from the business team, for example ODI ship
dates a day or two before BUSS's and wrapping that ends before the work order dates. They are listed, with
the values, in [Data_Inconsistencies_9Nov.pdf](Data_Inconsistencies_9Nov.pdf). The seed keeps them as they are,
and no rule is added for them until the business team answers.

## 2. Source entities (event contract v4)

One entity per sheet. Column names are the business team's own, with whitespace normalised. For
example, `Quantity ` becomes `Quantity` and `Allocated quantity  for SO ` becomes
`Allocated quantity for SO`. Their spelling stays (`Forcast`).

| Entity | System | Sheet | Key |
|---|---|---|---|
| `oracle.item` | Oracle | ITEM Master | Item Number |
| `buss.sales_order` | BUSS | Sales Orders | SO Line ID *(synthetic: Order Number-Order Line)* |
| `oracle.forecast` | Oracle | Oracle Planning & Forecast | Forecast ID |
| `odi.batch_mapping` | ODI | ODI Batch Data Entry Mapping | ODI Entry ID *(synthetic)* |
| `wms.inventory` | WMS | WMS | Inventory ID *(synthetic)* |
| `mips.work_order` | MIPS | MIPS | Work Order Number |

The same mappers read the workbook (Excel feed) and the event log (event feed). The Excel feed reads
`BIMBO_MIPS_WMS_ODI_Data*.xlsx` anywhere under `data/`.

## 3. Reconciled position (one per SKU)

| Step | Value | 76108 |
|---|---|---|
| 1 | Sales orders, BUSS (all lines) | 14,339 |
| 1 | Forecast, Oracle Planning | 41,769 |
| 2 | **Total demand** | 56,108 |
| 3 | − Allocated in WMS | 473 |
| 3 | **Demand left**: what MIPS must show as *Forecast / Order Quantity* | 55,635 |
| 4 | − Available in WMS | 7,000 |
| 4 | **Shortfall**: what the MIPS work order must make | 48,635 |
| 9 | Check: work order + available + allocated = demand | 56,108 |

When available stock covers the demand that is left, the shortfall is 0 and no work order is needed.
All on-hand stock is kept in WMS only, so the surplus stays in WMS. There is no carry-forward step any
more.

## 4. Rules (13, one per step of the flow)

| Rule | Step | Finds | Started in | Auto-correction |
|---|---|---|---|---|
| R01 | Capture demand | A BUSS order line or Oracle forecast that is missing from the ODI mapping | Integration (BUSS/Oracle → ODI) | Insert the ODI row |
| R02 | Capture demand | An ODI quantity that differs from its BUSS order or Oracle forecast | Integration | Set the ODI quantity |
| R03 | Capture demand | An ODI row whose order or forecast no longer exists (cancelled) | Integration | Delete the ODI row |
| R04 | Remove allocated stock | MIPS *Forecast / Order Quantity* ≠ demand − allocated | MIPS (or Integration when MIPS followed a stale ODI) | Set the MIPS quantity |
| R05 | Shortfall → work order | MIPS work-order quantity ≠ shortfall (over- or under-production) | MIPS | Set the work-order quantity |
| R06 | Create work order | A shortfall with no MIPS work order | MIPS | Create the work order |
| R07 | Work order → ODI | The ODI work order (number or quantity) differs from MIPS | Integration (MIPS → ODI) | Set the quantity on every ODI row of the work order |
| R08 | Allocate or produce | A demand line with neither allocated stock nor a work order | ODI | Link the row to the SKU's work order |
| R09 | Remove allocated stock | ODI allocated quantity ≠ WMS allocated quantity | Integration (WMS → ODI) | No (a person decides which order holds the stock) |
| R10 | Check stock | WMS on hand ≠ available + allocated | WMS | No (the count decides which value is wrong) |
| R11 | Produce | Batches mixed ≠ MIPS scheduled batches | ODI | No |
| R12 | Move FG to WMS | Finished goods wrapped after the requested ship date | ODI | No |
| R13 | Master data | Item or BUSS code differs between the item master, BUSS, MIPS and ODI | Undetermined (the item master decides) | No |

These parts stay as they were: severity, priority (`$ at risk × severity × learned confidence`),
accountability records, AI explanations with figure checks and audit, planner decisions, claims,
acknowledgments and the write-back state machine.

## 4a. The ML layer (R14-R17): today against the item's own history

The 13 rules check that the systems agree with each other. They cannot see a value that every system agrees on but
that is wrong: an order keyed with an extra zero that BUSS, ODI and MIPS all carried through. The ML layer
(`recon_agent/anomaly.py`) compares the current sales day with the same item's earlier sales days and flags what lies
far outside its normal range.

| Rule | Detector | Compares | Against |
|---|---|---|---|
| R14 | Unusual order quantity | Each BUSS order line today | The same customer's orders of the item on earlier sales days |
| R15 | Forecast bias | The Oracle forecast today | The item's forecasts on earlier sales days |
| R16 | Production anomaly | Today's work order's wrap duration (from ODI) | The item's earlier work orders |
| R17 | Inventory drift | The latest change in WMS on hand | The row's earlier changes |

**Method.** The modified z-score of Iglewicz and Hoaglin: z = (value - median) / scale, the scale being 1.4826 × the
median absolute deviation (MAD), or 1.2533 × the mean absolute deviation when the MAD is 0. Median and MAD are robust,
so one earlier outlier cannot hide the next. A value is flagged at |z| >= 3.5 (`RECON_ML_Z`) and is High at |z| >= 7;
nothing is judged with fewer than 6 earlier values (`RECON_ML_MIN_HISTORY`).

**History.** Reconciliation reads only the current sales day: the newest date among the BUSS orders and Oracle forecasts
(`ingest.split_current`). Rows of earlier days in the same tables are the history, and raise no rule flags. WMS rows
carry no date, so their history is the row's own change log in the event feed: one snapshot each time on hand changes.
The business workbook holds a single day, so it raises no ML flags until it carries several days.

**Flags.** ML flags are ordinary flags: ranked, explained by the AI, decided and audited like the rest. The explanation
is labelled "From the ML layer". Accountability names the system where the value entered, with the basis *likely* (an
outlier is not proof of an error), and the failure type *Statistical anomaly*. There is no automatic correction: a
person confirms the value first.

The v2 R17 (outliers across 86 production runs of one day) was removed with v3; R14-R17 replace it with history per item.

**Out of scope:** the raw-material checks in the workbook's notes ("if FG is not available check
on hand for components, then inbound for RM"). The item master lists the RM items, but the workbook
has no bill of materials and no RM stock or inbound. The checks can be added once that data exists.

## 5. Dummy data (seed): 6 SKUs, sales date Sat 12 Sep 2026, plant UBD

| SKU | Item | Built from | Seeded state | Flags |
|---|---|---|---|---|
| 76108 | BP WHITE HOT DOG BUNS 8P 14Z | The workbook, value for value | Reconciles: the control | none |
| 950513 | BP BUTTER HOT DOG BUNS 8P 15Z | Forecast FC002 (3,000) from the workbook; orders, stock, work order and price ($4.99) invented in the same shape | MIPS sized the work order to demand left and did not subtract WMS available stock. The plant also mixed one batch more than scheduled. | R05 (over-production 1,140), R11 |
| 76105 | BP WHITE HAMBURGER BUNS 8P 15Z | Forecast FC004 (6,677) from the workbook; the rest invented, priced like 76108 ($4.69) | ODI holds a stale copy of one order. WMS on hand does not balance. One order's ship date is before the wrap. | R02, R10, R12 |
| 950514 | BP BUTTER HAMBURGER BUNS 8P 16Z | Forecast FC005 (6,544) from the workbook; the rest invented ($4.99) | The BUSS -> ODI interface dropped Colonie's 1,250-unit order (MIPS planned it). | R01 (High, $6,238) |
| 150080 | ST DUTCH COUNTRY 100 WW BREAD 4.49 24Z | Forecast FC007 (35,544) from the workbook; the rest invented ($4.49) | WMS has 1,000 allocated to orders; ODI records only Chicopee's 600. | R09 (400 units, $1,796) |
| 77442 | GV WHITE SANDWICH BREAD 20Z | Forecast FC008 (34,578) from the workbook; the rest invented ($2.98) | MIPS re-planned the work order to 37,628; ODI still carries 35,000. | R07 (2,628 units, $7,831) |

On the starting day the worklist shows **5 SKUs** (76108 reconciles) with **8 open flags** and **$32,810** at risk.

The other seven FG items and the nine RM items in the item master are loaded as master data only.

**History for the ML layer:** the same three SKUs on the 8 earlier Saturdays (18 Jul to 5 Sep 2026):
each customer's orders, the forecast, a completed work order and its ODI wrap row, varying from week to week by a fixed
pattern whose median is today's value (orders about ±8%, forecasts ±4%, wrap time ±2.5%; BBU_LANCASTER NY CMB orders swing
from 90 to 1,000). WMS stock gets one count a day for the 8 days before the sales day. Today's values sit in the middle of
their history (|z| below 0.6), so the seed raises no ML flags. The seed publishes 508 events.

## 6. Scenarios (simulator), all on the clean SKU 76108 (fourteen)

| ID | Story | Raises |
|---|---|---|
| S1 | A customer raises a BUSS order; ODI is not refreshed | R02, R04, R05 |
| S2 | A new BUSS order line never reaches ODI | R01, R04, R05 |
| S3 | A customer cancels in BUSS; ODI keeps the line | R03, R04, R05 |
| S4 | WMS allocates more stock to an order; ODI and MIPS keep the old figure | R09, R04 |
| S5 | MIPS re-plans the work order; ODI keeps the old quantity | R05, R07 |
| S6 | A WMS cycle count changes on hand only | R10 |
| S7 | The line runs late: wrapping ends after the requested ship dates | R12 |
| S8 | The work order is deleted in MIPS; ODI still maps the demand to it | R06, R07 |
| S9 | An ODI reload drops the work order from one demand line | R08 |
| S10 | An order arrives from BUSS with a mistyped item code | R13 |

The ML layer's scenarios keep every system consistent, so no rule fires and only the ML flag shows what the rules miss.
The simulator labels them "ML layer".

| ID | Story | Raises |
|---|---|---|
| S11 | An order keyed with an extra zero (7,200 -> 72,000) passes through BUSS, ODI and MIPS | R14 (z +110) |
| S12 | Oracle doubles 76108's forecast (41,769 -> 83,538); ODI and MIPS follow it | R15 (z +19) |
| S13 | Line 1 runs at half speed: the wrap takes 9 h 44 min instead of 4 h 34 min | R16 (z +32) |
| S14 | WMS books 3,000 fewer on hand and available overnight; MIPS re-plans; the books balance | R17 (z -14) |

Each scenario is also a regression test (`tests/test_scenarios.py`; the ML layer in `tests/test_anomaly.py`).

## 7. What changes in the code

| Area | Change |
|---|---|
| `events/contract.py` | v3: the six entities above; sources BUSS, ORACLE, MIPS, ODI, WMS. v4 (10 Nov): `Price` is renamed `Price in $` |
| `ingest.py` | New records and mappers; one workbook reader (six sheets, headers normalised) |
| `position.py` | The position in section 3 |
| `detectors.py` | The 13 rules in section 4; `anomaly.py` is removed |
| `accountability.py` | One record per new rule; BUSS is listed as a system |
| `engine.py` | New KPIs (demand, stock, shortfall, over- and under-production units) |
| `events/seed.py`, `scenarios.py`, `derive.py`, `projector.py` | New seed and scenarios; no derived columns; simpler projection |
| `corrections.py` + simulator `/oic/corrections` | A correction can update several rows (R07) or delete a row (R03) |
| `web/store.py`, `_detail.html` | New ledger (the flow equation), systems table and source records |
| `ai.py`, `report.py` | Prompt, tools, owners and workbook sheets for the new model |
| `config.py` | Data source glob; no carry-forward rules |
| Tests | Rewritten for the new data. Security, sign-in, log, clock and fault tests are kept. |
| Docs | README, EVENT_CONTRACT, CHANGELOG, ReconAgent.txt rule list. The user and systems guides describe v2 until they are rewritten. |

## 8. Delivery

Done. The model was built on the branch `data-model-9nov` (9 to 10 Nov 2026), deployed to the demo VM
(worklist :8003, simulator :8103), and released as v0.3.0, when `main` moved to it and the branch was
deleted. See [REPOSITORY_HISTORY.md](../engineering/REPOSITORY_HISTORY.md).
