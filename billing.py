"""Prepaid minutes + mailed-check payments for Pixie.

Rate: $10/hour talk time => $1 = 6 minutes => minutes = amount_cents * 6 // 100.
callers.minutes_balance is a cached SUM of minute_transactions.delta_minutes.
Admin actions (log payment / adjust) require the existing BOT_API_KEY at the HTTP layer.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import db

DOLLARS_PER_HOUR = 10
MINUTES_PER_DOLLAR = 6  # $10/hr => 60 min / $10 = 6 min per $1
LOW_BALANCE_WARN = 5    # warn when remaining minutes <= this
ZERO_BLOCK = True       # when balance is 0 after verify, warn strongly (soft block in voice)


def dollars_to_minutes(amount_cents: int) -> int:
    if amount_cents <= 0:
        raise ValueError("amount_cents must be positive")
    return (amount_cents * MINUTES_PER_DOLLAR) // 100


def minutes_to_cents(minutes: int) -> int:
    """Inverse for receipts (exact only when minutes divisible by 6)."""
    return (minutes * 100) // MINUTES_PER_DOLLAR


def ensure_caller(phone: str, *, is_test: bool = False, unit: str | None = None,
                  name: str | None = None) -> dict:
    row = db.one("SELECT * FROM callers WHERE phone=$1", [phone])
    if row:
        if unit and not row.get("unit"):
            db.query("UPDATE callers SET unit=$2, updated_at=now() WHERE phone=$1", [phone, unit])
            row = db.one("SELECT * FROM callers WHERE phone=$1", [phone])
        return row
    db.query(
        "INSERT INTO callers (phone, is_test, unit, name, minutes_balance) VALUES ($1,$2,$3,$4,0) "
        "ON CONFLICT (phone) DO NOTHING",
        [phone, is_test, unit, name])
    return db.one("SELECT * FROM callers WHERE phone=$1", [phone]) or {"phone": phone, "minutes_balance": 0}


def get_balance(phone: str) -> int:
    row = db.one("SELECT minutes_balance FROM callers WHERE phone=$1", [phone])
    if not row:
        return 0
    return int(row.get("minutes_balance") or 0)


def recompute_balance(phone: str) -> int:
    row = db.one(
        "SELECT COALESCE(SUM(delta_minutes),0)::int AS bal FROM minute_transactions WHERE caller_phone=$1",
        [phone])
    bal = int((row or {}).get("bal") or 0)
    db.query("UPDATE callers SET minutes_balance=$2, updated_at=now() WHERE phone=$1", [phone, bal])
    return bal


def _append_tx(phone: str, delta: int, reason: str, *, reference: str | None = None,
               created_by: str = "system") -> dict:
    ensure_caller(phone)
    # Single-statement CTE: insert tx, update cache, return row
    row = db.one(
        "WITH cur AS (SELECT minutes_balance FROM callers WHERE phone=$1 FOR UPDATE), "
        "ins AS ("
        "  INSERT INTO minute_transactions (caller_phone, delta_minutes, reason, reference, created_by, balance_after) "
        "  SELECT $1, $2, $3, $4, $5, (SELECT minutes_balance FROM cur) + $2 "
        "  RETURNING *"
        "), "
        "upd AS ("
        "  UPDATE callers SET minutes_balance = (SELECT balance_after FROM ins), updated_at=now() "
        "  WHERE phone=$1 RETURNING minutes_balance"
        ") "
        "SELECT ins.*, upd.minutes_balance AS caller_balance FROM ins, upd",
        [phone, delta, reason, reference, created_by])
    if row is None:
        # Neon HTTPS may not support FOR UPDATE the same way; fall back to sequential
        bal = get_balance(phone) + delta
        row = db.one(
            "INSERT INTO minute_transactions (caller_phone, delta_minutes, reason, reference, created_by, balance_after) "
            "VALUES ($1,$2,$3,$4,$5,$6) RETURNING *",
            [phone, delta, reason, reference, created_by, bal])
        db.query("UPDATE callers SET minutes_balance=$2, updated_at=now() WHERE phone=$1", [phone, bal])
    return row


def log_payment(phone: str, *, method: str, amount_cents: int,
                check_or_mo_number: str | None = None, payer_name: str | None = None,
                received_date: str | date | None = None, notes: str | None = None,
                logged_by: str = "jacob", credit_now: bool = True,
                is_test: bool = False, unit: str | None = None) -> dict[str, Any]:
    """Jacob-only admin: record a check/MO/card payment and optionally credit minutes."""
    method = method.strip().lower()
    if method not in ("check", "money_order", "card", "other"):
        raise ValueError("method must be check|money_order|card|other")
    if amount_cents <= 0:
        raise ValueError("amount_cents must be positive")
    ensure_caller(phone, is_test=is_test, unit=unit)
    minutes = dollars_to_minutes(amount_cents) if credit_now else 0
    rd = received_date or date.today().isoformat()
    if isinstance(rd, date):
        rd = rd.isoformat()

    pay = db.one(
        "INSERT INTO payments (caller_phone, method, amount_cents, check_or_mo_number, payer_name, "
        "received_date, status, minutes_credited, notes, logged_by) "
        "VALUES ($1,$2,$3,$4,$5,$6,'received',$7,$8,$9) RETURNING *",
        [phone, method, amount_cents, check_or_mo_number, payer_name, rd, minutes, notes, logged_by])
    tx = None
    if credit_now and minutes > 0:
        tx = _append_tx(phone, minutes, "payment",
                        reference=f"payment:{pay['id']}", created_by=logged_by)
        db.query("UPDATE payments SET minute_tx_id=$2, updated_at=now() WHERE id=$1",
                 [pay["id"], tx["id"]])
        pay = db.one("SELECT * FROM payments WHERE id=$1", [pay["id"]])
    return {
        "payment": pay,
        "transaction": tx,
        "minutes_credited": minutes,
        "balance": get_balance(phone),
        "rate": {"dollars_per_hour": DOLLARS_PER_HOUR, "minutes_per_dollar": MINUTES_PER_DOLLAR},
    }


def mark_payment_status(payment_id: int, status: str, *, logged_by: str = "jacob",
                        notes: str | None = None) -> dict:
    status = status.strip().lower()
    if status not in ("received", "deposited", "bounced", "refunded", "void"):
        raise ValueError("bad status")
    pay = db.one("SELECT * FROM payments WHERE id=$1", [payment_id])
    if not pay:
        raise ValueError("payment not found")
    phone = pay["caller_phone"]
    # Bounce / refund / void: reverse credited minutes once
    if status in ("bounced", "refunded", "void") and pay["status"] not in ("bounced", "refunded", "void"):
        credited = int(pay.get("minutes_credited") or 0)
        if credited > 0:
            _append_tx(phone, -credited, "refund" if status == "refunded" else "adjustment",
                       reference=f"payment:{payment_id}:{status}", created_by=logged_by)
            db.query("UPDATE payments SET minutes_credited=0, status=$2, notes=COALESCE($3, notes), "
                     "updated_at=now() WHERE id=$1", [payment_id, status, notes])
        else:
            db.query("UPDATE payments SET status=$2, notes=COALESCE($3, notes), updated_at=now() WHERE id=$1",
                     [payment_id, status, notes])
    else:
        db.query("UPDATE payments SET status=$2, notes=COALESCE($3, notes), updated_at=now() WHERE id=$1",
                 [payment_id, status, notes])
    return {"payment": db.one("SELECT * FROM payments WHERE id=$1", [payment_id]),
            "balance": get_balance(phone)}


def deduct_call_minutes(phone: str, minutes: int, *, call_id: str | None = None,
                        created_by: str = "system") -> dict[str, Any]:
    """Charge talk time at end of call. minutes must be >= 1; clamps so balance never goes below 0
    unless ALLOW_NEGATIVE_BALANCE=true (not used by default)."""
    if minutes <= 0:
        return {"deducted": 0, "balance": get_balance(phone), "skipped": True}
    bal = get_balance(phone)
    charge = min(minutes, bal)  # never go negative by default
    if charge <= 0:
        return {"deducted": 0, "balance": bal, "skipped": True, "reason": "zero_balance"}
    tx = _append_tx(phone, -charge, "call_usage",
                    reference=f"call:{call_id}" if call_id else None, created_by=created_by)
    return {"deducted": charge, "requested": minutes, "balance": get_balance(phone),
            "transaction": tx, "shortfall": minutes - charge}


def adjust_minutes(phone: str, delta: int, *, reason: str = "adjustment",
                   notes: str | None = None, created_by: str = "jacob") -> dict:
    if reason not in ("adjustment", "bonus", "refund"):
        raise ValueError("reason must be adjustment|bonus|refund")
    ensure_caller(phone)
    tx = _append_tx(phone, delta, reason, reference=notes, created_by=created_by)
    return {"transaction": tx, "balance": get_balance(phone)}


def balance_speech(phone: str) -> str:
    bal = get_balance(phone)
    if bal <= 0:
        return ("You don't have any prepaid minutes left. To keep talking, someone can mail a "
                "check or money order — ask Jacob for the mailing address. Rate is ten dollars "
                "an hour, so one dollar buys six minutes.")
    if bal == 1:
        mins = "1 minute"
    else:
        mins = f"{bal} minutes"
    dollars = bal / MINUTES_PER_DOLLAR
    approx = f"about {dollars:.0f} dollars" if dollars >= 1 else "under a dollar"
    msg = f"You have {mins} of prepaid talk time left — {approx} at ten dollars an hour."
    if bal <= LOW_BALANCE_WARN:
        msg += " You're running low; a mailed check or money order can top you up."
    return msg


def gate_for_call(phone: str) -> dict[str, Any]:
    """Used after passcode verify: whether to warn / soft-block based on balance."""
    bal = get_balance(phone)
    return {
        "balance": bal,
        "warn": bal <= LOW_BALANCE_WARN,
        "zero": bal <= 0,
        "speech": balance_speech(phone) if bal <= LOW_BALANCE_WARN else None,
    }


def list_callers_by_unit(unit: str) -> list[dict]:
    return db.query(
        "SELECT phone, name, unit, minutes_balance, last_verified_at, created_at "
        "FROM callers WHERE unit=$1 ORDER BY name NULLS LAST, phone", [unit])


def list_units() -> list[dict]:
    return db.query(
        "SELECT code, display_name, notes, "
        "cardinality(incoming_e164) AS incoming_count, "
        "cardinality(outgoing_e164) AS outgoing_count, "
        "directory_date, directory_source "
        "FROM units ORDER BY code")


def set_caller_unit(phone: str, unit: str) -> dict:
    u = db.one("SELECT code FROM units WHERE code=$1", [unit])
    if not u:
        raise ValueError(f"unknown unit: {unit}")
    ensure_caller(phone)
    db.query("UPDATE callers SET unit=$2, updated_at=now() WHERE phone=$1", [phone, unit])
    return db.one("SELECT phone, name, unit, minutes_balance FROM callers WHERE phone=$1", [phone])


def estimate_call_minutes(started_at: datetime | None, ended_at: datetime | None = None) -> int:
    """Billable whole minutes; minimum 1 if the call lasted any time after verification."""
    if not started_at:
        return 0
    end = ended_at or datetime.now(timezone.utc)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    secs = max(0, int((end - started_at).total_seconds()))
    if secs <= 0:
        return 0
    return max(1, (secs + 59) // 60)  # round up to next minute
