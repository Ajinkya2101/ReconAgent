# Simulation Dummy Data, Explained

> **Data model changed (v3, 9 Nov 2026).** The agent now uses the business team's mapping: BUSS orders, Oracle forecast, ODI batch mapping, WMS stock and MIPS work orders, with 13 rules. See [DATA_MODEL_9NOV.md](../DATA_MODEL_9NOV.md) and [EVENT_CONTRACT.md](../EVENT_CONTRACT.md). Where this guide describes MIPS order lines, ODI runs, tickets, rules R01-R19 or carry-forward, it describes v2 and is due for a rewrite.

What the synthetic dataset contains, which columns link the tables, and what happens in the other systems when one system's data changes.

All the dummy data describes **one invented sales day (Sat 7 Nov 2026)** at the UBD (Lehigh Valley) bakery: 12 SKUs (`900101`–`900112`) and 4 customers, spread across 8 tables. Every description and customer name starts with `SIM`.

> **The key idea:** a change in one system does **not** automatically update any other system. When you edit a row, only the calculated columns *in that same row* update. Changes reach other systems only when a script or a person makes them. This is deliberate: the agent exists to find the gaps that open up between systems.

Related files in this folder:

- [Simulation_Dummy_Data.xlsx](Simulation_Dummy_Data.xlsx): every row, one sheet per table, plus *Scenario Changes* and a *Data Dictionary* of all ~140 columns.
- [Simulation_Dummy_Data_Guide.pdf](Simulation_Dummy_Data_Guide.pdf): the full guide, including every rule and scenario.
- [SCENARIO_WALKTHROUGH.md](SCENARIO_WALKTHROUGH.md): every seeded problem and scenario S1–S8 worked through with column names, calculations and the rules that flag them. [SCENARIO_WALKTHROUGH_PLAIN.md](SCENARIO_WALKTHROUGH_PLAIN.md) is the same in plain language.
- [SOURCE_DATA_MAPPING.md](SOURCE_DATA_MAPPING.md): how closely this data matches the client extracts in `data/`, column by column, and how data flows from either source to the worklist.

The source code is [seed.py](../../recon_agent/events/seed.py) (the starting day), [scenarios.py](../../recon_agent/events/scenarios.py) (S1–S8), [derive.py](../../recon_agent/events/derive.py) (calculated columns) and [contract.py](../../recon_agent/events/contract.py) (tables and columns).

## 1. The 8 tables

| System | Table | Rows | Row key | Main quantity columns |
|---|---|---|---|---|
| MIPS | `mips.order_line` | 17 | `Order Line ID` (ML-0001…) | `Order Quantity`, `Source System` (BUSS = sales order, OCloud = supply plan), `Order Moved` |
| ODI | `odi.finished_good` | 14 | `FGEntryID` (FG-0001…) | `NetUnitsOrdered`, `ActualWrappedUnits`, `MIPSOrderQty`, `MIPSBeginningInventoryQty` |
| ODI | `odi.batch` | 6 | `BatchEntryID` (BT-0001…) | `MIPS Sched Batches`, `Act Batches Mixed`, `Final Piece Variance Qty` |
| WMS | `wms.item_order` | 23 | `Item Line ID` (WL-0001…) | `Order Qty`, `Allocated Units Qty` (one row per SKU per customer) |
| WMS | `wms.order` | 4 | `Order Nbr` (9900001–4) | `Order Qty`, `Allocated Qty`, `Loaded Qty`, `Shipped Qty` (one row per customer) |
| WMS | `wms.ticket` | 11 | `Ticket Nbr` (TK-0001…) | `Received Qty`, `Returned Qty`, `Net Received Qty` |
| WMS | `wms.transaction` | 11 | `Txn Nbr` (WT-0001…) | `Txn Type` (RECEIPT / SHIP / RETURN_TO_PLANT / FREEZER_OUT), `Qty` |
| Oracle | `oracle.inventory_txn` | 11 | `TransactionId` (OT-0001…) | `TransactionQuantity`, `ProcessStatus` (POSTED / ERROR) |

That makes 97 rows in total. `Order Line ID`, `FGEntryID`, `BatchEntryID` and `Item Line ID` are *synthetic* keys: the client's extracts don't have them, so the simulation adds them to let a change name the row it updates.

### What each table holds

#### `mips.order_line`: MIPS planning lines (17 rows)

**One row is one line of the production plan:** "for this sales day, we need this many units of this item." There are two kinds of line, told apart by `Source System`:

- **BUSS** lines are real customer sales orders (7 lines).
- **OCloud Region2** lines are the supply plan, the forecast part of demand (10 lines).

An item's total demand is the sum of all its lines.

- **Main columns:** `FG Oracle Ref` (the item), `Order Quantity`, `Source System`, `Schedule Date` / `Offset Sales Date` / `Order Moved` (which sales day the line belongs to), `Units per Container`, `Retail Price`, `Last Modify Date Time`.
- **Notable rows:**
  - `ML-0001` is a BUSS order moved from the day before: `Order Moved` = TRUE and its offset sales date is Fri 6 Nov.
  - `ML-0007` has quantity 0 (900104's blind line).
  - `ML-0013` says 10 units per tray where WMS says 12.
  - Every BUSS line pads `FG Oracle Ref` with a trailing space, as the real extract does.

#### `odi.finished_good`: ODI production results (14 rows)

**One row is one item made in one production run.** It records what MIPS asked the plant to make (`NetUnitsOrdered`) and what the plant actually wrapped (`ActualWrappedUnits`). It also carries MIPS's own view of that run: `MIPSOrderQty` (the sales-order quantity) and `MIPSBeginningInventoryQty` (inventory recorded).

- **Main columns:**
  - `OracleFGNumber` (the item);
  - `OracleTypeCode` (the run or formula code, e.g. `SU900110`);
  - `LineCode`, `ShiftName`, and the production start and end times;
  - the quantity columns above;
  - `ExpectedEndingInventoryQty` (derived: wrapped − ordered).
- **Notable rows:**
  - `FG-0003` is 900102's recovery run, which re-ordered 2,000 after an 800-unit shortfall.
  - `FG-0006` is the zero, no-shift "ghost" left on Line 1 when run `SU900150` moved to Line 2 (`FG-0007`, code `SU900150A`).
  - `FG-0012` has a negative net order (−300), because inventory already covers demand.
  - Items made together share a run code: `SU900130` covers 900103 and 900104.

#### `odi.batch`: ODI mixing records (6 rows)

**One row is the dough mixing for one formula run:** how many batches MIPS scheduled, how many were actually mixed, and how many pieces that should give. There is no item column; a batch links to its runs through `Oracle Type`, which equals the runs' `OracleTypeCode`.

- **Main columns:** `Oracle Type`, `Prod Line`, `Shift`, `MIPS Sched Batches`, `Act Batches Mixed`, `Theo Batch Pieces Qty`, `Final Piece Variance Qty`.
- **Notable rows:** `BT-0003` (`SU900121`, 900102's recovery run) mixed 4 batches against 3 scheduled. All the others mixed exactly as scheduled.

#### `wms.item_order`: WMS order lines by item and customer (23 rows)

**One row is one customer's order for one item:** how much they ordered and how much the warehouse has set aside (allocated) for them. Adding up an item's rows gives the WMS order total that the agent compares with MIPS demand.

- **Main columns:** `Item Nbr`, `Cust Name`, `Original Ord Qty`, `Order Qty`, `Allocated Units Qty`, `Units per Container`. The stack, tray and `Completion%` columns are derived.
- **Notable rows:**
  - `WL-0015` (900107, Route 27) was raised from 800 to 1,000 after MIPS last synced, and only 800 is allocated.
  - `WL-0018` (900109) uses 12 units per tray where MIPS uses 10.
  - 900104 has no rows here at all, because nobody ordered it.

#### `wms.order`: WMS customer orders (4 rows)

**One row is one customer's whole order (the order header).** It carries the customer's totals and the delivery progress: allocated, packed, loaded onto the trailer, shipped. There is one order per customer: two distribution centres (transfer orders) and two direct-store-delivery routes.

- **Main columns:** `Order Nbr`, `Ship to Name`, `Order Type`, `Route Nbr`, `Order Qty`, `Allocated Qty`, `Packed Qty`, `Loaded Qty`, `Shipped Qty`. `Short Qty`, the percentages and `Progress` are derived.
- **Notable rows:**
  - `9900001` (SIM DC NORTH) is loaded and shipped.
  - `9900003` (SIM DSD ROUTE 12) shipped 7,900 with `Loaded Qty` 0: no trailer load scan.
  - The other two are packed and waiting for their trailers.
  - At the start, each header's totals equal the sum of that customer's item lines.

#### `wms.ticket`: WMS receipt tickets (11 rows)

**One row is one delivery of finished goods from a production run into the warehouse.** It records how much was received, and how much was later sent back to the plant. There is one ticket for every run that wrapped product; the three zero-output runs get none.

- **Main columns:**
  - `Ticket Nbr`, `Item Nbr`;
  - `Run Code` (the run's `OracleTypeCode`);
  - `Lot Nbr`;
  - `Received Date Time` (30 minutes after the run ended);
  - `Received Qty`, `Returned Qty`, and `Net Received Qty` (derived: received − returned).
- **Notable rows:** `TK-0010` (900111) is the ticket scenario S5 returns 120 damaged units against.

#### `wms.transaction`: WMS inventory movements (11 rows)

**One row is one stock movement in the warehouse that Oracle must also record:** a receipt, a shipment, a return to the plant or a freezer withdrawal. The seed holds one `RECEIPT` per ticket. The scenarios add the other types: `SHIP` in S8, `RETURN_TO_PLANT` in S5, `FREEZER_OUT` in S7.

- **Main columns:** `Txn Nbr`, `Txn Type`, `Txn Date Time`, `Item Nbr`, `Qty`, `Ticket Nbr` (for receipts and returns), `Order Nbr` (for shipments), `Lot Nbr`, `Reason Code`.
- **Notable rows:** none at the start. All 11 receipts are routine, and Oracle has posted every one.

#### `oracle.inventory_txn`: Oracle Fusion postings (11 rows)

**One row is Oracle's record of one WMS movement.** `SourceReference` holds the WMS `Txn Nbr` it records. `ProcessStatus` says whether it posted (`POSTED`) or failed (`ERROR`, with an `ErrorMessage`). Every WMS movement should have a `POSTED` row here within 10 minutes; if not, rule R18 raises a flag.

- **Main columns:** `TransactionId`, `SourceReference`, `ItemNumber`, `TransactionTypeName`, `TransactionQuantity` (negative for shipments and returns), `PostedDate`, `ProcessStatus`, `ErrorMessage`.
- **Notable rows:**
  - At the start, every receipt is posted 2 minutes after WMS booked it, so nothing is late.
  - Scenario S8 adds one `ERROR` posting and leaves one shipment with no posting at all.

## 2. How the tables link

These are the columns that point from one table to another:

```
                 SKU  (900101 … 900112)
 MIPS  FG Oracle Ref ─┬─ ODI  OracleFGNumber ─┬─ WMS  Item Nbr ─┬─ Oracle  ItemNumber
                      │                       │  (item_order,    │
                      │                       │   ticket, txn)   │
 MIPS  FG BUSS Code ──┴─ ODI  LegacyFGNumber  (e.g. 009101; carried but not used to match)

 ODI finished_good  OracleTypeCode ── ODI batch  Oracle Type ── WMS ticket  Run Code   (formula, e.g. SU900110)
 WMS item_order     Cust Name      ── WMS order  Ship to Name                          (customer)
 WMS ticket         Ticket Nbr     ── WMS transaction  Ticket Nbr                      (rule R19)
 WMS transaction    Txn Nbr        ── Oracle  SourceReference                          (rule R18)
 WMS transaction    Order Nbr      ── WMS order  Order Nbr                             (shipments)
 ticket / txn / Oracle   Lot Nbr / LotNumber                                           (lot)
```

The SKU columns are the main join. The agent rolls each system's rows up to one total per SKU and compares those totals. One quirk is planted on purpose: BUSS lines in MIPS store `FG Oracle Ref` with a trailing space (`"900101 "`). The agent trims it before matching, and rule R15 flags it.

## 3. How the starting day is built

The seed builds each table from the one before it, so the starting day is consistent wherever it should be:

1. **ODI run → WMS ticket.** Each run with `ActualWrappedUnits > 0` gets a ticket. `Received Qty` = wrapped units, `Run Code` = `OracleTypeCode`, and `Received Date Time` = run end + 30 min.
2. **Ticket → WMS transaction.** Each ticket gets a `RECEIPT` with the same `Ticket Nbr`, `Qty` and `Lot Nbr`.
3. **WMS transaction → Oracle posting.** Each receipt gets a `POSTED` row with `SourceReference` = `Txn Nbr`, posted 2 minutes later.
4. **WMS item lines → WMS order header.** Each customer's `Order Qty` / `Allocated Qty` on `wms.order` is the sum of that customer's `wms.item_order` lines.

The seed then gives each SKU one deliberate mismatch between MIPS, ODI and WMS. For example, `900107` has 3,500 units of MIPS demand but 3,700 units of WMS orders, which raises R09 and R10. Two SKUs (`900111`, `900112`) are left clean as controls. Together the planted problems raise exactly 14 flags.

## 4. What happens when you change a row

### Within the same row: updates automatically

[derive.py](../../recon_agent/events/derive.py) recomputes these columns whenever a row is edited, the way the source system itself would:

| Table | You change | Recomputed in the same row |
|---|---|---|
| `odi.finished_good` | `ActualWrappedUnits`, `NetUnitsOrdered` | `ExpectedEndingInventoryQty` (= wrapped − ordered), `TotalPieces`, `TotalActualDamagedUnits` |
| `wms.item_order` | `Order Qty`, `Allocated Units Qty` | `Order/Allocated Stack & Tray Qty`, `Stacks/Trays Remaining`, `Completion%` (GREEN/YELLOW) |
| `wms.order` | `Order Qty`, `Allocated Qty`, … | `Short Qty`, `% Allocated`, `% Loaded`, `% Shipped`, `Progress` |
| `wms.ticket` | `Received Qty`, `Returned Qty` | `Net Received Qty` (= received − returned) |
| `odi.batch` | `Final Piece Variance Qty` | `Final Piece Variance %` |
| `mips.order_line` | anything | `Last Modify Date Time` |
| `wms.transaction`, `oracle.inventory_txn` | anything | nothing |

### Across rows and systems: no automatic update

If you raise `Order Qty` on a WMS item line, the MIPS line, the ODI run and even the WMS order header all stay as they were. Other tables change only in four ways:

1. **A scenario script** changes several tables in one step ([scenarios.py](../../recon_agent/events/scenarios.py)).
2. **Someone edits them by hand** in the simulator. The one helper is **"Rebuild totals from item lines"**, which resets a `wms.order` header to the sum of its item lines.
3. **An approved correction** (write-back) changes the other system. For example, the R09 fix sets the MIPS line to match WMS.
4. **The seed**, but only once, when it builds the starting day.

## 5. Two examples

### S6: WMS changes, MIPS doesn't

Route 12 raises its order.

| Table | Key | Change |
|---|---|---|
| `wms.item_order` | `WL-0023` (900112, SIM DSD ROUTE 12) | `Order Qty` 900 → 1,100. `Allocated Units Qty` stays 900, so `Completion%` becomes YELLOW. |
| `wms.order` | `9900003` | `Order Qty` 7,900 → 8,100. `Short Qty` is recalculated. |
| `mips.order_line` | `ML-0017` (900112) | **not changed**: `Order Quantity` stays 900 |

The agent matches on SKU `900112`: WMS orders 1,100 against MIPS demand of 900. That raises **R09** (order mismatch of 200) and **R10** (WMS allocation 200 short).

### S7: three systems change, one number doesn't

Route 27 needs 300 more units, which WMS takes from the freezer.

| Table | Key | Change |
|---|---|---|
| `mips.order_line` | `ML-0016` (900111) | `Order Quantity` 2,500 → 2,800 |
| `odi.finished_good` | `FG-0013` (900111) | `NetUnitsOrdered` 4,000 → 4,300, but **`ActualWrappedUnits` stays 4,000** |
| `wms.item_order` | `WL-0022` | `Order Qty` and `Allocated Units Qty` → 1,300 |
| `wms.order` | `9900004` | `Order Qty` 4,200 → 4,500, `Allocated Qty` 4,000 → 4,300 |
| `wms.transaction` | new `WT-S7A` | `Txn Type` = FREEZER_OUT, `Qty` 300 |
| `oracle.inventory_txn` | new `OT-S7A` | `SourceReference` = WT-S7A, POSTED |

WMS allocated 300 more units than the plant wrapped, and MIPS can't see where they came from. That raises **R07** (supply from stock MIPS can't see).

## 6. Try it

```bash
python -m recon_agent events seed --reset          # rebuild the starting day
python -m recon_agent events scenario run S6       # play a scenario (S1 … S8)
python -m recon_agent simulate                     # simulator at http://127.0.0.1:8100: edit any row
```

Run the worklist on the event feed (`RECON_SOURCE=events python -m recon_agent serve`) to watch each flag appear as the data changes.
