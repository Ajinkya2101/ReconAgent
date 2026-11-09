# Client Alignment Review — Reconciliation Agent

**Baseline.** Only what the client gave us:

| Ref | Source | What it defines |
|---|---|---|
| **REQ** | `docs/reference/WMS_AI_Agent_Requirement.docx` | The use case: the 8 mismatch scenarios, today's manual process, the agent workflow (collect → reconciled position → match → detect → recommend) and two worked examples |
| **CH** | `docs/reference/Bimbo WMS Inventory Reconciliation PPT.pptx` | The 10 challenge areas and their business impact |
| **DATA** | `data/*.xlsx` | The five extracts for Saturday sales date 12 Sep 2026 (157 MIPS lines, 86 ODI runs, 36 batches, 18 WMS orders, 80 SKUs) |

Our own proposal deck is **not** a requirement. `ReconAgent.txt` is our summary of the solution, so it is
checked against the client sources in section 8, not used as a baseline.

**Method.**
1. Traced every REQ and CH statement to the code.
2. Profiled every extract column for its type, data quality and whether the engine uses it.
3. Ran the engine on the extracts with an empty decisions file and the AI turned off.

Status key: ✅ aligned · ⚠ partial · ❌ missing · 📄 needs data the extracts do not have.

---

## 1. Verdict

On the client's real data, the agent does what REQ asks for the scenarios the extracts can show:
- It consolidates all demand lines per SKU before applying inventory (REQ step 2).
- It gives a correction, not just a difference (REQ step 5).
- It finds scenarios 1, 2, 3, 4 and 8 with concrete examples, and scenarios 6 and 7 through proxies.

There are three kinds of gap:

1. **Data the client has not sent yet.** The extracts contain **no WMS tickets, returns, on-hand or
   freezer inventory, lots, work orders or Oracle postings**. That data is behind:
   - REQ scenarios 5 and 7, and the Oracle half of scenario 8;
   - the first detection in REQ: "MIPS says production is required, but WMS has enough unallocated
     inventory";
   - most of REQ step 3 (matching records).

   The code models these in the simulated event feed only. Ask the client for the missing extracts.
2. **Logic that was missing even though the extracts have the data** — ✅ **now fixed** (see the
   "Changed" section of the CHANGELOG):
   - moves to another day or run sequence (scenario 3);
   - WMS order changes (scenario 6);
   - the "reduce the requirement to N" wording of the REQ example correction;
   - closed lines (`Closed`);
   - the R19 area tag.

   All 125 tests pass, 13 of them new (`tests/test_client_alignment.py`). The headline totals on the
   extract are unchanged.
3. **One assumption that decides most of the numbers: moved lines count as demand.** The data supports it,
   but the client has not confirmed it (section 4).

---

## 2. What the client extracts contain

| Extract | Used by the engine | Present but **not used** | Data quality |
|---|---|---|---|
| MIPS Order Data (157 lines) | SKU, dates, Order Moved, qty, container, units per container, price, Source System, last modified, **`Closed`** (✅ now used: closed lines are not demand; all `F` in this extract) | `Scheduling Errors` (all empty), `Requested Ship Date`, `Units Per Carton` | **89 of 157 `FG Oracle Ref` values are padded with spaces**, so R15 fires and the agent normalises the keys. 41 lines are moved to sales date 11 Sep. |
| ODI Finished Good (86 runs) | SKU, line, shift, formula, ordered, wrapped, expected ending inventory, MIPS beginning inventory and order qty | `OCloudFGItemStructure` (Primary/Secondary, shown only), damage weights | Clean. 5 zero-quantity runs (2 blind, 3 moved copies). 2 negative net orders. |
| ODI Batch (36) | line, shift, formula, scheduled vs mixed batches, final piece variance | `Oracle Formula`, weight columns | Clean (one description padded) |
| WMS Fill Rate (18 orders + total) | order, allocated, loaded, shipped, short | `Route Nbr`, `Packed Qty` | **Loaded = 0 on all 18 orders** |
| WMS Order Summary by Item | item, units per container, customer order and allocated qty, report subtotals, **`Original Ord Qty`** (✅ now used by R09: it differs from `Order Qty` on 5 customer lines of 3 SKUs) | — | Block layout, parsed correctly |

All numbers are real numbers and all dates are real dates; no value is stored as text. The ingest coercion
risk in CODE_REVIEW M2 is therefore not present in this data. It stays as a guard for future extracts.

**What REQ step 1 asks for that no extract contains** (📄): WMS finished-goods inventory, ticket
quantities, returned-to-plant quantities, trailer allocation (only `Loaded Qty`), freezer inventory,
inventory location, work-order status, transaction timestamps, ODI/Oracle inventory transactions, failed
or delayed interfaces and posting status. MIPS added or canceled runs are also missing (no run identifier
or cancellation flag).

---

## 3. The eight REQ scenarios on the client's data

Flags by rule on the extracts: R01 10 · R02 26 · R03 2 · R04 3 · R05 2 · R06 29 · R07 4 · R08 2 · R09 11 ·
R10 1 · R11 3 · R12 1 · R13 1 · R14 1 · R15 1 · R17 6 · R16, R18, R19 0. That is 103 flags; 46 of the 78
SKUs with demand are flagged and 32 are clean.

| # | REQ scenario | Rule(s) | Evidence on the extract | Status | Gap |
|---|---|---|---|---|---|
| 1 | Several order lines for one SKU; inventory applied to the first line only | R01, R06 | 10 × R01. SKU 150080: 4 lines, 14,691 units, 2,720 inventory; first-line netting asks for 1,379 too many | ✅ | — |
| 2 | Inventory on a zero-quantity or blind line | R02, R03, R08 | 26 × R02, 2 × R03, 2 × R08 | ✅ | **Fixed.** R02 now ends like the REQ example: *"Apply the 2,115 units to the active sales-order lines…, keep the production requirement at 27,002 units (29,117 demand − 2,115 inventory)"* (R02-950996 on the extract). |
| 3 | Inventory does not follow a moved run: to another **line**, **production day** or **run sequence** | R04 | 3 × R04: SU290080A, SU290082A, SU260145, zero copies left on Line 3 (all line moves) | ✅ | **Fixed.** R04 now also detects moves to another production day and to another slot on the same line, and names which kind of move it was. |
| 4 | Recovery run; run-1 excess goes to the next sales day instead of run 2 | R05, R06 | SKU 8526: 1,747 needed after run 1, recovery ordered 2,280, over-produced by 1,843 | ✅ | — |
| 5 | Returned-to-plant tickets do not reduce the received total (1,020 → 816) | R19, `returns` in the position | — | 📄 | No ticket or return data in the extracts. R19 runs only on the simulated feed. |
| 6 | Stale data: workstations show different quantities; an order changed but the change did not reach the other system | R09, R13 | 11 × R09 (MIPS vs WMS order qty), 1 × R13 | ⚠ | **Order changes fixed:** R09 now reads `Original Ord Qty`. For SKUs 76108, 950507 and 951008, MIPS still plans exactly the original WMS total, so the flag lists the changed customer orders and the attribution is *certain*. Workstation views are still not in any extract (📄). |
| 7 | Freezer inventory stays in the system after use (≈300,000 units after July 4) | R07 (infers untracked supply) | 4 × R07. SKU 75481: WMS filled 10,320, production and inventory supply 9,384, 936 untracked | ⚠ 📄 | R07 finds the *symptom*. The REQ example is **lot-level** ("Freezer lot LV94 shows 16,000 units used… WMS continues to carry 16,000"), and the extracts have no freezer or lot data. The simulated feed has `Lot Nbr` and `FREEZER_*` transactions, but **no rule uses them**. |
| 8 | WMS transactions missing in Oracle (≈10 minutes) | R12, R13, R16, R18 | R12: all 18 orders shipped with loaded = 0 (349,147 units) | ⚠ 📄 | R12, R13 and R16 work on the extracts. The Oracle-posting check (R18) needs posting data that is not in the extracts. |

---

## 4. The assumption the client must confirm: moved lines count as demand

41 MIPS lines (29 SKUs, **74,753 units**) carry `Order Moved = True` with sales date 11 Sep, yet they sit
on the 12 Sep schedule. The engine counts them in the SKU's demand (`demand_total`), and R09 leaves them out
(`demand_current`). REQ asks for "total *valid* demand" per SKU and sales date. It does not say whether a
moved line is valid demand for the day it was moved from.

The answer decides most of the output:

| On the client extract | Moved lines counted (today) | Moved lines left out |
|---|---|---|
| Flags | 103 | 122 |
| $ at risk (as computed today) | $226,786 | $449,684 |
| Over-production risk units | 12,887 | 14,131 |
| Surplus not carried forward | 35,205 | 84,082 |
| Units from stock MIPS cannot see | 2,571 | 936 |
| Flags that change | 19 disappear, 38 appear (31 of them R16) | |

**The client's own data supports counting them.** If they are left out, R16 fires on 31 SKUs. So ODI's copy
of the MIPS order quantity already *includes* the moved lines, which means the client's systems treat them
as part of this production. Keep the current behaviour, but present this to the client as an explicit
assumption and get it confirmed.

Other assumptions to confirm at the same time:
- **Inventory is inferred:** demand minus what the first run ordered, because MIPS does not record it
  (26 × R02 show it is missing).
- **Closed lines:** `Closed = T` lines should be excluded from valid demand. No line is closed in this
  extract, but the code would count them today.
- **$3.99 default price** when MIPS has no retail price.
- The **severity thresholds**. REQ gives none: 1,000 units for R01/R05/R06 and 500 for R07 are our
  choices.
- The **sales-day calendar** (Monday–Saturday) used for carry-forward.

---

## 5. REQ detection list ("4. Detect mismatches")

| REQ condition | Rule | Status |
|---|---|---|
| MIPS says production is required, but **WMS has enough unallocated inventory** | — | ❌ 📄 No rule, and no extract carries WMS on-hand inventory. This is the first item in the client's list and their headline problem ("MIPS may show that additional production is required even though enough physical inventory exists in WMS"). Request a WMS on-hand or ticket extract, then build the rule. |
| Inventory applied to a zero-quantity line | R02, R03 | ✅ |
| Same SKU on multiple lines without consolidated inventory | R01 | ✅ |
| WMS shows completed inventory that MIPS does not recognise | R07 | ✅ |
| Return recorded but received quantity not reduced | R19 | ⚠ 📄 simulated feed only |
| Run moved but its inventory allocation did not move | R04 | ✅ line, day and sequence moves |
| Freezer inventory consumed in MIPS but still available in WMS | — | ❌ 📄 |
| WMS transaction not in Oracle within the expected time | R18 | ⚠ 📄 simulated feed only |
| Two workstations or reports show conflicting quantities | R13 | ⚠ reports only |
| Unusually large quantity, possibly a data-entry error | R17 | ✅ 6 flags on the extract |

---

## 6. REQ workflow and today's manual process

| REQ | Code | Status |
|---|---|---|
| **Step 1, collect:** MIPS, WMS, ODI/Oracle fields | Everything in the five extracts that the rules need | ⚠ 📄 see section 2 |
| **Step 2, position:** *Net = Total valid demand − Available usable inventory − Completed production + Valid returns or adjustments*, "for each SKU, sales date, and production period"; consolidate all demand lines first | Same equation ([position.py:104-127](../recon_agent/position.py#L104-L127)); all lines consolidated | ✅ equation · ⚠ one position **per SKU** per load. Correct for a single-day extract like this one, but several sales days in one load would merge. Production period is not a dimension. "Valid" now excludes `Closed` lines ✅. |
| **Step 3, match:** runs, **work orders**, tickets, returns, shipment allocations, **freezer lots**, Oracle transactions, by SKU, dates, line, run, **work-order no.**, ticket, **lot**, qty, timestamp | Matches by SKU (and formula and line for runs) | ⚠ 📄 the identifiers are not in the extracts |
| **Step 4, detect** | Section 5 | ⚠ |
| **Step 5, recommend:** "an operational explanation rather than only reporting that numbers differ" | Every rule gives a correction with quantities; accountability names the system at fault; AI explanation is optional | ✅ (R02 wording: see 3 #2) |
| Manual task: compare the MIPS schedule with WMS inventory | Position and systems table | ✅ |
| Manual task: work out whether several schedule lines are the same SKU demand | R01 | ✅ |
| Manual task: find inventory attached to the wrong line | R02, R03 | ✅ |
| Manual task: review tickets created, filled, returned or canceled | — | 📄 |
| Manual task: calculate the actual net physical quantity | Net requirement | ✅ |
| Manual task: find inventory carried to the wrong sales day | R06; moved lines | ✅ |
| Manual task: reassign inventory to a second run | R05 correction | ✅ |
| Manual task: override inflated inventory | R08, R06 | ✅ |
| Manual task: clear freezer inventory | — | 📄 |
| Manual task: investigate transactions that did not reach Oracle | R12 (and R18 on the feed) | ⚠ |
| Manual task: communicate corrections between **three shifts and ~11 supervisors** | Claims, notes for the next shift, shift briefing, change notifications on open screens | ✅ (claims and acks are not durable: CODE_REVIEW H3) |

---

## 7. Challenge areas (CH deck)

| Challenge area | How the agent addresses it | Status |
|---|---|---|
| System landscape: limited end-to-end visibility | One position per SKU across MIPS, ODI and WMS; systems table | ✅ |
| Inventory allocation: excess not applied to later demand of the same SKU | R01, R05, R06 and the carry-forward view | ✅ (carry-forward is display only) |
| Inventory synchronisation: balances and work orders misaligned | R02, R04, R08 | ⚠ no work orders |
| Freezer inventory management: manual designation | Corrections suggest "or designate to freezer" (R05, R06); R07 | ⚠ no freezer-lot logic |
| Order-to-batch traceability: how sales orders map to batches | R03, R14; the detail shows the source records per SKU | ⚠ no order → run → batch link view |
| Production visibility: MIPS lacks downstream order context | R09, R16; MIPS vs WMS on one scale | ✅ |
| **Returns & adjustments**: manual handling | R19 (simulated feed only), now tagged with this area ✅ | ⚠ 📄 needs return data |
| Data flow transparency: ownership and touchpoints | Accountability record per flag (origin system, handoff, each system's role) | ✅ |
| Warehouse traceability: many handoffs | R10, R12 | ✅ |
| Integration architecture: APIs and middleware | R13, R16 flag interface gaps; the event feed models CDC | ✅ (a process topic, not a rule) |

---

## 8. `ReconAgent.txt` against the client sources

`ReconAgent.txt` matches REQ on the equation, the consolidate-first principle and corrections over
differences. Correct it on these points:

1. It says R18/R19 are "not in the current version". REQ requires both conditions (scenarios 5 and 8, and
   the detection list). The rules exist; they need ticket and Oracle-posting data that the client has not
   sent. Say that instead.
2. It does not mention the REQ requirements that are not covered: WMS unallocated inventory, freezer lots,
   moved days and sequences, work-order and lot matching.
3. It states the moved-line treatment and the inferred inventory as facts. Present them as assumptions
   awaiting client confirmation (section 4).
4. The severity thresholds (R05 ≥ 1,000, …) and the priority formula with a base value are our design;
   REQ specifies neither. That is fine, but label them as proposals.

---

## 9. Required changes, in priority order

**Ask the client (unblocks the most)**
1. Confirm the section 4 assumptions: moved lines, inferred inventory, `Closed`, the default price, the
   severity thresholds and the calendar.
2. Request the missing data: WMS on-hand inventory and tickets (with returns, location and lot), freezer
   inventory, work orders, and Oracle inventory transactions with posting status.

**Code changes possible with today's extracts**
3. ✅ Done: `Closed` lines are excluded from valid demand.
4. ✅ Done: R04 detects moves to another production day or run sequence.
5. ✅ Done: `Original Ord Qty` vs `Order Qty` is used as evidence of an order change (scenario 6).
6. ✅ Done: R02's correction states the resulting requirement.
7. ✅ Done: R19 is tagged "Returns & Adjustments".
8. **Open:** fix the double-counted dollar figure (CODE_REVIEW M1): $226,786 on this extract,
   ≈$207,005 counted once per SKU. This lowers the headline number, so agree how to present it first.

**Once the data arrives**
9. A rule for "MIPS requires production but WMS has enough unallocated inventory".
10. A freezer-lot rule (consumed in MIPS, still on hand in WMS).
11. Link records by work order, ticket and lot (REQ step 3).
12. Positions per SKU **and sales date** if loads span several days (and the decision keys in CODE_REVIEW
    H1).
