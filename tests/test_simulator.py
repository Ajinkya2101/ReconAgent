"""Source simulator (System B): publishing through the operator app, and the worklist side of the
real-time loop (consumer commits, latency, server push, debounced AI narration).

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
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from recon_agent import ai, security, sources, users
from recon_agent.events import seed
from recon_agent.events.clock import SimClock
from recon_agent.events.log import Change, EventLog
from recon_agent.simulator.app import create_app, next_key
from recon_agent.web.store import AI_READY, Store

from recon_agent import config as _config
assert "recon-tests-" in str(_config.FEEDBACK_FILE) and not _config.AI_ENABLED, "tests are not isolated"


PASSWORD = "test-password-1"


def sign_in(client: TestClient, username: str, role: str = "supervisor", name: str | None = None) -> None:
    """Sign in through the real form, creating the account first (cheap hash: tests only)."""
    if not users.get(username):
        with mock.patch.object(security, "PBKDF2_ITERATIONS", 1000):
            users.add(username, name or username.title(), role, PASSWORD)
    r = client.post("/signin", data={"username": username, "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303, r.text


with mock.patch.object(security, "PBKDF2_ITERATIONS", 1000):   # an accounts file exists: no demo seeding
    if not users.get("ana"):
        users.add("ana", "Ana", "supervisor", PASSWORD)

NOW = dt.datetime(2026, 9, 11, 20, 0)
WO1 = seed.wo_number(seed.DEFAULT_SALES_DATE, 1)


def seeded_log() -> EventLog:
    log = EventLog(Path(tempfile.mkdtemp(prefix="recon-sim-")) / "events.db")
    seed.seed(log)
    return log


def field(entity_columns, col: str) -> str:
    return f"f{list(entity_columns).index(col)}"


class SimulatorTest(unittest.TestCase):
    def setUp(self):
        self.log = seeded_log()
        self.client = TestClient(create_app(self.log.path))
        sign_in(self.client, "ana", name="Ana")
        from recon_agent.events import contract
        self.contract = contract

    def form(self, entity: str, key: str, **changes) -> dict:
        """The editor form for a row as the page renders it, with some columns changed."""
        e = self.contract.ENTITIES[entity]
        row = self.contract.decode(e, self.log.current(entity, key))
        from recon_agent.simulator.app import field_value
        data = {f"f{i}": field_value(row[c], k) for i, (c, k) in enumerate(e.columns.items())}
        for col, v in changes.items():
            data[field(e.columns, col.replace("_", " ") if col not in e.columns else col)] = str(v)
        data["base_seq"] = str(self.log.tail(100)[-1].seq if False else self._row_seq(entity, key))
        data["derive"] = "1"
        return data

    def _row_seq(self, entity, key) -> int:
        from recon_agent.events.projector import Projector
        p = Projector(self.log)
        p.catch_up()
        return p.row_seq[entity][key]

    def test_requires_an_operator(self):
        anon = TestClient(create_app(self.log.path))
        r = anon.get("/t/wms.inventory", follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_unchanged_form_publishes_nothing(self):
        head = self.log.position()[1]
        r = self.client.post(f"/t/mips.work_order/edit/{WO1}", data=self.form("mips.work_order", WO1))
        self.assertIn("No change", r.text)
        self.assertEqual(self.log.position()[1], head)

    def test_edit_publishes_the_changed_columns_only(self):
        r = self.client.post("/t/odi.batch_mapping/edit/ODI-0002",
                             data={**self.form("odi.batch_mapping", "ODI-0002", Quantity=7500), "note": "recount"})
        self.assertIn("Published event", r.text)
        ev = self.log.tail(1)[0]
        self.assertEqual((ev.op, ev.key, ev.note), ("U", "ODI-0002", "recount"))
        self.assertTrue(ev.producer.startswith("simulator:Ana"))
        self.assertEqual({k for k in ev.after if ev.after[k] != ev.before[k]}, {"Quantity"})
        # The response refreshes the table and the feed out of band.
        self.assertIn('id="feed" class="feed" hx-swap-oob="true"', r.text)

    def test_stale_form_is_refused(self):
        stale = self.form("buss.sales_order", "2263245-1", **{"Order Quantity": 7900})
        self.log.publish(Change("buss.sales_order", "U", "2263245-1", {"Order Quantity": 7800}), "cli", NOW)
        head = self.log.position()[1]
        r = self.client.post("/t/buss.sales_order/edit/2263245-1", data=stale)
        self.assertIn("changed (event", r.text)
        self.assertEqual(self.log.position()[1], head)

    def test_bad_value_is_refused_and_kept_on_screen(self):
        head = self.log.position()[1]
        r = self.client.post("/t/wms.inventory/edit/INV-0001", data=self.form("wms.inventory", "INV-0001", **{"On hand": "lots"}))
        self.assertIn("Not saved", r.text)
        self.assertIn('value="lots"', r.text)
        self.assertEqual(self.log.position()[1], head)

    def test_insert_copy_and_delete(self):
        r = self.client.get("/t/odi.batch_mapping/new?clone=ODI-0002")
        self.assertIn('value="ODI-0022"', r.text)   # next free key in the entity's pattern (21 seeded rows)
        data = self.form("odi.batch_mapping", "ODI-0002", **{"ODI Entry ID": "ODI-0022", "Quantity": 500})
        r = self.client.post("/t/odi.batch_mapping/new", data=data)
        self.assertIn("Published event", r.text)
        self.assertIsNotNone(self.log.current("odi.batch_mapping", "ODI-0022"))
        r = self.client.post("/t/odi.batch_mapping/new", data=data)
        self.assertIn("already exists", r.text)
        self.client.post("/t/odi.batch_mapping/delete/ODI-0022", data={"note": "keyed by mistake"})
        self.assertIsNone(self.log.current("odi.batch_mapping", "ODI-0022"))

    def test_clock_controls(self):
        self.client.post("/clock", data={"action": "pause"})
        self.assertTrue(SimClock(self.log).state().paused)
        before = SimClock(self.log).now()
        self.client.post("/clock", data={"action": "advance", "value": "1h"})
        self.assertEqual(SimClock(self.log).now(), before + dt.timedelta(hours=1))
        r = self.client.post("/clock", data={"action": "rate", "value": "0"})
        self.assertIn("positive", r.text)

    def test_reset_reseeds(self):
        self.log.publish(Change("buss.sales_order", "D", "2263245-1"), "cli", NOW)
        r = self.client.post("/reset")
        self.assertEqual(r.headers.get("HX-Redirect"), "/t/buss.sales_order")
        self.assertEqual(self.log.position()[1], len(seed.build()))

    def test_poll_refreshes_the_table_only_when_the_log_moved(self):
        head = self.log.position()[1]
        self.assertNotIn('id="table"', self.client.get(f"/poll?entity=wms.inventory&seen={head}").text)
        self.assertIn('id="table"', self.client.get(f"/poll?entity=wms.inventory&seen={head - 1}").text)

    def test_next_key(self):
        from recon_agent.events import contract
        self.assertEqual(next_key(contract.ENTITIES["mips.work_order"], [WO1, WO1[:-1] + "3"]), WO1[:-1] + "4")
        self.assertEqual(next_key(contract.ENTITIES["odi.batch_mapping"], ["ODI-0009", "ODI-0021"]), "ODI-0022")


class RealtimeTest(unittest.TestCase):
    def test_worklist_commits_and_latency_is_measured(self):
        log = seeded_log()
        store = Store(sources.EventFeed(log.path))
        self.assertEqual(log.committed(sources.CONSUMER)[0], log.position()[1])
        self.assertEqual(log.latencies(sources.CONSUMER), [])   # seed events are not timed
        log.publish(Change("wms.inventory", "U", "INV-0003", {"On hand": 1800}), "simulator:test", NOW)
        self.assertEqual(log.latencies(sources.CONSUMER)[0][1], None)   # not processed yet
        store.refresh_if_sources_changed()
        seq, seconds = log.latencies(sources.CONSUMER)[0]
        self.assertEqual(seq, log.position()[1])
        self.assertGreaterEqual(seconds, 0)
        self.assertLess(seconds, 5)

    def test_stream_announces_a_new_version(self):
        log = seeded_log()
        with mock.patch.object(sources, "open_feed", lambda *_: sources.EventFeed(log.path)):
            from recon_agent.web.app import create_app as worklist
            app = worklist()
        client = TestClient(app)
        sign_in(client, "sam")
        store = app.state.store
        log.publish(Change("wms.inventory", "U", "INV-0003", {"On hand": 1800}), "cli", NOW)
        store.refresh_if_sources_changed()
        with client.stream("GET", "/stream?since=0") as r:
            lines = []
            for line in r.iter_lines():
                lines.append(line)
                if line.startswith("data:"):
                    break
        self.assertIn("event: changed", lines)
        self.assertIn(f"data: {store.version}", lines)
        r = client.get("/changes?since=0")
        self.assertIn('id="asof-detail"', r.text)   # the freshness line follows the feed
        self.assertIn(f"event #{log.position()[1]}", r.text)


class NarrationTest(unittest.TestCase):
    """The model is called once per quiet period, only for flags it has not explained."""

    def fake_model(self, top, kpis):
        self.calls.append([f.id for f in top])
        return {"shift_briefing": f"briefing {len(self.calls)}", "top_actions": ["act"],
                "by_id": {f.id: {"id": f.id, "explanation": f"AI {f.id}", "root_cause": "x", "owner": "o"}
                          for f in top},
                "source": "fake"}

    def setUp(self):
        self.calls = []
        patches = [mock.patch.object(ai, "credentials_available", lambda: True),
                   mock.patch.object(ai, "narrate_with_model", self.fake_model),
                   mock.patch("recon_agent.config.AI_DEBOUNCE_SECONDS", 1.0),
                   mock.patch("recon_agent.config.AI_MAX_WAIT_SECONDS", 5)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def wait_for(self, store, state, timeout=5.0):
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if store.narration_state == state and not store._ai_running and store._ai_timer is None:
                return
            time.sleep(0.05)
        self.fail(f"narration stayed {store.narration_state}")

    def test_debounced_and_cached(self):
        log = seeded_log()
        store = Store(sources.EventFeed(log.path))
        self.assertEqual(store.narration_state, "pending")
        # A burst of events inside the debounce window: one call, after it.
        for qty in (1850, 1820, 1800):
            log.publish(Change("wms.inventory", "U", "INV-0003", {"On hand": qty}), "cli", NOW)
            store.refresh_if_sources_changed()
        self.wait_for(store, "ai")
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(store.narration_is_ai)
        self.assertIn(AI_READY, store.changes_since(0, "someone"))
        _, f = store.find("R05-950513")
        self.assertEqual(f.narrative, "AI R05-950513")

        # A change that touches one SKU: everything else comes from the cache, one new call.
        log.publish(Change("wms.inventory", "U", "INV-0001", {"On hand": 7373}), "cli", NOW)
        store.refresh_if_sources_changed()
        self.assertEqual(store.find("R05-950513")[1].narrative, "AI R05-950513")   # cached at once
        self.assertFalse(store.narration_is_ai)   # run totals changed: rule briefing until the model answers
        self.wait_for(store, "ai")
        self.assertEqual(len(self.calls), 2)

        # Going back to an already explained state needs no call at all.
        log.publish(Change("wms.inventory", "U", "INV-0001", {"On hand": 7473}), "cli", NOW)
        store.refresh_if_sources_changed()
        self.assertEqual(store.narration_state, "ai")
        self.assertEqual(len(self.calls), 2)


if __name__ == "__main__":
    unittest.main()
