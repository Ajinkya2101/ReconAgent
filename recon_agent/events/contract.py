"""Event contract v2: what a source-system change looks like on the event log.

Each event is one row-level change (insert, update or delete) in one source
entity, shaped like a GoldenGate CDC record: an operation, the row key, and full
before/after row images. Row images use the client's own extract column names
(MIPS Order Data, ODI Finished Good / Batch Data, WMS Fill Rate and Order
Summary by Item), so ingest.py maps event rows and Excel rows with the same code.

Where an extract has no row identifier, the contract adds one synthetic key
column (marked below). Those are assumptions to confirm with Bimbo IT; see
docs/EVENT_CONTRACT.md. v2 adds the real-time detail no extract carries: WMS
receipt tickets and transactions, and Oracle Fusion inventory postings (a fourth
source, ORACLE), which R18 and R19 need.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

VERSION = 2
SOURCES = ("MIPS", "ODI", "WMS", "ORACLE")
OPS = ("I", "U", "D")   # GoldenGate op_type: insert, update, delete

# Column types. "number" accepts int or float.
STR, NUM, BOOL, DATE, DATETIME = "str", "number", "bool", "date", "datetime"


@dataclass(frozen=True)
class Entity:
    name: str
    source: str
    key: str                       # column holding the row key
    columns: dict[str, str]        # column -> type, in the client's extract order
    required: tuple[str, ...]      # columns that must be non-empty
    synthetic_key: bool = False    # the key column is not in the client extract
    description: str = ""
    summary: tuple[str, ...] = ()  # columns that identify a row at a glance (lists, CLI, simulator)
    label: str = ""                # short name for screens


ENTITIES: dict[str, Entity] = {e.name: e for e in (
    Entity(
        "mips.order_line", "MIPS", "Order Line ID", synthetic_key=True, label="Order lines",
        summary=("FG Oracle Ref", "FG Description", "Offset Sales Date", "Order Moved", "Source System",
                 "Order Quantity"),
        description="MIPS schedule line: a BUSS sales order or an OCloud supply-plan line (MIPS Order Data)",
        columns={
            "Order Line ID": STR, "Plant Code": STR, "Plant Name": STR, "Schedule Date": DATE,
            "Offset Sales Date": DATE, "BUSS Sales Date": DATE, "Order Moved": BOOL,
            "Product Sales Date": DATE, "Requested Ship Date": DATE, "Production Date": DATE,
            "FG Oracle Ref": STR, "FG BUSS Code": STR, "FG Description": STR, "Order Quantity": NUM,
            "Container Code": STR, "Units per Container": NUM, "Units Per Carton": NUM,
            "Containers per Rack": NUM, "Retail Price": NUM, "Closed": STR, "Source System": STR,
            "Last Modify Date Time": DATETIME, "Scheduling Errors": STR,
        },
        required=("Order Line ID", "FG Oracle Ref", "Schedule Date", "Offset Sales Date", "Order Quantity",
                  "Source System")),
    Entity(
        "odi.finished_good", "ODI", "FGEntryID", synthetic_key=True, label="Finished goods",
        summary=("OracleFGNumber", "OracleTypeCode", "LineCode", "ShiftName", "NetUnitsOrdered",
                 "ActualWrappedUnits", "MIPSBeginningInventoryQty"),
        description="ODI / Oracle production result for one SKU in one run (ODI Finished Good Data)",
        columns={
            "FGEntryID": STR, "PlantName": STR, "SalesDate": DATE, "ProductionStartDateTime": DATETIME,
            "ProductionEndDateTime": DATETIME, "LineCode": STR, "ShiftName": STR, "OracleTypeCode": STR,
            "LegacyFGNumber": STR, "OracleFGNumber": STR, "OCloudFGItemStructure": STR,
            "OracleFGDescription": STR, "NetUnitsOrdered": NUM, "ActualWrappedUnits": NUM,
            "ExpectedEndingInventoryQty": NUM, "MIPSBeginningInventoryQty": NUM,
            "MIPSInventoryAdjustmentQty": NUM, "MIPSOrderQty": NUM, "MIPSOrderAdjustmentQty": NUM,
            "ActualDamagedWeightWithWrap": NUM, "ActualDamagedUnitsWithWrap": NUM,
            "ActualDamagedWeightWithoutWrap": NUM, "ActualDamagedUnitsWithoutWrap": NUM,
            "TotalActualDamagedUnits": NUM, "TheoreticalRunTime": NUM, "TotalPieces": NUM,
            "OraclePiecesPerPkg": NUM,
        },
        required=("FGEntryID", "SalesDate", "LineCode", "OracleTypeCode", "OracleFGNumber")),
    Entity(
        "odi.batch", "ODI", "BatchEntryID", synthetic_key=True, label="Batches",
        summary=("Oracle Type", "MIPS Description", "Prod Line", "Shift", "MIPS Sched Batches", "Act Batches Mixed"),
        description="ODI batch (mixing) record for one formula run (ODI Batch Data)",
        columns={
            "BatchEntryID": STR, "PlantName": STR, "SalesDate": DATE, "WrappingStartDateTime": DATETIME,
            "WrappingEndDateTime": DATETIME, "Prod Line": STR, "Shift": STR, "Oracle Type": STR,
            "Oracle Formula": STR, "MIPS Description": STR, "MIPS Sched Batches": NUM,
            "Scrap Dough Weight": NUM, "Act Batches Mixed": NUM, "Total Raw Ingr Adjust Weight": NUM,
            "Total Other Batch Weight": NUM, "Total Baked Ingr Weight": NUM, "Total Remix Batch Weight": NUM,
            "Tot Batch Pieces Counted Qty": NUM, "Actual Damaged Weight w/o Wrap Weight": NUM,
            "Theo Batch Pieces Qty": NUM, "Batch Piece Var Qty": NUM, "Batch Piece Var %": NUM,
            "FG & FG Scrap Pieces Qty": NUM, "Final Piece Variance Qty": NUM, "Final Piece Variance %": NUM,
        },
        required=("BatchEntryID", "SalesDate", "Prod Line", "Oracle Type")),
    Entity(
        "wms.order", "WMS", "Order Nbr", label="Orders",
        summary=("Ship to Name", "Order Type", "Order Qty", "Allocated Qty", "Loaded Qty", "Shipped Qty"),
        description="WMS customer order header with fill and shipping progress (WMS Fill Rate Report)",
        columns={
            "Facility": STR, "Order Nbr": STR, "Ship to Name": STR, "Progress": STR, "Route Nbr": STR,
            "Order Type": STR, "Sales Date": DATE, "Order Qty": NUM, "Allocated Qty": NUM, "Packed Qty": NUM,
            "Loaded Qty": NUM, "Shipped Qty": NUM, "Short Qty": NUM, "% Allocated": NUM, "% Loaded": NUM,
            "% Shipped": NUM,
        },
        required=("Order Nbr", "Order Type", "Order Qty")),
    Entity(
        "wms.item_order", "WMS", "Item Line ID", synthetic_key=True, label="Item lines",
        summary=("Item Nbr", "Description", "Cust Name", "Units per Container", "Order Qty", "Allocated Units Qty"),
        description="WMS order quantity and allocation for one item and customer "
                    "(WMS Order Summary Report by Item, one row per customer line)",
        columns={
            "Item Line ID": STR, "Sales Date": DATE, "Item Nbr": STR, "Units per Container": NUM,
            "Description": STR, "Cust Name": STR, "Original Ord Qty": NUM, "Order Qty": NUM,
            "Order Stack Qty": NUM, "Order Tray Qty": NUM, "Order Units Qty": NUM, "Allocated Stacks": NUM,
            "Allocated Trays": NUM, "Allocated Units Qty": NUM, "Stacks Remaining": NUM,
            "Trays Remaining": NUM, "Completion%": STR,
        },
        required=("Item Line ID", "Item Nbr", "Cust Name", "Order Qty")),
    # --- v2: real-time detail the daily extracts do not carry (R18, R19, returns term) ----
    Entity(
        "wms.ticket", "WMS", "Ticket Nbr", label="Tickets",
        summary=("Item Nbr", "Run Code", "Location", "Received Qty", "Returned Qty", "Net Received Qty"),
        description="WMS receipt ticket: finished goods received from a plant run into the warehouse or freezer",
        columns={
            "Ticket Nbr": STR, "Sales Date": DATE, "Item Nbr": STR, "Description": STR, "Lot Nbr": STR,
            "Run Code": STR, "Location": STR, "Received Date Time": DATETIME, "Received Qty": NUM,
            "Returned Qty": NUM, "Net Received Qty": NUM, "Status": STR,
        },
        required=("Ticket Nbr", "Item Nbr", "Received Qty")),
    Entity(
        "wms.transaction", "WMS", "Txn Nbr", label="Transactions",
        summary=("Txn Type", "Item Nbr", "Qty", "Ticket Nbr", "Order Nbr", "Txn Date Time"),
        description="WMS inventory transaction (receipt, ship, return to plant, freezer move) that Oracle must post",
        columns={
            "Txn Nbr": STR, "Txn Type": STR, "Txn Date Time": DATETIME, "Item Nbr": STR, "Qty": NUM,
            "Ticket Nbr": STR, "Order Nbr": STR, "Lot Nbr": STR, "Reason Code": STR, "User": STR,
        },
        required=("Txn Nbr", "Txn Type", "Txn Date Time", "Item Nbr", "Qty")),
    Entity(
        "oracle.inventory_txn", "ORACLE", "TransactionId", label="Inventory postings",
        summary=("SourceReference", "TransactionTypeName", "ItemNumber", "TransactionQuantity", "ProcessStatus",
                 "PostedDate"),
        description="Oracle Fusion inventory transaction posted from a WMS transaction (SourceReference = Txn Nbr)",
        columns={
            "TransactionId": STR, "SourceReference": STR, "TransactionTypeName": STR, "ItemNumber": STR,
            "TransactionQuantity": NUM, "TransactionUnitOfMeasure": STR, "Subinventory": STR, "LotNumber": STR,
            "TransactionDate": DATETIME, "PostedDate": DATETIME, "ProcessStatus": STR, "ErrorMessage": STR,
        },
        required=("TransactionId", "SourceReference", "ItemNumber", "TransactionQuantity", "ProcessStatus")),
)}

# WMS transaction types, and the ones that change what the plant can count as usable supply.
TXN_TYPES = ("RECEIPT", "SHIP", "RETURN_TO_PLANT", "FREEZER_IN", "FREEZER_OUT", "ADJUST")
POSTING_STATUSES = ("POSTED", "PENDING", "ERROR")


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
