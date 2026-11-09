"""Nothing on screen is static: every figure and explanation follows the source data as it changes.

- Any edit to a source row that touches a worklist row reaches open screens, even when no flag
  appears or clears (a batch count, a wrap time, a customer name).
- The rules read every ODI row of a work order, not only the first.
- Scenarios change rows relative to what they hold, and what a scenario will raise is worked out
  on the current data, never written down.
- The live redraw: queue, day totals, briefing and the open detail, or a note that the row cleared.
- Exports are built from the current rows, and the database view recomputes the position.

    python -m unittest tests.test_live -v
"""
from __future__ import annotations

import os
import tempfile

os.environ["RECON_AI"] = "off"
os.environ.setdefault("RECON_TEST_DIR", tempfile.mkdtemp(prefix="recon-tests-"))
os.environ["RECON_FEEDBACK_FILE"] = os.path.join(os.environ["RECON_TEST_DIR"], "feedback.json")
os.environ["RECON_USERS_FILE"] = os.path.join(os.environ["RECON_TEST_DIR"], "users.json")
os.environ["RECON_COOKIE_SECURE"] = "off"

import datetime as dt
import io
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

import openpyxl
from fastapi.testclient import TestClient

from recon_agent import security, sources, users
from recon_agent.events import export, scenarios, seed
from recon_agent.events.log import Change, EventLog
from recon_agent.web.store import AI_READY, DATA_CHANGED, Store

from recon_agent import config as _config
assert "recon-tests-" in str(_config.FEEDBACK_FILE) and not _config.AI_ENABLED, "tests are not isolated"

NOW = dt.datetime(2026, 9, 11, 20, 0)
WO1, WO2, WO3 = (seed.wo_number(seed.DEFAULT_SALES_DATE, n) for n in (1, 2, 3))
PASSWORD = "test-password-1"


def seeded_log() -> EventLog:
    log = EventLog(Path(tempfile.mkdtemp(prefix="recon-live-")) / "events.db")
    seed.seed(log)
    return log


def flag(store: Store, fid: str):
    return store.find(fid)[1]


class ChangeReachesScreensTest(unittest.TestCase):
    def setUp(self):
        self.log = seeded_log()
        self.store = Store(sources.EventFeed(self.log.path))

    def edit(self, entity: str, key: str, cols: dict) -> set[str]:
        version = self.store.version
        self.log.publish(Change(entity, "U", key, cols), "simulator:test", NOW)
        self.store.refresh_if_sources_changed()
        return self.store.feed_changes_since(version)

    def test_an_edit_that_moves_no_flag_still_reaches_the_screen(self):
        # Each of these leaves every flag ID and its units as they were.
        cases = [("odi.batch_mapping", "ODI-0013", {"Act Batches Mixed": 6}, "950513"),        # R11 has 0 units
                 ("odi.batch_mapping", "ODI-0018", {"Wrapping End Date Time": "2026-09-10T11:00:00"}, "76105"),
                 ("buss.sales_order", "2263246-1", {"Customer Name": "ALBANY NY NEW DEPOT"}, "950513")]
        for entity, key, cols, row in cases:
            with self.subTest(entity=entity, key=key):
                self.assertEqual(self.edit(entity, key, cols), {row, DATA_CHANGED})
        self.assertIn("ODI-0013: 6 of 4", flag(self.store, f"R11-{WO2}").recommendation)   # rows now disagree
        self.assertIn("09/10 11:00", flag(self.store, f"R12-{WO3}").recommendation)
        self.assertEqual(self.store.rows["950513"].position.orders[0].customer, "ALBANY NY NEW DEPOT")

    def test_the_first_row_of_an_empty_worklist_is_announced(self):
        log = EventLog(Path(tempfile.mkdtemp(prefix="recon-live-")) / "events.db")
        seed.seed(log)
        for c in (Change("mips.work_order", "U", WO2, {"Work Order Quantity": 3700}),
                  Change("wms.inventory", "U", "INV-0003", {"On hand": 1800}),
                  Change("odi.batch_mapping", "U", "ODI-0019", {"Quantity": 900}),
                  Change("odi.batch_mapping", "U", "ODI-0018", {"Requested Ship Date": "2026-09-11"})):
            log.publish(c, "test", NOW)
        for k in ("ODI-0013", "ODI-0014", "ODI-0015", "ODI-0016"):
            log.publish(Change("odi.batch_mapping", "U", k, {"Act Batches Mixed": 4, "Work Order Quantity": 3700}),
                        "test", NOW)
        store = Store(sources.EventFeed(log.path))
        self.assertEqual(store.rows, {})
        version = store.version
        log.publish(Change("wms.inventory", "U", "INV-0001", {"On hand": 7373}), "simulator:test", NOW)
        store.refresh_if_sources_changed()
        self.assertEqual(store.feed_changes_since(version), {"76108", DATA_CHANGED})

    def test_a_change_to_a_planning_only_item_changes_nothing_on_screen(self):
        self.assertEqual(self.edit("oracle.forecast", "FC003", {"Forecast / Required Quantity": 900}), set())

    def test_ai_text_is_asked_again_when_the_wording_changes_but_not_the_units(self):
        f = flag(self.store, f"R12-{WO3}")
        before = self.store._ai_key(f)
        self.edit("odi.batch_mapping", "ODI-0018", {"Wrapping End Date Time": "2026-09-10T11:00:00"})
        after = self.store._ai_key(flag(self.store, f"R12-{WO3}"))
        self.assertEqual(before[:2], after[:2])   # same flag, same units
        self.assertNotEqual(before, after)


class EveryOdiRowTest(unittest.TestCase):
    def setUp(self):
        self.log = seeded_log()
        self.store = Store(sources.EventFeed(self.log.path))

    def publish(self, entity, key, cols):
        self.log.publish(Change(entity, "U", key, cols), "test", NOW)
        self.store.refresh_if_sources_changed()

    def test_a_stale_row_that_is_not_the_first_is_found_and_counted(self):
        self.publish("odi.batch_mapping", "ODI-0007", {"Work Order Quantity": 48000})
        f = flag(self.store, f"R07-{WO1}")
        self.assertEqual(f.units, 635)
        self.assertEqual((f.evidence["odi_rows_stale"], f.evidence["stale_rows"]), (1, "ODI-0007"))
        self.assertIn("1 of its 11 ODI rows still carries 48,000", f.recommendation)

    def test_batch_rows_that_disagree_are_named(self):
        self.publish("odi.batch_mapping", "ODI-0002", {"Act Batches Mixed": 2})
        f = flag(self.store, f"R11-{WO1}")
        self.assertEqual((f.evidence["scheduled"], f.evidence["mixed"]), (1, 2))
        self.assertIn("ODI-0002: 2 of 1", f.recommendation)
        self.assertIn("do not agree", f.recommendation)

    def test_the_latest_wrap_of_the_late_rows_is_reported(self):
        self.publish("odi.batch_mapping", "ODI-0019", {"Requested Ship Date": "2026-09-09",
                                                       "Wrapping End Date Time": "2026-09-10T12:30:00"})
        f = flag(self.store, f"R12-{WO3}")
        self.assertEqual(f.evidence["wrapping_end"], "09/10 12:30")
        self.assertEqual(f.units, 2400 + 800)   # ODI carries the Colonie order at 800 (R02)


class LiveScenarioTest(unittest.TestCase):
    def test_changes_are_relative_to_the_current_row(self):
        log = seeded_log()
        log.publish(Change("buss.sales_order", "U", "2263245-1", {"Order Quantity": 7300}), "test", NOW)
        scenarios.run(log, scenarios.SCENARIOS["S1"])
        self.assertEqual(log.current("buss.sales_order", "2263245-1")["Order Quantity"], 7900)
        scenarios.run(log, scenarios.SCENARIOS["S4"])
        inv = log.current("wms.inventory", "INV-0001")
        self.assertEqual((inv["Available quantity"], inv["Allocated quantity"]), (6340, 1133))

    def test_the_preview_follows_the_data(self):
        log = seeded_log()
        pv = scenarios.preview(log, scenarios.SCENARIOS["S1"])
        self.assertEqual({f.id: f.units for f in pv["appears"]},
                         {"R02-2263245-1": 600, "R04-76108": 600, "R05-76108": 600})
        self.assertEqual(log.current("buss.sales_order", "2263245-1")["Order Quantity"], 7200)   # untouched
        # An operator edit first: the same scenario now leaves different figures.
        log.publish(Change("buss.sales_order", "U", "2263245-1", {"Order Quantity": 7300}), "test", NOW)
        pv = scenarios.preview(log, scenarios.SCENARIOS["S1"])
        self.assertEqual(pv["appears"], [])
        self.assertEqual({f.id: f.units for f in pv["changes"]},
                         {"R02-2263245-1": 700, "R04-76108": 700, "R05-76108": 700})
        self.assertIn("Changed: R02-2263245-1 (700 units)", scenarios.describe(pv))

    def test_a_scenario_that_cannot_run_says_why(self):
        log = seeded_log()
        log.publish(Change("buss.sales_order", "D", "2263256-1"), "test", NOW)
        pv = scenarios.preview(log, scenarios.SCENARIOS["S3"])
        self.assertIn("does not exist", pv["error"])


def sign_in(client: TestClient, username: str, role: str) -> None:
    if not users.get(username):
        with mock.patch.object(security, "PBKDF2_ITERATIONS", 1000):
            users.add(username, username.title(), role, PASSWORD)
    r = client.post("/signin", data={"username": username, "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303, r.text


class LiveRedrawTest(unittest.TestCase):
    def setUp(self):
        from recon_agent.web.app import create_app as worklist
        self.log = seeded_log()
        with mock.patch.object(sources, "open_feed", lambda *_: sources.EventFeed(self.log.path)):
            self.app = worklist()
        self.store = self.app.state.store
        self.client = TestClient(self.app)
        sign_in(self.client, "lena", "planner")
        self.hx = {"HX-Request": "true"}

    def publish(self, entity, key, cols):
        self.log.publish(Change(entity, "U", key, cols), "simulator:test", NOW)
        self.store.refresh_if_sources_changed()

    def test_a_feed_change_redraws_itself(self):
        version = self.store.version
        self.publish("odi.batch_mapping", "ODI-0013", {"Act Batches Mixed": 6})
        changes = self.client.get(f"/changes?since={version}", headers=self.hx).text
        self.assertIn("data-live-refresh", changes)          # applied by itself, no "Update" pill
        self.assertNotIn("changes-pill", changes)
        r = self.client.get(f"/queue?since={version}&live=1&open=950513", headers=self.hx).text
        self.assertIn('id="detail" hx-swap-oob="innerHTML"', r)
        self.assertIn("ODI-0013: 6 of 4", r)
        self.assertIn('id="days" hx-swap-oob="outerHTML"', r)
        self.assertIn('id="briefing"', r)
        # A row that did not change is not redrawn.
        r = self.client.get(f"/queue?since={version}&live=1&open=76105", headers=self.hx).text
        self.assertNotIn('id="detail" hx-swap-oob', r)

    def test_an_open_row_that_clears_says_so(self):
        version = self.store.version
        self.publish("wms.inventory", "INV-0001", {"On hand": 7373})   # 76108 gets a row
        self.assertIn("76108", self.store.rows)
        version = self.store.version
        self.publish("wms.inventory", "INV-0001", {"On hand": 7473})   # and loses it again
        r = self.client.get(f"/queue?since={version}&live=1&open=76108", headers=self.hx).text
        self.assertIn("76108 has no flags now", r)
        self.assertIn("work order 48,635", r)

    def test_ai_text_redraws_the_open_detail(self):
        version = self.store.version
        self.store._bump(AI_READY, "ai")
        r = self.client.get(f"/queue?since={version}&live=1&open=950513", headers=self.hx).text
        self.assertIn('id="detail" hx-swap-oob="innerHTML"', r)

    def test_the_assistant_suggests_questions_about_the_current_queue(self):
        page = self.client.get("/").text
        top = self.store.ordered_rows()[0]
        self.assertIn(f'data-ask="Why does {top.sku} have the flag', page)


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.log = seeded_log()
        self.log.publish(Change("buss.sales_order", "U", "2263245-1", {"Order Quantity": 7800}), "test", NOW)

    def test_the_workbook_carries_the_current_rows_and_flags(self):
        wb = openpyxl.load_workbook(io.BytesIO(export.workbook(self.log)))
        orders = list(wb["Sales Orders"].iter_rows(values_only=True))
        header = orders[0]
        albany = next(r for r in orders if r[header.index("SO Line ID")] == "2263245-1")
        self.assertEqual(albany[header.index("Order Quantity")], 7800)
        flags = {r[0] for r in wb["Flags"].iter_rows(min_row=2, values_only=True)}
        self.assertTrue({"R02-2263245-1", "R04-76108", "R05-76108"} <= flags)

    def test_the_database_view_recomputes_the_position(self):
        path = Path(tempfile.mkdtemp()) / "x.sqlite"
        path.write_bytes(export.sqlite_db(self.log))
        with sqlite3.connect(path) as con:
            q = "SELECT total_demand, shortfall, work_order_qty, balance FROM v_sku_position WHERE sku = '76108'"
            self.assertEqual(con.execute(q).fetchone(), (56708, 49235, 48635, -600))
            con.execute("UPDATE mips_work_order SET work_order_quantity = 49235 WHERE fg_oracle_ref = '76108'")
            self.assertEqual(con.execute(q).fetchone()[3], 0)   # a view, not a stored figure

    def test_the_simulator_serves_the_export_to_operators_only(self):
        from recon_agent.simulator.app import create_app
        client = TestClient(create_app(self.log.path))
        self.assertEqual(client.get("/export/xlsx", follow_redirects=False).status_code, 303)
        sign_in(client, "otto", "planner")
        for kind, magic in (("xlsx", b"PK"), ("sqlite", b"SQLite format 3"), ("sql", b"-- Source systems")):
            r = client.get(f"/export/{kind}")
            self.assertEqual(r.status_code, 200, kind)
            self.assertTrue(r.content.startswith(magic), kind)
            self.assertIn("attachment", r.headers["content-disposition"])
        self.assertIn("INSERT INTO buss_sales_order", client.get("/export/sql").text)
        self.assertEqual(client.get("/export/pdf").status_code, 404)
        page = client.get("/scenarios").text
        self.assertIn("If you run it now", page)
        self.assertIn("R02-2263245-1", page)   # S1 on the current data (Albany at 7,800): R02 moves


if __name__ == "__main__":
    unittest.main()
