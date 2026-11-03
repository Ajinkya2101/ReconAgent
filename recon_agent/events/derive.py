"""Columns a source system computes itself, so a producer never has to set them by hand.

When an operator changes ActualWrappedUnits, ODI would also change
ExpectedEndingInventoryQty; when WMS allocates, the stack and tray counts and the
completion status follow. `derive` applies those rules to one typed row. The seed
and the source simulator both use it, so simulated rows stay self-consistent.
Cross-row totals (a WMS order header against its item lines) are deliberately not
derived: in the real systems they can disagree, and that disagreement is R13.

Stack and tray maths: one tray holds `Units per Container` units and one stack
holds TRAYS_PER_STACK trays (an assumption, as in the seed).
"""
from __future__ import annotations

import datetime as dt

TRAYS_PER_STACK = 21

# Columns `derive` computes, per entity (the simulator shows them read-only while derivation is on).
DERIVED = {
    "mips.order_line": ("Last Modify Date Time",),
    "odi.finished_good": ("ExpectedEndingInventoryQty", "TotalActualDamagedUnits", "TotalPieces"),
    "odi.batch": ("Final Piece Variance %",),
    "wms.item_order": ("Order Stack Qty", "Order Tray Qty", "Allocated Stacks", "Allocated Trays",
                       "Stacks Remaining", "Trays Remaining", "Completion%"),
    "wms.order": ("Short Qty", "% Allocated", "% Loaded", "% Shipped", "Progress"),
    "wms.ticket": ("Net Received Qty",),
    "wms.transaction": (),
    "oracle.inventory_txn": (),
}


def _n(v) -> float:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _int(x: float) -> int | float:
    return int(x) if float(x).is_integer() else round(x, 6)


def stacks_and_trays(units: float, upc: float) -> tuple[int, int]:
    trays = int(units // upc) if upc else 0
    return divmod(trays, TRAYS_PER_STACK)


def derive(entity: str, row: dict, now: dt.datetime | None = None) -> dict:
    """Return `row` with its derived columns recomputed. `now` stamps MIPS's last-modified time."""
    r = dict(row)
    if entity == "mips.order_line":
        if now is not None:
            r["Last Modify Date Time"] = now
    elif entity == "odi.finished_good":
        wrapped, ordered = _n(r.get("ActualWrappedUnits")), _n(r.get("NetUnitsOrdered"))
        damaged = _n(r.get("ActualDamagedUnitsWithWrap")) + _n(r.get("ActualDamagedUnitsWithoutWrap"))
        r["ExpectedEndingInventoryQty"] = _int(wrapped - ordered)
        r["TotalActualDamagedUnits"] = _int(damaged)
        r["TotalPieces"] = _int(max(wrapped, 0) + damaged)
    elif entity == "odi.batch":
        theo, var = _n(r.get("Theo Batch Pieces Qty")), _n(r.get("Final Piece Variance Qty"))
        r["Final Piece Variance %"] = round(var / theo, 6) if theo else 0
    elif entity == "wms.item_order":
        upc, ordered, allocated = (_n(r.get("Units per Container")), _n(r.get("Order Qty")),
                                   _n(r.get("Allocated Units Qty")))
        r["Order Stack Qty"], r["Order Tray Qty"] = stacks_and_trays(ordered, upc)
        r["Allocated Stacks"], r["Allocated Trays"] = stacks_and_trays(allocated, upc)
        r["Stacks Remaining"], r["Trays Remaining"] = stacks_and_trays(max(ordered - allocated, 0), upc)
        r["Completion%"] = "GREEN" if allocated >= ordered else "YELLOW"
    elif entity == "wms.order":
        ordered, allocated = _n(r.get("Order Qty")), _n(r.get("Allocated Qty"))
        loaded, shipped = _n(r.get("Loaded Qty")), _n(r.get("Shipped Qty"))
        r["Short Qty"] = _int(max(ordered - allocated, 0))
        r["% Allocated"] = round(100 * allocated / ordered, 4) if ordered else 0
        r["% Loaded"] = round(loaded / ordered, 4) if ordered else 0       # a fraction, as in the report
        r["% Shipped"] = round(shipped / ordered, 4) if ordered else 0
        r["Progress"] = "GREEN" if allocated >= ordered else "YELLOW"
    elif entity == "wms.ticket":
        # WMS's own net: what was received less what it has booked as returned against the ticket.
        r["Net Received Qty"] = _int(_n(r.get("Received Qty")) - _n(r.get("Returned Qty")))
    return r


def order_totals(item_rows: list[dict], customer: str) -> dict:
    """WMS order header quantities rebuilt from one customer's item lines (an explicit operator action)."""
    lines = [r for r in item_rows if r.get("Cust Name") == customer]
    return {"Order Qty": _int(sum(_n(r.get("Order Qty")) for r in lines)),
            "Allocated Qty": _int(sum(_n(r.get("Allocated Units Qty")) for r in lines))}
