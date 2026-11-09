"""Known mismatch scenarios as scripts of source-system changes.

Each scenario plays a short story against the starting day (seed.py) on SKU 76108, the
workbook's own example, which starts clean: the flag visibly appears on the worklist
when it runs. Every scenario breaks one step of the demand-to-delivery flow
(docs/DATA_MODEL_9NOV.md). Steps publish through the normal producer path, so injected
faults apply to them. `appears` lists the flags the scenario must raise (or clear) when
run on the untouched starting day; tests/test_scenarios.py checks every one, so the
library doubles as the regression suite for the rules.

Work-order numbers carry the sales date: "{date}" in a key or value is the starting
day's date (YYYYMMDD). Run one with `events scenario run S5`, or from the simulator's
Scenarios tab.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Callable

from . import contract, seed
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
    number: int
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


def D(entity: str, key: str, note: str | None = None) -> Change:
    return Change(entity, "D", key, note=note)


WO1 = "WO-UBD-{date}-001"                            # 76108's work order
_WO1 = seed.wo_number(seed.DEFAULT_SALES_DATE, 1)
ODI_76108 = [f"ODI-{n:04d}" for n in range(1, 13)]   # FC001 first, then the 11 orders in workbook order
ODI_ON_WO = [k for k in ODI_76108 if k != "ODI-0003"]  # Bay Shore (ODI-0003) is served from allocated stock

SCENARIOS: dict[str, Scenario] = {s.id: s for s in (
    Scenario(
        "S1", 1, "A customer raises a BUSS order; ODI is not refreshed",
        "ALBANY NY BAKERY raises its 76108 order 2263245 from 7,200 to 7,800 in BUSS. The ODI mapping keeps 7,200, "
        "so MIPS plans its demand and the work order 600 short.",
        ("76108",),
        [Step("BUSS takes the bigger order.",
              [U("buss.sales_order", "2263245-1", Order_Quantity=7800, note="Albany raised its order")])],
        {"R02-2263245-1": "BUSS has 7,800; ODI still maps 7,200",
         "R04-76108": "MIPS plans 55,635; the sources leave 56,235",
         "R05-76108": "the work order is 600 short of the 49,235 shortfall"}),
    Scenario(
        "S2", 2, "A new BUSS order line never reaches ODI",
        "BBU_LANCASTER NY CMB places a second 76108 order (2263260, 900 units). The BUSS to ODI interface drops it, "
        "so neither ODI nor MIPS knows the demand exists.",
        ("76108",),
        [Step("BUSS records the new order; nothing arrives in ODI.",
              [I("buss.sales_order", {
                  "SO Line ID": "2263260-1", "Order Number": "2263260", "Order Line": 1,
                  "Order Type": "DSD_SALES_ORDER", "Order Date": "sales", "Customer Name": "BBU_LANCASTER NY CMB",
                  "Ship-To Location (USA)": "Lancaster, NY, USA", "FG BUSS Code": "2771", "Item Number": "76108",
                  "Item Description": "BP WHITE HOT DOG BUNS 8P 14Z", "UOM": "Units", "Order Quantity": 900,
                  "Requested Ship Date": "sales", "Sales Date": "sales"})])],
        {"R01-2263260-1": "the 900-unit order is in BUSS but not in the ODI mapping",
         "R04-76108": "MIPS plans 55,635; the sources leave 56,535",
         "R05-76108": "the work order is 900 short"}),
    Scenario(
        "S3", 3, "A customer cancels in BUSS; ODI keeps the line",
        "WILMINGTON MA cancels its 352-unit 76108 order in BUSS. The ODI mapping keeps the line, so MIPS still plans "
        "for it: 352 units of over-production.",
        ("76108",),
        [Step("BUSS deletes the order line.", [D("buss.sales_order", "2263256-1", note="order cancelled")])],
        {"R03-ODI-0012": "ODI still maps the cancelled Wilmington order",
         "R04-76108": "MIPS plans 352 more than the sources leave",
         "R05-76108": "the work order makes 352 too many"}),
    Scenario(
        "S4", 4, "WMS allocates more stock; ODI and MIPS keep the old allocation",
        "WMS reserves 660 more 76108 for BBU_MASPETH NY PAGE from available stock (allocated 473 to 1,133, "
        "available 7,000 to 6,340). The shortfall is unchanged, but ODI and MIPS still carry the old allocation.",
        ("76108",),
        [Step("WMS moves 660 units from available to allocated.",
              [U("wms.inventory", "INV-0001", Available_quantity=6340, Allocated_quantity=1133)])],
        {"R09-76108": "WMS has 1,133 allocated; ODI shows 473",
         "R04-76108": "MIPS plans 55,635; demand less the new allocation is 54,975"}),
    Scenario(
        "S5", 5, "MIPS re-plans the work order; ODI keeps the old quantity",
        "A planner raises work order " + _WO1 + " in MIPS from 48,635 to 50,000 for safety. The shortfall is still "
        "48,635, and ODI keeps mapping the old quantity.",
        ("76108",),
        [Step("MIPS saves the bigger work order.",
              [U("mips.work_order", WO1, Work_Order_Quantity=50000, note="planner override")])],
        {"R05-76108": "the work order makes 1,365 more than the shortfall",
         f"R07-{_WO1}": "ODI still carries 48,635 for the work order"}),
    Scenario(
        "S6", 6, "A WMS cycle count changes on hand only",
        "A cycle count at FG.1.1.1 finds 100 fewer 76108 buns. WMS books the count against on hand but leaves "
        "available and allocated as they were, so WMS offers stock it does not have.",
        ("76108",),
        [Step("WMS books the count: on hand 7,473 to 7,373.", [U("wms.inventory", "INV-0001", On_hand=7373)])],
        {"R10-INV-0001": "on hand 7,373 but available 7,000 + allocated 473 = 7,473"}),
    Scenario(
        "S7", 7, "The line runs late: wrapping ends after the ship dates",
        "Line 1 goes down and work order " + _WO1 + " wraps on the evening before the sales day, after the "
        "requested ship dates of six orders. Finished goods reach WMS too late for them.",
        ("76108",),
        [Step("ODI records the late wrap on every row of the work order.",
              [U("odi.batch_mapping", k, Wrapping_Start_Date_Time="now", Wrapping_End_Date_Time="now")
               for k in ODI_ON_WO])],
        {f"R12-{_WO1}": "wrapping ended after the requested ship date of six orders (4,664 units)"}),
    Scenario(
        "S8", 8, "The work order is deleted in MIPS",
        "Work order " + _WO1 + " is deleted in MIPS by mistake. The shortfall of 48,635 has no work order, and ODI "
        "still maps the demand to the deleted one.",
        ("76108",),
        [Step("MIPS deletes the work order.", [D("mips.work_order", WO1, note="deleted by mistake")])],
        {"R06-76108": "48,635 short with no work order",
         f"R07-{_WO1}": "ODI maps 11 demand lines to a work order MIPS no longer has"}),
    Scenario(
        "S9", 9, "ODI loses the work-order link on a demand line",
        "An ODI reload drops the work order from ALBANY NY BAKERY's 7,200-unit order. The line has no allocated "
        "stock either, so nothing produces it.",
        ("76108",),
        [Step("ODI rewrites the Albany row without its work order.",
              [U("odi.batch_mapping", "ODI-0002", Work_Order_Number=None, Work_Order_Date=None,
                 Work_Order_Quantity=None)])],
        {"R08-ODI-0002": "7,200 units neither allocated nor on a work order"}),
    Scenario(
        "S10", 10, "An order arrives with a mistyped BUSS item code",
        "CHICOPEE MA's 76108 order comes from BUSS with FG BUSS Code 2717 instead of 2771. Joins on the BUSS code "
        "between BUSS and MIPS fail for this order.",
        ("76108",),
        [Step("BUSS sends the order with the wrong code.",
              [U("buss.sales_order", "2263257-1", FG_BUSS_Code="2717")])],
        {"R13-76108": "BUSS orders use codes 2717 and 2771; MIPS uses 2771"}),
)}


def _fill(value, sales_date: dt.date):
    return value.replace("{date}", f"{sales_date:%Y%m%d}") if isinstance(value, str) else value


def _resolve(log: EventLog, change: Change, now: dt.datetime, sales_date: dt.date) -> Change:
    """The change as the source system would commit it: placeholders filled, derived columns recomputed,
    and for an update only the columns that actually change."""
    e = contract.entity(change.entity)
    key = _fill(change.key, sales_date)
    row = {col: _fill(v, sales_date) for col, v in (change.row or {}).items()}
    for col, v in row.items():   # placeholders: "sales" = the starting day's sales date, "now" = plant time
        if v == "sales":
            row[col] = sales_date
        elif v == "now":
            row[col] = now
    if change.op == "I":
        full = derive(e.name, contract.validate(e, row), now=now)
        return Change(e.name, "I", str(full[e.key]), contract.encode(full), note=change.note)
    current = log.current(e.name, key)
    if current is None:
        raise contract.ContractError(f"{e.name} {key} does not exist; reset to the starting day first")
    if change.op == "U":
        typed = derive(e.name, contract.validate(e, {**current, **contract.encode(row)}), now=now)
        merged = contract.encode(typed)
        return Change(e.name, "U", key, {c: v for c, v in merged.items() if v != current.get(c)}, note=change.note)
    return Change(e.name, "D", key, note=change.note)


def sales_date_of(log: EventLog) -> dt.date:
    from .projector import Projector
    p = Projector(log, stream="source")
    p.catch_up()
    dates = [r["Sales Date"] for r in p.rows("buss.sales_order") if r["Sales Date"]]
    return max(set(dates), key=dates.count).date() if dates else dt.date.today()


def run(log: EventLog, scenario: Scenario, pause: float = 0.0,
        on_step: Callable[[int, Step, list[Event]], None] | None = None) -> list[Event]:
    """Play a scenario: advance the plant clock where a step says so, publish its changes in
    order (each through the normal path, so faults apply), and wait `pause` real seconds
    between steps so a watcher sees each step land.

    A step is one business transaction at the source (an item line and its order header, say),
    so its changes commit together: a worklist reloading mid-step would otherwise reconcile a
    half-applied step and show flags that vanish a moment later. Each change is resolved against
    the committed rows, so a step must not touch the same row twice."""
    clock = SimClock(log, cache_seconds=0)
    sales = sales_date_of(log)
    published: list[Event] = []
    for i, step in enumerate(scenario.steps):
        if i and pause:
            time.sleep(pause)
        if step.advance:
            clock.advance(parse_duration(step.advance))
        events = []
        if step.changes:
            now = clock.now()
            resolved = [_resolve(log, c, now, sales) for c in step.changes]
            for change in resolved:
                change.scenario = scenario.id
            events = [ev for ev in log.publish_many(resolved, producer=f"scenario:{scenario.id}", now=now) if ev]
        published += events
        if on_step:
            on_step(i, step, events)
    return published
