"""Write-back end to end: the worklist approves a correction, the simulator (standing in for
Oracle Integration) applies it over real HTTP, and the worklist confirms it only when the change
comes back through the feed. Also the failure paths: endpoint down, stale row, slow integration.

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
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import uvicorn
from fastapi.testclient import TestClient

from recon_agent import security, sources, users
from recon_agent.events import scenarios, seed
from recon_agent.events.log import Change, EventLog
from recon_agent.simulator.app import create_app as simulator
from recon_agent.web.store import Store

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

NOW = dt.datetime(2026, 11, 6, 20, 0)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WriteBackTest(unittest.TestCase):
    def setUp(self):
        self.log = EventLog(Path(tempfile.mkdtemp(prefix="recon-wb-")) / "events.db")
        seed.seed(self.log)
        scenarios.run(self.log, scenarios.SCENARIOS["S6"])   # R09 and R10 on 900112
        self.sim = simulator(self.log.path)
        port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(self.sim, host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=self.server.run, daemon=True).start()
        while not self.server.started:
            time.sleep(0.02)
        self.addCleanup(setattr, self.server, "should_exit", True)
        url = mock.patch("recon_agent.config.CORRECTIONS_URL", f"http://127.0.0.1:{port}/oic/corrections")
        url.start()
        self.addCleanup(url.stop)
        self.operator = TestClient(self.sim)
        sign_in(self.operator, "ana", name="Ana")
        self.store = Store(sources.EventFeed(self.log.path))

    def flags(self) -> set[str]:
        self.store.refresh_if_sources_changed()
        return {f.id for f in self.store.res.findings}

    def test_approved_correction_is_confirmed_by_the_feed(self):
        _, f = self.store.find("R09-900112")
        p = self.store.proposal(f)
        self.assertEqual((p.entity, p.key, p.changes), ("mips.order_line", "ML-0017", {"Order Quantity": 1100}))
        _, message = self.store.approve("R09-900112", "Pat Planner")
        self.assertIn("accepted", message)
        self.assertNotIn("R09-900112", self.flags())
        c = self.store.book.for_flag("R09-900112")
        self.assertEqual([h["status"] for h in c["history"]][-1], "Confirmed")
        self.assertIsNotNone(c["confirmed_seq"])
        self.assertEqual(self.store.decision(f)["decision"], "accept")   # approving accepts the flag
        src = self.log.current("mips.order_line", "ML-0017")
        self.assertEqual(src["Order Quantity"], 1100)
        self.assertNotEqual(src["Last Modify Date Time"], "2026-11-05T20:31:00")   # MIPS stamped it
        # A correction can cascade: ODI's copy of the sales-order quantity is now behind MIPS.
        self.assertIn("R16-900112", self.flags())

    def test_a_reset_retires_earlier_corrections(self):
        self.store.approve("R09-900112", "Pat Planner")
        self.log.reset()
        seed.seed(self.log)
        scenarios.run(self.log, scenarios.SCENARIOS["S6"])
        self.assertIn("R09-900112", self.flags())
        _, f = self.store.find("R09-900112")
        self.assertIsNone(self.store.correction(f))           # the old COR-0001 belongs to the old log
        self.assertIsNotNone(self.store.proposal(f))          # so the flag offers a fresh correction
        self.assertEqual(len(self.store.book.items), 1)       # still on file for the record

    def test_endpoint_down_then_retry(self):
        self.operator.post("/integration/oic", data={"mode": "down"})
        _, message = self.store.approve("R09-900112", "Pat Planner")
        self.assertIn("unavailable", message)
        c = self.store.book.for_flag("R09-900112")
        self.assertEqual(c["status"], "Failed")
        self.assertIn("R09-900112", self.flags())
        self.operator.post("/integration/oic", data={"mode": "accept"})
        self.store.retry(c["id"], "Pat Planner")
        self.assertNotIn("R09-900112", self.flags())
        self.assertEqual(self.store.book.items[c["id"]]["status"], "Confirmed")

    def test_stale_row_is_refused(self):
        # MIPS changes the line after the worklist computed its proposal but before approval reaches OIC.
        from recon_agent import corrections
        real_send = corrections.send

        def race(c, by):
            self.log.publish(Change("mips.order_line", "U", "ML-0017", {"Order Quantity": 950}), "cli", NOW)
            return real_send(c, by)

        with mock.patch("recon_agent.corrections.send", race):
            _, message = self.store.approve("R09-900112", "Pat Planner")
        self.assertIn("changed since", message)
        self.assertEqual(self.store.book.for_flag("R09-900112")["status"], "Failed")
        self.assertEqual(self.log.current("mips.order_line", "ML-0017")["Order Quantity"], 950)

    def test_slow_integration_keeps_it_sent_until_the_event_arrives(self):
        self.log.add_fault("delay", source="MIPS", seconds=0.6)
        self.store.approve("R09-900112", "Pat Planner")
        c = self.store.book.for_flag("R09-900112")
        self.assertEqual(c["status"], "Sent")                 # OIC said yes; the feed has not shown it
        self.assertIn("R09-900112", self.flags())
        deadline = time.monotonic() + 5                        # the simulator's pump delivers it
        while "R09-900112" in self.flags() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertNotIn("R09-900112", self.flags())
        self.assertEqual(self.store.book.items[c["id"]]["status"], "Confirmed")

    def test_only_planners_and_managers_approve(self):
        from recon_agent.web.app import create_app as worklist
        with mock.patch.object(sources, "open_feed", lambda *_: sources.EventFeed(self.log.path)):
            app = worklist()
        sup = TestClient(app)
        sign_in(sup, "sam", "supervisor")
        self.assertEqual(sup.post("/flags/R09-900112/correction").status_code, 403)
        detail = sup.get("/row/900112", headers={"HX-Request": "true"}).text
        self.assertIn("Proposed correction", detail)
        self.assertIn("A planner or manager approves", detail)
        planner = TestClient(app)
        sign_in(planner, "pat", "planner")
        r = planner.post("/api/corrections", json={"flag_id": "R09-900112"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["correction"]["status"], "Confirmed")
        self.assertEqual(planner.get(f"/api/corrections/{r.json()['correction']['id']}").json()["key"], "ML-0017")


class ResetSimulationTest(unittest.TestCase):
    def test_reset_from_the_worklist_returns_to_the_starting_day(self):
        from recon_agent import feedback
        from recon_agent.web.app import create_app as worklist
        log = EventLog(Path(tempfile.mkdtemp(prefix="recon-reset-")) / "events.db")
        seed.seed(log)
        with mock.patch.object(sources, "open_feed", lambda *_: sources.EventFeed(log.path)):
            app = worklist()
        store = app.state.store
        client = TestClient(app)
        sign_in(client, "rita", "planner", "Rita")
        # Change the day: fix route 27 (900107 drops out), claim and decide on other rows.
        log.publish(Change("wms.item_order", "U", "WL-0015", {"Order Qty": 800}), "test", NOW)
        store.refresh_if_sources_changed()
        self.assertNotIn("900107", store.rows)
        self.assertEqual(client.post("/row/900102/claim", headers={"HX-Request": "true"}).status_code, 200)
        client.post("/flags/R05-900102/ack", headers={"HX-Request": "true"})
        client.post("/flags/R05-900102/decision", data={"decision": "reject"}, headers={"HX-Request": "true"})
        self.assertIn("R05-900102", feedback.load()["decisions"])
        version = store.version

        page = client.get("/").text
        self.assertIn("Reset simulation", page)
        r = client.post("/simulation/reset", headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.headers["HX-Redirect"], "/")

        self.assertIn("900107", store.rows)                    # back to the seeded flags
        self.assertEqual(log.source_position()[1], seed.seed(EventLog(Path(tempfile.mkdtemp()) / "e.db")))
        self.assertEqual(store.claims, {})
        self.assertEqual(store.acks, {})
        self.assertIsNone(store.rows["900102"].claim)
        self.assertEqual(feedback.load()["decisions"], {})
        self.assertEqual(store.find("R05-900102")[1].status, "Open")
        other = client.get(f"/changes?since={version}", headers={"HX-Request": "true"}).text
        self.assertNotIn("Simulation reset", other)            # the one who reset is not told
        self.assertIn("__reset__", store.changes_since(version, "someone else"))


if __name__ == "__main__":
    unittest.main()
