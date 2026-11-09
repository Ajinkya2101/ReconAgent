"""Behaviour required by the client brief (docs/reference/WMS_AI_Agent_Requirement.docx) that the
client extracts can show: valid demand only, moved runs in all three senses, WMS order changes,
corrections that state the resulting requirement, and the challenge-area tags.

    python -m unittest tests.test_client_alignment -v
"""
from __future__ import annotations

import os
import tempfile

os.environ["RECON_AI"] = "off"
os.environ.setdefault("RECON_TEST_DIR", tempfile.mkdtemp(prefix="recon-tests-"))
os.environ["RECON_FEEDBACK_FILE"] = os.path.join(os.environ["RECON_TEST_DIR"], "feedback.json")
os.environ["RECON_USERS_FILE"] = os.path.join(os.environ["RECON_TEST_DIR"], "users.json")

import datetime as dt
import unittest
from pathlib import Path

from recon_agent import accountability, detectors, ingest, position
from recon_agent.events import seed
from recon_agent.events.log import Change, EventLog
from recon_agent.events.projector import Projector
from recon_agent.ingest import FgRun, MipsLine, SourceData, WmsCustomerOrder, WmsItem

DAY = dt.date(2026, 9, 12)


def line(sku: str, qty: float, *, closed: bool = False, buss: bool = True, moved: bool = False) -> MipsLine:
    return MipsLine(sku=sku, raw_sku=sku, buss_code="", description=f"ITEM {sku}", schedule_date=DAY,
                    offset_sales_date=DAY, production_date=DAY, order_moved=moved, order_qty=qty,
                    container_code="01", units_per_container=12, retail_price=4.0,
                    source_system="BUSS" if buss else "OCloud Region2", last_modified=None, closed=closed)


def run(sku: str, formula: str, line_code: str, shift: str | None, start: dt.datetime,
        ordered: float, wrapped: float, mips_order_qty: float = 0) -> FgRun:
    return FgRun(sku=sku, buss_code="", description=f"ITEM {sku}", sales_date=DAY, start=start, end=None,
                 line=line_code, shift=shift, formula_type=formula, item_structure="Primary",
                 net_units_ordered=ordered, wrapped_units=wrapped, expected_ending_inventory=wrapped - ordered,
                 mips_beginning_inventory=0, mips_inventory_adjustment=0, mips_order_qty=mips_order_qty,
                 mips_order_adjustment=0, damaged_units=0, pieces_per_pkg=1)


def wms(sku: str, *orders: tuple[str, float, float, float]) -> WmsItem:
    """orders: (customer, original qty, order qty, allocated)."""
    return WmsItem(sku=sku, description=f"ITEM {sku}", units_per_container=12,
                   orders=[WmsCustomerOrder(c, o, q, a, 0, 0, "GREEN") for c, o, q, a in orders])


def source(mips=(), runs=(), items=()) -> SourceData:
    return SourceData(mips=list(mips), fg_runs=list(runs), batches=[], wms_items={i.sku: i for i in items},
                      fill_orders=[], fill_report_total=0.0, files={})


def flags(src: SourceData) -> dict[str, detectors.Finding]:
    positions = position.build_positions(src)
    found = detectors.run_all(src, positions)
    accountability.assign(found, positions)
    return {f.id: f for f in found}


class ValidDemandTest(unittest.TestCase):
    """REQ step 2: 'Total valid demand'. A closed MIPS line is not demand."""

    def test_closed_flag_values(self):
        for v in ("T", "t", "TRUE", "Y", "1", True):
            self.assertTrue(ingest.is_closed(v), v)
        for v in ("F", "", None, "N", False):
            self.assertFalse(ingest.is_closed(v), v)

    def test_closed_line_is_kept_aside_not_counted(self):
        src = source([line("100", 2000), line("100", 1500, closed=True, buss=False)],
                     [run("100", "SU1", "Line 1", "Shift 1", dt.datetime(2026, 9, 11, 8), 2000, 2000, 2000)])
        p = position.build_positions(src)["100"]
        self.assertEqual(p.demand_total, 2000)
        self.assertEqual(len(p.closed_lines), 1)
        self.assertEqual(p.to_row()["Closed Lines (excluded)"], 1)
        self.assertEqual(p.net_requirement, 0)
        self.assertNotIn("R01-100", flags(src))   # one valid line: nothing to consolidate

    def test_closing_a_line_after_the_run_was_ordered_is_demand_removed_not_inventory(self):
        log = EventLog(Path(tempfile.mkdtemp(prefix="recon-align-")) / "events.db")
        seed.seed(log)
        before = Projector(log)
        before.catch_up()
        key, row = next((k, r) for k, r in before.tables["mips.order_line"].items()
                        if ingest.norm_sku(r["FG Oracle Ref"]) == "900107" and r["Source System"] != "BUSS")
        qty = row["Order Quantity"]
        log.publish(Change("mips.order_line", "U", key, {"Closed": "T"}), "test", dt.datetime(2026, 11, 6, 9))
        after = Projector(log)
        after.catch_up()
        src = after.source_data()
        p = position.build_positions(src)["900107"]
        self.assertEqual(p.demand_after_order, -qty)
        self.assertEqual(p.implied_inventory, 0)   # the removed line never reads as inventory MIPS netted


class MovedRunTest(unittest.TestCase):
    """REQ scenario 3: a run moved from one line, production day or run sequence to another."""

    GHOST = run("200", "SU7", "Line 1", None, dt.datetime(2026, 9, 9, 8), 0, 0)

    def r04(self, real: FgRun) -> detectors.Finding:
        found = flags(source([line("200", 1000)], [self.GHOST, real]))
        self.assertNotIn("R03-SU7-200", found)
        return found["R04-SU7-200"]

    def test_moved_to_another_line(self):
        f = self.r04(run("200", "SU7A", "Line 2", "Shift 1", dt.datetime(2026, 9, 9, 8), 1000, 1000))
        self.assertEqual(f.evidence["moved_to_another"], "line")
        self.assertIn("moved from Line 1 to Line 2", f.recommendation)
        self.assertIn("another line", f.accountability["failure_point"])

    def test_moved_to_another_production_day(self):
        f = self.r04(run("200", "SU7A", "Line 1", "Shift 1", dt.datetime(2026, 9, 10, 8), 1000, 1000))
        self.assertEqual(f.evidence["moved_to_another"], "day")
        self.assertIn("from production day 09/09 to 09/10", f.recommendation)
        self.assertIn("another production day", f.accountability["failure_point"])

    def test_moved_to_another_run_sequence(self):
        f = self.r04(run("200", "SU7A", "Line 1", "Shift 2", dt.datetime(2026, 9, 9, 14), 1000, 1000))
        self.assertEqual(f.evidence["moved_to_another"], "sequence")
        self.assertIn("from the 08:00 slot to 14:00 on Line 1", f.recommendation)

    def test_a_line_move_is_named_before_a_day_move(self):
        found = flags(source([line("200", 1000)], [
            self.GHOST, run("200", "SU7A", "Line 1", "Shift 1", dt.datetime(2026, 9, 10, 8), 500, 500),
            run("200", "SU7", "Line 3", "Shift 1", dt.datetime(2026, 9, 9, 8), 500, 500)]))
        self.assertEqual(found["R04-SU7-200"].evidence["moved_to_another"], "line")

    def test_a_zero_run_with_a_shift_stays_a_blind_line(self):
        blind = run("200", "SU7", "Line 1", "Shift 1", dt.datetime(2026, 9, 9, 8), 0, 0)
        found = flags(source([line("200", 1000)],
                             [blind, run("200", "SU7A", "Line 1", "Shift 1", dt.datetime(2026, 9, 10, 8), 1000, 1000)]))
        self.assertIn("R03-SU7-200", found)
        self.assertNotIn("R04-SU7-200", found)


class OrderChangeTest(unittest.TestCase):
    """REQ scenario 6: the order changed and the change did not reach the other system."""

    RUN = run("300", "SU9", "Line 1", "Shift 1", dt.datetime(2026, 9, 11, 8), 3500, 3500, 3500)

    def test_mips_still_on_the_original_order_is_proven_stale(self):
        src = source([line("300", 3500)], [self.RUN],
                     [wms("300", ("DC NORTH", 2700, 2700, 2700), ("ROUTE 27", 800, 1000, 800))])
        f = flags(src)["R09-300"]
        self.assertEqual(f.units, 200)
        self.assertEqual(f.evidence["wms_original_order_qty"], 3500)
        self.assertEqual(f.evidence["wms_orders_changed"], "ROUTE 27 800 → 1,000")
        self.assertIn("MIPS still plans the original 3,500", f.recommendation)
        a = f.accountability
        self.assertEqual((a["origin"], a["failure_type"], a["basis"], a["handoff"]),
                         ("MIPS", "Stale data", "certain", "WMS → MIPS"))

    def test_without_an_order_change_the_attribution_stays_likely(self):
        src = source([line("300", 3500)], [self.RUN], [wms("300", ("DC NORTH", 3700, 3700, 3500))])
        f = flags(src)["R09-300"]
        self.assertNotIn("wms_original_order_qty", f.evidence)
        self.assertEqual(f.accountability["basis"], "likely")

    def test_an_order_change_mips_already_follows_is_not_a_flag(self):
        src = source([line("300", 3700)], [run("300", "SU9", "Line 1", "Shift 1", dt.datetime(2026, 9, 11, 8),
                                               3700, 3700, 3700)],
                     [wms("300", ("DC NORTH", 2700, 2700, 2700), ("ROUTE 27", 800, 1000, 1000))])
        self.assertNotIn("R09-300", flags(src))


class CorrectionWordingTest(unittest.TestCase):
    """REQ step 5 example: 'Apply the 1,200 units to the active sales-order lines and reduce the
    production requirement to 800 units.'"""

    def test_r02_states_the_resulting_requirement(self):
        src = source([line("400", 2000)], [run("400", "SU4", "Line 1", "Shift 1", dt.datetime(2026, 9, 11, 8),
                                               800, 800, 2000)])
        f = flags(src)["R02-400"]
        self.assertEqual(f.units, 1200)
        self.assertEqual(f.evidence["requirement_after_inventory"], 800)
        self.assertIn("Apply the 1,200 units to the active sales-order lines", f.recommendation)
        self.assertIn("production requirement at 800 units (2,000 demand - 1,200 inventory)", f.recommendation)


class ChallengeAreaTest(unittest.TestCase):
    """The client's challenge areas (docs/reference/Bimbo WMS Inventory Reconciliation PPT.pptx)."""

    def test_returns_have_their_own_area(self):
        self.assertEqual(detectors.RULES["R19"][2], "Returns & Adjustments")


if __name__ == "__main__":
    unittest.main()
