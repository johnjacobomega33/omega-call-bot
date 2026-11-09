"""Legal Beagle (information-only) unit tests. No network/DB: db, billing and the LLM are stubbed."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import assistant as A  # noqa: E402
import legal_info as L  # noqa: E402
import voice  # noqa: E402

BANNED = [  # phrases that would be case-specific advice / form selection
    "you should file", "you should write", "you need to file", "you need form", "i recommend",
    "your best option", "you will win", "you have a case", "you should ask the judge",
]


class FakeLog:
    """Minimal stand-in for chat_logs + todos used by voice.legal_intro_given / A.add_todo."""

    def __init__(self):
        self.assistant_lines: list[tuple[str, str]] = []  # (call_id, text)
        self.todos: list[str] = []

    def one(self, sql, params=None):
        if "FROM chat_logs" in sql:
            return {"x": 1} if any(cid == params[0] and params[1] in c
                                   for cid, c in self.assistant_lines) else None
        if "INSERT INTO todos" in sql:
            self.todos.append(params[1])
            return {"id": len(self.todos), "title": params[1]}
        return None


class Statement(unittest.TestCase):
    def test_can_cant_under_30_seconds(self):
        words = len(L.CAN_CANT_TEXT.split())
        self.assertLessEqual(words, 80)  # ~28 s at 170 wpm, ~30 s at 160 wpm

    def test_can_cant_covers_required_points(self):
        t = L.CAN_CANT_TEXT.lower()
        for must in ["svp cases", "official forms", "timeline", "questions for your lawyer",
                     "patients' rights", "state bar", "courts", "legal advice about your case",
                     "what to file or write", "contact your lawyer or the court", "replace your attorney"]:
            self.assertIn(must, t)
        self.assertTrue(L.CAN_CANT_TEXT.startswith(L.CAN_CANT_MARK))

    def test_greeting_and_capabilities_mention_legal_beagle(self):
        self.assertIn("Legal Beagle", voice.BEGIN_MESSAGE)
        self.assertIn("not legal advice", voice.BEGIN_MESSAGE)
        self.assertIn("Legal Beagle", A.CAPABILITIES_TEXT)


class Library(unittest.TestCase):
    def test_every_topic_sourced_and_pending_review(self):
        for k, t in L.TOPICS.items():
            with self.subTest(k=k):
                self.assertTrue(t["sources"])
                self.assertEqual(t["attorney_review"], "pending")
                self.assertTrue(t["text"].startswith("In general"))

    def test_no_advice_language_anywhere(self):
        blob = " ".join([L.CAN_CANT_TEXT, L.REDIRECT, L.CONTACT_REDIRECT, L.REFERRALS, L.topic_menu()]
                        + [L.topic_answer(k) for k in L.TOPICS]).lower()
        for b in BANNED:
            self.assertNotIn(b, blob)

    def test_habeas_uses_hc001(self):
        self.assertIn("HC-001", L.topic_answer("habeas"))

    def test_topic_matching(self):
        cases = {
            "my trial keeps getting continued": "trial_delays",
            "how does the annual exam work": "annual_exam",
            "what's a marsden hearing": "replacing_counsel",
            "tell me about conditional release": "conditional_release",
            "how do habeas petitions work": "habeas",
            "how do I complain to the state bar": "state_bar",
            "can my legal mail be read": "mail_rules",
            "what's the probable cause hearing": "probable_cause",
        }
        for said, want in cases.items():
            with self.subTest(said=said):
                self.assertEqual(L.match_topic(said), want)


class Responses(unittest.TestCase):
    def test_advice_request_redirects(self):
        out = L.respond("My trial keeps getting continued. Should I file a motion to dismiss?",
                        intro_needed=False)
        self.assertIn(L.REDIRECT, out)
        self.assertIn("timely trial", out)
        self.assertIn("question for your lawyer", out)
        self.assertNotIn(L.CAN_CANT_MARK, out)

    def test_which_form_redirects(self):
        out = L.respond("Which form do I need to get out of here?", intro_needed=False)
        self.assertTrue(out.startswith(L.REDIRECT))

    def test_contact_request_refused(self):
        out = L.respond("Can you call my lawyer for me?", intro_needed=False)
        self.assertIn(L.CONTACT_REDIRECT, out)

    def test_unknown_gives_menu_and_referrals(self):
        self.assertIn("Which one?", L.respond("I have a legal question", intro_needed=False))
        self.assertIn("9 3 4, 3 8 6 0", L.respond("who can I call, give me phone numbers", intro_needed=False))

    def test_every_answer_has_reminder(self):
        for k in L.TOPICS:
            self.assertTrue(L.topic_answer(k).endswith(L.REMINDER))

    def test_question_title(self):
        self.assertEqual(L.question_for_attorney_title("save a question for my lawyer: when is my trial?"),
                         "For my lawyer: when is my trial?")
        self.assertEqual(L.question_for_attorney_title("Why was it continued?"),
                         "For my lawyer: Why was it continued?")


class CallFlow(unittest.TestCase):
    """Through voice._assistant_turn with stubs: intro once per call, repeatable, todos, billing."""

    def setUp(self):
        self.fake = FakeLog()
        self.patches = [
            mock.patch.object(voice.db, "one", side_effect=self.fake.one),  # same module object as A.db
            mock.patch.object(voice.billing, "get_balance", return_value=30),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def turn(self, text, intent, title="", call_id="call-1"):
        with mock.patch.object(A, "extract_intent", return_value={"intent": intent, "title": title}):
            out = voice._assistant_turn("+15550000099", text, [], True, call_id=call_id)[0]
        self.fake.assistant_lines.append((call_id, out))
        return out

    def test_intro_first_time_only_then_on_request(self):
        first = self.turn("how does the annual exam work", "legal_info")
        self.assertTrue(first.startswith(L.CAN_CANT_TEXT))
        self.assertIn("examined at least once a year", first)
        second = self.turn("what about conditional release", "legal_info")
        self.assertNotIn(L.CAN_CANT_MARK, second)
        again = self.turn("what can Legal Beagle do?", "chat")  # regex catches it before the LLM
        self.assertTrue(again.startswith(L.CAN_CANT_TEXT))

    def test_new_call_gets_intro_again(self):
        self.assertTrue(self.turn("tell me about habeas", "legal_info").startswith(L.CAN_CANT_TEXT))
        self.assertNotIn(L.CAN_CANT_MARK, self.turn("tell me about habeas", "legal_info"))
        self.assertTrue(self.turn("tell me about habeas", "legal_info", call_id="call-2")
                        .startswith(L.CAN_CANT_TEXT))

    def test_chat_or_lookup_legal_question_rerouted(self):
        out = self.turn("is the public defender allowed to keep continuing my trial", "lookup")
        self.assertIn(L.CAN_CANT_MARK, out)
        self.assertIn("In general", out)

    def test_save_question_for_attorney_uses_todos(self):
        out = self.turn("save a question for my lawyer: why was my trial continued",
                        "legal_question_for_attorney", title="why was my trial continued")
        self.assertEqual(self.fake.todos, ["For my lawyer: why was my trial continued"])
        self.assertIn("won't send it to anyone", out)

    def test_zero_balance_blocks_info_but_not_can_cant(self):
        with mock.patch.object(voice.billing, "get_balance", return_value=0), \
                mock.patch.object(voice.billing, "balance_speech", return_value="You have 0 minutes."):
            blocked = self.turn("tell me about habeas", "legal_info")
            self.assertIn("prepaid minutes", blocked)
            caps = self.turn("what can legal beagle do", "chat")
            self.assertTrue(caps.startswith(L.CAN_CANT_TEXT))


if __name__ == "__main__":
    unittest.main()
