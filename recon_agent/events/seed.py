"""Synthetic starting dataset: one small sales day, published as insert events.

All values are invented. Only the shape follows the client extracts (same
columns, same conventions: BUSS pads the FG Oracle Ref with a trailing space,
moved lines keep the schedule date but carry the prior offset sales date, ODI
runs share an OracleTypeCode per formula, a moved run leaves a zero copy with
no shift). SKU numbers are in the 9001xx range and every description starts
with "SIM", so nothing here can be mistaken for plant data.

Each SKU is built to show one known scenario (EXPECTED lists the flags), and two
SKUs reconcile cleanly as controls. The clock starts the evening before the
sales day: production is wrapped, WMS is allocating and loading trailers.
"""
from __future__ import annotations

import datetime as dt

from .clock import SimClock
from .derive import derive
from .log import Change, EventLog

DEFAULT_SALES_DATE = dt.date(2026, 11, 7)   # a Saturday, like the client's sample day
PLANT_CODE, PLANT_NAME = "UBD", "LEHIGH VALLEY PA BAKERY"
ODI_PLANT = "BREINIGSVILLE (LEHIGH VALLEY) PA BAKERY (UBD)"
SCENARIO = "seed"

# sku: (BUSS code, description, retail price, units per container)
ITEMS = {
    "900101": ("009101", "SIM 100% WW BREAD 24Z", 4.69, 10),
    "900102": ("009102", "SIM POTATO BREAD 22Z", 4.69, 10),
    "900103": ("009103", "SIM WHITE ROUND TOP 20Z", 3.99, 10),
    "900104": ("009104", "SIM WHITE ROUND TOP 2P 40Z", 5.49, 6),
    "900105": ("009105", "SIM SQUARE WHEAT 24Z", 4.29, 10),
    "900106": ("009106", "SIM HOT DOG BUNS 8P 12Z", 3.59, 12),
    "900107": ("009107", "SIM HAMBURGER BUNS 8P 12Z", 3.59, 12),
    "900108": ("009108", "SIM HONEY WHEAT 20Z", 4.49, 10),
    "900109": ("009109", "SIM ITALIAN BREAD 20Z", 3.99, 10),
    "900110": ("009110", "SIM MULTIGRAIN 24Z", 4.79, 10),
    "900111": ("009111", "SIM BUTTER TOP 20Z", 3.99, 10),
    "900112": ("009112", "SIM CINNAMON RAISIN 16Z", 5.29, 12),
}

# Flags the seed must raise (flag IDs, as the engine builds them). The seed test checks these.
EXPECTED = {
    "R01-900101": "scenario 1: inventory applied to the first of three order lines",
    "R05-900102": "scenario 4: recovery run over-built",
    "R02-900103": "scenario 2: inventory netted with no beginning inventory recorded",
    "R03-SU900130-900104": "scenario 2: zero-quantity (blind) line in a live run",
    "R04-SU900150-900105": "scenario 3: run moved from Line 1 to Line 2, ghost left behind",
    "R07-900106": "scenario 7: WMS filled from stock MIPS cannot see (freezer)",
    "R09-900107": "scenario 6: WMS order raised after MIPS last synced",
    "R10-900107": "scenario 5: WMS allocation short of the order",
    "R06-900108": "surplus not carried to the next sales day",
    "R11-900109": "units-per-container master data differs (MIPS 10, WMS 12)",
    "R08-900110": "negative net order: inventory exceeds demand",
    "R12-PLANT": "scenario 8: order shipped with no trailer load scan",
    "R14-SU900121-Line 2": "recovery batch mixed above schedule",
    "R15-MIPS-FG-REF": "BUSS pads the FG Oracle Ref, as in the client extract",
}
CLEAN_SKUS = ("900111", "900112")

CUSTOMERS = {   # name: (order number, order type, route)
    "SIM DC NORTH": ("9900001", "TRANSFER_ORDER", None),
    "SIM DC SOUTH": ("9900002", "TRANSFER_ORDER", None),
    "SIM DSD ROUTE 12": ("9900003", "DSD_SALES_ORDER", "3770012"),
    "SIM DSD ROUTE 27": ("9900004", "DSD_SALES_ORDER", "3770027"),
}


def start_time(sales_date: dt.date) -> dt.datetime:
    return dt.datetime.combine(sales_date - dt.timedelta(days=1), dt.time(20, 0))


def _at(d: dt.date, hh: int, mm: int = 0) -> dt.datetime:
    return dt.datetime.combine(d, dt.time(hh, mm))


def build(sales_date: dt.date = DEFAULT_SALES_DATE) -> list[Change]:
    S = sales_date
    P = S - dt.timedelta(days=1)            # prior sales day (moved lines land here)
    D3, D2 = S - dt.timedelta(days=3), S - dt.timedelta(days=2)   # production days
    plan_ts, order_ts = _at(S - dt.timedelta(days=4), 15, 32), _at(P - dt.timedelta(days=1), 20, 31)
    changes: list[Change] = []

    # --- MIPS schedule lines ------------------------------------------------
    n = 0

    def line(sku: str, qty: int, source: str, moved: bool = False, upc: int | None = None) -> None:
        nonlocal n
        n += 1
        buss, desc, price, item_upc = ITEMS[sku]
        sales = P if moved else S
        is_buss = source == "BUSS"
        ts = order_ts if is_buss else plan_ts
        changes.append(Change("mips.order_line", "I", row={
            "Order Line ID": f"ML-{n:04d}", "Plant Code": PLANT_CODE, "Plant Name": PLANT_NAME,
            "Schedule Date": S, "Offset Sales Date": sales, "BUSS Sales Date": sales, "Order Moved": moved,
            "Product Sales Date": sales, "Requested Ship Date": sales if is_buss else sales - dt.timedelta(days=1),
            "Production Date": None if is_buss else sales - dt.timedelta(days=1),
            "FG Oracle Ref": f"{sku} " if is_buss else sku, "FG BUSS Code": buss, "FG Description": desc,
            "Order Quantity": qty, "Container Code": "01", "Units per Container": upc or item_upc,
            "Units Per Carton": 0, "Containers per Rack": 17 if item_upc == 10 else 21, "Retail Price": price,
            "Closed": "F", "Source System": "BUSS" if is_buss else "OCloud Region2",
            "Last Modify Date Time": ts, "Scheduling Errors": ""}, source_ts=ts, scenario=SCENARIO))

    line("900101", 1300, "BUSS", moved=True)     # listed first: MIPS applies inventory here only
    line("900101", 2400, "BUSS")
    line("900101", 7600, "OCloud")
    line("900102", 1000, "BUSS")
    line("900102", 5000, "OCloud")
    line("900103", 5000, "OCloud")
    line("900104", 0, "OCloud")
    line("900105", 3000, "BUSS")
    line("900106", 4000, "OCloud")
    line("900107", 2000, "BUSS")
    line("900107", 1500, "OCloud")
    line("900108", 2500, "OCloud")
    line("900109", 1800, "OCloud")
    line("900110", 1200, "OCloud")
    line("900111", 1500, "BUSS")
    line("900111", 2500, "OCloud")
    line("900112", 900, "BUSS")

    # --- ODI finished-good runs ---------------------------------------------
    n = 0

    def run(sku: str, formula: str, line_code: str, shift: str | None, start: dt.datetime, hours: float,
            ordered: int, wrapped: int, mips_order_qty: int = 0, beginning_inventory: int = 0) -> None:
        nonlocal n
        n += 1
        buss, desc, _, _ = ITEMS[sku]
        end = start + dt.timedelta(hours=hours)
        changes.append(Change("odi.finished_good", "I", row=derive("odi.finished_good", {
            "FGEntryID": f"FG-{n:04d}", "PlantName": ODI_PLANT, "SalesDate": S,
            "ProductionStartDateTime": start, "ProductionEndDateTime": end, "LineCode": line_code,
            "ShiftName": shift, "OracleTypeCode": formula, "LegacyFGNumber": buss, "OracleFGNumber": sku,
            "OCloudFGItemStructure": "Primary", "OracleFGDescription": desc, "NetUnitsOrdered": ordered,
            "ActualWrappedUnits": wrapped,
            "MIPSBeginningInventoryQty": beginning_inventory, "MIPSInventoryAdjustmentQty": 0,
            "MIPSOrderQty": mips_order_qty, "MIPSOrderAdjustmentQty": 0,
            "ActualDamagedWeightWithWrap": 0, "ActualDamagedUnitsWithWrap": 0,
            "ActualDamagedWeightWithoutWrap": 0, "ActualDamagedUnitsWithoutWrap": 0,
            "TheoreticalRunTime": round(hours * 60, 1), "OraclePiecesPerPkg": 2 if "2P" in desc else 1}),
            source_ts=end, scenario=SCENARIO))

    run("900101", "SU900110", "Line 1", "Shift 1", _at(D3, 6), 3.5, 9000, 9000, mips_order_qty=3700)
    run("900102", "SU900120", "Line 1", "Shift 1", _at(D3, 9, 30), 2.5, 6000, 5200, mips_order_qty=1000)
    run("900102", "SU900121", "Line 2", "Shift 3", _at(D2, 1), 1.0, 2000, 2000)   # recovery run
    run("900103", "SU900130", "Line 1", "Shift 2", _at(D3, 14), 2.0, 4200, 4200)
    run("900104", "SU900130", "Line 1", "Shift 2", _at(D3, 14), 2.0, 0, 0)       # blind line
    run("900105", "SU900150", "Line 1", None, _at(D3, 18), 0.0, 0, 0)            # ghost of the moved run
    run("900105", "SU900150A", "Line 2", "Shift 1", _at(D2, 6), 1.5, 3000, 3000, mips_order_qty=3000)
    run("900106", "SU900160", "Line 2", "Shift 2", _at(D3, 15), 2.0, 4000, 3300)
    run("900107", "SU900160", "Line 2", "Shift 2", _at(D3, 15), 2.0, 3500, 3500, mips_order_qty=2000)
    run("900108", "SU900180", "Line 1", "Shift 3", _at(D2, 0), 1.5, 2500, 2900)
    run("900109", "SU900190", "Line 2", "Shift 3", _at(D2, 2, 30), 1.0, 1800, 1800)
    run("900110", "SU900200", "Line 1", "Shift 3", _at(D2, 3), 0.0, -300, 0, beginning_inventory=1500)
    run("900111", "SU900210", "Line 2", "Shift 1", _at(D2, 8), 2.0, 4000, 4000, mips_order_qty=1500)
    run("900112", "SU900220", "Line 2", "Shift 1", _at(D2, 10), 1.0, 900, 900, mips_order_qty=900)

    # --- ODI batches ----------------------------------------------------------
    n = 0

    def batch(formula: str, desc: str, line_code: str, shift: str, start: dt.datetime, sched: int, mixed: int,
              pieces_per_batch: int, variance: float) -> None:
        nonlocal n
        n += 1
        theo = pieces_per_batch * mixed
        changes.append(Change("odi.batch", "I", row=derive("odi.batch", {
            "BatchEntryID": f"BT-{n:04d}", "PlantName": ODI_PLANT, "SalesDate": S,
            "WrappingStartDateTime": start, "WrappingEndDateTime": start + dt.timedelta(hours=2),
            "Prod Line": line_code, "Shift": shift, "Oracle Type": formula, "Oracle Formula": f"PH{formula[2:]}",
            "MIPS Description": desc, "MIPS Sched Batches": sched, "Scrap Dough Weight": 0,
            "Act Batches Mixed": mixed, "Total Raw Ingr Adjust Weight": 0, "Total Other Batch Weight": 0,
            "Total Baked Ingr Weight": 0, "Total Remix Batch Weight": 0, "Tot Batch Pieces Counted Qty": 0,
            "Actual Damaged Weight w/o Wrap Weight": 0, "Theo Batch Pieces Qty": theo, "Batch Piece Var Qty": 0,
            "Batch Piece Var %": 0, "FG & FG Scrap Pieces Qty": 0, "Final Piece Variance Qty": variance}),
            source_ts=start + dt.timedelta(hours=2), scenario=SCENARIO))

    batch("SU900110", "SIM 100% Whole Wheat", "Line 1", "Shift 1", _at(D3, 6), 12, 12, 750, -12.4)
    batch("SU900120", "SIM Potato 22 oz", "Line 1", "Shift 1", _at(D3, 9, 30), 8, 8, 750, 6.1)
    batch("SU900121", "SIM Potato 22 oz recovery", "Line 2", "Shift 3", _at(D2, 1), 3, 4, 500, 2.0)
    batch("SU900130", "SIM White Round Top", "Line 1", "Shift 2", _at(D3, 14), 6, 6, 700, -4.3)
    batch("SU900160", "SIM Buns 12 oz", "Line 2", "Shift 2", _at(D3, 15), 9, 9, 820, 9.8)
    batch("SU900180", "SIM Honey Wheat", "Line 1", "Shift 3", _at(D2, 0), 4, 4, 725, -3.5)

    # --- WMS order lines by item and customer ---------------------------------
    n = 0
    item_lines: list[tuple[str, int, int]] = []   # (customer, ordered, allocated) for the order headers

    def wms(sku: str, customer: str, ordered: int, allocated: int | None = None, original: int | None = None,
            upc: int | None = None) -> None:
        nonlocal n
        n += 1
        _, desc, _, item_upc = ITEMS[sku]
        upc = upc or item_upc
        allocated = ordered if allocated is None else allocated
        changes.append(Change("wms.item_order", "I", row=derive("wms.item_order", {
            "Item Line ID": f"WL-{n:04d}", "Sales Date": S, "Item Nbr": sku, "Units per Container": upc,
            "Description": desc, "Cust Name": customer, "Original Ord Qty": original or ordered,
            "Order Qty": ordered, "Order Units Qty": 0, "Allocated Units Qty": allocated}),
            source_ts=_at(P, 14), scenario=SCENARIO))
        item_lines.append((customer, ordered, allocated))

    wms("900101", "SIM DC NORTH", 5000)
    wms("900101", "SIM DC SOUTH", 3000)
    wms("900101", "SIM DSD ROUTE 12", 2000)
    wms("900102", "SIM DC NORTH", 3000)
    wms("900102", "SIM DC SOUTH", 2000)
    wms("900102", "SIM DSD ROUTE 27", 1000)
    wms("900103", "SIM DC NORTH", 2500)
    wms("900103", "SIM DC SOUTH", 2500)
    wms("900105", "SIM DSD ROUTE 12", 1800)
    wms("900105", "SIM DSD ROUTE 27", 1200)
    wms("900106", "SIM DC NORTH", 2400)
    wms("900106", "SIM DC SOUTH", 1600)
    wms("900107", "SIM DC NORTH", 1500)
    wms("900107", "SIM DSD ROUTE 12", 1200)
    wms("900107", "SIM DSD ROUTE 27", 1000, allocated=800, original=800)   # raised after MIPS synced
    wms("900108", "SIM DC SOUTH", 1500)
    wms("900108", "SIM DSD ROUTE 12", 1000)
    wms("900109", "SIM DC NORTH", 1800, upc=12)
    wms("900110", "SIM DC SOUTH", 1200)
    wms("900111", "SIM DC NORTH", 2000)
    wms("900111", "SIM DSD ROUTE 12", 1000)
    wms("900111", "SIM DSD ROUTE 27", 1000)
    wms("900112", "SIM DSD ROUTE 12", 900)

    # --- WMS order headers (Fill Rate) ------------------------------------------
    # North is loaded and shipped, Route 12 shipped with no load scan (R12),
    # South and Route 27 are packed and waiting for their trailers.
    shipped = {"SIM DC NORTH": True, "SIM DSD ROUTE 12": False}
    for customer, (order_nbr, order_type, route) in CUSTOMERS.items():
        ordered = sum(o for c, o, _ in item_lines if c == customer)
        allocated = sum(a for c, _, a in item_lines if c == customer)
        ship = customer in shipped
        loaded = allocated if shipped.get(customer) else 0
        shipped_qty = allocated if ship else 0
        changes.append(Change("wms.order", "I", row=derive("wms.order", {
            "Facility": PLANT_CODE, "Order Nbr": order_nbr, "Ship to Name": customer, "Route Nbr": route,
            "Order Type": order_type, "Sales Date": S, "Order Qty": ordered, "Allocated Qty": allocated,
            "Packed Qty": allocated, "Loaded Qty": loaded, "Shipped Qty": shipped_qty}),
            source_ts=_at(P, 19 if ship else 16), scenario=SCENARIO))

    # --- v2: WMS receipt tickets, their receipt transactions, Oracle postings -------
    # One ticket per run that wrapped product, received 30 minutes after the run ended;
    # Oracle posts each receipt 2 minutes later. Nothing is late or returned yet.
    runs = [c.row for c in changes if c.entity == "odi.finished_good" and c.row["ActualWrappedUnits"] > 0]
    for i, r in enumerate(runs, 1):
        sku, qty = r["OracleFGNumber"], r["ActualWrappedUnits"]
        received = r["ProductionEndDateTime"] + dt.timedelta(minutes=30)
        ticket, txn = f"TK-{i:04d}", f"WT-{i:04d}"
        lot = f"L{received:%y%m%d}-{r['OracleTypeCode'][-4:]}"
        changes.append(Change("wms.ticket", "I", row=derive("wms.ticket", {
            "Ticket Nbr": ticket, "Sales Date": S, "Item Nbr": sku, "Description": ITEMS[sku][1], "Lot Nbr": lot,
            "Run Code": r["OracleTypeCode"], "Location": "DOCK", "Received Date Time": received,
            "Received Qty": qty, "Returned Qty": 0, "Status": "OPEN"}), source_ts=received, scenario=SCENARIO))
        changes.append(Change("wms.transaction", "I", row={
            "Txn Nbr": txn, "Txn Type": "RECEIPT", "Txn Date Time": received, "Item Nbr": sku, "Qty": qty,
            "Ticket Nbr": ticket, "Lot Nbr": lot, "User": "SIM RECEIVING"}, source_ts=received, scenario=SCENARIO))
        posted = received + dt.timedelta(minutes=2)
        changes.append(Change("oracle.inventory_txn", "I", row={
            "TransactionId": f"OT-{i:04d}", "SourceReference": txn, "TransactionTypeName": "WMS Receipt",
            "ItemNumber": sku, "TransactionQuantity": qty, "TransactionUnitOfMeasure": "Each",
            "Subinventory": "FG", "LotNumber": lot, "TransactionDate": received, "PostedDate": posted,
            "ProcessStatus": "POSTED"}, source_ts=posted, scenario=SCENARIO))

    changes.sort(key=lambda c: c.source_ts)   # publish in the order things happened at the plant
    return changes


def seed(log: EventLog, sales_date: dt.date = DEFAULT_SALES_DATE) -> int:
    """Publish the starting dataset into an empty log and start the clock. Returns the event count.

    The seed bypasses injected faults: it is the starting state, not traffic.
    """
    if log.source_position()[1]:
        raise ValueError("The event log is not empty. Reset it first (events seed --reset).")
    changes = build(sales_date)
    log.publish_many(changes, producer="seed", now=start_time(sales_date), faults=False)
    SimClock(log).set(start_time(sales_date))
    return len(changes)
