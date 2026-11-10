"""The worklist tells the truth about progress and hand-off.

- No empty view or filter ever says "Every SKU reconciles" while flags are open.
- Decisions move the money: the SKU shows what is still at risk and what was accepted; the shelf
  and the Decided tab (which counts flags) follow; a jump in rank is explained.
- Every Accept on a SKU steps back while a High flag on it waits for acknowledgment.
- Handed over collects notes, other people's claims and acknowledged-but-undecided flags, and the
  briefing says what changed since the reader's previous sign-in.
- Counts read "1 unit", never "1 units".

    python -m unittest tests.test_worklist_ui -v
"""
from __future__ import annotations

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from tests.test_live import seeded_log, sign_in   # isolates the feedback and users files first

from recon_agent import feedback, sources
from recon_agent.detectors import _units

HX = {"HX-Request": "true"}


class WorklistTruthTest(unittest.TestCase):
    def setUp(self):
        from recon_agent.web.app import create_app as worklist
        feedback.save({"rules": {}, "decisions": {}})
        self.log = seeded_log()
        with mock.patch.object(sources, "open_feed", lambda *_: sources.EventFeed(self.log.path)):
            self.app = worklist()
        self.store = self.app.state.store
        self.client = TestClient(self.app)
        sign_in(self.client, "lena", "planner")

    def top_flag(self, key: str):
        return self.store.rows[key].open_flags[0]

    def test_an_empty_view_is_never_an_all_clear(self):
        for path in ("/?view=mine", "/?view=decided", "/?view=handed"):
            page = self.client.get(path).text
            self.assertNotIn("Every SKU reconciles", page, path)
            self.assertIn("Pick a SKU from the queue", page, path)

    def test_the_top_row_is_not_current_until_its_detail_is_open(self):
        page = self.client.get("/").text
        self.assertIn('class="row is-auto"', page)
        self.assertNotIn("is-current", page)
        key = self.store.ordered_rows()[0].key
        self.assertIn(f'id="row-{key}" class="row is-current"', self.client.get(f"/row/{key}").text)

    def test_a_decision_moves_the_money_and_the_counts(self):
        row = self.store.ordered_rows()[0]
        f = row.open_flags[0]
        before = self.store.day_summary(row.sales_date)
        r = self.client.post(f"/flags/{f.id}/decision", data={"decision": "accept"}, headers=HX).text
        after = self.store.day_summary(row.sales_date)
        self.assertLess(after["dollars"], before["dollars"])
        self.assertEqual((after["decided"], after["accepted"]), (1, f.dollars))
        self.assertIn(f"${f.dollars:,.0f}</b> accepted", r)                       # the SKU and the shelf
        self.assertIn(f'id="day-stats-{row.sales_date.isoformat()}" hx-swap-oob', r)
        self.assertIn('<b class="view-count" id="count-decided" hx-swap-oob="outerHTML">1</b>', r)
        self.assertRegex(r, r"moves from rank \d+ to rank \d+|stays at rank|leaves the open queue")
        self.assertIn(row.key, self.client.get("/?view=decided").text)   # partly decided rows are listed

    def test_every_accept_steps_back_while_a_high_flag_waits(self):
        key = next(k for k, r in self.store.rows.items() if self.store.pending_acks(r) and len(r.open_flags) > 1)
        detail = self.client.get(f"/row/{key}", headers=HX).text
        self.assertNotIn('class="btn btn--primary" name="decision" value="accept"', detail)
        self.assertIn("Acknowledge the High", detail)
        self.client.post(f"/row/{key}/ack", headers=HX)
        detail = self.client.get(f"/row/{key}", headers=HX).text
        self.assertIn('class="btn btn--primary" name="decision" value="accept"', detail)

    def test_handed_over_and_the_since_line(self):
        other = TestClient(self.app)
        sign_in(other, "sam", "supervisor")
        key = self.store.ordered_rows()[-1].key
        other.post(f"/row/{key}/note", data={"text": "Waiting on the locator count."}, headers=HX)
        self.assertEqual(self.store.counts("Lena")["handed"], 1)
        self.assertIn(f'id="row-{key}"', self.client.get("/?view=handed").text)
        sign_in(self.client, "lena", "planner")   # a second sign-in: the briefing says what changed
        page = self.client.get("/").text
        self.assertIn("Since your last sign-in at", page)
        self.assertIn("1 shift note", page)

    def test_one_unit_is_singular(self):
        self.assertEqual((_units(1), _units(2400), _units(-1, "more unit")), ("1 unit", "2,400 units", "-1 more unit"))


if __name__ == "__main__":
    unittest.main()
