"""Starting dataset: one sales day at plant UBD, published as insert events.

Built from the business team's 9 Nov mapping workbook, as revised on 10 Nov
(docs/data-model/DATA_MODEL.md):

- 76108 BP WHITE HOT DOG BUNS 8P 14Z is the workbook's own example, value for value:
  11 BUSS orders (14,339) at $4.69, forecast FC001 (41,769), 473 allocated and 7,000
  available in WMS, work order WO-UBD-20260912-001 for 48,635. It reconciles: the control SKU.
- 950513 and 76105 take their forecast (FC002, FC004) from the workbook; their orders,
  stock, work orders and prices are invented in the same shape, each with known mismatches
  (EXPECTED lists the flags). 76105 is priced like 76108 (same white-bun family, $4.69);
  950513 (butter) at $4.99.

The workbook's own inconsistencies (docs/data-model/Data_Inconsistencies_9Nov.pdf: ODI ship dates a day
or two before BUSS's, wrapping before the work order dates, and so on) are kept as they are:
the seed mirrors the source data, it does not correct it.

The item master and all ten forecasts are loaded as in the workbook. An item that has
only a forecast is planned in Oracle, not yet scheduled, and gets no position.

Dates follow the sales date S (Saturday 12 Sep 2026, as in the workbook): orders and
forecasts are for S, ODI requested ship dates are S-1 or S-2, and 76108 wrapped on S-3.
The clock starts the evening before the sales day.

History for the ML layer (recon_agent/anomaly.py): the same three SKUs on the HISTORY_DAYS
earlier Saturdays, in the same tables: each customer's orders, the forecast, a completed
work order and its ODI wrap row, with ordinary week-to-week variation (a fixed random seed).
Reconciliation only reads day S, so the history raises no rule flags; today's values sit
inside their history, so the seed raises no ML flags either. WMS rows carry no date: their
history is the row's own change log, one stock count a day for the days before S.
"""
from __future__ import annotations

import datetime as dt

from .clock import SimClock
from .derive import derive
from .log import Change, EventLog

DEFAULT_SALES_DATE = dt.date(2026, 9, 12)   # Saturday, the workbook's sales date
PLANT_CODE = "UBD"
PLANT_NAME = "BREINIGSVILLE (LEHIGH VALLEY) PA BAKERY (UBD)"
SCENARIO = "seed"

# The workbook's ITEM Master, in its order: item number -> (description, UOM, type).
ITEM_MASTER = {
    "76108": ("BP WHITE HOT DOG BUNS 8P 14Z", "Units", "FG"),
    "950513": ("BP BUTTER HOT DOG BUNS 8P 15Z", "Units", "FG"),
    "967517": ("ART BRI HD BN BAG 8P 16Z", "Units", "FG"),
    "76105": ("BP WHITE HAMBURGER BUNS 8P 15Z", "Units", "FG"),
    "950514": ("BP BUTTER HAMBURGER BUNS 8P 16Z", "Units", "FG"),
    "950996": ("ART BRI HAM BN BAG 8P 16Z", "Units", "FG"),
    "150080": ("ST DUTCH COUNTRY 100 WW BREAD 4.49 24Z", "Units", "FG"),
    "77442": ("GV WHITE SANDWICH BREAD 20Z", "Units", "FG"),
    "76003": ("SL S N S MWGW ROUND TOP BREAD 20Z", "Units", "FG"),
    "74556": ("ST KING WHITE SANDWICH BREAD 22Z", "Units", "FG"),
    "3001": ("Wheat Flour", "KG", "RM"),
    "3002": ("Whole Wheat Flour", "KG", "RM"),
    "3003": ("Soybean Oil", "CAN", "RM"),
    "3004": ("Sugar", "KG", "RM"),
    "3005": ("Salt", "KG", "RM"),
    "3006": ("Yeast", "Units", "RM"),
    "3007": ("Dough Conditioner", "BAG", "RM"),
    "3008": ("Calcium Propionate", "BAG", "RM"),
    "3009": ("Butter", "Units", "RM"),
}

# The workbook's Oracle Planning & Forecast: forecast ID -> (item, quantity).
FORECASTS = {
    "FC001": ("76108", 41769), "FC002": ("950513", 3000), "FC003": ("967517", 500),
    "FC004": ("76105", 6677), "FC005": ("950514", 6544), "FC006": ("950996", 33445),
    "FC007": ("150080", 35544), "FC008": ("77442", 34578), "FC009": ("76003", 4323),
    "FC010": ("74556", 4321),
}

SHIP_TO = {   # customer -> Ship-To Location (USA), as in the workbook
    "ALBANY NY (PROSPECT RD) BAKERY": "Albany, NY, USA", "BBU_BAY SHORE NY ST": "Bay Shore, NY, USA",
    "BBU_LANCASTER NY CMB": "Lancaster, NY, USA", "BBU_LANCASTER NY IBP": "Lancaster, NY, USA",
    "BBU_MASPETH NY PAGE": "Maspeth, NY, USA", "BBU_MASPETH PAGE AR": None,
    "CANASTOTA NY DISTRIBUTION CENTER": "Canastota, NY, USA", "CHICOPEE MA DISTRIBUTION CENTER": "Chicopee, MA, USA",
    "COLONIE NY DISTRIBUTION CENTER": "Colonie, NY, USA",
    "MILLBURY MA (LATTI FARM RD) DISTRIBUTION CENTER": "Millbury, MA, USA",
    "WILMINGTON MA DISTRIBUTION CENTER": "Wilmington, MA, USA",
}


HISTORY_DAYS = 8   # earlier sales days (the same weekday, a week apart) seeded as the ML layer's history
# Week-to-week variation, as multipliers of today's value. Each pattern's median is exactly 1, so
# today sits in the middle of its history (robust z about 0) and a demo edit of a few percent stays
# normal; each series takes the pattern from a different week, so the series do not move together.
ORDER_PATTERN = (0.92, 1.05, 0.97, 1.08, 0.95, 1.03, 0.90, 1.06)      # customer orders, about +-8%
FORECAST_PATTERN = (0.96, 1.04, 0.98, 1.05, 0.94, 1.02, 0.97, 1.03)   # forecasts, about +-4%
WRAP_PATTERN = (0.97, 1.03, 0.99, 1.04, 0.96, 1.02, 0.98, 1.01)       # wrap duration, about +-2.5%
# Daily change of WMS on hand as a share of today's on hand, oldest first; the last is today's count.
STOCK_STEPS = (0.02, -0.03, 0.01, -0.015, 0.035, -0.02, 0.025, -0.01)
# Customers whose orders swing a lot from week to week (multiplier of today's order, by weeks back),
# so that a large order from them is normal: BBU_LANCASTER NY CMB orders anywhere from 90 to 1,000.
LUMPY = {"BBU_LANCASTER NY CMB": (3.1, 0.7, 7.4, 1.2, 5.5, 1.0, 6.7, 0.9)}


def history_order_number(order: str, weeks_back: int) -> str:
    """A distinct, same-length order number for the order this customer placed `weeks_back` weeks ago."""
    return str(int(order) - weeks_back * 10 ** (len(order) - 3))


def _week(pattern: tuple, weeks_back: int, series: int) -> float:
    return pattern[(weeks_back - 1 + series) % len(pattern)]


def wo_number(sales_date: dt.date, n: int) -> str:
    return f"WO-{PLANT_CODE}-{sales_date:%Y%m%d}-{n:03d}"


# One SKU's chain. Orders: (order number, customer, quantity, ODI requested ship date as days before S,
# allocated in WMS, ODI quantity when ODI's copy differs). Work order: what MIPS plans.
SKUS = {
    "76108": {   # the workbook, value for value: reconciles
        "buss": "2771", "forecast": "FC001", "wo": 1, "price": 4.69, "line": "Line 1", "shift": "Shift G",
        "oracle_type": "SU392933", "formula": "PH202825A", "mips_desc": "Ball Park Hot Dogs",
        "wrap": ((3, 17, 34), (3, 22, 8)), "batches": (1, 1), "forecast_ship": 2,
        "orders": [
            ("2263245", "ALBANY NY (PROSPECT RD) BAKERY", 7200, 1, 0, None),
            ("91220263121003", "BBU_BAY SHORE NY ST", 473, 1, 473, None),
            ("91220262951003", "BBU_LANCASTER NY CMB", 132, 1, 0, None),
            ("91220263101003", "BBU_LANCASTER NY IBP", 1210, 1, 0, None),
            ("91220262941003", "BBU_MASPETH NY PAGE", 660, 1, 0, None),
            ("91220262931003", "BBU_MASPETH PAGE AR", 44, 2, 0, None),
            ("2266620", "CANASTOTA NY DISTRIBUTION CENTER", 1606, 2, 0, None),
            ("2263257", "CHICOPEE MA DISTRIBUTION CENTER", 594, 2, 0, None),
            ("2263254", "COLONIE NY DISTRIBUTION CENTER", 1518, 2, 0, None),
            ("2263248", "MILLBURY MA (LATTI FARM RD) DISTRIBUTION CENTER", 550, 2, 0, None),
            ("2263256", "WILMINGTON MA DISTRIBUTION CENTER", 352, 2, 0, None),
        ],
        "stock": (7473, 7000, 473, "FG.1.1.1"), "mips": (55635, 48635),
    },
    "950513": {   # MIPS sized the work order to demand left, ignoring the 1,140 available (R05); one extra batch (R11)
        "buss": "2790", "forecast": "FC002", "wo": 2, "price": 4.99, "line": "Line 1", "shift": "Shift B",
        "oracle_type": "SU392940", "formula": "PH202830A", "mips_desc": "Ball Park Butter Hot Dogs",
        "wrap": ((3, 23, 5), (2, 1, 40)), "batches": (4, 5), "forecast_ship": 2,
        "orders": [
            ("2263246", "ALBANY NY (PROSPECT RD) BAKERY", 1200, 1, 0, None),
            ("2266621", "CANASTOTA NY DISTRIBUTION CENTER", 640, 2, 0, None),
            ("2263258", "CHICOPEE MA DISTRIBUTION CENTER", 360, 2, 360, None),
        ],
        "stock": (1500, 1140, 360, "FG.1.1.2"), "mips": (4840, 4840),
    },
    "76105": {   # ODI copy of the Colonie order stale (R02); WMS on hand off by 100 (R10); Albany wrapped late (R12)
        "buss": "2765", "forecast": "FC004", "wo": 3, "price": 4.69, "line": "Line 2", "shift": "Shift B",
        "oracle_type": "SU392951", "formula": "PH202840A", "mips_desc": "Ball Park Hamburger Buns",
        "wrap": ((2, 5, 10), (2, 9, 45)), "batches": (3, 3), "forecast_ship": 2,
        "orders": [
            ("2263247", "ALBANY NY (PROSPECT RD) BAKERY", 2400, 3, 0, None),
            ("2263255", "COLONIE NY DISTRIBUTION CENTER", 900, 2, 0, 800),
            ("2263249", "MILLBURY MA (LATTI FARM RD) DISTRIBUTION CENTER", 520, 2, 0, None),
            ("2263259", "WILMINGTON MA DISTRIBUTION CENTER", 300, 1, 300, None),
        ],
        "stock": (1900, 1500, 300, "FG.2.1.1"), "mips": (10497, 8997),
    },
}

# Flags the seed must raise (flag IDs, as the engine builds them). The seed test checks these.
EXPECTED = {
    "R05-950513": "MIPS sized the work order to the 4,840 demand left and did not subtract 1,140 available",
    f"R11-{wo_number(DEFAULT_SALES_DATE, 2)}": "the plant mixed 5 batches against 4 scheduled",
    "R02-2263255-1": "BUSS has the Colonie order at 900; ODI still maps 800",
    "R10-INV-0003": "WMS on hand 1,900 but available 1,500 + allocated 300 = 1,800",
    f"R12-{wo_number(DEFAULT_SALES_DATE, 3)}": "wrapping ended after the Albany order's requested ship date",
}
CLEAN_SKUS = ("76108",)


def start_time(sales_date: dt.date) -> dt.datetime:
    return dt.datetime.combine(sales_date - dt.timedelta(days=1), dt.time(20, 0))


def _at(d: dt.date, days_before: int, hh: int, mm: int = 0) -> dt.datetime:
    return dt.datetime.combine(d - dt.timedelta(days=days_before), dt.time(hh, mm))


def build(sales_date: dt.date = DEFAULT_SALES_DATE) -> list[Change]:
    S = sales_date
    changes: list[Change] = []

    def add(entity: str, row: dict, ts: dt.datetime) -> None:
        changes.append(Change(entity, "I", row=derive(entity, row), source_ts=ts, scenario=SCENARIO))

    # --- Oracle: item master and forecasts --------------------------------------
    for sku, (desc, uom, kind) in ITEM_MASTER.items():
        add("oracle.item", {"Item Number": sku, "Item Description": desc, "UOM": uom, "Item Type": kind},
            _at(S, 30, 8))
    for fid, (sku, qty) in FORECASTS.items():
        desc, uom, kind = ITEM_MASTER[sku]
        add("oracle.forecast", {"Forecast ID": fid, "Forecast Date": S, "Item Number": sku, "Item Description": desc,
                                "UOM": uom, "Item Type": kind, "Plant": PLANT_CODE,
                                "Forecast / Required Quantity": qty}, _at(S, 5, 15, 32))

    odi_n = 0
    for inv_n, (sku, c) in enumerate(SKUS.items(), 1):
        desc = ITEM_MASTER[sku][0]
        wo = wo_number(S, c["wo"])
        (ws_d, ws_h, ws_m), (we_d, we_h, we_m) = c["wrap"]
        wrap_start, wrap_end = _at(S, ws_d, ws_h, ws_m), _at(S, we_d, we_h, we_m)
        sched, mixed = c["batches"]
        demand_left, wo_qty = c["mips"]

        # --- BUSS sales orders --------------------------------------------------
        for order, customer, qty, _, _, _ in c["orders"]:
            add("buss.sales_order", {
                "SO Line ID": f"{order}-1", "Order Number": order, "Order Line": 1, "Order Type": "DSD_SALES_ORDER",
                "Order Date": S, "Customer Name": customer, "Ship-To Location (USA)": SHIP_TO[customer],
                "FG BUSS Code": c["buss"], "Item Number": sku, "Item Description": desc, "UOM": "Units",
                "Order Quantity": qty, "Requested Ship Date": S, "Sales Date": S, "Price in $": c["price"]}, _at(S, 3, 10))

        # --- MIPS work order ------------------------------------------------------
        add("mips.work_order", {
            "Production Date": S, "Work Order Number": wo, "FG Oracle Ref": sku, "FG BUSS Code": c["buss"],
            "FG Description": desc, "Forecast / Order Quantity": demand_left, "Prod Line": c["line"],
            "Shift": c["shift"], "Scheduling Errors": None, "Work Order Quantity": wo_qty, "Work order start date": S,
            "Work Order Completion Date": S, "Work Order Status": "Released"}, _at(S, 4, 6))

        # --- ODI batch data entry mapping: the forecast first, then each order ------
        def odi(source: str, ref: str, line: int | None, qty: float, ship_days: int, allocated: float) -> None:
            nonlocal odi_n
            odi_n += 1
            on_wo = not allocated   # a line served from allocated stock has no work order and no batches
                                    # (as in the workbook)
            add("odi.batch_mapping", {
                "ODI Entry ID": f"ODI-{odi_n:04d}", "Plant Code": PLANT_CODE, "Schedule Date": S,
                "Source System": source, "BUSS Sales / Forcast Date": S, "Product Sales Date": S,
                "Requested Ship Date": S - dt.timedelta(days=ship_days), "Sales Order / Forcast Date": S,
                "Sales Order Number / Forcast": ref, "SO Line number": line, "Quantity": qty,
                "Allocated quantity for SO": allocated or None,
                "Work Order Number": wo if on_wo else None, "Work Order Date": S if on_wo else None,
                "Work Order Quantity": wo_qty if on_wo else None, "Item Number": sku if on_wo else None,
                "Wrapping Start Date Time": wrap_start if on_wo else None,
                "Wrapping End Date Time": wrap_end if on_wo else None,
                "Plant Name": PLANT_NAME if on_wo else None, "Prod Line": c["line"] if on_wo else None,
                "Shift": c["shift"] if on_wo else None, "Oracle Type": c["oracle_type"] if on_wo else None,
                "Oracle Formula": c["formula"] if on_wo else None, "MIPS Description": c["mips_desc"] if on_wo else None,
                "MIPS Sched Batches": sched if on_wo else None, "Act Batches Mixed": mixed if on_wo else None,
                "Batch Status": None},
                wrap_end + dt.timedelta(minutes=30))

        odi("OCloud Region2", c["forecast"], None, FORECASTS[c["forecast"]][1], c["forecast_ship"], 0)
        for order, _, qty, ship_days, allocated, odi_qty in c["orders"]:
            odi("BUSS", order, 1, qty if odi_qty is None else odi_qty, ship_days, allocated)

        # --- WMS stock: a daily count for HISTORY_DAYS days, then today's ---------------
        on_hand, available, allocated = c["stock"][:3]
        inv = f"INV-{inv_n:04d}"
        levels = [on_hand]                               # back from today's count, one day at a time
        for share in reversed(STOCK_STEPS):
            levels.append(levels[-1] - round(on_hand * share))
        levels.reverse()                                 # oldest first; the last one is today's
        add("wms.inventory", {
            "Inventory ID": inv, "Item Number": sku, "Item Description": desc, "On hand": levels[0],
            "Available quantity": levels[0] - allocated, "Allocated quantity": allocated, "UOM": "Units",
            "Item Type": "FG", "Facility": PLANT_CODE, "Company / Inventory Organization": f"{PLANT_CODE}.1",
            "Locator": c["stock"][3]}, _at(S, HISTORY_DAYS + 1, 14))
        for days_before, level in zip(range(HISTORY_DAYS, 1, -1), levels[1:-1]):
            changes.append(Change("wms.inventory", "U", inv, {"On hand": level, "Available quantity": level - allocated},
                                  source_ts=_at(S, days_before, 14), scenario=SCENARIO))
        changes.append(Change("wms.inventory", "U", inv, {"On hand": on_hand, "Available quantity": available,
                                                          "Allocated quantity": allocated},
                              source_ts=_at(S, 1, 14), scenario=SCENARIO))

    # --- History for the ML layer: the same weekday, HISTORY_DAYS earlier weeks ------------
    for s_n, (sku, c) in enumerate(SKUS.items()):
        desc = ITEM_MASTER[sku][0]
        fid, base_fc = c["forecast"], FORECASTS[c["forecast"]][1]
        demand_left, wo_qty = c["mips"]
        (ws_d, ws_h, ws_m), (we_d, we_h, we_m) = c["wrap"]
        wrap_minutes = (_at(S, we_d, we_h, we_m) - _at(S, ws_d, ws_h, ws_m)).total_seconds() / 60
        orders_today = sum(o[2] for o in c["orders"])
        for k in range(1, HISTORY_DAYS + 1):
            D = S - dt.timedelta(weeks=k)
            total = 0
            for o_n, (order, customer, qty, *_) in enumerate(c["orders"]):
                m = LUMPY[customer][k - 1] if customer in LUMPY else _week(ORDER_PATTERN, k, s_n + o_n)
                q = max(round(qty * m), 1)
                total += q
                num = history_order_number(order, k)
                add("buss.sales_order", {
                    "SO Line ID": f"{num}-1", "Order Number": num, "Order Line": 1, "Order Type": "DSD_SALES_ORDER",
                    "Order Date": D, "Customer Name": customer, "Ship-To Location (USA)": SHIP_TO[customer],
                    "FG BUSS Code": c["buss"], "Item Number": sku, "Item Description": desc, "UOM": "Units",
                    "Order Quantity": q, "Requested Ship Date": D, "Sales Date": D, "Price in $": c["price"]},
                    _at(D, 3, 10))
            fc_q = round(base_fc * _week(FORECAST_PATTERN, k, s_n))
            h_fid = f"{fid}-{D:%m%d}"
            add("oracle.forecast", {"Forecast ID": h_fid, "Forecast Date": D, "Item Number": sku,
                                    "Item Description": desc, "UOM": "Units", "Item Type": "FG", "Plant": PLANT_CODE,
                                    "Forecast / Required Quantity": fc_q}, _at(D, 5, 15, 32))
            factor = (total + fc_q) / (orders_today + base_fc)
            h_wo, h_wo_qty = wo_number(D, c["wo"]), round(wo_qty * factor)
            add("mips.work_order", {
                "Production Date": D, "Work Order Number": h_wo, "FG Oracle Ref": sku, "FG BUSS Code": c["buss"],
                "FG Description": desc, "Forecast / Order Quantity": round(demand_left * factor),
                "Prod Line": c["line"], "Shift": c["shift"], "Scheduling Errors": None,
                "Work Order Quantity": h_wo_qty, "Work order start date": D, "Work Order Completion Date": D,
                "Work Order Status": "Completed"}, _at(D, 4, 6))
            start = _at(D, ws_d, ws_h, ws_m)
            end = start + dt.timedelta(minutes=round(wrap_minutes * _week(WRAP_PATTERN, k, s_n)))
            odi_n += 1
            add("odi.batch_mapping", {
                "ODI Entry ID": f"ODI-{odi_n:04d}", "Plant Code": PLANT_CODE, "Schedule Date": D,
                "Source System": "OCloud Region2", "BUSS Sales / Forcast Date": D, "Product Sales Date": D,
                "Requested Ship Date": D - dt.timedelta(days=c["forecast_ship"]), "Sales Order / Forcast Date": D,
                "Sales Order Number / Forcast": h_fid, "SO Line number": None, "Quantity": fc_q,
                "Allocated quantity for SO": None, "Work Order Number": h_wo, "Work Order Date": D,
                "Work Order Quantity": h_wo_qty, "Item Number": sku, "Wrapping Start Date Time": start,
                "Wrapping End Date Time": end, "Plant Name": PLANT_NAME, "Prod Line": c["line"], "Shift": c["shift"],
                "Oracle Type": c["oracle_type"], "Oracle Formula": c["formula"], "MIPS Description": c["mips_desc"],
                "MIPS Sched Batches": c["batches"][0], "Act Batches Mixed": c["batches"][0], "Batch Status": None},
                end + dt.timedelta(minutes=30))

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
