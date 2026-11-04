"""Source simulator: the operator app that stands in for MIPS, ODI/Oracle and WMS.

It shows the source systems' own state (every committed change, delivered or
not), lets an operator change, insert or delete rows, plays the scripted
scenarios, injects integration faults (delay, drop, duplicate, reorder), and
stands in for Oracle Integration receiving the worklist's approved corrections
(POST /oic/corrections). It is a separate app from the worklist and shares
nothing with it but the event log: it never reads the worklist's state, and the
worklist never reads this app's. Only the worklist's consumer commits flow back
(through the log), which gives the operator the worklist's lag and latency.

Run it with `python -m recon_agent simulate` (port 8100). Operators sign in with
the worklist's accounts (users.py). Synthetic data only; never deploy it next to a
production worklist.
"""
from __future__ import annotations

import datetime as dt
import re
import statistics
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__, config, security, sources, users
from ..events import contract, scenarios, seed
from ..events.clock import SimClock, parse_duration
from ..events.derive import DERIVED, derive, order_totals
from ..events.log import FAULT_KINDS, Change, Event, EventLog
from ..events.projector import Projector

HERE = Path(__file__).parent
PRODUCER = "simulator"
SESSION_COOKIE = "sim_session"
SYSTEMS = {s: [e for e in contract.ENTITIES.values() if e.source == s] for s in contract.SOURCES}
RATES = (1, 10, 60, 300)
OIC_MODES = {"accept": "Accept and apply", "reject": "Reject every correction", "down": "Unavailable (503)"}
FAULT_PRESETS = (
    ("Stale WMS feed", "Every WMS change reaches the worklist 60 s late", {"kind": "delay", "source": "WMS", "seconds": "60"}),
    ("Lose the next Oracle posting", "The next Oracle change never arrives", {"kind": "drop", "source": "ORACLE", "count": "1"}),
    ("At-least-once delivery", "Every change arrives twice, the copy 3 s later", {"kind": "duplicate", "seconds": "3"}),
    ("Out-of-order delivery", "Changes arrive in batches of 3, newest first", {"kind": "reorder", "batch": "3"}),
)


def published(ev: Event | None) -> str:
    """What happened to a change, for the operator."""
    if ev is None:
        return "No change: the row already has those values."
    if ev.seq:
        return f"Published event #{ev.seq} (source change S{ev.source_seq})."
    d = ev.deliveries[0] if ev.deliveries else {}
    return f"Committed source change S{ev.source_seq}, but the integration {d.get('fault') or 'holds it'}: the worklist does not see it yet."


# --- formatting ----------------------------------------------------------------

def show(v) -> str:
    """A cell value for reading."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, dt.datetime):
        return v.strftime("%m/%d %H:%M") if v.time() != dt.time() else v.strftime("%Y-%m-%d")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.4f}".rstrip("0")
    if isinstance(v, (int, float)):
        return f"{v:,.0f}"
    return str(v)


def field_value(v, kind: str) -> str:
    """A value for an <input>. Strings pass through (an operator's input shown back after an error)."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if kind == contract.DATE:
        return (v.date() if isinstance(v, dt.datetime) else v).isoformat()
    if kind == contract.DATETIME:
        return v.strftime("%Y-%m-%dT%H:%M:%S")
    if kind == contract.BOOL:
        return "true" if v else "false"
    return str(contract.to_json(v))


def describe(ev: Event) -> list[tuple[str, str, str]]:
    """(column, before, after) for every column an update changed."""
    if ev.op != "U":
        return []
    e = contract.ENTITIES[ev.entity]
    before, after = contract.decode(e, ev.before), contract.decode(e, ev.after)
    return [(c, show(before[c]), show(after[c])) for c in e.columns if before[c] != after[c]]


def next_key(e: contract.Entity, keys: list[str]) -> str:
    """The next free key in the entity's own pattern (ML-0018, 9900005, ...)."""
    best = None
    for k in keys:
        m = re.fullmatch(r"(.*?)(\d+)", k)
        if m and (best is None or int(m[2]) > int(best[2])):
            best = m
    if not best:
        return ""
    return f"{best[1]}{int(best[2]) + 1:0{len(best[2])}d}"


def percentile(values: list[float], q: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


# --- app ------------------------------------------------------------------------

def create_app(db_path: Path | None = None) -> FastAPI:
    log = EventLog(Path(db_path or config.EVENT_DB))
    clock = SimClock(log)
    projector = Projector(log, clock, stream="source")   # the source systems' truth, delivered or not
    oic = {"mode": "accept"}
    run_lock = threading.Lock()
    run_state: dict = {"id": None, "running": False, "lines": [], "error": "", "done": False}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()

        def pump():   # the integration: deliver delayed and duplicate copies when they are due
            while not stop.wait(0.25):
                try:
                    log.pump()
                except Exception as e:
                    print(f"Delivery pump: {e}")

        threading.Thread(target=pump, daemon=True, name="delivery-pump").start()
        yield
        stop.set()

    if not config.USERS_FILE.exists():
        print(f"No accounts yet ({config.USERS_FILE}): start the worklist once, or run "
              "`python -m recon_agent users seed`.")
    failed_by_user = security.RateLimiter(config.SIGNIN_ATTEMPTS, config.SIGNIN_WINDOW_SECONDS)
    failed_by_address = security.RateLimiter(config.SIGNIN_ATTEMPTS_PER_ADDRESS, config.SIGNIN_WINDOW_SECONDS)
    app = FastAPI(title="Source simulator", version=__version__, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    security.harden(app, favicon="/sim-static/favicon.svg")
    app.mount("/static", StaticFiles(directory=HERE.parent / "web" / "static"), name="static")
    app.mount("/sim-static", StaticFiles(directory=HERE / "static"), name="sim-static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters.update(show=show)
    templates.env.globals.update(SYSTEMS=SYSTEMS, ENTITIES=contract.ENTITIES, DERIVED=DERIVED, RATES=RATES,
                                 config=config, field_value=field_value, describe=describe, contract=contract,
                                 SCENARIOS=scenarios.SCENARIOS, FAULT_KINDS=FAULT_KINDS, FAULT_PRESETS=FAULT_PRESETS,
                                 OIC_MODES=OIC_MODES)

    def signed_in(request: Request) -> str:
        """The signed-in operator's name, or "" (same accounts as the worklist)."""
        session = security.unsign(request.cookies.get(SESSION_COOKIE), "simulator")
        account = users.get(session["u"]) if session else None
        return account["name"] if account and account["session"] == session.get("s") else ""

    def operator(request: Request) -> str:
        name = signed_in(request)
        if not name:
            target = "/signin?next=" + quote(str(request.url.path))
            raise HTTPException(status_code=303, headers={"Location": target, "HX-Redirect": target})
        return name

    def entity_or_404(name: str) -> contract.Entity:
        if name not in contract.ENTITIES:
            raise HTTPException(status_code=404, detail=f"No entity {name}")
        return contract.ENTITIES[name]

    def status() -> dict:
        log_id, head = log.position()
        seq, at = log.committed(sources.CONSUMER)
        samples = log.latencies(sources.CONSUMER, 100)
        done = [s for _, s in samples if s is not None]
        return {"head": head, "log_id": log_id[:8], "committed": seq, "committed_at": at,
                "lag": max(head - seq, 0), "pending": sum(1 for _, s in samples if s is None),
                "samples": len(done),
                "p50": statistics.median(done) if done else None,
                "p95": percentile(done, 0.95) if done else None,
                "max": max(done) if done else None,
                "worklist_live": at is not None and (seq == head or (dt.datetime.now() - at).total_seconds() < 120),
                "integration": log.integration(), "faults": len(log.faults()), "oic": oic["mode"]}

    def ctx(request: Request, e: contract.Entity | None = None, page: str = "table", **extra) -> dict:
        projector.catch_up()
        e = e or contract.ENTITIES["mips.order_line"]
        return {"e": e, "page": page, "rows": projector.rows(e.name), "row_seq": projector.row_seq[e.name],
                "counts": {n: len(t) for n, t in projector.tables.items()}, "clock": clock, "now": clock.now(),
                "status": status(), "feed": log.source_tail(30)[::-1], "head": projector.seq,
                "operator": signed_in(request), "run": run_state, **extra}

    def render(request: Request, name: str, **c) -> HTMLResponse:
        return templates.TemplateResponse(request, name, c)

    def editor(request: Request, e: contract.Entity, key: str | None, *, mode: str = "edit", values: dict | None = None,
               message: str = "", error: str = "", oob: bool = False) -> HTMLResponse:
        c = ctx(request, e)
        row = projector.tables[e.name].get(key) if key else None
        if mode == "edit" and row is None:
            mode, error = "gone", error or f"{e.key} {key} no longer exists (deleted)."
        if values is None:
            values = dict(row) if row else {c_: None for c_ in e.columns}
        return render(request, "_editor_response.html", **c, key=key, row=row, mode=mode, values=values,
                      base_seq=projector.row_seq[e.name].get(key, 0), message=message, error=error, oob=oob)

    def raw_form(e: contract.Entity, form) -> dict:
        """Column -> submitted text, from the editor's fields (named f0, f1, ... in column order)."""
        return {col: form.get(f"f{i}") for i, col in enumerate(e.columns) if form.get(f"f{i}") is not None}

    def typed_form(e: contract.Entity, form) -> dict:
        """Column values from the editor, typed as the projector holds them (dates as datetimes)."""
        out = {}
        for col, raw in raw_form(e, form).items():
            kind = e.columns[col]
            raw = raw if kind == contract.STR else raw.strip()
            v = contract.coerce(raw if raw != "" or kind == contract.STR else None, kind)
            out[col] = dt.datetime.combine(v, dt.time()) if kind == contract.DATE and v is not None else v
        return out

    def same(a, b) -> bool:
        return a == b or a in ("", None) and b in ("", None)

    # --- identity --------------------------------------------------------------
    def signin_form(request: Request, target: str, username: str = "", error: str = "", status: int = 200):
        resp = render(request, "signin.html", next=target, error=error, username=username)
        resp.status_code = status
        return resp

    @app.get("/signin", response_class=HTMLResponse)
    def signin_page(request: Request, next: str = "/"):
        return signin_form(request, security.safe_next(next))

    @app.post("/signin")
    def signin(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
        username, target = username.strip().lower()[:40], security.safe_next(next)
        address = security.client_ip(request)
        if failed_by_user.blocked(username) or failed_by_address.blocked(address):
            return signin_form(request, target, username, "Too many failed sign-ins. Wait 15 minutes and try again.", 429)
        account = users.authenticate(username, password)
        if not account:
            failed_by_user.hit(username)
            failed_by_address.hit(address)
            return signin_form(request, target, username, "That username and password do not match.", 401)
        failed_by_user.reset(username)
        resp = RedirectResponse(target, status_code=303)
        resp.set_cookie(SESSION_COOKIE, security.sign({"u": account["username"], "s": account["session"]}, "simulator"),
                        max_age=int(config.SESSION_HOURS * 3600), httponly=True, samesite="lax",
                        secure=config.COOKIE_SECURE)
        return resp

    @app.post("/signout")
    def signout():
        resp = RedirectResponse("/signin", status_code=303)
        resp.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax", secure=config.COOKIE_SECURE)
        return resp

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "version": __version__}

    # --- pages -----------------------------------------------------------------
    @app.get("/")
    def home(request: Request):
        operator(request)
        return RedirectResponse("/t/mips.order_line", status_code=303)

    @app.get("/t/{entity}", response_class=HTMLResponse)
    def table_page(request: Request, entity: str, key: str = "", new: str = ""):
        operator(request)
        e = entity_or_404(entity)
        c = ctx(request, e)
        mode = "new" if new else "edit" if key in projector.tables[e.name] else "none"
        values = None
        if mode == "new":
            base = projector.tables[e.name].get(new) or {}
            values = {**{c_: None for c_ in e.columns}, **base,
                      e.key: next_key(e, list(projector.tables[e.name]))}
        elif mode == "edit":
            values = dict(projector.tables[e.name][key])
        return render(request, "index.html", **c, key=key, row=projector.tables[e.name].get(key), mode=mode,
                      values=values, base_seq=projector.row_seq[e.name].get(key, 0), message="", error="",
                      oob=False, clone_of=new)

    @app.get("/t/{entity}/edit/{key}", response_class=HTMLResponse)
    def edit_row(request: Request, entity: str, key: str):
        operator(request)
        return editor(request, entity_or_404(entity), key)

    @app.get("/t/{entity}/new", response_class=HTMLResponse)
    def new_row(request: Request, entity: str, clone: str = ""):
        operator(request)
        e = entity_or_404(entity)
        projector.catch_up()
        base = projector.tables[e.name].get(clone) or {}
        values = {**{c_: None for c_ in e.columns}, **base, e.key: next_key(e, list(projector.tables[e.name]))}
        return editor(request, e, None, mode="new", values=values,
                      message=f"Copy of {clone}: change what differs, then insert." if base else "")

    # --- publishing ------------------------------------------------------------
    @app.post("/t/{entity}/edit/{key}", response_class=HTMLResponse)
    async def save_row(request: Request, entity: str, key: str):
        who = operator(request)
        e = entity_or_404(entity)
        form = await request.form()
        projector.catch_up()
        current = projector.tables[e.name].get(key)
        if current is None:
            return editor(request, e, key, error=f"{e.key} {key} was deleted meanwhile.")
        if projector.row_seq[e.name].get(key, 0) != int(form.get("base_seq") or 0):
            return editor(request, e, key, error="This row changed (event "
                          f"#{projector.row_seq[e.name][key]}) after you opened it. Its current values are "
                          "shown; make your change again.")
        try:
            values = {**current, **typed_form(e, form)}
        except (ValueError, TypeError) as err:
            return editor(request, e, key, values={**current, **raw_form(e, form)}, error=f"Not saved: {err}")
        if all(same(values[c_], current[c_]) for c_ in e.columns):
            return editor(request, e, key, message="No change: the row already has those values.")
        if form.get("derive"):
            values = derive(e.name, values, now=clock.now())
        changed = {c_: contract.to_json(v) for c_, v in values.items() if not same(v, current[c_])}
        try:
            ev = log.publish(Change(e.name, "U", key, changed, note=(form.get("note") or "").strip() or None),
                             f"{PRODUCER}:{who}", clock.now())
        except contract.ContractError as err:
            return editor(request, e, key, values=values, error=f"Not saved: {err}")
        return editor(request, e, key, message=published(ev), oob=True)

    @app.post("/t/{entity}/new", response_class=HTMLResponse)
    async def insert_row(request: Request, entity: str):
        who = operator(request)
        e = entity_or_404(entity)
        form = await request.form()
        try:
            values = {**{c_: None for c_ in e.columns}, **typed_form(e, form)}
        except (ValueError, TypeError) as err:
            return editor(request, e, None, mode="new", values=raw_form(e, form), error=f"Not inserted: {err}")
        if form.get("derive"):
            values = derive(e.name, values, now=clock.now())
        key = str(values.get(e.key) or "").strip()
        try:
            ev = log.publish(Change(e.name, "I", key, contract.encode(values),
                                    note=(form.get("note") or "").strip() or None), f"{PRODUCER}:{who}", clock.now())
        except contract.ContractError as err:
            return editor(request, e, None, mode="new", values=values, error=f"Not inserted: {err}")
        return editor(request, e, key, message=published(ev), oob=True)

    @app.post("/t/{entity}/delete/{key}", response_class=HTMLResponse)
    async def delete_row(request: Request, entity: str, key: str):
        who = operator(request)
        e = entity_or_404(entity)
        form = await request.form()
        try:
            ev = log.publish(Change(e.name, "D", key, note=(form.get("note") or "").strip() or None),
                             f"{PRODUCER}:{who}", clock.now())
        except contract.ContractError as err:
            return editor(request, e, key, error=str(err))
        return editor(request, e, None, mode="none", message=f"Deleted {key}. " + published(ev), oob=True)

    @app.post("/t/wms.order/recalc/{key}", response_class=HTMLResponse)
    def recalc_order(request: Request, key: str):
        """Rebuild an order header's quantities from its customer's item lines (clears an R13 mismatch)."""
        who = operator(request)
        e = contract.ENTITIES["wms.order"]
        projector.catch_up()
        current = projector.tables[e.name].get(key)
        if current is None:
            return editor(request, e, key)
        values = derive(e.name, {**current, **order_totals(projector.rows("wms.item_order"),
                                                             current["Ship to Name"])})
        changed = {c_: contract.to_json(v) for c_, v in values.items() if v != current[c_]}
        ev = log.publish(Change(e.name, "U", key, changed, note="Order totals rebuilt from item lines"),
                         f"{PRODUCER}:{who}", clock.now())
        return editor(request, e, key, message=published(ev) if ev
                      else "Order totals already match the item lines.", oob=True)

    # --- clock, reset, live refresh ---------------------------------------------
    @app.post("/clock", response_class=HTMLResponse)
    def control_clock(request: Request, action: str = Form(...), value: str = Form("")):
        operator(request)
        error = ""
        try:
            if action == "pause":
                clock.pause()
            elif action == "resume":
                clock.resume()
            elif action == "rate":
                clock.set_rate(float(value))
            elif action == "advance":
                clock.advance(parse_duration(value))
            elif action == "set":
                clock.set(dt.datetime.fromisoformat(value))
        except ValueError as err:
            error = str(err)
        return render(request, "_clock.html", clock=clock, now=clock.now(), clock_error=error)

    @app.post("/reset")
    def reset(request: Request, sales_date: str = Form(seed.DEFAULT_SALES_DATE.isoformat())):
        operator(request)
        log.reset()
        seed.seed(log, dt.date.fromisoformat(sales_date))
        return Response(status_code=204, headers={"HX-Redirect": "/t/mips.order_line"})

    @app.get("/poll", response_class=HTMLResponse)
    def poll(request: Request, entity: str = "mips.order_line", seen: int = 0, page: str = "table"):
        """Every 2 s: feed, clock and worklist status; the table too when the source moved."""
        operator(request)
        e = entity_or_404(entity)
        c = ctx(request, e, page=page)
        return render(request, "_poll.html", **c, table_changed=page == "table" and c["head"] != seen)

    # --- scenarios ---------------------------------------------------------------
    @app.get("/scenarios", response_class=HTMLResponse)
    def scenarios_page(request: Request):
        operator(request)
        return render(request, "scenarios.html", **ctx(request, page="scenarios"))

    @app.post("/scenarios/{sid}/run", response_class=HTMLResponse)
    def run_scenario(request: Request, sid: str, reset: str = Form(""), pause: float = Form(3.0)):
        who = operator(request)
        sc = scenarios.SCENARIOS.get(sid)
        if sc is None:
            raise HTTPException(status_code=404, detail=f"No scenario {sid}")
        with run_lock:
            if run_state["running"]:
                return render(request, "_run.html", run=run_state, busy=True)
            run_state.update(id=sid, running=True, lines=[], error="", done=False, by=who)

        def say(line: str) -> None:
            run_state["lines"].append(f"{clock.now():%H:%M:%S}  {line}")

        def play():
            try:
                if reset:
                    log.reset()
                    seed.seed(log)
                    say("Reset to the starting day.")
                scenarios.run(log, sc, pause=max(0.0, min(pause, 30.0)),
                              on_step=lambda i, step, evs: say(
                                  f"Step {i + 1}/{len(sc.steps)}: {step.say} "
                                  f"({', '.join(published(ev) for ev in evs) if evs else 'no change'})"))
                say("Done. Watch the worklist for: " + ", ".join(sc.appears))
            except Exception as err:   # a scenario needs the starting rows; show why it stopped
                run_state["error"] = f"{err}. Reset to the starting day and run it again."
            finally:
                run_state.update(running=False, done=True)

        threading.Thread(target=play, daemon=True, name=f"scenario-{sid}").start()
        return render(request, "_run.html", run=run_state, busy=False)

    @app.get("/scenarios/run", response_class=HTMLResponse)
    def run_status(request: Request):
        operator(request)
        return render(request, "_run.html", run=run_state, busy=False)

    # --- integration: faults and the corrections endpoint ---------------------------
    def integration_panel(request: Request, message: str = "", error: str = "") -> HTMLResponse:
        return render(request, "_integration.html", **ctx(request, page="integration"),
                      faults=log.faults(), message=message, error=error)

    @app.get("/integration", response_class=HTMLResponse)
    def integration_page(request: Request):
        operator(request)
        return render(request, "integration.html", **ctx(request, page="integration"), faults=log.faults(),
                      message="", error="")

    @app.post("/integration/faults", response_class=HTMLResponse)
    def add_fault(request: Request, kind: str = Form(...), source: str = Form("*"), entity: str = Form("*"),
                  seconds: str = Form(""), batch: str = Form(""), count: str = Form(""), note: str = Form("")):
        who = operator(request)
        try:
            fid = log.add_fault(kind, source or "*", entity or "*", float(seconds) if seconds else None,
                                int(batch) if batch else None, int(count) if count else None,
                                note.strip() or f"added by {who}")
        except ValueError as err:
            return integration_panel(request, error=str(err))
        return integration_panel(request, message=f"Fault {fid} is active.")

    @app.post("/integration/faults/{fid}/remove", response_class=HTMLResponse)
    def remove_fault(request: Request, fid: int):
        operator(request)
        log.remove_fault(fid)
        return integration_panel(request, message=f"Fault {fid} removed.")

    @app.post("/integration/clear", response_class=HTMLResponse)
    def clear_faults(request: Request):
        operator(request)
        log.remove_fault()
        n = log.flush()
        return integration_panel(request, message=f"All faults removed; {n} held change(s) delivered.")

    @app.post("/integration/flush", response_class=HTMLResponse)
    def flush(request: Request):
        operator(request)
        return integration_panel(request, message=f"Delivered {log.flush()} delayed or held change(s) now.")

    @app.post("/integration/redeliver", response_class=HTMLResponse)
    def redeliver(request: Request):
        operator(request)
        return integration_panel(request, message=f"Replayed {log.redeliver_dropped()} dropped change(s) from the source.")

    @app.post("/integration/oic", response_class=HTMLResponse)
    def oic_mode(request: Request, mode: str = Form(...)):
        operator(request)
        if mode in OIC_MODES:
            oic["mode"] = mode
        return integration_panel(request, message=f"Corrections endpoint: {OIC_MODES[oic['mode']].lower()}.")

    @app.post("/oic/corrections")
    async def receive_correction(request: Request):
        """Stand-in for Oracle Integration receiving an approved correction from the worklist.

        Body: {correction_id, flag_id, requested_by, entity, op ("U" or "I"), key, expected, changes}.
        202 when applied (the change then travels the integration like any other), 409 when the
        row no longer has the values the correction was computed from, 422 when rejected, 503
        when the endpoint is set to unavailable.
        """
        if config.CORRECTIONS_TOKEN and not security.same_secret(request.headers.get("X-Corrections-Token"),
                                                                  config.CORRECTIONS_TOKEN):
            return JSONResponse({"error": "not authorized"}, status_code=401)
        if oic["mode"] == "down":
            return JSONResponse({"error": "Oracle Integration is unavailable (simulated)"}, status_code=503)
        body = await request.json()
        if oic["mode"] == "reject":
            return JSONResponse({"error": "rejected by Oracle Integration (simulated)"}, status_code=422)
        try:
            e = contract.entity(body["entity"])
            op, key, changes = body.get("op", "U"), str(body.get("key") or ""), body.get("changes") or {}
            now = clock.now()
            note = f"Correction {body.get('correction_id')} for {body.get('flag_id')}, approved by {body.get('requested_by')}"
            if op == "I":
                row = derive(e.name, contract.validate(e, changes), now=now)
                change = Change(e.name, "I", str(row[e.key]), contract.encode(row), note=note)
            else:
                current = log.current(e.name, key)
                if current is None:
                    return JSONResponse({"error": f"{e.name} {key} does not exist"}, status_code=404)
                stale = {c: current.get(c) for c, v in (body.get("expected") or {}).items() if current.get(c) != v}
                if stale:
                    return JSONResponse({"error": "the row changed since the correction was computed",
                                         "current": stale}, status_code=409)
                merged = contract.encode(derive(e.name, contract.validate(e, {**current, **changes}), now=now))
                change = Change(e.name, "U", key, {c: v for c, v in merged.items() if v != current.get(c)}, note=note)
            ev = log.publish(change, "oic:corrections", now)
        except (KeyError, contract.ContractError, ValueError) as err:
            return JSONResponse({"error": str(err)}, status_code=422)
        return JSONResponse({"status": "accepted", "source_seq": ev.source_seq if ev else None,
                             "event_seq": ev.seq if ev else None}, status_code=202)

    return app
