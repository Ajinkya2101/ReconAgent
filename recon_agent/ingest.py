"""Source adapters: read each system's extract into canonical records.

Every adapter normalises the join keys (Oracle FG number, dates, line codes) so
the reconciliation engine never sees source-specific quirks such as the
trailing whitespace BUSS appends to FG Oracle Ref, or the block layout of the
WMS Order Summary report. In production these adapters would be swapped for
API/middleware readers without changing anything downstream.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from . import config


def norm_sku(value) -> str:
    """Canonical Oracle FG number: stripped string, no leading zeros, no '.0'."""
    if value is None:
        return ""
    s = str(value).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.lstrip("0") or "0"


def as_date(value) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return None


def num(value) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


CLOSED_VALUES = ("T", "TRUE", "Y", "YES", "1")


def is_closed(value) -> bool:
    """MIPS `Closed` flag. 'F' (open) on every line of the client extract; a closed line is not valid demand."""
    return value is True or str(value or "").strip().upper() in CLOSED_VALUES


@dataclass
class MipsLine:
    sku: str
    raw_sku: str
    buss_code: str
    description: str
    schedule_date: dt.date
    offset_sales_date: dt.date
    production_date: dt.date | None
    order_moved: bool
    order_qty: float
    container_code: str
    units_per_container: float
    retail_price: float
    source_system: str
    last_modified: dt.datetime | None
    closed: bool = False   # MIPS `Closed`: the line no longer counts as demand

    @property
    def is_sales_order(self) -> bool:
        return self.source_system.upper() == "BUSS"


@dataclass
class FgRun:
    sku: str
    buss_code: str
    description: str
    sales_date: dt.date
    start: dt.datetime | None
    end: dt.datetime | None
    line: str
    shift: str | None
    formula_type: str
    item_structure: str
    net_units_ordered: float
    wrapped_units: float
    expected_ending_inventory: float
    mips_beginning_inventory: float
    mips_inventory_adjustment: float
    mips_order_qty: float
    mips_order_adjustment: float
    damaged_units: float
    pieces_per_pkg: float


@dataclass
class BatchRun:
    sales_date: dt.date
    wrap_start: dt.datetime | None
    line: str
    shift: str | None
    formula_type: str
    description: str
    sched_batches: float
    actual_batches: float
    theo_pieces: float
    final_piece_variance: float
    final_piece_variance_pct: float


@dataclass
class WmsCustomerOrder:
    customer: str
    original_qty: float
    order_qty: float
    allocated_units: float
    stacks_remaining: float
    trays_remaining: float
    status: str


@dataclass
class WmsItem:
    sku: str
    description: str
    units_per_container: float
    orders: list[WmsCustomerOrder] = field(default_factory=list)
    report_total_order_qty: float = 0.0
    report_total_allocated: float = 0.0

    @property
    def order_qty(self) -> float:
        return sum(o.order_qty for o in self.orders)

    @property
    def allocated(self) -> float:
        return sum(o.allocated_units for o in self.orders)

    @property
    def original_order_qty(self) -> float:
        """What the customers first ordered ('Original Ord Qty'); differs from order_qty when an order changed."""
        return sum(o.original_qty for o in self.orders)

    @property
    def changed_orders(self) -> list[WmsCustomerOrder]:
        return [o for o in self.orders if o.original_qty and o.original_qty != o.order_qty]


@dataclass
class WmsFillOrder:
    order_nbr: str
    ship_to: str
    order_type: str
    sales_date: dt.date | None
    order_qty: float
    allocated_qty: float
    packed_qty: float
    loaded_qty: float
    shipped_qty: float
    short_qty: float


@dataclass
class WmsTicket:
    ticket: str
    sku: str
    run_code: str
    location: str
    received_at: dt.datetime | None
    received_qty: float
    returned_qty: float
    net_received_qty: float


@dataclass
class WmsTxn:
    txn: str
    txn_type: str
    at: dt.datetime | None
    sku: str
    qty: float
    ticket: str
    order: str
    reason: str


@dataclass
class OracleTxn:
    txn_id: str
    source_ref: str
    txn_type: str
    sku: str
    qty: float
    posted_at: dt.datetime | None
    status: str
    error: str


@dataclass
class SourceData:
    mips: list[MipsLine]
    fg_runs: list[FgRun]
    batches: list[BatchRun]
    wms_items: dict[str, WmsItem]
    fill_orders: list[WmsFillOrder]
    fill_report_total: float
    files: dict[str, str]
    as_of: dt.datetime | None = None   # event feed: simulated time of the projection
    # Real-time detail only the event feed carries (empty for the daily extracts).
    tickets: list[WmsTicket] = field(default_factory=list)
    wms_txns: list[WmsTxn] = field(default_factory=list)
    oracle_txns: list[OracleTxn] = field(default_factory=list)
    # Event feed only: each SKU's MIPS demand when its first production run was last ordered.
    # Demand changed after that is new demand, not inventory MIPS netted (see SkuPosition).
    demand_at_order: dict[str, float] = field(default_factory=dict)


def _find(pattern: str, data_dir: Path) -> Path:
    matches = sorted(data_dir.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No source file matching '{pattern}' in {data_dir}")
    return matches[-1]


def _rows(path: Path) -> list[tuple]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        return [tuple(r) for r in wb.active.iter_rows(values_only=True)]
    finally:
        wb.close()  # read-only mode keeps the file handle open until closed


def _table(rows: list[tuple], header_marker: str) -> list[dict]:
    """Rows below the first row whose first cell equals header_marker."""
    idx = next(i for i, r in enumerate(rows) if r and r[0] == header_marker)
    header = rows[idx]
    return [dict(zip(header, r)) for r in rows[idx + 1:] if any(v is not None for v in r)]


# --- record mappers ----------------------------------------------------------
# Each takes rows keyed by the client's own column names, so the Excel extracts and
# the event feed (recon_agent/events, same columns) produce identical records.

def mips_lines(records: list[dict]) -> list[MipsLine]:
    out = []
    for r in records:
        if r.get("FG Oracle Ref") is None:
            continue
        out.append(MipsLine(
            sku=norm_sku(r["FG Oracle Ref"]),
            raw_sku=str(r["FG Oracle Ref"]),
            buss_code=str(r["FG BUSS Code"] or "").strip(),
            description=str(r["FG Description"] or "").strip(),
            schedule_date=as_date(r["Schedule Date"]),
            offset_sales_date=as_date(r["Offset Sales Date"]),
            production_date=as_date(r["Production Date"]),
            order_moved=bool(r["Order Moved"]),
            order_qty=num(r["Order Quantity"]),
            container_code=str(r["Container Code"] or ""),
            units_per_container=num(r["Units per Container"]),
            retail_price=num(r["Retail Price"]),
            source_system=str(r["Source System"] or "").strip(),
            last_modified=r["Last Modify Date Time"],
            closed=is_closed(r.get("Closed")),
        ))
    return out


def fg_runs(records: list[dict]) -> list[FgRun]:
    out = []
    for r in records:
        out.append(FgRun(
            sku=norm_sku(r["OracleFGNumber"]),
            buss_code=str(r["LegacyFGNumber"] or "").strip(),
            description=str(r["OracleFGDescription"] or "").strip(),
            sales_date=as_date(r["SalesDate"]),
            start=r["ProductionStartDateTime"],
            end=r["ProductionEndDateTime"],
            line=str(r["LineCode"] or ""),
            shift=r["ShiftName"],
            formula_type=str(r["OracleTypeCode"] or "").strip(),
            item_structure=str(r["OCloudFGItemStructure"] or ""),
            net_units_ordered=num(r["NetUnitsOrdered"]),
            wrapped_units=num(r["ActualWrappedUnits"]),
            expected_ending_inventory=num(r["ExpectedEndingInventoryQty"]),
            mips_beginning_inventory=num(r["MIPSBeginningInventoryQty"]),
            mips_inventory_adjustment=num(r["MIPSInventoryAdjustmentQty"]),
            mips_order_qty=num(r["MIPSOrderQty"]),
            mips_order_adjustment=num(r["MIPSOrderAdjustmentQty"]),
            damaged_units=num(r["TotalActualDamagedUnits"]),
            pieces_per_pkg=num(r["OraclePiecesPerPkg"]),
        ))
    return out


def batch_runs(records: list[dict]) -> list[BatchRun]:
    out = []
    for r in records:
        out.append(BatchRun(
            sales_date=as_date(r["SalesDate"]),
            wrap_start=r["WrappingStartDateTime"],
            line=str(r["Prod Line"] or ""),
            shift=r["Shift"],
            formula_type=str(r["Oracle Type"] or "").strip(),
            description=str(r["MIPS Description"] or "").strip(),
            sched_batches=num(r["MIPS Sched Batches"]),
            actual_batches=num(r["Act Batches Mixed"]),
            theo_pieces=num(r["Theo Batch Pieces Qty"]),
            final_piece_variance=num(r["Final Piece Variance Qty"]),
            final_piece_variance_pct=num(r["Final Piece Variance %"]),
        ))
    return out


def wms_items(records: list[dict]) -> dict[str, WmsItem]:
    """One record per item and customer line, as in the 'Order Summary Report by Item'."""
    items: dict[str, WmsItem] = {}
    for r in records:
        sku = norm_sku(r["Item Nbr"])
        item = items.get(sku)
        if item is None:
            item = items[sku] = WmsItem(sku=sku, description=str(r["Description"] or "").strip(),
                                        units_per_container=num(r["Units per Container"]))
        item.orders.append(WmsCustomerOrder(
            customer=r["Cust Name"], original_qty=num(r["Original Ord Qty"]), order_qty=num(r["Order Qty"]),
            allocated_units=num(r["Allocated Units Qty"]), stacks_remaining=num(r["Stacks Remaining"]),
            trays_remaining=num(r["Trays Remaining"]), status=str(r["Completion%"] or "")))
    return items


def fill_orders(records: list[dict]) -> list[WmsFillOrder]:
    orders = []
    for r in records:
        orders.append(WmsFillOrder(
            order_nbr=str(r["Order Nbr"]), ship_to=str(r["Ship to Name"] or ""),
            order_type=str(r["Order Type"] or ""), sales_date=as_date(r["Sales Date"]),
            order_qty=num(r["Order Qty"]), allocated_qty=num(r["Allocated Qty"]),
            packed_qty=num(r["Packed Qty"]), loaded_qty=num(r["Loaded Qty"]),
            shipped_qty=num(r["Shipped Qty"]), short_qty=num(r["Short Qty"])))
    return orders


def wms_tickets(records: list[dict]) -> list[WmsTicket]:
    return [WmsTicket(ticket=str(r["Ticket Nbr"]), sku=norm_sku(r["Item Nbr"]), run_code=str(r["Run Code"] or ""),
                      location=str(r["Location"] or ""), received_at=r["Received Date Time"],
                      received_qty=num(r["Received Qty"]), returned_qty=num(r["Returned Qty"]),
                      net_received_qty=num(r["Net Received Qty"])) for r in records]


def wms_transactions(records: list[dict]) -> list[WmsTxn]:
    return [WmsTxn(txn=str(r["Txn Nbr"]), txn_type=str(r["Txn Type"] or "").strip().upper(), at=r["Txn Date Time"],
                   sku=norm_sku(r["Item Nbr"]), qty=num(r["Qty"]), ticket=str(r["Ticket Nbr"] or ""),
                   order=str(r["Order Nbr"] or ""), reason=str(r["Reason Code"] or "")) for r in records]


def oracle_transactions(records: list[dict]) -> list[OracleTxn]:
    return [OracleTxn(txn_id=str(r["TransactionId"]), source_ref=str(r["SourceReference"] or ""),
                      txn_type=str(r["TransactionTypeName"] or ""), sku=norm_sku(r["ItemNumber"]),
                      qty=num(r["TransactionQuantity"]), posted_at=r["PostedDate"],
                      status=str(r["ProcessStatus"] or "").strip().upper(), error=str(r["ErrorMessage"] or ""))
            for r in records]


# --- Excel extract readers ---------------------------------------------------

def load_mips(path: Path) -> list[MipsLine]:
    return mips_lines(_table(_rows(path), "Plant Code"))


def load_fg_runs(path: Path) -> list[FgRun]:
    return fg_runs(_table(_rows(path), "PlantName"))


def load_batches(path: Path) -> list[BatchRun]:
    return batch_runs(_table(_rows(path), "PlantName"))


WMS_ITEM_COLUMNS = ("Cust Name", "Original Ord Qty", "Order Qty", "Order Stack Qty", "Order Tray Qty",
                    "Order Units Qty", "Allocated Stacks", "Allocated Trays", "Allocated Units Qty",
                    "Stacks Remaining", "Trays Remaining", "Completion%")


def load_wms_order_summary(path: Path) -> dict[str, WmsItem]:
    """Parse the block-structured 'Order Summary Report by Item'."""
    rows = _rows(path)
    records: list[dict] = []
    heads: list[dict] = []
    subtotals: dict[str, tuple[float, float]] = {}
    head = None
    for i, r in enumerate(rows):
        first = r[0] if r else None
        if first == "Item Nbr:":
            h = rows[i + 1]
            head = {"Item Nbr": h[0], "Units per Container": h[1], "Description": h[2]}
            heads.append(head)
        elif head is None or first == "Cust Name":
            continue
        elif isinstance(first, str) and isinstance(r[1], (int, float)):
            records.append({**head, **dict(zip(WMS_ITEM_COLUMNS, r))})
        elif first is None and isinstance(r[1], (int, float)) and records and                 records[-1]["Item Nbr"] == head["Item Nbr"]:
            subtotals[norm_sku(head["Item Nbr"])] = (num(r[2]), num(r[8]))
    lines = wms_items(records)
    # Keep the report's item order, including items printed with no customer lines.
    items = {}
    for h in heads:
        sku = norm_sku(h["Item Nbr"])
        items[sku] = lines.get(sku) or WmsItem(sku=sku, description=str(h["Description"] or "").strip(),
                                               units_per_container=num(h["Units per Container"]))
    for sku, (order_qty, allocated) in subtotals.items():
        items[sku].report_total_order_qty = order_qty
        items[sku].report_total_allocated = allocated
    return items


def load_fill_rate(path: Path) -> tuple[list[WmsFillOrder], float]:
    rows = _table(_rows(path), "Facility")
    total = next((num(r["Order Qty"]) for r in rows if r.get("Order Type") == "Total:"), 0.0)
    return fill_orders([r for r in rows if r.get("Order Type") != "Total:"]), total


def load_all(data_dir: Path | None = None) -> SourceData:
    data_dir = Path(data_dir or config.DATA_DIR)
    paths = {k: _find(p, data_dir) for k, p in config.SOURCES.items()}
    fill_orders, fill_total = load_fill_rate(paths["wms_fill_rate"])
    return SourceData(
        mips=load_mips(paths["mips_orders"]),
        fg_runs=load_fg_runs(paths["odi_finished_goods"]),
        batches=load_batches(paths["odi_batches"]),
        wms_items=load_wms_order_summary(paths["wms_order_summary"]),
        fill_orders=fill_orders,
        fill_report_total=fill_total,
        files={k: p.name for k, p in paths.items()},
    )
