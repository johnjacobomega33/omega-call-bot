"""Apply schema.sql to DATABASE_URL (idempotent). Usage: python migrate.py"""
import os
import re

import db


def statements(path: str = os.path.join(os.path.dirname(__file__), "schema.sql")) -> list[str]:
    text = open(path, encoding="utf-8").read()
    text = re.sub(r"--[^\n]*", "", text)
    return [s.strip() for s in text.split(";") if s.strip()]


def main() -> None:
    stmts = statements()
    for s in stmts:
        db.query(s)
    print(f"applied {len(stmts)} statements")


if __name__ == "__main__":
    main()
