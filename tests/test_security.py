"""Sign-in and hardening: username/password accounts, signed sessions, throttling, safe redirects,
escaped errors, security headers and the cross-site request check, for the worklist and the simulator.

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

import json
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from recon_agent import __version__, security, sources, users
from recon_agent.events import seed
from recon_agent.events.log import EventLog
from recon_agent.simulator.app import create_app as simulator

from recon_agent import config as _config
assert "recon-tests-" in str(_config.USERS_FILE) and not _config.AI_ENABLED, "tests are not isolated"

PASSWORD = "test-password-1"
cheap_hashes = mock.patch.object(security, "PBKDF2_ITERATIONS", 1000)


def account(username: str, role: str = "supervisor", name: str | None = None) -> dict:
    with cheap_hashes:
        return users.get(username) or users.add(username, name or username.title(), role, PASSWORD)


account("ana", "supervisor", "Ana")   # an accounts file exists: no demo seeding


def worklist_app():
    log = EventLog(Path(tempfile.mkdtemp(prefix="recon-sec-")) / "events.db")
    seed.seed(log)
    with mock.patch.object(sources, "open_feed", lambda *_: sources.EventFeed(log.path)):
        from recon_agent.web.app import create_app
        return create_app()


class WorklistSignInTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = worklist_app()

    def setUp(self):
        self.client = TestClient(self.app)

    def sign_in(self, username: str, password: str = PASSWORD, **headers):
        return self.client.post("/signin", data={"username": username, "password": password},
                                headers=headers, follow_redirects=False)

    def test_anonymous_is_sent_to_sign_in(self):
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/signin"))
        self.assertEqual(self.client.get("/api/flags", follow_redirects=False).status_code, 303)

    def test_wrong_password_is_refused(self):
        account("wrongpw")
        r = self.sign_in("wrongpw", "not-the-password")
        self.assertEqual(r.status_code, 401)
        self.assertIn("do not match", r.text)
        self.assertNotIn("recon_session", r.cookies)
        self.assertEqual(self.sign_in("nobody-here").status_code, 401)

    def test_name_and_role_come_from_the_account(self):
        account("pat", "planner", "Pat Lee")
        self.assertEqual(self.sign_in("PAT").status_code, 303)   # usernames are case-insensitive
        page = self.client.get("/").text
        self.assertIn("Pat Lee", page)
        self.assertIn("planner", page)

    def test_role_cannot_be_chosen_at_sign_in(self):
        account("sup1", "supervisor")
        self.client.post("/signin", data={"username": "sup1", "password": PASSWORD, "role": "manager"},
                         follow_redirects=False)
        self.assertEqual(self.client.post("/flags/R09-0/correction", follow_redirects=False).status_code, 403)

    def test_forged_and_plain_cookies_are_ignored(self):
        account("forger")
        self.sign_in("forger")
        token = self.client.cookies["recon_session"]
        body, mac = token.split(".")
        payload = json.loads(security._unb64(body))
        payload["u"] = "ana"
        forged = TestClient(self.app)
        forged.cookies.set("recon_session", security._b64(json.dumps(payload).encode()) + "." + mac)
        self.assertEqual(forged.get("/", follow_redirects=False).status_code, 303)
        old_style = TestClient(self.app)
        old_style.cookies.set("recon_user", "Mallory")
        old_style.cookies.set("recon_role", "manager")
        self.assertEqual(old_style.get("/", follow_redirects=False).status_code, 303)

    def test_gateway_headers_are_not_trusted(self):
        r = self.client.get("/", headers={"X-Remote-User": "Mallory", "X-Remote-Role": "manager"},
                            follow_redirects=False)
        self.assertEqual(r.status_code, 303)

    def test_expired_session_is_refused(self):
        a = account("expired")
        stale = security.sign({"u": a["username"], "s": a["session"]}, "worklist", max_age=-1)
        self.client.cookies.set("recon_session", stale)
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 303)

    def test_simulator_cookie_does_not_open_the_worklist(self):
        a = account("crossapp")
        self.client.cookies.set("recon_session", security.sign({"u": a["username"], "s": a["session"]}, "simulator"))
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 303)

    def test_password_change_and_removal_end_sessions(self):
        account("rotate")
        self.sign_in("rotate")
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 200)
        with cheap_hashes:
            users.set_password("rotate", "another-password-2")
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 303)
        self.client.post("/signin", data={"username": "rotate", "password": "another-password-2"})
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 200)
        users.remove("rotate")
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 303)

    def test_role_change_applies_at_once(self):
        account("promote", "supervisor")
        self.sign_in("promote")
        self.assertEqual(self.client.post("/flags/R09-0/correction", follow_redirects=False).status_code, 403)
        users.set_role("promote", "manager")
        self.assertEqual(self.client.post("/flags/R09-0/correction", follow_redirects=False).status_code, 404)   # allowed; no such flag

    def test_repeated_failures_lock_the_username(self):
        account("locked")
        for _ in range(_config.SIGNIN_ATTEMPTS):
            self.assertEqual(self.sign_in("locked", "wrong-password").status_code, 401)
        self.assertEqual(self.sign_in("locked").status_code, 429)   # even the right password, for now

    def test_sign_in_never_redirects_off_site(self):
        account("redirect")
        for evil in ("//evil.example", "/\\evil.example", "https://evil.example", "/\r\nSet-Cookie: x=1"):
            r = self.client.post("/signin", data={"username": "redirect", "password": PASSWORD, "next": evil},
                                 follow_redirects=False)
            self.assertEqual(r.status_code, 303)
            self.assertEqual(r.headers["location"], "/")
        r = self.client.post("/signin", data={"username": "redirect", "password": PASSWORD, "next": "/row/150080"},
                             follow_redirects=False)
        self.assertEqual(r.headers["location"], "/row/150080")

    def test_error_fragments_are_escaped(self):
        account("escape")
        self.sign_in("escape")
        r = self.client.post("/flags/<img src=x onerror=alert(1)>/decision", data={"decision": "accept"},
                             headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("<img", r.text)
        self.assertIn("&lt;img", r.text)

    def test_cross_site_posts_are_refused(self):
        account("csrf", "manager")
        self.sign_in("csrf")
        r = self.client.post("/flags/R09-0/decision", data={"decision": "accept"},
                             headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status_code, 403)
        r = self.client.post("/flags/R09-0/decision", data={"decision": "accept"},
                             headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        r = self.client.post("/flags/R09-0/decision", data={"decision": "accept"},
                             headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(r.status_code, 404)   # through the check; no such flag

    def test_security_headers(self):
        r = self.client.get("/signin")
        self.assertIn("frame-ancestors 'none'", r.headers["content-security-policy"])
        self.assertNotIn("unsafe-eval", r.headers["content-security-policy"])
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertNotIn("server", r.headers)

    def test_health_and_docs(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.json(), {"status": "ok", "version": __version__, "source": "events"})
        self.assertEqual(self.client.get("/api/docs").status_code, 404)   # off unless RECON_API_DOCS=on
        self.assertEqual(self.client.get("/api/openapi.json").status_code, 404)


class SimulatorSignInTest(unittest.TestCase):
    def setUp(self):
        log = EventLog(Path(tempfile.mkdtemp(prefix="recon-sec-sim-")) / "events.db")
        seed.seed(log)
        self.client = TestClient(simulator(log.path))

    def test_operator_signs_in_with_an_account(self):
        self.assertEqual(self.client.get("/t/wms.order", follow_redirects=False).status_code, 303)
        r = self.client.post("/signin", data={"username": "ana", "password": "wrong-password"})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/signin", data={"username": "ana", "password": PASSWORD}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.client.get("/t/wms.order").status_code, 200)

    def test_operator_cookie_cannot_be_forged(self):
        self.client.cookies.set("sim_operator", "Mallory")
        self.assertEqual(self.client.get("/t/wms.order", follow_redirects=False).status_code, 303)
        a = account("ana")
        self.client.cookies.set("sim_session", security.sign({"u": a["username"], "s": a["session"]}, "worklist"))
        self.assertEqual(self.client.get("/t/wms.order", follow_redirects=False).status_code, 303)

    def test_corrections_token_is_enforced_when_set(self):
        with mock.patch.object(_config, "CORRECTIONS_TOKEN", "s3cret-token"):
            r = self.client.post("/oic/corrections", json={}, headers={"X-Corrections-Token": "nope"})
            self.assertEqual(r.status_code, 401)


class AccountsTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="recon-users-")) / "users.json"

    def test_demo_accounts_cover_every_role(self):
        with cheap_hashes:
            created = users.seed_demo(self.path, password="demo-password-1")
            self.assertEqual({r for _, _, r, _ in created}, set(users.ROLES))
            self.assertEqual(users.seed_demo(self.path, password="demo-password-1"), [])   # idempotent
        self.assertTrue(users.authenticate("manager1", "demo-password-1", self.path))
        self.assertNotIn("demo-password-1", self.path.read_text())   # only hashes are stored

    def test_random_demo_passwords_when_none_is_given(self):
        with cheap_hashes, mock.patch.dict(os.environ, {"RECON_DEMO_PASSWORD": ""}):
            created = users.seed_demo(self.path)
        passwords = [pw for *_, pw in created]
        self.assertEqual(len(set(passwords)), len(passwords))
        self.assertTrue(all(len(pw) >= users.MIN_PASSWORD for pw in passwords))

    def test_rules_for_new_accounts(self):
        with cheap_hashes:
            with self.assertRaises(users.UserError):
                users.add("ok.name", "OK", "planner", "short", self.path)
            with self.assertRaises(users.UserError):
                users.add("Bad Name!", "Bad", "planner", PASSWORD, self.path)
            with self.assertRaises(users.UserError):
                users.add("ok.name", "OK", "admin", PASSWORD, self.path)
            users.add("ok.name", "OK", "planner", PASSWORD, self.path)
            with self.assertRaises(users.UserError):
                users.add("ok.name", "OK", "planner", PASSWORD, self.path)


class PrimitivesTest(unittest.TestCase):
    def test_signed_tokens(self):
        token = security.sign({"u": "x"}, "worklist", max_age=60)
        self.assertEqual(security.unsign(token, "worklist")["u"], "x")
        self.assertIsNone(security.unsign(token, "simulator"))
        self.assertIsNone(security.unsign(token[:-2] + "AA", "worklist"))
        self.assertIsNone(security.unsign("garbage", "worklist"))
        self.assertIsNone(security.unsign(security.sign({"u": "x"}, "worklist", max_age=-1), "worklist"))

    def test_passwords(self):
        with cheap_hashes:
            h = security.hash_password("correct horse")
        self.assertTrue(security.verify_password("correct horse", h))
        self.assertFalse(security.verify_password("wrong horse", h))
        self.assertFalse(security.verify_password("x", "not-a-hash"))

    def test_atomic_json(self):
        path = Path(tempfile.mkdtemp(prefix="recon-json-")) / "state.json"
        security.write_json(path, {"a": 1})
        security.write_json(path, {"a": 2})
        self.assertEqual(json.loads(path.read_text()), {"a": 2})
        self.assertEqual([p.name for p in path.parent.iterdir()], ["state.json"])   # no temp files left

    def test_rate_limiter_window(self):
        rl = security.RateLimiter(2, 0.2)
        rl.hit("k")
        rl.hit("k")
        self.assertTrue(rl.blocked("k"))
        time.sleep(0.25)
        self.assertFalse(rl.blocked("k"))


if __name__ == "__main__":
    unittest.main()
