"""The 9 Nov data model: the demand-to-delivery equation and the edges of its rules.

    Work Order Qty = (Sales Orders + Forecast) - Allocated - Available     (docs/data-model/DATA_MODEL.md)

Every edit to one source moves only its own term; stock that covers the demand needs no work
order; an item with only a forecast is planning, not a mismatch; ODI rows served from stock
belong to the SKU of the order they map.

    python -m unittest tests.test_data_model -v
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

from recon_agent import engine, sources
from recon_agent.events import seed
from recon_agent.events.log import Change, EventLog
from recon_agent.events.projector import Projector
from recon_agent.web.store import Store, ledger

from recon_agent import config as _config
assert "recon-tests-" in str(_config.FEEDBACK_FILE) and not _config.AI_ENABLED, "tests are not isolated"

NOW = dt.datetime(2026, 9, 11, 20, 0)
WO1 = seed.wo_number(seed.DEFAULT_SALES_DATE, 1)
TERMS = ("so_qty", "forecast_qty", "allocated", "available", "mips_demand", "wo_qty", "odi_demand")


def seeded_log() -> EventLog:
    log = EventLog(Path(tempfile.mkdtemp(prefix="recon-model-")) / "events.db")
    seed.seed(log)
    return log


def result(log: EventLog):
    p = Projector(log)
    p.catch_up()
    return engine.run(src=p.source_data(), use_ai=False)


class EquationTest(unittest.TestCase):
    def setUp(self):
        self.log = seeded_log()
        self.before = result(self.log).positions["76108"]

    def publish(self, entity: str, key: str, **cols) -> None:
        self.log.publish(Change(entity, "U", key, {k.replace("_", " "): v for k, v in cols.items()}), "test", NOW)

    def moved(self) -> dict[str, float]:
        after = result(self.log).positions["76108"]
        return {t: getattr(after, t) - getattr(self.before, t) for t in TERMS if getattr(after, t) != getattr(self.before, t)}

    def test_each_source_moves_only_its_own_term(self):
        cases = [("buss.sales_order", "2263245-1", {"Order_Quantity": 7300}, {"so_qty": 100}),
                 ("oracle.forecast", "FC001", {"Forecast_/_Required_Quantity": 41669}, {"forecast_qty": -100}),
                 ("wms.inventory", "INV-0001", {"Available_quantity": 6900}, {"available": -100}),
                 ("mips.work_order", WO1, {"Work_Order_Quantity": 48735}, {"wo_qty": 100}),
                 ("odi.batch_mapping", "ODI-0002", {"Quantity": 7100}, {"odi_demand": -100})]
        for entity, key, cols, want in cases:
            with self.subTest(entity):
                self.log.reset()
                seed.seed(self.log)
                self.publish(entity, key, **{k.replace("_/_", " / "): v for k, v in cols.items()})
                self.assertEqual(self.moved(), want)

    def test_stock_that_covers_the_demand_needs_no_work_order(self):
        self.publish("wms.inventory", "INV-0001", On_hand=60473, Available_quantity=60000)
        res = result(self.log)
        p = res.positions["76108"]
        self.assertEqual((p.shortfall, p.stock_surplus), (0, 60000 - 55635))
        f = next(f for f in res.findings if f.id == "R05-76108")
        self.assertEqual(f.units, -48635)                      # the whole work order is over-production
        self.assertIn("no production is needed", f.recommendation)
        self.assertEqual(f.accountability["basis"], "certain")
        self.assertNotIn("R06-76108", {x.id for x in res.findings})

    def test_mips_that_ignores_allocated_stock_is_named(self):
        self.publish("mips.work_order", WO1, **{"Forecast / Order Quantity": 56108})
        f = next(f for f in result(self.log).findings if f.id == "R04-76108")
        self.assertEqual(f.units, -473)
        self.assertIn("did not remove the 473 units WMS has already allocated", f.recommendation)
        self.assertEqual((f.accountability["origin"], f.accountability["basis"]), ("MIPS", "certain"))

    def test_an_odi_row_served_from_stock_belongs_to_its_order(self):
        p = result(self.log).positions["76108"]
        bay_shore = next(e for e in p.odi if e.ref == "91220263121003")
        self.assertEqual((bay_shore.item, bay_shore.wo, bay_shore.allocated), ("", "", 473))   # as in the workbook
        self.assertEqual(bay_shore.sku, "76108")
        self.assertEqual(p.odi_allocated, p.allocated)

    def test_an_item_with_only_a_forecast_is_planning_until_it_has_orders(self):
        res = result(self.log)
        self.assertNotIn("950514", res.positions)              # FC005 alone
        self.log.publish(Change("buss.sales_order", "I", row={
            "SO Line ID": "2263300-1", "Order Number": "2263300", "Order Line": 1, "Item Number": "950514",
            "Order Quantity": 300, "Sales Date": "2026-09-12"}), "test", NOW)
        res = result(self.log)
        self.assertEqual(res.positions["950514"].demand, 300 + 6544)
        self.assertEqual({f.id for f in res.findings if f.sku == "950514"},
                         {"R01-2263300-1", "R01-FC005", "R06-950514"})


class ProposalTest(unittest.TestCase):
    def test_several_work_orders_need_a_person(self):
        log = seeded_log()
        log.publish(Change("mips.work_order", "I", row={
            "Work Order Number": "WO-UBD-20260912-009", "FG Oracle Ref": "76108", "Work Order Quantity": 500,
            "Forecast / Order Quantity": 0}), "test", NOW)
        store = Store(sources.EventFeed(log.path))
        _, f = store.find("R05-76108")
        self.assertEqual(f.units, -500)
        self.assertIsNone(store.proposal(f))                   # which of the two work orders to cut?


class LedgerTest(unittest.TestCase):
    def test_the_card_draws_the_equation(self):
        store = Store(sources.EventFeed(seeded_log().path))
        L = ledger(store.rows["950513"])
        self.assertEqual((L["demand"], L["allocated"], L["available"], L["shortfall"], L["wo"]),
                         (5200, 360, 1140, 3700, 4840))
        self.assertEqual((L["over"], L["short"], L["mips_ok"], L["wo_ok"]), (1140, 0, True, False))
        # On one scale: stock and the work order up to the demand mark, the excess past it.
        self.assertAlmostEqual(L["pct"]["alloc"] + L["pct"]["avail"] + L["pct"]["wo"], L["pct"]["demand"], places=1)
        self.assertGreater(L["pct"]["over"], 0)
        L = ledger(store.rows["76105"])
        self.assertEqual((L["balance"], L["over"], L["short"], L["wo_ok"]), (0, 0, 0, True))
        self.assertEqual([s["system"] for s in L["systems"]], ["BUSS", "Oracle", "ODI", "WMS", "WMS", "MIPS", "MIPS"])


if __name__ == "__main__":
    unittest.main()
