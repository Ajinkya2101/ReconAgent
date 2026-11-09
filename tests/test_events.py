"""Event feed and workbook feed: contract, log, clock, projector, seed, and the worklist reacting.

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
import unittest
from pathlib import Path

import openpyxl

from recon_agent import config, engine, ingest, sources
from recon_agent.events import contract, seed
from recon_agent.events.clock import SimClock, parse_duration
from recon_agent.events.log import Change, EventLog
from recon_agent.events.projector import Projector
from recon_agent.web.store import Store

from recon_agent import config as _config
assert "recon-tests-" in str(_config.FEEDBACK_FILE) and not _config.AI_ENABLED, "tests are not isolated"

NOW = dt.datetime(2026, 9, 11, 20, 0)
WO = "mips.work_order"   # the entity the log tests use


def fresh_log() -> EventLog:
    return EventLog(Path(tempfile.mkdtemp(prefix="recon-events-")) / "events.db")


def seeded_log() -> EventLog:
    log = fresh_log()
    seed.seed(log)
    return log


def order(key: str = "9901", qty: int = 100) -> dict:
    """A MIPS work order row."""
    return {"Work Order Number": key, "FG Oracle Ref": "76108", "Work Order Quantity": qty,
            "Production Date": "2026-09-12"}


def run(log: EventLog):
    p = Projector(log)
    p.catch_up()
    return engine.run(src=p.source_data(), use_ai=False)


def write_workbook(log: EventLog, path: Path) -> Path:
    """The source rows laid out as the business team's workbook: one sheet per entity, their column
    names (padded as theirs are), no synthetic key columns, Sales Orders starting in column B."""
    p = Projector(log, stream="source")
    p.catch_up()
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    padded = {"Quantity": "Quantity ", "Allocated quantity for SO": "Allocated quantity  for SO ",
              "Available quantity": "Available quantity ", "Work order start date": "Work order start date "}
    for e in contract.ENTITIES.values():
        ws = wb.create_sheet(e.sheet)
        cols = [c for c in e.columns if not (e.synthetic_key and c == e.key)]
        lead = [None] if e.sheet == "Sales Orders" else []
        ws.append(lead + [padded.get(c, c) for c in cols])
        for row in p.rows(e.name):
            ws.append(lead + [row[c] for c in cols])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


class ContractTest(unittest.TestCase):
    def test_full_typed_image(self):
        row = contract.validate(contract.entity(WO), order(qty="120"))
        self.assertEqual(row["Work Order Quantity"], 120)
        self.assertEqual(row["Production Date"], dt.date(2026, 9, 12))
        self.assertIsNone(row["Work Order Status"])
        self.assertEqual(len(row), len(contract.entity(WO).columns))

    def test_rejects_unknown_and_missing_columns(self):
        e = contract.entity(WO)
        with self.assertRaises(contract.ContractError):
            contract.validate(e, {**order(), "Bogus": 1})
        with self.assertRaises(contract.ContractError):
            contract.validate(e, {"Work Order Number": "1"})
        with self.assertRaises(contract.ContractError):
            contract.validate(e, order(qty="lots"))

    def test_round_trip_keeps_values(self):
        e = contract.entity("odi.batch_mapping")
        change = next(c for c in seed.build() if c.entity == e.name and c.row["Work Order Number"])
        image = contract.encode(contract.validate(e, change.row))
        self.assertEqual(contract.encode(contract.validate(e, image)), image)
        decoded = contract.decode(e, image)
        self.assertIsInstance(decoded["Schedule Date"], dt.datetime)   # as openpyxl reads dates
        self.assertIsInstance(decoded["Wrapping End Date Time"], dt.datetime)

    def test_one_entity_per_workbook_sheet(self):
        self.assertEqual({e.sheet for e in contract.ENTITIES.values()},
                         {"ITEM Master", "Sales Orders", "Oracle Planning & Forecast", "ODI Batch Data Entry Mapping",
                          "WMS", "MIPS"})
        self.assertEqual(set(contract.SOURCES), {e.source for e in contract.ENTITIES.values()})

    def test_bool_parsing(self):
        self.assertIs(contract.coerce("yes", contract.BOOL), True)
        self.assertIs(contract.coerce("F", contract.BOOL), False)


class LogTest(unittest.TestCase):
    def test_insert_update_delete(self):
        log = fresh_log()
        ev = log.publish(Change(WO, "I", row=order()), "test", NOW)
        self.assertEqual((ev.seq, ev.op, ev.key, ev.source), (1, "I", "9901", "MIPS"))
        ev = log.publish(Change(WO, "U", "9901", {"Work Order Quantity": 150}), "test", NOW)
        self.assertEqual((ev.before["Work Order Quantity"], ev.after["Work Order Quantity"]), (100, 150))
        self.assertIsNone(log.publish(Change(WO, "U", "9901", {"Work Order Quantity": "150"}), "test", NOW))
        ev = log.publish(Change(WO, "D", "9901"), "test", NOW)
        self.assertIsNone(ev.after)
        self.assertIsNone(log.current(WO, "9901"))
        self.assertEqual(log.position()[1], 3)

    def test_rejects_invalid_changes_atomically(self):
        log = fresh_log()
        log.publish(Change(WO, "I", row=order()), "test", NOW)
        for bad in (Change(WO, "I", row=order()),                                  # duplicate key
                    Change(WO, "U", "nope", {"Work Order Quantity": 1}),           # unknown key
                    Change(WO, "U", "9901", {"Work Order Number": "9902"}),        # key change
                    Change(WO, "X", "9901")):                                      # unknown op
            with self.assertRaises(contract.ContractError):
                log.publish(bad, "test", NOW)
        with self.assertRaises(contract.ContractError):   # second change fails, so the first is rolled back
            log.publish_many([Change(WO, "I", row=order("9902")), Change(WO, "I", row=order("9902"))], "test", NOW)
        self.assertEqual(log.position()[1], 1)

    def test_append_only(self):
        log = fresh_log()
        log.publish(Change(WO, "I", row=order()), "test", NOW)
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
        log.publish(Change(WO, "I", row=order()), "test", NOW)
        self.assertEqual(p.catch_up(), 1)
        self.assertEqual(p.catch_up(), 0)
        log.publish(Change(WO, "U", "9901", {"Work Order Status": "Released"}), "test", NOW)
        log.publish(Change(WO, "I", row=order("9902")), "test", NOW)
        log.publish(Change(WO, "D", "9901"), "test", NOW)
        self.assertEqual(p.catch_up(), 3)
        self.assertEqual(list(p.tables[WO]), ["9902"])
        log.reset()
        p.catch_up()
        self.assertEqual(p.tables[WO], {})

    def test_arrival_order_does_not_change_the_result(self):
        changes = seed.build()
        shuffled = changes[:]
        random.Random(7).shuffle(shuffled)
        results = []
        for batch in (changes, shuffled):
            log = fresh_log()
            log.publish_many(batch, "seed", NOW)
            results.append(sorted((f.id, f.units) for f in run(log).findings))
        self.assertEqual(results[0], results[1])


class SeedTest(unittest.TestCase):
    def test_seed_raises_exactly_the_designed_flags(self):
        log = seeded_log()
        res = run(log)
        self.assertEqual({f.id for f in res.findings}, set(seed.EXPECTED))
        self.assertEqual(res.kpis["sales_date"], seed.DEFAULT_SALES_DATE.isoformat())
        self.assertTrue(set(seed.CLEAN_SKUS).isdisjoint({f.sku for f in res.findings}))
        self.assertEqual(sorted(res.positions), ["76105", "76108", "950513"])   # forecast-only items: no position
        self.assertEqual(SimClock(log).state().anchor_sim, seed.start_time(seed.DEFAULT_SALES_DATE))

    def test_the_control_sku_is_the_workbook_example(self):
        p = run(seeded_log()).positions["76108"]
        # Demand_to_Delivery_Flow_76108.docx: (14,339 + 41,769) - 473 - 7,000 = 48,635.
        self.assertEqual((p.so_qty, p.forecast_qty, p.demand), (14339, 41769, 56108))
        self.assertEqual((p.allocated, p.demand_left, p.available, p.shortfall), (473, 55635, 7000, 48635))
        self.assertEqual((p.mips_demand, p.wo_qty, p.balance), (55635, 48635, 0))
        self.assertEqual((len(p.orders), len(p.odi)), (11, 12))

    def test_seed_needs_an_empty_log(self):
        with self.assertRaises(ValueError):
            seed.seed(seeded_log())

    def test_another_sales_date_moves_every_date(self):
        log = fresh_log()
        seed.seed(log, dt.date(2026, 9, 19))
        res = run(log)
        self.assertEqual(res.kpis["sales_date"], "2026-09-19")
        self.assertEqual({f.rule for f in res.findings}, {f.split("-")[0] for f in seed.EXPECTED})
        self.assertIn("R12-WO-UBD-20260919-003", {f.id for f in res.findings})

    def test_empty_log_runs(self):
        self.assertEqual(run(fresh_log()).findings, [])


class StoreTest(unittest.TestCase):
    def store(self) -> tuple[Store, EventLog]:
        log = seeded_log()
        return Store(sources.EventFeed(log.path)), log

    def test_worklist_reacts_to_an_event(self):
        store, log = self.store()
        self.assertNotIn("76108", store.rows)   # the control SKU reconciles
        version = store.version
        store.refresh_if_sources_changed()
        self.assertEqual(store.version, version)   # nothing published: no reload
        log.publish(Change("wms.inventory", "U", "INV-0001", {"On hand": 7373}), "test", NOW)
        store.refresh_if_sources_changed()
        self.assertEqual([f.id for f in store.rows["76108"].flags], ["R10-INV-0001"])
        self.assertIn("76108", store.changes_since(version, "someone"))
        self.assertEqual(store.feed.seq, log.position()[1])

    def test_ack_sla_follows_the_simulated_clock(self):
        store, log = self.store()
        clock = SimClock(log, cache_seconds=0)
        store.feed.clock.cache_seconds = 0
        clock.pause()
        _, f = store.find("R05-950513")
        self.assertEqual(store.ack_state(f)["state"], "due")
        clock.advance(dt.timedelta(minutes=config.HIGH_ACK_MINUTES + 1))
        self.assertEqual(store.ack_state(f)["state"], "overdue")


class WorkbookFeedTest(unittest.TestCase):
    def test_the_workbook_and_the_event_feed_reconcile_the_same(self):
        log = seeded_log()
        data = Path(tempfile.mkdtemp(prefix="recon-xlsx-"))
        write_workbook(log, data / "BIMBO_MIPS_WMS_ODI_Data_TEST.xlsx")
        src = sources.ExcelFeed(data).load()
        self.assertIsInstance(src, ingest.SourceData)
        from_xlsx, from_events = engine.run(src=src, use_ai=False), run(log)
        self.assertEqual(sorted((f.id, f.units) for f in from_xlsx.findings),
                         sorted((f.id, f.units) for f in from_events.findings))
        self.assertEqual({k: v.to_row() for k, v in from_xlsx.positions.items()},
                         {k: v.to_row() for k, v in from_events.positions.items()})

    def test_the_workbook_is_found_anywhere_under_the_data_directory(self):
        data = Path(tempfile.mkdtemp(prefix="recon-xlsx-"))
        feed = sources.ExcelFeed(data)
        self.assertEqual(feed.position(), 0.0)
        write_workbook(seeded_log(), data / "new_dummy_data" / "BIMBO_MIPS_WMS_ODI_Data_TEST.xlsx")
        self.assertGreater(feed.position(), 0.0)
        self.assertEqual(len(feed.load().sales_orders), 18)


@unittest.skipUnless(any(config.DATA_DIR.glob(config.WORKBOOK_GLOB)), "the 9 Nov mapping workbook is not present")
class BusinessWorkbookTest(unittest.TestCase):
    def test_76108_reconciles_as_the_business_team_worked_it(self):
        res = engine.run(src=sources.ExcelFeed().load(), use_ai=False)
        p = res.positions["76108"]
        self.assertEqual((p.so_qty, p.forecast_qty, p.allocated, p.available, p.shortfall),
                         (14339, 41769, 473, 7000, 48635))
        self.assertEqual((p.mips_demand, p.wo_qty, p.balance), (55635, 48635, 0))
        self.assertEqual(res.findings, [])
        self.assertEqual(list(res.positions), ["76108"])   # the other nine forecasts are planning only


if __name__ == "__main__":
    unittest.main()
