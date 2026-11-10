# Data model v3: the 9 Nov business mapping

**Status:** implemented on branch `data-model-9nov`. It replaces the data model built from the five
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

The old R17 statistical outlier check is removed. It needs eight or more runs to work, and the new
data has one work order per SKU.

**Out of scope:** the raw-material checks in the workbook's notes ("if FG is not available check
on hand for components, then inbound for RM"). The item master lists the RM items, but the workbook
has no bill of materials and no RM stock or inbound. The checks can be added once that data exists.

## 5. Dummy data (seed): 3 SKUs, sales date Sat 12 Sep 2026, plant UBD

| SKU | Item | Built from | Seeded state | Flags |
|---|---|---|---|---|
| 76108 | BP WHITE HOT DOG BUNS 8P 14Z | The workbook, value for value | Reconciles: the control | none |
| 950513 | BP BUTTER HOT DOG BUNS 8P 15Z | Forecast FC002 (3,000) from the workbook; orders, stock, work order and price ($4.99) invented in the same shape | MIPS sized the work order to demand left and did not subtract WMS available stock. The plant also mixed one batch more than scheduled. | R05 (over-production 1,140), R11 |
| 76105 | BP WHITE HAMBURGER BUNS 8P 15Z | Forecast FC004 (6,677) from the workbook; the rest invented, priced like 76108 ($4.69) | ODI holds a stale copy of one order. WMS on hand does not balance. One order's ship date is before the wrap. | R02, R10, R12 |

The other seven FG items and the nine RM items in the item master are loaded as master data only.

## 6. Scenarios (simulator), all on the clean SKU 76108 (ten)

| ID | Story | Raises |
|---|---|---|
| S1 | A customer raises a BUSS order; ODI is not refreshed | R02, R04, R05 |
| S2 | A new BUSS order line never reaches ODI | R01, R04, R05 |
| S3 | A customer cancels in BUSS; ODI keeps the line | R03, R04, R05 |
| S4 | WMS allocates more stock to an order; ODI and MIPS keep the old figure | R09, R04, R05 |
| S5 | MIPS re-plans the work order; ODI keeps the old quantity | R05, R07 |
| S6 | A WMS cycle count changes on hand only | R10 |
| S7 | The line runs late: wrapping ends after the requested ship dates | R12 |
| S8 | The work order is deleted in MIPS; ODI still maps the demand to it | R06, R07 |
| S9 | An ODI reload drops the work order from one demand line | R08 |
| S10 | An order arrives from BUSS with a mistyped item code | R13 |

Each scenario is also a regression test (`tests/test_scenarios.py`).

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

1. Commit the open work on `main` (done: `aa4afa9`).
2. Implement on `data-model-9nov`, with all tests passing.
3. Deploy the branch to the demo VM as the main application (worklist :8003, simulator :8103),
   reseed the event log, and smoke-test sign-in, the seed flags and one scenario with write-back.
