"""Arithmetic consistency of the reconciled position under source edits.

An edit to one source row must move the position the way a planner would work it out by
hand: a MIPS quantity change moves demand (and so the net requirement) one for one and
never shows up as inventory; a wrapped-units change moves completed production; a return
booked in WMS reduces usable supply once. The card's bar must draw the same equation.

    python -m unittest tests.test_consistency -v
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
import unittest
from pathlib import Path
from types import SimpleNamespace

from recon_agent import engine
from recon_agent.events import scenarios, seed
from recon_agent.events.clock import SimClock
from recon_agent.events.log import Change, EventLog
from recon_agent.events.projector import Projector
from recon_agent.web.store import ledger

from recon_agent import config as _config
assert "recon-tests-" in str(_config.FEEDBACK_FILE) and not _config.AI_ENABLED, "tests are not isolated"

U = scenarios.U


def seeded_log() -> EventLog:
    log = EventLog(Path(tempfile.mkdtemp(prefix="recon-cons-")) / "events.db")
    seed.seed(log)
    return log


def edit(log: EventLog, *changes: Change) -> None:
    """Publish changes as the simulator does: one operator commit each, derived columns recomputed."""
    clock = SimClock(log, cache_seconds=0)
    for c in changes:
        clock.advance(dt.timedelta(minutes=1))
        log.publish(scenarios._resolve(log, c, clock.now(), seed.DEFAULT_SALES_DATE), "simulator:test", clock.now())


def result(log: EventLog, stream: str = "events"):
    p = Projector(log, stream=stream)
    p.catch_up()
    return engine.run(src=p.source_data(), use_ai=False)


def flags(res) -> dict[str, float]:
    return {f.id: f.units for f in res.findings}


def numbers(res) -> dict[str, tuple]:
    return {s: (p.demand_total, p.implied_inventory, p.wrapped, p.returns, p.net_requirement)
            for s, p in res.positions.items()}


def source_rows(log: EventLog, entity: str) -> dict[str, dict]:
    p = Projector(log, stream="source")
    p.catch_up()
    return dict(p.tables[entity])


class EveryRowTest(unittest.TestCase):
    """Edit every MIPS line and every ODI run of the starting day, one at a time."""

    @classmethod
    def setUpClass(cls):
        cls.log = seeded_log()
        cls.base = result(cls.log)

    def test_a_demand_edit_moves_only_demand(self):
        for key, row in source_rows(self.log, "mips.order_line").items():
            sku = row["FG Oracle Ref"].strip()
            qty = row["Order Quantity"]
            with self.subTest(line=key, sku=sku):
                before = self.base.positions[sku]
                edit(self.log, U("mips.order_line", key, Order_Quantity=qty + 500))
                try:
                    after = result(self.log).positions[sku]
                    self.assertEqual(after.demand_total, before.demand_total + 500)
                    self.assertEqual(after.implied_inventory, before.implied_inventory, "demand read as inventory")
                    self.assertEqual(after.net_requirement, before.net_requirement + 500)
                finally:
                    edit(self.log, U("mips.order_line", key, Order_Quantity=qty))
                self.assertEqual(numbers(result(self.log)), numbers(self.base), "the revert did not restore the day")

    def test_a_wrapped_edit_moves_only_completed_production(self):
        for key, row in source_rows(self.log, "odi.finished_good").items():
            if not row["ActualWrappedUnits"]:
                continue
            sku, wrapped = row["OracleFGNumber"], row["ActualWrappedUnits"]
            with self.subTest(run=key, sku=sku):
                before = self.base.positions[sku]
                edit(self.log, U("odi.finished_good", key, ActualWrappedUnits=wrapped - 100))
                try:
                    after = result(self.log).positions[sku]
                    self.assertEqual(after.implied_inventory, before.implied_inventory)
                    self.assertEqual(after.net_requirement, before.net_requirement + 100)
                finally:
                    edit(self.log, U("odi.finished_good", key, ActualWrappedUnits=wrapped))
                self.assertEqual(numbers(result(self.log)), numbers(self.base))


class DemandAfterOrderTest(unittest.TestCase):
    def test_raising_demand_to_what_was_made_clears_the_recovery_flag(self):
        """900102: run 1 ordered 6,000 and wrapped 5,200; the recovery run made 2,000. At 7,200
        demand the recovery was exactly the shortfall: no surplus, no over-build, no phantom inventory."""
        log = seeded_log()
        self.assertEqual(flags(result(log))["R05-900102"], 1200)
        edit(log, U("mips.order_line", "ML-0005", Order_Quantity=6200))
        res = result(log)
        p = res.positions["900102"]
        self.assertEqual((p.demand_total, p.implied_inventory, p.wrapped, p.net_requirement), (7200, 0, 7200, 0))
        self.assertEqual(p.demand_after_order, 1200)
        found = flags(res)
        self.assertNotIn("R05-900102", found)
        self.assertNotIn("R01-900102", found)
        self.assertEqual(found["R09-900102"], -1200)   # MIPS now plans 1,200 more than WMS has on order

    def test_a_demand_rise_is_a_shortfall_not_inventory(self):
        log = seeded_log()
        edit(log, U("mips.order_line", "ML-0016", Order_Quantity=3500))   # 900111: 4,000 -> 5,000
        res = result(log)
        p = res.positions["900111"]
        self.assertEqual((p.implied_inventory, p.net_requirement), (0, 1000))
        self.assertNotIn("R02-900111", flags(res))

    def test_netted_inventory_survives_a_later_demand_change(self):
        """900103: MIPS netted 800 (5,000 demand, 4,200 ordered). Demand +500 afterwards is 500 short."""
        log = seeded_log()
        edit(log, U("mips.order_line", "ML-0006", Order_Quantity=5500))
        res = result(log)
        p = res.positions["900103"]
        self.assertEqual((p.demand_at_order, p.implied_inventory, p.net_requirement), (5000, 800, 500))
        self.assertEqual(flags(res)["R02-900103"], 800)

    def test_reordering_the_first_run_sizes_it_against_the_new_demand(self):
        log = seeded_log()
        edit(log, U("mips.order_line", "ML-0005", Order_Quantity=6200),          # 900102 demand 7,200
             U("odi.finished_good", "FG-0002", NetUnitsOrdered=6600))            # MIPS re-sends run 1
        p = result(log).positions["900102"]
        self.assertEqual(p.demand_after_order, 0)
        self.assertEqual(p.implied_inventory, 600)                               # 7,200 - 6,600 netted
        edit(log, U("mips.order_line", "ML-0005", Order_Quantity=5000))          # demand back to 6,000 later
        p = result(log).positions["900102"]
        self.assertEqual((p.implied_inventory, p.net_requirement), (600, 6000 - 600 - 7200))

    def test_inserted_and_deleted_lines_are_demand_changes(self):
        log = seeded_log()
        base = result(log).positions["900111"]
        line = dict(source_rows(log, "mips.order_line")["ML-0015"])
        line.update({"Order Line ID": "ML-T1", "Order Quantity": 500})
        edit(log, Change("mips.order_line", "I", row=line))
        p = result(log).positions["900111"]
        self.assertEqual((p.implied_inventory, p.net_requirement), (0, base.net_requirement + 500))
        edit(log, Change("mips.order_line", "D", "ML-T1"), Change("mips.order_line", "D", "ML-0015"))
        p = result(log).positions["900111"]
        self.assertEqual((p.implied_inventory, p.net_requirement), (0, base.net_requirement - 1500))

    def test_delivery_order_does_not_change_the_answer(self):
        """The ODI re-order arrives before the MIPS change it followed: the worklist still converges."""
        log = seeded_log()
        log.add_fault("delay", entity="mips.order_line", seconds=60)
        edit(log, U("mips.order_line", "ML-0005", Order_Quantity=6200),
             U("odi.finished_good", "FG-0002", NetUnitsOrdered=6600),
             U("mips.order_line", "ML-0004", Order_Quantity=1500))
        log.remove_fault()
        log.flush()
        self.assertEqual(numbers(result(log)), numbers(result(log, stream="source")))
        p = result(log).positions["900102"]
        self.assertEqual((p.demand_after_order, p.implied_inventory), (500, 600))


class RecoveryRunTest(unittest.TestCase):
    def test_a_recovery_sized_to_the_shortfall_is_not_flagged(self):
        log = seeded_log()
        edit(log, U("mips.order_line", "ML-0005", Order_Quantity=6200),
             U("odi.finished_good", "FG-0002", NetUnitsOrdered=7200))            # run 1 ordered the full 7,200
        self.assertNotIn("R05-900102", flags(result(log)))

    def test_an_oversized_recovery_is_flagged_before_it_wraps(self):
        log = seeded_log()
        edit(log, U("odi.finished_good", "FG-0003", ActualWrappedUnits=0))       # recovery ordered 2,000, not run
        f = next(f for f in result(log).findings if f.id == "R05-900102")
        self.assertEqual(f.units, 1200)                                          # 800 needed, 2,000 ordered
        self.assertIn("Cut the open recovery order", f.recommendation)


class ReturnsTest(unittest.TestCase):
    def test_a_return_counts_once_wherever_wms_books_it(self):
        log = seeded_log()
        scenarios.run(log, scenarios.SCENARIOS["S5"])                            # transaction only (R19)
        self.assertEqual(result(log).positions["900111"].returns, 120)
        edit(log, U("wms.ticket", "TK-0010", Returned_Qty=120))                  # the R19 correction
        res = result(log)
        self.assertEqual(res.positions["900111"].returns, 120)
        self.assertNotIn("R19-TK-0010", flags(res))

    def test_a_return_booked_only_on_the_ticket_reduces_supply(self):
        log = seeded_log()
        edit(log, U("wms.ticket", "TK-0010", Returned_Qty=120))
        p = result(log).positions["900111"]
        self.assertEqual((p.returns, p.usable_supply, p.net_requirement), (120, 3880, 120))

    def test_the_card_draws_the_equation(self):
        log = seeded_log()
        scenarios.run(log, scenarios.SCENARIOS["S5"])
        for sku, p in result(log).positions.items():
            with self.subTest(sku=sku):
                L = ledger(SimpleNamespace(position=p, sales_date=seed.DEFAULT_SALES_DATE, carry_accepted=False))
                if L:
                    self.assertEqual(L["short"] - L["surplus"], p.net_requirement)
        f = next(f for f in result(log).findings if f.id == "R07-900111")
        self.assertIn("covers 3,880 of 4,000", f.recommendation)


class CardTest(unittest.TestCase):
    def test_the_card_explains_demand_changes_and_returns(self):
        from unittest import mock
        from fastapi.testclient import TestClient
        from recon_agent import security, sources, users
        from recon_agent.web.app import create_app as worklist

        log = seeded_log()
        edit(log, U("mips.order_line", "ML-0006", Order_Quantity=5500))          # 900103 demand +500
        scenarios.run(log, scenarios.SCENARIOS["S5"])                            # 900111 returns 120
        with mock.patch.object(security, "PBKDF2_ITERATIONS", 1000):   # an account exists: no demo seeding
            if not users.get("cora"):
                users.add("cora", "Cora", "supervisor", "test-password-1")
        with mock.patch.object(sources, "open_feed", lambda *_: sources.EventFeed(log.path)):
            app = worklist()
        client = TestClient(app)
        client.post("/signin", data={"username": "cora", "password": "test-password-1"}, follow_redirects=False)
        card = client.get("/row/900103", headers={"HX-Request": "true"}).text
        self.assertIn("+500 since production was ordered", card)
        self.assertIn("from the 5,000 demand it was ordered against", card)
        self.assertIn("Still to produce", card)
        card = client.get("/row/900111", headers={"HX-Request": "true"}).text
        self.assertIn("Wrapped 4,000 · 120 returned", card)
        self.assertIn("Short 120", card)


if __name__ == "__main__":
    unittest.main()
