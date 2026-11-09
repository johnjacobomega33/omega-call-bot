"""Passcode security: spoken-digit parsing, salted scrypt hashing, lockout rules."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import db

PASSCODE_LEN = 6
MAX_ATTEMPTS = 3
LOCKOUT = timedelta(minutes=5)

# ---------------------------------------------------------------- digit parsing
_UNITS = {
    "zero": 0, "oh": 0, "o": 0, "nought": 0, "nil": 0,
    "one": 1, "won": 1, "two": 2, "to": 2, "too": 2, "three": 3, "tree": 3,
    "four": 4, "for": 4, "fore": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "ate": 8, "nine": 9, "niner": 9,
}
# words that only count as digits when next to another number word/numeral
_HOMOPHONES = {"oh", "o", "won", "to", "too", "tree", "for", "fore", "ate", "nil"}
_TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_MULT = {"double": 2, "triple": 3}


def _is_numberish(tok: str | None) -> bool:
    if tok is None:
        return False
    return (tok.isdigit() or (tok in _UNITS and tok not in _HOMOPHONES)
            or tok in _TEENS or tok in _TENS or tok in _MULT or tok == "hundred")


def parse_digits(text: str | None) -> str:
    """Turn speech like 'one two three, 4 5 6' / '12 34 56' / 'double five' into '123456'."""
    if not text:
        return ""
    t = text.lower().replace("-", " ")
    t = re.sub(r"(\d)", r" \1 ", t)            # split numerals into single digits
    toks = re.findall(r"[a-z]+|\d", t)
    # a homophone ("to", "for", "oh") counts only inside a run of number words
    # that has at least one unambiguous number in it
    ok_homo: set[int] = set()
    run: list[int] = []
    for k, tk in enumerate(toks + ["."]):
        if tk in _HOMOPHONES or _is_numberish(tk):
            run.append(k)
            continue
        if any(_is_numberish(toks[m]) for m in run):
            ok_homo.update(m for m in run if toks[m] in _HOMOPHONES)
        run = []
    out: list[str] = []
    i = 0
    while i < len(toks):
        tok = toks[i]
        next_t = toks[i + 1] if i + 1 < len(toks) else None
        if tok.isdigit():
            out.append(tok)
        elif tok in _MULT and next_t is not None:
            nxt = next_t
            if nxt.isdigit() or nxt in _UNITS:
                d = nxt if nxt.isdigit() else str(_UNITS[nxt])
                out.append(d * _MULT[tok])
                i += 2
                continue
        elif tok in _UNITS:
            if tok in _HOMOPHONES and i not in ok_homo:
                pass  # "the code for me" -> not a digit
            else:
                out.append(str(_UNITS[tok]))
        elif tok in _TEENS:
            out.append(str(_TEENS[tok]))
        elif tok in _TENS:
            val = _TENS[tok]
            if next_t in _UNITS and _UNITS[next_t] != 0 and next_t not in _HOMOPHONES:
                out.append(str(val + _UNITS[next_t]))
                i += 2
                continue
            out.append(str(val))
        elif tok == "hundred" and out and len(out[-1]) == 1:
            lead = out.pop()
            rest = 0
            j = i + 1
            if j < len(toks) and toks[j] == "and":
                j += 1
            if j < len(toks) and toks[j] in _TEENS:
                rest, j = _TEENS[toks[j]], j + 1
            elif j < len(toks) and toks[j] in _TENS:
                rest, j = _TENS[toks[j]], j + 1
                if j < len(toks) and toks[j] in _UNITS and toks[j] not in _HOMOPHONES:
                    rest, j = rest + _UNITS[toks[j]], j + 1
            elif j < len(toks) and toks[j] in _UNITS and toks[j] not in _HOMOPHONES:
                rest, j = _UNITS[toks[j]], j + 1
            out.append(f"{lead}{rest:02d}")
            i = j
            continue
        i += 1
    return "".join(out)


# ---------------------------------------------------------------- hashing
_N, _R, _P = 2 ** 14, 8, 1


def _pepper() -> bytes:
    return os.environ.get("PASSCODE_PEPPER", "").encode("utf-8")


def _material(code: str, peppered: bool) -> bytes:
    raw = code.encode("utf-8")
    if peppered:
        return hmac.new(_pepper(), raw, hashlib.sha256).digest()
    return raw


def hash_passcode(code: str) -> str:
    if not (len(code) == PASSCODE_LEN and code.isdigit()):
        raise ValueError("passcode must be exactly 6 digits")
    peppered = bool(_pepper())
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(_material(code, peppered), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"scrypt${'p1' if peppered else 'p0'}${_N}${_R}${_P}${b64(salt)}${b64(dk)}"


def check_passcode(code: str, stored: str | None) -> bool:
    if not stored or not code:
        return False
    try:
        algo, pep, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt, want = base64.b64decode(salt_b64), base64.b64decode(dk_b64)
        got = hashlib.scrypt(_material(code, pep == "p1"), salt=salt, n=int(n), r=int(r),
                             p=int(p), dklen=len(want))
    except Exception:  # noqa: BLE001
        return False
    return hmac.compare_digest(got, want)


# ---------------------------------------------------------------- lockout rules (pure)
@dataclass
class LockState:
    failed_attempts: int
    locked_until: datetime | None


def is_locked(st: LockState, now: datetime) -> bool:
    return st.locked_until is not None and now < st.locked_until


def after_failure(st: LockState, now: datetime) -> LockState:
    """Record one wrong attempt. Caller must check is_locked() first."""
    failed = st.failed_attempts
    if st.locked_until is not None and now >= st.locked_until:
        failed = 0  # previous lockout has expired: fresh set of tries
    failed += 1
    if failed >= MAX_ATTEMPTS:
        return LockState(MAX_ATTEMPTS, now + LOCKOUT)
    return LockState(failed, None)


def after_success(_st: LockState, _now: datetime) -> LockState:
    return LockState(0, None)


def attempts_left(st: LockState, now: datetime) -> int:
    if is_locked(st, now):
        return 0
    if st.locked_until is not None:  # expired lock
        return MAX_ATTEMPTS
    return max(0, MAX_ATTEMPTS - st.failed_attempts)


# ---------------------------------------------------------------- DB-backed operations
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ts(v) -> datetime | None:
    if v in (None, ""):
        return None
    s = str(v).replace(" ", "T", 1)
    if re.search(r"[+-]\d\d$", s):
        s += ":00"
    return datetime.fromisoformat(s)


def get_caller(phone: str) -> dict | None:
    return db.one("SELECT * FROM callers WHERE phone=$1", [phone])


def caller_has_passcode(phone: str) -> bool:
    row = get_caller(phone)
    return bool(row and row.get("passcode_hash"))


def set_passcode(phone: str, code: str, *, is_test: bool = False, allow_overwrite: bool = False,
                 pre_hashed: str | None = None) -> bool:
    """Create/attach a passcode. Returns False if one exists and overwrite not allowed."""
    h = pre_hashed or hash_passcode(code)
    if allow_overwrite:
        row = db.one(
            "INSERT INTO callers (phone, passcode_hash, is_test) VALUES ($1,$2,$3) "
            "ON CONFLICT (phone) DO UPDATE SET passcode_hash=EXCLUDED.passcode_hash, "
            "failed_attempts=0, locked_until=NULL, updated_at=now() RETURNING id",
            [phone, h, is_test])
    else:
        row = db.one(
            "INSERT INTO callers (phone, passcode_hash, is_test) VALUES ($1,$2,$3) "
            "ON CONFLICT (phone) DO UPDATE SET passcode_hash=EXCLUDED.passcode_hash, updated_at=now() "
            "WHERE callers.passcode_hash IS NULL RETURNING id",
            [phone, h, is_test])
    return row is not None


def verify(phone: str, code: str, now: datetime | None = None) -> dict:
    """Check a passcode with server-side 3-strikes / 5-minute lockout.

    Returns {ok, locked, attempts_left, locked_until, reason}. Never returns the code.
    """
    for _ in range(3):  # optimistic-concurrency retries
        now = now or utcnow()
        row = get_caller(phone)
        if not row or not row.get("passcode_hash"):
            return {"ok": False, "locked": False, "attempts_left": None, "locked_until": None,
                    "reason": "no_passcode"}
        st = LockState(int(row["failed_attempts"] or 0), _ts(row.get("locked_until")))
        if is_locked(st, now):
            return {"ok": False, "locked": True, "attempts_left": 0,
                    "locked_until": st.locked_until.isoformat(), "reason": "locked"}
        good = len(code) == PASSCODE_LEN and check_passcode(code, row["passcode_hash"])
        new = after_success(st, now) if good else after_failure(st, now)
        upd = db.one(
            "UPDATE callers SET failed_attempts=$2, locked_until=$3, updated_at=now(), "
            "last_verified_at = CASE WHEN $4 THEN now() ELSE last_verified_at END "
            "WHERE phone=$1 AND failed_attempts=$5 AND locked_until IS NOT DISTINCT FROM $6 "
            "RETURNING id",
            [phone, new.failed_attempts, new.locked_until.isoformat() if new.locked_until else None,
             good, st.failed_attempts, row.get("locked_until")])
        if upd is None:
            now = None
            continue  # someone else updated concurrently; re-read
        locked = is_locked(new, now)
        return {"ok": good, "locked": locked, "attempts_left": attempts_left(new, now),
                "locked_until": new.locked_until.isoformat() if new.locked_until else None,
                "reason": "ok" if good else ("locked" if locked else "wrong_code")}
    return {"ok": False, "locked": False, "attempts_left": None, "locked_until": None,
            "reason": "busy"}
