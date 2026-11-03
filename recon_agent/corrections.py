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
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config, security
from .detectors import Finding
from .events import contract
from .events.derive import order_totals
from .ingest import norm_sku

ACTIVE = ("Approved", "Sent")


@dataclass
class Proposal:
    flag_id: str
    rule: str
    system: str                 # source system that changes
    entity: str
    op: str                     # "U" update, "I" insert
    key: str
    expected: dict              # column -> value the row must still have (JSON form)
    changes: dict               # column -> new value (JSON form)
    summary: str


def _j(v):
    return contract.to_json(v)


def _runs(tables: dict, sku: str) -> list[dict]:
    rows = [r for r in tables["odi.finished_good"].values()
            if norm_sku(r["OracleFGNumber"]) == sku and (r["NetUnitsOrdered"] or r["ActualWrappedUnits"])]
    return sorted(rows, key=lambda r: (r["ProductionStartDateTime"] is None, r["ProductionStartDateTime"] or 0))


def propose(f: Finding, tables: dict[str, dict[str, dict]], now: dt.datetime) -> Proposal | None:
    """The one source-row change that resolves `f`, or None when the rule has no automatic correction."""
    if f.rule == "R09":   # MIPS plans a different quantity than WMS has on order: MIPS follows WMS
        lines = [r for r in tables["mips.order_line"].values()
                 if norm_sku(r["FG Oracle Ref"]) == f.sku and not r["Order Moved"]]
        if not lines:
            return None
        line = max(lines, key=lambda r: (r["Source System"] == "BUSS", r["Order Quantity"] or 0))
        old = line["Order Quantity"] or 0
        new = max(0, old + f.units)
        return Proposal(f.id, f.rule, "MIPS", "mips.order_line", "U", line["Order Line ID"],
                        {"Order Quantity": _j(old)}, {"Order Quantity": _j(new)},
                        f"Set MIPS line {line['Order Line ID']} ({line['Source System']}) from {old:,.0f} to "
                        f"{new:,.0f} so MIPS plans what WMS has on order.")
    if f.rule == "R19":   # the ticket does not show the return: book it on the ticket
        tk = tables["wms.ticket"].get(f.key)
        if not tk:
            return None
        returned = f.evidence["returned_by_transactions"]
        return Proposal(f.id, f.rule, "WMS", "wms.ticket", "U", f.key,
                        {"Returned Qty": _j(tk["Returned Qty"] or 0)}, {"Returned Qty": _j(returned)},
                        f"Book {returned:,.0f} returned units on ticket {f.key}; WMS then shows "
                        f"{(tk['Received Qty'] or 0) - returned:,.0f} received.")
    if f.rule == "R18":   # resubmit the WMS transaction to Oracle
        txn = tables["wms.transaction"].get(f.key)
        if not txn:
            return None
        existing = next((r for r in tables["oracle.inventory_txn"].values() if r["SourceReference"] == f.key), None)
        if existing:
            return Proposal(f.id, f.rule, "Oracle", "oracle.inventory_txn", "U", existing["TransactionId"],
                            {"ProcessStatus": existing["ProcessStatus"]},
                            {"ProcessStatus": "POSTED", "ErrorMessage": None, "PostedDate": _j(now)},
                            f"Resubmit Oracle transaction {existing['TransactionId']} for WMS {f.key} and post it.")
        sign = -1 if txn["Txn Type"] in ("SHIP", "RETURN_TO_PLANT", "FREEZER_OUT") else 1
        row = {"TransactionId": f"OT-R-{f.key}", "SourceReference": f.key,
               "TransactionTypeName": f"WMS {txn['Txn Type'].replace('_', ' ').title()}",
               "ItemNumber": txn["Item Nbr"], "TransactionQuantity": _j(sign * (txn["Qty"] or 0)),
               "TransactionUnitOfMeasure": "Each", "Subinventory": "FG", "LotNumber": txn["Lot Nbr"],
               "TransactionDate": _j(txn["Txn Date Time"]), "PostedDate": _j(now), "ProcessStatus": "POSTED"}
        return Proposal(f.id, f.rule, "Oracle", "oracle.inventory_txn", "I", row["TransactionId"], {}, row,
                        f"Post WMS {txn['Txn Type'].lower()} {f.key} ({txn['Qty']:,.0f} units) to Oracle as "
                        f"{row['TransactionId']}.")
    if f.rule == "R13" and f.key == "FILL-VS-ITEM":   # rebuild the order header that disagrees most
        items = list(tables["wms.item_order"].values())
        best = None
        for o in tables["wms.order"].values():
            totals = order_totals(items, o["Ship to Name"])
            gap = abs((o["Order Qty"] or 0) - totals["Order Qty"])
            if gap and (best is None or gap > best[0]):
                best = (gap, o, totals)
        if not best:
            return None
        _, o, totals = best
        return Proposal(f.id, f.rule, "WMS", "wms.order", "U", o["Order Nbr"],
                        {"Order Qty": _j(o["Order Qty"]), "Allocated Qty": _j(o["Allocated Qty"])},
                        {k: _j(v) for k, v in totals.items()},
                        f"Rebuild WMS order {o['Order Nbr']} ({o['Ship to Name']}) from its item lines: "
                        f"{totals['Order Qty']:,.0f} ordered, {totals['Allocated Qty']:,.0f} allocated.")
    if f.rule == "R16":   # ODI's copy of the sales-order quantity follows MIPS
        runs = _runs(tables, f.sku)
        if not runs:
            return None
        first, others = runs[0], sum(r["MIPSOrderQty"] or 0 for r in runs[1:])
        new = f.evidence["mips_sales_order_qty"] - others
        return Proposal(f.id, f.rule, "ODI", "odi.finished_good", "U", first["FGEntryID"],
                        {"MIPSOrderQty": _j(first["MIPSOrderQty"] or 0)}, {"MIPSOrderQty": _j(new)},
                        f"Re-send the MIPS sales-order quantity to ODI run {first['FGEntryID']}: {new:,.0f}.")
    # Deliberately no automatic correction where the fix needs a fact only a person can establish:
    # R02 (where the netted inventory physically is: recording it blindly could hide a real
    # shortfall), R07 (which freezer lot was used), R05/R06 (which day takes the surplus), R10/R11.
    return None


def arrived(c: dict, tables: dict[str, dict[str, dict]]) -> bool:
    """True when the worklist's own view of the source shows the corrected values."""
    e = contract.ENTITIES[c["entity"]]
    row = tables[e.name].get(c["key"])
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
                       "changes": c["changes"]}).encode()
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
