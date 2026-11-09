"""Fold the event log into the current state of each source system.

The projector keeps one table per entity (key -> typed row), applies only the
events it has not seen, and hands the engine the same SourceData the workbook
reader builds, so nothing downstream knows where the data came from.

Semantics: insert and update both upsert the full after image (an update for a
key the projector never saw is applied as an insert, so a late-joining reader
still converges); delete removes the key. Rows come out in key order (the
source's own line sequence), so the result never depends on the order in which
events for different rows arrived.
"""
from __future__ import annotations

import datetime as dt

from .. import ingest
from . import contract
from .clock import SimClock
from .log import Event, EventLog


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
        if ev.op == "D":
            table.pop(ev.key, None)
            self.row_seq[ev.entity].pop(ev.key, None)
        else:
            table[ev.key] = contract.decode(contract.ENTITIES[ev.entity], ev.after)
            self.row_seq[ev.entity][ev.key] = ev.seq
        self.last_source_ts = ev.source_ts

    def rows(self, entity: str) -> list[dict]:
        table = self.tables[entity]
        return [table[k] for k in sorted(table)]

    def source_data(self) -> ingest.SourceData:
        return ingest.build(
            {name: self.rows(name) for name in contract.ENTITIES},
            files={name: f"event log #{self.seq} · {name} ({len(self.tables[name])} rows)"
                   for name in contract.ENTITIES},
            as_of=self.clock.now())
