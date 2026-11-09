import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import billing as B  # noqa: E402


class RateMath(unittest.TestCase):
    def test_ten_dollars_is_sixty_minutes(self):
        self.assertEqual(B.dollars_to_minutes(1000), 60)

    def test_one_dollar_is_six_minutes(self):
        self.assertEqual(B.dollars_to_minutes(100), 6)

    def test_fifty_cents_floors(self):
        self.assertEqual(B.dollars_to_minutes(50), 3)

    def test_rejects_non_positive(self):
        with self.assertRaises(ValueError):
            B.dollars_to_minutes(0)
        with self.assertRaises(ValueError):
            B.dollars_to_minutes(-100)

    def test_minutes_to_cents(self):
        self.assertEqual(B.minutes_to_cents(60), 1000)
        self.assertEqual(B.minutes_to_cents(6), 100)


class CallEstimate(unittest.TestCase):
    def test_round_up(self):
        start = datetime(2026, 11, 8, 20, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(B.estimate_call_minutes(start, start + timedelta(seconds=1)), 1)
        self.assertEqual(B.estimate_call_minutes(start, start + timedelta(seconds=60)), 1)
        self.assertEqual(B.estimate_call_minutes(start, start + timedelta(seconds=61)), 2)
        self.assertEqual(B.estimate_call_minutes(start, start + timedelta(minutes=5)), 5)

    def test_none_start(self):
        self.assertEqual(B.estimate_call_minutes(None), 0)


class UnitsSeedFile(unittest.TestCase):
    def test_seed_has_thirty_units_including_ma_and_11(self):
        import json
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations", "units_seed.json")
        with open(path, encoding='utf-8') as f:
            rows = json.load(f)
        codes = {r["code"] for r in rows}
        self.assertEqual(len(rows), 30)
        self.assertIn("MA1", codes)
        self.assertIn("MA2", codes)
        self.assertIn("Unit 11", codes)
        self.assertIn("Unit 28", codes)
        u11 = next(r for r in rows if r["code"] == "Unit 11")
        self.assertEqual(len(u11["incoming_e164"]), 4)
        self.assertTrue(all(n.startswith("+1559") for n in u11["incoming_e164"]))


if __name__ == "__main__":
    unittest.main()
