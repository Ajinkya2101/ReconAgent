"""Export the source systems as they are now: an Excel workbook, a SQLite database, a Postgres script.

Built on demand from the event log's source stream (what the simulator shows), so an export always
carries the current rows, including every change made since the seed. The reconciled position and
the flags in the workbook come from a fresh engine run, and the databases compute the position in a
view (`v_sku_position`), so nothing in an export is a stored result.

    python -m recon_agent events export --out DIR      # or the simulator's Export links
"""
from __future__ import annotations

import datetime as dt
import io
import re
import sqlite3
import tempfile
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import contract
from .log import EventLog
from .projector import Projector

TABLE = {e: e.replace(".", "_") for e in contract.ENTITIES}
SQL_TYPE = {contract.STR: "TEXT", contract.NUM: "NUMERIC", contract.BOOL: "BOOLEAN",
            contract.DATE: "DATE", contract.DATETIME: "TIMESTAMP"}
HEAD = PatternFill("solid", fgColor="1F3A5F")

# The equation in SQL, so a database client recomputes the position from whatever the tables hold.
VIEW = """CREATE VIEW v_sku_position AS
WITH cur AS (SELECT MAX(d) AS sales_day FROM (SELECT sales_date AS d FROM buss_sales_order
                                               UNION ALL SELECT forecast_date FROM oracle_forecast) x),
     so AS (SELECT item_number AS sku, SUM(order_quantity) AS sales_orders, COUNT(*) AS order_lines
            FROM buss_sales_order WHERE sales_date IS NULL OR sales_date = (SELECT sales_day FROM cur)
            GROUP BY item_number),
     wms AS (SELECT item_number AS sku, SUM(on_hand) AS on_hand, SUM(available_quantity) AS available,
                    SUM(allocated_quantity) AS allocated FROM wms_inventory GROUP BY item_number),
     wo AS (SELECT fg_oracle_ref AS sku, SUM(forecast_order_quantity) AS mips_demand,
                   SUM(work_order_quantity) AS work_order_qty FROM mips_work_order
            WHERE production_date IS NULL OR production_date = (SELECT sales_day FROM cur) GROUP BY fg_oracle_ref),
     skus AS (SELECT sku FROM so UNION SELECT sku FROM wms UNION SELECT sku FROM wo),
     fc AS (SELECT item_number AS sku, SUM(forecast_required_quantity) AS forecast
            FROM oracle_forecast WHERE forecast_date IS NULL OR forecast_date = (SELECT sales_day FROM cur)
            GROUP BY item_number),
     base AS (SELECT s.sku, i.item_description AS description,
                     COALESCE(so.order_lines, 0) AS order_lines, COALESCE(so.sales_orders, 0) AS sales_orders,
                     COALESCE(fc.forecast, 0) AS forecast, COALESCE(wms.on_hand, 0) AS on_hand,
                     COALESCE(wms.allocated, 0) AS allocated, COALESCE(wms.available, 0) AS available,
                     COALESCE(wo.mips_demand, 0) AS mips_demand, COALESCE(wo.work_order_qty, 0) AS work_order_qty
              FROM skus s LEFT JOIN so ON so.sku = s.sku LEFT JOIN fc ON fc.sku = s.sku
              LEFT JOIN wms ON wms.sku = s.sku LEFT JOIN wo ON wo.sku = s.sku
              LEFT JOIN oracle_item i ON i.item_number = s.sku)
SELECT sku, description, order_lines, sales_orders, forecast,
       sales_orders + forecast AS total_demand,
       allocated,
       CASE WHEN sales_orders + forecast - allocated > 0 THEN sales_orders + forecast - allocated ELSE 0 END AS demand_left,
       available,
       CASE WHEN sales_orders + forecast - allocated - available > 0
            THEN sales_orders + forecast - allocated - available ELSE 0 END AS shortfall,
       mips_demand, work_order_qty,
       work_order_qty + available + allocated - (sales_orders + forecast) AS balance
FROM base;
"""


def snake(col: str) -> str:
    """'Allocated quantity for SO' -> 'allocated_quantity_for_so': the database column name."""
    s = col.replace("%", " pct").replace("$", " usd").replace("/", " ").replace("&", " and ").replace("-", " ")
    return re.sub(r"[^0-9a-z]+", "_", s.lower()).strip("_")


def _value(v, kind):
    if v is None:
        return None
    if kind == contract.DATE and isinstance(v, dt.datetime):
        return v.date()
    if kind == contract.NUM and isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def current_rows(log: EventLog) -> dict[str, list[dict]]:
    """Every entity's rows as the source systems hold them now."""
    p = Projector(log, stream="source")
    p.catch_up()
    return {e: [{c: _value(r[c], k) for c, k in contract.ENTITIES[e].columns.items()} for r in p.rows(e)]
            for e in contract.ENTITIES}


def _engine_run(log: EventLog):
    from .. import engine
    p = Projector(log, stream="source")
    p.catch_up()
    return engine.run(src=p.source_data(), use_ai=False)


def workbook(log: EventLog) -> bytes:
    rows, res = current_rows(log), _engine_run(log)
    wb = openpyxl.Workbook()
    info = wb.active
    info.title = "About"
    info.append(["Source systems as they are now: Inventory Reconciliation Agent (data model v3, 9 Nov mapping)"])
    info.append([f"Exported {dt.datetime.now():%Y-%m-%d %H:%M} from event log source change "
                 f"#{log.source_position()[1]}. Sales date {res.kpis['sales_date']}."])
    info.append([])
    info.append(["Sheet", "System", "Rows", "What it is"])
    for e in contract.ENTITIES.values():
        info.append([e.sheet, e.source, len(rows[e.name]), e.description])
    info.append(["Reconciled position", "Engine", len(res.positions), "The equation per SKU, computed at export time"])
    info.append(["Flags", "Engine", len(res.findings), "The flags the worklist shows for this data"])
    info.append(["Column map", "-", "", "Workbook column -> database table and column"])
    info.append([])
    info.append(["Equation: Work Order Qty = (Sales Orders + Forecast) - Allocated - Available"])
    info["A1"].font = Font(bold=True, size=13)
    for c in info[4]:
        c.font = Font(bold=True)
    info.column_dimensions["A"].width = 32
    info.column_dimensions["D"].width = 90

    def sheet(title, header, data, widths=None):
        ws = wb.create_sheet(title[:31])
        ws.append(header)
        for r in data:
            ws.append(r)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = HEAD
            c.alignment = Alignment(wrap_text=True, vertical="top")
        for i, h in enumerate(header, 1):
            ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(h, max(11, min(30, len(str(h)) + 3)))
        for col in ws.iter_cols(min_row=2):
            for c in col:
                if isinstance(c.value, dt.datetime):
                    c.number_format = "yyyy-mm-dd hh:mm"
                elif isinstance(c.value, dt.date):
                    c.number_format = "yyyy-mm-dd"
                elif isinstance(c.value, (int, float)):
                    c.number_format = "#,##0"
        ws.freeze_panes = "A2"
        if ws.max_row > 1:
            ws.auto_filter.ref = ws.dimensions

    for e in contract.ENTITIES.values():
        cols = list(e.columns)
        sheet(e.sheet, cols, [[r[c] for c in cols] for r in rows[e.name]])
    pos = [p.to_row() for p in res.positions.values()]
    if pos:
        sheet("Reconciled position", list(pos[0]), [list(r.values()) for r in pos], {"Description": 34})
    sheet("Flags", ["Flag ID", "Rule", "Title", "Severity", "SKU", "Units", "$ at risk", "Started in", "Impact",
                    "Correction"],
          [[f.id, f.rule, f.title, f.severity, f.sku, f.units, round(f.dollars, 2), f.accountability.get("origin"),
            f.accountability.get("impact"), f.recommendation] for f in res.findings],
          {"Title": 44, "Correction": 110, "Flag ID": 28})
    sheet("Column map", ["Sheet", "Workbook column", "Table", "Column", "Type"],
          [[e.sheet, c, TABLE[e.name], snake(c), SQL_TYPE[k]] for e in contract.ENTITIES.values()
           for c, k in e.columns.items()], {"Workbook column": 32, "Column": 32})
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _literal(v, kind, pg: bool) -> str:
    if v is None:
        return "NULL"
    if kind == contract.BOOL:
        return ("TRUE" if v else "FALSE") if pg else ("1" if v else "0")
    if kind == contract.NUM:
        return str(v)
    if kind == contract.DATE:
        return f"'{v.isoformat()}'"
    if kind == contract.DATETIME:
        return f"'{v.isoformat(sep=' ')}'"
    return "'" + str(v).replace("'", "''") + "'"


def sql_script(log: EventLog, postgres: bool = True) -> str:
    """Tables, rows and the position view. Postgres puts them in schema `recon`; re-running replaces them."""
    rows = current_rows(log)
    parts = [f"-- Source systems as they are now (data model v3, 9 Nov mapping). Exported "
             f"{dt.datetime.now():%Y-%m-%d %H:%M} at source change #{log.source_position()[1]}.\n"]
    if postgres:
        parts.append("CREATE SCHEMA IF NOT EXISTS recon;\nSET search_path TO recon;\n")
    parts.append("DROP VIEW IF EXISTS v_sku_position;\n")
    for e in contract.ENTITIES.values():
        t = TABLE[e.name]
        parts.append(f"DROP TABLE IF EXISTS {t};\n")
        cols = [f"  {snake(c)} {SQL_TYPE[k]}{' PRIMARY KEY' if c == e.key else ''}" for c, k in e.columns.items()]
        parts.append(f"-- {e.sheet} ({e.source}): {e.description}\nCREATE TABLE {t} (\n" + ",\n".join(cols) + "\n);\n")
        names = ", ".join(snake(c) for c in e.columns)
        for r in rows[e.name]:
            vals = ", ".join(_literal(r[c], k, postgres) for c, k in e.columns.items())
            parts.append(f"INSERT INTO {t} ({names}) VALUES ({vals});\n")
        parts.append("\n")
    parts.append(VIEW)
    return "".join(parts)


def sqlite_db(log: EventLog) -> bytes:
    with tempfile.TemporaryDirectory(prefix="recon-export-") as folder:
        path = Path(folder) / "recon.sqlite"
        con = sqlite3.connect(path)
        try:
            con.executescript(sql_script(log, postgres=False))
            con.commit()
        finally:
            con.close()
        return path.read_bytes()
