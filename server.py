"""Omega call-bot: Gemini brain + passcode-gated calendar / to-dos / projects + reminders."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import assistant as A
import db
import llm
import reminders
import security as S
import voice

SYSTEM_PROMPT = (
    f"You are {A.ASSISTANT_NAME}, a quirky, warm, friendly personal assistant on the phone. "
    "Keep every reply to 1-3 short spoken sentences. No lists, no markdown, no stage directions."
)
E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def _send(h: BaseHTTPRequestHandler, status: int, body: bytes, ctype: str) -> None:
    h.send_response(status)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def _json(h, status: int, payload: Any) -> None:
    _send(h, status, json.dumps(payload, default=str).encode(), "application/json; charset=utf-8")


def _text(h, status: int, text: str) -> None:
    _send(h, status, text.encode(), "text/plain; charset=utf-8")


def _bot_key_ok(h) -> bool:
    want = os.environ.get("BOT_API_KEY", "").strip()
    got = (h.headers.get("X-Bot-Key") or "").strip()
    return bool(want) and hmac.compare_digest(want, got)


def _agentphone_sig_ok(raw: bytes, h) -> bool:
    secret = os.environ.get("AGENTPHONE_WEBHOOK_SECRET", "").strip()
    sig, ts = h.headers.get("X-Webhook-Signature") or "", h.headers.get("X-Webhook-Timestamp") or ""
    if not secret or not re.fullmatch(r"\d{1,12}", ts) or not re.fullmatch(r"sha256=[0-9a-f]{64}", sig):
        return False
    if abs(time.time() - int(ts)) > 300:
        return False
    exp = hmac.new(secret.encode(), ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + exp, sig)


class Bad(Exception):
    def __init__(self, status: int, payload: dict):
        super().__init__(str(payload))
        self.status, self.payload = status, payload


def _phone(body: dict) -> str:
    p = str(body.get("phone") or "").strip()
    if not E164.match(p):
        raise Bad(400, {"error": "phone must be E.164, e.g. +15550000001"})
    return p


def _gate(body: dict) -> str:
    """Require a correct passcode (counts toward the 3-try / 5-minute lockout)."""
    phone = _phone(body)
    code = S.parse_digits(str(body.get("passcode") or ""))
    res = S.verify(phone, code)
    if not res["ok"]:
        raise Bad(423 if res["locked"] else 401, {"error": "passcode required", **res})
    return phone


# ------------------------------------------------------------------ API handlers
def api_passcode_set(b):
    phone = _phone(b)
    code = S.parse_digits(str(b.get("passcode") or ""))
    if len(code) != S.PASSCODE_LEN:
        raise Bad(400, {"error": "passcode must be exactly 6 digits"})
    if not S.set_passcode(phone, code, is_test=voice.is_test_number(phone)):
        raise Bad(409, {"error": "passcode already set for this phone"})
    if b.get("name"):
        A.set_name(phone, str(b["name"]))
    return {"ok": True}


def api_passcode_verify(b):
    phone = _phone(b)
    return S.verify(phone, S.parse_digits(str(b.get("passcode") or "")))


def api_parse(b):
    text = str(b.get("text") or "").strip()
    if not text:
        raise Bad(400, {"error": "text required"})
    cmd = A.extract_intent(text)
    start = A.resolve_start(cmd.get("date", ""), cmd.get("time", ""))
    return {"command": cmd, "starts_at": start.isoformat() if start else None,
            "spoken": A.say_time(start) if start else None}


def api_appt_add(b):
    phone = _gate(b)
    text = str(b.get("text") or "").strip()
    if text:
        cmd = A.extract_intent(text)
        if cmd["intent"] != "add_appointment":
            raise Bad(422, {"error": "not an appointment", "command": cmd})
    else:
        cmd = {"title": b.get("title"), "date": b.get("date"), "time": b.get("time"),
               "remind_minutes_before": b.get("remind_minutes_before", -1), "notes": b.get("notes", "")}
    start = A.resolve_start(str(cmd.get("date") or ""), str(cmd.get("time") or ""))
    if start is None:
        raise Bad(422, {"error": "need a date and time", "command": cmd})
    row = A.add_appointment(phone, str(cmd.get("title") or "Appointment"), start,
                            remind_minutes_before=int(cmd.get("remind_minutes_before") or -1),
                            notes=str(cmd.get("notes") or ""), source_text=text,
                            is_test=voice.is_test_number(phone))
    return {"ok": True, "appointment": row, "spoken": A.say_time(start)}


def api_appt_list(b):
    phone = _gate(b)
    return {"appointments": A.upcoming_appointments(phone, int(b.get("limit") or 10))}


def api_appt_cancel(b):
    phone = _gate(b)
    return A.cancel_appointment(phone, str(b.get("target") or ""), str(b.get("target_date") or ""))


def api_todos(b, op):
    phone = _gate(b)
    if op == "add":
        return {"todo": A.add_todo(phone, str(b.get("title") or ""), voice.is_test_number(phone))}
    if op == "complete":
        return A.complete_todo(phone, str(b.get("target") or ""))
    return {"todos": A.open_todos(phone, 20)}


def api_projects(b, op):
    phone = _gate(b)
    if op == "add":
        return {"project": A.add_project(phone, str(b.get("name") or ""), str(b.get("next_step") or ""),
                                         voice.is_test_number(phone))}
    if op == "update":
        return A.update_project(phone, str(b.get("target") or ""), str(b.get("status") or ""),
                                str(b.get("next_step") or ""))
    return {"projects": A.unfinished_projects(phone, 20)}


def api_briefing(b):
    phone = _gate(b)
    name = A.caller_name(phone)
    return {"spoken": voice._ready_reply(phone, name)}


def api_chatlog(b):
    phone = _gate(b)
    return {"spoken": A.chat_log_summary(phone)}


def api_inbox(b):
    phone = _gate(b)
    return {"stub": True, "items": A.unread_inbox(phone, 20)}


ROUTES = {
    "/api/passcode/set": api_passcode_set,
    "/api/passcode/verify": api_passcode_verify,
    "/api/appointments/parse": api_parse,
    "/api/appointments/add": api_appt_add,
    "/api/appointments/list": api_appt_list,
    "/api/appointments/cancel": api_appt_cancel,
    "/api/todos/add": lambda b: api_todos(b, "add"),
    "/api/todos/list": lambda b: api_todos(b, "list"),
    "/api/todos/complete": lambda b: api_todos(b, "complete"),
    "/api/projects/add": lambda b: api_projects(b, "add"),
    "/api/projects/list": lambda b: api_projects(b, "list"),
    "/api/projects/update": lambda b: api_projects(b, "update"),
    "/api/briefing": api_briefing,
    "/api/chatlog": api_chatlog,
    "/api/inbox": api_inbox,
    "/api/reminders/run": lambda b: reminders.run_due(),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.log_date_time_string(), fmt % args), flush=True)

    def _raw(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", ""):
            _text(self, 200, "bot placeholder ok")
        elif path == "/health/llm":
            _json(self, 200, {"model": llm.model(), "keyConfigured": bool(llm.api_key())})
        elif path == "/health":
            ok_db = False
            try:
                ok_db = db.configured() and bool(db.one("SELECT 1 AS ok"))
            except Exception:  # noqa: BLE001
                ok_db = False
            _json(self, 200, {"db": ok_db, "llm": bool(llm.api_key()), "assistant": A.ASSISTANT_NAME,
                              "reminderDryRun": reminders.dry_run(),
                              "webhookSecret": bool(os.environ.get("AGENTPHONE_WEBHOOK_SECRET")),
                              "botKey": bool(os.environ.get("BOT_API_KEY"))})
        elif path == "/api/greeting":
            _json(self, 200, {"begin_message": voice.BEGIN_MESSAGE, "capabilities": A.CAPABILITIES_TEXT})
        else:
            _text(self, 404, "not found")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        raw = self._raw()
        try:
            body = json.loads(raw.decode() or "{}") if raw else {}
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
        except (ValueError, UnicodeDecodeError) as exc:
            _json(self, 400, {"error": f"invalid JSON: {exc}"})
            return

        if path == "/chat":
            msg = str(body.get("message") or "").strip()
            if not llm.api_key():
                _json(self, 503, {"error": "GEMINI_API_KEY not set"})
                return
            if not msg:
                _json(self, 400, {"error": "message is required"})
                return
            try:
                reply = llm.generate(llm.history_to_contents(body.get("history") or [], msg), SYSTEM_PROMPT)
            except Exception as exc:  # noqa: BLE001
                _json(self, 502, {"error": str(exc)})
                return
            _json(self, 200, {"reply": reply})
            return

        if path == "/voice":  # AgentPhone voice webhook (signed) or manual test (X-Bot-Key)
            if not (_agentphone_sig_ok(raw, self) or _bot_key_ok(self)):
                _json(self, 401, {"error": "unauthorized"})
                return
            ev = body.get("event")
            try:
                if ev == "agent.message" and body.get("channel") == "voice":
                    _json(self, 200, voice.handle_turn(body))
                elif ev == "agent.call_ended":
                    voice.handle_call_ended(body)
                    _json(self, 200, {"ok": True})
                else:
                    _json(self, 200, {"ok": True})
            except Exception as exc:  # noqa: BLE001
                print("voice error:", repr(exc), flush=True)
                _json(self, 200, {"text": "Sorry, I hit a snag. Could you say that again?"})
            return

        fn = ROUTES.get(path)
        if fn is None:
            _text(self, 404, "not found")
            return
        if not _bot_key_ok(self):
            _json(self, 401, {"error": "unauthorized"})
            return
        try:
            _json(self, 200, fn(body))
        except Bad as b:
            _json(self, b.status, b.payload)
        except Exception as exc:  # noqa: BLE001
            print("api error:", path, repr(exc), flush=True)
            _json(self, 500, {"error": type(exc).__name__})


def main():
    port = int(os.environ.get("PORT", "10000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
