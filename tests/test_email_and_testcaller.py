"""Voice email (allowlist-locked, AgentMail mocked) + per-caller no-passcode flag + caller-ID aliases.
No network or DB: db.query/db.one are replaced by a tiny fake; AgentMail _post is mocked (no real email)."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import assistant as A  # noqa: E402
import db  # noqa: E402
import email_voice as E  # noqa: E402
import security as S  # noqa: E402
import voice  # noqa: E402

DREW = "+15599341564"


class FakeDB:
    def __init__(self):
        self.sessions = {}
        self.emails = []
        self.callers = {DREW: {"phone": DREW, "name": "Drew", "email": "andrew.ortega@omegamadeit.com",
                               "passcode_hash": None, "passcode_required": False, "is_test": True,
                               "failed_attempts": 0, "locked_until": None}}
        self.aliases = {"+15599350126": DREW}
        self.logs = []

    def query(self, sql, params=None):
        p = params or []
        if sql.startswith("UPDATE call_sessions SET pending_request"):
            self.sessions.setdefault(p[0], {})["pending_request"] = p[1]
        elif sql.startswith("INSERT INTO email_log"):
            self.emails.append(dict(zip(["caller_phone", "call_id", "from", "to", "subject", "body", "status",
                                         "msg_id", "error", "is_test"], p)))
        elif sql.startswith("INSERT INTO call_sessions"):
            self.sessions.setdefault(p[0], {}).update({"call_id": p[0], "caller_phone": p[1], "stage": p[2],
                                                       "verified": p[3]})
        elif sql.startswith("INSERT INTO chat_logs"):
            self.logs.append((p[2], p[3]))
        elif "COUNT(*)::int AS n FROM email_log" in sql:
            return [{"n": sum(1 for e in self.emails if e["status"] == "sent")}]
        elif sql.startswith("SELECT pending_request FROM call_sessions"):
            s = self.sessions.get(p[0])
            return [s] if s else []
        elif sql.startswith("SELECT email, name FROM callers") or sql.startswith("SELECT * FROM callers"):
            c = self.callers.get(p[0])
            return [c] if c else []
        elif sql.startswith("SELECT caller_phone FROM caller_phone_aliases"):
            return [{"caller_phone": self.aliases[p[0]]}] if p[0] in self.aliases else []
        elif sql.startswith("SELECT * FROM call_sessions"):
            s = self.sessions.get(p[0])
            return [s] if s and "stage" in s else []
        elif sql.startswith("SELECT name FROM callers"):
            c = self.callers.get(p[0])
            return [{"name": c["name"]}] if c else []
        return []

    def one(self, sql, params=None):
        rows = self.query(sql, params)
        return rows[0] if rows else None


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.sent = []
        self.p = [mock.patch.object(db, "query", side_effect=self.db.query),
                  mock.patch.object(db, "one", side_effect=self.db.one),
                  mock.patch.object(E, "_post", side_effect=self._fake_post),
                  mock.patch.object(voice.billing, "get_balance", return_value=60)]
        for x in self.p:
            x.start()
        self.db.sessions["c1"] = {"call_id": "c1", "caller_phone": DREW, "stage": "ready", "verified": True}

    def tearDown(self):
        for x in reversed(self.p):
            x.stop()

    def _fake_post(self, inbox, payload):
        self.sent.append((inbox, payload))
        return {"message_id": "m1", "thread_id": "t1"}

    def say(self, text, intent="chat"):
        with mock.patch.object(A, "extract_intent", return_value={"intent": intent, "title": ""}):
            return voice._assistant_turn(DREW, text, [], True, call_id="c1")[0]


class EmailFlow(Base):
    def test_full_flow_sends_only_after_yes(self):
        self.assertIn("What would you like to say", self.say("email Omega"))
        self.assertEqual(self.sent, [])
        back = self.say("Hey Omega, the test is working great")
        self.assertIn("the test is working great", back)
        self.assertIn("Should I send it", back)
        self.assertEqual(self.sent, [])
        self.assertIn("Sent", self.say("yes"))
        self.assertEqual(len(self.sent), 1)
        inbox, payload = self.sent[0]
        self.assertEqual(inbox, "andrew.ortega@omegamadeit.com")
        self.assertEqual(payload["to"], ["johnjacobomega33@gmail.com"])
        self.assertEqual(payload["subject"], "Message from Drew via Pixie")
        self.assertIn("the test is working great", payload["text"])
        self.assertEqual(self.db.emails[-1]["status"], "sent")
        self.assertIsNone(E.get_draft("c1"))

    def test_jacob_alias_accepted(self):
        self.assertIn("I'll email Omega", self.say("send an email to Jacob"))

    def test_no_cancels(self):
        self.say("email Omega")
        self.say("No worries, all good here")  # a body starting with "no" is still a message
        self.assertIn("didn't send", self.say("no"))
        self.assertEqual(self.sent, [])

    def test_unclear_confirm_reasks(self):
        self.say("email Omega")
        self.say("testing one two")
        self.assertIn("yes or no", self.say("hmm"))
        self.assertEqual(self.sent, [])

    def test_other_recipient_refused(self):
        out = self.say("email my mom")
        self.assertIn("isn't available yet", out)
        self.assertIsNone(E.get_draft("c1"))
        out = self.say("send an email to bob@example.com")
        self.assertIn("isn't available yet", out)
        self.assertEqual(self.sent, [])

    def test_send_refuses_non_allowlisted_even_if_called_directly(self):
        self.assertFalse(E.send(DREW, "c1", "someone@example.com", "hi"))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.db.emails[-1]["status"], "blocked")

    def test_api_failure_logged(self):
        with mock.patch.object(E, "_post", side_effect=OSError("down")):
            self.say("email Omega")
            self.say("hello")
            self.assertIn("couldn't send", self.say("yes"))
        self.assertEqual(self.db.emails[-1]["status"], "failed")

    def test_reading_mail_is_not_a_send(self):
        for t in ["do I have any email", "read my emails", "any new messages"]:
            self.assertFalse(E.wants_email(t))

    def test_daily_limit(self):
        self.db.emails = [{"status": "sent"}] * E.DAILY_LIMIT
        self.assertIn("limit", self.say("email Omega"))


class NoPasscodeCaller(Base):
    def test_drew_skips_passcode_via_alias(self):
        ev = {"data": {"callId": "c2", "from": "+15599350126", "direction": "inbound", "transcript": "hello"}}
        with mock.patch.object(voice, "_ready_reply", return_value="READY"):
            out = voice.handle_turn(ev)
        self.assertEqual(out["text"], "READY")
        self.assertTrue(self.db.sessions["c2"]["verified"])
        self.assertEqual(self.db.sessions["c2"]["caller_phone"], DREW)  # alias resolved to Drew

    def test_unknown_number_still_gets_passcode_gate(self):
        ev = {"data": {"callId": "c3", "from": "+15550001111", "direction": "inbound", "transcript": "hello"}}
        out = voice.handle_turn(ev)
        self.assertIn("passcode", out["text"])
        self.assertFalse(self.db.sessions["c3"]["verified"])

    def test_flag_default_requires_passcode(self):
        self.assertTrue(S.passcode_required({"passcode_required": True}))
        self.assertTrue(S.passcode_required({}))
        self.assertTrue(S.passcode_required(None))
        self.assertFalse(S.passcode_required({"passcode_required": False}))

    def test_greeting_does_not_ask_for_passcode(self):
        self.assertNotIn("passcode", voice.BEGIN_MESSAGE)
        self.assertIn("say hello", voice.BEGIN_MESSAGE)


if __name__ == "__main__":
    unittest.main()
