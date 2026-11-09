"""Event contract v3: what a source-system change looks like on the event log.

Each event is one row-level change (insert, update or delete) in one source
entity, shaped like a GoldenGate CDC record: an operation, the row key, and full
before/after row images. Row images use the business team's own column names
from the 9 Nov mapping workbook (BIMBO_MIPS_WMS_ODI_Data, one sheet per entity;
see docs/DATA_MODEL_9NOV.md), with whitespace normalised, so ingest.py maps event
rows and workbook rows with the same code.

Where a sheet has no row identifier, the contract adds one synthetic key column
(marked below). Those are assumptions to confirm with Bimbo IT; see
docs/EVENT_CONTRACT.md.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

VERSION = 3
SOURCES = ("BUSS", "ORACLE", "MIPS", "ODI", "WMS")
OPS = ("I", "U", "D")   # GoldenGate op_type: insert, update, delete

# Column types. "number" accepts int or float.
STR, NUM, BOOL, DATE, DATETIME = "str", "number", "bool", "date", "datetime"


@dataclass(frozen=True)
class Entity:
    name: str
    source: str
    key: str                       # column holding the row key
    columns: dict[str, str]        # column -> type, in the workbook's column order
    required: tuple[str, ...]      # columns that must be non-empty
    synthetic_key: bool = False    # the key column is not in the workbook
    description: str = ""
    summary: tuple[str, ...] = ()  # columns that identify a row at a glance (lists, CLI, simulator)
    label: str = ""                # short name for screens
    sheet: str = ""                # the workbook sheet the entity is read from


ENTITIES: dict[str, Entity] = {e.name: e for e in (
    Entity(
        "buss.sales_order", "BUSS", "SO Line ID", synthetic_key=True, label="Sales orders",
        sheet="Sales Orders",
        summary=("Order Number", "Customer Name", "Item Number", "Order Quantity", "Requested Ship Date"),
        description="Customer order line received from BUSS (Sales Orders)",
        columns={
            "SO Line ID": STR, "Order Number": STR, "Order Line": NUM, "Order Type": STR, "Order Date": DATE,
            "Customer Name": STR, "Ship-To Location (USA)": STR, "FG BUSS Code": STR, "Item Number": STR,
            "Item Description": STR, "UOM": STR, "Order Quantity": NUM, "Requested Ship Date": DATE,
            "Sales Date": DATE, "Price": NUM,
        },
        required=("SO Line ID", "Order Number", "Item Number", "Order Quantity")),
    Entity(
        "oracle.forecast", "ORACLE", "Forecast ID", label="Forecast", sheet="Oracle Planning & Forecast",
        summary=("Forecast Date", "Item Number", "Item Description", "Plant", "Forecast / Required Quantity"),
        description="Expected demand planned in Oracle (Oracle Planning & Forecast)",
        columns={
            "Forecast ID": STR, "Forecast Date": DATE, "Item Number": STR, "Item Description": STR, "UOM": STR,
            "Item Type": STR, "Plant": STR, "Forecast / Required Quantity": NUM,
        },
        required=("Forecast ID", "Item Number", "Forecast / Required Quantity")),
    Entity(
        "oracle.item", "ORACLE", "Item Number", label="Item master", sheet="ITEM Master",
        summary=("Item Number", "Item Description", "UOM", "Item Type"),
        description="Product catalogue: finished goods (FG) and raw materials (RM) (ITEM Master)",
        columns={"Item Number": STR, "Item Description": STR, "UOM": STR, "Item Type": STR},
        required=("Item Number",)),
    Entity(
        "mips.work_order", "MIPS", "Work Order Number", label="Work orders", sheet="MIPS",
        summary=("FG Oracle Ref", "FG Description", "Forecast / Order Quantity", "Work Order Quantity",
                 "Prod Line", "Work Order Status"),
        description="Production work order planned in MIPS for the demand left after allocated stock (MIPS)",
        columns={
            "Production Date": DATE, "Work Order Number": STR, "FG Oracle Ref": STR, "FG BUSS Code": STR,
            "FG Description": STR, "Forecast / Order Quantity": NUM, "Prod Line": STR, "Shift": STR,
            "Scheduling Errors": STR, "Work Order Quantity": NUM, "Work order start date": DATE,
            "Work Order Completion Date": DATE, "Work Order Status": STR,
        },
        required=("Work Order Number", "FG Oracle Ref", "Work Order Quantity")),
    Entity(
        "odi.batch_mapping", "ODI", "ODI Entry ID", synthetic_key=True, label="Batch mapping",
        sheet="ODI Batch Data Entry Mapping",
        summary=("Source System", "Sales Order Number / Forcast", "Quantity", "Allocated quantity for SO",
                 "Work Order Number", "Work Order Quantity"),
        description="ODI's join of one demand line (BUSS order or Oracle forecast) to its work order and "
                    "MIPS batches (ODI Batch Data Entry Mapping)",
        columns={
            "ODI Entry ID": STR, "Plant Code": STR, "Schedule Date": DATE, "Source System": STR,
            "BUSS Sales / Forcast Date": DATE, "Product Sales Date": DATE, "Requested Ship Date": DATE,
            "Sales Order / Forcast Date": DATE, "Sales Order Number / Forcast": STR, "SO Line number": NUM,
            "Quantity": NUM, "Allocated quantity for SO": NUM, "Work Order Number": STR, "Work Order Date": DATE,
            "Work Order Quantity": NUM, "Item Number": STR, "Wrapping Start Date Time": DATETIME,
            "Wrapping End Date Time": DATETIME, "Plant Name": STR, "Prod Line": STR, "Shift": STR,
            "Oracle Type": STR, "Oracle Formula": STR, "MIPS Description": STR, "MIPS Sched Batches": NUM,
            "Act Batches Mixed": NUM, "Batch Status": STR,
        },
        required=("ODI Entry ID", "Source System", "Sales Order Number / Forcast", "Quantity")),
    Entity(
        "wms.inventory", "WMS", "Inventory ID", synthetic_key=True, label="Inventory", sheet="WMS",
        summary=("Item Number", "Item Description", "On hand", "Available quantity", "Allocated quantity",
                 "Locator"),
        description="Warehouse stock for one item and locator; all on-hand stock is kept in WMS only (WMS)",
        columns={
            "Inventory ID": STR, "Item Number": STR, "Item Description": STR, "On hand": NUM,
            "Available quantity": NUM, "Allocated quantity": NUM, "UOM": STR, "Item Type": STR, "Facility": STR,
            "Company / Inventory Organization": STR, "Locator": STR,
        },
        required=("Inventory ID", "Item Number", "On hand")),
)}


class ContractError(ValueError):
    pass


def entity(name: str) -> Entity:
    try:
        return ENTITIES[name]
    except KeyError:
        raise ContractError(f"Unknown entity '{name}'. Known: {', '.join(ENTITIES)}") from None


# --- values ------------------------------------------------------------------

def coerce(value, kind: str):
    """Python value of `kind` from a JSON value or a command-line string. Empty -> None."""
    if value is None or value == "" and kind != STR:
        return None
    if kind == STR:
        return value if isinstance(value, str) else str(value)
    if kind == NUM:
        if isinstance(value, bool):
            raise ContractError(f"expected a number, got {value!r}")
        n = value if isinstance(value, (int, float)) else float(value)
        return int(n) if float(n).is_integer() else float(n)
    if kind == BOOL:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "t", "yes", "y", "1"):
            return True
        if text in ("false", "f", "no", "n", "0"):
            return False
        raise ContractError(f"expected true/false, got {value!r}")
    if kind == DATE:
        if isinstance(value, dt.datetime):
            return value.date()
        if isinstance(value, dt.date):
            return value
        return dt.date.fromisoformat(str(value)[:10])
    if kind == DATETIME:
        if isinstance(value, dt.datetime):
            return value
        if isinstance(value, dt.date):
            return dt.datetime.combine(value, dt.time())
        return dt.datetime.fromisoformat(str(value))
    raise ContractError(f"unknown column type {kind}")


def to_json(value):
    if isinstance(value, dt.datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def validate(e: Entity, row: dict) -> dict:
    """A full, typed row image: unknown columns rejected, missing ones None, required ones present."""
    unknown = set(row) - set(e.columns)
    if unknown:
        raise ContractError(f"{e.name}: unknown column(s) {', '.join(sorted(unknown))}")
    out = {}
    for col, kind in e.columns.items():
        try:
            out[col] = coerce(row.get(col), kind)
        except (ValueError, TypeError) as err:
            raise ContractError(f"{e.name}.{col}: {err}") from None
    missing = [c for c in e.required if out[c] is None or out[c] == ""]
    if missing:
        raise ContractError(f"{e.name}: required column(s) empty: {', '.join(missing)}")
    return out


def encode(row: dict | None) -> dict | None:
    return None if row is None else {k: to_json(v) for k, v in row.items()}


def decode(e: Entity, row: dict | None) -> dict | None:
    """Typed row from its JSON image. Dates come back as datetimes, as openpyxl reads them."""
    if row is None:
        return None
    out = {}
    for col, kind in e.columns.items():
        v = coerce(row.get(col), kind)
        out[col] = dt.datetime.combine(v, dt.time()) if kind == DATE and v is not None else v
    return out
