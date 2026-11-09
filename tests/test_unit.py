import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import security as S  # noqa: E402


class DigitParsing(unittest.TestCase):
    CASES = {
        "one two three four five six": "123456",
        "123 456": "123456",
        "1 2 3, 4 5 6.": "123456",
        "12 34 56": "123456",
        "123-456": "123456",
        "one twenty three four fifty six": "123456",
        "twelve thirty four fifty six": "123456",
        "double five one two three four": "551234",
        "triple seven nine nine nine": "777999",
        "oh one two three four five": "012345",
        "zero zero seven one one nine": "007119",
        "my code for you is 9 8 7 6 5 4": "987654",
        "to for six eight ate one": "246881",
        "One Two Three Four Five Six": "123456",
        "one hundred twenty three four five six": "123456",
        "nine eight seven, uh, six five four": "987654",
        "the code is for me": "",
        "what?": "",
        "": "",
    }

    def test_cases(self):
        for spoken, want in self.CASES.items():
            with self.subTest(spoken=spoken):
                self.assertEqual(S.parse_digits(spoken), want)


class Hashing(unittest.TestCase):
    def test_roundtrip_and_salt(self):
        h1, h2 = S.hash_passcode("123456"), S.hash_passcode("123456")
        self.assertNotEqual(h1, h2)  # random salt
        self.assertNotIn("123456", h1)  # never plaintext
        self.assertTrue(h1.startswith("scrypt$"))
        self.assertTrue(S.check_passcode("123456", h1))
        self.assertFalse(S.check_passcode("123457", h1))
        self.assertFalse(S.check_passcode("", h1))
        self.assertFalse(S.check_passcode("123456", None))
        self.assertFalse(S.check_passcode("123456", "garbage"))

    def test_pepper(self):
        os.environ["PASSCODE_PEPPER"] = "pepper-A"
        try:
            h = S.hash_passcode("654321")
            self.assertIn("$p1$", h)
            self.assertTrue(S.check_passcode("654321", h))
            os.environ["PASSCODE_PEPPER"] = "pepper-B"
            self.assertFalse(S.check_passcode("654321", h))
        finally:
            os.environ.pop("PASSCODE_PEPPER", None)

    def test_rejects_bad_codes(self):
        for bad in ("12345", "1234567", "abcdef", ""):
            with self.assertRaises(ValueError):
                S.hash_passcode(bad)


class Lockout(unittest.TestCase):
    def test_three_fails_lock_five_minutes_then_unlock(self):
        t0 = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
        st = S.LockState(0, None)
        st = S.after_failure(st, t0)
        self.assertEqual((st.failed_attempts, S.is_locked(st, t0), S.attempts_left(st, t0)), (1, False, 2))
        st = S.after_failure(st, t0 + timedelta(seconds=5))
        self.assertEqual((st.failed_attempts, S.attempts_left(st, t0)), (2, 1))
        t3 = t0 + timedelta(seconds=10)
        st = S.after_failure(st, t3)
        self.assertTrue(S.is_locked(st, t3))
        self.assertEqual(st.locked_until, t3 + timedelta(minutes=5))
        self.assertTrue(S.is_locked(st, t3 + timedelta(minutes=4, seconds=59)))
        self.assertEqual(S.attempts_left(st, t3 + timedelta(minutes=1)), 0)
        t_after = t3 + timedelta(minutes=5)
        self.assertFalse(S.is_locked(st, t_after))
        self.assertEqual(S.attempts_left(st, t_after), 3)
        # first failure after expiry starts a fresh count (not an instant re-lock)
        st2 = S.after_failure(st, t_after)
        self.assertEqual((st2.failed_attempts, st2.locked_until), (1, None))

    def test_success_resets(self):
        t0 = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
        st = S.after_failure(S.after_failure(S.LockState(0, None), t0), t0)
        st = S.after_success(st, t0)
        self.assertEqual((st.failed_attempts, st.locked_until), (0, None))


if __name__ == "__main__":
    unittest.main()
