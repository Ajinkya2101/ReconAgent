"""Source adapters: read each system's rows into canonical records.

The data model is the business team's 9 Nov mapping (docs/DATA_MODEL_9NOV.md): one
workbook, one sheet per source - Oracle item master and forecast, BUSS sales orders,
the ODI batch data entry mapping, WMS stock and MIPS work orders. Every mapper
normalises the join keys (item numbers, order numbers, dates), so the engine never
sees source quirks such as an order number read as a float. The workbook reader and
the event feed (recon_agent/events) hand the mappers rows with the same column names,
so both produce identical records. In production the readers would be swapped for
API/middleware readers without changing anything downstream.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

import openpyxl

from . import config


def norm_sku(value) -> str:
    """Canonical Oracle item number: stripped string, no leading zeros, no '.0'."""
    if value is None:
        return ""
    s = str(value).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.lstrip("0") or ("0" if s else "")


def norm_ref(value) -> str:
    """An order number, forecast ID or work-order number as text ('' when empty)."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def as_date(value) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return None


def num(value) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def text(value) -> str:
    return "" if value is None else str(value).strip()


@dataclass
class Item:
    sku: str
    description: str
    uom: str
    item_type: str   # FG finished good, RM raw material


@dataclass
class SalesOrder:
    line_id: str
    order_nbr: str
    line: int
    order_type: str
    order_date: dt.date | None
    customer: str
    ship_to: str
    buss_code: str
    sku: str
    description: str
    uom: str
    qty: float
    requested_ship: dt.date | None
    sales_date: dt.date | None
    price: float

    @property
    def ref(self) -> tuple[str, int]:
        return self.order_nbr, self.line


@dataclass
class Forecast:
    forecast_id: str
    date: dt.date | None
    sku: str
    description: str
    plant: str
    qty: float


@dataclass
class OdiEntry:
    entry_id: str
    plant: str
    source_system: str
    ref: str                      # Sales Order Number / Forcast: an order number or a forecast ID
    ref_line: int                 # SO Line number (0 for a forecast)
    requested_ship: dt.date | None
    qty: float
    allocated: float
    wo: str
    wo_qty: float
    item: str                     # Item Number as ODI carries it ('' on lines served from stock)
    wrap_start: dt.datetime | None
    wrap_end: dt.datetime | None
    line: str
    shift: str
    oracle_type: str
    mips_description: str
    sched_batches: float
    act_batches: float
    batch_status: str
    sku: str = ""                 # resolved by the engine: the item of the demand line it maps

    @property
    def is_sales_order(self) -> bool:
        return self.source_system.upper() == "BUSS"


@dataclass
class WmsStock:
    inventory_id: str
    sku: str
    description: str
    on_hand: float
    available: float
    allocated: float
    item_type: str
    facility: str
    locator: str


@dataclass
class WorkOrder:
    wo: str
    production_date: dt.date | None
    sku: str
    buss_code: str
    description: str
    demand_qty: float             # Forecast / Order Quantity: the demand left that MIPS plans
    line: str
    shift: str
    errors: str
    wo_qty: float
    start: dt.date | None
    completion: dt.date | None
    status: str


@dataclass
class SourceData:
    items: dict[str, Item]
    sales_orders: list[SalesOrder]
    forecasts: list[Forecast]
    odi: list[OdiEntry]
    stock: list[WmsStock]
    work_orders: list[WorkOrder]
    files: dict[str, str]
    as_of: dt.datetime | None = None   # event feed: simulated time of the projection


# --- record mappers ----------------------------------------------------------
# Each takes rows keyed by the business team's column names, so the workbook and the
# event feed (recon_agent/events, same columns) produce identical records.

def items(records: list[dict]) -> dict[str, Item]:
    out = {}
    for r in records:
        sku = norm_sku(r.get("Item Number"))
        if sku:
            out[sku] = Item(sku, text(r.get("Item Description")), text(r.get("UOM")), text(r.get("Item Type")).upper())
    return out


def sales_orders(records: list[dict]) -> list[SalesOrder]:
    out = []
    for r in records:
        if r.get("Order Number") is None:
            continue
        order, line = norm_ref(r["Order Number"]), int(num(r.get("Order Line")) or 1)
        out.append(SalesOrder(
            line_id=text(r.get("SO Line ID")) or f"{order}-{line}", order_nbr=order, line=line,
            order_type=text(r.get("Order Type")), order_date=as_date(r.get("Order Date")),
            customer=text(r.get("Customer Name")), ship_to=text(r.get("Ship-To Location (USA)")),
            buss_code=norm_ref(r.get("FG BUSS Code")), sku=norm_sku(r.get("Item Number")),
            description=text(r.get("Item Description")), uom=text(r.get("UOM")), qty=num(r.get("Order Quantity")),
            requested_ship=as_date(r.get("Requested Ship Date")), sales_date=as_date(r.get("Sales Date")),
            price=num(r.get("Price"))))
    return out


def forecasts(records: list[dict]) -> list[Forecast]:
    return [Forecast(forecast_id=norm_ref(r["Forecast ID"]), date=as_date(r.get("Forecast Date")),
                     sku=norm_sku(r.get("Item Number")), description=text(r.get("Item Description")),
                     plant=text(r.get("Plant")), qty=num(r.get("Forecast / Required Quantity")))
            for r in records if r.get("Forecast ID") is not None]


def odi_entries(records: list[dict]) -> list[OdiEntry]:
    out = []
    for i, r in enumerate(records, 1):
        if r.get("Sales Order Number / Forcast") is None:
            continue
        out.append(OdiEntry(
            entry_id=text(r.get("ODI Entry ID")) or f"ODI-{i:04d}", plant=text(r.get("Plant Code")),
            source_system=text(r.get("Source System")), ref=norm_ref(r["Sales Order Number / Forcast"]),
            ref_line=int(num(r.get("SO Line number"))), requested_ship=as_date(r.get("Requested Ship Date")),
            qty=num(r.get("Quantity")), allocated=num(r.get("Allocated quantity for SO")),
            wo=norm_ref(r.get("Work Order Number")), wo_qty=num(r.get("Work Order Quantity")),
            item=norm_sku(r.get("Item Number")), wrap_start=r.get("Wrapping Start Date Time"),
            wrap_end=r.get("Wrapping End Date Time"), line=text(r.get("Prod Line")), shift=text(r.get("Shift")),
            oracle_type=text(r.get("Oracle Type")), mips_description=text(r.get("MIPS Description")),
            sched_batches=num(r.get("MIPS Sched Batches")), act_batches=num(r.get("Act Batches Mixed")),
            batch_status=text(r.get("Batch Status"))))
    return out


def wms_stock(records: list[dict]) -> list[WmsStock]:
    out = []
    for i, r in enumerate(records, 1):
        if r.get("Item Number") is None:
            continue
        out.append(WmsStock(
            inventory_id=text(r.get("Inventory ID")) or f"INV-{i:04d}", sku=norm_sku(r["Item Number"]),
            description=text(r.get("Item Description")), on_hand=num(r.get("On hand")),
            available=num(r.get("Available quantity")), allocated=num(r.get("Allocated quantity")),
            item_type=text(r.get("Item Type")).upper(), facility=text(r.get("Facility")),
            locator=text(r.get("Locator"))))
    return out


def work_orders(records: list[dict]) -> list[WorkOrder]:
    return [WorkOrder(
        wo=norm_ref(r["Work Order Number"]), production_date=as_date(r.get("Production Date")),
        sku=norm_sku(r.get("FG Oracle Ref")), buss_code=norm_ref(r.get("FG BUSS Code")),
        description=text(r.get("FG Description")), demand_qty=num(r.get("Forecast / Order Quantity")),
        line=text(r.get("Prod Line")), shift=text(r.get("Shift")), errors=text(r.get("Scheduling Errors")),
        wo_qty=num(r.get("Work Order Quantity")), start=as_date(r.get("Work order start date")),
        completion=as_date(r.get("Work Order Completion Date")), status=text(r.get("Work Order Status")))
        for r in records if r.get("Work Order Number") is not None]


def build(rows: dict[str, list[dict]], files: dict[str, str], as_of: dt.datetime | None = None) -> SourceData:
    """SourceData from rows per entity name (contract.ENTITIES keys)."""
    return SourceData(
        items=items(rows.get("oracle.item", [])),
        sales_orders=sales_orders(rows.get("buss.sales_order", [])),
        forecasts=forecasts(rows.get("oracle.forecast", [])),
        odi=odi_entries(rows.get("odi.batch_mapping", [])),
        stock=wms_stock(rows.get("wms.inventory", [])),
        work_orders=work_orders(rows.get("mips.work_order", [])),
        files=files, as_of=as_of)


# --- workbook reader -----------------------------------------------------------

def clean_header(value) -> str:
    """'Allocated quantity  for SO ' -> 'Allocated quantity for SO': the contract's column name."""
    return re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""


def sheet_rows(ws) -> list[dict]:
    """Rows below the header (the first row with three or more cells), keyed by cleaned header.

    The sheets differ in layout (Sales Orders starts in column B); empty columns are dropped."""
    rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
    idx = next((i for i, r in enumerate(rows) if sum(v is not None for v in r) >= 3), None)
    if idx is None:
        return []
    header = [clean_header(v) for v in rows[idx]]
    out = []
    for r in rows[idx + 1:]:
        if any(v is not None for v in r):
            out.append({h: (v.strip() if isinstance(v, str) else v) for h, v in zip(header, r) if h})
    return out


def find_workbook(data_dir: Path) -> Path:
    matches = sorted(Path(data_dir).glob(config.WORKBOOK_GLOB), key=lambda p: p.stat().st_mtime)
    if not matches:
        raise FileNotFoundError(f"No workbook matching '{config.WORKBOOK_GLOB}' in {data_dir}")
    return matches[-1]


def load_workbook(path: Path) -> SourceData:
    from .events import contract   # the sheet each entity is read from

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        names = {clean_header(n).lower(): n for n in wb.sheetnames}
        rows = {}
        for e in contract.ENTITIES.values():
            sheet = names.get(e.sheet.lower())
            rows[e.name] = sheet_rows(wb[sheet]) if sheet else []
    finally:
        wb.close()  # read-only mode keeps the file handle open until closed
    return build(rows, {e: f"{path.name} · {contract.ENTITIES[e].sheet} ({len(r)} rows)" for e, r in rows.items()})


def load_all(data_dir: Path | None = None) -> SourceData:
    return load_workbook(find_workbook(Path(data_dir or config.DATA_DIR)))
