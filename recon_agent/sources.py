"""Where source data comes from: the daily Excel extracts or the event feed.

Both feeds give the engine the same SourceData. The web store asks a feed for its
`position()` on each request and re-runs the engine only when it moves, so the
worklist reacts to a new extract file and to a new event in the same way.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Hashable

from . import config, detectors, ingest

CONSUMER = "worklist"   # this app's name in the event log's consumer commits


class ExcelFeed:
    mode = "excel"

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = Path(data_dir or config.DATA_DIR)

    def position(self) -> Hashable:
        return tuple(max((p.stat().st_mtime for p in self.data_dir.glob(pattern)), default=0.0)
                     for pattern in config.SOURCES.values())

    def load(self) -> ingest.SourceData:
        return ingest.load_all(self.data_dir)

    def now(self) -> dt.datetime:
        return dt.datetime.now()

    def commit(self) -> None:
        pass

    @property
    def seq(self) -> None:
        return None


class EventFeed:
    """The simulated real-time feed: projects the event log, keeps simulated time."""
    mode = "events"

    def __init__(self, db_path: Path | None = None):
        from .events.clock import SimClock
        from .events.log import EventLog
        from .events.projector import Projector

        self.log = EventLog(Path(db_path or config.EVENT_DB))
        self.clock = SimClock(self.log)
        self.projector = Projector(self.log, self.clock)
        self.deadline: dt.datetime | None = None

    def position(self) -> Hashable:
        # Time alone can raise a flag (R18's posting window): the position also moves when
        # the plant clock passes the next deadline, so the worklist reconciles then too.
        return (*self.log.position(), self.deadline is not None and self.clock.now() >= self.deadline)

    def load(self) -> ingest.SourceData:
        self.projector.catch_up()
        src = self.projector.source_data()
        self.deadline = detectors.next_deadline(src)
        return src

    def now(self) -> dt.datetime:
        return self.clock.now()

    def commit(self) -> None:
        """Tell producers the worklist now shows everything up to the last applied event."""
        if self.projector.seq:
            self.log.commit(CONSUMER, self.projector.seq, dt.datetime.now())

    @property
    def seq(self) -> int:
        """Last event applied to the projection."""
        return self.projector.seq


def open_feed(data_dir: Path | None = None, mode: str | None = None) -> ExcelFeed | EventFeed:
    mode = mode or config.SOURCE_MODE
    if mode == "events":
        return EventFeed()
    if mode == "excel":
        return ExcelFeed(data_dir)
    raise ValueError(f"RECON_SOURCE must be 'excel' or 'events', not '{mode}'")
