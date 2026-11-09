"""Mismatch detectors: one rule per step of the demand-to-delivery flow.

The flow (docs/DATA_MODEL_9NOV.md): capture demand from BUSS orders and the Oracle
forecast, remove the stock WMS has allocated, subtract what WMS has available, size
the MIPS work order to the shortfall, map every demand line to that work order in
ODI, produce and wrap before the ship date. Each rule checks that one system holds
its part of that chain, produces evidence from the source rows, and proposes a
quantified correction. The rules are deterministic so every finding is auditable;
the AI layer explains and prioritises them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .ingest import SourceData
from .position import SkuPosition

# code: (title, flow step, challenge area, base severity)
RULES = {
    "R01": ("Demand line missing from the ODI mapping", "1 - Capture demand", "Integration Architecture", "High"),
    "R02": ("ODI quantity differs from the order or forecast", "1 - Capture demand", "Integration Architecture", "Medium"),
    "R03": ("ODI maps demand that no longer exists", "1 - Capture demand", "Integration Architecture", "Medium"),
    "R04": ("MIPS demand is not demand less allocated stock", "3 - Remove allocated stock", "Inventory Allocation", "Medium"),
    "R05": ("Work order not sized to the shortfall", "4/5 - Shortfall to work order", "Inventory Allocation", "High"),
    "R06": ("Shortfall with no work order", "5 - Create work order", "Production Visibility", "High"),
    "R07": ("ODI work order differs from MIPS", "5 - Work order to ODI", "Order-to-Batch Traceability", "Medium"),
    "R08": ("Demand line neither allocated nor on a work order", "3/5 - Allocate or produce", "Order-to-Batch Traceability", "Medium"),
    "R09": ("Allocated stock differs between WMS and ODI", "3 - Remove allocated stock", "Inventory Synchronization", "Medium"),
    "R10": ("WMS on hand is not available plus allocated", "4 - Check available stock", "Warehouse Traceability", "Medium"),
    "R11": ("Batches mixed differ from the MIPS schedule", "7 - Produce, then wrap", "Order-to-Batch Traceability", "Low"),
    "R12": ("Finished goods wrapped after the requested ship date", "8 - Move FG to WMS", "Production Visibility", "High"),
    "R13": ("Item codes differ between systems", "Master data", "Data Flow Transparency", "Low"),
}

SEVERITY_WEIGHT = {"High": 3.0, "Medium": 2.0, "Low": 1.0}


@dataclass
class Finding:
    rule: str
    key: str
    sku: str
    description: str
    units: float
    unit_price: float
    evidence: dict
    recommendation: str
    severity: str = ""
    confidence: float = 1.0
    narrative: str = ""
    status: str = "Open"
    systems: list[str] = field(default_factory=list)
    base_priority: float = 50.0  # floor so zero-$ control findings still rank
    # Which system misfired and what each system did (accountability.py), set by the engine.
    accountability: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.rule}-{self.key}"

    @property
    def title(self) -> str:
        return RULES[self.rule][0]

    @property
    def scenario(self) -> str:
        return RULES[self.rule][1]

    @property
    def area(self) -> str:
        return RULES[self.rule][2]

    @property
    def dollars(self) -> float:
        return abs(self.units) * self.unit_price

    @property
    def priority(self) -> float:
        # $ exposure scaled by severity and by learned rule confidence.
        return (self.dollars + self.base_priority) * SEVERITY_WEIGHT[self.severity] * self.confidence

    def __post_init__(self):
        self.severity = self.severity or RULES[self.rule][3]


def _fmt(x: float) -> str:
    return f"{x:,.0f}"


def _sized(units: float, high: str = "High", low: str = "Medium") -> str:
    return high if abs(units) >= config.HIGH_UNITS else low


def _differs(a: float, b: float) -> bool:
    return abs(a - b) > config.QTY_TOLERANCE_UNITS


def _equation(p: SkuPosition) -> str:
    return (f"{_fmt(p.so_qty)} ordered + {_fmt(p.forecast_qty)} forecast - {_fmt(p.allocated)} allocated - "
            f"{_fmt(p.available)} available = {_fmt(p.shortfall)}")


# --------------------------------------------------------------------------
# 1. Capture demand: every BUSS order line and Oracle forecast reaches ODI unchanged
# --------------------------------------------------------------------------

def r01_r02_r03_demand_capture(p: SkuPosition) -> list[Finding]:
    out = []
    odi_so = {(e.ref, e.ref_line or 1): e for e in p.odi if e.is_sales_order}
    odi_fc = {e.ref: e for e in p.odi if not e.is_sales_order}
    sources: list[tuple[str, str, str, float, object]] = (
        [("BUSS", f"{o.order_nbr}-{o.line}", f"sales order {o.order_nbr} line {o.line} ({o.customer})", o.qty,
          odi_so.get(o.ref)) for o in p.orders]
        + [("Oracle", f.forecast_id, f"forecast {f.forecast_id}", f.qty, odi_fc.get(f.forecast_id))
           for f in p.forecasts])
    for system, key, label, qty, entry in sources:
        if entry is None:
            out.append(Finding(
                "R01", key, p.sku, p.description, qty, p.unit_price,
                evidence={"source_system": system, "demand_line": label, "source_qty": qty,
                          "odi_demand": p.odi_demand, "total_demand": p.demand},
                recommendation=(
                    f"{system} has {label} for {_fmt(qty)} units, but the ODI batch mapping has no row for it, so "
                    f"MIPS and the work order never see this demand. Re-send it through ODI and add its "
                    f"{_fmt(qty)} units to the work order, or the customer is short."),
                severity=_sized(qty), systems=[system, "ODI", "MIPS"]))
        elif _differs(entry.qty, qty):
            diff = qty - entry.qty
            mips_current = bool(p.work_orders) and not _differs(p.mips_demand, p.demand_left)
            out.append(Finding(
                "R02", key, p.sku, p.description, diff, p.unit_price,
                evidence={"source_system": system, "demand_line": label, "source_qty": qty, "odi_qty": entry.qty,
                          "odi_entry": entry.entry_id, "mips_demand": p.mips_demand, "demand_left": p.demand_left},
                recommendation=(
                    f"{system} has {label} at {_fmt(qty)} units, but ODI still maps {_fmt(entry.qty)} "
                    f"({diff:+,.0f}): the change never reached ODI. Set the ODI row {entry.entry_id} to {_fmt(qty)}"
                    + (f". MIPS already plans the current {_fmt(p.demand_left)} demand left, so only the ODI copy "
                       f"and the batches mapped to it are wrong." if mips_current else
                       ", then re-check the MIPS demand and work order, which follow ODI's figure.")),
                severity=_sized(diff, "High", "Medium"), systems=[system, "ODI", "MIPS"]))
    known_so = {o.ref for o in p.orders}
    known_fc = {f.forecast_id for f in p.forecasts}
    for e in p.odi:
        missing = (e.ref, e.ref_line or 1) not in known_so if e.is_sales_order else e.ref not in known_fc
        if missing:
            what = f"sales order {e.ref} line {e.ref_line or 1}" if e.is_sales_order else f"forecast {e.ref}"
            source = "BUSS" if e.is_sales_order else "Oracle"
            out.append(Finding(
                "R03", e.entry_id, p.sku, p.description, -e.qty, p.unit_price,
                evidence={"odi_entry": e.entry_id, "demand_line": what, "odi_qty": e.qty, "source_system": source,
                          "work_order": e.wo or None},
                recommendation=(
                    f"ODI row {e.entry_id} maps {what} for {_fmt(e.qty)} units, but {source} no longer has it "
                    f"(cancelled or deleted). The {_fmt(e.qty)} units are over-production if MIPS keeps planning "
                    f"them: remove the ODI row and take the units off the work order."),
                severity=_sized(e.qty), systems=[source, "ODI", "MIPS"]))
    return out


# --------------------------------------------------------------------------
# 3-5. MIPS plans demand less allocated stock, and sizes the work order to the shortfall
# --------------------------------------------------------------------------

def r04_mips_demand(p: SkuPosition) -> list[Finding]:
    if not p.work_orders or not _differs(p.mips_demand, p.demand_left):
        return []
    diff = p.demand_left - p.mips_demand
    via_odi = not _differs(p.mips_demand, p.odi_demand_left) and _differs(p.odi_demand, p.demand)
    if via_odi:
        cause = (f"MIPS follows ODI's copy of the demand ({_fmt(p.odi_demand)}), which is "
                 f"{_fmt(abs(p.odi_demand - p.demand))} {'short of' if p.odi_demand < p.demand else 'above'} BUSS "
                 f"and Oracle. Correct the ODI mapping first; then ")
    elif not _differs(p.mips_demand, p.demand):
        cause = f"MIPS did not remove the {_fmt(p.allocated)} units WMS has already allocated to orders. "
    else:
        cause = ""
    return [Finding(
        "R04", p.sku, p.sku, p.description, diff, p.unit_price,
        evidence={"sales_orders": p.so_qty, "forecast": p.forecast_qty, "total_demand": p.demand,
                  "wms_allocated": p.allocated, "demand_left": p.demand_left, "mips_demand": p.mips_demand,
                  "odi_demand": p.odi_demand, "work_orders": ", ".join(p.wo_numbers)},
        recommendation=(
            f"MIPS plans {_fmt(p.mips_demand)} as Forecast / Order Quantity, but BUSS orders "
            f"({_fmt(p.so_qty)}) plus the Oracle forecast ({_fmt(p.forecast_qty)}) less the {_fmt(p.allocated)} "
            f"allocated in WMS leave {_fmt(p.demand_left)} ({diff:+,.0f}). {cause}"
            f"{'set' if cause.endswith('then ') else 'Set'} the MIPS demand to {_fmt(p.demand_left)}."),
        severity=_sized(diff, "High", "Medium"), systems=["MIPS", "ODI", "WMS"])]


def r05_work_order_size(p: SkuPosition) -> list[Finding]:
    if not p.work_orders or not _differs(p.wo_qty, p.shortfall):
        return []
    units = p.shortfall - p.wo_qty   # negative: the work order makes too many
    wos = ", ".join(p.wo_numbers)
    if p.shortfall == 0:
        why = (f"WMS already has {_fmt(p.available)} available against {_fmt(p.demand_left)} demand left, so no "
               f"production is needed. Cancel work order {wos} or cut it to 0")
    elif p.available and not _differs(p.wo_qty, p.demand_left):
        why = (f"MIPS sized the work order to the {_fmt(p.demand_left)} demand left and did not subtract the "
               f"{_fmt(p.available)} units available in WMS. Cut work order {wos} to {_fmt(p.shortfall)}")
    elif not _differs(p.wo_qty, p.demand):
        why = (f"MIPS sized the work order to the full {_fmt(p.demand)} demand, ignoring the {_fmt(p.allocated)} "
               f"allocated and {_fmt(p.available)} available in WMS. Set work order {wos} to {_fmt(p.shortfall)}")
    elif units > 0:
        why = f"Raise work order {wos} to {_fmt(p.shortfall)}, or {_fmt(units)} units of demand go unfilled"
    else:
        why = f"Cut work order {wos} to {_fmt(p.shortfall)}; as planned it makes {_fmt(-units)} too many"
    return [Finding(
        "R05", p.sku, p.sku, p.description, units, p.unit_price,
        evidence={"total_demand": p.demand, "wms_allocated": p.allocated, "demand_left": p.demand_left,
                  "wms_available": p.available, "shortfall": p.shortfall, "work_order_qty": p.wo_qty,
                  "mips_demand": p.mips_demand, "work_orders": wos},
        recommendation=(
            f"The shortfall is {_equation(p)}, but MIPS plans {_fmt(p.wo_qty)} on work order {wos} "
            f"({-units:+,.0f}). {why}."),
        severity=_sized(units), systems=["MIPS", "WMS", "ODI"])]


def r06_missing_work_order(p: SkuPosition) -> list[Finding]:
    if p.work_orders or p.shortfall <= config.QTY_TOLERANCE_UNITS:
        return []
    return [Finding(
        "R06", p.sku, p.sku, p.description, p.shortfall, p.unit_price,
        evidence={"total_demand": p.demand, "wms_allocated": p.allocated, "wms_available": p.available,
                  "shortfall": p.shortfall, "odi_work_orders": ", ".join(sorted(p.odi_wo_qty)) or None},
        recommendation=(
            f"Demand needs {_fmt(p.shortfall)} more units ({_equation(p)}), but MIPS has no work order for "
            f"{p.sku}. Create a work order for {_fmt(p.shortfall)} units, or the orders ship short."),
        severity="High", systems=["MIPS", "ODI"])]


# --------------------------------------------------------------------------
# 5. The work order reaches ODI, and every demand line is allocated or produced
# --------------------------------------------------------------------------

def r07_odi_work_order(p: SkuPosition) -> list[Finding]:
    out = []
    mips = {w.wo: w for w in p.work_orders}
    for wo, odi_qty in p.odi_wo_qty.items():
        rows = [e.entry_id for e in p.odi if e.wo == wo]
        w = mips.get(wo)
        if w is None:
            out.append(Finding(
                "R07", wo, p.sku, p.description, odi_qty, p.unit_price,
                evidence={"odi_work_order": wo, "odi_work_order_qty": odi_qty, "odi_rows": len(rows),
                          "mips_work_orders": ", ".join(p.wo_numbers) or None},
                recommendation=(
                    f"ODI maps {len(rows)} demand {'line' if len(rows) == 1 else 'lines'} to work order {wo} "
                    f"({_fmt(odi_qty)} units), but MIPS has no such work order. Production and batches follow "
                    f"MIPS: re-link the ODI rows to the MIPS work order, or restore it in MIPS."),
                severity="Medium", systems=["MIPS", "ODI"]))
            continue
        stale = [e.entry_id for e in p.odi if e.wo == wo and _differs(e.wo_qty, w.wo_qty)]
        if stale:
            diff = w.wo_qty - odi_qty
            out.append(Finding(
                "R07", wo, p.sku, p.description, diff, p.unit_price,
                evidence={"work_order": wo, "mips_work_order_qty": w.wo_qty, "odi_work_order_qty": odi_qty,
                          "odi_rows_stale": len(stale)},
                recommendation=(
                    f"MIPS has work order {wo} at {_fmt(w.wo_qty)}, but {len(stale)} ODI "
                    f"{'row still carries' if len(stale) == 1 else 'rows still carry'} {_fmt(odi_qty)} "
                    f"({diff:+,.0f}). Re-send the work order from MIPS to ODI so batches are mixed for the "
                    f"current quantity."),
                systems=["MIPS", "ODI"]))
    return out


def r08_uncovered_lines(p: SkuPosition) -> list[Finding]:
    out = []
    for e in p.odi:
        gap = e.qty - e.allocated
        if e.wo or gap <= config.QTY_TOLERANCE_UNITS:
            continue
        what = f"sales order {e.ref}" if e.is_sales_order else f"forecast {e.ref}"
        out.append(Finding(
            "R08", e.entry_id, p.sku, p.description, gap, p.unit_price,
            evidence={"odi_entry": e.entry_id, "demand_line": what, "odi_qty": e.qty, "allocated": e.allocated,
                      "mips_work_orders": ", ".join(p.wo_numbers) or None},
            recommendation=(
                f"ODI row {e.entry_id} ({what}, {_fmt(e.qty)} units) has {_fmt(e.allocated)} allocated and no work "
                f"order, so {_fmt(gap)} units are neither in stock nor in production. "
                + (f"Link the row to work order {p.wo_numbers[0]}." if len(p.wo_numbers) == 1 else
                   "Allocate WMS stock to it or link it to a work order.")),
            severity=_sized(gap), systems=["ODI", "MIPS", "WMS"]))
    return out


def r09_allocation(p: SkuPosition) -> list[Finding]:
    if not p.stock or not p.odi or not _differs(p.odi_allocated, p.allocated):
        return []
    diff = p.allocated - p.odi_allocated
    lines = "; ".join(f"{e.ref} {_fmt(e.allocated)}" for e in p.odi if e.allocated)
    return [Finding(
        "R09", p.sku, p.sku, p.description, diff, p.unit_price,
        evidence={"wms_allocated": p.allocated, "odi_allocated": p.odi_allocated,
                  "odi_allocated_lines": lines or None},
        recommendation=(
            f"WMS has {_fmt(p.allocated)} units allocated to orders, but the ODI mapping shows "
            f"{_fmt(p.odi_allocated)} ({diff:+,.0f}). WMS holds all on-hand stock, so it is the reference: update "
            f"the ODI allocation for the order WMS reserved, then re-check the MIPS demand."),
        severity="Medium", systems=["WMS", "ODI", "MIPS"])]


# --------------------------------------------------------------------------
# 4, 7, 8. WMS balances; the plant mixes what was scheduled and wraps before shipping
# --------------------------------------------------------------------------

def r10_wms_balance(p: SkuPosition) -> list[Finding]:
    out = []
    for s in p.stock:
        diff = s.on_hand - (s.available + s.allocated)
        if not _differs(diff, 0):
            continue
        out.append(Finding(
            "R10", s.inventory_id, p.sku, p.description, diff, p.unit_price,
            evidence={"inventory": f"{s.inventory_id} ({s.facility} {s.locator})", "on_hand": s.on_hand,
                      "available": s.available, "allocated": s.allocated},
            recommendation=(
                f"WMS shows {_fmt(s.on_hand)} on hand at {s.locator or s.facility}, but available "
                f"({_fmt(s.available)}) plus allocated ({_fmt(s.allocated)}) is {_fmt(s.available + s.allocated)} "
                f"({diff:+,.0f}). The shortfall is sized from available stock: count the locator and correct the "
                f"quantity that is wrong before the next work order is planned."),
            severity=_sized(diff, "High", "Medium"), systems=["WMS"]))
    return out


def r11_batches(p: SkuPosition) -> list[Finding]:
    out, seen = [], set()
    for e in p.odi:
        if not e.wo or e.wo in seen or not e.sched_batches:
            continue
        seen.add(e.wo)
        if _differs(e.act_batches, e.sched_batches):
            diff = e.act_batches - e.sched_batches
            out.append(Finding(
                "R11", e.wo, p.sku, p.description, 0, p.unit_price,
                evidence={"work_order": e.wo, "formula": e.oracle_type, "line": e.line, "shift": e.shift,
                          "scheduled": e.sched_batches, "mixed": e.act_batches},
                recommendation=(
                    f"Work order {e.wo} ({e.mips_description or p.description}, {e.line} {e.shift}) mixed "
                    f"{_fmt(e.act_batches)} batches against {_fmt(e.sched_batches)} scheduled in MIPS ({diff:+,.0f}). "
                    f"Record the reason (rework, recovery) so the {'extra' if diff > 0 else 'missing'} output is "
                    f"expected in WMS."),
                systems=["ODI", "MIPS"], base_priority=500))
    return out


def r12_late_wrap(p: SkuPosition) -> list[Finding]:
    late: dict[str, list] = {}
    for e in p.odi:
        if e.is_sales_order and e.wo and e.wrap_end and e.requested_ship and e.wrap_end.date() > e.requested_ship:
            late.setdefault(e.wo, []).append(e)
    out = []
    for wo, rows in late.items():
        units = sum(e.qty - e.allocated for e in rows)
        first = min(e.requested_ship for e in rows)
        out.append(Finding(
            "R12", wo, p.sku, p.description, units, p.unit_price,
            evidence={"work_order": wo, "wrapping_end": f"{rows[0].wrap_end:%m/%d %H:%M}",
                      "earliest_ship_date": f"{first:%m/%d}", "late_orders": len(rows),
                      "orders": ", ".join(e.ref for e in rows)},
            recommendation=(
                f"Work order {wo} finished wrapping {rows[0].wrap_end:%m/%d %H:%M}, after the requested ship date of "
                f"{len(rows)} {'order' if len(rows) == 1 else 'orders'} (earliest {first:%m/%d}; {_fmt(units)} units). "
                f"Finished goods must reach WMS before the ship date: move the run earlier or agree a new ship date "
                f"with the customers."),
            severity=_sized(units), systems=["ODI", "MIPS", "WMS"]))
    return out


def r13_item_codes(p: SkuPosition) -> list[Finding]:
    issues = []
    if not p.in_item_master:
        issues.append(f"{p.sku} is not in the Oracle item master")
    wo_codes = {w.buss_code for w in p.work_orders if w.buss_code}
    if p.buss_codes and wo_codes and p.buss_codes != wo_codes:
        issues.append(f"BUSS orders use FG BUSS Code {', '.join(sorted(p.buss_codes))}, MIPS uses "
                      f"{', '.join(sorted(wo_codes))}")
    elif len(p.buss_codes) > 1:
        issues.append(f"BUSS orders carry {len(p.buss_codes)} FG BUSS Codes ({', '.join(sorted(p.buss_codes))})")
    odd = sorted({e.item for e in p.odi if e.item and e.item != p.sku})
    if odd:
        issues.append(f"ODI rows for {p.sku} carry Item Number {', '.join(odd)}")
    if not issues:
        return []
    return [Finding(
        "R13", p.sku, p.sku, p.description, 0, p.unit_price,
        evidence={"issues": "; ".join(issues), "buss_codes": ", ".join(sorted(p.buss_codes)) or None,
                  "mips_buss_codes": ", ".join(sorted(wo_codes)) or None},
        recommendation=(
            f"{'; '.join(issues)}. Joins between systems rely on these codes: align them with the Oracle item "
            f"master (the system of record)."),
        systems=["Oracle", "BUSS", "MIPS", "ODI"])]


SKU_RULES = [r01_r02_r03_demand_capture, r04_mips_demand, r05_work_order_size, r06_missing_work_order,
             r07_odi_work_order, r08_uncovered_lines, r09_allocation, r10_wms_balance, r11_batches,
             r12_late_wrap, r13_item_codes]


def run_all(src: SourceData, positions: dict[str, SkuPosition]) -> list[Finding]:
    findings: list[Finding] = []
    for p in positions.values():
        for rule in SKU_RULES:
            findings.extend(rule(p))
    return findings
