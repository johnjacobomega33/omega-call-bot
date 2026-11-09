"""Live tests against Neon + Gemini. Skipped unless DATABASE_URL / GEMINI_API_KEY are set.
Uses fake caller numbers +1555000009x and deletes their rows afterwards."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import assistant as A  # noqa: E402
import db  # noqa: E402
import reminders  # noqa: E402
import security as S  # noqa: E402
import voice  # noqa: E402

PHONES = ["+15550000091", "+15550000092", "+15550000093"]


def cleanup():
    for p in PHONES:
        db.query("DELETE FROM callers WHERE phone=$1", [p])  # cascades appointments/todos/projects/inbox
        db.query("DELETE FROM chat_logs WHERE caller_phone=$1", [p])
        db.query("DELETE FROM call_sessions WHERE caller_phone=$1", [p])
        db.query("DELETE FROM call_summaries WHERE caller_number=$1", [p])


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "needs DATABASE_URL")
class LiveDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup()

    @classmethod
    def tearDownClass(cls):
        cleanup()

    def test_lockout_in_db(self):
        p = PHONES[0]
        self.assertTrue(S.set_passcode(p, "246810", is_test=True))
        self.assertFalse(S.set_passcode(p, "111111"))  # can't silently overwrite
        row = S.get_caller(p)
        self.assertNotIn("246810", row["passcode_hash"])
        self.assertEqual(S.verify(p, "000000")["attempts_left"], 2)
        self.assertEqual(S.verify(p, "000001")["attempts_left"], 1)
        r = S.verify(p, "000002")
        self.assertTrue(r["locked"])
        lu = S._ts(S.get_caller(p)["locked_until"])
        self.assertAlmostEqual((lu - datetime.now(timezone.utc)).total_seconds(), 300, delta=30)
        r = S.verify(p, "246810")  # right code while locked still refused
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "locked")
        # simulate 5 minutes passing
        db.query("UPDATE callers SET locked_until = now() - interval '1 second' WHERE phone=$1", [p])
        r = S.verify(p, "246810")
        self.assertTrue(r["ok"])
        row = S.get_caller(p)
        self.assertEqual((row["failed_attempts"], row["locked_until"]), (0, None))

    def test_voice_flow_and_reminder_dry_run(self):
        p, call = PHONES[1], "test-call-live-1"
        ev = lambda t: {"event": "agent.message", "channel": "voice",  # noqa: E731
                        "data": {"callId": call, "from": p, "to": "+15594263799", "transcript": t,
                                 "direction": "inbound"}, "recentHistory": []}
        r = voice.handle_turn(ev("hello"))
        self.assertIn("first call", r["text"])
        r = voice.handle_turn(ev("tell me what you can do"))
        self.assertIn("appointments", r["text"])
        r = voice.handle_turn(ev("one three five seven nine two"))
        self.assertIn("again", r["text"])
        r = voice.handle_turn(ev("135 792"))
        self.assertIn("call you", r["text"])
        r = voice.handle_turn(ev("call me Testy"))
        self.assertIn("Testy", r["text"])
        logs = db.query("SELECT content FROM chat_logs WHERE call_id=$1", [call])
        self.assertFalse(any("135" in l["content"] or "seven" in l["content"] for l in logs))
        voice.handle_call_ended({"event": "agent.call_ended", "data": {"callId": call}})

        # seed data, then second call: wrong, right -> briefing
        start = datetime.now(timezone.utc) + timedelta(minutes=30)
        A.add_appointment(p, "Test dentist", start, remind_minutes_before=60, is_test=True)
        A.add_todo(p, "buy stamps", True)
        A.add_project(p, "garage shelves", "", True)
        db.query("INSERT INTO inbox_items (caller_phone,kind,sender,subject,is_test) VALUES ($1,'email','Stub','Hi',true)", [p])
        call = "test-call-live-2"
        r = voice.handle_turn(ev("my passcode is 1 1 1 1 1 1"))
        self.assertEqual(r["text"], "Hmm, that didn't match. Let's try once more.")
        r = voice.handle_turn(ev("one three five seven nine two"))
        t = r["text"]
        for frag in ("Perfect, thank you, Testy", "Test dentist", "1 new email", "buy stamps",
                     "garage shelves", "So, how can I help you today?"):
            self.assertIn(frag, t)

        out = reminders.run_due()
        mine = [e for e in out["results"] if e["to"] == p]
        self.assertTrue(out["dry_run"])
        self.assertEqual([e["status"] for e in mine], ["dry_run"])
        row = db.one("SELECT reminded, reminder_status FROM appointments WHERE caller_phone=$1", [p])
        self.assertEqual((row["reminded"], row["reminder_status"]), (True, "dry_run"))

    def test_voice_lockout(self):
        p = PHONES[2]
        S.set_passcode(p, "999111", is_test=True)
        ev = lambda t, c: {"event": "agent.message", "channel": "voice",  # noqa: E731
                           "data": {"callId": c, "from": p, "transcript": t, "direction": "inbound"}}
        voice.handle_turn(ev("1 2 3 4 5 6", "lk1"))
        voice.handle_turn(ev("1 2 3 4 5 7", "lk1"))
        r = voice.handle_turn(ev("1 2 3 4 5 8", "lk1"))
        self.assertTrue(r.get("hangup"))
        self.assertIn("paused access for 5 minutes", r["text"])
        r = voice.handle_turn(ev("9 9 9 1 1 1", "lk2"))  # new call, correct code, still locked
        self.assertTrue(r.get("hangup"))


@unittest.skipUnless(os.environ.get("GEMINI_API_KEY"), "needs GEMINI_API_KEY")
class LiveGemini(unittest.TestCase):
    def test_extract_dentist_tuesday(self):
        now = datetime(2026, 11, 8, 19, 0, tzinfo=ZoneInfo("America/Los_Angeles"))  # Sunday
        cmd = A.extract_intent("Put the dentist on Tuesday at 3pm", now=now)
        self.assertEqual(cmd["intent"], "add_appointment")
        self.assertIn("dentist", cmd["title"].lower())
        self.assertEqual((cmd["date"], cmd["time"]), ("2026-11-10", "15:00"))
        start = A.resolve_start(cmd["date"], cmd["time"])
        self.assertEqual(start.utcoffset(), timedelta(hours=-8))

    def test_extract_cancel_and_list(self):
        now = datetime(2026, 11, 8, 19, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        self.assertEqual(A.extract_intent("what's on my calendar this week?", now=now)["intent"],
                         "list_appointments")
        c = A.extract_intent("cancel my dentist appointment", now=now)
        self.assertEqual(c["intent"], "cancel_appointment")
        self.assertIn("dentist", (c["target"] + c["title"]).lower())


if __name__ == "__main__":
    unittest.main()
