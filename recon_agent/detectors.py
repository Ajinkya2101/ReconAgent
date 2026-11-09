"""Mismatch detectors.

Each rule maps to a scenario in the WMS_AI_Agent_Requirement brief and to a
challenge area on the "Challenges" slide, produces evidence from the source
systems, and proposes a quantified correction. The rules are deterministic so
every finding is auditable; the AI layer explains and prioritises them.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass, field

from . import config
from .ingest import SourceData
from .position import SkuPosition

# code: (title, requirement-doc scenario, challenge area, base severity)
RULES = {
    "R01": ("Inventory applied to first order line only", "1 - Multiple order lines for same SKU", "Inventory Allocation", "High"),
    "R02": ("Inventory netted without audit trail", "2 - Inventory on zero-qty / blind line", "Inventory Synchronization", "Medium"),
    "R03": ("Zero-quantity (blind) production line", "2 - Inventory on zero-qty / blind line", "Order-to-Batch Traceability", "Low"),
    "R04": ("Production run moved; ghost run left behind", "3 - Inventory does not follow moved run", "Inventory Synchronization", "Medium"),
    "R05": ("Recovery run over-built (double-counted demand)", "4 - Additional / recovery production runs", "Inventory Allocation", "High"),
    "R06": ("Excess production not carried to next demand", "1/4 - Excess not applied to subsequent demand", "Inventory Allocation", "Medium"),
    "R07": ("WMS filled demand from untracked inventory", "7 - Freezer inventory remains after use", "Freezer Inventory Management", "High"),
    "R08": ("Negative net order: inventory exceeds demand", "2 - Inventory on zero-qty / blind line", "Inventory Synchronization", "Medium"),
    "R09": ("MIPS vs WMS order quantity mismatch", "6 - Screen-refresh / stale data", "Production Visibility", "Medium"),
    "R10": ("WMS allocation short of order", "5 - Net quantity not reflected", "Warehouse Traceability", "Medium"),
    "R11": ("Units-per-container master data conflict", "Data harmonization", "Data Flow Transparency", "Medium"),
    "R12": ("Shipped without trailer load scan", "8 - Missing downstream transactions", "Warehouse Traceability", "High"),
    "R13": ("Cross-report total mismatch", "8 - Missing downstream transactions", "Integration Architecture", "Medium"),
    "R14": ("Batches mixed differ from MIPS schedule", "Order-to-batch traceability", "Order-to-Batch Traceability", "Low"),
    "R15": ("SKU key formatting differs by source system", "Data harmonization", "Data Flow Transparency", "Low"),
    "R16": ("ODI MIPS order qty disagrees with MIPS sales orders", "8 - Missing downstream transactions", "Integration Architecture", "Medium"),
    "R17": ("Statistical outlier - possible data-entry error", "Unusually large quantity", "Production Visibility", "Medium"),
    "R18": ("WMS transaction not posted in Oracle within 10 minutes", "8 - Missing downstream transactions", "Integration Architecture", "High"),
    "R19": ("Return to plant did not reduce received quantity", "5 - Net quantity not reflected", "Warehouse Traceability", "High"),
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


def _run_label(r) -> str:
    start = r.start.strftime("%m/%d %H:%M") if r.start else "n/a"
    return f"{r.formula_type} {r.line}/{r.shift or 'no shift'} @ {start}"


# --------------------------------------------------------------------------
# SKU-level rules
# --------------------------------------------------------------------------

def r01_first_line_allocation(p: SkuPosition) -> list[Finding]:
    if len(p.lines) < 2 or p.first_line_overproduction_risk <= 0:
        return []
    parts = " + ".join(f"{_fmt(l.order_qty)} ({'SO' if l.is_sales_order else 'SP'}"
                       f"{', moved' if l.order_moved else ''})" for l in p.lines)
    return [Finding(
        "R01", p.sku, p.sku, p.description, p.first_line_overproduction_risk, p.unit_price,
        evidence={"lines": parts, "total_demand": p.demand_total, "inventory": p.implied_inventory,
                  "first_line_qty": p.lines[0].order_qty,
                  "first_line_only_requirement": p.first_line_only_requirement,
                  "consolidated_requirement": p.consolidated_requirement},
        recommendation=(
            f"Consolidate the {len(p.lines)} MIPS lines ({parts} = {_fmt(p.demand_total)}) before applying "
            f"the {_fmt(p.implied_inventory)} units of available inventory. SKU-level requirement is "
            f"{_fmt(p.consolidated_requirement)}, not {_fmt(p.first_line_only_requirement)} - "
            f"applying inventory to the first line alone risks over-producing "
            f"{_fmt(p.first_line_overproduction_risk)} units."),
        severity="High" if p.first_line_overproduction_risk >= 1000 else "Medium",
        systems=["MIPS", "ODI"])]


def r02_untraceable_inventory(p: SkuPosition) -> list[Finding]:
    if (p.implied_inventory <= 0 or p.recorded_inventory != 0 or p.first_line_overproduction_risk > 0
            or any(r.net_units_ordered < 0 for r in p.runs)):
        return []
    sent = p.active_runs[0].net_units_ordered
    later = p.demand_after_order
    return [Finding(
        "R02", p.sku, p.sku, p.description, p.implied_inventory, p.unit_price,
        evidence={"mips_demand": p.demand_at_order, "odi_net_ordered_first_run": sent,
                  "mips_beginning_inventory_recorded": p.recorded_inventory,
                  **({"demand_changed_after_order": later} if later else {})},
        recommendation=(
            f"MIPS sent {_fmt(sent)} to production against {_fmt(p.demand_at_order)} demand"
            + (f" (demand has since changed by {later:+,.0f}, which is new demand, not inventory)" if later else "")
            + ", so "
            f"{_fmt(p.implied_inventory)} units of inventory were applied - but MIPSBeginningInventoryQty "
            f"is 0. Identify the schedule line holding that inventory (likely a zero-qty or planning line) "
            f"and record it as beginning inventory so WMS tickets and Oracle can trace it."),
        systems=["MIPS", "ODI"])]


def r05_recovery_run(p: SkuPosition) -> list[Finding]:
    runs = p.active_runs
    if len(runs) < 2:
        return []
    first, *rest = runs
    # A recovery run should make only what run 1 left short of the requirement. It over-builds by
    # what it ordered beyond that (even before it wraps) or by what was wrapped beyond the
    # requirement (when it over-ran its own order), whichever is larger.
    remaining = max(0.0, p.consolidated_requirement - first.wrapped_units)
    recovery_ordered = sum(r.net_units_ordered for r in rest)
    overbuilt = p.wrapped - p.consolidated_requirement
    overbuild = max(recovery_ordered - remaining, overbuilt, 0.0)
    if overbuild < config.OVERPRODUCTION_MIN_UNITS:
        return []
    detail = "; ".join(f"{_run_label(r)}: ordered {_fmt(r.net_units_ordered)}, wrapped "
                       f"{_fmt(r.wrapped_units)} ({r.expected_ending_inventory:+,.0f})" for r in runs)
    action = (f"Apply the {_fmt(overbuild)}-unit surplus to the next sales day for this SKU (or designate to "
              f"freezer)" if overbuilt >= overbuild else
              f"Cut the open recovery order to the {_fmt(remaining)} still needed before it wraps; as ordered it "
              f"makes {_fmt(overbuild)} too many")
    return [Finding(
        "R05", p.sku, p.sku, p.description, overbuild, p.unit_price,
        evidence={"runs": detail, "demand": p.demand_total, "net_ordered_all_runs": p.net_ordered,
                  "wrapped_all_runs": p.wrapped, "need_after_first_run": remaining},
        recommendation=(
            f"After run 1 wrapped {_fmt(first.wrapped_units)}, "
            + (f"only {_fmt(remaining)} units were still needed" if remaining else
               "the SKU requirement was already covered")
            + f", but the recovery run(s) ordered {_fmt(recovery_ordered)}. Across runs ODI ordered "
            f"{_fmt(p.net_ordered)} against {_fmt(p.demand_total)} MIPS demand and "
            f"{_fmt(p.wrapped)} were wrapped. {action}, and size future recovery runs to the remaining "
            f"shortfall only."),
        severity="High" if overbuild >= 1000 else "Medium",
        systems=["MIPS", "ODI"])]


def r06_carryover(p: SkuPosition) -> list[Finding]:
    surplus = -p.net_requirement
    if (len(p.active_runs) >= 2 or surplus < config.CARRYOVER_MIN_UNITS
            or any(r.net_units_ordered < 0 for r in p.runs)):
        return []
    return [Finding(
        "R06", p.sku, p.sku, p.description, surplus, p.unit_price,
        evidence={"demand": p.demand_total, "inventory_applied": p.implied_inventory,
                  "wrapped": p.wrapped, "odi_expected_ending_inventory": p.expected_excess},
        recommendation=(
            f"{_fmt(surplus)} units remain after covering all {_fmt(p.demand_total)} units of demand. "
            f"Carry them to the next sales day as beginning inventory for this SKU and reduce the next "
            f"production requirement by {_fmt(surplus)} (or designate to freezer), instead of letting "
            f"MIPS roll them into an unrelated line."),
        severity="Medium" if surplus >= 1000 else "Low",
        systems=["ODI", "MIPS", "WMS"])]


def r07_untracked_supply(p: SkuPosition) -> list[Finding]:
    gap = p.net_requirement
    if gap < 20 or not p.wms or p.wms_allocated < p.demand_current:
        return []
    return [Finding(
        "R07", p.sku, p.sku, p.description, gap, p.unit_price,
        evidence={"demand": p.demand_total, "inventory_applied": p.implied_inventory,
                  "wrapped": p.wrapped, "wms_allocated": p.wms_allocated,
                  "moved_demand": p.demand_moved, "odi_runs": len(p.active_runs),
                  **({"returned_to_plant": p.returns} if p.returns else {})},
        recommendation=(
            f"WMS allocated the full {_fmt(p.wms_allocated)} units, yet production plus applied "
            f"inventory only covers {_fmt(p.usable_supply)} of {_fmt(p.demand_total)} demand"
            + (f" ({_fmt(p.returns)} of the {_fmt(p.wrapped)} wrapped went back to the plant)" if p.returns else "")
            + f". {_fmt(gap)} units came from stock MIPS does not see (freezer or prior-day tickets)"
            + (f", or part of the {_fmt(p.demand_moved)} units moved to the prior sales day went unfilled"
               if p.demand_moved else "") +
            f". Confirm the freezer/ticket draw in WMS and deduct it in MIPS; otherwise freezer balances "
            f"stay overstated."),
        severity="High" if gap >= 500 else "Medium",
        systems=["WMS", "MIPS", "ODI"])]


def r08_negative_net(p: SkuPosition) -> list[Finding]:
    neg = [r for r in p.runs if r.net_units_ordered < 0]
    if not neg:
        return []
    excess = -sum(r.net_units_ordered for r in neg)
    return [Finding(
        "R08", p.sku, p.sku, p.description, excess, p.unit_price,
        evidence={"runs": "; ".join(f"{_run_label(r)}: net {_fmt(r.net_units_ordered)}" for r in neg),
                  "demand": p.demand_total, "wms_allocated": p.wms_allocated},
        recommendation=(
            f"ODI shows a negative net order ({_fmt(-excess)}): inventory already exceeds the "
            f"{_fmt(p.demand_total)}-unit demand by {_fmt(excess)}. No production is needed. Remove the "
            f"run from the schedule, and verify the {_fmt(excess)} surplus units exist in WMS before they are "
            f"carried forward."),
        systems=["MIPS", "ODI", "WMS"])]


def r09_order_mismatch(p: SkuPosition) -> list[Finding]:
    if not p.wms or not p.lines:
        return []
    diff = p.wms_order_qty - p.demand_current
    if abs(diff) <= config.QTY_TOLERANCE_UNITS:
        return []
    last_mod = max((l.last_modified for l in p.lines if l.last_modified), default=None)
    direction = (f"WMS carries {_fmt(diff)} more units than MIPS scheduled - the order changed after MIPS "
                 f"last synced{f' ({last_mod:%m/%d %H:%M})' if last_mod else ''}. Confirm the WMS order and "
                 f"add {_fmt(diff)} to the MIPS requirement, or production will be short."
                 if diff > 0 else
                 f"MIPS scheduled {_fmt(-diff)} more units than WMS has on order. Refresh MIPS from the "
                 f"order source; the extra {_fmt(-diff)} units are an over-production candidate.")
    return [Finding(
        "R09", p.sku, p.sku, p.description, diff, p.unit_price,
        evidence={"mips_demand_sales_date": p.demand_current, "wms_order_qty": p.wms_order_qty,
                  "mips_last_modified": str(last_mod) if last_mod else None},
        recommendation=direction, systems=["MIPS", "WMS"])]


def r10_wms_short(p: SkuPosition) -> list[Finding]:
    if not p.wms or p.wms_allocated >= p.wms_order_qty:
        return []
    short = p.wms_order_qty - p.wms_allocated
    custs = [o.customer for o in p.wms.orders if o.allocated_units < o.order_qty]
    return [Finding(
        "R10", p.sku, p.sku, p.description, short, p.unit_price,
        evidence={"wms_order_qty": p.wms_order_qty, "wms_allocated": p.wms_allocated,
                  "wrapped": p.wrapped, "short_customers": ", ".join(custs)},
        recommendation=(
            f"WMS is {_fmt(short)} units short for {', '.join(custs)} although ODI wrapped "
            f"{_fmt(p.wrapped)}. Check for returned/damaged tickets not netted from the received total, "
            f"or stale workstation data, before a new work order is created."),
        systems=["WMS", "ODI"])]


def r11_uom(p: SkuPosition) -> list[Finding]:
    if not p.wms or not p.upc_mips or p.upc_mips == p.wms.units_per_container:
        return []
    return [Finding(
        "R11", p.sku, p.sku, p.description, 0, p.unit_price,
        evidence={"mips_units_per_container": p.upc_mips,
                  "wms_units_per_container": p.wms.units_per_container},
        recommendation=(
            f"MIPS uses {p.upc_mips:.0f} units/container but WMS uses "
            f"{p.wms.units_per_container:.0f}. Stack and tray counts will not tie out between systems; "
            f"align the item master (Oracle is the system of record)."),
        systems=["MIPS", "WMS"])]


def r16_odi_order_qty(p: SkuPosition) -> list[Finding]:
    if not p.active_runs or not p.lines:
        return []
    odi_qty = sum(r.mips_order_qty for r in p.runs)
    if abs(odi_qty - p.demand_sales_orders) <= config.QTY_TOLERANCE_UNITS:
        return []
    return [Finding(
        "R16", p.sku, p.sku, p.description, odi_qty - p.demand_sales_orders, p.unit_price,
        evidence={"odi_mips_order_qty": odi_qty, "mips_sales_order_qty": p.demand_sales_orders},
        recommendation="ODI's copy of the MIPS sales-order quantity differs from MIPS. Re-run the "
                       "MIPS→ODI interface for this SKU and confirm no order transaction was dropped.",
        systems=["MIPS", "ODI"])]


SKU_RULES = [r01_first_line_allocation, r02_untraceable_inventory, r05_recovery_run, r06_carryover,
             r07_untracked_supply, r08_negative_net, r09_order_mismatch, r10_wms_short, r11_uom,
             r16_odi_order_qty]


# --------------------------------------------------------------------------
# Run / plant-level rules
# --------------------------------------------------------------------------

def r03_r04_run_structure(src: SourceData, positions: dict[str, SkuPosition]) -> list[Finding]:
    out = []
    real = [r for r in src.fg_runs if r.net_units_ordered or r.wrapped_units]
    for r in src.fg_runs:
        if r.net_units_ordered or r.wrapped_units:
            continue
        price = positions[r.sku].unit_price if r.sku in positions else config.DEFAULT_UNIT_PRICE
        base = re.sub(r"A$", "", r.formula_type)
        # A moved run shows up as a zero, no-shift copy whose formula (± 'A' alt suffix)
        # actually ran on another line.
        moved_to = next((x for x in real if re.sub(r"A$", "", x.formula_type) == base
                         and x.line != r.line), None)
        if r.shift is None and moved_to:
            out.append(Finding(
                "R04", f"{r.formula_type}-{r.sku}", r.sku, r.description, 0, price,
                evidence={"ghost_run": _run_label(r), "actual_run": _run_label(moved_to),
                          "actual_run_sku": moved_to.sku},
                recommendation=(
                    f"Run {r.formula_type} was moved from {r.line} to {moved_to.line} "
                    f"({moved_to.formula_type}). A zero-quantity copy remains on {r.line}. Confirm inventory "
                    f"and WMS work-order allocations moved with the run, then remove the ghost entry."),
                systems=["MIPS", "ODI"], base_priority=1000))
        else:
            out.append(Finding(
                "R03", f"{r.formula_type}-{r.sku}", r.sku, r.description, 0, price,
                evidence={"run": _run_label(r), "item_structure": r.item_structure},
                recommendation=(
                    f"{r.description} is scheduled in run {r.formula_type} with zero demand and zero "
                    f"output. If inventory was applied to this line it never reduces real demand; move any "
                    f"allocation to the active line and close the blind line."),
                systems=["MIPS", "ODI"]))
    return out


def r12_r13_wms_reports(src: SourceData) -> list[Finding]:
    out = []
    unscanned = [o for o in src.fill_orders if o.shipped_qty > 0 and o.loaded_qty == 0]
    if unscanned:
        units = sum(o.shipped_qty for o in unscanned)
        out.append(Finding(
            "R12", "PLANT", "", "All shipments", units, 0.0,
            evidence={"orders": len(unscanned), "of_total_orders": len(src.fill_orders),
                      "shipped_units": units},
            recommendation=(
                f"{len(unscanned)} of {len(src.fill_orders)} orders ({_fmt(units)} units) show Shipped with "
                f"Loaded = 0. Trailer load scans are missing, so shipment allocation cannot be traced "
                f"ticket-to-trailer. Confirm the ship transactions reached Oracle within the 10-minute SLA "
                f"and reinstate load scanning."),
            systems=["WMS", "Oracle"], base_priority=5000))
    item_total = sum(i.order_qty for i in src.wms_items.values())
    if src.fill_report_total and abs(item_total - src.fill_report_total) > config.QTY_TOLERANCE_UNITS:
        diff = src.fill_report_total - item_total
        out.append(Finding(
            "R13", "FILL-VS-ITEM", "", "WMS report totals", diff, config.DEFAULT_UNIT_PRICE,
            evidence={"fill_rate_total": src.fill_report_total, "item_summary_total": item_total},
            recommendation=(
                f"The WMS Fill Rate report totals {_fmt(src.fill_report_total)} units but the Order Summary by "
                f"Item totals {_fmt(item_total)} ({diff:+,.0f}). An order line is missing from one extract or "
                f"was changed between report runs. Reconcile at order-line level before financial close."),
            systems=["WMS"], base_priority=1000))
    for item in src.wms_items.values():
        if item.report_total_order_qty and item.report_total_order_qty != item.order_qty:
            out.append(Finding(
                "R13", f"ITEM-{item.sku}", item.sku, item.description,
                item.report_total_order_qty - item.order_qty, config.DEFAULT_UNIT_PRICE,
                evidence={"printed_total": item.report_total_order_qty, "sum_of_lines": item.order_qty},
                recommendation="Report subtotal does not equal the sum of customer lines; re-extract the report.",
                systems=["WMS"]))
    return out


def r14_batches(src: SourceData) -> list[Finding]:
    out = []
    for b in src.batches:
        if b.sched_batches and b.actual_batches != b.sched_batches:
            diff = b.actual_batches - b.sched_batches
            pieces = b.theo_pieces / b.actual_batches * diff if b.actual_batches else 0
            out.append(Finding(
                "R14", f"{b.formula_type}-{b.line}", "", b.description, 0, 0.0,
                evidence={"formula": b.formula_type, "line": b.line, "shift": b.shift,
                          "scheduled": b.sched_batches, "mixed": b.actual_batches,
                          "approx_extra_pieces": round(pieces)},
                recommendation=(
                    f"{b.formula_type} ({b.description}, {b.line}) mixed {b.actual_batches:.0f} batches vs "
                    f"{b.sched_batches:.0f} scheduled ({diff:+.0f}, ≈{pieces:,.0f} pieces). Record the reason "
                    f"(recovery/rework) so extra output is expected in WMS and not treated as unplanned."),
                systems=["MIPS", "ODI"], base_priority=500))
    return out


def r15_key_hygiene(src: SourceData) -> list[Finding]:
    dirty = [l for l in src.mips if l.raw_sku != l.raw_sku.strip()]
    if not dirty:
        return []
    by_source = sorted({l.source_system for l in dirty})
    return [Finding(
        "R15", "MIPS-FG-REF", "", "MIPS FG Oracle Ref", 0, 0.0,
        evidence={"lines_with_padding": len(dirty), "of_lines": len(src.mips), "source_systems": by_source},
        recommendation=(
            f"{len(dirty)} of {len(src.mips)} MIPS lines (from {', '.join(by_source)}) carry a padded FG "
            f"Oracle Ref (e.g. '{dirty[0].raw_sku}'). Exact-match joins to WMS/Oracle silently fail; trim at "
            f"the interface. The agent normalises keys in the meantime."),
        systems=["MIPS"])]


# --------------------------------------------------------------------------
# Real-time rules (event feed only: the extracts carry no tickets or postings)
# --------------------------------------------------------------------------

def _unposted(src: SourceData) -> list:
    posted = {o.source_ref for o in src.oracle_txns if o.status == "POSTED"}
    return [t for t in src.wms_txns if t.txn not in posted and t.at is not None]


def r18_unposted(src: SourceData, positions: dict[str, SkuPosition]) -> list[Finding]:
    if src.as_of is None:
        return []
    sla = dt.timedelta(minutes=config.ORACLE_POSTING_SLA_MINUTES)
    oracle = {o.source_ref: o for o in src.oracle_txns}
    out = []
    for t in _unposted(src):
        waited = src.as_of - t.at
        if waited < sla:
            continue
        o = oracle.get(t.txn)
        p = positions.get(t.sku)
        why = (f"Oracle rejected it ({o.status}{': ' + o.error if o.error else ''})" if o
               else "Oracle has no transaction for it")
        out.append(Finding(
            "R18", t.txn, t.sku, p.description if p else t.sku, t.qty,
            p.unit_price if p else config.DEFAULT_UNIT_PRICE,
            evidence={"wms_transaction": f"{t.txn} {t.txn_type} {_fmt(t.qty)}", "wms_time": f"{t.at:%m/%d %H:%M}",
                      "minutes_waiting": int(waited.total_seconds() // 60),
                      "oracle_status": o.status if o else "not received", "oracle_error": o.error if o else None,
                      "ticket": t.ticket or None, "order": t.order or None},
            recommendation=(
                f"WMS {t.txn_type.replace('_', ' ').lower()} {t.txn} ({_fmt(t.qty)} units, {t.at:%H:%M}) is "
                f"{int(waited.total_seconds() // 60)} minutes old and {why}. Oracle inventory and finance are "
                f"understated until it posts: resubmit it through the integration and check the interface "
                f"error queue."),
            systems=["WMS", "Oracle"], base_priority=2000))
    return out


def next_deadline(src: SourceData) -> dt.datetime | None:
    """The next plant time at which an unposted WMS transaction breaches the R18 window."""
    if src.as_of is None:
        return None
    sla = dt.timedelta(minutes=config.ORACLE_POSTING_SLA_MINUTES)
    due = [t.at + sla for t in _unposted(src) if t.at + sla > src.as_of]
    return min(due) if due else None


def r19_returns(src: SourceData, positions: dict[str, SkuPosition]) -> list[Finding]:
    returned: dict[str, float] = defaultdict(float)
    for t in src.wms_txns:
        if t.txn_type == "RETURN_TO_PLANT" and t.ticket:
            returned[t.ticket] += t.qty
    tickets = {t.ticket: t for t in src.tickets}
    out = []
    for nbr, qty in returned.items():
        tk = tickets.get(nbr)
        if tk is None or qty - tk.returned_qty <= config.QTY_TOLERANCE_UNITS:
            continue
        gap = qty - tk.returned_qty
        p = positions.get(tk.sku)
        should = tk.received_qty - qty
        out.append(Finding(
            "R19", nbr, tk.sku, p.description if p else tk.sku, gap, p.unit_price if p else config.DEFAULT_UNIT_PRICE,
            evidence={"ticket": f"{nbr} ({tk.run_code or 'no run'}, {tk.location or 'no location'})",
                      "received": tk.received_qty, "returned_by_transactions": qty,
                      "returned_on_ticket": tk.returned_qty, "net_received_on_ticket": tk.net_received_qty,
                      "net_received_should_be": should},
            recommendation=(
                f"WMS booked {_fmt(qty)} units returned to the plant against ticket {nbr}, but the ticket still "
                f"shows {_fmt(tk.net_received_qty)} received, not {_fmt(should)}. Reduce the ticket's net received "
                f"quantity by {_fmt(gap)}. Until then WMS can allocate {_fmt(gap)} units that are back at the "
                f"plant, and the returned units are missing from the production requirement."),
            severity="High" if gap >= 500 else "Medium",
            systems=["WMS", "MIPS"]))
    return out


def run_all(src: SourceData, positions: dict[str, SkuPosition]) -> list[Finding]:
    findings: list[Finding] = []
    for p in positions.values():
        for rule in SKU_RULES:
            findings.extend(rule(p))
    findings.extend(r03_r04_run_structure(src, positions))
    findings.extend(r12_r13_wms_reports(src))
    findings.extend(r14_batches(src))
    findings.extend(r15_key_hygiene(src))
    findings.extend(r18_unposted(src, positions))
    findings.extend(r19_returns(src, positions))
    return findings
