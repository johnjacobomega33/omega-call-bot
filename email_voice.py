"""Minimal voice email for the free test (Jacob-approved Nov 9, 2026).

Flow: "email Omega" -> Pixie asks what to say -> reads it back -> sends only after "yes".
Recipients are HARD-LOCKED to ALLOWLIST. Sends via AgentMail from the caller's own inbox
(callers.email, must be @omegamadeit.com). Every attempt is written to email_log.
Per-call draft state lives in call_sessions.pending_request (JSON).
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

import db

ALLOWLIST = {  # spoken name -> address. Only this contact for the test.
    "omega": "johnjacobomega33@gmail.com",
    "jacob": "johnjacobomega33@gmail.com",
}
DISPLAY = {"johnjacobomega33@gmail.com": "Omega"}
SENDER_DOMAIN = "omegamadeit.com"
DAILY_LIMIT = 5
API = "https://api.agentmail.to/v0/inboxes/{inbox}/messages/send"

EMAIL_RE = re.compile(r"\b(?:send|write|shoot)\b.{0,20}\b(?:e-?mail|message|note)\b(?!s)|"
                      r"(?:^|\b(?:can you|could you|please|i want to|i'd like to)\s+)e-?mail\s+(?!list)[a-z]", re.I)
YES_RE = re.compile(r"^\s*(?:yes|yeah|yep|yup|sure|send it|go ahead|please do|correct|that's right|ok(?:ay)?)\b", re.I)
NO_RE = re.compile(r"^\s*(?:no|nope|nah|don't|do not|cancel|stop|never ?mind|wait)\b", re.I)
CANCEL_ONLY_RE = re.compile(r"^\s*(?:no|nope|cancel|stop|never ?mind|forget it)\W*$", re.I)
_SKIP = {"to", "an", "a", "the", "my", "him", "her", "email", "message", "send", "please", "for", "me",
         "real", "quick", "now", "right", "can", "you", "could", "i", "want", "would", "like"}


def wants_email(text: str) -> bool:
    return bool(EMAIL_RE.search(text or ""))


def recipient_name(text: str) -> str:
    """Spoken recipient: an allowlisted name, any other word/address after 'email ... to', or ''."""
    t = text or ""
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", t):
        addr = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", t).group(0).lower()
        return next((n for n, a in ALLOWLIST.items() if a == addr), addr)
    low = t.lower()
    for name in ALLOWLIST:
        if re.search(rf"\b{name}\b", low):
            return name
    m = re.search(r"(?:e-?mail|message|note)\s+(.*)", low)
    for w in re.findall(r"[a-z][a-z'.-]*", m.group(1) if m else ""):
        if w not in _SKIP:
            return w
    return ""


# ----------------------------------------------------------------- draft state
def get_draft(call_id: str | None) -> dict | None:
    if not call_id:
        return None
    row = db.one("SELECT pending_request FROM call_sessions WHERE call_id=$1", [call_id])
    raw = (row or {}).get("pending_request")
    try:
        d = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("kind") == "email" else None


def set_draft(call_id: str | None, draft: dict | None) -> None:
    if call_id:
        db.query("UPDATE call_sessions SET pending_request=$2, updated_at=now() WHERE call_id=$1",
                 [call_id, json.dumps(draft) if draft else None])


# ----------------------------------------------------------------- sending
def _sender(phone: str) -> tuple[str | None, str]:
    row = db.one("SELECT email, name FROM callers WHERE phone=$1", [phone]) or {}
    email = (row.get("email") or "").strip().lower()
    if not email.endswith("@" + SENDER_DOMAIN):
        email = ""
    return (email or None), (row.get("name") or "a Pixie caller")


def _log(phone, call_id, frm, to, subject, body, status, msg_id=None, error=None, is_test=False):
    db.query("INSERT INTO email_log (caller_phone,call_id,from_address,to_address,subject,body,status,"
             "provider_message_id,error,is_test) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
             [phone, call_id, frm, to, subject, body, status, msg_id, (error or "")[:500] or None, is_test])


def _sent_today(phone: str) -> int:
    row = db.one("SELECT COUNT(*)::int AS n FROM email_log WHERE caller_phone=$1 AND status='sent' "
                 "AND created_at > now() - interval '1 day'", [phone])
    return int((row or {}).get("n") or 0)


def _post(inbox: str, payload: dict) -> dict:
    key = os.environ.get("AGENTMAIL_API_KEY", "").strip()
    if not key:
        raise RuntimeError("AGENTMAIL_API_KEY not set")
    req = urllib.request.Request(API.format(inbox=inbox), data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def send(phone: str, call_id: str | None, to: str, body: str, is_test: bool = False) -> bool:
    if to not in ALLOWLIST.values():  # belt and braces: never send outside the allowlist
        _log(phone, call_id, None, to, None, body, "blocked", error="recipient not allowlisted", is_test=is_test)
        return False
    frm, name = _sender(phone)
    subject = f"Message from {name} via Pixie"
    if not frm:
        _log(phone, call_id, None, to, subject, body, "blocked", error="caller has no omegamadeit.com inbox",
             is_test=is_test)
        return False
    text = f"{body}\n\n-- Sent by voice through Pixie (test line). Spoken by {name}, read back and confirmed."
    try:
        res = _post(frm, {"to": [to], "subject": subject, "text": text})
    except (urllib.error.URLError, RuntimeError, ValueError, OSError) as exc:
        _log(phone, call_id, frm, to, subject, body, "failed", error=repr(exc), is_test=is_test)
        print("email send error:", repr(exc), flush=True)
        return False
    _log(phone, call_id, frm, to, subject, body, "sent", msg_id=res.get("message_id"), is_test=is_test)
    return True


# ----------------------------------------------------------------- conversation
def start(phone: str, call_id: str | None, text: str) -> str:
    name = recipient_name(text)
    if name and name not in ALLOWLIST:
        return ("Sorry, sending email to other people isn't available yet. Right now I can only email "
                "Omega. Just say 'email Omega' if you'd like to.")
    if _sent_today(phone) >= DAILY_LIMIT:
        return "You've reached today's email limit for this test. Try again tomorrow."
    if not _sender(phone)[0]:
        return "Email isn't set up for your account yet."
    set_draft(call_id, {"kind": "email", "step": "body", "to": ALLOWLIST["omega"]})
    return "Sure, I'll email Omega. What would you like to say?"


def continue_draft(phone: str, call_id: str | None, text: str, draft: dict, is_test: bool = False) -> str:
    to = draft.get("to") or ALLOWLIST["omega"]
    who = DISPLAY.get(to, "Omega")
    if draft.get("step") == "body":
        body = (text or "").strip()
        if not body or CANCEL_ONLY_RE.match(body):
            set_draft(call_id, None)
            return "Okay, I won't send anything."
        set_draft(call_id, {**draft, "step": "confirm", "body": body[:1500]})
        return f"Here's your email to {who}: {body[:1500]} ... Should I send it? Say yes or no."
    if draft.get("step") == "confirm":
        if YES_RE.match(text or ""):
            set_draft(call_id, None)
            if send(phone, call_id, to, draft.get("body", ""), is_test=is_test):
                return f"Sent! Your email is on its way to {who}."
            return "Sorry, I couldn't send that email right now. Nothing was sent."
        if NO_RE.match(text or ""):
            set_draft(call_id, None)
            return "Okay, I didn't send it. You can say 'email Omega' to start over."
        return "Should I send it? Please say yes or no."
    set_draft(call_id, None)
    return "Sorry, let's start over. Say 'email Omega' when you're ready."
