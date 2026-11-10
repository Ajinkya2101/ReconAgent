"""The ML layer (recon_agent/anomaly.py): robust z-scores against each item's own history.

    python -m unittest tests.test_anomaly -v
"""
from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from recon_agent import anomaly, config, engine, ingest
from recon_agent.events import scenarios, seed
from recon_agent.events.log import Change, EventLog
from recon_agent.events.projector import Projector

NOW = dt.datetime(2026, 9, 11, 20, 0)


def seeded() -> EventLog:
    log = EventLog(Path(tempfile.mkdtemp(prefix="recon-ml-")) / "events.db")
    seed.seed(log)
    return log


def run(log: EventLog):
    p = Projector(log)
    p.catch_up()
    return engine.run(src=p.source_data(), use_ai=False)


def ml_flags(res) -> dict:
    return {f.id: f for f in res.findings if f.rule in ("R14", "R15", "R16", "R17")}


class RobustZTest(unittest.TestCase):
    def test_the_median_scores_zero_and_one_outlier_cannot_hide_the_next(self):
        history = [100, 104, 96, 102, 98, 101, 99, 103]
        self.assertAlmostEqual(anomaly.robust_z(100.5, history)[0], 0, places=0)
        self.assertGreater(anomaly.robust_z(1000, history)[0], 100)
        # A single earlier outlier barely moves the median and the MAD, so the next one still stands out.
        self.assertGreater(anomaly.robust_z(1000, history + [5000])[0], 100)

    def test_a_flat_history_does_not_divide_by_zero(self):
        z, med, scale = anomaly.robust_z(120, [100] * 8)
        self.assertEqual((med, scale), (100, 1.0))
        self.assertEqual(z, 20)

    def test_nothing_is_judged_without_enough_history(self):
        self.assertIsNone(anomaly._check(10_000, [100] * (config.ML_MIN_HISTORY - 1)))
        self.assertIsNotNone(anomaly._check(10_000, [100, 101, 99, 100, 102, 98][:config.ML_MIN_HISTORY]))


class HistoryTest(unittest.TestCase):
    def test_history_never_enters_reconciliation(self):
        res = run(seeded())
        self.assertEqual(ml_flags(res), {})                     # today sits inside its history
        h = res.src.history
        self.assertEqual((len(h.sales_orders), len(h.forecasts), len(h.work_orders), len(h.odi)),
                         (18 * seed.HISTORY_DAYS, 3 * seed.HISTORY_DAYS, 3 * seed.HISTORY_DAYS, 3 * seed.HISTORY_DAYS))
        p = res.positions["76108"]
        self.assertEqual((p.so_qty, p.forecast_qty, p.wo_qty, len(p.odi)), (14339, 41769, 48635, 12))
        self.assertEqual(res.kpis["sales_date"], "2026-09-12")

    def test_a_single_day_raises_no_ml_flags(self):
        p = Projector(seeded())
        p.catch_up()
        src = p.source_data()
        day = ingest.split_current(src)
        bare = ingest.SourceData(src.items, day.sales_orders, day.forecasts, day.odi, day.stock, day.work_orders,
                                 files={})
        bare.sales_orders[0].qty *= 10                        # an order ten times usual, but no history to say so
        self.assertEqual(ml_flags(engine.run(src=bare, use_ai=False)), {})

    def test_wms_history_is_one_snapshot_per_change_of_on_hand(self):
        log = seeded()
        before = len(run(log).src.stock_history["INV-0001"])
        self.assertEqual(before, seed.HISTORY_DAYS + 1)
        log.publish(Change("wms.inventory", "U", "INV-0001", {"Allocated quantity": 500}), "test", NOW)
        self.assertEqual(len(run(log).src.stock_history["INV-0001"]), before)   # on hand did not move
        log.publish(Change("wms.inventory", "U", "INV-0001", {"On hand": 7400}), "test", NOW)
        self.assertEqual(len(run(log).src.stock_history["INV-0001"]), before + 1)


class DetectorTest(unittest.TestCase):
    def test_ordinary_variation_stays_quiet(self):
        log = seeded()
        log.publish(Change("buss.sales_order", "U", "2263245-1", {"Order Quantity": 7800}), "test", NOW)   # +8%
        log.publish(Change("wms.inventory", "U", "INV-0001", {"On hand": 7373}), "test", NOW)              # -100
        self.assertEqual(ml_flags(run(log)), {})

    def test_each_ml_scenario_raises_one_high_flag_that_explains_itself(self):
        expect = {"S11": ("R14-2263245-1", "BUSS", "Over-production"),
                  "S12": ("R15-FC001", "Oracle", "Over-production"),
                  "S13": ("R16-WO-UBD-20260912-001", "ODI", "Short shipment"),
                  "S14": ("R17-INV-0001", "WMS", "Data integrity")}
        for sid, (fid, origin, impact) in expect.items():
            with self.subTest(scenario=sid):
                sc = scenarios.SCENARIOS[sid]
                self.assertEqual(sc.layer, "ML")
                log = seeded()
                scenarios.run(log, sc)
                res = run(log)
                flags = ml_flags(res)
                self.assertEqual(list(flags), [fid])
                f = flags[fid]
                self.assertEqual(f.severity, "High")
                self.assertGreaterEqual(abs(f.evidence["robust_z"]), 2 * config.ML_Z_THRESHOLD)
                self.assertEqual(f.evidence["method"], anomaly.METHOD)
                self.assertGreaterEqual(f.evidence["history_points"], config.ML_MIN_HISTORY)
                a = f.accountability
                self.assertEqual((a["origin"], a["impact"], a["failure_type"], a["basis"]),
                                 (origin, impact, "Statistical anomaly", "likely"))
                self.assertIn("robust z", f.recommendation)
                # The systems agree, so no rule fires: what the ML layer sees, the rules cannot.
                self.assertEqual({g.rule for g in res.findings if g.sku == "76108"}, {f.rule})

    def test_the_threshold_is_configurable(self):
        log = seeded()
        log.publish(Change("buss.sales_order", "U", "2263245-1", {"Order Quantity": 9000}), "test", NOW)   # +25%
        self.assertEqual(ml_flags(run(log)), {})
        with mock.patch.object(config, "ML_Z_THRESHOLD", 2.0):
            self.assertIn("R14-2263245-1", ml_flags(run(log)))


if __name__ == "__main__":
    unittest.main()
