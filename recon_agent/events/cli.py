"""`python -m recon_agent events ...`: the producer side of the POC feed.

    events seed [--sales-date 2026-11-07] [--reset]   publish the synthetic starting day
    events status                                     log position, rows per entity, clock
    events tail [-n 20]                               latest events
    events show ENTITY [KEY]                          current rows (from the projection)
    events set ENTITY KEY "Column=value" ...          update columns of one row
    events insert ENTITY "Column=value" ...           insert a row (key column included)
    events delete ENTITY KEY                          delete a row
    events clock [--set T] [--advance 30m] [--rate 60] [--pause | --resume]
    events entities                                   the contract: entities and columns
    events reset                                      drop every event and the clock
    events scenario list | run S5 [--reset] [--pause 3]   the eight scripted scenarios
    events fault list | add KIND [--source WMS] [--entity E] [--seconds 60] [--batch 3] [--count 1]
    events fault remove ID | clear                    integration faults (delay, drop, duplicate, reorder)
    events deliver [--now] [--replay-dropped]         deliver what is due (or everything), replay drops

These work whatever RECON_SOURCE says, so a second terminal can change the data
while the worklist runs with RECON_SOURCE=events.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from .. import config
from . import contract, scenarios, seed
from .clock import SimClock, parse_duration
from .log import FAULT_KINDS, Change, EventLog
from .projector import Projector


def _assignments(entity: contract.Entity, pairs: list[str]) -> dict:
    row = {}
    for pair in pairs:
        col, sep, value = pair.partition("=")
        if not sep:
            sys.exit(f"Expected Column=value, got '{pair}'")
        col = col.strip()
        if col not in entity.columns:
            sys.exit(f"{entity.name} has no column '{col}'. See: events entities")
        row[col] = value
    return row


def _describe(ev) -> str:
    if ev.op == "U":
        diff = ", ".join(f"{k}: {ev.before.get(k)} -> {v}" for k, v in ev.after.items() if ev.before.get(k) != v)
    elif ev.op == "I":
        diff = "inserted"
    else:
        diff = "deleted"
    tag = f" [{ev.scenario}]" if ev.scenario and ev.scenario != "seed" else ""
    note = f" - {ev.note}" if ev.note else ""
    return (f"#{ev.seq:<5} {ev.source_ts:%m/%d %H:%M:%S}  {ev.op}  {ev.entity:<18} {ev.key:<10} "
            f"{diff}{tag}{note}  ({ev.producer})")


def _publish(log: EventLog, change: Change) -> None:
    try:
        ev = log.publish(change, producer="cli", now=SimClock(log).now())
    except contract.ContractError as e:
        sys.exit(f"Rejected: {e}")
    if ev is None:
        print("No change: the row already has those values.")
    elif ev.seq:
        print(_describe(ev))
    else:
        print(f"Committed as source change S{ev.source_seq}, not delivered yet: "
              f"{ev.deliveries[0]['fault'] if ev.deliveries else 'held'}.")


def _clock_line(clock: SimClock) -> str:
    s = clock.state()
    state = "paused" if s.paused else f"running x{s.rate:g}"
    return f"{clock.now():%a %Y-%m-%d %H:%M:%S} ({state})"


def run(args) -> None:
    log = EventLog(Path(args.db))
    log.pump()   # deliver anything a delay fault has made due, as the simulator's pump would
    clock = SimClock(log)
    cmd = args.events_cmd

    if cmd == "seed":
        if args.reset:
            log.reset()
        sales_date = dt.date.fromisoformat(args.sales_date)
        try:
            count = seed.seed(log, sales_date)
        except ValueError as e:
            sys.exit(str(e))
        print(f"Published {count} synthetic events for sales date {sales_date:%a %Y-%m-%d}.")
        print(f"Clock: {_clock_line(SimClock(log))}")
        print(f"Expected flags: {len(seed.EXPECTED)} (see recon_agent/events/seed.py). Log: {log.path}")

    elif cmd == "reset":
        log.reset()
        print(f"Event log cleared: {log.path}")

    elif cmd == "status":
        p = Projector(log, clock, stream="source")
        p.catch_up()
        log_id, head = log.position()
        i = log.integration()
        print(f"Log      {log.path}\n         id {log_id[:8]}, {head} events delivered, {p.seq} source changes, "
              f"contract v{contract.VERSION}")
        print(f"Faults   {len(log.faults())} active; {i['pending']} delayed, {i['held']} held, {i['dropped']} dropped")
        print(f"Clock    {_clock_line(clock)}")
        print(f"Feed     the worklist reads {'this log' if config.SOURCE_MODE == 'events' else 'the Excel extracts'}"
              f" (RECON_SOURCE={config.SOURCE_MODE})")
        for name, table in p.tables.items():
            print(f"  {name:<18} {len(table):>4} rows")

    elif cmd == "tail":
        for ev in log.tail(args.n):
            print(_describe(ev))

    elif cmd == "show":
        e = contract.entity(args.entity)
        p = Projector(log, clock, stream="source")   # the source system's own state
        p.catch_up()
        rows = p.tables[e.name]
        if args.key:
            row = rows.get(args.key)
            if not row:
                sys.exit(f"No {e.name} row {args.key}")
            width = max(map(len, row))
            for col, v in row.items():
                print(f"{col:<{width}}  {contract.to_json(v) if v is not None else ''}")
            return
        cols = args.columns.split(",") if args.columns else [e.key, *e.summary]
        print("  ".join(cols))
        for key in sorted(rows):
            print("  ".join(str(contract.to_json(rows[key].get(c))) for c in cols))

    elif cmd == "set":
        e = contract.entity(args.entity)
        _publish(log, Change(e.name, "U", args.key, _assignments(e, args.assignments), note=args.note))

    elif cmd == "insert":
        e = contract.entity(args.entity)
        row = _assignments(e, args.assignments)
        _publish(log, Change(e.name, "I", row.get(e.key), row, note=args.note))

    elif cmd == "delete":
        e = contract.entity(args.entity)
        _publish(log, Change(e.name, "D", args.key, note=args.note))

    elif cmd == "clock":
        try:
            if args.set:
                clock.set(dt.datetime.fromisoformat(args.set))
            if args.advance:
                clock.advance(parse_duration(args.advance))
            if args.rate:
                clock.set_rate(args.rate)
            if args.pause:
                clock.pause()
            if args.resume:
                clock.resume()
        except ValueError as e:
            sys.exit(str(e))
        print(f"Clock: {_clock_line(clock)}")

    elif cmd == "scenario":
        if args.action == "list" or not args.id:
            for sc in scenarios.SCENARIOS.values():
                print(f"{sc.id}  scenario {sc.number}: {sc.title}\n    {sc.story}\n    raises: {', '.join(sc.appears)}")
            return
        sc = scenarios.SCENARIOS.get(args.id.upper())
        if not sc:
            sys.exit(f"No scenario {args.id}. Known: {', '.join(scenarios.SCENARIOS)}")
        if args.reset:
            log.reset()
            seed.seed(log)
        try:
            scenarios.run(log, sc, pause=args.pause, on_step=lambda i, step, evs: print(
                f"Step {i + 1}/{len(sc.steps)}: {step.say} ({len(evs)} change(s))"))
        except contract.ContractError as e:
            sys.exit(f"Stopped: {e}")
        print(f"Done. Watch the worklist for: {', '.join(sc.appears)}")

    elif cmd == "fault":
        if args.action == "add":
            try:
                fid = log.add_fault(args.kind, args.source, args.entity, args.seconds, args.batch, args.count, args.note)
            except ValueError as e:
                sys.exit(str(e))
            print(f"Fault {fid} active.")
        elif args.action == "remove":
            log.remove_fault(args.id)
            print(f"Fault {args.id} removed.")
        elif args.action == "clear":
            log.remove_fault()
            print(f"All faults removed; {log.flush()} held change(s) delivered.")
        for f in log.faults():
            scope = f"{f['source']}/{f['entity']}".replace("*/*", "all").replace("/*", "")
            left = f["remaining"] if f["remaining"] is not None else "until removed"
            print(f"  #{f['id']} {f['kind']:<9} {scope:<24} seconds={f['seconds']} batch={f['batch']} left={left}")

    elif cmd == "deliver":
        if args.replay_dropped:
            print(f"Replayed {log.redeliver_dropped()} dropped change(s).")
        print(f"Delivered {log.flush() if args.now else log.pump()} change(s).")

    elif cmd == "entities":
        for e in contract.ENTITIES.values():
            print(f"{e.name}  ({e.source}) key: {e.key}{' (synthetic)' if e.synthetic_key else ''}")
            print(f"  {e.description}")
            for col, kind in e.columns.items():
                print(f"    {col:<38} {kind}{'  required' if col in e.required else ''}")
            print()


def add_parser(sub) -> None:
    p = sub.add_parser("events", help="Publish and inspect simulated source-system events")
    p.add_argument("--db", default=str(config.EVENT_DB), help="Event log file (default: RECON_EVENT_DB)")
    ev = p.add_subparsers(dest="events_cmd", required=True)

    s = ev.add_parser("seed", help="Publish the synthetic starting day into an empty log")
    s.add_argument("--sales-date", default=seed.DEFAULT_SALES_DATE.isoformat())
    s.add_argument("--reset", action="store_true", help="Clear the log first")
    ev.add_parser("reset", help="Drop every event and the clock")
    ev.add_parser("status", help="Log position, rows per entity and the clock")
    s = ev.add_parser("tail", help="Latest events")
    s.add_argument("-n", type=int, default=20)
    s = ev.add_parser("show", help="Current rows of an entity, or one row in full")
    s.add_argument("entity")
    s.add_argument("key", nargs="?")
    s.add_argument("--columns", help="Comma-separated columns to list")
    for name, help_text in (("set", "Update columns of one row"), ("insert", "Insert a row")):
        s = ev.add_parser(name, help=help_text)
        s.add_argument("entity")
        if name == "set":
            s.add_argument("key")
        s.add_argument("assignments", nargs="+", metavar="Column=value")
        s.add_argument("--note", help="Why (shown in tail)")
    s = ev.add_parser("delete", help="Delete one row")
    s.add_argument("entity")
    s.add_argument("key")
    s.add_argument("--note")
    s = ev.add_parser("clock", help="Show or control the simulated clock")
    s.add_argument("--set", metavar="YYYY-MM-DDTHH:MM")
    s.add_argument("--advance", metavar="DURATION", help="e.g. 15m, 2h, 1h30m, -10m")
    s.add_argument("--rate", type=float, help="Simulated seconds per real second")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--pause", action="store_true")
    g.add_argument("--resume", action="store_true")
    ev.add_parser("entities", help="The event contract: entities and their columns")
    s = ev.add_parser("scenario", help="List or run the scripted scenarios")
    s.add_argument("action", choices=["list", "run"])
    s.add_argument("id", nargs="?")
    s.add_argument("--reset", action="store_true", help="Reset to the starting day first")
    s.add_argument("--pause", type=float, default=0.0, help="Real seconds between steps")
    s = ev.add_parser("fault", help="Inject integration faults")
    s.add_argument("action", choices=["list", "add", "remove", "clear"])
    s.add_argument("kind", nargs="?", choices=FAULT_KINDS)
    s.add_argument("--id", type=int, help="Fault to remove")
    s.add_argument("--source", default="*")
    s.add_argument("--entity", default="*")
    s.add_argument("--seconds", type=float)
    s.add_argument("--batch", type=int)
    s.add_argument("--count", type=int, help="Changes to affect (default: until removed)")
    s.add_argument("--note")
    s = ev.add_parser("deliver", help="Deliver delayed changes that are due")
    s.add_argument("--now", action="store_true", help="Deliver everything delayed or held now")
    s.add_argument("--replay-dropped", action="store_true", help="Replay dropped changes from the source")
    p.set_defaults(func=run)
