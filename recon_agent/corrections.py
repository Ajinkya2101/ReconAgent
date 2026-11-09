"""Write-back (Phase 2 of the product, POC on the event feed): from a flag to a confirmed change
in the source system.

    Flagged -> Proposed -> Approved (planner or manager) -> Sent -> Confirmed
                                                          \\-> Failed -> (retry) -> Sent
                                                                     \\-> Cancelled (no longer needed)

`propose` turns a flag into one concrete source-row change, computed from the rows the
worklist currently sees. Approving sends it to the Corrections API (Oracle Integration; in
the POC the source simulator's /oic/corrections), which applies it only if the row still has
the values the proposal was computed from. A correction is Confirmed when the worklist sees the
change arrive back through the feed, not when the API says yes. Every correction keeps its ID,
before and after values and status history (corrections.json, next to the planner decisions).
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config, security
from .detectors import Finding
from .events import contract
from .ingest import norm_sku

ACTIVE = ("Approved", "Sent")


@dataclass
class Proposal:
    flag_id: str
    rule: str
    system: str                 # source system that changes
    entity: str
    op: str                     # "U" update, "I" insert, "D" delete
    key: str
    expected: dict              # column -> value the row must still have (JSON form)
    changes: dict               # column -> new value (JSON form)
    summary: str
    # More rows of the same entity that take the same `changes` in the same source transaction
    # (one work order spans many ODI rows): [{"key": ..., "expected": {...}}].
    rows: list = field(default_factory=list)


def _j(v):
    return contract.to_json(v)


def _odi_rows(tables: dict, sku: str, wo: str | None = None) -> list[dict]:
    """ODI rows of one SKU (by the item ODI carries, or the work order they map to)."""
    wos = {k for k, w in tables["mips.work_order"].items() if norm_sku(w["FG Oracle Ref"]) == sku}
    rows = [r for r in tables["odi.batch_mapping"].values()
            if norm_sku(r["Item Number"]) == sku or (r["Work Order Number"] and r["Work Order Number"] in wos)]
    return [r for r in rows if wo is None or r["Work Order Number"] == wo]


def _work_orders(tables: dict, sku: str) -> list[dict]:
    return [w for w in tables["mips.work_order"].values() if norm_sku(w["FG Oracle Ref"]) == sku]


def _next_key(keys, prefix: str) -> str:
    nums = [int(k[len(prefix):]) for k in keys if k.startswith(prefix) and k[len(prefix):].isdigit()]
    return f"{prefix}{max(nums, default=0) + 1:04d}"


WO_COLUMNS = ("Work Order Number", "Work Order Date", "Work Order Quantity", "Item Number",
              "Wrapping Start Date Time", "Wrapping End Date Time", "Plant Name", "Prod Line", "Shift",
              "Oracle Type", "Oracle Formula", "MIPS Description", "MIPS Sched Batches", "Act Batches Mixed")


def propose(f: Finding, tables: dict[str, dict[str, dict]], now: dt.datetime) -> Proposal | None:
    """The source change that resolves `f`, or None when the rule has no automatic correction."""
    ev = f.evidence
    if f.rule == "R01":   # the demand line never reached ODI: map it to the SKU's work order
        template = next((r for r in _odi_rows(tables, f.sku) if r["Work Order Number"]), None)
        if template is None:
            return None
        is_so = ev["source_system"] == "BUSS"
        src = (tables["buss.sales_order"].get(f.key) if is_so else tables["oracle.forecast"].get(f.key))
        if src is None:
            return None
        date = src["Sales Date"] if is_so else src["Forecast Date"]
        key = _next_key(tables["odi.batch_mapping"], "ODI-")
        row = {**{c: _j(template[c]) for c in WO_COLUMNS}, "ODI Entry ID": key, "Plant Code": template["Plant Code"],
               "Schedule Date": _j(template["Schedule Date"]),
               "Source System": "BUSS" if is_so else "OCloud Region2", "BUSS Sales / Forcast Date": _j(date),
               "Product Sales Date": _j(date),
               "Requested Ship Date": _j(src["Requested Ship Date"] if is_so else template["Requested Ship Date"]),
               "Sales Order / Forcast Date": _j(date),
               "Sales Order Number / Forcast": src["Order Number"] if is_so else src["Forecast ID"],
               "SO Line number": src["Order Line"] if is_so else None, "Quantity": _j(ev["source_qty"])}
        return Proposal(f.id, f.rule, "ODI", "odi.batch_mapping", "I", key, {}, row,
                        f"Map {ev['source_qty']:,.0f} units in ODI as {key}, on work order "
                        f"{template['Work Order Number']}, for {ev['demand_line']}.")
    if f.rule == "R02":   # ODI's copy follows the source
        r = tables["odi.batch_mapping"].get(ev["odi_entry"])
        if not r:
            return None
        return Proposal(f.id, f.rule, "ODI", "odi.batch_mapping", "U", ev["odi_entry"],
                        {"Quantity": _j(r["Quantity"])}, {"Quantity": _j(ev["source_qty"])},
                        f"Set ODI row {ev['odi_entry']} from {r['Quantity']:,.0f} to {ev['source_qty']:,.0f}, as "
                        f"{ev['source_system']} has {ev['demand_line']}.")
    if f.rule == "R03":   # the demand is gone at the source: remove its ODI row
        r = tables["odi.batch_mapping"].get(f.key)
        if not r:
            return None
        return Proposal(f.id, f.rule, "ODI", "odi.batch_mapping", "D", f.key, {"Quantity": _j(r["Quantity"])}, {},
                        f"Remove ODI row {f.key} ({r['Quantity']:,.0f} units): {ev['source_system']} no longer has "
                        f"{ev['demand_line']}.")
    if f.rule in ("R04", "R05"):   # MIPS follows the sources: demand left, or the shortfall
        wos = _work_orders(tables, f.sku)
        if len(wos) != 1:
            return None   # several work orders: a person decides which one changes
        w = wos[0]
        col, new, what = (("Forecast / Order Quantity", ev["demand_left"], "demand")
                          if f.rule == "R04" else ("Work Order Quantity", ev["shortfall"], "work order quantity"))
        old = w[col] or 0
        return Proposal(f.id, f.rule, "MIPS", "mips.work_order", "U", w["Work Order Number"],
                        {col: _j(old)}, {col: _j(new)},
                        f"Set the MIPS {what} on {w['Work Order Number']} from {old:,.0f} to {new:,.0f}.")
    if f.rule == "R06":   # no work order for the shortfall: create it (restoring the number ODI still maps)
        odi = _odi_rows(tables, f.sku)
        refs = sorted({r["Work Order Number"] for r in odi if r["Work Order Number"]})
        orders = [o for o in tables["buss.sales_order"].values() if norm_sku(o["Item Number"]) == f.sku]
        sales = max((o["Sales Date"] for o in orders if o["Sales Date"]), default=now)
        sales = sales.date() if isinstance(sales, dt.datetime) else sales
        wo = refs[0] if refs and refs[0] not in tables["mips.work_order"] else _next_key(
            tables["mips.work_order"], f"WO-UBD-{sales:%Y%m%d}-")
        line = next((r for r in odi if r["Prod Line"]), {})
        p_demand = ev["total_demand"] - ev["wms_allocated"]
        row = {"Production Date": _j(sales), "Work Order Number": wo, "FG Oracle Ref": f.sku,
               "FG BUSS Code": next((o["FG BUSS Code"] for o in orders if o["FG BUSS Code"]), None),
               "FG Description": f.description, "Forecast / Order Quantity": _j(max(0, p_demand)),
               "Prod Line": line.get("Prod Line"), "Shift": line.get("Shift"), "Scheduling Errors": None,
               "Work Order Quantity": _j(ev["shortfall"]), "Work order start date": _j(sales),
               "Work Order Completion Date": _j(sales), "Work Order Status": "Released"}
        return Proposal(f.id, f.rule, "MIPS", "mips.work_order", "I", wo, {}, row,
                        f"Create MIPS work order {wo} for {ev['shortfall']:,.0f} units of {f.sku}.")
    if f.rule == "R07" and "work_order" in ev:   # every ODI row of the work order takes the MIPS quantity
        w = tables["mips.work_order"].get(f.key)
        rows = [r for r in tables["odi.batch_mapping"].values()
                if r["Work Order Number"] == f.key and r["Work Order Quantity"] != w["Work Order Quantity"]] if w else []
        if not rows:
            return None
        rows.sort(key=lambda r: r["ODI Entry ID"])
        new = w["Work Order Quantity"]
        return Proposal(f.id, f.rule, "ODI", "odi.batch_mapping", "U", rows[0]["ODI Entry ID"],
                        {"Work Order Quantity": _j(rows[0]["Work Order Quantity"])}, {"Work Order Quantity": _j(new)},
                        f"Re-send work order {f.key} to ODI: set {len(rows)} "
                        f"{'row' if len(rows) == 1 else 'rows'} to {new:,.0f}.",
                        rows=[{"key": r["ODI Entry ID"], "expected": {"Work Order Quantity": _j(r["Work Order Quantity"])}}
                              for r in rows[1:]])
    if f.rule == "R08":   # link the uncovered line to the SKU's one work order
        wos = _work_orders(tables, f.sku)
        r = tables["odi.batch_mapping"].get(f.key)
        if len(wos) != 1 or not r:
            return None
        w = wos[0]
        changes = {"Work Order Number": w["Work Order Number"], "Work Order Date": _j(w["Production Date"]),
                   "Work Order Quantity": _j(w["Work Order Quantity"])}
        return Proposal(f.id, f.rule, "ODI", "odi.batch_mapping", "U", f.key,
                        {"Work Order Number": r["Work Order Number"]}, changes,
                        f"Link ODI row {f.key} to work order {w['Work Order Number']}: {ev['demand_line']}.")
    # Deliberately no automatic correction where the fix needs a fact only a person can establish:
    # R09 (which order holds the stock), R10 (which WMS figure the count proves wrong), R11 (why the
    # plant mixed more), R12 (move the run or the ship date), R13 (which code is right).
    return None


def _targets(c: dict) -> list[tuple[str, dict]]:
    return [(c["key"], c.get("expected") or {})] + [(r["key"], r.get("expected") or {}) for r in c.get("rows") or []]


def arrived(c: dict, tables: dict[str, dict[str, dict]]) -> bool:
    """True when the worklist's own view of the source shows the corrected values on every row."""
    e = contract.ENTITIES[c["entity"]]
    for key, _ in _targets(c):
        row = tables[e.name].get(key)
        if c["op"] == "D":
            if row is not None:
                return False
            continue
        if row is None:
            return False
        for col, v in c["changes"].items():
            want = contract.coerce(v, e.columns[col])
            have = row[col]
            if isinstance(want, dt.date) and not isinstance(want, dt.datetime):
                want = dt.datetime.combine(want, dt.time())
            if want != have and not (want in ("", None) and have in ("", None)):
                return False
    return True


def send(c: dict, requested_by: str) -> tuple[bool, str]:
    """POST one correction to the Corrections API. (accepted, message)."""
    body = json.dumps({"correction_id": c["id"], "flag_id": c["flag_id"], "requested_by": requested_by,
                       "entity": c["entity"], "op": c["op"], "key": c["key"], "expected": c["expected"],
                       "changes": c["changes"], "rows": c.get("rows") or []}).encode()
    req = urllib.request.Request(config.CORRECTIONS_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          **({"X-Corrections-Token": config.CORRECTIONS_TOKEN}
                                             if config.CORRECTIONS_TOKEN else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            reply = json.loads(r.read() or b"{}")
            return True, f"accepted by Oracle Integration (source change S{reply.get('source_seq')})"
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}").get("error", "")
        except ValueError:
            detail = ""
        reason = {409: "the source row changed since this was computed", 503: "Oracle Integration is unavailable",
                  422: "Oracle Integration rejected it"}.get(e.code, f"HTTP {e.code}")
        return False, f"{reason}{': ' + detail if detail and e.code != 409 else ''}"
    except (urllib.error.URLError, OSError) as e:
        return False, f"the Corrections API is unreachable ({getattr(e, 'reason', e)})"


class CorrectionBook:
    """Every correction with its status history, persisted as JSON.

    A correction belongs to one event log (`log_id`): after a reset, earlier corrections stay in
    the file for the record but no longer show on flags or wait for confirmation.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.items: dict[str, dict] = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.log_id: str | None = None   # the log the worklist reads now (set on every reload)

    def _save(self) -> None:
        security.write_json(self.path, self.items, indent=2, default=str)

    def current(self) -> list[dict]:
        return [c for c in self.items.values() if c.get("log_id") == self.log_id]

    def for_flag(self, flag_id: str) -> dict | None:
        found = [c for c in self.current() if c["flag_id"] == flag_id]
        return found[-1] if found else None

    def in_status(self, *statuses: str) -> list[dict]:
        return [c for c in self.current() if c["status"] in statuses]

    def create(self, p: Proposal, row_key: str, by: str, at: dt.datetime) -> dict:
        with self.lock:
            cid = f"COR-{len(self.items) + 1:04d}"
            self.items[cid] = {"id": cid, "log_id": self.log_id, **asdict(p), "row_key": row_key, "status": "Approved",
                               "created_by": by, "error": "", "confirmed_seq": None,
                               "history": [{"status": "Approved", "at": at.isoformat(timespec="seconds"), "by": by,
                                            "note": p.summary}]}
            self._save()
            return self.items[cid]

    def update(self, cid: str, status: str, by: str, at: dt.datetime, note: str = "", **fields) -> dict:
        with self.lock:
            c = self.items[cid]
            c.update(status=status, **fields)
            c["history"].append({"status": status, "at": at.isoformat(timespec="seconds"), "by": by, "note": note})
            self._save()
            return c
