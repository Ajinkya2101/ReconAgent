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
    systems        one entry per system - MIPS, ODI and WMS always, Oracle when the
                   rule involves it - with its role and what it shows

The AI layer explains this record in more detail; it never changes it.
"""
from __future__ import annotations

import datetime as dt

from .detectors import Finding
from .position import SkuPosition

SYSTEMS = ("MIPS", "ODI", "WMS")   # always listed; "Oracle" is added when a rule involves it
ORIGINS = ("MIPS", "ODI", "WMS", "Oracle", "Integration", "Undetermined")
ROLES = {
    "Misfired": "the system where the error started",
    "Affected": "holds or acts on a wrong value because of it",
    "Reference": "holds the value the others should match",
    "Conflicting": "disagrees with another system; the data cannot say which is wrong",
    "To check": "this rule cannot see it, but it may carry the error",
    "Not involved": "plays no part in this error",
}
IMPACTS = ("Over-production", "Under-production", "Stranded surplus", "Short shipment",
           "Overstated inventory", "Traceability gap", "Financial posting gap", "Data integrity")
BASIS = ("certain", "likely", "undetermined")


def _n(x) -> str:
    try:
        return f"{float(x):,.0f}"
    except (TypeError, ValueError):
        return str(x)


def _record(f: Finding, impact: str, origin: str, failure_type: str, failure_point: str, basis: str,
            roles: dict[str, tuple[str, str]], handoff: str | None = None) -> dict:
    names = list(SYSTEMS) + (["Oracle"] if "Oracle" in f.systems or "Oracle" in roles else [])
    systems = [{"system": s, "role": roles.get(s, ("Not involved", ""))[0],
                "detail": roles.get(s, ("Not involved", ""))[1]} for s in names]
    return {"impact": impact, "impact_units": round(abs(f.units)), "origin": origin, "handoff": handoff,
            "failure_type": failure_type, "failure_point": failure_point, "basis": basis, "systems": systems}


def _r01(f, p, ev):
    return _record(
        f, "Over-production", "MIPS", "Planning logic",
        "MIPS inventory netting applied carried-in inventory to the first order line instead of the "
        "SKU's total demand.", "certain",
        {"MIPS": ("Misfired", f"Netted {_n(ev['inventory'])} units of inventory against line 1 "
                              f"({_n(ev['first_line_qty'])}) only, so it asks for {_n(ev['first_line_only_requirement'])} "
                              f"instead of {_n(ev['consolidated_requirement'])} across all lines."),
         "ODI": ("Affected", f"Production is sized from the MIPS requirement: up to {_n(f.units)} units more "
                             f"than the SKU needs.")})


def _r02(f, p, ev):
    return _record(
        f, "Traceability gap", "MIPS", "Missing record",
        "MIPS netted inventory out of demand without recording it as beginning inventory.", "certain",
        {"MIPS": ("Misfired", f"Sent {_n(ev['odi_net_ordered_first_run'])} to production against "
                              f"{_n(ev['mips_demand'])} demand, netting {_n(f.units)} as inventory, but its "
                              f"beginning inventory is {_n(ev['mips_beginning_inventory_recorded'])}."),
         "ODI": ("Affected", f"Ordered {_n(ev['odi_net_ordered_first_run'])}; nothing in ODI shows where the "
                             f"other {_n(f.units)} units came from."),
         "WMS": ("To check", "No ticket or freezer lot is tied to the netted units; one would prove they exist.")})


def _r03(f, p, ev):
    return _record(
        f, "Traceability gap", "MIPS", "Scheduling",
        "MIPS scheduled a production line with zero demand (a blind line).", "certain",
        {"MIPS": ("Misfired", f"Scheduled {f.description} in run {ev['run']} with zero demand."),
         "ODI": ("Affected", "Carries the run with zero ordered and zero wrapped; inventory applied to it "
                             "never reduces real demand.")})


def _r04(f, p, ev):
    return _record(
        f, "Traceability gap", "MIPS", "Scheduling",
        "A run was moved to another line and the original schedule entry was not removed.", "certain",
        {"MIPS": ("Misfired", f"Moved the run ({ev['actual_run']}) but left the original entry "
                              f"({ev['ghost_run']}) on the schedule."),
         "ODI": ("Affected", f"Still holds a zero-quantity copy: {ev['ghost_run']}."),
         "WMS": ("To check", "Work-order allocations may still point at the ghost run.")})


def _r05(f, p: SkuPosition | None, ev):
    remaining = ev["need_after_first_run"]
    if p and p.active_runs:
        first = p.active_runs[0]
        recovery_ordered = p.net_ordered - first.net_units_ordered
        sized_wrong = recovery_ordered - remaining >= p.wrapped - p.consolidated_requirement
    else:
        recovery_ordered, sized_wrong = None, True
    if sized_wrong:
        return _record(
            f, "Over-production", "MIPS", "Planning logic",
            "The recovery run was sized to the order again instead of the shortfall left by run 1 "
            "(demand counted twice).", "certain",
            {"MIPS": ("Misfired", f"Ordered {_n(recovery_ordered) if recovery_ordered is not None else 'more'} on the "
                                  f"recovery run when {_n(remaining)} were still needed."),
             "ODI": ("Affected", f"Ordered {_n(ev['net_ordered_all_runs'])} and wrapped {_n(ev['wrapped_all_runs'])} "
                                 f"across runs against {_n(ev['demand'])} demand."),
             "WMS": ("To check", f"Will receive the {_n(f.units)} surplus units; they need a next-day or freezer "
                                 f"destination.")}, handoff="MIPS → ODI")
    return _record(
        f, "Over-production", "ODI", "Production execution",
        "The runs wrapped more than they were ordered to make.", "certain",
        {"ODI": ("Misfired", f"Wrapped {_n(ev['wrapped_all_runs'])} across runs against "
                             f"{_n(ev['net_ordered_all_runs'])} ordered."),
         "MIPS": ("Reference", f"Demand is {_n(ev['demand'])}; {_n(remaining)} were still needed after run 1."),
         "WMS": ("To check", f"Will receive the {_n(f.units)} surplus units.")})


def _r06(f, p, ev):
    return _record(
        f, "Stranded surplus", "MIPS", "Carry-forward",
        "MIPS did not carry the surplus forward as the next sales day's beginning inventory.", "certain",
        {"MIPS": ("Misfired", f"Has not carried the {_n(f.units)}-unit surplus to the next sales day; next "
                              f"day's requirement will not be reduced by it."),
         "ODI": ("Reference", f"Wrapped {_n(ev['wrapped'])} and reports {_n(ev['odi_expected_ending_inventory'])} "
                              f"expected ending inventory."),
         "WMS": ("To check", f"Holds the {_n(f.units)} surplus units physically; confirm them before they are "
                             f"carried or frozen.")})


def _r07(f, p, ev):
    supply = p.usable_supply if p else None
    return _record(
        f, "Overstated inventory", "WMS", "Missing transaction",
        "WMS shipped stock (usually freezer or prior-day tickets) without a transaction MIPS can see.",
        "likely",
        {"WMS": ("Misfired", f"Allocated the full {_n(ev['wms_allocated'])} units, {_n(f.units)} more than "
                             f"production and applied inventory cover"
                             + (f" ({_n(supply)} of {_n(ev['demand'])})." if supply is not None else ".")),
         "MIPS": ("Affected", f"Does not see the {_n(f.units)} units drawn, so its freezer and inventory "
                              f"balances stay overstated."),
         "ODI": ("Reference", f"Wrapped {_n(ev['wrapped'])}"
                              + (f", of which {_n(ev['returned_to_plant'])} went back to the plant." if
                                 ev.get("returned_to_plant") else "."))})


def _r08(f, p, ev):
    return _record(
        f, "Stranded surplus", "MIPS", "Scheduling",
        "MIPS kept a run on the schedule although inventory already exceeds demand.", "certain",
        {"MIPS": ("Misfired", f"Kept a run scheduled although inventory exceeds the {_n(ev['demand'])} demand "
                              f"by {_n(f.units)}."),
         "ODI": ("Affected", f"Shows a negative net order ({_n(-abs(f.units))}): the run is not needed."),
         "WMS": ("To check", f"Confirm the {_n(f.units)} surplus units exist before they are carried forward.")})


def _when(value) -> str:
    try:
        return f"{dt.datetime.fromisoformat(str(value)):%m/%d %H:%M}"
    except ValueError:
        return str(value)


def _r09(f, p, ev):
    mips, wms, gap = ev["mips_demand_sales_date"], ev["wms_order_qty"], abs(f.units)
    more = f.units < 0   # MIPS plans more than WMS has on order
    impact = "Over-production" if more else "Under-production"
    when = f" (last modified {_when(ev['mips_last_modified'])})" if ev.get("mips_last_modified") else ""
    # Demand MIPS changed after production was ordered, in the direction of the gap, means MIPS itself
    # moved (a planning change), not that WMS moved while MIPS was not refreshed.
    changed = p.demand_after_order if p else 0
    if changed and (changed > 0) == more:
        odi = ("To check", f"Production was ordered against {_n(p.demand_at_order)} demand, before MIPS changed by "
                           f"{changed:+,.0f}; check that no run is resized to MIPS's {_n(mips)}.")
        return _record(
            f, impact, "MIPS", "Planning change",
            f"MIPS demand {'rose' if changed > 0 else 'fell'} by {_n(abs(changed))} after production was ordered"
            f"{when}, with no matching change to the customer orders in WMS.", "likely",
            {"MIPS": ("Misfired", f"Plans {_n(mips)}, {_n(gap)} {'more' if more else 'less'} than the {_n(wms)} "
                                  f"on order in WMS."),
             "WMS": ("Reference", f"Holds the current customer orders: {_n(wms)}."), "ODI": odi})
    if more:
        cause = f"MIPS plans more than the customers have on order in WMS; it was not refreshed{when}."
        odi_detail = f"Production is sized to the MIPS figure: {_n(gap)} more than WMS has on order."
    else:
        cause = f"The customer order changed in WMS after MIPS last synced{when}."
        odi_detail = f"Production is sized to the MIPS figure: {_n(gap)} short of the WMS order."
    return _record(
        f, impact, "MIPS", "Stale data", cause, "likely",
        {"MIPS": ("Misfired", f"Still plans {_n(mips)}; WMS has {_n(wms)} on order."),
         "WMS": ("Reference", f"Holds the current customer orders: {_n(wms)}."),
         "ODI": ("Affected", odi_detail)}, handoff="WMS → MIPS")


def _r10(f, p, ev):
    order, alloc, wrapped = ev["wms_order_qty"], ev["wms_allocated"], ev["wrapped"]
    available = wrapped + (p.implied_inventory if p else 0)
    if available >= order or not p:
        return _record(
            f, "Short shipment", "WMS", "Allocation",
            "WMS allocated less than it has on hand: a returned or damaged ticket not netted, or a stale "
            "workstation.", "likely",
            {"WMS": ("Misfired", f"Allocated {_n(alloc)} of {_n(order)} for {ev['short_customers']}."),
             "ODI": ("Reference", f"Wrapped {_n(wrapped)}, enough for the order.")})
    if p.demand_current < order:
        return _record(
            f, "Short shipment", "MIPS", "Stale data",
            "Production was planned for less than WMS has on order, so there is not enough to allocate.",
            "likely",
            {"MIPS": ("Misfired", f"Planned {_n(p.demand_current)} against {_n(order)} on order in WMS."),
             "ODI": ("Affected", f"Wrapped {_n(wrapped)}, sized to the MIPS plan."),
             "WMS": ("Reference", f"Allocated {_n(alloc)} of {_n(order)}; {_n(f.units)} short for "
                                  f"{ev['short_customers']}.")}, handoff="WMS → MIPS")
    return _record(
        f, "Short shipment", "ODI", "Production execution",
        "The plant wrapped less than MIPS planned, so WMS cannot allocate the full order.", "likely",
        {"ODI": ("Misfired", f"Wrapped {_n(wrapped)}, short of the {_n(p.consolidated_requirement)} requirement."),
         "MIPS": ("Reference", f"Planned {_n(p.demand_current)}."),
         "WMS": ("Affected", f"Allocated {_n(alloc)} of {_n(order)}; {_n(f.units)} short for "
                             f"{ev['short_customers']}.")})


def _r11(f, p, ev):
    return _record(
        f, "Data integrity", "Undetermined", "Master data",
        "MIPS and WMS carry different units per container for the item; the Oracle item master decides "
        "which is right.", "undetermined",
        {"MIPS": ("Conflicting", f"{_n(ev['mips_units_per_container'])} units per container."),
         "WMS": ("Conflicting", f"{_n(ev['wms_units_per_container'])} units per container.")})


def _r12(f, p, ev):
    return _record(
        f, "Traceability gap", "WMS", "Missed scan",
        "Orders were marked shipped without a trailer load scan.", "certain",
        {"WMS": ("Misfired", f"{_n(ev['orders'])} of {_n(ev['of_total_orders'])} orders "
                             f"({_n(ev['shipped_units'])} units) show Shipped with Loaded = 0."),
         "Oracle": ("To check", "Confirm the ship transactions posted within the 10-minute window.")})


def _r13(f, p, ev):
    if "fill_rate_total" in ev:
        detail = (f"Fill Rate report totals {_n(ev['fill_rate_total'])}; Order Summary by Item totals "
                  f"{_n(ev['item_summary_total'])}.")
    else:
        detail = f"Printed subtotal {_n(ev['printed_total'])}; the customer lines add up to {_n(ev['sum_of_lines'])}."
    return _record(
        f, "Data integrity", "WMS", "Report extract",
        "Two WMS views of the same orders do not add up: a line is missing from one extract or changed "
        "between report runs.", "certain", {"WMS": ("Misfired", detail)})


def _r14(f, p, ev):
    diff = ev["mixed"] - ev["scheduled"]
    return _record(
        f, "Over-production" if diff > 0 else "Under-production", "ODI", "Production execution",
        "The plant mixed a different number of batches than MIPS scheduled, with no recorded reason.",
        "certain",
        {"ODI": ("Misfired", f"Mixed {_n(ev['mixed'])} batches against {_n(ev['scheduled'])} scheduled on "
                             f"{ev['line']}."),
         "MIPS": ("Reference", f"Scheduled {_n(ev['scheduled'])} batches."),
         **({"WMS": ("To check", f"About {_n(ev['approx_extra_pieces'])} extra pieces will arrive unplanned.")}
            if diff > 0 else {})})


def _r15(f, p, ev):
    return _record(
        f, "Data integrity", "MIPS", "Interface formatting",
        "The BUSS interface pads the FG Oracle Ref, so exact-match joins between systems fail.", "certain",
        {"MIPS": ("Misfired", f"{_n(ev['lines_with_padding'])} of {_n(ev['of_lines'])} lines carry a padded "
                              f"FG Oracle Ref."),
         "ODI": ("Affected", "Exact-match joins to MIPS keys fail; the agent normalises keys meanwhile."),
         "WMS": ("Affected", "Exact-match joins to MIPS keys fail; the agent normalises keys meanwhile.")},
        handoff="BUSS → MIPS")


def _r16(f, p, ev):
    return _record(
        f, "Over-production" if f.units > 0 else "Under-production", "Integration", "Interface lag",
        "The MIPS → ODI interface did not deliver the current sales-order quantity.", "certain",
        {"MIPS": ("Reference", f"Sales orders total {_n(ev['mips_sales_order_qty'])}."),
         "ODI": ("Affected", f"Holds {_n(ev['odi_mips_order_qty'])} as the MIPS order quantity.")},
        handoff="MIPS → ODI")


def _r17(f, p, ev):
    if ev.get("metric") == "wrap overrun %":
        return _record(
            f, "Over-production" if f.units > 0 else "Under-production", "ODI", "Data entry or over-run",
            "A run's wrapped count is far from what it was ordered to make: a keying error or a real "
            "over-run.", "undetermined",
            {"ODI": ("Misfired", f"Run {ev['run']} wrapped {_n(ev['wrapped'])} against {_n(ev['ordered'])} "
                                 f"ordered ({ev['value']}; plant median {ev['plant_median']})."),
             "WMS": ("To check", "The received ticket quantity shows which count is real.")})
    return _record(
        f, "Data integrity", "ODI", "Data entry",
        "A batch's final piece variance is far from the plant median: check scaling and scrap entries.",
        "undetermined",
        {"ODI": ("Misfired", f"{ev['formula']} on {ev['line']}: final piece variance {_n(ev['value'])} "
                             f"(plant median {_n(ev['plant_median'])}).")})


def _r18(f, p, ev):
    waited = f"{_n(ev['minutes_waiting'])} minutes"
    if ev.get("oracle_status") == "not received":
        return _record(
            f, "Financial posting gap", "Integration", "Missing transaction",
            "The WMS → Oracle interface never delivered the transaction.", "certain",
            {"WMS": ("Reference", f"Recorded {ev['wms_transaction']} at {ev['wms_time']}."),
             "Oracle": ("Affected", f"Has no transaction for it after {waited}.")}, handoff="WMS → Oracle")
    why = ev["oracle_status"] + (f": {ev['oracle_error']}" if ev.get("oracle_error") else "")
    return _record(
        f, "Financial posting gap", "Oracle", "Posting rejected",
        "Oracle received the WMS transaction and rejected it.", "certain",
        {"WMS": ("Reference", f"Recorded {ev['wms_transaction']} at {ev['wms_time']}."),
         "Oracle": ("Misfired", f"Rejected it ({why}); unposted after {waited}.")}, handoff="WMS → Oracle")


def _r19(f, p, ev):
    return _record(
        f, "Overstated inventory", "WMS", "Missing update",
        "WMS booked a return to the plant but did not reduce the ticket's received quantity.", "certain",
        {"WMS": ("Misfired", f"Booked {_n(ev['returned_by_transactions'])} returned on ticket {ev['ticket']}, "
                             f"but the ticket still shows {_n(ev['net_received_on_ticket'])} net received, "
                             f"not {_n(ev['net_received_should_be'])}."),
         "MIPS": ("Affected", f"The {_n(f.units)} returned units are missing from the production requirement "
                              f"until the ticket is corrected.")})


BY_RULE = {"R01": _r01, "R02": _r02, "R03": _r03, "R04": _r04, "R05": _r05, "R06": _r06, "R07": _r07,
           "R08": _r08, "R09": _r09, "R10": _r10, "R11": _r11, "R12": _r12, "R13": _r13, "R14": _r14,
           "R15": _r15, "R16": _r16, "R17": _r17, "R18": _r18, "R19": _r19}


def attribute(f: Finding, p: SkuPosition | None = None) -> dict:
    """The accountability record for one finding (see the module docstring)."""
    try:
        return BY_RULE[f.rule](f, p, f.evidence)
    except (KeyError, TypeError, ValueError):
        # A rule without a mapping, or evidence missing a field: say so rather than guess.
        return _record(f, "Data integrity", "Undetermined", "Not mapped",
                       "No accountability mapping for this rule's evidence; see the evidence below.",
                       "undetermined", {s: ("To check", "") for s in f.systems if s in SYSTEMS})


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
