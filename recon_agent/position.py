"""Reconciled SKU position: the business team's demand-to-delivery equation, one row per SKU.

    Total demand   = Sales orders (BUSS, all lines) + Forecast (Oracle Planning)
    Demand left    = Total demand - Allocated stock (WMS)        -> MIPS Forecast / Order Quantity
    Shortfall      = Demand left  - Available stock (WMS)        -> MIPS Work Order Quantity
    Check          = Work order + Available + Allocated = Total demand

For 76108 on 12 Sep 2026: (14,339 + 41,769) - 473 - 7,000 = 48,635 (docs/DATA_MODEL_9NOV.md).
All on-hand stock is kept in WMS only, so when available stock covers the demand left the
shortfall is 0 and the surplus simply stays in WMS.

Each ODI mapping row belongs to the SKU of the demand line it maps (the BUSS order line or
the Oracle forecast it names): ODI leaves Item Number empty on lines served from stock.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import config
from .ingest import Forecast, OdiEntry, SalesOrder, SourceData, WmsStock, WorkOrder


@dataclass
class SkuPosition:
    sku: str
    description: str
    unit_price: float
    orders: list[SalesOrder] = field(default_factory=list)
    forecasts: list[Forecast] = field(default_factory=list)
    odi: list[OdiEntry] = field(default_factory=list)
    stock: list[WmsStock] = field(default_factory=list)
    work_orders: list[WorkOrder] = field(default_factory=list)
    in_item_master: bool = True

    # --- demand (BUSS + Oracle) -------------------------------------------
    @property
    def so_qty(self) -> float:
        return sum(o.qty for o in self.orders)

    @property
    def forecast_qty(self) -> float:
        return sum(f.qty for f in self.forecasts)

    @property
    def demand(self) -> float:
        return self.so_qty + self.forecast_qty

    # --- stock (WMS) --------------------------------------------------------
    @property
    def on_hand(self) -> float:
        return sum(s.on_hand for s in self.stock)

    @property
    def available(self) -> float:
        return sum(s.available for s in self.stock)

    @property
    def allocated(self) -> float:
        return sum(s.allocated for s in self.stock)

    # --- the equation -------------------------------------------------------
    @property
    def demand_left(self) -> float:
        """What MIPS must plan: demand less the stock already reserved for orders."""
        return max(0.0, self.demand - self.allocated)

    @property
    def shortfall(self) -> float:
        """What the work order must make: demand left less the free stock."""
        return max(0.0, self.demand_left - self.available)

    @property
    def stock_surplus(self) -> float:
        """Free stock beyond the demand left: it stays in WMS."""
        return max(0.0, self.available - self.demand_left)

    # --- MIPS ---------------------------------------------------------------
    @property
    def mips_demand(self) -> float:
        return sum(w.demand_qty for w in self.work_orders)

    @property
    def wo_qty(self) -> float:
        return sum(w.wo_qty for w in self.work_orders)

    @property
    def wo_numbers(self) -> list[str]:
        return [w.wo for w in self.work_orders]

    # --- ODI ----------------------------------------------------------------
    @property
    def odi_demand(self) -> float:
        return sum(e.qty for e in self.odi)

    @property
    def odi_allocated(self) -> float:
        return sum(e.allocated for e in self.odi)

    @property
    def odi_wo_qty(self) -> dict[str, float]:
        """Work-order quantity ODI carries per work order (the first row that names it)."""
        out: dict[str, float] = {}
        for e in self.odi:
            if e.wo:
                out.setdefault(e.wo, e.wo_qty)
        return out

    @property
    def odi_demand_left(self) -> float:
        """The demand left as ODI's copy of the demand gives it (what MIPS sees through ODI)."""
        return max(0.0, self.odi_demand - self.allocated)

    # --- check --------------------------------------------------------------
    @property
    def planned_supply(self) -> float:
        return self.wo_qty + self.available + self.allocated

    @property
    def balance(self) -> float:
        """Planned supply less demand: 0 reconciles, positive over-produces, negative falls short."""
        return self.planned_supply - self.demand

    @property
    def sales_date(self) -> dt.date | None:
        dates = Counter(d for d in [o.sales_date for o in self.orders] + [f.date for f in self.forecasts] if d)
        return dates.most_common(1)[0][0] if dates else None

    @property
    def buss_codes(self) -> set[str]:
        return {o.buss_code for o in self.orders if o.buss_code}

    def to_row(self) -> dict:
        return {
            "SKU": self.sku,
            "Description": self.description,
            "Sales Order Lines": len(self.orders),
            "Sales Orders (BUSS)": self.so_qty,
            "Forecast (Oracle)": self.forecast_qty,
            "Total Demand": self.demand,
            "WMS On Hand": self.on_hand,
            "WMS Allocated": self.allocated,
            "WMS Available": self.available,
            "Demand Left": self.demand_left,
            "Shortfall": self.shortfall,
            "Stock Surplus": self.stock_surplus,
            "MIPS Forecast / Order Qty": self.mips_demand,
            "MIPS Work Order Qty": self.wo_qty,
            "Work Orders": ", ".join(self.wo_numbers),
            "ODI Demand": self.odi_demand,
            "ODI Allocated": self.odi_allocated,
            "Balance (supply - demand)": self.balance,
            "Unit Price": self.unit_price,
        }


def resolve_odi_skus(src: SourceData) -> None:
    """Give each ODI row the SKU of the demand line it maps; fall back to its own Item Number."""
    so_sku = {(o.order_nbr, o.line): o.sku for o in src.sales_orders}
    fc_sku = {f.forecast_id: f.sku for f in src.forecasts}
    wo_sku = {w.wo: w.sku for w in src.work_orders}
    for e in src.odi:
        if e.is_sales_order:
            sku = so_sku.get((e.ref, e.ref_line or 1))
        else:
            sku = fc_sku.get(e.ref)
        e.sku = sku or e.item or wo_sku.get(e.wo, "")


def build_positions(src: SourceData) -> dict[str, SkuPosition]:
    """One position per SKU that has orders, a work order, an ODI row or stock on the day.

    An item with only an Oracle forecast is planned in Oracle but not yet scheduled anywhere
    else: it is master planning, not a mismatch, so it gets no position (the workbook carries
    forecasts for all ten FG items and the rest of the chain for one)."""
    resolve_odi_skus(src)
    by: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for o in src.sales_orders:
        by[o.sku]["orders"].append(o)
    for w in src.work_orders:
        by[w.sku]["work_orders"].append(w)
    for e in src.odi:
        if e.sku:
            by[e.sku]["odi"].append(e)
    for s in src.stock:
        if s.item_type != "RM":
            by[s.sku]["stock"].append(s)
    in_scope = set(by)
    for f in src.forecasts:
        if f.sku in in_scope:
            by[f.sku]["forecasts"].append(f)

    positions: dict[str, SkuPosition] = {}
    for sku, parts in by.items():
        item = src.items.get(sku)
        desc = (item.description if item else "") or next(
            (x.description for k in ("orders", "work_orders", "stock", "forecasts") for x in parts.get(k, [])
             if x.description), "")
        price = next((o.price for o in parts.get("orders", []) if o.price), config.DEFAULT_UNIT_PRICE)
        positions[sku] = SkuPosition(sku=sku, description=desc, unit_price=price,
                                     orders=parts.get("orders", []), forecasts=parts.get("forecasts", []),
                                     odi=parts.get("odi", []), stock=parts.get("stock", []),
                                     work_orders=parts.get("work_orders", []),
                                     in_item_master=item is not None or not src.items)
    return positions
