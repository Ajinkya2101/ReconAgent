"""Live worklist state: the engine run plus who claimed, acknowledged or decided what.

Rows are SKUs with their flags nested; plant-level flags (no SKU) are rows of
their own. Every change bumps `version`, so an open screen can tell it is
behind instead of silently showing a different number than the next one.

The data comes from a feed (sources.py): the source workbook, or the event log
with its simulated clock. Times in the store (claims, acknowledgments, SLAs)
follow the feed's clock.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from dataclasses import dataclass, field

from .. import ai, config, corrections, engine, feedback, sources
from ..detectors import Finding
from ..position import SkuPosition

ACCEPTED = "Accepted - awaiting correction"
REJECTED = "Rejected"
AI_READY = "__ai__"   # change key: the AI text for this run has arrived
SIM_RESET = "__reset__"   # change key: someone reset the simulation to the starting day
DATA_CHANGED = "__data__"   # change key: run totals moved (day tiles, briefing), whichever rows changed
FEED = ("engine", "feed", "ai")   # who a change is "by" when it came from the data or the model, not a person
SEVERITY_RANK = {"High": 3, "Medium": 2, "Low": 1}


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")


def fingerprint(row: "Row") -> str:
    """Everything the worklist shows for a row that comes from the data: each flag's figures, evidence,
    explanation and accountability, and every source record behind the reconciled position. Any edit
    in any source system that touches the row changes it, so open screens are told."""
    flags = [(f.id, round(f.units, 2), f.severity, f.status, f.evidence, f.recommendation, f.narrative,
              f.accountability) for f in row.flags]
    return json.dumps([flags, repr(row.position)], sort_keys=True, default=str)


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
    def at_risk(self) -> float:
        """Money still at risk: the largest open flag, counted once per SKU like the engine's total."""
        return max((f.dollars for f in self.open_flags), default=0.0)

    @property
    def decided_flags(self) -> list[Finding]:
        return [f for f in self.flags if f.status != "Open"]

    @property
    def accepted_dollars(self) -> float:
        """Each accepted flag's own retail $, added up. Flags on one SKU can describe the same units,
        so this is not deduplicated, and the screen says so wherever it shows it."""
        return sum(f.dollars for f in self.flags if f.status == ACCEPTED)

    @property
    def lead_units(self) -> float:
        """Units on the most important open flag: what a row with no retail value leads with."""
        return abs(self.top.units)

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
    # Hand-off notes per row ("checked WMS, waiting on the locator count"), newest last. Like claims
    # they live in memory: they are for the next shift, not a record of the decision.
    notes: dict[str, list[dict]] = field(default_factory=dict)
    briefing_read: dict[str, str] = field(default_factory=dict)   # user -> briefing id they marked read
    # Plant-clock times of each person's sign-ins, the last two. Shift boundaries are not confirmed
    # yet, so "what changed while you were away" is measured from the sign-in before this one.
    signins: dict[str, list[dt.datetime]] = field(default_factory=dict)
    decided_at: dict[str, dict] = field(default_factory=dict)   # flag id -> {"user", "at"} on the plant clock
    first_seen: dict[str, dt.datetime] = field(default_factory=dict)
    rows: dict[str, Row] = field(default_factory=dict)
    narration_state: str = "rules"   # rules | pending | ai | failed
    narration_error: str = ""
    # Model text per flag, keyed by its figures, rule text and attribution: an unchanged flag keeps
    # its explanation across reloads. A None value means the model was asked and returned nothing.
    ai_cache: dict[tuple, dict | None] = field(default_factory=dict)
    ai_briefings: dict[tuple, dict] = field(default_factory=dict)   # run (flag keys + KPIs) -> model briefing
    _ai_timer: threading.Timer | None = None
    _ai_running: bool = False
    _ai_first_change: float | None = None
    _loaded: bool = False
    _kpi_print: str = ""

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
        """(Re)run the engine. Returns the row keys whose figures, flags or explanations changed."""
        with self.lock:
            before = {k: fingerprint(r) for k, r in self.rows.items()}
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
                rows[key] = Row(key=key, sku=flags[0].sku, description=flags[0].description,
                                sales_date=p.sales_date if p and p.sales_date else self.sales_date,
                                # A carry-forward moves stock on the figures the other flags
                                # question, so those are checked first: carries come last.
                                flags=sorted(flags, key=lambda f: (f.rule in config.CARRY_FORWARD_RULES,
                                                                   -f.priority)),
                                position=p, claim=self.claims.get(key))
            self.rows = rows
            after = {k: fingerprint(r) for k, r in rows.items()}
            changed = {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
            kpi_print = json.dumps(self.res.kpis, sort_keys=True, default=str)
            if self._loaded:   # the first load has no screens to tell
                for k in changed:
                    self._bump(k, "engine")
                if changed or kpi_print != self._kpi_print:
                    self._bump(DATA_CHANGED, "engine")
            self._loaded, self._kpi_print = True, kpi_print
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
                             changes=p.changes, summary=p.summary, key=p.key, op=p.op, rows=p.rows, error="")
        return row, self._send(c, row.key, user)

    # --- AI narration ----------------------------------------------------
    # The model is never called once per event. After a reload, cached text is applied at once;
    # if flags are left that the model has not explained, one call is made when the feed has been
    # quiet for AI_DEBOUNCE_SECONDS (at most AI_MAX_WAIT_SECONDS after the first change).
    @staticmethod
    def _ai_key(f: Finding) -> tuple:
        # Everything the explanation rests on: a change to any figure, sentence or attribution asks again.
        return (f.id, round(f.units, 2), json.dumps(f.evidence, sort_keys=True, default=str), f.recommendation,
                json.dumps(f.accountability, sort_keys=True, default=str))

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
                             source=briefing["source"], audit_id=briefing.get("audit_id"),
                             briefing_check=briefing.get("briefing_check"))
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
                "source": narration["source"], "audit_id": narration.get("audit_id"),
                "briefing_check": narration.get("briefing_check")}
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
            self.notes.clear()
            self.decided_at.clear()
            self.briefing_read.clear()
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
            except Exception as e:  # a missing or half-copied workbook must not take the worklist down
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
            if view == "handed" and not self.handed_over(r, user):
                continue
            if view == "decided" and not r.decided_flags:   # any decided flag: the tab counts flags
                continue
            if view == "all" and not r.is_open:
                continue
            out.append(r)
        return sorted(out, key=lambda r: r.priority, reverse=True)

    def handed_over(self, r: Row, user: str = "") -> bool:
        """Open work someone has already touched without finishing it: a shift note, a claim held by
        someone else, or a High flag acknowledged but not yet decided."""
        if not r.is_open:
            return False
        return bool(self.notes.get(r.key)) or bool(r.claim and r.claim["user"] != user) \
            or any(f.id in self.acks for f in r.open_flags)

    def days(self) -> list[dict]:
        """Each sales day with open work, oldest first."""
        return [self.day_summary(d) for d in sorted({r.sales_date for r in self.rows.values() if r.is_open})]

    def day_summary(self, d: dt.date) -> dict:
        """One sales day: money still at risk (each SKU's largest open flag, as the engine counts it)
        and what has been accepted and decided so far, so a decision visibly moves the shelf."""
        rows = [r for r in self.rows.values() if r.sales_date == d]
        open_rows = [r for r in rows if r.is_open]
        return {"date": d, "rows": len(open_rows), "flags": sum(len(r.open_flags) for r in open_rows),
                "dollars": sum(r.at_risk for r in open_rows),
                "decided": sum(len(r.decided_flags) for r in rows),
                "accepted": sum(r.accepted_dollars for r in rows)}

    def ranks(self) -> dict[str, int]:
        """Each open row's place in the priority order of the whole open queue (1 = first).

        Filters narrow the list but keep these numbers, so a gap in the ranks shows rows
        filtered away rather than a reordering."""
        return {r.key: i for i, r in enumerate(self.ordered_rows(), start=1)}

    def multi_day(self) -> bool:
        """More than one sales day has open work: only then does each row need its own day tag."""
        return len(self.days()) > 1

    def pending_acks(self, row: Row) -> list[Finding]:
        """The row's High flags still waiting for acknowledgment, most urgent first."""
        pending = [f for f in row.open_flags if self.ack_state(f)["state"] in ("due", "overdue")]
        return sorted(pending, key=lambda f: self.first_seen[f.id])

    def carried_in(self) -> list[dict]:
        """Accepted carry-forward surplus, by the sales day it lands on (awaiting the MIPS correction)."""
        by_day: dict[dt.date, float] = {}
        for r in self.rows.values():
            if r.carry_accepted and r.position:
                surplus = r.position.stock_surplus
                by_day[next_sales_day(r.sales_date)] = by_day.get(next_sales_day(r.sales_date), 0.0) + surplus
        return [{"date": d, "units": u} for d, u in sorted(by_day.items()) if u]

    def counts(self, user: str) -> dict:
        return {"all": sum(1 for r in self.rows.values() if r.is_open),
                "ack": sum(1 for r in self.rows.values()
                           if any(self.ack_state(f)["state"] in ("due", "overdue") for f in r.flags)),
                "mine": sum(1 for r in self.rows.values() if r.claim and r.claim["user"] == user),
                "handed": sum(1 for r in self.rows.values() if self.handed_over(r, user)),
                "decided": sum(len(r.decided_flags) for r in self.rows.values())}   # flags, not rows

    def signed_in(self, user: str) -> None:
        self.signins[user] = (self.signins.get(user, []) + [self.now()])[-2:]

    def since_last_signin(self, user: str) -> dict | None:
        """What other people and the feed did since this person's previous sign-in, or None when
        there is no previous sign-in on record (first sign-in since the worklist started)."""
        times = self.signins.get(user, [])
        if len(times) < 2:
            return None
        since = times[0]
        open_flags = [f for r in self.rows.values() for f in r.open_flags]
        # Other people's actions count from the sign-in second itself (the plant clock has whole
        # seconds); flags count as new only when raised after it.
        return {
            "at": since,
            "new": sum(1 for f in open_flags if self.first_seen.get(f.id, since) > since),
            "decided": sum(1 for d in self.decided_at.values() if d["user"] != user and d["at"] >= since),
            "notes": sum(1 for ns in self.notes.values() for n in ns if n["user"] != user and n["at"] >= since),
            "claims": sum(1 for c in self.claims.values() if c["user"] != user and c["at"] >= since),
            "acks": sum(1 for a in self.acks.values() if a["user"] != user and a["at"] >= since),
        }

    def since_line(self, user: str) -> dict | None:
        """The briefing's "since your last sign-in" line, as its parts."""
        s = self.since_last_signin(user)
        if s is None:
            return None

        def n(k: int, one: str, many: str) -> str:
            return f"{k} {one if k == 1 else many}"

        parts = [p for k, p in (
            (s["new"], n(s["new"], "new flag", "new flags")),
            (s["decided"], n(s["decided"], "flag decided by others", "flags decided by others")),
            (s["notes"], n(s["notes"], "shift note", "shift notes")),
            (s["claims"], n(s["claims"], "claim", "claims")),
            (s["acks"], n(s["acks"], "High flag acknowledged", "High flags acknowledged"))) if k]
        return {"at": s["at"], "parts": parts}

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
        pending = self.pending_acks(row)
        return pending[0] if pending else None

    def decision(self, f: Finding) -> dict | None:
        return self.decisions.get(f.id)

    def changes_since(self, version: int, user: str) -> set[str]:
        return {c.key for c in self.changes if c.version > version and c.by != user}

    def feed_changes_since(self, version: int) -> set[str]:
        """Keys changed by the source data (not by a person) since `version`: these apply live."""
        return {c.key for c in self.changes if c.version > version and c.by in FEED}

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

    def acknowledge_row(self, key: str, user: str) -> tuple[Row, int]:
        """Acknowledge every High flag on the row still waiting: one tap from an alert link."""
        with self.lock:
            row = self.rows[key]
            pending = self.pending_acks(row)
            for f in pending:
                self.acks[f.id] = {"user": user, "at": self.now()}
            if pending:
                self._bump(row.key, user)
            return row, len(pending)

    def add_note(self, key: str, user: str, text: str) -> Row:
        with self.lock:
            row = self.rows[key]
            self.notes.setdefault(key, []).append({"user": user, "at": self.now(), "text": text})
            del self.notes[key][:-20]
            self._bump(key, user)
            return row

    def decide(self, flag_id: str, decision: str, user: str, note: str = "") -> Row:
        with self.lock:
            row, f = self.find(flag_id)
            state = feedback.record(f.id, round(f.units, 2), decision, note.strip(), by=user)
            self.decisions = state["decisions"]
            self.decided_at[f.id] = {"user": user, "at": self.now()}
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
    """The demand-to-delivery equation for the SKU detail, on one shared scale.

    The bar draws supply against demand: allocated and available WMS stock, then the MIPS work
    order, against the total demand mark. What the work order makes beyond the shortfall shows
    past the mark (over-production); what it leaves uncovered shows short of it."""
    p = row.position
    if not p or not (p.orders or p.forecasts or p.work_orders or p.stock):
        return None
    demand = p.demand
    alloc = min(p.allocated, demand)
    avail = min(p.available, max(demand - alloc, 0.0))
    wo_in = min(p.wo_qty, max(demand - alloc - avail, 0.0))
    over, short = max(0.0, p.balance), max(0.0, -p.balance)
    systems = [("BUSS", "Sales orders", p.so_qty), ("Oracle", "Forecast", p.forecast_qty),
               ("ODI", "Demand mapped", p.odi_demand), ("WMS", "Allocated", p.allocated),
               ("WMS", "Available", p.available), ("MIPS", "Demand planned", p.mips_demand),
               ("MIPS", "Work order", p.wo_qty)]
    scale = max([demand, p.planned_supply] + [v for *_, v in systems]) or 1.0

    def pct(x: float) -> float:
        return round(100 * x / scale, 2)

    return {
        "so": p.so_qty, "forecast": p.forecast_qty, "demand": demand, "allocated": p.allocated,
        "demand_left": p.demand_left, "available": p.available, "shortfall": p.shortfall,
        "surplus_stock": p.stock_surplus, "mips_demand": p.mips_demand, "wo": p.wo_qty,
        "work_orders": p.wo_numbers, "odi_wo": p.odi_wo_qty, "odi_demand": p.odi_demand,
        "odi_allocated": p.odi_allocated, "balance": p.balance, "over": over, "short": short,
        "orders": len(p.orders), "forecasts": [f.forecast_id for f in p.forecasts],
        "mips_ok": abs(p.mips_demand - p.demand_left) < 0.5, "wo_ok": abs(p.wo_qty - p.shortfall) < 0.5,
        "pct": {"demand": pct(demand), "alloc": pct(alloc), "avail": pct(avail), "wo": pct(wo_in),
                "over": pct(over), "short": pct(short)},
        "systems": [{"system": s, "label": l, "value": v, "pct": pct(v)} for s, l, v in systems],
        "next_day": next_sales_day(row.sales_date),
    }
