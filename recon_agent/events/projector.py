"""Fold the event log into the current state of each source system.

The projector keeps one table per entity (key -> typed row), applies only the
events it has not seen, and hands the engine the same SourceData the Excel
adapters build, so nothing downstream knows where the data came from.

Semantics: insert and update both upsert the full after image (an update for a
key the projector never saw is applied as an insert, so a late-joining reader
still converges); delete removes the key. Rows come out in key order (the
source's own line sequence), so the result never depends on the order in which
events for different rows arrived.

Demand at order time: the agent infers the inventory MIPS netted as the SKU's demand
less what its first production run ordered. That only holds for the demand MIPS saw
when it sized the run, so the projector also keeps each MIPS line's quantity history
and the source position at which each ODI run's order was last set. `demand_at_order`
rebuilds each SKU's demand as of that position, so a later demand change reads as new
demand, not as inventory. Positions are source sequence numbers (the seed is the
opening snapshot, position 0), so the answer does not depend on delivery order either.
"""
from __future__ import annotations

import datetime as dt

from .. import ingest
from . import contract
from .clock import SimClock
from .log import Event, EventLog

# Entity feeding each SourceData input, labelled as the extract it replaces.
EXTRACTS = {
    "mips_orders": "mips.order_line",
    "odi_finished_goods": "odi.finished_good",
    "odi_batches": "odi.batch",
    "wms_fill_rate": "wms.order",
    "wms_order_summary": "wms.item_order",
}

# Producer of the opening load (seed.py). Its rows are what the plant knew when production was
# ordered, whatever order they were committed in; history for demand_at_order starts after it.
INITIAL_LOAD = "seed"


class Projector:
    """stream="events" is the consumer view (what the integration delivered); stream="source"
    is the source systems' own state (what the simulator shows and edits)."""

    def __init__(self, log: EventLog, clock: SimClock | None = None, stream: str = "events"):
        self.log = log
        self.clock = clock or SimClock(log)
        self.stream = stream
        self.reset()

    def reset(self) -> None:
        self.log_id: str | None = None
        self.seq = 0
        self.tables: dict[str, dict[str, dict]] = {name: {} for name in contract.ENTITIES}
        self.row_seq: dict[str, dict[str, int]] = {name: {} for name in contract.ENTITIES}   # last event per row
        # Consumer protection against a faulty integration: the source position applied per row
        # (kept after a delete, so a late copy cannot bring the row back) and the event IDs seen.
        self.row_source_seq: dict[str, dict[str, int]] = {name: {} for name in contract.ENTITIES}
        self.seen: set[str] = set()
        self.duplicates_ignored = 0
        self.stale_ignored = 0
        self.last_source_ts: dt.datetime | None = None
        # For demand_at_order: (position, SKU or None once deleted, quantity) per MIPS line,
        # and the position at which each ODI run's order (quantity or SKU) was last set.
        self.line_history: dict[str, list[tuple[int, str | None, float]]] = {}
        self.order_set_at: dict[str, int] = {}

    def catch_up(self) -> int:
        """Apply new events. Starts over if the log was reset. Returns how many were read."""
        log_id, head = self.log.position() if self.stream == "events" else self.log.source_position()
        if log_id != self.log_id:
            self.reset()
            self.log_id = log_id
        if head <= self.seq:
            return 0
        events = self.log.read_since(self.seq, stream=self.stream)
        for ev in events:
            self.apply(ev)
        return len(events)

    def apply(self, ev: Event) -> None:
        self.seq = ev.seq
        if ev.event_id in self.seen:   # a duplicate delivery: already applied
            self.duplicates_ignored += 1
            return
        self.seen.add(ev.event_id)
        if ev.source_seq <= self.row_source_seq[ev.entity].get(ev.key, 0):   # older than what this row shows
            self.stale_ignored += 1
            return
        self.row_source_seq[ev.entity][ev.key] = ev.source_seq
        table = self.tables[ev.entity]
        old = table.get(ev.key)
        if ev.op == "D":
            table.pop(ev.key, None)
            self.row_seq[ev.entity].pop(ev.key, None)
        else:
            table[ev.key] = contract.decode(contract.ENTITIES[ev.entity], ev.after)
            self.row_seq[ev.entity][ev.key] = ev.seq
        self._track_demand(ev, old, table.get(ev.key))
        self.last_source_ts = ev.source_ts

    def _track_demand(self, ev: Event, old: dict | None, new: dict | None) -> None:
        at = 0 if ev.producer == INITIAL_LOAD else ev.source_seq
        if ev.entity == "mips.order_line":
            self.line_history.setdefault(ev.key, []).append(
                (at, ingest.norm_sku(new["FG Oracle Ref"]) if new and new["FG Oracle Ref"] is not None else None,
                 ingest.num(new["Order Quantity"]) if new else 0.0))
        elif ev.entity == "odi.finished_good" and (
                old is None or new is None
                or any(old.get(c) != new.get(c) for c in ("NetUnitsOrdered", "OracleFGNumber"))):
            self.order_set_at[ev.key] = at

    def demand_at_order(self) -> dict[str, float]:
        """Each SKU's MIPS demand at the source position where its first active run's order was last set."""
        runs = sorted(((k, r) for k, r in sorted(self.tables["odi.finished_good"].items())
                       if ingest.num(r["NetUnitsOrdered"]) or ingest.num(r["ActualWrappedUnits"])),
                      key=lambda kr: (kr[1]["ProductionStartDateTime"] is None,
                                      kr[1]["ProductionStartDateTime"] or dt.datetime.min))
        first: dict[str, str] = {}
        for k, r in runs:   # same order as position.build_positions, so the same run is "first"
            first.setdefault(ingest.norm_sku(r["OracleFGNumber"]), k)
        out: dict[str, float] = {}
        for sku, run_key in first.items():
            at = self.order_set_at.get(run_key, 0)
            total = 0.0
            for history in self.line_history.values():
                then = max((h for h in history if h[0] <= at), key=lambda h: h[0], default=None)
                if then and then[1] == sku:
                    total += then[2]
            out[sku] = total
        return out

    def rows(self, entity: str) -> list[dict]:
        table = self.tables[entity]
        return [table[k] for k in sorted(table)]

    def source_data(self) -> ingest.SourceData:
        orders = ingest.fill_orders(self.rows("wms.order"))
        return ingest.SourceData(
            mips=ingest.mips_lines(self.rows("mips.order_line")),
            fg_runs=ingest.fg_runs(self.rows("odi.finished_good")),
            batches=ingest.batch_runs(self.rows("odi.batch")),
            wms_items=ingest.wms_items(self.rows("wms.item_order")),
            fill_orders=orders,
            # Live WMS has no printed report total: the header orders are the total
            # that R13 checks the item lines against.
            fill_report_total=sum(o.order_qty for o in orders),
            files={k: f"event log #{self.seq} · {e} ({len(self.tables[e])} rows)" for k, e in EXTRACTS.items()},
            as_of=self.clock.now(),
            tickets=ingest.wms_tickets(self.rows("wms.ticket")),
            wms_txns=ingest.wms_transactions(self.rows("wms.transaction")),
            oracle_txns=ingest.oracle_transactions(self.rows("oracle.inventory_txn")),
            demand_at_order=self.demand_at_order(),
        )
