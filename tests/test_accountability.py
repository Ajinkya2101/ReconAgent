"""System accountability and auditable AI explanations.

Every flag says which system misfired (computed by the rules, so it is as auditable as the
quantity), the model's explanation of it is checked against the engine's numbers, and every
model call lands in the audit log.

    python -m unittest tests.test_accountability -v
"""
from __future__ import annotations

import os
import tempfile

os.environ["RECON_AI"] = "off"
os.environ.setdefault("RECON_TEST_DIR", tempfile.mkdtemp(prefix="recon-tests-"))
os.environ["RECON_FEEDBACK_FILE"] = os.path.join(os.environ["RECON_TEST_DIR"], "feedback.json")
os.environ["RECON_USERS_FILE"] = os.path.join(os.environ["RECON_TEST_DIR"], "users.json")
os.environ["RECON_COOKIE_SECURE"] = "off"

import json
import re
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from recon_agent import accountability, ai, engine
from recon_agent.events import scenarios, seed
from recon_agent.events.log import EventLog
from recon_agent.events.projector import Projector

from recon_agent import config as _config
assert "recon-tests-" in str(_config.FEEDBACK_FILE) and not _config.AI_ENABLED, "tests are not isolated"


WO2, WO3 = seed.wo_number(seed.DEFAULT_SALES_DATE, 2), seed.wo_number(seed.DEFAULT_SALES_DATE, 3)


def seeded_log() -> EventLog:
    log = EventLog(Path(tempfile.mkdtemp(prefix="recon-acct-")) / "events.db")
    seed.seed(log)
    return log


def result(log: EventLog):
    p = Projector(log)
    p.catch_up()
    return engine.run(src=p.source_data(), use_ai=False)


def by_id(res) -> dict:
    return {f.id: f for f in res.findings}


def roles(f) -> dict[str, str]:
    return {s["system"]: s["role"] for s in f.accountability["systems"]}


def reply(text: str, tool_calls=None, finish="stop"):
    """A stand-in for one OCI chat choice."""
    return SimpleNamespace(finish_reason=finish,
                           message=SimpleNamespace(content=[SimpleNamespace(text=text)], tool_calls=tool_calls))


class AttributionTest(unittest.TestCase):
    """The rules name the misfiring system for every flag, in the seeded day and every scenario."""

    @classmethod
    def setUpClass(cls):
        cls.runs = {"seed": result(seeded_log())}
        for sid, sc in scenarios.SCENARIOS.items():
            log = seeded_log()
            scenarios.run(log, sc)
            cls.runs[sid] = result(log)

    def test_every_flag_has_a_complete_record(self):
        for name, res in self.runs.items():
            for f in res.findings:
                a = f.accountability
                with self.subTest(run=name, flag=f.id):
                    self.assertNotEqual(a["failure_type"], "Not mapped")
                    self.assertIn(a["origin"], accountability.ORIGINS)
                    self.assertIn(a["impact"], accountability.IMPACTS)
                    self.assertIn(a["basis"], accountability.BASIS)
                    self.assertTrue(a["failure_point"])
                    self.assertEqual([s["system"] for s in a["systems"]][:3], list(accountability.SYSTEMS))
                    for s in a["systems"]:
                        self.assertIn(s["role"], accountability.ROLES)
                    misfired = [s["system"] for s in a["systems"] if s["role"] == "Misfired"]
                    if a["origin"] in ("Integration", "Undetermined"):
                        self.assertEqual(misfired, [])   # the fault sits between systems, or is unknown
                    else:
                        self.assertEqual(misfired, [a["origin"]])

    def test_the_seeded_problems_are_pinned_on_the_right_system(self):
        f = by_id(self.runs["seed"])
        # MIPS sized 950513's work order to the demand left and ignored the stock WMS has available.
        a = f["R05-950513"].accountability
        self.assertEqual((a["origin"], a["impact"], a["basis"]), ("MIPS", "Over-production", "certain"))
        self.assertEqual(roles(f["R05-950513"]), {"MIPS": "Misfired", "ODI": "Affected", "WMS": "Reference"})
        self.assertIn("Plans 4,840 on work order WO-UBD-20260912-002; 4,840 demand left less 1,140 available "
                      "is 3,700.", a["systems"][0]["detail"])
        # ODI's copy of the Colonie order is stale, but MIPS already plans the right demand.
        a = f["R02-2263255-1"].accountability
        self.assertEqual((a["origin"], a["handoff"], a["impact"]), ("Integration", "BUSS → ODI", "Traceability gap"))
        self.assertEqual(roles(f["R02-2263255-1"]), {"MIPS": "Reference", "ODI": "Affected", "WMS": "Not involved",
                                                     "BUSS": "Reference"})
        # WMS on hand does not balance; the data cannot say which figure is wrong.
        a = f["R10-INV-0003"].accountability
        self.assertEqual((a["origin"], a["basis"], a["impact"]), ("WMS", "undetermined", "Traceability gap"))
        self.assertEqual(roles(f[f"R11-{WO2}"])["ODI"], "Misfired")
        self.assertEqual((f[f"R12-{WO3}"].accountability["impact"], roles(f[f"R12-{WO3}"])["WMS"]),
                         ("Short shipment", "Affected"))

    def test_the_scenarios_are_pinned_on_the_right_system(self):
        # S1: MIPS did its sums right on ODI's stale copy of the demand: the interface is at fault.
        for fid in ("R04-76108", "R05-76108"):
            a = by_id(self.runs["S1"])[fid].accountability
            self.assertEqual((a["origin"], a["handoff"], a["basis"]), ("Integration", "BUSS/Oracle → ODI", "certain"))
            self.assertEqual(roles(by_id(self.runs["S1"])[fid])["MIPS"], "Affected")
        a = by_id(self.runs["S4"])["R04-76108"].accountability
        self.assertEqual((a["origin"], a["handoff"]), ("Integration", "WMS → ODI"))
        # S5: a planner override in MIPS, which ODI never received.
        r05, r07 = by_id(self.runs["S5"])["R05-76108"], by_id(self.runs["S5"])["R07-WO-UBD-20260912-001"]
        self.assertEqual((r05.accountability["origin"], r05.accountability["impact"]), ("MIPS", "Over-production"))
        self.assertEqual((r07.accountability["origin"], r07.accountability["handoff"]), ("Integration", "MIPS → ODI"))
        a = by_id(self.runs["S8"])["R06-76108"].accountability
        self.assertEqual((a["origin"], a["impact"], a["basis"]), ("MIPS", "Under-production", "certain"))
        self.assertEqual(by_id(self.runs["S6"])["R10-INV-0001"].accountability["impact"], "Overstated inventory")
        f = by_id(self.runs["S10"])["R13-76108"]
        self.assertEqual(f.accountability["origin"], "Undetermined")
        self.assertEqual((roles(f)["BUSS"], roles(f)["MIPS"]), ("Conflicting", "Conflicting"))

    def test_quantities_are_written_unsigned(self):
        for name, res in self.runs.items():
            for f in res.findings:
                for sy in f.accountability["systems"]:
                    with self.subTest(run=name, flag=f.id, system=sy["system"]):
                        self.assertNotRegex(sy["detail"], r"(?<![\w(])-\d")

    def test_kpis_total_the_flags_by_origin(self):
        for name, res in self.runs.items():
            with self.subTest(run=name):
                origins = res.kpis["by_origin"]
                self.assertEqual(sum(r["flags"] for r in origins.values()), len(res.findings))
                self.assertEqual(sum(r["flags"] for r in res.kpis["by_impact"].values()), len(res.findings))
                dollars = [r["dollars"] for r in origins.values()]
                self.assertEqual(dollars, sorted(dollars, reverse=True))
        self.assertIn("Integration (BUSS/Oracle → ODI)", self.runs["S1"].kpis["by_origin"])
        self.assertIn("Where the flags started: ODI", self.runs["seed"].narration["shift_briefing"])


class FigureCheckTest(unittest.TestCase):
    """An explanation may only quote numbers the engine produced."""

    def setUp(self):
        self.res = result(seeded_log())
        self.f = by_id(self.res)["R05-950513"]
        self.payloads = {f.id: ai._finding_payload(f) for f in self.res.findings}

    def entry(self, **over) -> dict:
        e = {"id": self.f.id, "owner": "Production Planner",
             "explanation": "MIPS plans 4,840 on the work order, but 4,840 demand left less 1,140 available is "
                            "3,700. Cut the work order to 3,700.",
             "root_cause": "MIPS sized the work order to the demand left, not the shortfall.",
             "cause_chain": [{"system": "MIPS", "step": "Planned 4,840 on the work order."},
                             {"system": "WMS", "step": "Has 1,140 available."}],
             "impact": "1,140 units of over-production.", "verify": "The MIPS work order.",
             "evidence_used": ["shortfall", "wms_available"], "linked_findings": []}
        return {**e, **over}

    def test_figures_from_the_engine_pass(self):
        chk = ai.check_entry(self.entry(), self.f, self.payloads, self.res.kpis)["check"]
        self.assertEqual(chk["unverified_figures"], [])
        self.assertGreaterEqual(chk["figures_checked"], 6)
        self.assertTrue(chk["chain_starts_at_origin"])

    def test_an_invented_figure_is_reported(self):
        e = self.entry(explanation="The work order makes 1,350 too many.")
        self.assertEqual(ai.check_entry(e, self.f, self.payloads, self.res.kpis)["check"]["unverified_figures"],
                         ["1,350"])

    def test_codes_dates_and_small_counts_are_not_figures(self):
        checked, unknown = ai.check_figures(
            ["R05 on ODI-0017 and WO-UBD-20260912-001 at 2026-11-05 20:31, across 3 lines in run 2."], set())
        self.assertEqual(unknown, ["2026", "20"])   # the year and hour are checked; the rest are codes
        checked, unknown = ai.check_figures(["MIPS was -1,200 off; 1,200."], {1200.0})
        self.assertEqual((checked, unknown), (2, []))

    def test_bad_citations_are_dropped_and_recorded(self):
        e = self.entry(evidence_used=["shortfall", "made_up_key"], linked_findings=["R99-NOPE", self.f.id],
                       cause_chain=[{"system": "Excel", "step": "x"}, {"system": "ODI", "step": "Built it."}])
        out = ai.check_entry(e, self.f, self.payloads, self.res.kpis)
        self.assertEqual(out["evidence_used"], ["shortfall"])
        self.assertEqual(out["linked_findings"], [])
        self.assertEqual([s["system"] for s in out["cause_chain"]], ["ODI"])
        chk = out["check"]
        self.assertEqual(chk["evidence_dropped"], ["made_up_key"])
        self.assertEqual(chk["links_dropped"], ["R99-NOPE", self.f.id])
        self.assertEqual(chk["steps_dropped"], 1)
        self.assertFalse(chk["chain_starts_at_origin"])   # the rules say MIPS, the model started at ODI

    def test_a_linked_flags_figures_count_as_known(self):
        r02, r10 = by_id(self.res)["R02-2263255-1"], by_id(self.res)["R10-INV-0003"]
        e = {"id": r10.id, "explanation": "76105 also has an ODI copy at 800 instead of 900.",
             "linked_findings": [r02.id]}
        self.assertEqual(ai.check_entry(e, r10, self.payloads, self.res.kpis)["check"]["unverified_figures"], [])
        e["linked_findings"] = []
        self.assertEqual(ai.check_entry(e, r10, self.payloads, self.res.kpis)["check"]["unverified_figures"], ["800", "900"])


class AuditLogTest(unittest.TestCase):
    """Every model call is logged with its input, output and checks; explanations carry the log id."""

    def setUp(self):
        self.audit = Path(tempfile.mkdtemp(prefix="recon-audit-")) / "ai_audit.jsonl"
        p = mock.patch.object(_config, "AI_AUDIT_FILE", self.audit)
        p.start()
        self.addCleanup(p.stop)
        self.res = result(seeded_log())

    def lines(self) -> list[dict]:
        return [json.loads(x) for x in self.audit.read_text(encoding="utf-8").splitlines()]

    @staticmethod
    def model_reply(messages, **_):
        ids = re.findall(r'"id": "(R\d\d-[^"]+)"', messages[1].content[0].text)
        findings = [{"id": i, "owner": "Production Planner", "explanation": f"{i}: see the evidence.",
                     "root_cause": "x", "cause_chain": [{"system": "MIPS", "step": "Misfired."}],
                     "impact": "Over-production.", "verify": "The MIPS work order.", "evidence_used": [],
                     "linked_findings": []} for i in ids]
        findings[0]["explanation"] = "It made 9,999 too many."   # one invented figure
        return reply(json.dumps({"shift_briefing": "MIPS caused the most exposure.", "top_actions": ["act"],
                                 "findings": findings}))

    def test_a_narration_is_logged_and_its_entries_point_at_the_log(self):
        top = self.res.findings[:5]
        with mock.patch.object(ai, "_chat", self.model_reply):
            out = ai.narrate_with_model(top, self.res.kpis)
        [rec] = self.lines()
        self.assertEqual(rec["kind"], "narration")
        self.assertEqual(rec["audit_id"], out["audit_id"])
        self.assertEqual([f["id"] for f in rec["request"]["findings"]], [f.id for f in top])
        self.assertIn("accountability", rec["request"]["findings"][0])
        self.assertEqual(len(rec["prompt_sha256"]), 64)
        self.assertEqual(rec["checks"][top[0].id]["unverified_figures"], ["9,999"])
        for f in top:
            self.assertEqual(out["by_id"][f.id]["audit_id"], rec["audit_id"])
        self.assertEqual(out["by_id"][top[0].id]["check"]["unverified_figures"], ["9,999"])

    def test_a_failed_call_is_logged_too(self):
        with mock.patch.object(ai, "_chat", lambda *a, **k: reply("not json")):
            with self.assertRaises(ai.AIError):
                ai.narrate_with_model(self.res.findings[:3], self.res.kpis)
        [rec] = self.lines()
        self.assertIn("unusable narration", rec["error"])

    def test_text_that_cannot_be_logged_is_not_shown(self):
        with mock.patch.object(_config, "AI_AUDIT_FILE", Path(tempfile.mkdtemp())):   # a directory
            with mock.patch.object(ai, "_chat", self.model_reply):
                with self.assertRaises(ai.AIError):
                    ai.narrate_with_model(self.res.findings[:3], self.res.kpis)

    def test_an_assistant_answer_is_logged_with_its_tool_calls(self):
        call = SimpleNamespace(id="c1", name="get_sku_position", arguments='{"sku": "950513"}')
        turns = iter([reply("", tool_calls=[call]),
                      reply("MIPS plans 4,840 for 950513 against a 3,700 shortfall, about 4,321 extra.")])
        with mock.patch.object(ai, "credentials_available", lambda: True), \
                mock.patch.object(ai, "_chat", lambda *a, **k: next(turns)):
            out = ai.ask_audited("why so much 950513?", self.res.positions, self.res.findings, user="ana")
        [rec] = self.lines()
        self.assertEqual((rec["kind"], rec["user"], rec["audit_id"]), ("ask", "ana", out["audit_id"]))
        self.assertEqual(rec["tool_calls"][0]["tool"], "get_sku_position")
        self.assertEqual(out["check"]["unverified_figures"], ["4,321"])


class CardTest(unittest.TestCase):
    def test_the_card_shows_accountability_and_the_ai_trail(self):
        from fastapi.testclient import TestClient
        from recon_agent import security, sources, users
        from recon_agent.web.app import create_app as worklist

        audit = Path(tempfile.mkdtemp(prefix="recon-audit-")) / "ai_audit.jsonl"
        log = seeded_log()
        patches = [mock.patch.object(_config, "AI_AUDIT_FILE", audit),
                   mock.patch.object(ai, "credentials_available", lambda: True),
                   mock.patch.object(ai, "_chat", AuditLogTest.model_reply),
                   mock.patch("recon_agent.config.AI_DEBOUNCE_SECONDS", 0.05),
                   mock.patch.object(sources, "open_feed", lambda *_: sources.EventFeed(log.path))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with mock.patch.object(security, "PBKDF2_ITERATIONS", 1000):
            if not users.get("aria"):
                users.add("aria", "Aria", "planner", "test-password-1")
        app = worklist()
        client = TestClient(app)
        client.post("/signin", data={"username": "aria", "password": "test-password-1"}, follow_redirects=False)

        card = client.get("/row/950513", headers={"HX-Request": "true"}).text   # shown with or without AI
        self.assertIn("Started in", card)
        self.assertIn("Over-production", card)
        self.assertIn('role--misfired', card)
        self.assertIn("Plans 4,840 on work order WO-UBD-20260912-002", card)
        self.assertIn("Shortfall: the work order", card)   # the equation, step by step

        end = time.monotonic() + 5
        while time.monotonic() < end and client.get("/api/status").json().get("narration") != "ai":
            time.sleep(0.05)
        card = client.get("/row/76105", headers={"HX-Request": "true"}).text
        self.assertIn("How it happened", card)
        audit_id = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])["audit_id"]
        self.assertIn(audit_id, card)
        self.assertIn("not in the engine's data: 9,999", card)   # R12 on 76105 ranks first
        flags = {f["id"]: f for f in client.get("/api/flags").json()}
        self.assertEqual(flags["R12-WO-UBD-20260912-003"]["accountability"]["origin"], "ODI")
        self.assertEqual(flags["R12-WO-UBD-20260912-003"]["ai"]["audit_id"], audit_id)
        briefing = client.get("/briefing", headers={"HX-Request": "true"}).text
        self.assertIn("Where today's flags started", briefing)


if __name__ == "__main__":
    unittest.main()
