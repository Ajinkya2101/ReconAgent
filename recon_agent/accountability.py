"""System-level accountability: which system misfired, and what each system did.

Every finding gets one accountability record, computed from the same values the
rule used, so the attribution is as deterministic and auditable as the quantity:

    impact         the business effect (over-production, under-production, ...)
    origin         the system (or the interface between two systems) where it started
    handoff        the interface that failed, when the fault sits between systems
    failure_type   how it misfired (planning logic, stale data, missing transaction, ...)
    failure_point  one sentence naming the step that went wrong
    basis          how sure the attribution is: "certain" (the data proves it),
                   "likely" (the usual cause; another is possible) or "undetermined"
    systems        one entry per system - MIPS, ODI and WMS always, BUSS and Oracle when
                   the rule involves them - with its role and what it shows

The AI layer explains this record in more detail; it never changes it.
"""
from __future__ import annotations

from .detectors import Finding
from .position import SkuPosition

SYSTEMS = ("MIPS", "ODI", "WMS")   # always listed; BUSS and Oracle are added when a rule involves them
OPTIONAL = ("BUSS", "Oracle")
ORIGINS = ("BUSS", "Oracle", "MIPS", "ODI", "WMS", "Integration", "Undetermined")
ROLES = {
    "Misfired": "the system where the error started",
    "Affected": "holds or acts on a wrong value because of it",
    "Reference": "holds the value the others should match",
    "Conflicting": "disagrees with another system; the data cannot say which is wrong",
    "To check": "this rule cannot see it, but it may carry the error",
    "Not involved": "plays no part in this error",
}
IMPACTS = ("Over-production", "Under-production", "Short shipment", "Overstated inventory",
           "Traceability gap", "Data integrity")
BASIS = ("certain", "likely", "undetermined")


def _n(x) -> str:
    try:
        return f"{float(x):,.0f}"
    except (TypeError, ValueError):
        return str(x)


def _same(a: float, b: float) -> bool:
    return abs(a - b) < 0.5


def _record(f: Finding, impact: str, origin: str, failure_type: str, failure_point: str, basis: str,
            roles: dict[str, tuple[str, str]], handoff: str | None = None) -> dict:
    names = list(SYSTEMS) + [s for s in OPTIONAL if s in f.systems or s in roles]
    systems = [{"system": s, "role": roles.get(s, ("Not involved", ""))[0],
                "detail": roles.get(s, ("Not involved", ""))[1]} for s in names]
    return {"impact": impact, "impact_units": round(abs(f.units)), "origin": origin, "handoff": handoff,
            "failure_type": failure_type, "failure_point": failure_point, "basis": basis, "systems": systems}


# --- 1. Capture demand ----------------------------------------------------------

def _r01(f, p, ev):
    src = ev["source_system"]
    return _record(
        f, "Under-production", "Integration", "Missing transaction",
        f"The {src} → ODI interface did not deliver {ev['demand_line']}.", "certain",
        {src: ("Reference", f"Holds {ev['demand_line']}: {_n(ev['source_qty'])} units."),
         "ODI": ("Affected", f"Has no mapping row for it; ODI maps {_n(ev['odi_demand'])} of "
                             f"{_n(ev['total_demand'])} demand."),
         "MIPS": ("Affected", f"Plans without these {_n(f.units)} units, so the work order is short by them.")},
        handoff=f"{src} → ODI")


def _r02(f, p, ev):
    src, more = ev["source_system"], f.units > 0
    if _same(ev["mips_demand"], ev["demand_left"]):
        mips = ("Reference", f"Already plans the current {_n(ev['demand_left'])} demand left.")
        impact = "Traceability gap"
    else:
        mips = ("Affected", f"Plans {_n(ev['mips_demand'])} from ODI's figure: {_n(abs(f.units))} "
                            f"{'short' if more else 'too many'}.")
        impact = "Under-production" if more else "Over-production"
    return _record(
        f, impact, "Integration", "Stale data",
        f"The {ev['demand_line']} changed in {src} and the change never reached ODI.", "certain",
        {src: ("Reference", f"Holds {_n(ev['source_qty'])} units for {ev['demand_line']}."),
         "ODI": ("Affected", f"Still maps {_n(ev['odi_qty'])} on row {ev['odi_entry']}."), "MIPS": mips},
        handoff=f"{src} → ODI")


def _r03(f, p, ev):
    src = ev["source_system"]
    return _record(
        f, "Over-production", "Integration", "Missing delete",
        f"The {ev['demand_line']} was removed in {src}, but the ODI mapping kept it.", "certain",
        {src: ("Reference", f"No longer has {ev['demand_line']}."),
         "ODI": ("Affected", f"Still maps it on row {ev['odi_entry']}: {_n(ev['odi_qty'])} units."),
         "MIPS": ("To check", f"Plans these {_n(ev['odi_qty'])} units if it follows ODI.")}, handoff=f"{src} → ODI")


# --- 3-5. MIPS demand and the work order ------------------------------------------

def _r04(f, p: SkuPosition | None, ev):
    mips, left = ev["mips_demand"], ev["demand_left"]
    impact = "Under-production" if f.units > 0 else "Over-production"
    roles = {"BUSS": ("Reference", f"Orders total {_n(ev['sales_orders'])}."),
             "Oracle": ("Reference", f"Forecast is {_n(ev['forecast'])}."),
             "WMS": ("Reference", f"Has {_n(ev['wms_allocated'])} allocated to orders.")}
    if p and _same(mips, p.odi_demand_left) and not _same(p.odi_demand, p.demand):
        # MIPS did its arithmetic right on ODI's copy of the demand: the copy is what is wrong.
        return _record(
            f, impact, "Integration", "Stale data",
            "MIPS planned from ODI's copy of the demand, which no longer matches BUSS and Oracle.", "certain",
            {**roles, "ODI": ("Affected", f"Maps {_n(p.odi_demand)} of demand against {_n(ev['total_demand'])} in "
                                          f"BUSS and Oracle."),
             "MIPS": ("Affected", f"Plans {_n(mips)}, ODI's demand less allocated stock; it should be {_n(left)}.")},
            handoff="BUSS/Oracle → ODI")
    if p and not _same(p.odi_allocated, p.allocated) and _same(mips, max(0.0, p.demand - p.odi_allocated)):
        return _record(
            f, impact, "Integration", "Stale data",
            "WMS changed its allocation and MIPS still plans from the allocation ODI carries.", "certain",
            {**roles, "WMS": ("Reference", f"Has {_n(p.allocated)} allocated."),
             "ODI": ("Affected", f"Shows {_n(p.odi_allocated)} allocated."),
             "MIPS": ("Affected", f"Plans {_n(mips)} from the old allocation; it should be {_n(left)}.")},
            handoff="WMS → ODI")
    if _same(mips, ev["total_demand"]):
        return _record(
            f, impact, "MIPS", "Planning logic", "MIPS did not remove the stock WMS has already allocated.",
            "certain",
            {**roles, "MIPS": ("Misfired", f"Plans the full {_n(mips)} demand; less the {_n(ev['wms_allocated'])} "
                                           f"allocated it is {_n(left)}."),
             "ODI": ("Reference", f"Maps {_n(ev['odi_demand'])} of demand.")})
    return _record(
        f, impact, "MIPS", "Stale data",
        "MIPS demand does not match the current BUSS orders, Oracle forecast and WMS allocation: it was not "
        "refreshed, or it was changed by hand.", "likely",
        {**roles, "MIPS": ("Misfired", f"Plans {_n(mips)}; the sources leave {_n(left)}."),
         "ODI": ("Reference", f"Maps {_n(ev['odi_demand'])} of demand.")})


def _r05(f, p: SkuPosition | None, ev):
    wo, shortfall, left, avail = ev["work_order_qty"], ev["shortfall"], ev["demand_left"], ev["wms_available"]
    impact = "Over-production" if f.units < 0 else "Under-production"
    wms = ("Reference", f"Has {_n(avail)} available and {_n(ev['wms_allocated'])} allocated.")
    odi = ("Affected", f"Mixes batches for work order {ev['work_orders']} at the MIPS quantity.")
    if not _same(ev["mips_demand"], left):
        if p and _same(ev["mips_demand"], p.odi_demand_left) and not _same(p.odi_demand, p.demand):
            return _record(
                f, impact, "Integration", "Stale data",
                "The work order was sized from ODI's copy of the demand, which no longer matches BUSS and Oracle.",
                "certain",
                {"MIPS": ("Affected", f"Plans {_n(wo)} on the work order; the shortfall is {_n(shortfall)}."),
                 "WMS": wms, "ODI": ("Affected", f"Maps {_n(p.odi_demand)} of demand against {_n(ev['total_demand'])}.")},
                handoff="BUSS/Oracle → ODI")
        return _record(
            f, impact, "MIPS", "Planning logic",
            "The work order follows a MIPS demand figure that is itself wrong (see the R04 flag).", "likely",
            {"MIPS": ("Misfired", f"Plans {_n(wo)} on the work order; the shortfall is {_n(shortfall)}."),
             "WMS": wms, "ODI": odi})
    if avail and _same(wo, left):
        point, basis = ("MIPS sized the work order to the demand left without subtracting the stock available "
                        "in WMS."), "certain"
    elif shortfall == 0:
        point, basis = "MIPS raised a work order although WMS stock covers the demand left.", "certain"
    else:
        point, basis = "The MIPS work order was set to a quantity other than the shortfall.", "likely"
    return _record(
        f, impact, "MIPS", "Planning logic", point, basis,
        {"MIPS": ("Misfired", f"Plans {_n(wo)} on work order {ev['work_orders']}; {_n(left)} demand left less "
                              f"{_n(avail)} available is {_n(shortfall)}."),
         "WMS": wms, "ODI": odi})


def _r06(f, p, ev):
    return _record(
        f, "Under-production", "MIPS", "Missing record", "MIPS has no work order for a shortfall.", "certain",
        {"MIPS": ("Misfired", f"No work order, but the SKU is {_n(ev['shortfall'])} short."),
         "WMS": ("Reference", f"Has {_n(ev['wms_available'])} available and {_n(ev['wms_allocated'])} allocated."),
         "ODI": ("To check", f"Maps the demand to {ev['odi_work_orders']}." if ev.get("odi_work_orders") else
                             "Maps the demand to no work order.")})


# --- 5. Work order to ODI, every line covered -------------------------------------

def _r07(f, p, ev):
    if "odi_work_order" in ev:
        return _record(
            f, "Traceability gap", "Integration", "Missing transaction",
            f"ODI links demand to work order {ev['odi_work_order']}, which MIPS does not have.", "likely",
            {"MIPS": ("Reference", f"Has work orders: {ev['mips_work_orders'] or 'none'}."),
             "ODI": ("Affected", f"Maps {_n(ev['odi_rows'])} rows to {ev['odi_work_order']}.")},
            handoff="MIPS → ODI")
    return _record(
        f, "Under-production" if f.units > 0 else "Over-production", "Integration", "Stale data",
        f"MIPS changed work order {ev['work_order']} and the new quantity never reached ODI.", "certain",
        {"MIPS": ("Reference", f"Work order quantity {_n(ev['mips_work_order_qty'])}."),
         "ODI": ("Affected", f"{_n(ev['odi_rows_stale'])} rows still carry {_n(ev['odi_work_order_qty'])}.")},
        handoff="MIPS → ODI")


def _r08(f, p, ev):
    return _record(
        f, "Short shipment", "ODI", "Missing link",
        f"ODI mapped the {ev['demand_line']} to no work order, and it has no allocated stock.", "likely",
        {"ODI": ("Misfired", f"Row {ev['odi_entry']}: {_n(ev['odi_qty'])} units, {_n(ev['allocated'])} allocated, "
                             f"no work order."),
         "MIPS": ("To check", f"Work orders: {ev['mips_work_orders'] or 'none'}."),
         "WMS": ("To check", "Stock could be allocated to the order instead.")})


def _r09(f, p, ev):
    return _record(
        f, "Traceability gap", "Integration", "Stale data",
        "The allocation in ODI does not match what WMS has reserved for orders.", "likely",
        {"WMS": ("Reference", f"Has {_n(ev['wms_allocated'])} allocated (WMS holds all on-hand stock)."),
         "ODI": ("Affected", f"Shows {_n(ev['odi_allocated'])} allocated."),
         "MIPS": ("To check", "Its demand left is sized from the allocated figure.")}, handoff="WMS → ODI")


# --- 4, 7, 8. Stock, batches, wrap time, master data ---------------------------------

def _r10(f, p, ev):
    return _record(
        # Less on hand than available + allocated: WMS offers stock that is not there. More: units no
        # order or availability accounts for (on hold, damaged, not booked).
        f, "Overstated inventory" if f.units < 0 else "Traceability gap", "WMS", "Inventory balance",
        "WMS on hand does not equal available plus allocated: a count or a transaction is wrong.", "undetermined",
        {"WMS": ("Misfired", f"{ev['inventory']}: on hand {_n(ev['on_hand'])}, available {_n(ev['available'])}, "
                             f"allocated {_n(ev['allocated'])}."),
         "MIPS": ("To check", "The shortfall is sized from the available figure.")})


def _r11(f, p, ev):
    diff = ev["mixed"] - ev["scheduled"]
    return _record(
        f, "Over-production" if diff > 0 else "Under-production", "ODI", "Production execution",
        "The plant mixed a different number of batches than MIPS scheduled, with no recorded reason.", "certain",
        {"ODI": ("Misfired", f"Mixed {_n(ev['mixed'])} batches against {_n(ev['scheduled'])} scheduled on "
                             f"{ev['line']}."),
         "MIPS": ("Reference", f"Scheduled {_n(ev['scheduled'])} batches for {ev['work_order']}."),
         **({"WMS": ("To check", "Extra output will arrive that no order is waiting for.")} if diff > 0 else {})})


def _r12(f, p, ev):
    return _record(
        f, "Short shipment", "ODI", "Production execution",
        f"Wrapping for work order {ev['work_order']} ended after the requested ship date.", "likely",
        {"ODI": ("Misfired", f"Wrapping ended {ev['wrapping_end']}; earliest ship date {ev['earliest_ship_date']}."),
         "MIPS": ("To check", "Check whether the work order was scheduled too late."),
         "WMS": ("Affected", f"Cannot ship {_n(f.units)} units of {_n(ev['late_orders'])} orders on time.")})


def _r13(f, p, ev):
    return _record(
        f, "Data integrity", "Undetermined", "Master data",
        "Systems use different codes for the same item; the Oracle item master decides which is right.",
        "undetermined",
        {"Oracle": ("Reference", "The item master is the system of record."),
         "BUSS": ("Conflicting", f"FG BUSS Code {ev['buss_codes'] or 'none'}."),
         "MIPS": ("Conflicting", f"FG BUSS Code {ev['mips_buss_codes'] or 'none'}."),
         "ODI": ("To check", ev["issues"])})


BY_RULE = {"R01": _r01, "R02": _r02, "R03": _r03, "R04": _r04, "R05": _r05, "R06": _r06, "R07": _r07,
           "R08": _r08, "R09": _r09, "R10": _r10, "R11": _r11, "R12": _r12, "R13": _r13}


def attribute(f: Finding, p: SkuPosition | None = None) -> dict:
    """The accountability record for one finding (see the module docstring)."""
    try:
        return BY_RULE[f.rule](f, p, f.evidence)
    except (KeyError, TypeError, ValueError):
        # A rule without a mapping, or evidence missing a field: say so rather than guess.
        return _record(f, "Data integrity", "Undetermined", "Not mapped",
                       "No accountability mapping for this rule's evidence; see the evidence below.",
                       "undetermined", {s: ("To check", "") for s in f.systems if s in SYSTEMS + OPTIONAL})


def assign(findings: list[Finding], positions: dict[str, SkuPosition]) -> None:
    for f in findings:
        f.accountability = attribute(f, positions.get(f.sku))


def origin_label(f: Finding) -> str:
    """Where a finding started, naming the interface when it is one ("Integration (MIPS → ODI)")."""
    a = f.accountability or {}
    origin = a.get("origin", "Undetermined")
    return f"Integration ({a['handoff']})" if origin == "Integration" and a.get("handoff") else origin


def rollup(findings: list[Finding]) -> dict:
    """Totals by origin system and by impact, largest $ exposure first."""
    def add(table: dict, key: str, f: Finding) -> None:
        r = table.setdefault(key, {"flags": 0, "high": 0, "units": 0, "dollars": 0})
        r["flags"] += 1
        r["high"] += f.severity == "High"
        r["units"] += round(abs(f.units))
        r["dollars"] += round(f.dollars)

    by_origin: dict[str, dict] = {}
    by_impact: dict[str, dict] = {}
    for f in findings:
        add(by_origin, origin_label(f), f)
        add(by_impact, (f.accountability or {}).get("impact", "Data integrity"), f)

    def ordered(t: dict) -> dict:
        return dict(sorted(t.items(), key=lambda kv: (-kv[1]["dollars"], -kv[1]["flags"])))
    return {"by_origin": ordered(by_origin), "by_impact": ordered(by_impact)}
