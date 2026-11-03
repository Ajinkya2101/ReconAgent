"""Exception worklist: FastAPI + Jinja + htmx front end over the reconciliation engine.

HTML routes serve planners and supervisors (full pages, or fragments when htmx
asks). /api/* is the Reconciliation API the architecture names, as JSON.
People sign in with a username and password (accounts in users.py); the role
comes from the account. The session is a signed cookie (security.py).

A background watcher reconciles as soon as the feed moves, and /stream pushes a
`changed` event to every open screen, which then fetches /changes as before.
The 20-second poll stays as a fallback for networks that block event streams.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import html
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__, ai, config, security, sources, users
from .store import ACCEPTED, REJECTED, Row, Store, day_tag, ledger, next_sales_day

HERE = Path(__file__).parent
SESSION_COOKIE = "recon_session"
APPROVERS = ("planner", "manager")   # who may approve write-back to a source system
VIEWS = {"all": "Open", "ack": "Needs acknowledgment", "mine": "Claimed by me", "decided": "Decided"}


# --- formatting ----------------------------------------------------------------

def num(x) -> str:
    return f"{x:,.0f}" if isinstance(x, (int, float)) else str(x)


def money(x) -> str:
    return f"${x:,.0f}"


def hm(t: dt.datetime | None) -> str:
    return t.strftime("%H:%M") if t else ""


def initials(name: str) -> str:
    parts = [p for p in re.split(r"[\s._-]+", name) if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


ACRONYMS = {"odi": "ODI", "mips": "MIPS", "wms": "WMS", "upc": "units/container", "so": "SO", "sp": "SP", "fg": "FG"}


def humanize(key: str) -> str:
    words = [ACRONYMS.get(w, w) for w in key.lower().split("_")]
    return " ".join([words[0][:1].upper() + words[0][1:]] + words[1:])


def evidence_value(v):
    if isinstance(v, float):
        return f"{v:,.0f}" if v == int(v) else f"{v:,.1f}"
    if isinstance(v, dt.datetime):
        return v.strftime("%m/%d %H:%M")
    if isinstance(v, list):
        return ", ".join(map(str, v))
    text = str(v)
    m = re.fullmatch(r"\d{4}-(\d{2})-(\d{2})[ T](\d{2}:\d{2})(:\d{2}(\.\d+)?)?", text)
    return f"{m[1]}/{m[2]} {m[3]}" if m else text


def evidence_lines(v) -> list[str]:
    text = evidence_value(v)
    return [s.strip() for s in text.split(";")] if ";" in text else [text]


# --- app -----------------------------------------------------------------------

def watch(store: Store, stop: threading.Event) -> None:
    """Reconcile as soon as the feed moves, whether or not anyone has a screen open."""
    while not stop.wait(config.WEB_WATCH_SECONDS):
        try:
            store.refresh_if_sources_changed()
        except Exception as e:   # e.g. the event log is locked mid-reset: try again next tick
            print(f"Feed check failed ({e}); retrying.")


def create_app(data_dir: Path | None = None) -> FastAPI:
    users.ensure_accounts()
    store = Store(sources.open_feed(data_dir))
    failed_by_user = security.RateLimiter(config.SIGNIN_ATTEMPTS, config.SIGNIN_WINDOW_SECONDS)
    failed_by_address = security.RateLimiter(config.SIGNIN_ATTEMPTS_PER_ADDRESS, config.SIGNIN_WINDOW_SECONDS)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()
        threading.Thread(target=watch, args=(store, stop), daemon=True, name="feed-watcher").start()
        yield
        stop.set()

    app = FastAPI(title="Reconciliation worklist", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs" if config.API_DOCS else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if config.API_DOCS else None)
    security.harden(app, docs_path="/api/docs")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    env = templates.env
    env.filters.update(num=num, money=money, hm=hm, initials=initials, humanize=humanize,
                       evidence_lines=evidence_lines)
    env.globals.update(APPROVERS=APPROVERS, day_tag=day_tag, ledger=ledger, next_sales_day=next_sales_day, VIEWS=VIEWS, ACCEPTED=ACCEPTED, REJECTED=REJECTED,
                       config=config, store=store, ai_available=ai.credentials_available())

    def user_of(request: Request) -> dict | None:
        """The signed-in account. Name and role are read from the account on every request, so a
        role change applies at once and a removed account or changed password ends the session."""
        session = security.unsign(request.cookies.get(SESSION_COOKIE), "worklist")
        account = users.get(session["u"]) if session else None
        if not account or account["session"] != session.get("s"):
            return None
        return {"name": account["name"], "username": account["username"], "role": account["role"],
                "initials": initials(account["name"])}

    def require_user(request: Request) -> dict:
        user = user_of(request)
        if not user:
            target = "/signin?next=" + quote(str(request.url.path))
            raise HTTPException(status_code=303, headers={"Location": target, "HX-Redirect": target})
        return user

    def is_htmx(request: Request) -> bool:
        return request.headers.get("HX-Request") == "true"

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx)

    def page_context(request: Request, user: dict, selected: Row | None, auto_selected: bool) -> dict:
        p = request.query_params
        day, view, q = p.get("day", ""), p.get("view", "all"), p.get("q", "")
        view = view if view in VIEWS else "all"
        rows = store.ordered_rows(day, view, q, user["name"])
        return {"user": user, "rows": rows, "selected": selected, "auto_selected": auto_selected,
                "day": day, "view": view, "q": q, "days": store.days(),
                "counts": store.counts(user["name"]), "version": store.version}

    def detail_response(request: Request, row: Row, user: dict, oob_row: bool = True,
                        retag_now: bool = False) -> HTMLResponse:
        return render(request, "_detail_response.html", user=user, selected=row, oob_row=oob_row,
                      version=store.version, retag_now=retag_now)

    # --- identity ----------------------------------------------------------------
    @app.get("/signin", response_class=HTMLResponse)
    def signin_page(request: Request, next: str = "/"):
        return render(request, "signin.html", next=security.safe_next(next), error="", username="")

    @app.post("/signin")
    def signin(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
        username, target = username.strip().lower()[:40], security.safe_next(next)
        address = security.client_ip(request)
        if failed_by_user.blocked(username) or failed_by_address.blocked(address):
            return render_signin(request, target, username, "Too many failed sign-ins. Wait 15 minutes and try again.", 429)
        account = users.authenticate(username, password)
        if not account:
            failed_by_user.hit(username)
            failed_by_address.hit(address)
            return render_signin(request, target, username, "That username and password do not match.", 401)
        failed_by_user.reset(username)
        resp = RedirectResponse(target, status_code=303)
        resp.set_cookie(SESSION_COOKIE, security.sign({"u": account["username"], "s": account["session"]}, "worklist"),
                        max_age=int(config.SESSION_HOURS * 3600), httponly=True, samesite="lax",
                        secure=config.COOKIE_SECURE)
        return resp

    def render_signin(request: Request, target: str, username: str, error: str, status: int) -> HTMLResponse:
        resp = render(request, "signin.html", next=target, error=error, username=username)
        resp.status_code = status
        return resp

    @app.post("/signout")
    def signout():
        resp = RedirectResponse("/signin", status_code=303)
        resp.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax", secure=config.COOKIE_SECURE)
        return resp

    @app.get("/healthz")
    def healthz():
        """Liveness for the container and the load balancer: no session, no engine work."""
        return {"status": "ok", "version": __version__, "source": store.feed.mode}

    # --- worklist ----------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def worklist(request: Request):
        user = require_user(request)
        store.refresh_if_sources_changed()
        ctx = page_context(request, user, None, True)
        ctx["selected"] = ctx["rows"][0] if ctx["rows"] else None
        return render(request, "worklist.html", **ctx)

    @app.get("/row/{key}", response_class=HTMLResponse)
    def row_page(request: Request, key: str):
        user = require_user(request)
        row = store.rows.get(key) or store.rows.get(key.lstrip("0"))
        if not row:
            return render(request, "not_found.html", user=user, key=key, version=store.version)
        if is_htmx(request):
            return detail_response(request, row, user, oob_row=False)
        return render(request, "worklist.html", **page_context(request, user, row, False))

    @app.get("/queue", response_class=HTMLResponse)
    def queue(request: Request, open: str = "", since: int = -1):
        user = require_user(request)
        if not is_htmx(request):
            return RedirectResponse("/?" + str(request.query_params), status_code=303)
        store.refresh_if_sources_changed()
        ctx = page_context(request, user, None, False)
        ctx["open_key"] = open if open in store.rows else ""
        changed = store.changes_since(since, user["name"]) if since >= 0 else set()
        ctx["detail_row"] = store.rows[open] if open in changed and open in store.rows else None
        return render(request, "_queue_response.html", **ctx)

    @app.get("/changes", response_class=HTMLResponse)
    def changes(request: Request, since: int = 0):
        user = require_user(request)
        store.refresh_if_sources_changed()
        keys = store.changes_since(since, user["name"])
        return render(request, "_changes.html", keys=keys, checked=store.now(), version=store.version,
                      since=since, asof_oob=True)

    @app.get("/stream")
    async def stream(request: Request, since: int = 0):
        """Server push: a `changed` event whenever the store's version passes `since`.

        The event carries no data; the page fetches /changes, which filters out the
        viewer's own changes and renders the "rows changed" pill as before.
        """
        require_user(request)

        async def events():
            version, idle = since, 0.0
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                if store.version != version:
                    version, idle = store.version, 0.0
                    yield f"event: changed\ndata: {version}\n\n"
                elif idle >= 15:   # keeps proxies from closing a quiet stream
                    idle = 0.0
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.25)
                idle += 0.25

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/row/{key}/claim", response_class=HTMLResponse)
    def claim(request: Request, key: str):
        user = require_user(request)
        return detail_response(request, store.claim(key, user["name"]), user)

    @app.post("/row/{key}/release", response_class=HTMLResponse)
    def release(request: Request, key: str):
        user = require_user(request)
        return detail_response(request, store.release(key, user["name"]), user)

    @app.post("/flags/{flag_id}/ack", response_class=HTMLResponse)
    def acknowledge(request: Request, flag_id: str):
        user = require_user(request)
        return detail_response(request, store.acknowledge(flag_id, user["name"]), user)

    @app.post("/flags/{flag_id}/decision", response_class=HTMLResponse)
    def decide(request: Request, flag_id: str, decision: str = Form(...), note: str = Form("")):
        user = require_user(request)
        if decision not in ("accept", "reject"):
            raise HTTPException(400, "decision must be accept or reject")
        try:
            row = store.decide(flag_id, decision, user["name"], note)
        except KeyError:
            raise HTTPException(404, f"No flag {flag_id}")
        if not is_htmx(request):
            return RedirectResponse(f"/row/{row.key}", status_code=303)
        retag = decision == "accept" and flag_id.split("-", 1)[0] in config.CARRY_FORWARD_RULES
        return detail_response(request, row, user, retag_now=retag)

    def require_approver(request: Request) -> dict:
        user = require_user(request)
        if user["role"] not in APPROVERS:
            raise HTTPException(403, "Only a planner or manager approves write-back to the source systems.")
        return user

    @app.post("/flags/{flag_id}/correction", response_class=HTMLResponse)
    def approve_correction(request: Request, flag_id: str):
        user = require_approver(request)
        try:
            row, _ = store.approve(flag_id, user["name"])
        except KeyError:
            raise HTTPException(404, f"No flag {flag_id}")
        return detail_response(request, store.rows.get(row.key, row), user)

    @app.post("/corrections/{correction_id}/retry", response_class=HTMLResponse)
    def retry_correction(request: Request, correction_id: str):
        user = require_approver(request)
        if not store.book or correction_id not in store.book.items:
            raise HTTPException(404, f"No correction {correction_id}")
        row, _ = store.retry(correction_id, user["name"])
        if row is None:
            return Response(status_code=204, headers={"HX-Refresh": "true"})
        return detail_response(request, store.rows.get(row.key, row), user)

    @app.get("/briefing", response_class=HTMLResponse)
    def briefing(request: Request):
        user = require_user(request)
        return render(request, "_briefing.html", user=user, expanded=True)

    @app.post("/ask", response_class=HTMLResponse)
    def ask_html(request: Request, question: str = Form(...)):
        require_user(request)
        return render(request, "_answer.html", question=question, answer=answer(question))

    # --- Reconciliation API (JSON) --------------------------------------------------
    @app.get("/api/positions/{sku}")
    def api_position(request: Request, sku: str):
        require_user(request)
        p = store.res.positions.get(sku.strip().lstrip("0"))
        if not p:
            raise HTTPException(404, f"SKU {sku} not found")
        row = store.rows.get(p.sku)
        return {"position": p.to_row(), "ledger": ledger(row) if row else None,
                "flags": [f.id for f in row.flags] if row else []}

    @app.get("/api/flags")
    def api_flags(request: Request, status: str = "open"):
        require_user(request)
        flags = [f for r in store.rows.values() for f in r.flags
                 if status == "all" or (status == "open") == (f.status == "Open")]
        return [{"id": f.id, "rule": f.rule, "title": f.title, "severity": f.severity, "sku": f.sku,
                 "item": f.description, "units": f.units, "dollars": round(f.dollars, 2),
                 "priority": round(f.priority, 1), "status": f.status, "evidence": f.evidence,
                 "recommendation": f.recommendation, "explanation": f.narrative} for f in
                sorted(flags, key=lambda f: f.priority, reverse=True)]

    @app.post("/api/flags/{flag_id}/decision")
    async def api_decide(request: Request, flag_id: str):
        user = require_user(request)
        body = await request.json()
        if body.get("decision") not in ("accept", "reject"):
            raise HTTPException(400, "decision must be accept or reject")
        try:
            store.decide(flag_id, body["decision"], user["name"], body.get("note", ""))
        except KeyError:
            raise HTTPException(404, f"No flag {flag_id}")
        return {"id": flag_id, "status": store.find(flag_id)[1].status, "version": store.version}

    @app.post("/api/corrections")
    async def api_correct(request: Request):
        """Approve and send the proposed correction for a flag. Body: {"flag_id": "..."}."""
        user = require_approver(request)
        body = await request.json()
        if not store.book:
            raise HTTPException(409, "Write-back needs the event feed (RECON_SOURCE=events).")
        try:
            _, message = store.approve(body["flag_id"], user["name"])
        except KeyError:
            raise HTTPException(404, f"No flag {body.get('flag_id')}")
        c = store.book.for_flag(body["flag_id"])
        return {"message": message, "correction": c}

    @app.get("/api/corrections")
    def api_corrections(request: Request):
        require_user(request)
        return list(store.book.items.values()) if store.book else []

    @app.get("/api/corrections/{correction_id}")
    def api_correction(request: Request, correction_id: str):
        require_user(request)
        if not store.book or correction_id not in store.book.items:
            raise HTTPException(404, f"No correction {correction_id}")
        return store.book.items[correction_id]

    @app.get("/api/briefing/{shift}")
    def api_briefing(request: Request, shift: str):
        require_user(request)
        n = store.res.narration
        return {"shift": shift, "sales_date": store.res.kpis["sales_date"], "briefing": n["shift_briefing"],
                "top_actions": n["top_actions"], "source": n["source"]}

    @app.post("/api/ask")
    async def api_ask(request: Request):
        require_user(request)
        return answer((await request.json()).get("question", ""))

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 303:
            if is_htmx(request):
                return Response(status_code=204, headers={"HX-Redirect": exc.headers["HX-Redirect"]})
            return RedirectResponse(exc.headers["Location"], status_code=303)
        if request.url.path.startswith("/api/") or not is_htmx(request):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return HTMLResponse(f'<p class="inline-error" role="alert">{html.escape(str(exc.detail))}</p>',
                            status_code=exc.status_code,
                            headers={"HX-Retarget": "#toast", "HX-Reswap": "innerHTML"})

    # --- assistant -------------------------------------------------------------------
    def answer(question: str) -> dict:
        question = question.strip()[:500]
        res = store.res
        mentioned = [k for k in dict.fromkeys(m.lstrip("0") for m in re.findall(r"\b\d{3,7}\b", question))
                     if k in store.rows]
        ai_error = ""
        if ai.credentials_available():
            try:
                text = ai.ask(question, res.positions, res.findings)
            except ai.AIError as e:
                ai_error = str(e)
            else:
                linked = [k for k in dict.fromkeys(m.lstrip("0") for m in re.findall(r"\b\d{3,7}\b", text))
                          if k in store.rows]
                return {"source": "ai", "text": text, "rows": [store.rows[k] for k in linked[:6]]}
        if mentioned:
            rows = [store.rows[k] for k in mentioned[:3]]
            return {"source": "rules", "text": "", "rows": rows, "ai_error": ai_error}
        words = [w for w in re.findall(r"[a-zA-Z]{4,}", question.lower())
                 if w not in {"what", "which", "why", "does", "still", "show", "need", "more", "with", "from",
                              "have", "there", "this", "that", "mips", "units", "much", "many", "today"}]
        hits = [r for r in store.ordered_rows() if any(w in r.description.lower() for w in words)]
        return {"source": "rules", "text": "", "rows": hits[:6], "searched": words, "ai_error": ai_error}

    @app.get("/api/feed")
    def api_feed(request: Request):
        require_user(request)
        p = getattr(store.feed, "projector", None)
        return {"mode": store.feed.mode, "event_seq": store.feed.seq, "version": store.version,
                "duplicates_ignored": p.duplicates_ignored if p else 0, "stale_ignored": p.stale_ignored if p else 0,
                "reconciled_at": store.loaded_at, "sales_date": store.sales_date,
                "narration": store.narration_state, "open_rows": sum(1 for r in store.rows.values() if r.is_open)}

    app.state.store = store
    return app
