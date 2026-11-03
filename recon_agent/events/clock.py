"""Simulated plant clock, shared through the event log file.

    sim_now = anchor_sim + (wall_now - anchor_wall) * rate     (running)
    sim_now = anchor_sim                                       (paused)

Every control (set, advance, rate, pause, resume) re-anchors at the current
wall time, so simulated time never jumps except when asked to. With no clock
stored yet, simulated time is wall time. Rules and SLAs that depend on time
(the 15-minute acknowledgment, later R18's 10-minute posting window) read this
clock in event mode, so a shift can be fast-forwarded or frozen for a demo.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass

from .log import EventLog


@dataclass(frozen=True)
class ClockState:
    anchor_sim: dt.datetime
    anchor_wall: float
    rate: float
    paused: bool

    def at(self, wall: float) -> dt.datetime:
        if self.paused:
            return self.anchor_sim
        return self.anchor_sim + dt.timedelta(seconds=(wall - self.anchor_wall) * self.rate)


def parse_duration(text: str) -> dt.timedelta:
    """'90s', '15m', '2h', '1d', '1h30m' or a plain number of minutes; may start with '-'."""
    text = text.strip().lower()
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("+-")
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return sign * dt.timedelta(minutes=float(text))
    parts = re.findall(r"(\d+(?:\.\d+)?)([dhms])", text)
    if not parts or "".join(n + u for n, u in parts) != text:
        raise ValueError(f"Cannot read duration '{text}'. Use e.g. 90s, 15m, 2h, 1d or 1h30m.")
    unit = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}
    return sign * sum((dt.timedelta(**{unit[u]: float(n)}) for n, u in parts), dt.timedelta())


class SimClock:
    def __init__(self, log: EventLog, cache_seconds: float = 1.0):
        self.log = log
        self.cache_seconds = cache_seconds
        self._state: ClockState | None = None
        self._read_at = 0.0

    def state(self) -> ClockState:
        wall = time.time()
        # cache_seconds <= 0 always re-reads: time.time() on Windows only ticks every ~15 ms.
        if self._state is None or self.cache_seconds <= 0 or wall - self._read_at > self.cache_seconds:
            r = self.log.clock_row()
            self._state = (ClockState(dt.datetime.fromisoformat(r["anchor_sim"]), r["anchor_wall"], r["rate"],
                                      bool(r["paused"]))
                           if r else ClockState(dt.datetime.now(), wall, 1.0, False))
            self._read_at = wall
        return self._state

    def now(self) -> dt.datetime:
        return self.state().at(time.time())

    def _store(self, anchor_sim: dt.datetime, rate: float, paused: bool) -> ClockState:
        self.log.set_clock_row(anchor_sim, rate, paused)
        self._state = None
        return self.state()

    def set(self, t: dt.datetime) -> ClockState:
        s = self.state()
        return self._store(t, s.rate, s.paused)

    def advance(self, delta: dt.timedelta) -> ClockState:
        s = self.state()
        return self._store(self.now() + delta, s.rate, s.paused)

    def set_rate(self, rate: float) -> ClockState:
        if rate <= 0:
            raise ValueError("rate must be positive; use pause to stop the clock")
        return self._store(self.now(), rate, self.state().paused)

    def pause(self) -> ClockState:
        return self._store(self.now(), self.state().rate, True)

    def resume(self) -> ClockState:
        return self._store(self.now(), self.state().rate, False)
