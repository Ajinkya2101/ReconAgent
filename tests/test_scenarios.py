"""The scenario library is the regression suite for the rules: each of the eight scenarios,
played on the untouched starting day, must raise (and clear) exactly the flags it declares.
Also the integration faults and the consumer's protection against them.

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import tempfile

# Isolation, however unittest imports this file (`discover -s tests` skips tests/__init__.py):
# never call the model, never touch the real planner decisions. Set before recon_agent is imported.
os.environ["RECON_AI"] = "off"
os.environ.setdefault("RECON_TEST_DIR", tempfile.mkdtemp(prefix="recon-tests-"))
os.environ["RECON_FEEDBACK_FILE"] = os.path.join(os.environ["RECON_TEST_DIR"], "feedback.json")
os.environ["RECON_USERS_FILE"] = os.path.join(os.environ["RECON_TEST_DIR"], "users.json")
os.environ["RECON_COOKIE_SECURE"] = "off"   # the test client talks plain http

import datetime as dt
import tempfile
import time
import unittest
from pathlib import Path

from recon_agent import engine, sources
from recon_agent.events import scenarios, seed
from recon_agent.events.clock import SimClock
from recon_agent.events.log import Change, EventLog
from recon_agent.events.projector import Projector
from recon_agent.web.store import Store

from recon_agent import config as _config
assert "recon-tests-" in str(_config.FEEDBACK_FILE) and not _config.AI_ENABLED, "tests are not isolated"

NOW = dt.datetime(2026, 11, 6, 20, 0)


def seeded_log() -> EventLog:
    log = EventLog(Path(tempfile.mkdtemp(prefix="recon-scn-")) / "events.db")
    seed.seed(log)
    return log


def flags(log: EventLog, stream: str = "events") -> set[str]:
    p = Projector(log, stream=stream)
    p.catch_up()
    return {f.id for f in engine.run(src=p.source_data(), use_ai=False).findings}


class ScenarioLibraryTest(unittest.TestCase):
    def test_every_scenario_raises_exactly_its_flags(self):
        self.assertEqual(sorted(s.number for s in scenarios.SCENARIOS.values()), list(range(1, 9)))
        for sid, sc in scenarios.SCENARIOS.items():
            with self.subTest(sid):
                log = seeded_log()
                before = flags(log)
                published = scenarios.run(log, sc)
                after = flags(log)
                self.assertTrue(published)
                self.assertEqual(after - before, set(sc.appears))
                self.assertEqual(before - after, set(sc.clears))
                self.assertTrue(all(ev.producer == f"scenario:{sid}" for ev in log.tail(len(published))))

    def test_a_scenario_needs_the_starting_rows(self):
        log = seeded_log()
        log.publish(Change("odi.finished_good", "D", "FG-0013"), "cli", NOW)
        with self.assertRaises(Exception):
            scenarios.run(log, scenarios.SCENARIOS["S1"])

    def test_r18_appears_when_plant_time_passes_the_window(self):
        """No event arrives when a posting goes overdue: the worklist must notice the clock."""
        log = seeded_log()
        store = Store(sources.EventFeed(log.path))
        store.feed.clock.cache_seconds = 0
        SimClock(log).pause()
        sc = scenarios.SCENARIOS["S8"]
        scenarios.run(log, scenarios.Scenario(sc.id, sc.number, sc.title, sc.story, sc.skus, sc.steps[:2], {}))
        store.refresh_if_sources_changed()
        self.assertFalse(any(f.rule == "R18" for f in store.res.findings))
        SimClock(log).advance(dt.timedelta(minutes=9))
        store.refresh_if_sources_changed()
        self.assertEqual({f.id for f in store.res.findings if f.rule == "R18"}, {"R18-WT-S8A", "R18-WT-S8F"})


class FaultTest(unittest.TestCase):
    def change(self, qty: int) -> Change:
        return Change("wms.item_order", "U", "WL-0015", {"Order Qty": qty})

    def test_delay_holds_the_event_then_delivers(self):
        log = seeded_log()
        log.add_fault("delay", source="WMS", seconds=0.3)
        head = log.position()[1]
        ev = log.publish(self.change(800), "test", NOW)
        self.assertEqual(ev.seq, 0)
        self.assertEqual(log.position()[1], head)              # the consumer sees nothing yet
        self.assertEqual(log.current("wms.item_order", "WL-0015")["Order Qty"], 800)   # the source has it
        self.assertIn("R09-900107", flags(log))
        time.sleep(0.35)
        self.assertEqual(log.pump(), 1)
        self.assertNotIn("R09-900107", flags(log))

    def test_drop_loses_the_event_until_redelivered(self):
        log = seeded_log()
        log.add_fault("drop", entity="wms.item_order", remaining=1)
        log.publish(self.change(800), "test", NOW)
        self.assertEqual(log.integration()["dropped"], 1)
        self.assertEqual(log.faults(), [])                     # a one-shot fault switches itself off
        self.assertIn("R09-900107", flags(log))                # the worklist still shows the old order
        self.assertNotIn("R09-900107", flags(log, stream="source"))
        self.assertEqual(log.redeliver_dropped(), 1)
        self.assertNotIn("R09-900107", flags(log))

    def test_a_later_full_image_heals_a_dropped_update(self):
        log = seeded_log()
        log.add_fault("drop", entity="wms.item_order", remaining=1)
        log.publish(self.change(900), "test", NOW)
        log.publish(Change("wms.item_order", "U", "WL-0015", {"Allocated Units Qty": 900}), "test", NOW)
        p = Projector(log)
        p.catch_up()
        row = p.tables["wms.item_order"]["WL-0015"]
        self.assertEqual((row["Order Qty"], row["Allocated Units Qty"]), (900, 900))   # the lost 900 arrives too

    def test_duplicates_are_ignored_by_the_consumer(self):
        log = seeded_log()
        log.add_fault("duplicate", seconds=0)
        log.publish(self.change(800), "test", NOW)
        log.publish(self.change(1000), "test", NOW)
        log.pump()
        self.assertEqual(log.integration()["duplicates_delivered"], 2)
        p = Projector(log)
        p.catch_up()
        self.assertEqual(p.duplicates_ignored, 2)
        self.assertEqual(p.tables["wms.item_order"]["WL-0015"]["Order Qty"], 1000)   # not the late 800 copy

    def test_reordered_events_keep_the_newest_image(self):
        log = seeded_log()
        log.add_fault("reorder", batch=3)
        for qty in (700, 750, 800):
            log.publish(self.change(qty), "test", NOW)
        delivered = [e.after["Order Qty"] for e in log.tail(3)]
        self.assertEqual(delivered, [800, 750, 700])           # newest first
        p = Projector(log)
        p.catch_up()
        self.assertEqual(p.stale_ignored, 2)
        self.assertEqual(p.tables["wms.item_order"]["WL-0015"]["Order Qty"], 800)

    def test_flush_and_removal(self):
        log = seeded_log()
        log.add_fault("reorder", batch=5)
        fid = log.add_fault("delay", seconds=60)
        log.publish(self.change(800), "test", NOW)
        self.assertEqual(log.integration()["held"], 1)
        log.remove_fault(fid)
        self.assertEqual(len(log.faults()), 1)
        self.assertEqual(log.flush(), 1)
        self.assertEqual(log.integration()["held"], 0)
        with self.assertRaises(ValueError):
            log.add_fault("delay", seconds=0)
        with self.assertRaises(ValueError):
            log.add_fault("teleport")

    def test_seed_bypasses_faults_and_reset_clears_them(self):
        log = seeded_log()
        log.add_fault("drop")
        log.reset()
        self.assertEqual(log.faults(), [])
        log.add_fault("drop")
        seed.seed(log) if not log.source_position()[1] else None
        self.assertEqual(log.position()[1], len(seed.build()))


if __name__ == "__main__":
    unittest.main()
