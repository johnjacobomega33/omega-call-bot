"""AgentPhone voice-webhook call flow: greeting -> passcode gate -> briefing -> assistant."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

import assistant as A
import billing
import db
import email_voice as E
import legal_info as L
import llm
import security as S

NAME = A.ASSISTANT_NAME

KEYPAD_ENABLED = os.environ.get("KEYPAD_ENABLED", "false").lower() == "true"
_PASS_ASK = ("First, can I get your 6-digit passcode? You can say it or type it on your keypad."
             if KEYPAD_ENABLED else "First, can I get your 6-digit passcode? Just say the six digits.")
# Spoken before we know who is calling, so it must not assume a passcode (some test callers skip it).
_START = "Go ahead and say hello to get started."


def greeting(name: str | None = None) -> str:
    """Pre-passcode greeting. AgentPhone speaks agent.beginMessage before we know who is calling,
    so the live beginMessage uses greeting(None); the name is used after verification."""
    hi = f"Hi {name}" if name else "Hi there"
    return (f"{hi}, it's so good to hear from you! This is {NAME}, your personal assistant. "
            "I can also share general legal information through Legal Beagle. That's information only, "
            "not legal advice. Quick tip before we start: if you ever want to know everything I can help with, just "
            "say 'tell me what you can do' and I'll walk you through it. " + _START)


BEGIN_MESSAGE = greeting(None)

CAPS_RE = re.compile(r"what (?:all )?(?:can|do) you do|tell me what you can do|what are you able|"
                     r"what can i (?:do|ask)|list of (?:things|everything)|\bhelp\b|\bmenu\b", re.I)
LOCKOUT_TEXT = "For your security, I've paused access for 5 minutes. Please call back in a little bit."
BYE_RE = re.compile(r"\b(?:bye|goodbye|that's all|that is all|hang up)\b", re.I)


def is_test_number(phone: str) -> bool:
    return phone.startswith("+1555")


def _session(call_id: str) -> dict | None:
    return db.one("SELECT * FROM call_sessions WHERE call_id=$1", [call_id])


def _save(call_id: str, phone: str, stage: str, verified: bool = False,
          pending_hash: str | None = None) -> None:
    # When first verified, stamp billable_started_at for prepaid minute deduction at hangup.
    db.query(
        "INSERT INTO call_sessions (call_id,caller_phone,stage,verified,pending_hash,"
        "verified_at,billable_started_at) VALUES ($1,$2,$3,$4,$5,"
        "CASE WHEN $4 THEN now() ELSE NULL END, CASE WHEN $4 THEN now() ELSE NULL END) "
        "ON CONFLICT (call_id) DO UPDATE SET stage=$3, verified=$4, pending_hash=$5, updated_at=now(), "
        "verified_at=COALESCE(call_sessions.verified_at, CASE WHEN $4 THEN now() ELSE NULL END), "
        "billable_started_at=COALESCE(call_sessions.billable_started_at, "
        "CASE WHEN $4 THEN now() ELSE NULL END)",
        [call_id, phone, stage, verified, pending_hash])


def _first_name(text: str) -> str:
    m = re.search(r"(?:my name is|call me|it's|it is|i'm|i am|this is)\s+([a-z][a-z' -]{0,40})", text, re.I)
    cand = m.group(1) if m else text
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", cand)
    if not words or len(words) > 3:
        return ""
    return " ".join(w.capitalize() for w in words[:2])


def _ready_reply(phone: str, name: str | None) -> str:
    hello = f"Perfect, thank you{', ' + name if name else ''}! Here's what's going on today."
    gate = billing.gate_for_call(phone)
    bal_bit = ""
    if gate["zero"]:
        bal_bit = (" Quick note: you have no prepaid minutes left, so this call may be cut short "
                   "until a check or money order tops you up. ")
    elif gate["warn"] and gate.get("speech"):
        bal_bit = f" Quick note: {gate['speech']} "
    return f"{hello} {bal_bit.strip()} {A.briefing(phone)} So, how can I help you today?".replace("  ", " ")


def handle_turn(event: dict[str, Any]) -> dict[str, Any]:
    """Process one agent.message (voice) event. Returns the AgentPhone response object."""
    data = event.get("data") or {}
    call_id = str(data.get("callId") or "")
    direction = str(data.get("direction") or "inbound")
    raw_phone = str(data.get("from") if direction == "inbound" else data.get("to") or "")
    phone = S.resolve_phone(raw_phone) if raw_phone else raw_phone
    text = str(data.get("transcript") or "").strip()
    keypad = str(data.get("digits") or data.get("dtmf") or "")  # not sent by AgentPhone today
    if not call_id or not phone:
        return {"text": "Sorry, I couldn't identify this call."}
    sess = _session(call_id)
    caller = S.get_caller(phone)
    test = is_test_number(phone) or bool((caller or {}).get("is_test"))
    has_code = bool(caller and caller.get("passcode_hash"))
    if sess is None and caller and not S.passcode_required(caller):
        # Per-caller test flag (e.g., Drew's free test): skip the passcode gate entirely.
        _save(call_id, phone, "ready", verified=True)
        A.log_turn(phone, call_id, "user", text or "[silence]", test)
        msg = _ready_reply(phone, A.caller_name(phone))
        A.log_turn(phone, call_id, "assistant", msg, test)
        return {"text": msg}
    if sess is None:
        stage = "verify" if has_code else "set_code"
        _save(call_id, phone, stage)
        sess = {"stage": stage, "verified": False, "pending_hash": None}
    stage = sess["stage"]

    digits = keypad if keypad.isdigit() else S.parse_digits(text)
    gate = stage in ("set_code", "confirm_code", "verify")
    A.log_turn(phone, call_id, "user", "[passcode entered]" if gate and digits else (text or "[silence]"), test)

    def reply(msg: str, *, hangup: bool = False) -> dict[str, Any]:
        A.log_turn(phone, call_id, "assistant", msg, test)
        out: dict[str, Any] = {"text": msg}
        if hangup:
            out["hangup"] = True
        return out

    # ---- capabilities are always available, even before the passcode (no personal data)
    if gate and not digits and CAPS_RE.search(text):
        tail = ("Whenever you're ready, just say your six-digit passcode." if stage == "verify"
                else "First, let's set up your six-digit passcode. Please say six digits you'll remember.")
        return reply(f"{A.CAPABILITIES_TEXT} {tail}")
    if gate and not digits and BYE_RE.search(text):
        return reply("Okay, talk soon. Goodbye!", hangup=True)

    # ---- first call: choose a code, then confirm it
    if stage == "set_code":
        if len(digits) != S.PASSCODE_LEN:
            msg = ("Since this is your first call, let's set up a six-digit passcode to keep "
                   "your calendar and messages private. Please say six digits you'll remember.")
            if digits:
                msg = "I need exactly six digits. Please say your new six-digit passcode."
            return reply(msg)
        _save(call_id, phone, "confirm_code", pending_hash=S.hash_passcode(digits))
        return reply("Got it! Since this is your first call, that will be your new passcode. "
                     "Please say the same six digits again to confirm.")

    if stage == "confirm_code":
        if not digits:
            return reply("Please say your six-digit passcode again to confirm it.")
        if not S.check_passcode(digits, sess.get("pending_hash")):
            _save(call_id, phone, "set_code")
            return reply("Those didn't match, so let's start over. Please say a new six-digit passcode.")
        S.set_passcode(phone, digits, is_test=test, pre_hashed=sess["pending_hash"])
        _save(call_id, phone, "ask_name", verified=True)
        return reply("Perfect, your passcode is saved. You'll say it at the start of each call. "
                     "What would you like me to call you?")

    if stage == "ask_name":
        name = _first_name(text)
        if name:
            A.set_name(phone, name)
        _save(call_id, phone, "ready", verified=True)
        return reply(f"Nice to meet you{', ' + name if name else ''}! How can I help you today?")

    # ---- returning caller: verify (3 tries, then 5-minute lock, enforced in the DB)
    if stage == "verify":
        st = S.LockState(int(caller["failed_attempts"] or 0), S._ts(caller.get("locked_until")))
        if S.is_locked(st, S.utcnow()):
            _save(call_id, phone, "locked")
            return reply(LOCKOUT_TEXT, hangup=True)
        if not digits:
            return reply(_PASS_ASK.replace("First, can", "Before I pull anything up, can"))
        res = S.verify(phone, digits)
        if res["ok"]:
            _save(call_id, phone, "ready", verified=True)
            return reply(_ready_reply(phone, A.caller_name(phone)))
        if res["locked"]:
            _save(call_id, phone, "locked")
            return reply(LOCKOUT_TEXT, hangup=True)
        return reply("Hmm, that didn't match. Let's try once more.")

    if stage == "locked" or not sess.get("verified"):
        return reply(LOCKOUT_TEXT, hangup=True)

    # ---- verified: assistant
    out = _assistant_turn(phone, text, event.get("recentHistory") or [], test, call_id=call_id)
    return reply(out[0], hangup=out[0].endswith("Goodbye!"))


def legal_intro_given(call_id: str | None) -> bool:
    """True if Pixie already spoke the Legal Beagle can/can't statement on this call (chat_logs)."""
    if not call_id:
        return False
    row = db.one("SELECT 1 AS x FROM chat_logs WHERE call_id=$1 AND role='assistant' "
                 "AND position($2 in content) > 0 LIMIT 1", [call_id, L.CAN_CANT_MARK])
    return bool(row)


def _legal_turn(phone: str, it: str, cmd: dict, text: str, test: bool, call_id: str | None) -> str:
    if it == "legal_capabilities":
        return f"{L.CAN_CANT_TEXT} {L.PRIVACY}"
    intro = "" if legal_intro_given(call_id) else L.CAN_CANT_TEXT + " "
    if it == "legal_question_for_attorney":
        row = A.add_todo(phone, L.question_for_attorney_title(cmd.get("title") or text), is_test=test)
        return (f"{intro}Saved to your to-do list: {row['title']}. I won't send it to anyone. "
                "Say 'read my to-do list' to hear your questions before you talk to your lawyer.")
    return L.respond(text, intro_needed=bool(intro))


def _assistant_turn(phone: str, text: str, history: list, test: bool,
                    call_id: str | None = None) -> tuple[str]:
    # An email draft in progress owns the turn (what to say -> read back -> yes/no).
    draft = E.get_draft(call_id)
    if draft:
        return (E.continue_draft(phone, call_id, text, draft, is_test=test),)
    # Deterministic first: these phrasings never reach the LLM classifier.
    if E.wants_email(text):
        cmd = {"intent": "send_email"}
    elif L.LEGAL_CAPS_RE.search(text or ""):
        cmd = {"intent": "legal_capabilities"}
    else:
        try:
            cmd = A.extract_intent(text, history=history)
        except Exception as exc:  # noqa: BLE001
            print("intent error:", exc, flush=True)
            return ("Sorry, I had trouble with that. Could you say it again?",)
    it = cmd["intent"]
    # Safety net: legal-sounding questions never go to free-form chat or web lookup.
    if it in ("chat", "lookup") and L.looks_legal(text):
        it = "legal_info"
    # Soft block: zero prepaid minutes — still allow balance check, goodbye, capabilities, chat.
    if billing.get_balance(phone) <= 0 and it not in (
            "check_balance", "minutes_remaining", "capabilities", "legal_capabilities", "goodbye",
            "chat", "briefing"):
        return (billing.balance_speech(phone) + " I can still chat briefly, but calendar and lists "
                "need prepaid minutes. Ask Jacob how to mail a check or money order.",)
    if it == "capabilities":
        return (A.CAPABILITIES_TEXT,)
    if it == "send_email":
        return (E.start(phone, call_id, text),)
    if it in ("legal_info", "legal_capabilities", "legal_question_for_attorney"):
        return (_legal_turn(phone, it, cmd, text, test, call_id),)
    if it in ("check_balance", "minutes_remaining"):
        return (billing.balance_speech(phone),)
    if it == "briefing":
        return (A.briefing(phone) + " Anything else?",)
    if it == "goodbye":
        return ("You're welcome! Talk soon. Goodbye!",)
    if it == "set_name" and cmd.get("name"):
        A.set_name(phone, cmd["name"])
        return (f"Got it, I'll call you {cmd['name'].strip()}.",)
    if it == "add_appointment":
        start = A.resolve_start(cmd.get("date", ""), cmd.get("time", ""))
        if not cmd.get("date"):
            return ("Sure. What day is that?",)
        if start is None:
            return (f"What time is {cmd.get('title') or 'that'}?",)
        if start < datetime.now(timezone.utc):
            return ("That time has already passed. What date did you mean?",)
        row = A.add_appointment(phone, cmd.get("title") or "Appointment", start,
                                remind_minutes_before=cmd.get("remind_minutes_before"),
                                notes=cmd.get("notes", ""), source_text=text, is_test=test)
        return (f"Done. {row['title']}, {A.say_time(start)}. I'll call you with a reminder beforehand.",)
    if it == "list_appointments":
        items = A.upcoming_appointments(phone, 5)
        if not items:
            return ("You don't have anything coming up on your calendar.",)
        said = "; ".join(f"{i['title']}, {A.say_time(A._ts(i['starts_at']), i['timezone'])}" for i in items)
        return (f"Here's what's coming up: {said}.",)
    if it == "cancel_appointment":
        res = A.cancel_appointment(phone, cmd.get("target") or cmd.get("title", ""), cmd.get("target_date", ""))
        if res["cancelled"]:
            c = res["cancelled"]
            return (f"Okay, I cancelled {c['title']} on {A.say_time(A._ts(c['starts_at']), c['timezone'])}.",)
        if res["candidates"]:
            names = ", or ".join(f"{c['title']} {A.say_time(A._ts(c['starts_at']), c['timezone'])}"
                                 for c in res["candidates"][:3])
            return (f"Which one should I cancel: {names}?",)
        return ("I couldn't find that appointment.",)
    if it == "add_todo":
        row = A.add_todo(phone, cmd.get("title") or text, is_test=test)
        return (f"Added {row['title']} to your to-do list.",)
    if it == "list_todos":
        items = A.open_todos(phone, 8)
        if not items:
            return ("Your to-do list is empty. Nice work!",)
        return ("On your to-do list: " + "; ".join(i["title"] for i in items) + ".",)
    if it == "complete_todo":
        res = A.complete_todo(phone, cmd.get("target") or cmd.get("title", ""))
        if res["done"]:
            return (f"Nice! I checked off {res['done']['title']}.",)
        return ("Which to-do did you finish?",)
    if it == "add_project":
        row = A.add_project(phone, cmd.get("title") or text, cmd.get("next_step", ""), is_test=test)
        return (f"I've added the project {row['name']}.",)
    if it == "list_projects":
        items = A.unfinished_projects(phone, 5)
        if not items:
            return ("You don't have any unfinished projects.",)
        said = "; ".join(f"{p['name']}" + (f", next step {p['next_step']}" if p.get("next_step") else "")
                         for p in items)
        return (f"Your open projects: {said}.",)
    if it == "update_project":
        res = A.update_project(phone, cmd.get("target") or cmd.get("title", ""),
                               cmd.get("project_status", ""), cmd.get("next_step", ""))
        if res["updated"]:
            u = res["updated"]
            return (f"Updated {u['name']}, it's now {u['status'].replace('_', ' ')}.",)
        return ("Which project do you mean?",)
    if it == "lookup":
        try:
            return (A.lookup(cmd.get("title") or text),)
        except Exception:  # noqa: BLE001
            return ("Sorry, I couldn't look that up right now.",)
    if it == "read_chat_log":
        return (A.chat_log_summary(phone),)
    if it == "read_messages":
        items = A.unread_inbox(phone, 5)
        if not items:
            return ("You don't have any new emails or texts. Email and text syncing isn't hooked up yet.",)
        said = "; ".join(f"a {i['kind']} from {i.get('sender') or 'someone'}"
                         + (f" about {i['subject']}" if i.get("subject") else "") for i in items)
        return (f"You have {said}.",)
    # general chat
    try:
        out = llm.generate(llm.history_to_contents(history[-6:], text),
                           f"You are {NAME}, a quirky, warm, light and friendly personal assistant (a secretary) on a phone call. "
                           "Reply in 1-3 short spoken sentences. No lists or markdown. Never explain law or give legal "
                           "advice; for legal topics say 'Ask me about Legal Beagle' instead.")
    except Exception:  # noqa: BLE001
        out = "Sorry, I didn't catch that. Could you say it another way?"
    return (out,)


def handle_call_ended(event: dict[str, Any]) -> None:
    data = event.get("data") or {}
    call_id = str(data.get("callId") or "")
    if not call_id:
        return
    sess = _session(call_id)
    phone = (sess or {}).get("caller_phone") or ""
    # Prepaid deduction before deleting the session
    if sess and sess.get("verified") and phone:
        started = sess.get("billable_started_at") or sess.get("verified_at") or sess.get("created_at")
        try:
            from datetime import datetime, timezone
            if started:
                s = str(started).replace(" ", "T", 1)
                if len(s) >= 3 and (s[-3] == "+" or s[-3] == "-") and ":" not in s[-5:]:
                    pass
                started_dt = datetime.fromisoformat(s.replace("Z", "+00:00") if s.endswith("Z") else s)
                mins = billing.estimate_call_minutes(started_dt)
                if mins > 0:
                    billing.deduct_call_minutes(phone, mins, call_id=call_id)
        except Exception as exc:  # noqa: BLE001
            print("billing deduct error:", repr(exc), flush=True)
    db.query("DELETE FROM call_sessions WHERE call_id=$1", [call_id])
    if not sess or not sess.get("verified"):
        return
    turns = db.query("SELECT role, content FROM chat_logs WHERE call_id=$1 ORDER BY id", [call_id])
    user_turns = [t for t in turns if t["role"] == "user" and t["content"] != "[passcode entered]"]
    if len(user_turns) < 2 or not llm.api_key():
        return
    convo = "\n".join(f"{t['role']}: {t['content']}" for t in turns)
    try:
        summary = llm.generate([{"role": "user", "parts": [{"text": convo}]}],
                               "Summarize this phone call in one short sentence for the caller's log. "
                               "Never include passcodes or digits strings.", temperature=0.2, max_tokens=80)
    except Exception:  # noqa: BLE001
        return
    db.query("INSERT INTO call_summaries (summary, caller_number) VALUES ($1,$2)",
             [summary, sess["caller_phone"]])
