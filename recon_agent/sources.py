"""Where source data comes from: the source workbook or the event feed.

Both feeds give the engine the same SourceData. The web store asks a feed for its
`position()` on each request and re-runs the engine only when it moves, so the
worklist reacts to a new workbook and to a new event in the same way.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Hashable

from . import config, ingest

CONSUMER = "worklist"   # this app's name in the event log's consumer commits


class ExcelFeed:
    mode = "excel"

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = Path(data_dir or config.DATA_DIR)

    def position(self) -> Hashable:
        return max((p.stat().st_mtime for p in self.data_dir.glob(config.WORKBOOK_GLOB)), default=0.0)

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

    def position(self) -> Hashable:
        return self.log.position()

    def load(self) -> ingest.SourceData:
        self.projector.catch_up()
        return self.projector.source_data()

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
