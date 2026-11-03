"""Event log in SQLite: the stand-in for the source systems' change capture, the
integration (GoldenGate / OIC) and OCI Streaming during the POC.

Two streams live in one file:

- `source_changes`: every change the source systems committed, in commit order
  (`source_seq`). Producers (seed, CLI, simulator, scenarios, write-back) validate
  against this state: it is the truth of MIPS, ODI, WMS and Oracle.
- `events`: what the integration delivered to consumers, in delivery order (`seq`).
  The worklist reads only this stream. It is append-only (triggers enforce it).

Between them sits the delivery queue (`deliveries`). Without faults a change is
delivered in the same transaction it is produced. Active faults (`faults`) can
delay, drop, duplicate or reorder deliveries, as a real integration can; `pump`
delivers what is due. Every delivered copy of a change keeps its `event_id` and
`source_seq`, so a consumer can ignore duplicates and stale images.

Readers commit how far they have processed (as a Kafka consumer commits offsets),
which gives producers the reader's lag and event-to-screen latency. `reset` clears
everything and gives the log a new `log_id` so readers start over.

Each call opens its own connection, so one EventLog is safe to share between
threads. WAL mode lets the app read while a producer writes. Keep every process
that opens the file on one machine (or one Docker volume): SQLite locking does
not work across a Windows bind mount.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import contract

TABLES = ("events", "source_changes", "deliveries", "faults", "commits", "sim_clock", "meta")
FAULT_KINDS = ("delay", "drop", "duplicate", "reorder")

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_changes (
    source_seq  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL,
    entity      TEXT NOT NULL,
    op          TEXT NOT NULL CHECK (op IN ('I', 'U', 'D')),
    key         TEXT NOT NULL,
    before      TEXT,
    after       TEXT,
    source_ts   TEXT NOT NULL,
    produced_at TEXT NOT NULL,
    producer    TEXT NOT NULL,
    scenario    TEXT,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS source_entity_key ON source_changes (entity, key, source_seq);
CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL,
    source_seq  INTEGER NOT NULL,
    copy        INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL,
    entity      TEXT NOT NULL,
    op          TEXT NOT NULL CHECK (op IN ('I', 'U', 'D')),
    key         TEXT NOT NULL,
    before      TEXT,
    after       TEXT,
    source_ts   TEXT NOT NULL,
    emitted_ts  TEXT NOT NULL,
    producer    TEXT NOT NULL,
    scenario    TEXT,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS events_entity_key ON events (entity, key, seq);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
    BEGIN SELECT RAISE(ABORT, 'the event log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
    BEGIN SELECT RAISE(ABORT, 'the event log is append-only'); END;
CREATE TABLE IF NOT EXISTS deliveries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_seq  INTEGER NOT NULL,
    copy        INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL CHECK (status IN ('pending', 'held', 'delivered', 'dropped')),
    deliver_at  REAL,
    fault_id    INTEGER,
    fault       TEXT,
    seq         INTEGER
);
CREATE INDEX IF NOT EXISTS deliveries_open ON deliveries (status, deliver_at);
CREATE TABLE IF NOT EXISTS faults (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '*',
    entity      TEXT NOT NULL DEFAULT '*',
    seconds     REAL,
    batch       INTEGER,
    remaining   INTEGER,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    note        TEXT
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commits (
    consumer TEXT NOT NULL, seq INTEGER NOT NULL, processed_at TEXT NOT NULL, PRIMARY KEY (consumer, seq)
);
CREATE TABLE IF NOT EXISTS sim_clock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    anchor_sim TEXT NOT NULL, anchor_wall REAL NOT NULL, rate REAL NOT NULL, paused INTEGER NOT NULL
);
"""


@dataclass
class Event:
    """A change as delivered (stream "events") or as produced (stream "source", seq = source_seq)."""
    seq: int
    event_id: str
    source: str
    entity: str
    op: str
    key: str
    before: dict | None     # JSON row images, as stored
    after: dict | None
    source_ts: dt.datetime
    emitted_ts: dt.datetime   # delivered (events) or produced (source changes), wall clock
    producer: str
    scenario: str | None = None
    note: str | None = None
    source_seq: int = 0
    copy: int = 0             # 0 = first delivery, 1+ = duplicate delivered by a fault
    deliveries: list[dict] = field(default_factory=list)   # source stream only: what became of it


@dataclass
class Change:
    """A change a producer wants to make. `row` is the full image for an insert and
    only the changed columns for an update; a delete needs just the key."""
    entity: str
    op: str
    key: str | None = None
    row: dict | None = None
    source_ts: dt.datetime | None = None
    scenario: str | None = None
    note: str | None = None


def _loads(text: str | None) -> dict | None:
    return json.loads(text) if text else None


def _event(r: sqlite3.Row) -> Event:
    return Event(r["seq"], r["event_id"], r["source"], r["entity"], r["op"], r["key"], _loads(r["before"]),
                 _loads(r["after"]), dt.datetime.fromisoformat(r["source_ts"]),
                 dt.datetime.fromisoformat(r["emitted_ts"]), r["producer"], r["scenario"], r["note"],
                 r["source_seq"], r["copy"])


def _source_event(r: sqlite3.Row) -> Event:
    return Event(r["source_seq"], r["event_id"], r["source"], r["entity"], r["op"], r["key"], _loads(r["before"]),
                 _loads(r["after"]), dt.datetime.fromisoformat(r["source_ts"]),
                 dt.datetime.fromisoformat(r["produced_at"]), r["producer"], r["scenario"], r["note"],
                 r["source_seq"])


def _wall(t: float | None = None) -> str:
    return dt.datetime.fromtimestamp(time.time() if t is None else t).isoformat(timespec="milliseconds")


class EventLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con:
            has_meta = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'meta'").fetchone()
            version = has_meta and con.execute("SELECT v FROM meta WHERE k = 'contract_version'").fetchone()
            if has_meta and (not version or version[0] != str(contract.VERSION)):
                print(f"Event log {self.path} uses contract v{version[0] if version else '?'}; "
                      f"recreating it empty for v{contract.VERSION}. Publish the seed again (events seed).")
                for t in TABLES:
                    con.execute(f"DROP TABLE IF EXISTS {t}")
            con.executescript(SCHEMA)
            con.execute("INSERT OR IGNORE INTO meta VALUES ('log_id', ?)", (uuid.uuid4().hex,))
            con.execute("INSERT OR IGNORE INTO meta VALUES ('contract_version', ?)", (str(contract.VERSION),))

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=10000")
        return con

    @contextmanager
    def _write(self):
        """One write transaction, taken up front so a read-then-append cannot interleave."""
        with closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise

    # --- reading -----------------------------------------------------------
    def position(self) -> tuple[str, int]:
        """(log_id, last delivered seq). Moves whenever an event is delivered or the log is reset."""
        with closing(self._connect()) as con:
            log_id = con.execute("SELECT v FROM meta WHERE k = 'log_id'").fetchone()[0]
            head = con.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        return log_id, head

    def source_position(self) -> tuple[str, int]:
        """(log_id, last source_seq): moves whenever a source system commits a change."""
        with closing(self._connect()) as con:
            log_id = con.execute("SELECT v FROM meta WHERE k = 'log_id'").fetchone()[0]
            head = con.execute("SELECT COALESCE(MAX(source_seq), 0) FROM source_changes").fetchone()[0]
        return log_id, head

    def read_since(self, seq: int, limit: int | None = None, stream: str = "events") -> list[Event]:
        """Delivered events after `seq`, or (stream="source") source changes after source_seq `seq`."""
        table, col, conv = (("events", "seq", _event) if stream == "events"
                            else ("source_changes", "source_seq", _source_event))
        sql = f"SELECT * FROM {table} WHERE {col} > ? ORDER BY {col}" + (" LIMIT ?" if limit else "")
        with closing(self._connect()) as con:
            return [conv(r) for r in con.execute(sql, (seq, limit) if limit else (seq,))]

    def tail(self, n: int = 20) -> list[Event]:
        with closing(self._connect()) as con:
            rows = con.execute("SELECT * FROM events ORDER BY seq DESC LIMIT ?", (n,)).fetchall()
        return [_event(r) for r in reversed(rows)]

    def source_tail(self, n: int = 30) -> list[Event]:
        """Latest source changes, newest last, each with what the integration did with it."""
        with closing(self._connect()) as con:
            rows = con.execute("SELECT * FROM source_changes ORDER BY source_seq DESC LIMIT ?", (n,)).fetchall()
            out = [_source_event(r) for r in reversed(rows)]
            if out:
                deliveries = con.execute(
                    "SELECT * FROM deliveries WHERE source_seq >= ? ORDER BY copy, id", (out[0].source_seq,)).fetchall()
                by_seq: dict[int, list[dict]] = {}
                for d in deliveries:
                    by_seq.setdefault(d["source_seq"], []).append(dict(d))
                for ev in out:
                    ev.deliveries = by_seq.get(ev.source_seq, [])
        return out

    def current(self, entity: str, key: str, con: sqlite3.Connection | None = None) -> dict | None:
        """The row image in the source system after its last change (None if absent or deleted)."""
        sql = "SELECT op, after FROM source_changes WHERE entity = ? AND key = ? ORDER BY source_seq DESC LIMIT 1"
        if con is None:
            with closing(self._connect()) as c:
                r = c.execute(sql, (entity, key)).fetchone()
        else:
            r = con.execute(sql, (entity, key)).fetchone()
        return json.loads(r["after"]) if r and r["op"] != "D" else None

    # --- publishing --------------------------------------------------------
    def publish(self, change: Change, producer: str, now: dt.datetime, faults: bool = True) -> Event | None:
        return self.publish_many([change], producer, now, faults)[0]

    def publish_many(self, changes: list[Change], producer: str, now: dt.datetime,
                     faults: bool = True) -> list[Event | None]:
        """Validate changes against the source state, commit them, and hand each to the
        integration. All or nothing.

        Returns the source change for each (None for an update that changes nothing,
        as CDC does not capture a no-op). `seq` on the result is the delivered event
        number, or 0 when a fault held or dropped it (see `deliveries`). `now` is the
        simulated time used when a change has no source_ts. faults=False bypasses
        injected faults (the seed).
        """
        self.pump()
        out: list[Event | None] = []
        with self._write() as con:
            pending: dict[tuple[str, str], dict | None] = {}   # images written earlier in this batch
            active = self._active_faults(con) if faults else []
            for c in changes:
                e = contract.entity(c.entity)
                if c.op not in contract.OPS:
                    raise contract.ContractError(f"op must be one of {contract.OPS}, got {c.op!r}")
                key = str(c.key if c.key is not None else (c.row or {}).get(e.key, ""))
                if not key:
                    raise contract.ContractError(f"{e.name}: no key ({e.key})")
                cur = pending[(e.name, key)] if (e.name, key) in pending else self.current(e.name, key, con)
                if c.op == "I":
                    if cur is not None:
                        raise contract.ContractError(f"{e.name} {key} already exists; update it instead")
                    after = contract.encode(contract.validate(e, {**(c.row or {}), e.key: key}))
                elif cur is None:
                    raise contract.ContractError(f"{e.name} {key} does not exist")
                elif c.op == "U":
                    if e.key in (c.row or {}) and str(c.row[e.key]) != key:
                        raise contract.ContractError(f"{e.name}: the key column {e.key} cannot change")
                    after = contract.encode(contract.validate(e, {**cur, **(c.row or {})}))
                    if after == cur:
                        out.append(None)
                        continue
                else:
                    after = None
                ts = c.source_ts or now
                ev = Event(0, uuid.uuid4().hex, e.source, e.name, c.op, key, cur, after, ts, dt.datetime.now(),
                           producer, c.scenario, c.note)
                ev.source_seq = con.execute(
                    "INSERT INTO source_changes (event_id, source, entity, op, key, before, after, source_ts,"
                    " produced_at, producer, scenario, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ev.event_id, ev.source, ev.entity, ev.op, ev.key,
                     json.dumps(cur) if cur is not None else None, json.dumps(after) if after is not None else None,
                     ts.isoformat(timespec="seconds"), ev.emitted_ts.isoformat(timespec="milliseconds"),
                     producer, c.scenario, c.note)).lastrowid
                ev.seq = self._route(con, ev, active)
                ev.deliveries = [dict(d) for d in con.execute(
                    "SELECT * FROM deliveries WHERE source_seq = ? ORDER BY copy", (ev.source_seq,))]
                pending[(e.name, key)] = after
                out.append(ev)
            self._release_reorders(con)
        return out

    # --- integration: faults and delivery -------------------------------------
    def _active_faults(self, con) -> list[sqlite3.Row]:
        return con.execute("SELECT * FROM faults WHERE active = 1 ORDER BY id").fetchall()

    def _route(self, con, ev: Event, active: list[sqlite3.Row]) -> int:
        """Queue the delivery of one source change through the active faults. Returns its seq if delivered now."""
        matching = [f for f in active if f["source"] in ("*", ev.source) and f["entity"] in ("*", ev.entity)
                    and (f["remaining"] is None or f["remaining"] > 0)]
        by_kind = {f["kind"]: f for f in reversed(matching)}   # the oldest fault of each kind applies
        used = []
        if "drop" in by_kind:
            f = by_kind["drop"]
            con.execute("INSERT INTO deliveries (source_seq, status, fault_id, fault) VALUES (?, 'dropped', ?, ?)",
                        (ev.source_seq, f["id"], "dropped"))
            used.append(f)
            seq = 0
        else:
            now = time.time()
            if "reorder" in by_kind:
                f = by_kind["reorder"]
                con.execute("INSERT INTO deliveries (source_seq, status, fault_id, fault) VALUES (?, 'held', ?, ?)",
                            (ev.source_seq, f["id"], f"held for reorder (batch of {f['batch']})"))
                used.append(f)
                seq = 0
            elif "delay" in by_kind:
                f = by_kind["delay"]
                con.execute("INSERT INTO deliveries (source_seq, status, deliver_at, fault_id, fault)"
                            " VALUES (?, 'pending', ?, ?, ?)",
                            (ev.source_seq, now + f["seconds"], f["id"], f"delayed {f['seconds']:g} s"))
                used.append(f)
                seq = 0
            else:
                did = con.execute("INSERT INTO deliveries (source_seq, status) VALUES (?, 'pending')",
                                  (ev.source_seq,)).lastrowid
                seq = self._deliver(con, did)
            if "duplicate" in by_kind:
                f = by_kind["duplicate"]
                con.execute("INSERT INTO deliveries (source_seq, copy, status, deliver_at, fault_id, fault)"
                            " VALUES (?, 1, 'pending', ?, ?, ?)",
                            (ev.source_seq, now + (f["seconds"] or 0), f["id"], "duplicate copy"))
                used.append(f)
        for f in used:
            if f["remaining"] is not None:
                con.execute("UPDATE faults SET remaining = remaining - 1, active = remaining - 1 > 0 WHERE id = ?",
                            (f["id"],))
        return seq

    def _deliver(self, con, delivery_id: int) -> int:
        d = con.execute("SELECT * FROM deliveries WHERE id = ?", (delivery_id,)).fetchone()
        s = con.execute("SELECT * FROM source_changes WHERE source_seq = ?", (d["source_seq"],)).fetchone()
        seq = con.execute(
            "INSERT INTO events (event_id, source_seq, copy, source, entity, op, key, before, after, source_ts,"
            " emitted_ts, producer, scenario, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (s["event_id"], s["source_seq"], d["copy"], s["source"], s["entity"], s["op"], s["key"], s["before"],
             s["after"], s["source_ts"], _wall(), s["producer"], s["scenario"], s["note"])).lastrowid
        con.execute("UPDATE deliveries SET status = 'delivered', seq = ?, deliver_at = ? WHERE id = ?",
                    (seq, time.time(), delivery_id))
        return seq

    def _release_reorders(self, con, force: bool = False) -> None:
        """A reorder fault holds deliveries until it has a full batch, then delivers them newest first."""
        for f in con.execute("SELECT DISTINCT fault_id FROM deliveries WHERE status = 'held'").fetchall():
            fault = con.execute("SELECT * FROM faults WHERE id = ?", (f["fault_id"],)).fetchone()
            held = con.execute("SELECT id FROM deliveries WHERE status = 'held' AND fault_id = ? ORDER BY source_seq DESC",
                               (f["fault_id"],)).fetchall()
            if force or fault is None or not fault["active"] or len(held) >= (fault["batch"] or 2):
                for d in held:
                    self._deliver(con, d["id"])

    def pump(self) -> int:
        """Deliver every delayed or duplicate copy that is due. Returns how many were delivered."""
        with closing(self._connect()) as con:
            due = con.execute("SELECT 1 FROM deliveries WHERE status = 'pending' AND deliver_at <= ? LIMIT 1",
                              (time.time(),)).fetchone()
        if not due:
            return 0
        with self._write() as con:
            ids = [r["id"] for r in con.execute(
                "SELECT id FROM deliveries WHERE status = 'pending' AND deliver_at <= ? ORDER BY deliver_at, id",
                (time.time(),))]
            for i in ids:
                self._deliver(con, i)
        return len(ids)

    def flush(self) -> int:
        """Deliver everything delayed, duplicated or held right now (dropped changes stay dropped)."""
        with self._write() as con:
            ids = [r["id"] for r in con.execute(
                "SELECT id FROM deliveries WHERE status = 'pending' ORDER BY deliver_at, id")]
            for i in ids:
                self._deliver(con, i)
            held = con.execute("SELECT COUNT(*) FROM deliveries WHERE status = 'held'").fetchone()[0]
            self._release_reorders(con, force=True)
        return len(ids) + held

    def redeliver_dropped(self) -> int:
        """Replay dropped changes from the source, in source order (as re-running an integration does)."""
        with self._write() as con:
            ids = [r["id"] for r in con.execute(
                "SELECT id FROM deliveries WHERE status = 'dropped' ORDER BY source_seq")]
            for i in ids:
                self._deliver(con, i)
        return len(ids)

    def add_fault(self, kind: str, source: str = "*", entity: str = "*", seconds: float | None = None,
                  batch: int | None = None, remaining: int | None = None, note: str | None = None) -> int:
        if kind not in FAULT_KINDS:
            raise ValueError(f"fault must be one of {', '.join(FAULT_KINDS)}")
        if source != "*" and source not in contract.SOURCES:
            raise ValueError(f"source must be * or one of {', '.join(contract.SOURCES)}")
        if entity != "*":
            contract.entity(entity)
        if kind == "delay" and not (seconds and seconds > 0):
            raise ValueError("a delay needs a positive number of seconds")
        if kind == "reorder" and not (batch and batch >= 2):
            raise ValueError("a reorder needs a batch of at least 2 changes")
        if remaining is not None and remaining < 1:
            raise ValueError("count must be at least 1 (or empty for 'until removed')")
        with self._write() as con:
            return con.execute(
                "INSERT INTO faults (kind, source, entity, seconds, batch, remaining, created_at, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, source, entity, seconds, batch, remaining, _wall(), note)).lastrowid

    def faults(self, active_only: bool = True) -> list[dict]:
        with closing(self._connect()) as con:
            sql = "SELECT * FROM faults" + (" WHERE active = 1" if active_only else "") + " ORDER BY id"
            return [dict(r) for r in con.execute(sql)]

    def remove_fault(self, fault_id: int | None = None) -> None:
        """Switch one fault (or all) off. Held reorder batches are released."""
        with self._write() as con:
            if fault_id is None:
                con.execute("UPDATE faults SET active = 0")
            else:
                con.execute("UPDATE faults SET active = 0 WHERE id = ?", (fault_id,))
            self._release_reorders(con)

    def integration(self) -> dict:
        """What the integration is holding back, for the operator."""
        with closing(self._connect()) as con:
            counts = {r["status"]: r["n"] for r in con.execute(
                "SELECT status, COUNT(*) AS n FROM deliveries WHERE status <> 'delivered' GROUP BY status")}
            dups = con.execute("SELECT COUNT(*) FROM deliveries WHERE copy > 0 AND status = 'delivered'").fetchone()[0]
        return {"pending": counts.get("pending", 0), "held": counts.get("held", 0),
                "dropped": counts.get("dropped", 0), "duplicates_delivered": dups}

    def reset(self) -> None:
        """Clear every change, delivery, fault, commit and the clock, and start a new log_id."""
        with self._write() as con:
            con.execute("DROP TABLE events")   # DROP does not fire the delete trigger
            for t in ("source_changes", "deliveries", "faults", "sim_clock", "commits"):
                con.execute(f"DELETE FROM {t}")
            con.execute("DELETE FROM sqlite_sequence WHERE name IN ('source_changes', 'deliveries', 'faults')")
            con.execute("UPDATE meta SET v = ? WHERE k = 'log_id'", (uuid.uuid4().hex,))
        with closing(self._connect()) as con:
            con.executescript(SCHEMA)

    # --- consumer progress ---------------------------------------------------
    def commit(self, consumer: str, seq: int, processed_at: dt.datetime) -> None:
        """Record that `consumer` has fully processed every event up to `seq`."""
        with self._write() as con:
            con.execute("INSERT OR IGNORE INTO commits VALUES (?, ?, ?)",
                        (consumer, seq, processed_at.isoformat(timespec="milliseconds")))

    def committed(self, consumer: str) -> tuple[int, dt.datetime | None]:
        with closing(self._connect()) as con:
            r = con.execute("SELECT seq, processed_at FROM commits WHERE consumer = ? ORDER BY seq DESC LIMIT 1",
                            (consumer,)).fetchone()
        return (r["seq"], dt.datetime.fromisoformat(r["processed_at"])) if r else (0, None)

    def latencies(self, consumer: str, n: int = 200) -> list[tuple[int, float | None]]:
        """(seq, seconds from delivery to processed by `consumer`) for the latest n events.

        None while the event is still unprocessed. Seed events and duplicate copies
        are left out: the seed is published in bulk before any reader runs.
        """
        sql = """SELECT e.seq, e.emitted_ts,
                        (SELECT MIN(c.processed_at) FROM commits c WHERE c.consumer = ? AND c.seq >= e.seq) AS done
                 FROM events e WHERE e.producer <> 'seed' AND e.copy = 0 ORDER BY e.seq DESC LIMIT ?"""
        with closing(self._connect()) as con:
            rows = con.execute(sql, (consumer, n)).fetchall()
        return [(r["seq"], (dt.datetime.fromisoformat(r["done"]) - dt.datetime.fromisoformat(r["emitted_ts"]))
                 .total_seconds() if r["done"] else None) for r in rows]

    # --- simulated clock state -----------------------------------------------
    def clock_row(self) -> sqlite3.Row | None:
        with closing(self._connect()) as con:
            return con.execute("SELECT * FROM sim_clock WHERE id = 1").fetchone()

    def set_clock_row(self, anchor_sim: dt.datetime, rate: float, paused: bool) -> None:
        with self._write() as con:
            con.execute("INSERT OR REPLACE INTO sim_clock VALUES (1, ?, ?, ?, ?)",
                        (anchor_sim.isoformat(), time.time(), rate, int(paused)))
