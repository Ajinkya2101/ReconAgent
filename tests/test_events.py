"""Event feed: contract, log, clock, projector, seed, and the worklist reacting to events.

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
import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

from recon_agent import config, engine, ingest, sources
from recon_agent.events import contract, seed
from recon_agent.events.clock import SimClock, parse_duration
from recon_agent.events.log import Change, EventLog
from recon_agent.events.projector import Projector
from recon_agent.web.store import Store

from recon_agent import config as _config
assert "recon-tests-" in str(_config.FEEDBACK_FILE) and not _config.AI_ENABLED, "tests are not isolated"

NOW = dt.datetime(2026, 11, 6, 20, 0)


def fresh_log() -> EventLog:
    return EventLog(Path(tempfile.mkdtemp(prefix="recon-events-")) / "events.db")


def seeded_log() -> EventLog:
    log = fresh_log()
    seed.seed(log)
    return log


def order(key: str = "9901", qty: int = 100) -> dict:
    return {"Order Nbr": key, "Order Type": "TRANSFER_ORDER", "Order Qty": qty, "Sales Date": "2026-11-07"}


class ContractTest(unittest.TestCase):
    def test_full_typed_image(self):
        row = contract.validate(contract.entity("wms.order"), order(qty="120"))
        self.assertEqual(row["Order Qty"], 120)
        self.assertEqual(row["Sales Date"], dt.date(2026, 11, 7))
        self.assertIsNone(row["Shipped Qty"])
        self.assertEqual(len(row), len(contract.entity("wms.order").columns))

    def test_rejects_unknown_and_missing_columns(self):
        e = contract.entity("wms.order")
        with self.assertRaises(contract.ContractError):
            contract.validate(e, {**order(), "Bogus": 1})
        with self.assertRaises(contract.ContractError):
            contract.validate(e, {"Order Nbr": "1"})
        with self.assertRaises(contract.ContractError):
            contract.validate(e, order(qty="lots"))

    def test_round_trip_keeps_values_and_padding(self):
        e = contract.entity("mips.order_line")
        change = next(c for c in seed.build() if c.entity == e.name and c.row["Source System"] == "BUSS")
        image = contract.encode(contract.validate(e, change.row))
        self.assertTrue(image["FG Oracle Ref"].endswith(" "))   # BUSS padding survives, as in the extract
        self.assertEqual(contract.encode(contract.validate(e, image)), image)
        decoded = contract.decode(e, image)
        self.assertIsInstance(decoded["Schedule Date"], dt.datetime)   # as openpyxl reads dates

    def test_bool_parsing(self):
        self.assertIs(contract.coerce("yes", contract.BOOL), True)
        self.assertIs(contract.coerce("F", contract.BOOL), False)


class LogTest(unittest.TestCase):
    def test_insert_update_delete(self):
        log = fresh_log()
        ev = log.publish(Change("wms.order", "I", row=order()), "test", NOW)
        self.assertEqual((ev.seq, ev.op, ev.key, ev.source), (1, "I", "9901", "WMS"))
        ev = log.publish(Change("wms.order", "U", "9901", {"Order Qty": 150}), "test", NOW)
        self.assertEqual((ev.before["Order Qty"], ev.after["Order Qty"]), (100, 150))
        self.assertIsNone(log.publish(Change("wms.order", "U", "9901", {"Order Qty": "150"}), "test", NOW))
        ev = log.publish(Change("wms.order", "D", "9901"), "test", NOW)
        self.assertIsNone(ev.after)
        self.assertIsNone(log.current("wms.order", "9901"))
        self.assertEqual(log.position()[1], 3)

    def test_rejects_invalid_changes_atomically(self):
        log = fresh_log()
        log.publish(Change("wms.order", "I", row=order()), "test", NOW)
        for bad in (Change("wms.order", "I", row=order()),                        # duplicate key
                    Change("wms.order", "U", "nope", {"Order Qty": 1}),           # unknown key
                    Change("wms.order", "U", "9901", {"Order Nbr": "9902"}),      # key change
                    Change("wms.order", "X", "9901")):                            # unknown op
            with self.assertRaises(contract.ContractError):
                log.publish(bad, "test", NOW)
        with self.assertRaises(contract.ContractError):   # second change fails, so the first is rolled back
            log.publish_many([Change("wms.order", "I", row=order("9902")),
                              Change("wms.order", "I", row=order("9902"))], "test", NOW)
        self.assertEqual(log.position()[1], 1)

    def test_append_only(self):
        log = fresh_log()
        log.publish(Change("wms.order", "I", row=order()), "test", NOW)
        with sqlite3.connect(log.path) as con:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("UPDATE events SET key = 'x'")
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("DELETE FROM events")

    def test_reset_starts_a_new_log(self):
        log = seeded_log()
        before = log.position()
        log.reset()
        after = log.position()
        self.assertNotEqual(before[0], after[0])
        self.assertEqual(after[1], 0)
        self.assertIsNone(log.clock_row())


class ClockTest(unittest.TestCase):
    def test_controls(self):
        clock = SimClock(fresh_log(), cache_seconds=0)
        clock.set(NOW)
        clock.pause()
        frozen = clock.now()
        self.assertEqual(clock.now(), frozen)
        clock.advance(parse_duration("1h30m"))
        self.assertEqual(clock.now(), frozen + dt.timedelta(minutes=90))
        clock.set_rate(60)
        self.assertTrue(clock.state().paused)
        clock.resume()
        self.assertGreaterEqual(clock.now(), frozen + dt.timedelta(minutes=90))
        with self.assertRaises(ValueError):
            clock.set_rate(0)

    def test_durations(self):
        self.assertEqual(parse_duration("15"), dt.timedelta(minutes=15))
        self.assertEqual(parse_duration("-10m"), dt.timedelta(minutes=-10))
        self.assertEqual(parse_duration("1d2h"), dt.timedelta(hours=26))
        with self.assertRaises(ValueError):
            parse_duration("soon")


class ProjectorTest(unittest.TestCase):
    def test_incremental_and_reset(self):
        log = fresh_log()
        p = Projector(log)
        log.publish(Change("wms.order", "I", row=order()), "test", NOW)
        self.assertEqual(p.catch_up(), 1)
        self.assertEqual(p.catch_up(), 0)
        log.publish(Change("wms.order", "U", "9901", {"Shipped Qty": 100}), "test", NOW)
        log.publish(Change("wms.order", "I", row=order("9902")), "test", NOW)
        log.publish(Change("wms.order", "D", "9901"), "test", NOW)
        self.assertEqual(p.catch_up(), 3)
        self.assertEqual(list(p.tables["wms.order"]), ["9902"])
        log.reset()
        p.catch_up()
        self.assertEqual(p.tables["wms.order"], {})

    def test_arrival_order_does_not_change_the_result(self):
        changes = seed.build()
        shuffled = changes[:]
        random.Random(7).shuffle(shuffled)
        results = []
        for batch in (changes, shuffled):
            log = fresh_log()
            log.publish_many(batch, "test", NOW)
            p = Projector(log)
            p.catch_up()
            res = engine.run(src=p.source_data(), use_ai=False)
            results.append(sorted((f.id, f.units) for f in res.findings))
        self.assertEqual(results[0], results[1])


class SeedTest(unittest.TestCase):
    def test_seed_raises_exactly_the_designed_flags(self):
        log = seeded_log()
        p = Projector(log)
        p.catch_up()
        res = engine.run(src=p.source_data(), use_ai=False)
        self.assertEqual({f.id for f in res.findings}, set(seed.EXPECTED))
        self.assertEqual(res.kpis["sales_date"], seed.DEFAULT_SALES_DATE.isoformat())
        flagged = {f.sku for f in res.findings}
        self.assertTrue(set(seed.CLEAN_SKUS).isdisjoint(flagged))
        self.assertEqual(SimClock(log).state().anchor_sim, seed.start_time(seed.DEFAULT_SALES_DATE))

    def test_seed_needs_an_empty_log(self):
        with self.assertRaises(ValueError):
            seed.seed(seeded_log())

    def test_seed_is_synthetic(self):
        for c in seed.build():
            row = c.row
            sku = row.get("FG Oracle Ref") or row.get("OracleFGNumber") or row.get("Item Nbr")
            if sku:
                self.assertTrue(str(sku).strip().startswith("9001"), sku)
            desc = row.get("FG Description") or row.get("OracleFGDescription") or row.get("Description")
            if desc:
                self.assertTrue(desc.startswith("SIM "), desc)

    def test_empty_log_runs(self):
        p = Projector(fresh_log())
        p.catch_up()
        res = engine.run(src=p.source_data(), use_ai=False)
        self.assertEqual(res.findings, [])


class StoreTest(unittest.TestCase):
    def store(self) -> tuple[Store, EventLog]:
        log = seeded_log()
        return Store(sources.EventFeed(log.path)), log

    def test_worklist_reacts_to_an_event(self):
        store, log = self.store()
        self.assertIn("900107", store.rows)
        version = store.version
        store.refresh_if_sources_changed()
        self.assertEqual(store.version, version)   # nothing published: no reload
        # Route 27 goes back to its original order: WMS now matches MIPS for 900107.
        log.publish(Change("wms.item_order", "U", "WL-0015", {"Order Qty": 800}), "test", NOW)
        store.refresh_if_sources_changed()
        self.assertNotIn("900107", store.rows)
        changed = store.changes_since(version, "someone")
        self.assertIn("900107", changed)
        self.assertEqual(store.feed.seq, log.position()[1])

    def test_ack_sla_follows_the_simulated_clock(self):
        store, log = self.store()
        clock = SimClock(log, cache_seconds=0)
        store.feed.clock.cache_seconds = 0
        clock.pause()
        _, f = store.find("R05-900102")
        self.assertEqual(store.ack_state(f)["state"], "due")
        clock.advance(dt.timedelta(minutes=config.HIGH_ACK_MINUTES + 1))
        self.assertEqual(store.ack_state(f)["state"], "overdue")


@unittest.skipUnless(any(config.DATA_DIR.glob(config.SOURCES["mips_orders"])), "client extracts not present")
class ExcelFeedTest(unittest.TestCase):
    def test_validation_run_record_counts(self):
        src = sources.ExcelFeed().load()
        self.assertEqual((len(src.mips), len(src.fg_runs), len(src.batches), len(src.fill_orders)),
                         (157, 86, 36, 18))
        self.assertIsInstance(src, ingest.SourceData)


if __name__ == "__main__":
    unittest.main()
