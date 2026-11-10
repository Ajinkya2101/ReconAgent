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

NOW = dt.datetime(2026, 9, 11, 20, 0)
FLAG = "R02-2263245-1"   # S1: BUSS has Albany at 7,800, ODI still maps 7,200 on ODI-0002
WO1 = seed.wo_number(seed.DEFAULT_SALES_DATE, 1)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WriteBackTest(unittest.TestCase):
    def setUp(self):
        self.log = EventLog(Path(tempfile.mkdtemp(prefix="recon-wb-")) / "events.db")
        seed.seed(self.log)
        scenarios.run(self.log, scenarios.SCENARIOS["S1"])   # R02, R04 and R05 on 76108
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

    def approve(self, flag_id: str) -> str:
        _, message = self.store.approve(flag_id, "Pat Planner")
        self.assertIn("accepted", message)
        self.assertEqual(self.store.book.for_flag(flag_id)["status"], "Confirmed")
        return message

    def test_one_source_error_is_one_discrepancy(self):
        """S1 raises R02, R04 and R05 for the same 600 units: one discrepancy, led by where it entered."""
        row = self.store.rows["76108"]
        (d,) = row.discrepancies
        self.assertEqual([f.id for f in d.flags], [FLAG, "R04-76108", "R05-76108"])
        self.assertEqual((d.units, d.dollars), (600, max(f.dollars for f in d.flags)))

    def test_separate_errors_stay_separate(self):
        """The seed's 76105 has three unrelated flags, two of them for 100 units: three discrepancies."""
        log = EventLog(Path(tempfile.mkdtemp(prefix="recon-wb-")) / "events.db")
        seed.seed(log)
        rows = Store(sources.EventFeed(log.path)).rows
        self.assertEqual(sorted(len(d.flags) for d in rows["76105"].discrepancies), [1, 1, 1])
        for row in rows.values():   # every seeded flag is its own discrepancy
            self.assertEqual(len(row.discrepancies), len(row.flags))

    def test_one_decision_covers_the_discrepancy(self):
        row, d = self.store.decide_discrepancy("R05-76108", "reject", "Pat Planner", "checked with BUSS")
        self.assertEqual({f.status for f in d.flags}, {"Rejected"})
        self.assertFalse(row.is_open)
        row, d = self.store.decide_discrepancy(FLAG, "accept", "Pat Planner")
        self.assertEqual({f.status for f in d.flags}, {"Accepted - awaiting correction"})

    def test_approving_a_discrepancy_sends_every_fix(self):
        """ODI first, then the MIPS demand and work order: all three flags clear through the feed."""
        _, said = self.store.approve_discrepancy(FLAG, "Pat Planner")
        self.assertTrue(said)
        self.assertFalse({FLAG, "R04-76108", "R05-76108"} & self.flags())
        statuses = {c["flag_id"]: c["status"] for c in self.store.book.items.values()}
        self.assertEqual(statuses.get(FLAG), "Confirmed")

    def test_approved_correction_is_confirmed_by_the_feed(self):
        _, f = self.store.find(FLAG)
        p = self.store.proposal(f)
        self.assertEqual((p.entity, p.key, p.changes), ("odi.batch_mapping", "ODI-0002", {"Quantity": 7800}))
        self.approve(FLAG)
        self.assertNotIn(FLAG, self.flags())
        c = self.store.book.for_flag(FLAG)
        self.assertIsNotNone(c["confirmed_seq"])
        self.assertEqual(self.store.decision(f)["decision"], "accept")   # approving accepts the flag
        self.assertEqual(self.log.current("odi.batch_mapping", "ODI-0002")["Quantity"], 7800)
        # ODI is right now; MIPS still plans the old demand and work order, so those flags stay.
        self.assertTrue({"R04-76108", "R05-76108"} <= self.flags())

    def test_the_whole_chain_is_corrected_system_by_system(self):
        """ODI, then the MIPS demand, then the work order; the new work order then reaches ODI."""
        self.approve(FLAG)
        self.approve("R04-76108")
        self.assertEqual(self.log.current("mips.work_order", WO1)["Forecast / Order Quantity"], 56235)
        self.approve("R05-76108")
        self.assertEqual(self.log.current("mips.work_order", WO1)["Work Order Quantity"], 49235)
        self.assertIn(f"R07-{WO1}", self.flags())   # every ODI row of the work order still carries 48,635
        _, f = self.store.find(f"R07-{WO1}")
        p = self.store.proposal(f)
        self.assertEqual((p.key, len(p.rows)), ("ODI-0001", 10))   # 11 rows on the work order
        head = self.log.source_position()[1]
        self.approve(f"R07-{WO1}")
        self.assertEqual(self.log.source_position()[1] - head, 11)  # one change per row, one transaction
        self.assertFalse(any("76108" in x or WO1 in x for x in self.flags()))
        self.assertTrue(all(self.log.current("odi.batch_mapping", k)["Work Order Quantity"] == 49235
                            for k in scenarios.ODI_ON_WO))

    def test_inserts_deletes_and_links(self):
        cases = {"S2": ("R01-2263260-1", "I", "odi.batch_mapping"), "S3": ("R03-ODI-0012", "D", "odi.batch_mapping"),
                 "S8": ("R06-76108", "I", "mips.work_order"), "S9": ("R08-ODI-0002", "U", "odi.batch_mapping")}
        for sid, (flag, op, entity) in cases.items():
            with self.subTest(sid):
                self.log.reset()
                seed.seed(self.log)
                scenarios.run(self.log, scenarios.SCENARIOS[sid])
                self.assertIn(flag, self.flags())
                _, f = self.store.find(flag)
                p = self.store.proposal(f)
                self.assertEqual((p.op, p.entity), (op, entity))
                self.approve(flag)
                self.assertNotIn(flag, self.flags())

    def test_restoring_a_deleted_work_order_clears_the_odi_link(self):
        self.log.reset()
        seed.seed(self.log)
        scenarios.run(self.log, scenarios.SCENARIOS["S8"])
        self.assertTrue({"R06-76108", f"R07-{WO1}"} <= self.flags())
        self.approve("R06-76108")   # re-created under the number ODI still maps
        self.assertEqual(self.log.current("mips.work_order", WO1)["Work Order Quantity"], 48635)
        self.assertFalse(any("76108" in x or WO1 in x for x in self.flags()))

    def test_a_reset_retires_earlier_corrections(self):
        self.store.approve(FLAG, "Pat Planner")
        self.log.reset()
        seed.seed(self.log)
        scenarios.run(self.log, scenarios.SCENARIOS["S1"])
        self.assertIn(FLAG, self.flags())
        _, f = self.store.find(FLAG)
        self.assertIsNone(self.store.correction(f))           # the old COR-0001 belongs to the old log
        self.assertIsNotNone(self.store.proposal(f))          # so the flag offers a fresh correction
        self.assertEqual(len(self.store.book.items), 1)       # still on file for the record

    def test_endpoint_down_then_retry(self):
        self.operator.post("/integration/oic", data={"mode": "down"})
        _, message = self.store.approve(FLAG, "Pat Planner")
        self.assertIn("unavailable", message)
        c = self.store.book.for_flag(FLAG)
        self.assertEqual(c["status"], "Failed")
        self.assertIn(FLAG, self.flags())
        self.operator.post("/integration/oic", data={"mode": "accept"})
        self.store.retry(c["id"], "Pat Planner")
        self.assertNotIn(FLAG, self.flags())
        self.assertEqual(self.store.book.items[c["id"]]["status"], "Confirmed")

    def test_stale_row_is_refused(self):
        # ODI changes the row after the worklist computed its proposal but before approval reaches OIC.
        from recon_agent import corrections
        real_send = corrections.send

        def race(c, by):
            self.log.publish(Change("odi.batch_mapping", "U", "ODI-0002", {"Quantity": 7500}), "cli", NOW)
            return real_send(c, by)

        with mock.patch("recon_agent.corrections.send", race):
            _, message = self.store.approve(FLAG, "Pat Planner")
        self.assertIn("changed since", message)
        self.assertEqual(self.store.book.for_flag(FLAG)["status"], "Failed")
        self.assertEqual(self.log.current("odi.batch_mapping", "ODI-0002")["Quantity"], 7500)

    def test_slow_integration_keeps_it_sent_until_the_event_arrives(self):
        self.log.add_fault("delay", source="ODI", seconds=0.6)
        self.store.approve(FLAG, "Pat Planner")
        c = self.store.book.for_flag(FLAG)
        self.assertEqual(c["status"], "Sent")                 # OIC said yes; the feed has not shown it
        self.assertIn(FLAG, self.flags())
        deadline = time.monotonic() + 5                        # the simulator's pump delivers it
        while FLAG in self.flags() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertNotIn(FLAG, self.flags())
        self.assertEqual(self.store.book.items[c["id"]]["status"], "Confirmed")

    def test_only_planners_and_managers_approve(self):
        from recon_agent.web.app import create_app as worklist
        with mock.patch.object(sources, "open_feed", lambda *_: sources.EventFeed(self.log.path)):
            app = worklist()
        sup = TestClient(app)
        sign_in(sup, "sam", "supervisor")
        self.assertEqual(sup.post(f"/flags/{FLAG}/correction").status_code, 403)
        detail = sup.get("/row/76108", headers={"HX-Request": "true"}).text
        self.assertIn('<span class="code">ODI-0002</span> Quantity', detail)   # the proposed fix is shown
        self.assertIn("A planner or manager approves", detail)
        self.assertNotIn("Approve and send", detail)
        planner = TestClient(app)
        sign_in(planner, "pat", "planner")
        r = planner.post("/api/corrections", json={"flag_id": FLAG})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["correction"]["status"], "Confirmed")
        self.assertEqual(planner.get(f"/api/corrections/{r.json()['correction']['id']}").json()["key"], "ODI-0002")


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
        # Change the day: a WMS count breaks 76108; claim and decide on another row.
        log.publish(Change("wms.inventory", "U", "INV-0001", {"On hand": 7373}), "test", NOW)
        store.refresh_if_sources_changed()
        self.assertIn("76108", store.rows)
        self.assertEqual(client.post("/row/950513/claim", headers={"HX-Request": "true"}).status_code, 200)
        client.post("/flags/R05-950513/ack", headers={"HX-Request": "true"})
        client.post("/flags/R05-950513/decision", data={"decision": "reject"}, headers={"HX-Request": "true"})
        self.assertIn("R05-950513", feedback.load()["decisions"])
        version = store.version

        page = client.get("/").text
        self.assertIn("Reset simulation", page)
        r = client.post("/simulation/reset", headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.headers["HX-Redirect"], "/")

        self.assertNotIn("76108", store.rows)                  # back to the seeded flags
        self.assertEqual(log.source_position()[1], seed.seed(EventLog(Path(tempfile.mkdtemp()) / "e.db")))
        self.assertEqual(store.claims, {})
        self.assertEqual(store.acks, {})
        self.assertIsNone(store.rows["950513"].claim)
        self.assertEqual(feedback.load()["decisions"], {})
        self.assertEqual(store.find("R05-950513")[1].status, "Open")
        other = client.get(f"/changes?since={version}", headers={"HX-Request": "true"}).text
        self.assertNotIn("Simulation reset", other)            # the one who reset is not told
        self.assertIn("__reset__", store.changes_since(version, "someone else"))


if __name__ == "__main__":
    unittest.main()
