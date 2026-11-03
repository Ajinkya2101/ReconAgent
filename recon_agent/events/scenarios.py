"""The eight known mismatch scenarios as scripts of source-system changes.

Each scenario plays a short story against the starting day (seed.py): mostly on
the two SKUs that start clean (900111, 900112), so the flag visibly appears on
the worklist when it runs. Steps publish through the normal producer path, so
injected faults apply to them, and derived columns are recomputed as the source
system would. `expect` lists the flags the scenario must raise (or clear) when
run on the untouched starting day; tests/test_scenarios.py checks every one, so
the library doubles as the regression suite for the rules.

Run one with `events scenario run S5`, or from the simulator's Scenarios tab.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Callable

from . import contract
from .clock import SimClock, parse_duration
from .derive import derive
from .log import Change, Event, EventLog


@dataclass
class Step:
    say: str                                         # what happens at the plant, for the operator
    changes: list[Change] = field(default_factory=list)
    advance: str | None = None                       # plant time that passes before this step


@dataclass
class Scenario:
    id: str
    number: int                                      # the requirement's scenario number
    title: str
    story: str
    skus: tuple[str, ...]
    steps: list[Step]
    appears: dict[str, str]                          # flag ID -> why it appears
    clears: dict[str, str] = field(default_factory=dict)


def U(entity: str, key: str, note: str | None = None, **cols) -> Change:
    return Change(entity, "U", key, {k.replace("_", " "): v for k, v in cols.items()}, note=note)


def I(entity: str, row: dict, note: str | None = None) -> Change:
    return Change(entity, "I", row=row, note=note)


def ship(txn: str, sku: str, qty: int, order: str) -> Change:
    return I("wms.transaction", {"Txn Nbr": txn, "Txn Type": "SHIP", "Txn Date Time": "now", "Item Nbr": sku,
                                 "Qty": qty, "Order Nbr": order, "User": "SIM SHIPPING"})


def posting(txn_id: str, source_ref: str, kind: str, sku: str, qty: int, status: str = "POSTED",
            error: str | None = None) -> Change:
    return I("oracle.inventory_txn", {
        "TransactionId": txn_id, "SourceReference": source_ref, "TransactionTypeName": kind, "ItemNumber": sku,
        "TransactionQuantity": qty, "TransactionUnitOfMeasure": "Each", "Subinventory": "FG",
        "TransactionDate": "now", "PostedDate": "now" if status == "POSTED" else None, "ProcessStatus": status,
        "ErrorMessage": error})


SCENARIOS: dict[str, Scenario] = {s.id: s for s in (
    Scenario(
        "S1", 1, "Inventory applied to the first order line only",
        "MIPS nets 2,000 units of carried-in inventory for SIM BUTTER TOP, but applies them against the first "
        "of its two order lines only. The run is cut to 2,000.",
        ("900111",),
        [Step("MIPS sends 2,000 to production instead of 4,000; the run wraps 2,000.",
              [U("odi.finished_good", "FG-0013", NetUnitsOrdered=2000, ActualWrappedUnits=2000,
                 note="MIPS netted 2,000 carried-in units")])],
        {"R01-900111": "inventory covers only the first 1,500-unit line: 500 units of over-production risk"}),
    Scenario(
        "S2", 2, "Inventory parked on a zero-quantity line",
        "MIPS nets 200 units of inventory for SIM CINNAMON RAISIN through a new zero-quantity planning line, "
        "and ODI schedules that line as a blind run. Nothing records the inventory.",
        ("900112",),
        [Step("MIPS adds a zero-quantity supply-plan line and nets 200 units; the run is cut to 700.",
              [I("mips.order_line", {
                  "Order Line ID": "ML-S2A", "Plant Code": "UBD", "Plant Name": "LEHIGH VALLEY PA BAKERY",
                  "Schedule Date": "sales", "Offset Sales Date": "sales", "BUSS Sales Date": "sales",
                  "Order Moved": False, "Product Sales Date": "sales", "FG Oracle Ref": "900112",
                  "FG BUSS Code": "009112", "FG Description": "SIM CINNAMON RAISIN 16Z", "Order Quantity": 0,
                  "Container Code": "01", "Units per Container": 12, "Units Per Carton": 0,
                  "Containers per Rack": 21, "Retail Price": 5.29, "Closed": "F",
                  "Source System": "OCloud Region2", "Scheduling Errors": ""}),
               U("odi.finished_good", "FG-0014", NetUnitsOrdered=700, ActualWrappedUnits=700)]),
         Step("ODI schedules the zero line on Line 1 as its own run.",
              [I("odi.finished_good", {
                  "FGEntryID": "FG-S2A", "PlantName": "BREINIGSVILLE (LEHIGH VALLEY) PA BAKERY (UBD)",
                  "SalesDate": "sales", "ProductionStartDateTime": "now", "ProductionEndDateTime": "now", "LineCode": "Line 1", "ShiftName": "Shift 2", "OracleTypeCode": "SU900220",
                  "LegacyFGNumber": "009112", "OracleFGNumber": "900112", "OCloudFGItemStructure": "Primary",
                  "OracleFGDescription": "SIM CINNAMON RAISIN 16Z", "NetUnitsOrdered": 0, "ActualWrappedUnits": 0,
                  "MIPSBeginningInventoryQty": 0, "MIPSOrderQty": 0, "OraclePiecesPerPkg": 1})])],
        {"R02-900112": "200 units netted with no beginning inventory recorded",
         "R03-SU900220-900112": "a zero-quantity run with a shift: a blind line"}),
    Scenario(
        "S3", 3, "Production run moved to another line",
        "SIM BUTTER TOP's run moves from Line 2 to Line 1 (formula SU900210A). ODI leaves a zero copy with no "
        "shift on Line 2.",
        ("900111",),
        [Step("The run is re-created on Line 1; the Line 2 entry is zeroed and loses its shift.",
              [I("odi.finished_good", {
                  "FGEntryID": "FG-S3A", "PlantName": "BREINIGSVILLE (LEHIGH VALLEY) PA BAKERY (UBD)",
                  "SalesDate": "sales", "ProductionStartDateTime": "now", "ProductionEndDateTime": "now", "LineCode": "Line 1", "ShiftName": "Shift 1", "OracleTypeCode": "SU900210A",
                  "LegacyFGNumber": "009111", "OracleFGNumber": "900111", "OCloudFGItemStructure": "Primary",
                  "OracleFGDescription": "SIM BUTTER TOP 20Z", "NetUnitsOrdered": 4000, "ActualWrappedUnits": 4000,
                  "MIPSBeginningInventoryQty": 0, "MIPSOrderQty": 1500, "OraclePiecesPerPkg": 1}),
               U("odi.finished_good", "FG-0013", NetUnitsOrdered=0, ActualWrappedUnits=0, MIPSOrderQty=0,
                 ShiftName=None)])],
        {"R04-SU900210-900111": "the zero copy left on Line 2 is a ghost of the moved run"}),
    Scenario(
        "S4", 4, "Recovery run double-counts demand",
        "SIM CINNAMON RAISIN's run wraps 100 short. WMS still fills from stock, then a recovery run is "
        "ordered for the full 900 instead of the 100 missing.",
        ("900112",),
        [Step("The run wraps 800 of 900.", [U("odi.finished_good", "FG-0014", ActualWrappedUnits=800)]),
         Step("Ninety minutes later a recovery run on Line 1 makes the full 900 again.",
              [I("odi.finished_good", {
                  "FGEntryID": "FG-S4A", "PlantName": "BREINIGSVILLE (LEHIGH VALLEY) PA BAKERY (UBD)",
                  "SalesDate": "sales", "ProductionStartDateTime": "now", "ProductionEndDateTime": "now", "LineCode": "Line 1", "ShiftName": "Shift 3", "OracleTypeCode": "SU900221",
                  "LegacyFGNumber": "009112", "OracleFGNumber": "900112", "OCloudFGItemStructure": "Primary",
                  "OracleFGDescription": "SIM CINNAMON RAISIN 16Z", "NetUnitsOrdered": 900,
                  "ActualWrappedUnits": 900, "MIPSBeginningInventoryQty": 0, "MIPSOrderQty": 0,
                  "OraclePiecesPerPkg": 1})], advance="90m")],
        {"R05-900112": "the recovery run over-builds by 800"}),
    Scenario(
        "S5", 5, "Return to plant does not reduce the received quantity",
        "WMS returns 120 damaged units of SIM BUTTER TOP to the plant against ticket TK-0010. Oracle posts the "
        "return, but the ticket keeps its full received quantity.",
        ("900111",),
        [Step("WMS books the return; Oracle posts it.",
              [I("wms.transaction", {"Txn Nbr": "WT-S5A", "Txn Type": "RETURN_TO_PLANT", "Txn Date Time": "now",
                                     "Item Nbr": "900111", "Qty": 120, "Ticket Nbr": "TK-0010",
                                     "Reason Code": "DAMAGED", "User": "SIM RETURNS"}),
               posting("OT-S5A", "WT-S5A", "WMS Return to Plant", "900111", -120)])],
        {"R19-TK-0010": "the ticket still shows 4,000 received, not 3,880",
         "R07-900111": "WMS allocated 4,000 but only 3,880 usable units were made"}),
    Scenario(
        "S6", 6, "Order changed after MIPS last synced (stale screen)",
        "Route 12 raises its SIM CINNAMON RAISIN order from 900 to 1,100 in WMS. MIPS has not refreshed, so "
        "its screen still plans 900.",
        ("900112",),
        [Step("WMS takes the bigger order; the order header follows.",
              [U("wms.item_order", "WL-0023", Order_Qty=1100, note="Route 12 raised its order"),
               U("wms.order", "9900003", Order_Qty=8100)])],
        {"R09-900112": "WMS carries 200 more than MIPS scheduled",
         "R10-900112": "WMS is 200 short against the bigger order"}),
    Scenario(
        "S7", 7, "Freezer inventory not deducted",
        "Route 27 needs 300 more SIM BUTTER TOP. WMS fills them from the freezer and MIPS raises the plan, "
        "but nothing deducts the freezer stock from what MIPS believes was made.",
        ("900111",),
        [Step("MIPS plans 300 more and sends them to ODI, but the plant makes nothing extra: WMS allocates "
              "them from the freezer and posts the move.",
              [U("mips.order_line", "ML-0016", Order_Quantity=2800),
               U("odi.finished_good", "FG-0013", NetUnitsOrdered=4300),
               U("wms.item_order", "WL-0022", Order_Qty=1300, Allocated_Units_Qty=1300),
               U("wms.order", "9900004", Order_Qty=4500, Allocated_Qty=4300),
               I("wms.transaction", {"Txn Nbr": "WT-S7A", "Txn Type": "FREEZER_OUT", "Txn Date Time": "now",
                                     "Item Nbr": "900111", "Qty": 300, "Reason Code": "FILL", "User": "SIM FREEZER"}),
               posting("OT-S7A", "WT-S7A", "WMS Freezer Issue", "900111", 300)])],
        {"R07-900111": "300 units shipped from stock MIPS cannot see"}),
    Scenario(
        "S8", 8, "WMS transactions missing in Oracle",
        "The SIM DC SOUTH trailer ships. Oracle posts four of the six item shipments, rejects one, and never "
        "receives the sixth. After 10 plant minutes both are overdue.",
        (),
        [Step("WMS ships order 9900002, one transaction per item.",
              [U("wms.order", "9900002", Loaded_Qty=11800, Shipped_Qty=11800),
               ship("WT-S8A", "900101", 3000, "9900002"), ship("WT-S8B", "900102", 2000, "9900002"),
               ship("WT-S8C", "900103", 2500, "9900002"), ship("WT-S8D", "900106", 1600, "9900002"),
               ship("WT-S8E", "900108", 1500, "9900002"), ship("WT-S8F", "900110", 1200, "9900002")]),
         Step("Oracle posts four, rejects 900110's (lot not found), and never sees 900101's.",
              [posting("OT-S8B", "WT-S8B", "WMS Shipment", "900102", -2000),
               posting("OT-S8C", "WT-S8C", "WMS Shipment", "900103", -2500),
               posting("OT-S8D", "WT-S8D", "WMS Shipment", "900106", -1600),
               posting("OT-S8E", "WT-S8E", "WMS Shipment", "900108", -1500),
               posting("OT-S8F", "WT-S8F", "WMS Shipment", "900110", -1200, status="ERROR",
                       error="Lot not found in subinventory FG")], advance="2m"),
         Step("Ten plant minutes pass.", advance="9m")],
        {"R18-WT-S8A": "Oracle never received the 900101 shipment",
         "R18-WT-S8F": "Oracle rejected the 900110 shipment"}),
)}


def _resolve(log: EventLog, change: Change, now: dt.datetime, sales_date: dt.date) -> Change:
    """The change as the source system would commit it: placeholders filled, derived columns recomputed,
    and for an update only the columns that actually change."""
    e = contract.entity(change.entity)
    row = dict(change.row or {})
    for col, v in row.items():   # placeholders: "sales" = the starting day's sales date, "now" = plant time
        if v == "sales":
            row[col] = sales_date
        elif v == "now":
            row[col] = now
    if change.op == "I":
        full = derive(e.name, contract.validate(e, row), now=now)
        return Change(e.name, "I", str(full[e.key]), contract.encode(full), note=change.note)
    if change.op == "U":
        current = log.current(e.name, change.key)
        if current is None:
            raise contract.ContractError(f"{e.name} {change.key} does not exist; reset to the starting day first")
        typed = derive(e.name, contract.validate(e, {**current, **contract.encode(row)}), now=now)
        merged = contract.encode(typed)
        return Change(e.name, "U", change.key, {c: v for c, v in merged.items() if v != current.get(c)},
                      note=change.note)
    return change


def sales_date_of(log: EventLog) -> dt.date:
    from .projector import Projector
    p = Projector(log, stream="source")
    p.catch_up()
    dates = [r["Schedule Date"] for r in p.rows("mips.order_line") if r["Schedule Date"]]
    return max(set(dates), key=dates.count).date() if dates else dt.date.today()


def run(log: EventLog, scenario: Scenario, pause: float = 0.0,
        on_step: Callable[[int, Step, list[Event]], None] | None = None) -> list[Event]:
    """Play a scenario: advance the plant clock where a step says so, publish its changes in
    order (each through the normal path, so faults apply), and wait `pause` real seconds
    between steps so a watcher sees each step land."""
    clock = SimClock(log, cache_seconds=0)
    sales = sales_date_of(log)
    published: list[Event] = []
    for i, step in enumerate(scenario.steps):
        if i and pause:
            time.sleep(pause)
        if step.advance:
            clock.advance(parse_duration(step.advance))
        events = []
        for c in step.changes:
            now = clock.now()
            change = _resolve(log, c, now, sales)
            change.scenario = scenario.id
            ev = log.publish(change, producer=f"scenario:{scenario.id}", now=now)
            if ev:
                events.append(ev)
        published += events
        if on_step:
            on_step(i, step, events)
    return published
