# Scenario Walkthrough: Worked Examples and Calculations

Every problem in the simulation's dummy data, worked through from the source rows to the flag. For each one, this document gives:

- the rows and column values involved;
- the arithmetic the engine does;
- the rule conditions that fire (and the nearby ones that don't);
- the flag it raises, with severity, units, dollar value and priority;
- **the exact sheet and row in [Simulation_Dummy_Data.xlsx](Simulation_Dummy_Data.xlsx)** for every row mentioned, so you can point at it during a demo.

It covers:

- **Part A:** the 14 flags already present in the starting day (the seed).
- **Part B:** the eight live scenarios, S1–S8.
- **Part C:** a summary of everything.

All numbers come from the engine itself. Each scenario was replayed on a fresh seeded event log with AI explanations off. To reproduce one: `python -m recon_agent events seed --reset`, then `python -m recon_agent events scenario run S1`.

A plain-language version of this document is [SCENARIO_WALKTHROUGH_PLAIN.md](SCENARIO_WALKTHROUGH_PLAIN.md). The tables themselves are described in [DUMMY_DATA_EXPLAINED.md](DUMMY_DATA_EXPLAINED.md).

---

## Demo reference: finding rows in the workbook

References in this document look like **`MIPS Order Lines` r6**, meaning sheet *MIPS Order Lines*, Excel row 6.

**How the workbook is laid out**

- **Data sheets:** every data sheet has its title on rows 1–2, the column headers on **row 4**, and data from **row 5**.
- **The quick rule:** on the eight data sheets, **Excel row = the number in the row ID + 4**. So ML-0013 is row 17, FG-0006 is row 10, WL-0023 is row 27 and TK-0010 is row 14. On *WMS Orders*, 9900001–9900004 are rows 5–8.
- **What the data sheets show:** the **starting day** only, before any scenario. Rows that a scenario *inserts* (ML-S2A, FG-S2A, FG-S3A, FG-S4A, WT-S5A, OT-S5A, WT-S7A, OT-S7A, WT-S8A–F, OT-S8B–F) do not appear on the data sheets. Every scenario change, inserts and updates alike, is listed on the **`Scenario Changes`** sheet (rows 5–34).
- **Flags:** the 14 starting flags are on **`Seed Flags`** (rows 5–18, in worklist order), and the eight scenarios are on **`Scenarios`** (rows 5–12).

### Rows by item

| Item | Description | `MIPS Order Lines` | `ODI Finished Goods` | `WMS Item Orders` | `Seed Flags` |
|---|---|---|---|---|---|
| 900101 | SIM 100% WW BREAD 24Z | r5–r7 (ML-0001–0003) | r5 (FG-0001) | r5–r7 (WL-0001–0003) | r7 (R01) |
| 900102 | SIM POTATO BREAD 22Z | r8–r9 (ML-0004–0005) | r6–r7 (FG-0002–0003) | r8–r10 (WL-0004–0006) | r5 (R05), r15 (R14) |
| 900103 | SIM WHITE ROUND TOP 20Z | r10 (ML-0006) | r8 (FG-0004) | r11–r12 (WL-0007–0008) | r9 (R02) |
| 900104 | SIM WHITE ROUND TOP 2P 40Z | r11 (ML-0007) | r9 (FG-0005) | none | r17 (R03) |
| 900105 | SIM SQUARE WHEAT 24Z | r12 (ML-0008) | r10–r11 (FG-0006–0007) | r13–r14 (WL-0009–0010) | r11 (R04) |
| 900106 | SIM HOT DOG BUNS 8P 12Z | r13 (ML-0009) | r12 (FG-0008) | r15–r16 (WL-0011–0012) | r8 (R07) |
| 900107 | SIM HAMBURGER BUNS 8P 12Z | r14–r15 (ML-0010–0011) | r13 (FG-0009) | r17–r19 (WL-0013–0015) | r13 (R09), r14 (R10) |
| 900108 | SIM HONEY WHEAT 20Z | r16 (ML-0012) | r14 (FG-0010) | r20–r21 (WL-0016–0017) | r12 (R06) |
| 900109 | SIM ITALIAN BREAD 20Z | r17 (ML-0013) | r15 (FG-0011) | r22 (WL-0018) | r16 (R11) |
| 900110 | SIM MULTIGRAIN 24Z | r18 (ML-0014) | r16 (FG-0012) | r23 (WL-0019) | r10 (R08) |
| 900111 | SIM BUTTER TOP 20Z | r19–r20 (ML-0015–0016) | r17 (FG-0013) | r24–r26 (WL-0020–0022) | clean |
| 900112 | SIM CINNAMON RAISIN 16Z | r21 (ML-0017) | r18 (FG-0014) | r27 (WL-0023) | clean |

**Other sheets**

- **`ODI Batches`:** BT-0001–0006 are r5–r10. The flagged one is BT-0003, r7.
- **`WMS Orders`:** 9900001 North r5, 9900002 South r6, 9900003 Route 12 r7, 9900004 Route 27 r8.
- **`WMS Receipt Tickets`, `WMS Transactions` and `Oracle Inventory Postings`:** TK-/WT-/OT-0001–0011 are r5–r15. TK-0010, used in S5, is r14.
- **Plant-level flags on `Seed Flags`:** R12 is r6, R15 is r18.

### Scenario changes by scenario

| Scenario | `Scenarios` row | `Scenario Changes` rows |
|---|---|---|
| S1 | r5 | r5 |
| S2 | r6 | r6–r8 |
| S3 | r7 | r9–r10 |
| S4 | r8 | r11–r12 |
| S5 | r9 | r13–r14 |
| S6 | r10 | r15–r16 |
| S7 | r11 | r17–r22 |
| S8 | r12 | r23–r34 |

---

## 0. How to read the calculations

### 0.1 The quantities the engine builds per item

[position.py](../../recon_agent/position.py) gathers each item's rows from every system into one *position* and computes:

| Quantity | Formula | From columns |
|---|---|---|
| **Demand** | sum of all the item's MIPS lines | `mips.order_line.Order Quantity` |
| **Sales-day demand** | Demand − lines moved to the prior day | `Order Moved` = TRUE lines excluded |
| **Sales-order demand** | Demand on BUSS lines only | `Source System` = BUSS |
| **Active run** | a run with a non-zero ordered or wrapped quantity | `NetUnitsOrdered`, `ActualWrappedUnits` |
| **Net ordered** | sum over the item's runs | `odi.finished_good.NetUnitsOrdered` |
| **Wrapped** | sum over the item's runs | `ActualWrappedUnits` |
| **Implied inventory** | inventory MIPS netted out before sending demand to production: Demand at order − the *first* active run's order. Never below 0. | derived |
| **Demand at order** | the demand when the first active run's order was last set. The seed is the opening snapshot, so on the untouched day it equals Demand. A later MIPS change (edit, new or deleted line) is new demand, not inventory; re-ordering the first run takes it up | event history |
| **Recorded inventory** | sum over the item's runs | `MIPSBeginningInventoryQty + MIPSInventoryAdjustmentQty` |
| **Consolidated requirement** | Demand − Implied inventory (≥ 0): what the plant should make | derived |
| **First-line-only requirement** | Demand − min(Implied inventory, first line's quantity): what MIPS asks for if it nets inventory against the first line only | derived |
| **First-line risk** | First-line-only requirement − Consolidated requirement (≥ 0) | derived |
| **Returns** | units WMS returned to the plant: per ticket, the larger of the RETURN_TO_PLANT transactions and the ticket's `Returned Qty` | `wms.transaction.Qty`, `wms.ticket.Returned Qty` |
| **Net requirement** | Demand − Implied inventory − Wrapped + Returns. Positive = still to supply; negative = surplus | derived |
| **WMS ordered / allocated** | sum over the item's customer lines | `wms.item_order.Order Qty`, `Allocated Units Qty` |
| **Unit price** | the first non-zero MIPS price, else $3.99 | `Retail Price` |

### 0.2 The rules used in this document

| Rule | Fires when | Units on the flag | Severity |
|---|---|---|---|
| **R01** Inventory applied to first line only | 2+ MIPS lines and First-line risk > 0 | First-line risk | High at ≥ 1,000, else Medium |
| **R02** Inventory netted without audit trail | Implied inventory > 0, Recorded inventory = 0, no R01 risk, no negative run | Implied inventory | Medium |
| **R03** Blind line | a run with ordered = 0 and wrapped = 0 that is not a ghost (it has a shift) | 0 | Low |
| **R04** Ghost of a moved run | a zero run with **no shift**, whose formula (ignoring a trailing "A") produced output on another line | 0 | Medium |
| **R05** Recovery run over-built | 2+ active runs and Over-build ≥ 100, where Over-build = the larger of (Recovery ordered − Still needed after run 1) and (Wrapped − Consolidated requirement) | Over-build | High at ≥ 1,000, else Medium |
| **R06** Surplus not carried forward | exactly 1 active run, surplus (−Net requirement) ≥ 50, no negative run | surplus | Medium at ≥ 1,000, else Low |
| **R07** Filled from untracked stock | Net requirement ≥ 20 and WMS allocated ≥ Sales-day demand | Net requirement | High at ≥ 500, else Medium |
| **R08** Negative net order | any run with `NetUnitsOrdered` < 0 | −(sum of negative orders) | Medium |
| **R09** MIPS vs WMS order mismatch | WMS ordered ≠ Sales-day demand | WMS ordered − Sales-day demand | Medium |
| **R10** WMS allocation short | WMS allocated < WMS ordered | the shortfall | Medium |
| **R11** Units-per-container conflict | MIPS `Units per Container` (most common on container `01` lines) ≠ WMS `Units per Container` | 0 | Medium |
| **R12** Shipped without load scan | any `wms.order` with `Shipped Qty` > 0 and `Loaded Qty` = 0 | shipped units on those orders | High |
| **R13** Report totals differ | sum of `wms.order.Order Qty` ≠ sum of `wms.item_order.Order Qty` | the difference | Medium |
| **R14** Batches differ from schedule | `Act Batches Mixed` ≠ `MIPS Sched Batches` (scheduled > 0) | 0 | Low |
| **R15** Padded item keys | any MIPS `FG Oracle Ref` with leading or trailing spaces | 0 | Low |
| **R16** ODI's copy of the order is stale | sum of the item's `MIPSOrderQty` ≠ Sales-order demand | the difference | Medium |
| **R18** WMS movement not in Oracle | a `wms.transaction` with no POSTED `oracle.inventory_txn` whose `SourceReference` is its `Txn Nbr`, 10+ plant minutes old | `Qty` | High |
| **R19** Return not booked on the ticket | RETURN_TO_PLANT units against a ticket > that ticket's `Returned Qty` | the gap | High at ≥ 500, else Medium |

### 0.3 Dollars and priority

- **Dollar value** = |units| × unit price.
- **Priority** = (dollar value + base value) × severity weight × rule confidence.
  - Severity weight: High 3, Medium 2, Low 1.
  - Base value: 50 by default; R14 500; R04 1,000; R18 2,000; R12 5,000.
  - Confidence is 1.0 here, because no planner decisions exist in the simulation.
- **Dollar exposure** (the worklist's headline figure) adds the dollar values of R01, R05, R06, R07, R08, R09, R10 and R19 only.

---

## Part A. The starting day: 14 flags

The seed builds Saturday 7 Nov 2026 with 12 items. Ten each carry one designed problem, and two (900111, 900112) are clean. Flags are listed in the order the worklist ranks them, which is also the order of the `Seed Flags` sheet. **Dollar exposure: $17,500**, which is R01 $4,690 + R05 $5,628 + R06 $1,796 + R07 $2,513 + R08 $1,437 + R09 $718 + R10 $718.

> The `Seed Flags` sheet's total also includes R02's $3,192. The worklist leaves R02 out of dollar exposure (see the note on `Seed Flags` r20).

### A1. 900102 SIM POTATO BREAD 22Z → **R05-900102** (High) and **R14-SU900121-Line 2** (Low)

*Flags: `Seed Flags` r5 (R05) and r15 (R14).*

**Rows**

| Table | Row | Excel | Columns |
|---|---|---|---|
| `mips.order_line` | ML-0004 | `MIPS Order Lines` r8 | `Source System` BUSS, `Order Quantity` 1,000 |
| `mips.order_line` | ML-0005 | `MIPS Order Lines` r9 | `Source System` OCloud Region2, `Order Quantity` 5,000 |
| `odi.finished_good` | FG-0002 | `ODI Finished Goods` r6 | `OracleTypeCode` SU900120, Line 1, Shift 1, `NetUnitsOrdered` 6,000, `ActualWrappedUnits` 5,200, `MIPSOrderQty` 1,000 |
| `odi.finished_good` | FG-0003 (recovery run) | `ODI Finished Goods` r7 | `OracleTypeCode` SU900121, Line 2, Shift 3, `NetUnitsOrdered` 2,000, `ActualWrappedUnits` 2,000 |
| `odi.batch` | BT-0003 | `ODI Batches` r7 | `Oracle Type` SU900121, `MIPS Sched Batches` 3, `Act Batches Mixed` 4, `Theo Batch Pieces Qty` 2,000 |
| `wms.item_order` | WL-0004–WL-0006 | `WMS Item Orders` r8–r10 | ordered and allocated 3,000 + 2,000 + 1,000 = 6,000 |

**Calculation**

```
Demand                    = 1,000 + 5,000                = 6,000
Active runs               = 2 (FG-0002, FG-0003)
Implied inventory         = Demand − first run's order   = 6,000 − 6,000 = 0
Consolidated requirement  = 6,000 − 0                    = 6,000
Wrapped                   = 5,200 + 2,000                = 7,200
Still needed after run 1  = 6,000 − 5,200                = 800
Recovery ordered beyond   = 2,000 − 800                  = 1,200
Wrapped beyond            = Wrapped − Consolidated       = 7,200 − 6,000 = 1,200
Over-build                = larger of the two            = 1,200   (≥ 100 → R05)
```

If MIPS demand is raised to 7,200 after these runs (ML-0005 to 6,200), demand at order stays 6,000, so implied inventory stays 0. Still needed after run 1 becomes 2,000, the recovery run's 2,000 was exactly right, and the net requirement is 0: R05 clears and R09 (MIPS 7,200 against WMS 6,000) appears.

**R05:** 1,200 units, High (≥ 1,000). 1,200 × $4.69 = **$5,628**. Priority (5,628 + 50) × 3 = 17,034. The recovery run ordered 2,000 when only 800 were missing.

**R14:** BT-0003 mixed 4 batches against 3 scheduled. Extra pieces ≈ 2,000 ÷ 4 × (4 − 3) = **500**. Low, 0 units, priority (0 + 500) × 1 = 500.

**Not raised:**
- R06, because there are 2 active runs.
- R16, because `MIPSOrderQty` 1,000 + 0 = sales-order demand 1,000.
- R09, because WMS 6,000 = sales-day demand 6,000.

### A2. Plant: Route 12 shipped without a load scan → **R12-PLANT** (High)

*Flag: `Seed Flags` r6.*

**Rows:** `wms.order` 9900003 (SIM DSD ROUTE 12) is **`WMS Orders` r7**: `Order Qty` 7,900, `Allocated Qty` 7,900, `Shipped Qty` 7,900, `Loaded Qty` **0**.

For comparison:
- 9900001 North (r5) has Loaded 18,200 = Shipped 18,200.
- 9900002 (r6) and 9900004 (r8) have not shipped (Shipped 0).

**Calculation:** orders with Shipped > 0 and Loaded = 0 → 1 of 4. Units = 7,900.

**R12:** High, 7,900 units, $0 (plant-level, no item price), priority (0 + 5,000) × 3 = **15,000**.

### A3. 900101 SIM 100% WW BREAD 24Z → **R01-900101** (High)

*Flag: `Seed Flags` r7.*

**Rows**

| Table | Row | Excel | Columns |
|---|---|---|---|
| `mips.order_line` | ML-0001 | `MIPS Order Lines` r5 | BUSS, `Order Quantity` 1,300, `Order Moved` TRUE, `Offset Sales Date` 2026-11-06 |
| `mips.order_line` | ML-0002 | `MIPS Order Lines` r6 | BUSS, 2,400 |
| `mips.order_line` | ML-0003 | `MIPS Order Lines` r7 | OCloud Region2, 7,600 |
| `odi.finished_good` | FG-0001 | `ODI Finished Goods` r5 | SU900110, `NetUnitsOrdered` 9,000, `ActualWrappedUnits` 9,000, `MIPSOrderQty` 3,700 |
| `wms.item_order` | WL-0001–WL-0003 | `WMS Item Orders` r5–r7 | 5,000 + 3,000 + 2,000 = 10,000 ordered and allocated |

**Calculation**

```
Demand                       = 1,300 + 2,400 + 7,600                   = 11,300
Sales-day demand             = 11,300 − 1,300 (moved)                  = 10,000
Implied inventory            = Demand − Net ordered (1 active run)    = 11,300 − 9,000 = 2,300
Consolidated requirement     = 11,300 − 2,300                          = 9,000
First-line-only requirement  = 11,300 − min(2,300, 1,300)              = 10,000
First-line risk              = 10,000 − 9,000                          = 1,000   (> 0, 3 lines → R01)
Net requirement              = 11,300 − 2,300 − 9,000 + 0              = 0
```

**R01:** 1,000 units, High (≥ 1,000). 1,000 × $4.69 = **$4,690**. Priority (4,690 + 50) × 3 = 14,220.

**Not raised:**
- R02: it is excluded when R01 applies.
- R09: WMS 10,000 = sales-day demand 10,000, because the moved line is excluded.
- R16: `MIPSOrderQty` 3,700 = BUSS lines 1,300 + 2,400.

### A4. 900106 SIM HOT DOG BUNS 8P 12Z → **R07-900106** (High)

*Flag: `Seed Flags` r8.*

**Rows**

| Table | Row | Excel | Columns |
|---|---|---|---|
| `mips.order_line` | ML-0009 | `MIPS Order Lines` r13 | OCloud Region2, 4,000 |
| `odi.finished_good` | FG-0008 | `ODI Finished Goods` r12 | SU900160, `NetUnitsOrdered` 4,000, `ActualWrappedUnits` **3,300** |
| `wms.item_order` | WL-0011, WL-0012 | `WMS Item Orders` r15–r16 | 2,400 + 1,600 = 4,000 ordered and **allocated** |

**Calculation**

```
Demand = 4,000;  Implied inventory = 4,000 − 4,000 = 0;  Wrapped = 3,300
Net requirement   = 4,000 − 0 − 3,300 + 0  = 700   (≥ 20)
WMS allocated 4,000 ≥ Sales-day demand 4,000      → R07
```

**R07:** 700 units, High (≥ 500). 700 × $3.59 = **$2,513**. Priority (2,513 + 50) × 3 = 7,689. WMS filled 700 units that the plant never made, which means they came from stock MIPS cannot see.

### A5. 900103 SIM WHITE ROUND TOP 20Z → **R02-900103** (Medium)

*Flag: `Seed Flags` r9.*

**Rows**

| Table | Row | Excel | Columns |
|---|---|---|---|
| `mips.order_line` | ML-0006 | `MIPS Order Lines` r10 | OCloud Region2, 5,000 |
| `odi.finished_good` | FG-0004 | `ODI Finished Goods` r8 | SU900130, `NetUnitsOrdered` **4,200**, `ActualWrappedUnits` 4,200, `MIPSBeginningInventoryQty` **0** |
| `wms.item_order` | WL-0007, WL-0008 | `WMS Item Orders` r11–r12 | 2,500 + 2,500 = 5,000 |

**Calculation**

```
Implied inventory  = 5,000 − 4,200 = 800   (> 0)
Recorded inventory = 0 + 0         = 0     (= 0)
R01 risk = 0 (only one line);  no negative run           → R02
```

**R02:** 800 units, Medium. 800 × $3.99 = **$3,192**. Priority (3,192 + 50) × 2 = 6,484. MIPS took 800 units of inventory into account, but nothing records where they are.

### A6. 900110 SIM MULTIGRAIN 24Z → **R08-900110** (Medium)

*Flag: `Seed Flags` r10.*

**Rows**

| Table | Row | Excel | Columns |
|---|---|---|---|
| `mips.order_line` | ML-0014 | `MIPS Order Lines` r18 | OCloud Region2, 1,200 |
| `odi.finished_good` | FG-0012 | `ODI Finished Goods` r16 | SU900200, `NetUnitsOrdered` **−300**, `ActualWrappedUnits` 0, `MIPSBeginningInventoryQty` 1,500 |
| `wms.item_order` | WL-0019 | `WMS Item Orders` r23 | 1,200 ordered and allocated |

**Calculation**

```
Any run with NetUnitsOrdered < 0 → yes (−300)                     → R08
Excess = −(−300) = 300      (inventory 1,500 − demand 1,200 = 300)
```

**R08:** 300 units, Medium. 300 × $4.79 = **$1,437**. Priority (1,437 + 50) × 2 = 2,974.

**Not raised:** R02 and R06, because both are excluded when a run has a negative net order.

### A7. 900105 SIM SQUARE WHEAT 24Z → **R04-SU900150-900105** (Medium)

*Flag: `Seed Flags` r11.*

**Rows**

| Table | Row | Excel | Columns |
|---|---|---|---|
| `odi.finished_good` | FG-0006 (the ghost) | `ODI Finished Goods` r10 | `OracleTypeCode` **SU900150**, Line 1, `ShiftName` **empty**, `NetUnitsOrdered` 0, `ActualWrappedUnits` 0 |
| `odi.finished_good` | FG-0007 (where the run went) | `ODI Finished Goods` r11 | `OracleTypeCode` **SU900150A**, **Line 2**, Shift 1, 3,000 / 3,000 |
| `mips.order_line` | ML-0008 | `MIPS Order Lines` r12 | BUSS, 3,000 |
| `wms.item_order` | WL-0009, WL-0010 | `WMS Item Orders` r13–r14 | 1,800 + 1,200 = 3,000 |

**Calculation:** FG-0006 has zero ordered and wrapped, and no shift. "SU900150A" without its trailing "A" is "SU900150", and it ran on a different line, so FG-0006 is a ghost → R04.

**R04:** Medium, 0 units, priority (0 + 1,000) × 2 = **2,000**. Demand 3,000 = wrapped 3,000, so the numbers tie; the flag is about the leftover record.

### A8. 900108 SIM HONEY WHEAT 20Z → **R06-900108** (Low)

*Flag: `Seed Flags` r12.*

**Rows**

| Table | Row | Excel | Columns |
|---|---|---|---|
| `mips.order_line` | ML-0012 | `MIPS Order Lines` r16 | OCloud Region2, 2,500 |
| `odi.finished_good` | FG-0010 | `ODI Finished Goods` r14 | SU900180, `NetUnitsOrdered` 2,500, `ActualWrappedUnits` **2,900**; `ExpectedEndingInventoryQty` = 2,900 − 2,500 = 400 |
| `wms.item_order` | WL-0016, WL-0017 | `WMS Item Orders` r20–r21 | 1,500 + 1,000 = 2,500 |

**Calculation**

```
Net requirement = 2,500 − 0 − 2,900 = −400  → surplus 400 (≥ 50), 1 active run, no negative run → R06
```

**R06:** 400 units, Low (< 1,000). 400 × $4.49 = **$1,796**. Priority (1,796 + 50) × 1 = 1,846.

### A9. 900107 SIM HAMBURGER BUNS 8P 12Z → **R09-900107** and **R10-900107** (both Medium)

*Flags: `Seed Flags` r13 (R09) and r14 (R10).*

**Rows**

| Table | Row | Excel | Columns |
|---|---|---|---|
| `mips.order_line` | ML-0010 | `MIPS Order Lines` r14 | BUSS 2,000; `Last Modify Date Time` 2026-11-05 20:31 |
| `mips.order_line` | ML-0011 | `MIPS Order Lines` r15 | OCloud Region2 1,500 |
| `odi.finished_good` | FG-0009 | `ODI Finished Goods` r13 | SU900160, 3,500 / 3,500, `MIPSOrderQty` 2,000 |
| `wms.item_order` | WL-0013 | `WMS Item Orders` r17 | SIM DC NORTH, 1,500 / 1,500 |
| `wms.item_order` | WL-0014 | `WMS Item Orders` r18 | SIM DSD ROUTE 12, 1,200 / 1,200 |
| `wms.item_order` | WL-0015 | `WMS Item Orders` **r19** | SIM DSD ROUTE 27, `Original Ord Qty` 800, `Order Qty` **1,000**, `Allocated Units Qty` **800** |

**Calculation**

```
Sales-day demand (MIPS) = 2,000 + 1,500          = 3,500
WMS ordered             = 1,500 + 1,200 + 1,000  = 3,700
WMS allocated           = 1,500 + 1,200 + 800    = 3,500
R09: 3,700 − 3,500 = +200  (≠ 0)
R10: 3,700 − 3,500 = 200 short   (customer: SIM DSD ROUTE 27)
```

**R09:** +200 units, Medium. 200 × $3.59 = **$718**. Priority (718 + 50) × 2 = 1,536. WMS took a bigger order after MIPS last synced (11/05 20:31).

**R10:** 200 units, Medium, **$718**, priority 1,536.

### A10. 900109 SIM ITALIAN BREAD 20Z → **R11-900109** (Medium)

*Flag: `Seed Flags` r16.*

**Rows**
- ML-0013 (**`MIPS Order Lines` r17**): `Container Code` 01, `Units per Container` **10**.
- WL-0018 (**`WMS Item Orders` r22**): `Units per Container` **12**.

**Calculation:** 10 ≠ 12 → R11. 0 units, priority (0 + 50) × 2 = **100**. Every quantity ties (1,800 everywhere); only the tray size differs.

### A11. 900104 SIM WHITE ROUND TOP 2P 40Z → **R03-SU900130-900104** (Low)

*Flag: `Seed Flags` r17.*

**Rows**
- ML-0007 (**`MIPS Order Lines` r11**): OCloud Region2, `Order Quantity` **0**.
- FG-0005 (**`ODI Finished Goods` r9**): `OracleTypeCode` SU900130 (the same formula run as 900103 in r8), Line 1, **Shift 2**, 0 / 0.
- No WMS lines.

**Calculation:** FG-0005 has zero ordered and wrapped, but it **has** a shift, so it is not a ghost → R03. 0 units, priority (0 + 50) × 1 = **50**.

### A12. Plant: padded item keys → **R15-MIPS-FG-REF** (Low)

*Flag: `Seed Flags` r18.*

**Rows:** all 7 BUSS lines store `FG Oracle Ref` with a trailing space, e.g. `"900101 "`. They are ML-0001, -0002, -0004, -0008, -0010, -0015 and -0017, which are **`MIPS Order Lines` r5, r6, r8, r12, r14, r19, r21**.

**Calculation:** 7 of 17 lines have `raw value ≠ trimmed value` → R15. 0 units, priority 50. The agent trims the keys before matching, so no join is lost.

### A13. 900111 and 900112: clean controls

| Item | Excel rows | Demand | Sent to production | Wrapped | WMS ordered | WMS allocated | Units/tray MIPS = WMS | `MIPSOrderQty` = BUSS demand |
|---|---|---|---|---|---|---|---|---|
| 900111 | `MIPS Order Lines` r19–r20 · `ODI Finished Goods` r17 · `WMS Item Orders` r24–r26 | 1,500 + 2,500 = 4,000 | 4,000 | 4,000 | 2,000 + 1,000 + 1,000 = 4,000 | 4,000 | 10 = 10 | 1,500 = 1,500 |
| 900112 | `MIPS Order Lines` r21 · `ODI Finished Goods` r18 · `WMS Item Orders` r27 | 900 | 900 | 900 | 900 | 900 | 12 = 12 | 900 = 900 |

Implied inventory 0, net requirement 0, one run, no zero or negative runs: **no rule fires**. The scenarios break these two items on purpose.

---

## Part B. The eight scenarios

Each scenario starts from the untouched starting day at plant time **Fri 06 Nov 20:00**. A scenario only *adds* flags; none of the 14 starting flags is cleared or changed. Derived columns are recomputed by `derive.py` on every change, as noted in each table.

**Demo tip:** the data sheets show each row's value *before* the scenario. The "Excel" column below gives two places to look:

- the starting row on its data sheet (where one exists), to show the *before* value;
- the line on `Scenario Changes` that shows the change itself.

### S1. Inventory applied to the first order line only (item 900111)

*`Scenarios` r5 · `Scenario Changes` r5.*

**What happens:** MIPS nets 2,000 units of carried-in inventory against SIM BUTTER TOP, but the planning logic applies them to the first of its two lines only. Production is cut from 4,000 to 2,000.

**Change**

| Step | Table | Op | Row | Excel | Column: before → after |
|---|---|---|---|---|---|
| 1 | `odi.finished_good` | U | FG-0013 | `ODI Finished Goods` r17 → `Scenario Changes` r5 | `NetUnitsOrdered` 4,000 → 2,000; `ActualWrappedUnits` 4,000 → 2,000; *derived* `TotalPieces` 4,000 → 2,000 |

**Unchanged rows used in the calculation:** ML-0015 (`MIPS Order Lines` r19) and ML-0016 (r20).

**Calculation**

```
MIPS lines:  ML-0015 BUSS 1,500 (first)  +  ML-0016 OCloud 2,500  = Demand 4,000
Implied inventory            = 4,000 − 2,000            = 2,000
Consolidated requirement     = 4,000 − 2,000            = 2,000
First-line-only requirement  = 4,000 − min(2,000, 1,500) = 2,500
First-line risk              = 2,500 − 2,000            = 500     → R01
Net requirement              = 4,000 − 2,000 − 2,000    = 0
```

**Flag:** **R01-900111**, Medium (500 < 1,000), 500 units × $3.99 = **$1,995**, priority (1,995 + 50) × 2 = 4,090.

**Not raised:**
- R02, because it is excluded when R01 applies.
- R09, because WMS 4,000 = demand 4,000.
- R16, because `MIPSOrderQty` 1,500 = BUSS demand 1,500.

**Dollar exposure:** $17,500 → **$19,495**.

### S2. Inventory parked on a zero-quantity line (item 900112)

*`Scenarios` r6 · `Scenario Changes` r6–r8.*

**What happens:** MIPS adds an empty supply-plan line for SIM CINNAMON RAISIN and nets 200 units of inventory through it, so production is cut from 900 to 700. Then ODI schedules that empty line as its own run.

**Changes**

| Step | Table | Op | Row | Excel | Column values |
|---|---|---|---|---|---|
| 1 | `mips.order_line` | I | ML-S2A | new row → `Scenario Changes` r6 | `FG Oracle Ref` 900112, `Source System` OCloud Region2, `Order Quantity` **0** |
| 1 | `odi.finished_good` | U | FG-0014 | `ODI Finished Goods` r18 → `Scenario Changes` r7 | `NetUnitsOrdered` 900 → **700**; `ActualWrappedUnits` 900 → 700 |
| 2 | `odi.finished_good` | I | FG-S2A | new row → `Scenario Changes` r8 | `OracleTypeCode` SU900220, Line 1, `ShiftName` **Shift 2**, `NetUnitsOrdered` 0, `ActualWrappedUnits` 0, `MIPSBeginningInventoryQty` 0 |

**Unchanged rows used in the calculation:** ML-0017 (`MIPS Order Lines` r21) and WL-0023 (`WMS Item Orders` r27).

**Calculation**

```
Demand = 900 (ML-0017) + 0 (ML-S2A) = 900;  active runs: only FG-0014 (FG-S2A is zero)
Implied inventory  = 900 − 700 = 200     (> 0)
Recorded inventory = 0                   (= 0)
First-line risk    = (900 − min(200, 900)) − (900 − 200) = 700 − 700 = 0   (no R01)
No negative run                                                         → R02 (after step 1)
FG-S2A: ordered 0, wrapped 0, has a shift → not a ghost                 → R03 (after step 2)
```

**Flags**
- **R02-900112:** Medium, 200 units × $5.29 = **$1,058**, priority (1,058 + 50) × 2 = 2,216. It appears after step 1.
- **R03-SU900220-900112:** Low, 0 units, priority 50. It appears after step 2.

**Not raised:** R16, because `MIPSOrderQty` 900 + 0 = BUSS demand 900.

**Dollar exposure:** unchanged at $17,500, because R02 is not counted in it.

### S3. Production run moved to another line (item 900111)

*`Scenarios` r7 · `Scenario Changes` r9–r10.*

**What happens:** SIM BUTTER TOP's run is moved from Line 2 to Line 1 and re-created with the alternate formula code SU900210A. ODI leaves the old Line 2 entry behind as a zero copy with no shift.

**Changes**

| Step | Table | Op | Row | Excel | Column values |
|---|---|---|---|---|---|
| 1 | `odi.finished_good` | I | FG-S3A | new row → `Scenario Changes` r9 | `OracleTypeCode` **SU900210A**, **Line 1**, Shift 1, `NetUnitsOrdered` 4,000, `ActualWrappedUnits` 4,000, `MIPSOrderQty` 1,500 |
| 1 | `odi.finished_good` | U | FG-0013 | `ODI Finished Goods` r17 → `Scenario Changes` r10 | `ShiftName` Shift 1 → **empty**; `NetUnitsOrdered` 4,000 → 0; `ActualWrappedUnits` 4,000 → 0; `MIPSOrderQty` 1,500 → 0 |

**Calculation**

```
FG-0013: ordered 0, wrapped 0, no shift; base code SU900210
FG-S3A:  "SU900210A" minus trailing A = SU900210, on Line 1 ≠ Line 2, with output    → R04
Quantities: demand 4,000, wrapped 4,000 (FG-S3A), implied inventory 0, net requirement 0
```

**Flag:** **R04-SU900210-900111**, Medium, 0 units, priority (0 + 1,000) × 2 = **2,000**.

**Not raised:**
- R03: a zero run with no shift is treated as a ghost, not a blind line.
- R16: `MIPSOrderQty` 0 + 1,500 = 1,500.

**Dollar exposure:** unchanged.

### S4. Recovery run double-counts demand (item 900112)

*`Scenarios` r8 · `Scenario Changes` r11–r12.*

**What happens:** SIM CINNAMON RAISIN's run wraps 100 short. Ninety minutes later, a recovery run is ordered for the full 900 instead of the 100 that are missing.

**Changes**

| Step | Plant time | Table | Op | Row | Excel | Column: before → after |
|---|---|---|---|---|---|---|
| 1 | 20:00 | `odi.finished_good` | U | FG-0014 | `ODI Finished Goods` r18 → `Scenario Changes` r11 | `ActualWrappedUnits` 900 → **800**; *derived* `ExpectedEndingInventoryQty` 0 → −100 |
| 2 | 21:30 | `odi.finished_good` | I | FG-S4A | new row → `Scenario Changes` r12 | `OracleTypeCode` SU900221, Line 1, Shift 3, `NetUnitsOrdered` **900**, `ActualWrappedUnits` 900, `MIPSOrderQty` 0 |

**After step 1 (a temporary flag)**

```
Demand 900, implied 0, wrapped 800
Net requirement = 900 − 0 − 800 = 100 (≥ 20);  WMS allocated 900 ≥ 900   → R07-900112 (100 units, Medium)
```

WMS has filled the order (WL-0023, `WMS Item Orders` r27) from stock before the recovery run exists.

**After step 2**

```
Active runs = 2
Implied inventory         = 900 − first run's order 900   = 0
Consolidated requirement  = 900
Wrapped                   = 800 + 900                     = 1,700
Still needed after run 1  = 900 − 800                     = 100
Recovery ordered beyond   = 900 − 100                     = 800
Wrapped beyond            = 1,700 − 900                   = 800
Over-build                = larger of the two             = 800   (≥ 100)  → R05
Net requirement           = 900 − 0 − 1,700               = −800  → R07 clears (< 20)
```

**Flag:** **R05-900112**, Medium (800 < 1,000), 800 × $5.29 = **$4,232**, priority (4,232 + 50) × 2 = 8,564. The temporary R07-900112 clears.

**Not raised:** R06, because there are 2 active runs.

**Dollar exposure:** $17,500 → **$21,732**.

### S5. Return to plant does not reduce the received quantity (item 900111)

*`Scenarios` r9 · `Scenario Changes` r13–r14.*

**What happens:** WMS sends 120 damaged units of SIM BUTTER TOP back to the plant against ticket TK-0010, and Oracle posts the return. The ticket itself is not reduced.

**Changes**

| Step | Table | Op | Row | Excel | Column values |
|---|---|---|---|---|---|
| 1 | `wms.transaction` | I | WT-S5A | new row → `Scenario Changes` r13 | `Txn Type` **RETURN_TO_PLANT**, `Item Nbr` 900111, `Qty` **120**, `Ticket Nbr` **TK-0010**, `Reason Code` DAMAGED |
| 1 | `oracle.inventory_txn` | I | OT-S5A | new row → `Scenario Changes` r14 | `SourceReference` WT-S5A, `TransactionTypeName` WMS Return to Plant, `TransactionQuantity` −120, `ProcessStatus` POSTED |

**The row that does not change:** `wms.ticket` TK-0010, **`WMS Receipt Tickets` r14**. `Received Qty` 4,000, `Returned Qty` **0**, `Net Received Qty` (derived) 4,000.

**Other unchanged rows used:**
- FG-0013 (`ODI Finished Goods` r17), which wrapped 4,000;
- WL-0020–0022 (`WMS Item Orders` r24–r26), with 4,000 allocated.

**Calculation**

```
R19: returns booked against TK-0010 by transactions = 120
     ticket's Returned Qty                          = 0
     gap = 120 − 0 = 120 (> 0)                                      → R19
     Net Received Qty should be 4,000 − 120 = 3,880, but shows 4,000

R07: Returns term for 900111 = 120
     Net requirement = 4,000 − 0 − 4,000 + 120 = 120 (≥ 20)
     WMS allocated 4,000 ≥ sales-day demand 4,000                   → R07

R18: OT-S5A is POSTED with SourceReference WT-S5A                  → not raised
```

**Flags**
- **R19-TK-0010:** Medium (120 < 500), 120 × $3.99 = **$478.80**, priority (478.80 + 50) × 2 = 1,057.6.
- **R07-900111:** Medium (120 < 500), **$478.80**, priority 1,057.6. WMS still allocates 4,000, but only 3,880 usable units exist.

**Dollar exposure:** $17,500 → **$18,458** (+$957.60, rounded).

### S6. Order changed after MIPS last synced (item 900112)

*`Scenarios` r10 · `Scenario Changes` r15–r16.*

**What happens:** Route 12 raises its SIM CINNAMON RAISIN order from 900 to 1,100 in WMS, and the order header follows. MIPS has not refreshed, so it still plans 900.

**Changes**

| Step | Table | Op | Row | Excel | Column: before → after |
|---|---|---|---|---|---|
| 1 | `wms.item_order` | U | WL-0023 | `WMS Item Orders` r27 → `Scenario Changes` r15 | `Order Qty` 900 → **1,100** (`Allocated Units Qty` stays 900). *Derived:* `Trays Remaining` 0 → 16 (200 ÷ 12 per tray), `Completion%` GREEN → YELLOW |
| 1 | `wms.order` | U | 9900003 | `WMS Orders` r7 → `Scenario Changes` r16 | `Order Qty` 7,900 → **8,100**. *Derived:* `Short Qty` 0 → 200, `% Allocated` 100 → 97.53, `Progress` GREEN → YELLOW |

**The row that does not change:** `mips.order_line` ML-0017, **`MIPS Order Lines` r21**. `Order Quantity` 900, `Last Modify Date Time` 2026-11-05 20:31.

**Calculation**

```
Sales-day demand (MIPS) = 900
WMS ordered             = 1,100;  WMS allocated = 900
R09: 1,100 − 900 = +200      → R09
R10: 1,100 − 900 = 200 short (SIM DSD ROUTE 12)   → R10
R13: headers 18,200 + 11,800 + 8,100 + 4,200 = 42,300 = item lines 42,300   → not raised
```

**Flags**
- **R09-900112:** Medium, +200 × $5.29 = **$1,058**, priority 2,216.
- **R10-900112:** Medium, 200 × $5.29 = **$1,058**, priority 2,216.

**Dollar exposure:** $17,500 → **$19,616**.

### S7. Freezer inventory not deducted (item 900111)

*`Scenarios` r11 · `Scenario Changes` r17–r22.*

**What happens:** Route 27 needs 300 more SIM BUTTER TOP. MIPS raises the plan and sends it to ODI, but the plant makes nothing extra: WMS fills the 300 from the freezer and posts the move. Nothing tells MIPS the units came from the freezer.

**Changes**

| Step | Table | Op | Row | Excel | Column: before → after |
|---|---|---|---|---|---|
| 1 | `mips.order_line` | U | ML-0016 | `MIPS Order Lines` r20 → `Scenario Changes` r17 | `Order Quantity` 2,500 → **2,800** |
| 1 | `odi.finished_good` | U | FG-0013 | `ODI Finished Goods` r17 → `Scenario Changes` r18 | `NetUnitsOrdered` 4,000 → **4,300** (`ActualWrappedUnits` stays **4,000**); *derived* `ExpectedEndingInventoryQty` 0 → −300 |
| 1 | `wms.item_order` | U | WL-0022 | `WMS Item Orders` r26 → `Scenario Changes` r19 | `Order Qty` 1,000 → 1,300; `Allocated Units Qty` 1,000 → **1,300** |
| 1 | `wms.order` | U | 9900004 | `WMS Orders` r8 → `Scenario Changes` r20 | `Order Qty` 4,200 → 4,500; `Allocated Qty` 4,000 → 4,300 |
| 1 | `wms.transaction` | I | WT-S7A | new row → `Scenario Changes` r21 | `Txn Type` **FREEZER_OUT**, `Item Nbr` 900111, `Qty` 300, `Reason Code` FILL |
| 1 | `oracle.inventory_txn` | I | OT-S7A | new row → `Scenario Changes` r22 | `SourceReference` WT-S7A, `TransactionTypeName` WMS Freezer Issue, `TransactionQuantity` 300, POSTED |

**Unchanged rows used:** ML-0015 (`MIPS Order Lines` r19), WL-0020 and WL-0021 (`WMS Item Orders` r24–r25).

**Calculation**

```
Demand            = 1,500 + 2,800 = 4,300
Implied inventory = 4,300 − 4,300 = 0
Wrapped           = 4,000
Net requirement   = 4,300 − 0 − 4,000 = 300 (≥ 20)
WMS allocated     = 2,000 + 1,000 + 1,300 = 4,300 ≥ 4,300           → R07
R09: WMS ordered 4,300 = MIPS 4,300                                  → not raised
R13: Route 27 header 4,500 = its lines 1,000 + 1,200 + 1,000 + 1,300 → not raised
R18: OT-S7A is POSTED                                               → not raised
```

**Flag:** **R07-900111**, Medium (300 < 500), 300 × $3.99 = **$1,197**, priority (1,197 + 50) × 2 = 2,494.

**Dollar exposure:** $17,500 → **$18,697**.

### S8. WMS transactions missing in Oracle (plant level)

*`Scenarios` r12 · `Scenario Changes` r23–r34.*

**What happens:** The SIM DC SOUTH trailer is loaded and shipped. WMS books one shipment per item. Oracle posts four of them, rejects one (900110: "Lot not found"), and never receives the sixth (900101). Ten plant minutes pass.

**Changes**

| Step | Plant time | Table | Op | Row | Excel | Column values |
|---|---|---|---|---|---|---|
| 1 | 20:00 | `wms.order` | U | 9900002 | `WMS Orders` r6 → `Scenario Changes` r23 | `Loaded Qty` 0 → 11,800; `Shipped Qty` 0 → 11,800; *derived* `% Loaded`, `% Shipped` 0 → 1.0 |
| 1 | 20:00 | `wms.transaction` | I | WT-S8A | `Scenario Changes` **r24** | SHIP, 900101, **3,000**, `Order Nbr` 9900002 |
| 1 | 20:00 | `wms.transaction` | I | WT-S8B | `Scenario Changes` r25 | SHIP, 900102, 2,000 |
| 1 | 20:00 | `wms.transaction` | I | WT-S8C | `Scenario Changes` r26 | SHIP, 900103, 2,500 |
| 1 | 20:00 | `wms.transaction` | I | WT-S8D | `Scenario Changes` r27 | SHIP, 900106, 1,600 |
| 1 | 20:00 | `wms.transaction` | I | WT-S8E | `Scenario Changes` r28 | SHIP, 900108, 1,500 |
| 1 | 20:00 | `wms.transaction` | I | WT-S8F | `Scenario Changes` **r29** | SHIP, 900110, **1,200** |
| 2 | 20:02 | `oracle.inventory_txn` | I | OT-S8B … OT-S8E | `Scenario Changes` r30–r33 | `SourceReference` WT-S8B … WT-S8E, `ProcessStatus` **POSTED** |
| 2 | 20:02 | `oracle.inventory_txn` | I | OT-S8F | `Scenario Changes` **r34** | `SourceReference` WT-S8F, `ProcessStatus` **ERROR**, `ErrorMessage` "Lot not found in subinventory FG" |
| 3 | 20:11 | *(no row change; the clock moves 9 minutes)* | | | | |

There is **no** Oracle row for WT-S8A anywhere. Point at the gap between r29 and r30: the postings start at OT-S8**B**.

**Calculation (at plant time 20:11)**

| WMS `Txn Nbr` | POSTED Oracle row with this `SourceReference`? | Minutes since `Txn Date Time` 20:00 | Result |
|---|---|---|---|
| WT-S8A (r24) | none: Oracle never received it | 11 ≥ 10 | **R18** |
| WT-S8B–E (r25–r28) | yes (OT-S8B–E, r30–r33) | — | fine |
| WT-S8F (r29) | no: OT-S8F (r34) is ERROR | 11 ≥ 10 | **R18** |

After step 2 (20:02) neither is flagged, because only 2 minutes have passed. The flags appear because **time** moves, without any new data.

**Flags**
- **R18-WT-S8A:** High, 3,000 × $4.69 = **$14,070**, priority (14,070 + 2,000) × 3 = 48,210. This is the top of the worklist.
- **R18-WT-S8F:** High, 1,200 × $4.79 = **$5,748**, priority (5,748 + 2,000) × 3 = 23,244.

**Not raised:**
- R12 does not grow: 9900002 has Loaded 11,800, so it was scanned.

**Dollar exposure:** unchanged, because R18 is not counted in it.

---

## Part C. Summary

| Where | Item | Flag | Rule | Severity | Units | $ | Key calculation | Excel (rows to show) |
|---|---|---|---|---|---|---|---|---|
| Seed | 900102 | R05-900102 | R05 | High | 1,200 | 5,628 | wrapped 7,200 − requirement 6,000 | `ODI Finished Goods` r6–r7 · `Seed Flags` r5 |
| Seed | plant | R12-PLANT | R12 | High | 7,900 | – | 9900003 shipped 7,900, loaded 0 | `WMS Orders` r7 · `Seed Flags` r6 |
| Seed | 900101 | R01-900101 | R01 | High | 1,000 | 4,690 | 10,000 first-line-only − 9,000 consolidated | `MIPS Order Lines` r5–r7 · `ODI Finished Goods` r5 · `Seed Flags` r7 |
| Seed | 900106 | R07-900106 | R07 | High | 700 | 2,513 | demand 4,000 − wrapped 3,300; WMS allocated 4,000 | `ODI Finished Goods` r12 · `WMS Item Orders` r15–r16 · `Seed Flags` r8 |
| Seed | 900103 | R02-900103 | R02 | Medium | 800 | 3,192 | demand 5,000 − sent 4,200; recorded 0 | `MIPS Order Lines` r10 · `ODI Finished Goods` r8 · `Seed Flags` r9 |
| Seed | 900110 | R08-900110 | R08 | Medium | 300 | 1,437 | NetUnitsOrdered −300 | `ODI Finished Goods` r16 · `Seed Flags` r10 |
| Seed | 900105 | R04-SU900150-900105 | R04 | Medium | – | – | zero, no-shift SU900150 beside SU900150A on Line 2 | `ODI Finished Goods` r10–r11 · `Seed Flags` r11 |
| Seed | 900108 | R06-900108 | R06 | Low | 400 | 1,796 | wrapped 2,900 − demand 2,500 | `ODI Finished Goods` r14 · `Seed Flags` r12 |
| Seed | 900107 | R09-900107 | R09 | Medium | 200 | 718 | WMS 3,700 − MIPS 3,500 | `MIPS Order Lines` r14–r15 · `WMS Item Orders` r17–r19 · `Seed Flags` r13 |
| Seed | 900107 | R10-900107 | R10 | Medium | 200 | 718 | ordered 3,700 − allocated 3,500 | `WMS Item Orders` r19 · `Seed Flags` r14 |
| Seed | 900102 | R14-SU900121-Line 2 | R14 | Low | – | – | mixed 4 vs scheduled 3 (≈ 500 pieces) | `ODI Batches` r7 · `Seed Flags` r15 |
| Seed | 900109 | R11-900109 | R11 | Medium | – | – | 10 vs 12 units per tray | `MIPS Order Lines` r17 · `WMS Item Orders` r22 · `Seed Flags` r16 |
| Seed | 900104 | R03-SU900130-900104 | R03 | Low | – | – | zero run with a shift | `ODI Finished Goods` r9 · `Seed Flags` r17 |
| Seed | plant | R15-MIPS-FG-REF | R15 | Low | – | – | 7 of 17 keys padded | `MIPS Order Lines` r5, r6, r8, r12, r14, r19, r21 · `Seed Flags` r18 |
| S1 | 900111 | R01-900111 | R01 | Medium | 500 | 1,995 | 2,500 first-line-only − 2,000 consolidated | `MIPS Order Lines` r19–r20 · `Scenario Changes` r5 |
| S2 | 900112 | R02-900112 | R02 | Medium | 200 | 1,058 | demand 900 − sent 700; recorded 0 | `MIPS Order Lines` r21 · `Scenario Changes` r6–r7 |
| S2 | 900112 | R03-SU900220-900112 | R03 | Low | – | – | new zero run with a shift | `Scenario Changes` r8 |
| S3 | 900111 | R04-SU900210-900111 | R04 | Medium | – | – | zero, no-shift SU900210 beside SU900210A on Line 1 | `ODI Finished Goods` r17 · `Scenario Changes` r9–r10 |
| S4 | 900112 | R05-900112 | R05 | Medium | 800 | 4,232 | wrapped 1,700 − requirement 900 | `ODI Finished Goods` r18 · `Scenario Changes` r11–r12 |
| S5 | 900111 | R19-TK-0010 | R19 | Medium | 120 | 478.80 | 120 returned vs 0 on the ticket | `WMS Receipt Tickets` r14 · `Scenario Changes` r13 |
| S5 | 900111 | R07-900111 | R07 | Medium | 120 | 478.80 | net requirement 4,000 − 4,000 + 120 returns | `ODI Finished Goods` r17 · `WMS Item Orders` r24–r26 · `Scenario Changes` r13 |
| S6 | 900112 | R09-900112 | R09 | Medium | 200 | 1,058 | WMS 1,100 − MIPS 900 | `MIPS Order Lines` r21 · `WMS Item Orders` r27 · `Scenario Changes` r15 |
| S6 | 900112 | R10-900112 | R10 | Medium | 200 | 1,058 | ordered 1,100 − allocated 900 | `WMS Item Orders` r27 · `Scenario Changes` r15–r16 |
| S7 | 900111 | R07-900111 | R07 | Medium | 300 | 1,197 | demand 4,300 − wrapped 4,000; WMS allocated 4,300 | `Scenario Changes` r17–r22 |
| S8 | 900101 | R18-WT-S8A | R18 | High | 3,000 | 14,070 | no Oracle row after 11 minutes | `Scenario Changes` r24 (no posting) |
| S8 | 900110 | R18-WT-S8F | R18 | High | 1,200 | 5,748 | Oracle ERROR after 11 minutes | `Scenario Changes` r29, r34 |

R13, R16 and R17 never fire on the dummy data. To see them in the simulator:
- **R13:** change a `wms.order` header's `Order Qty` so the headers no longer equal the item lines.
- **R16:** change an ODI run's `MIPSOrderQty`.
- **R17:** set one run's `ActualWrappedUnits` far above its `NetUnitsOrdered`, for example FG-0011 (`ODI Finished Goods` r15) to 5,400.
