"""Reconciled SKU position: one row per SKU that all three systems agree on.

    Net production requirement = Total valid demand
                                 - Available usable inventory
                                 - Completed production
                                 + Valid returns / adjustments

Demand comes from every MIPS schedule line for the SKU (sales orders *and*
supply-planning lines, including lines moved to another sales day), so
inventory is consolidated at SKU level instead of being applied to the first
matching line only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import config
from .ingest import FgRun, MipsLine, SourceData, WmsItem


@dataclass
class SkuPosition:
    sku: str
    description: str
    unit_price: float
    lines: list[MipsLine] = field(default_factory=list)
    runs: list[FgRun] = field(default_factory=list)
    wms: WmsItem | None = None
    returns: float = 0.0   # units WMS returned to the plant (event feed only): wrapped but not usable

    # --- MIPS demand -----------------------------------------------------
    @property
    def demand_total(self) -> float:
        return sum(l.order_qty for l in self.lines)

    @property
    def demand_moved(self) -> float:
        return sum(l.order_qty for l in self.lines if l.order_moved)

    @property
    def demand_current(self) -> float:
        return self.demand_total - self.demand_moved

    @property
    def demand_sales_orders(self) -> float:
        return sum(l.order_qty for l in self.lines if l.is_sales_order)

    @property
    def demand_supply_plan(self) -> float:
        return self.demand_total - self.demand_sales_orders

    # --- ODI / Oracle production ----------------------------------------
    @property
    def net_ordered(self) -> float:
        return sum(r.net_units_ordered for r in self.runs)

    @property
    def wrapped(self) -> float:
        return sum(r.wrapped_units for r in self.runs)

    @property
    def expected_excess(self) -> float:
        return sum(r.expected_ending_inventory for r in self.runs)

    @property
    def recorded_inventory(self) -> float:
        return sum(r.mips_beginning_inventory + r.mips_inventory_adjustment for r in self.runs)

    @property
    def active_runs(self) -> list[FgRun]:
        return [r for r in self.runs if r.net_units_ordered or r.wrapped_units]

    @property
    def implied_inventory(self) -> float:
        """Inventory MIPS netted out of demand before sending it to production.

        Only the part of demand not sent to the *first* active run counts;
        later recovery runs re-order the shortfall and must not reduce it.
        """
        if not self.active_runs:
            return 0.0
        first_run_order = self.active_runs[0].net_units_ordered
        return max(0.0, self.demand_total - first_run_order) if len(self.active_runs) > 1 \
            else max(0.0, self.demand_total - self.net_ordered)

    # --- WMS fulfilment --------------------------------------------------
    @property
    def wms_order_qty(self) -> float:
        return self.wms.order_qty if self.wms else 0.0

    @property
    def wms_allocated(self) -> float:
        return self.wms.allocated if self.wms else 0.0

    @property
    def upc_mips(self) -> float:
        counts = Counter(l.units_per_container for l in self.lines if l.container_code == "01")
        return counts.most_common(1)[0][0] if counts else 0.0

    # --- Reconciled requirement -----------------------------------------
    @property
    def net_requirement(self) -> float:
        """Positive = still to produce, negative = surplus to carry forward."""
        return self.demand_total - self.implied_inventory - self.wrapped + self.returns

    @property
    def first_line_only_requirement(self) -> float:
        """What MIPS reports when inventory is applied to the first line only."""
        if not self.lines:
            return 0.0
        covered = min(self.implied_inventory, self.lines[0].order_qty)
        return self.demand_total - covered

    @property
    def consolidated_requirement(self) -> float:
        return max(0.0, self.demand_total - self.implied_inventory)

    @property
    def first_line_overproduction_risk(self) -> float:
        return max(0.0, self.first_line_only_requirement - self.consolidated_requirement)

    def to_row(self) -> dict:
        return {
            "SKU": self.sku,
            "Description": self.description,
            "MIPS Lines": len(self.lines),
            "Demand Total": self.demand_total,
            "Demand Sales Date": self.demand_current,
            "Demand Moved": self.demand_moved,
            "Sales Orders (BUSS)": self.demand_sales_orders,
            "Supply Plan (OCloud)": self.demand_supply_plan,
            "Inventory Applied (implied)": self.implied_inventory,
            "Inventory Recorded in MIPS": self.recorded_inventory,
            "ODI Runs": len(self.active_runs),
            "ODI Net Ordered": self.net_ordered,
            "ODI Wrapped": self.wrapped,
            "ODI Expected Excess": self.expected_excess,
            "WMS Order Qty": self.wms_order_qty,
            "WMS Allocated": self.wms_allocated,
            "Returns to Plant": self.returns,
            "Net Requirement": self.net_requirement,
            "Units/Container MIPS": self.upc_mips,
            "Units/Container WMS": self.wms.units_per_container if self.wms else None,
            "Unit Price": self.unit_price,
        }


def propose_allocation(p: SkuPosition) -> list[dict]:
    """Consolidated line-level allocation for one SKU.

    Inventory, then production, is applied across *all* lines in priority
    order - earliest sales date first, customer sales orders before supply-plan
    lines - and whatever is left is carried to the next sales day.
    """
    ordered = sorted(p.lines, key=lambda l: (l.offset_sales_date, not l.is_sales_order, -l.order_qty))
    inventory, production = p.implied_inventory, p.wrapped
    rows = []
    for l in ordered:
        from_inv = min(inventory, l.order_qty)
        inventory -= from_inv
        from_prod = min(production, l.order_qty - from_inv)
        production -= from_prod
        rows.append({"SKU": p.sku, "Description": p.description,
                     "Sales Date": l.offset_sales_date, "Line Type": "Sales order" if l.is_sales_order
                     else "Supply plan", "Moved": l.order_moved, "Order Qty": l.order_qty,
                     "Inventory Applied": from_inv, "Production Applied": from_prod,
                     "Unfilled": l.order_qty - from_inv - from_prod})
    leftover = inventory + production
    if leftover:
        rows.append({"SKU": p.sku, "Description": p.description, "Sales Date": "next sales day",
                     "Line Type": "Carry forward", "Moved": False, "Order Qty": 0,
                     "Inventory Applied": -leftover, "Production Applied": 0, "Unfilled": 0})
    return rows


def build_positions(src: SourceData) -> dict[str, SkuPosition]:
    lines_by_sku: dict[str, list[MipsLine]] = defaultdict(list)
    for l in src.mips:
        lines_by_sku[l.sku].append(l)
    runs_by_sku: dict[str, list[FgRun]] = defaultdict(list)
    for r in sorted(src.fg_runs, key=lambda r: (r.start is None, r.start)):
        runs_by_sku[r.sku].append(r)

    returns: dict[str, float] = defaultdict(float)
    for t in src.wms_txns:
        if t.txn_type == "RETURN_TO_PLANT":
            returns[t.sku] += t.qty

    positions: dict[str, SkuPosition] = {}
    for sku in set(lines_by_sku) | set(runs_by_sku) | set(src.wms_items):
        lines = lines_by_sku.get(sku, [])
        runs = runs_by_sku.get(sku, [])
        wms = src.wms_items.get(sku)
        desc = (wms.description if wms else "") or (runs[0].description if runs else "") \
            or (lines[0].description if lines else "")
        price = next((l.retail_price for l in lines if l.retail_price), config.DEFAULT_UNIT_PRICE)
        positions[sku] = SkuPosition(sku=sku, description=desc, unit_price=price,
                                     lines=lines, runs=runs, wms=wms, returns=returns.get(sku, 0.0))
    return positions
