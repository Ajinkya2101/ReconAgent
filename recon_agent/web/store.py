"""Live worklist state: the engine run plus who claimed, acknowledged or decided what.

Rows are SKUs with their flags nested; plant-level flags (no SKU) are rows of
their own. Every change bumps `version`, so an open screen can tell it is
behind instead of silently showing a different number than the next one.

The data comes from a feed (sources.py): the Excel extracts, or the event log
with its simulated clock. Times in the store (claims, acknowledgments, SLAs)
follow the feed's clock.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field

from .. import ai, config, corrections, engine, feedback, sources
from ..detectors import Finding
from ..position import SkuPosition

ACCEPTED = "Accepted - awaiting correction"
REJECTED = "Rejected"
AI_READY = "__ai__"   # change key: the AI text for this run has arrived
SIM_RESET = "__reset__"   # change key: someone reset the simulation to the starting day
SEVERITY_RANK = {"High": 3, "Medium": 2, "Low": 1}


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")


def next_sales_day(d: dt.date) -> dt.date:
    d += dt.timedelta(days=1)
    while d.weekday() not in config.SALES_WEEKDAYS:
        d += dt.timedelta(days=1)
    return d


@dataclass
class Row:
    key: str
    sku: str
    description: str
    sales_date: dt.date
    flags: list[Finding]
    position: SkuPosition | None
    claim: dict | None = None

    @property
    def open_flags(self) -> list[Finding]:
        return [f for f in self.flags if f.status == "Open"]

    @property
    def is_open(self) -> bool:
        return bool(self.open_flags)

    @property
    def top(self) -> Finding:
        return max(self.open_flags or self.flags, key=lambda f: f.priority)

    @property
    def priority(self) -> float:
        return self.top.priority if self.is_open else -1.0

    @property
    def dollars(self) -> float:
        # Largest single open flag: summing a SKU's flags would count overlapping rules twice.
        return max(f.dollars for f in (self.open_flags or self.flags))

    @property
    def severity(self) -> str:
        pool = self.open_flags or self.flags
        return max(pool, key=lambda f: SEVERITY_RANK[f.severity]).severity

    @property
    def carry_accepted(self) -> bool:
        return any(f.rule in config.CARRY_FORWARD_RULES and f.status == ACCEPTED for f in self.flags)


@dataclass
class Change:
    version: int
    key: str
    by: str


@dataclass
class Store:
    feed: sources.ExcelFeed | sources.EventFeed
    lock: threading.RLock = field(default_factory=threading.RLock)
    version: int = 0
    changes: list[Change] = field(default_factory=list)
    claims: dict[str, dict] = field(default_factory=dict)
    acks: dict[str, dict] = field(default_factory=dict)
    first_seen: dict[str, dt.datetime] = field(default_factory=dict)
    rows: dict[str, Row] = field(default_factory=dict)
    narration_state: str = "rules"   # rules | pending | ai | failed
    narration_error: str = ""
    # Model text per flag, keyed by (id, units, evidence): a flag whose numbers are unchanged keeps
    # its explanation across reloads. A None value means the model was asked and returned nothing.
    ai_cache: dict[tuple, dict | None] = field(default_factory=dict)
    ai_briefings: dict[tuple, dict] = field(default_factory=dict)   # run (flag keys + KPIs) -> model briefing
    _ai_timer: threading.Timer | None = None
    _ai_running: bool = False
    _ai_first_change: float | None = None

    def __post_init__(self):
        # Write-back needs source row keys, which only the event feed has. Corrections belong to
        # one event log, so the book sits next to it.
        self.book = (corrections.CorrectionBook(self.feed.log.path.with_name("corrections.json"))
                     if self.feed.mode == "events" else None)
        self.load()

    def now(self) -> dt.datetime:
        return self.feed.now().replace(microsecond=0)

    # --- loading ---------------------------------------------------------
    def load(self) -> set[str]:
        """(Re)run the engine. Returns the row keys whose flags changed."""
        with self.lock:
            before = {k: {(f.id, round(f.units, 2)) for f in r.flags} for k, r in self.rows.items()}
            # Taken first: anything published during the run triggers one more reload, never none.
            self.position = self.feed.position()
            # Rule text first, so the worklist opens at once; the AI text follows in the background.
            self.res = engine.run(src=self.feed.load(), use_ai=False)
            if self.book:
                self.book.log_id = self.feed.projector.log_id
            self.rule_narration = self.res.narration
            self.loaded_at = self.now()
            self.sales_date = dt.date.fromisoformat(self.res.kpis["sales_date"])
            if not ai.credentials_available():
                self.narration_is_ai, self.ai_ids, self.narration_state = False, set(), "rules"
            elif self._apply_ai(self.res):
                self.narration_state = "ai"
            else:
                self.narration_state = "pending"
                self._schedule_ai()
            state = feedback.load()
            self.decisions = state["decisions"]
            live_ids = {f.id for f in self.res.findings}
            self.dismissed = sum(1 for fid, d in self.decisions.items()
                                 if d["decision"] == "reject" and fid not in live_ids)

            grouped: dict[str, list[Finding]] = {}
            for f in self.res.findings:
                self.first_seen.setdefault(f.id, self.loaded_at)
                grouped.setdefault(f.sku or f"plant-{slug(f.id)}", []).append(f)
            rows = {}
            for key, flags in grouped.items():
                p = self.res.positions.get(flags[0].sku) if flags[0].sku else None
                dates = Counter(l.schedule_date for l in p.lines) if p and p.lines else None
                rows[key] = Row(key=key, sku=flags[0].sku, description=flags[0].description,
                                sales_date=dates.most_common(1)[0][0] if dates else self.sales_date,
                                flags=sorted(flags, key=lambda f: f.priority, reverse=True),
                                position=p, claim=self.claims.get(key))
            self.rows = rows
            after = {k: {(f.id, round(f.units, 2)) for f in r.flags} for k, r in rows.items()}
            changed = {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
            if before:
                for k in changed:
                    self._bump(k, "engine")
            self._confirm_corrections()
            self.feed.commit()
            return changed

    # --- write-back --------------------------------------------------------
    def proposal(self, f: Finding) -> corrections.Proposal | None:
        if not self.book:
            return None
        return corrections.propose(f, self.feed.projector.tables, self.now())

    def correction(self, f: Finding) -> dict | None:
        return self.book.for_flag(f.id) if self.book else None

    def _confirm_corrections(self) -> None:
        """A correction is Confirmed when the feed shows the corrected values, not when the API answers."""
        if not self.book:
            return
        tables = self.feed.projector.tables
        for c in self.book.in_status(*corrections.ACTIVE):
            if corrections.arrived(c, tables):
                seq = self.feed.projector.row_seq[c["entity"]].get(c["key"])
                self.book.update(c["id"], "Confirmed", "feed", self.now(), f"seen in the feed at event #{seq}",
                                 confirmed_seq=seq)
                self._bump(c["row_key"], "feed")

    def _send(self, c: dict, row_key: str, user: str) -> str:
        ok, message = corrections.send(c, user)   # outside the lock: the API may take seconds
        with self.lock:
            if self.book.items[c["id"]]["status"] == "Approved":   # the feed may have confirmed it already
                self.book.update(c["id"], "Sent" if ok else "Failed", "Corrections API", self.now(), message,
                                 error="" if ok else message)
            # Without integration delay the change is already in the log: reconcile now, so the
            # approver sees it confirmed (or the flag gone) in the same response.
            self.refresh_if_sources_changed()
            self._confirm_corrections()
            self._bump(row_key, user)
        return message

    def approve(self, flag_id: str, user: str) -> tuple[Row, str]:
        """Approve the proposed correction (accepting the flag if still open) and send it."""
        with self.lock:
            row, f = self.find(flag_id)
            current = self.correction(f)
            if current and current["status"] in corrections.ACTIVE:
                return row, f"{current['id']} is already on its way."
            p = self.proposal(f)
            if p is None:
                return row, "This flag has no automatic correction; correct it in the source system."
            if f.status == "Open":
                self.decide(flag_id, "accept", user, f"Approved correction: {p.summary}")
            c = self.book.create(p, row.key, user, self.now())
        return row, self._send(c, row.key, user)

    def retry(self, correction_id: str, user: str) -> tuple[Row | None, str]:
        """Send a failed correction again, recomputed from the rows the worklist sees now."""
        with self.lock:
            c = self.book.items[correction_id]
            try:
                row, f = self.find(c["flag_id"])
            except KeyError:
                self.book.update(c["id"], "Cancelled", user, self.now(), "the flag is gone: nothing to correct")
                return None, "The flag is gone: nothing to correct."
            p = self.proposal(f)
            if p is None:
                self.book.update(c["id"], "Cancelled", user, self.now(), "no correction applies any more")
                return row, "No correction applies any more."
            self.book.update(c["id"], "Approved", user, self.now(), "retried", expected=p.expected,
                             changes=p.changes, summary=p.summary, key=p.key, op=p.op, error="")
        return row, self._send(c, row.key, user)

    # --- AI narration ----------------------------------------------------
    # The model is never called once per event. After a reload, cached text is applied at once;
    # if flags are left that the model has not explained, one call is made when the feed has been
    # quiet for AI_DEBOUNCE_SECONDS (at most AI_MAX_WAIT_SECONDS after the first change).
    @staticmethod
    def _ai_key(f: Finding) -> tuple:
        return f.id, round(f.units, 2), json.dumps(f.evidence, sort_keys=True, default=str)

    def _briefing_key(self, res) -> tuple:
        return (tuple(self._ai_key(f) for f in res.findings[:config.AI_TOP_FINDINGS]),
                json.dumps(res.kpis, sort_keys=True, default=str))

    def _apply_ai(self, res) -> bool:
        """Put cached model text on `res`. True when the model has nothing left to explain."""
        top = res.findings[:config.AI_TOP_FINDINGS]
        known = {f.id: self.ai_cache[self._ai_key(f)] for f in top if self._ai_key(f) in self.ai_cache}
        by_ai = {fid: entry for fid, entry in known.items() if entry}
        narration = {**self.rule_narration, "by_id": {**self.rule_narration["by_id"], **by_ai}}
        briefing = self.ai_briefings.get(self._briefing_key(res))
        if briefing:   # a briefing quotes run totals, so it is only reused for the exact same run
            narration.update(shift_briefing=briefing["shift_briefing"], top_actions=briefing["top_actions"],
                             source=briefing["source"])
        engine.apply_narration(res, narration)
        # Flags past the first AI_TOP_FINDINGS keep their rule text, owner and scenario.
        self.ai_ids, self.narration_is_ai = set(by_ai), briefing is not None
        return briefing is not None and len(known) == len(top)

    def _schedule_ai(self) -> None:
        now = time.monotonic()
        self._ai_first_change = self._ai_first_change or now
        due = min(now + config.AI_DEBOUNCE_SECONDS, self._ai_first_change + config.AI_MAX_WAIT_SECONDS)
        if self._ai_timer:
            self._ai_timer.cancel()
        self._ai_timer = threading.Timer(max(0.0, due - now), self._narrate)
        self._ai_timer.daemon = True
        self._ai_timer.start()

    def _narrate(self) -> None:
        with self.lock:
            if self._ai_running:   # the running call reschedules itself if the data moved on
                return
            res, self._ai_timer, self._ai_first_change = self.res, None, None
            if self._apply_ai(res):   # another call already explained this run
                self.narration_state = "ai"
                return
            self._ai_running = True
        top = res.findings[:config.AI_TOP_FINDINGS]
        try:
            narration = ai.narrate_with_model(top, res.kpis)
        except ai.AIError as e:
            with self.lock:
                self._ai_running = False
                if res is self.res:
                    self.narration_state, self.narration_error = "failed", str(e)
            print(f"{e} - the worklist keeps rule text.")
            return
        with self.lock:
            self._ai_running = False
            for f in top:
                self.ai_cache[self._ai_key(f)] = narration["by_id"].get(f.id)
            for stale in list(self.ai_cache)[:-2000]:
                del self.ai_cache[stale]
            self.ai_briefings[self._briefing_key(res)] = {
                "shift_briefing": narration["shift_briefing"], "top_actions": narration["top_actions"],
                "source": narration["source"]}
            for stale in list(self.ai_briefings)[:-20]:
                del self.ai_briefings[stale]
            if self._apply_ai(self.res):
                self.narration_state = "ai"
                self._bump(AI_READY, "ai")
            else:   # the feed moved while the model was writing: explain what is new
                self._schedule_ai()

    def reset_simulation(self, user: str, sales_date: dt.date | None = None) -> None:
        """Event feed only: back to the synthetic starting day, for the next demo.

        Clears the event log and publishes the seed again, and clears the simulation's planner
        decisions, claims and acknowledgments. Earlier corrections stay on file under the old log.
        """
        from ..events import seed

        if self.feed.mode != "events":
            raise RuntimeError("Reset works on the simulated event feed only (RECON_SOURCE=events).")
        with self.lock:
            self.feed.log.reset()
            seed.seed(self.feed.log, sales_date or seed.DEFAULT_SALES_DATE)
            feedback.save({"rules": {}, "decisions": {}})
            self.claims.clear()
            self.acks.clear()
            self.first_seen.clear()
            self.rows = {}
            self.load()
            self._bump(SIM_RESET, user)

    def refresh_if_sources_changed(self) -> None:
        position = self.feed.position()
        if position == self.position:
            return
        with self.lock:
            if position == self.position:   # another request reloaded meanwhile
                return
            try:
                self.load()
            except Exception as e:  # a missing or half-copied extract must not take the worklist down
                self.position = position   # try again when the source moves again
                print(f"Reload failed ({e}) - the worklist keeps the {self.sales_date} data.")

    # --- queries ---------------------------------------------------------
    def ordered_rows(self, day: str = "", view: str = "all", q: str = "", user: str = "") -> list[Row]:
        q = q.strip().lower()
        out = []
        for r in self.rows.values():
            if day and r.sales_date.isoformat() != day:
                continue
            if q and q not in r.sku.lower() and q not in r.description.lower():
                continue
            if view == "ack" and not any(self.ack_state(f)["state"] in ("due", "overdue") for f in r.flags):
                continue
            if view == "mine" and not (r.claim and r.claim["user"] == user):
                continue
            if view == "decided" and r.is_open:
                continue
            if view == "all" and not r.is_open:
                continue
            out.append(r)
        return sorted(out, key=lambda r: r.priority, reverse=True)

    def days(self) -> list[dict]:
        by_day: dict[dt.date, list[Row]] = {}
        for r in self.rows.values():
            if r.is_open:
                by_day.setdefault(r.sales_date, []).append(r)
        return [{"date": d, "rows": len(rs), "flags": sum(len(r.open_flags) for r in rs),
                 "dollars": self.res.kpis["dollar_exposure"] if d == self.sales_date else None}
                for d, rs in sorted(by_day.items())]

    def carried_in(self) -> list[dict]:
        """Accepted carry-forward surplus, by the sales day it lands on (awaiting the MIPS correction)."""
        by_day: dict[dt.date, float] = {}
        for r in self.rows.values():
            if r.carry_accepted and r.position:
                surplus = max(0.0, -r.position.net_requirement)
                by_day[next_sales_day(r.sales_date)] = by_day.get(next_sales_day(r.sales_date), 0.0) + surplus
        return [{"date": d, "units": u} for d, u in sorted(by_day.items()) if u]

    def counts(self, user: str) -> dict:
        return {"all": sum(1 for r in self.rows.values() if r.is_open),
                "ack": sum(1 for r in self.rows.values()
                           if any(self.ack_state(f)["state"] in ("due", "overdue") for f in r.flags)),
                "mine": sum(1 for r in self.rows.values() if r.claim and r.claim["user"] == user),
                "decided": sum(1 for r in self.rows.values() if not r.is_open)}

    def find(self, flag_id: str) -> tuple[Row, Finding]:
        for r in self.rows.values():
            for f in r.flags:
                if f.id == flag_id:
                    return r, f
        raise KeyError(flag_id)

    def ack_state(self, f: Finding) -> dict:
        if f.severity != "High" or f.status != "Open":
            return {"state": "none"}
        if f.id in self.acks:
            return {"state": "acked", **self.acks[f.id]}
        due = self.first_seen[f.id] + dt.timedelta(minutes=config.HIGH_ACK_MINUTES)
        minutes = int((due - self.now()).total_seconds() // 60)
        return {"state": "due" if minutes >= 0 else "overdue", "due": due, "minutes": abs(minutes)}

    def row_ack_flag(self, row: Row) -> Finding | None:
        """The row's most urgent High flag still waiting for acknowledgment."""
        pending = [f for f in row.open_flags if self.ack_state(f)["state"] in ("due", "overdue")]
        return min(pending, key=lambda f: self.first_seen[f.id]) if pending else None

    def decision(self, f: Finding) -> dict | None:
        return self.decisions.get(f.id)

    def changes_since(self, version: int, user: str) -> set[str]:
        return {c.key for c in self.changes if c.version > version and c.by != user}

    # --- actions ---------------------------------------------------------
    def _bump(self, key: str, by: str) -> None:
        self.version += 1
        self.changes.append(Change(self.version, key, by))
        del self.changes[:-500]

    def claim(self, key: str, user: str) -> Row:
        with self.lock:
            row = self.rows[key]
            row.claim = self.claims[key] = {"user": user, "at": self.now()}
            self._bump(key, user)
            return row

    def release(self, key: str, user: str) -> Row:
        with self.lock:
            row = self.rows[key]
            if row.claim and row.claim["user"] == user:
                self.claims.pop(key, None)
                row.claim = None
                self._bump(key, user)
            return row

    def acknowledge(self, flag_id: str, user: str) -> Row:
        with self.lock:
            row, f = self.find(flag_id)
            self.acks[f.id] = {"user": user, "at": self.now()}
            self._bump(row.key, user)
            return row

    def decide(self, flag_id: str, decision: str, user: str, note: str = "") -> Row:
        with self.lock:
            row, f = self.find(flag_id)
            state = feedback.record(f.id, round(f.units, 2), decision, note.strip(), by=user)
            self.decisions = state["decisions"]
            f.status = ACCEPTED if decision == "accept" else REJECTED
            # The agent learns: every flag of this rule re-ranks on the new confidence.
            for r in self.rows.values():
                for g in r.flags:
                    if g.rule == f.rule:
                        g.confidence = feedback.rule_confidence(state, g.rule)
            self._bump(row.key, user)
            return row


# --- view helpers ------------------------------------------------------------

def day_tag(d: dt.date) -> dict:
    label, fill, ink = config.DAY_CODES[d.weekday()]
    return {"label": label, "code": d.strftime("%m·%d"), "fill": fill, "ink": ink,
            "long": d.strftime("%A %d %B %Y"), "iso": d.isoformat()}


def ledger(row: Row) -> dict | None:
    """Reconciled position laid out on one shared scale, for the SKU detail."""
    p = row.position
    if not p or not (p.lines or p.runs):
        return None
    demand, inventory, wrapped = p.demand_total, p.implied_inventory, p.wrapped
    supply = inventory + wrapped
    systems = [("MIPS", "Demand, all lines", demand), ("MIPS", "Inventory applied", inventory),
               ("ODI", "Ordered", p.net_ordered), ("ODI", "Wrapped", wrapped),
               ("WMS", "Ordered", p.wms_order_qty), ("WMS", "Allocated", p.wms_allocated)]
    scale = max([supply, demand] + [v for *_, v in systems]) or 1.0

    def pct(x: float) -> float:
        return round(100 * x / scale, 2)

    inv_in = min(inventory, demand)
    wrap_in = min(wrapped, demand - inv_in)
    surplus, short = max(0.0, supply - demand), max(0.0, demand - supply)
    return {
        "demand": demand, "inventory": inventory, "wrapped": wrapped, "net": p.net_requirement,
        "surplus": surplus, "short": short, "lines": len(p.lines),
        "sales_orders": p.demand_sales_orders, "supply_plan": p.demand_supply_plan, "moved": p.demand_moved,
        "pct": {"demand": pct(demand), "inv": pct(inv_in), "wrap": pct(wrap_in), "surplus": pct(surplus),
                "short": pct(short)},
        "systems": [{"system": s, "label": l, "value": v, "pct": pct(v)} for s, l, v in systems],
        "next_day": next_sales_day(row.sales_date),
    }
