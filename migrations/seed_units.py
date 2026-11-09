"""Seed units table from units_seed.json. Usage: DATABASE_URL=... python migrations/seed_units.py"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db  # noqa: E402

SEED = os.path.join(os.path.dirname(__file__), "units_seed.json")
DIRECTORY_DATE = "2016-06-24"
DIRECTORY_SAVED = "2026-11-08T20:00:00-08:00"
SOURCE = "DSH Coalinga patient phone list (CSHIncomingphones.pdf, revised 06/24/2016)"


def main() -> None:
    rows = json.load(open(SEED, encoding="utf-8"))
    n = 0
    for r in rows:
        db.query(
            "INSERT INTO units (code, display_name, notes, incoming_e164, outgoing_e164, "
            "incoming_raw, outgoing_raw, directory_source, directory_date, directory_saved_at) "
            "VALUES ($1,$2,$3,$4::text[],$5::text[],$6,$7,$8,$9::date,$10::timestamptz) "
            "ON CONFLICT (code) DO UPDATE SET "
            "display_name=EXCLUDED.display_name, notes=EXCLUDED.notes, "
            "incoming_e164=EXCLUDED.incoming_e164, outgoing_e164=EXCLUDED.outgoing_e164, "
            "incoming_raw=EXCLUDED.incoming_raw, outgoing_raw=EXCLUDED.outgoing_raw, "
            "directory_source=EXCLUDED.directory_source, directory_date=EXCLUDED.directory_date, "
            "directory_saved_at=EXCLUDED.directory_saved_at, updated_at=now()",
            [r["code"], r["display_name"], r.get("notes"), r["incoming_e164"], r["outgoing_e164"],
             r.get("incoming_raw"), r.get("outgoing_raw"), SOURCE, DIRECTORY_DATE, DIRECTORY_SAVED])
        n += 1
    count = db.one("SELECT COUNT(*)::int AS n FROM units")
    u11 = db.one("SELECT code, cardinality(incoming_e164) AS i, cardinality(outgoing_e164) AS o "
                 "FROM units WHERE code='Unit 11'")
    print(f"seeded {n} unit rows; table has {count['n']}; Unit 11 incoming={u11['i']} outgoing={u11['o']}")


if __name__ == "__main__":
    main()
