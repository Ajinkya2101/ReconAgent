"""The scenario library is the regression suite for the rules: each of the ten scenarios,
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

from recon_agent import engine
from recon_agent.events import scenarios, seed
from recon_agent.events.log import Change, EventLog
from recon_agent.events.projector import Projector

from recon_agent import config as _config
assert "recon-tests-" in str(_config.FEEDBACK_FILE) and not _config.AI_ENABLED, "tests are not isolated"

NOW = dt.datetime(2026, 9, 11, 20, 0)


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
        self.assertEqual(sorted(s.number for s in scenarios.SCENARIOS.values()), list(range(1, 15)))
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

    def test_a_step_lands_in_one_commit(self):
        """A worklist reloading between two changes of one step would show flags that vanish a
        moment later (S7 changes eleven ODI rows of one work order)."""
        for sid, sc in scenarios.SCENARIOS.items():
            with self.subTest(sid):
                log, commits = seeded_log(), []
                publish_many = log.publish_many

                def spy(changes, *args, **kwargs):
                    out = publish_many(changes, *args, **kwargs)
                    commits.append(flags(log))   # what a worklist reloading now would show
                    return out

                log.publish_many = spy
                scenarios.run(log, sc)
                self.assertEqual(len(commits), sum(1 for s in sc.steps if s.changes))

    def test_a_scenario_needs_the_starting_rows(self):
        log = seeded_log()
        log.publish(Change("buss.sales_order", "D", "2263245-1"), "cli", NOW)
        with self.assertRaises(Exception):
            scenarios.run(log, scenarios.SCENARIOS["S1"])


class FaultTest(unittest.TestCase):
    # 76105's WMS stock: on hand 1,900 against 1,800 available + allocated is R10. A count of 1,800 fixes it.
    FLAG = "R10-INV-0003"

    def change(self, qty: int) -> Change:
        return Change("wms.inventory", "U", "INV-0003", {"On hand": qty})

    def test_delay_holds_the_event_then_delivers(self):
        log = seeded_log()
        log.add_fault("delay", source="WMS", seconds=0.3)
        head = log.position()[1]
        ev = log.publish(self.change(1800), "test", NOW)
        self.assertEqual(ev.seq, 0)
        self.assertEqual(log.position()[1], head)              # the consumer sees nothing yet
        self.assertEqual(log.current("wms.inventory", "INV-0003")["On hand"], 1800)   # the source has it
        self.assertIn(self.FLAG, flags(log))
        time.sleep(0.35)
        self.assertEqual(log.pump(), 1)
        self.assertNotIn(self.FLAG, flags(log))

    def test_drop_loses_the_event_until_redelivered(self):
        log = seeded_log()
        log.add_fault("drop", entity="wms.inventory", remaining=1)
        log.publish(self.change(1800), "test", NOW)
        self.assertEqual(log.integration()["dropped"], 1)
        self.assertEqual(log.faults(), [])                     # a one-shot fault switches itself off
        self.assertIn(self.FLAG, flags(log))                # the worklist still shows the old order
        self.assertNotIn(self.FLAG, flags(log, stream="source"))
        self.assertEqual(log.redeliver_dropped(), 1)
        self.assertNotIn(self.FLAG, flags(log))

    def test_a_later_full_image_heals_a_dropped_update(self):
        log = seeded_log()
        log.add_fault("drop", entity="wms.inventory", remaining=1)
        log.publish(self.change(1850), "test", NOW)
        log.publish(Change("wms.inventory", "U", "INV-0003", {"Available quantity": 1550}), "test", NOW)
        p = Projector(log)
        p.catch_up()
        row = p.tables["wms.inventory"]["INV-0003"]
        self.assertEqual((row["On hand"], row["Available quantity"]), (1850, 1550))   # the lost 1,850 arrives too

    def test_duplicates_are_ignored_by_the_consumer(self):
        log = seeded_log()
        log.add_fault("duplicate", seconds=0)
        log.publish(self.change(1850), "test", NOW)
        log.publish(self.change(1800), "test", NOW)
        log.pump()
        self.assertEqual(log.integration()["duplicates_delivered"], 2)
        p = Projector(log)
        p.catch_up()
        self.assertEqual(p.duplicates_ignored, 2)
        self.assertEqual(p.tables["wms.inventory"]["INV-0003"]["On hand"], 1800)   # not the late 1,850 copy

    def test_reordered_events_keep_the_newest_image(self):
        log = seeded_log()
        log.add_fault("reorder", batch=3)
        for qty in (1700, 1750, 1800):
            log.publish(self.change(qty), "test", NOW)
        delivered = [e.after["On hand"] for e in log.tail(3)]
        self.assertEqual(delivered, [1800, 1750, 1700])        # newest first
        p = Projector(log)
        p.catch_up()
        self.assertEqual(p.stale_ignored, 2)
        self.assertEqual(p.tables["wms.inventory"]["INV-0003"]["On hand"], 1800)

    def test_flush_and_removal(self):
        log = seeded_log()
        log.add_fault("reorder", batch=5)
        fid = log.add_fault("delay", seconds=60)
        log.publish(self.change(1800), "test", NOW)
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
