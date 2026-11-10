"""Known mismatch scenarios as scripts of source-system changes.

Each scenario plays a short story on SKU 76108, the workbook's own example, which starts
clean: every scenario breaks one step of the demand-to-delivery flow
(docs/DATA_MODEL_9NOV.md). Steps publish through the normal producer path, so injected
faults apply to them.

Nothing here is a result. A change is relative to what the row holds when the step runs
(`add(600)` raises a quantity by 600), and what a scenario will raise is worked out live by
`preview`, which plays it on a scratch copy of the log and runs the engine on both sides.
`appears` and `clears` are only the flags expected on the untouched starting day: the
regression oracle that tests/test_scenarios.py checks.

Work-order numbers carry the sales date: "{date}" in a key or value is the starting day's
date (YYYYMMDD). Run one with `events scenario run S5`, or from the simulator's Scenarios tab.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
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
    number: int
    title: str
    story: str
    skus: tuple[str, ...]
    steps: list[Step]
    appears: tuple[str, ...]                         # flag IDs raised on the untouched starting day (tests)
    clears: tuple[str, ...] = ()


def U(entity: str, key: str, note: str | None = None, **cols) -> Change:
    return Change(entity, "U", key, {k.replace("_", " "): v for k, v in cols.items()}, note=note)


def I(entity: str, row: dict, note: str | None = None) -> Change:
    return Change(entity, "I", row=row, note=note)


def D(entity: str, key: str, note: str | None = None) -> Change:
    return Change(entity, "D", key, note=note)


def add(n: float) -> Callable:
    """A quantity change relative to what the row holds when the step runs."""
    return lambda current: (current or 0) + n


WO1 = "WO-UBD-{date}-001"                            # 76108's work order
ODI_76108 = [f"ODI-{n:04d}" for n in range(1, 13)]   # FC001 first, then the 11 orders in workbook order
ODI_ON_WO = [k for k in ODI_76108 if k != "ODI-0003"]  # Bay Shore (ODI-0003) is served from allocated stock

SCENARIOS: dict[str, Scenario] = {s.id: s for s in (
    Scenario(
        "S1", 1, "A customer raises a BUSS order; ODI is not refreshed",
        "ALBANY NY BAKERY raises its 76108 order 2263245 by 600 units in BUSS. The ODI mapping is not refreshed.",
        ("76108",),
        [Step("BUSS takes the bigger order (+600).",
              [U("buss.sales_order", "2263245-1", Order_Quantity=add(600), note="Albany raised its order")])],
        ("R02-2263245-1", "R04-76108", "R05-76108")),
    Scenario(
        "S2", 2, "A new BUSS order line never reaches ODI",
        "BBU_LANCASTER NY CMB places a new 900-unit 76108 order (2263260) in BUSS. The BUSS to ODI interface "
        "drops it.",
        ("76108",),
        [Step("BUSS records the new order; nothing arrives in ODI.",
              [I("buss.sales_order", {
                  "SO Line ID": "2263260-1", "Order Number": "2263260", "Order Line": 1,
                  "Order Type": "DSD_SALES_ORDER", "Order Date": "sales", "Customer Name": "BBU_LANCASTER NY CMB",
                  "Ship-To Location (USA)": "Lancaster, NY, USA", "FG BUSS Code": "2771", "Item Number": "76108",
                  "Item Description": "BP WHITE HOT DOG BUNS 8P 14Z", "UOM": "Units", "Order Quantity": 900,
                  "Requested Ship Date": "sales", "Sales Date": "sales", "Price in $": 4.69})])],
        ("R01-2263260-1", "R04-76108", "R05-76108")),
    Scenario(
        "S3", 3, "A customer cancels in BUSS; ODI keeps the line",
        "WILMINGTON MA cancels its 76108 order 2263256 in BUSS. The ODI mapping keeps the line.",
        ("76108",),
        [Step("BUSS deletes the order line.", [D("buss.sales_order", "2263256-1", note="order cancelled")])],
        ("R03-ODI-0012", "R04-76108", "R05-76108")),
    Scenario(
        "S4", 4, "WMS allocates more stock; ODI and MIPS keep the old allocation",
        "WMS reserves 660 more 76108 for an order, moving them from available to allocated. ODI and MIPS keep "
        "the allocation they had.",
        ("76108",),
        [Step("WMS moves 660 units from available to allocated.",
              [U("wms.inventory", "INV-0001", Available_quantity=add(-660), Allocated_quantity=add(660))])],
        ("R09-76108", "R04-76108")),
    Scenario(
        "S5", 5, "MIPS re-plans the work order; ODI keeps the old quantity",
        "A planner raises 76108's work order by 1,365 units in MIPS for safety. ODI keeps mapping the old quantity.",
        ("76108",),
        [Step("MIPS saves the bigger work order (+1,365).",
              [U("mips.work_order", WO1, Work_Order_Quantity=add(1365), note="planner override")])],
        ("R05-76108", "R07-WO-UBD-20260912-001")),
    Scenario(
        "S6", 6, "A WMS cycle count changes on hand only",
        "A cycle count at 76108's locator books 100 fewer buns against on hand only. Available and allocated stay "
        "as they were.",
        ("76108",),
        [Step("WMS books the count against on hand (-100).", [U("wms.inventory", "INV-0001", On_hand=add(-100))])],
        ("R10-INV-0001",)),
    Scenario(
        "S7", 7, "The line runs late: wrapping ends after the ship dates",
        "Line 1 goes down and 76108's work order wraps now, in plant time, after the requested ship date of "
        "orders that ship earlier.",
        ("76108",),
        [Step("ODI records the late wrap on every row of the work order.",
              [U("odi.batch_mapping", k, Wrapping_Start_Date_Time="now", Wrapping_End_Date_Time="now")
               for k in ODI_ON_WO])],
        ("R12-WO-UBD-20260912-001",)),
    Scenario(
        "S8", 8, "The work order is deleted in MIPS",
        "76108's work order is deleted in MIPS by mistake. ODI still maps the demand to it.",
        ("76108",),
        [Step("MIPS deletes the work order.", [D("mips.work_order", WO1, note="deleted by mistake")])],
        ("R06-76108", "R07-WO-UBD-20260912-001")),
    Scenario(
        "S9", 9, "ODI loses the work-order link on a demand line",
        "An ODI reload drops the work order from ALBANY NY BAKERY's line. The line has no allocated stock either.",
        ("76108",),
        [Step("ODI rewrites the Albany row without its work order.",
              [U("odi.batch_mapping", "ODI-0002", Work_Order_Number=None, Work_Order_Date=None,
                 Work_Order_Quantity=None)])],
        ("R08-ODI-0002",)),
    Scenario(
        "S10", 10, "An order arrives with a mistyped BUSS item code",
        "CHICOPEE MA's 76108 order arrives from BUSS with a mistyped FG BUSS Code (2717).",
        ("76108",),
        [Step("BUSS sends the order with the wrong code.",
              [U("buss.sales_order", "2263257-1", FG_BUSS_Code="2717")])],
        ("R13-76108",)),
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
        row = {col: v(current.get(col)) if callable(v) else v for col, v in row.items()}   # relative changes
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


# --- live preview ---------------------------------------------------------------------------------

def _source_flags(log: EventLog) -> dict:
    from .. import engine
    from .projector import Projector
    p = Projector(log, stream="source")
    p.catch_up()
    return {f.id: f for f in engine.run(src=p.source_data(), use_ai=False).findings}


def flag_diff(before: dict, after: dict) -> dict:
    """What changed between two runs: flags raised, cleared, and still open with new figures."""
    return {"appears": [after[k] for k in after if k not in before],
            "clears": [before[k] for k in before if k not in after],
            "changes": [after[k] for k in after if k in before and
                        (round(after[k].units, 2), after[k].recommendation) !=
                        (round(before[k].units, 2), before[k].recommendation)]}


def preview(log: EventLog, scenario: Scenario, before: dict | None = None) -> dict:
    """What `scenario` would raise and clear if it ran now: it plays on a scratch copy of the log, so
    the answer follows the current source data, whatever the operator has changed. `before` is the
    current run's flags, when the caller already has them."""
    import shutil
    folder = Path(tempfile.mkdtemp(prefix="recon-preview-"))
    try:
        with closing(sqlite3.connect(log.path)) as src, closing(sqlite3.connect(folder / "events.db")) as dst:
            src.backup(dst)
        copy = EventLog(folder / "events.db")
        before = _source_flags(log) if before is None else before
        try:
            run(copy, scenario)
        except (contract.ContractError, KeyError, ValueError) as err:
            return {"appears": [], "clears": [], "changes": [], "error": str(err)}
        return {**flag_diff(before, _source_flags(copy)), "error": ""}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def describe(diff: dict) -> str:
    """One line for the operator: what the worklist will show, from a flag_diff."""
    def fmt(fs):
        return ", ".join(f"{f.id} ({abs(f.units):,.0f} units)" if f.units else f.id for f in fs)
    parts = [f"Raised: {fmt(diff['appears'])}" if diff["appears"] else "",
             f"changed: {fmt(diff['changes'])}" if diff["changes"] else "",
             f"cleared: {fmt(diff['clears'])}" if diff["clears"] else ""]
    text = "; ".join(p for p in parts if p)
    return (text[0].upper() + text[1:] + ".") if text else "No flag changed."


def preview_all(log: EventLog) -> dict[str, dict]:
    before = _source_flags(log)
    return {sid: preview(log, sc, before) for sid, sc in SCENARIOS.items()}
