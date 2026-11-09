"""Intent extraction (Gemini JSON mode) + calendar / to-do / project / inbox features."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import db
import llm

DEFAULT_TZ = "America/Los_Angeles"
ASSISTANT_NAME = os.environ.get("ASSISTANT_NAME", "Pixie").strip() or "Pixie"


def lead_minutes() -> int:
    try:
        return int(os.environ.get("REMINDER_LEAD_MINUTES", "60"))
    except ValueError:
        return 60


INTENTS = [
    "add_appointment", "list_appointments", "cancel_appointment",
    "add_todo", "list_todos", "complete_todo",
    "add_project", "list_projects", "update_project",
    "read_chat_log", "read_messages", "capabilities", "briefing", "lookup",
    "check_balance", "minutes_remaining",
    "legal_info", "legal_capabilities", "legal_question_for_attorney", "send_email",
    "set_name", "goodbye", "chat",
]

INTENT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "intent": {"type": "STRING", "enum": INTENTS},
        "title": {"type": "STRING", "description": "appointment / to-do / project name, short"},
        "date": {"type": "STRING", "description": "YYYY-MM-DD resolved local date, or empty"},
        "time": {"type": "STRING", "description": "HH:MM 24-hour local time, or empty"},
        "remind_minutes_before": {"type": "INTEGER", "description": "-1 if not stated"},
        "notes": {"type": "STRING"},
        "target": {"type": "STRING", "description": "words identifying the existing item to cancel/complete/update"},
        "target_date": {"type": "STRING", "description": "YYYY-MM-DD of item to cancel, or empty"},
        "project_status": {"type": "STRING", "enum": ["none", "idea", "in_progress", "paused", "done", "dropped"]},
        "next_step": {"type": "STRING"},
        "name": {"type": "STRING", "description": "caller's preferred name for set_name"},
    },
    "required": ["intent", "title", "date", "time", "remind_minutes_before", "notes", "target",
                 "target_date", "project_status", "next_step", "name"],
}

CAPABILITIES_TEXT = (
    "I can save appointments and call you with reminders, read you your schedule, keep your "
    "to-do list, go over your messages, help you finish projects, look things up, tell you how "
    "many prepaid minutes you have left, keep a private log of our chats, and, through Legal Beagle, "
    "give general legal information about commitment cases and court forms. That's information only, "
    "not legal advice. Talk time is "
    "ten dollars an hour, prepaid. What sounds good?"
)


def now_local(tz: str = DEFAULT_TZ) -> datetime:
    return datetime.now(ZoneInfo(tz))


def extract_intent(text: str, tz: str = DEFAULT_TZ, now: datetime | None = None,
                   history: list[Any] | None = None) -> dict[str, Any]:
    now = (now or now_local(tz)).astimezone(ZoneInfo(tz))
    system = (
        "You turn one caller utterance from a phone call into a JSON command for a personal "
        f"assistant. Current local date/time: {now.strftime('%A %Y-%m-%d %H:%M')} ({tz}). "
        "Resolve relative dates ('Tuesday', 'tomorrow', 'next Friday') to the NEXT future "
        "occurrence and output YYYY-MM-DD; times as 24-hour HH:MM ('3pm' -> 15:00, 'noon' -> 12:00). "
        "If no time was said, leave time empty. remind_minutes_before is -1 unless they say "
        "e.g. 'remind me 2 hours before' (=120). Titles are short ('Dentist'). "
        "Intents: add_appointment, list_appointments ('what's on my calendar'), cancel_appointment, "
        "add_todo, list_todos, complete_todo ('I finished X', 'check off X'), add_project, "
        "list_projects, update_project (status change or next step), read_chat_log (past "
        "conversations / chat history), read_messages (emails or texts), capabilities ('what can "
        "you do', 'help'), briefing ('catch me up', 'what's new'), check_balance / "
        "minutes_remaining ('how many minutes do I have', 'what's my balance', 'am I out of time'), "
        "set_name ('call me X'), send_email ('email Omega', 'send an email to Jacob'; put the "
        "recipient name in target), "
        "legal_info (any question about the law, courts, lawyers, SVP/commitment cases, hearings, trials, "
        "petitions, habeas, CONREP, release, fee waivers, patients' rights, mail rules, State Bar, or "
        "'should I file...'), legal_capabilities ('what can Legal Beagle do'), "
        "legal_question_for_attorney ('save a question for my lawyer', 'add that to my questions for my "
        "attorney'; put the question itself in title), "
        "lookup (look something up / current facts / web search; put the search query in title), goodbye, chat (anything else). Use empty strings for unused fields (project_status 'none')."
    )
    contents = llm.history_to_contents((history or [])[-4:], text)
    raw = llm.generate(contents, system, temperature=0.0, max_tokens=300, json_schema=INTENT_SCHEMA)
    data = json.loads(raw)
    if data.get("project_status") == "none":
        data["project_status"] = ""
    if data.get("intent") not in INTENTS:
        data["intent"] = "chat"
    return data


def resolve_start(date_s: str, time_s: str, tz: str = DEFAULT_TZ) -> datetime | None:
    if not date_s or not time_s:
        return None
    try:
        d = datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return d.replace(tzinfo=ZoneInfo(tz))


# ----------------------------------------------------------------- speech formatting
def say_time(dt: datetime, tz: str = DEFAULT_TZ, now: datetime | None = None) -> str:
    loc = dt.astimezone(ZoneInfo(tz))
    now = (now or now_local(tz)).astimezone(ZoneInfo(tz))
    hour = loc.strftime("%I").lstrip("0")
    t = f"{hour}{':' + loc.strftime('%M') if loc.minute else ''} {loc.strftime('%p')}"
    if loc.date() == now.date():
        day = "today"
    elif loc.date() == (now + timedelta(days=1)).date():
        day = "tomorrow"
    elif 0 < (loc.date() - now.date()).days < 7:
        day = loc.strftime("%A")
    else:
        day = f"{loc.strftime('%A, %B')} {loc.day}"
    return f"{day} at {t}"


def _ts(v) -> datetime:
    s = str(v).replace(" ", "T", 1)
    if re.search(r"[+-]\d\d$", s):
        s += ":00"
    return datetime.fromisoformat(s)


# ----------------------------------------------------------------- callers
def caller_name(phone: str) -> str | None:
    row = db.one("SELECT name FROM callers WHERE phone=$1", [phone])
    return (row or {}).get("name") or None


def set_name(phone: str, name: str) -> None:
    db.query("UPDATE callers SET name=$2, updated_at=now() WHERE phone=$1", [phone, name.strip()[:60]])


# ----------------------------------------------------------------- appointments
def add_appointment(phone: str, title: str, starts_at: datetime, *, tz: str = DEFAULT_TZ,
                    remind_minutes_before: int | None = None, notes: str = "",
                    source_text: str = "", is_test: bool = False) -> dict:
    lead = lead_minutes() if remind_minutes_before is None or remind_minutes_before < 0 else remind_minutes_before
    remind_at = starts_at - timedelta(minutes=lead)
    return db.one(
        "INSERT INTO appointments (caller_phone,title,starts_at,timezone,remind_at,notes,source_text,is_test) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id,title,starts_at,remind_at,timezone",
        [phone, title.strip()[:200] or "Appointment", starts_at.isoformat(), tz,
         remind_at.isoformat(), notes or None, source_text or None, is_test])


def upcoming_appointments(phone: str, limit: int = 5) -> list[dict]:
    return db.query(
        "SELECT id,title,starts_at,timezone,remind_at,reminded,notes FROM appointments "
        "WHERE caller_phone=$1 AND cancelled_at IS NULL AND starts_at >= now() - interval '1 hour' "
        "ORDER BY starts_at LIMIT $2", [phone, limit])


def _match(items: list[dict], key: str, target: str) -> list[dict]:
    words = [w for w in re.findall(r"[a-z0-9]+", (target or "").lower()) if len(w) > 2]
    if not words:
        return items if len(items) == 1 else []
    return [it for it in items if any(w in str(it.get(key, "")).lower() for w in words)]


def cancel_appointment(phone: str, target: str, target_date: str = "") -> dict:
    items = upcoming_appointments(phone, 50)
    if target_date:
        tz_items = [it for it in items
                    if _ts(it["starts_at"]).astimezone(ZoneInfo(it["timezone"])).strftime("%Y-%m-%d") == target_date]
        if tz_items:
            items = tz_items
    hits = _match(items, "title", target) if target else (items if len(items) == 1 else [])
    if len(hits) != 1:
        return {"cancelled": None, "candidates": hits or items[:3]}
    row = db.one("UPDATE appointments SET cancelled_at=now() WHERE id=$1 AND caller_phone=$2 "
                 "RETURNING id,title,starts_at,timezone", [hits[0]["id"], phone])
    return {"cancelled": row, "candidates": []}


# ----------------------------------------------------------------- todos
def add_todo(phone: str, title: str, is_test: bool = False) -> dict:
    return db.one("INSERT INTO todos (caller_phone,title,is_test) VALUES ($1,$2,$3) RETURNING id,title",
                  [phone, title.strip()[:200], is_test])


def open_todos(phone: str, limit: int = 10) -> list[dict]:
    return db.query("SELECT id,title,due_at FROM todos WHERE caller_phone=$1 AND NOT done "
                    "ORDER BY created_at LIMIT $2", [phone, limit])


def complete_todo(phone: str, target: str) -> dict:
    hits = _match(open_todos(phone, 50), "title", target)
    if len(hits) != 1:
        return {"done": None, "candidates": hits}
    row = db.one("UPDATE todos SET done=true, done_at=now() WHERE id=$1 RETURNING id,title", [hits[0]["id"]])
    return {"done": row, "candidates": []}


# ----------------------------------------------------------------- projects
def add_project(phone: str, name: str, next_step: str = "", is_test: bool = False) -> dict:
    return db.one("INSERT INTO projects (caller_phone,name,next_step,is_test) VALUES ($1,$2,$3,$4) "
                  "RETURNING id,name,status", [phone, name.strip()[:200], next_step or None, is_test])


def unfinished_projects(phone: str, limit: int = 5) -> list[dict]:
    return db.query("SELECT id,name,status,next_step FROM projects WHERE caller_phone=$1 "
                    "AND status IN ('idea','in_progress','paused') ORDER BY updated_at DESC LIMIT $2",
                    [phone, limit])


def update_project(phone: str, target: str, status: str = "", next_step: str = "") -> dict:
    hits = _match(unfinished_projects(phone, 50), "name", target)
    if len(hits) != 1:
        return {"updated": None, "candidates": hits}
    row = db.one("UPDATE projects SET status=COALESCE(NULLIF($2,''),status), "
                 "next_step=COALESCE(NULLIF($3,''),next_step), updated_at=now() WHERE id=$1 "
                 "RETURNING id,name,status,next_step", [hits[0]["id"], status, next_step])
    return {"updated": row, "candidates": []}


# ----------------------------------------------------------------- inbox (STUB source)
def unread_inbox(phone: str, limit: int = 5) -> list[dict]:
    """Emails/texts for the briefing. STUB: nothing syncs into inbox_items yet."""
    return db.query("SELECT id,kind,sender,subject,snippet,received_at FROM inbox_items "
                    "WHERE caller_phone=$1 AND NOT is_read ORDER BY received_at DESC LIMIT $2",
                    [phone, limit])


# ----------------------------------------------------------------- chat log
def log_turn(phone: str, call_id: str | None, role: str, content: str, is_test: bool = False) -> None:
    db.query("INSERT INTO chat_logs (caller_phone,call_id,role,content,is_test) VALUES ($1,$2,$3,$4,$5)",
             [phone, call_id, role, content[:4000], is_test])


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def chat_log_summary(phone: str) -> str:
    sums = db.query("SELECT created_at, summary FROM call_summaries WHERE caller_number=$1 "
                    "ORDER BY created_at DESC LIMIT 3", [phone])
    if sums:
        parts = [f"{_cap(say_time(_ts(s['created_at'])))}: {s['summary']}" for s in sums]
        return "Here are your last conversations. " + " ".join(parts)
    turns = db.query("SELECT role, content, created_at FROM chat_logs WHERE caller_phone=$1 "
                     "AND role='user' AND content <> '[passcode entered]' ORDER BY created_at DESC LIMIT 5",
                     [phone])
    if not turns:
        return "I don't have any saved conversations with you yet."
    said = "; ".join(t["content"] for t in reversed(turns))
    return f"Recently you told me: {said}."


# ----------------------------------------------------------------- briefing
def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def briefing(phone: str, tz: str = DEFAULT_TZ) -> str:
    """Short spoken briefing. Only call AFTER passcode verification."""
    bits: list[str] = []
    appts = upcoming_appointments(phone, 3)
    if appts:
        first = appts[0]
        s = f"On your calendar: {first['title']}, {say_time(_ts(first['starts_at']), first['timezone'])}"
        if len(appts) > 1:
            s += f", plus {_plural(len(appts) - 1, 'more')} coming up"
        bits.append(s + ".")
    else:
        bits.append("Your calendar is clear.")
    inbox = unread_inbox(phone, 10)
    emails = sum(1 for i in inbox if i["kind"] == "email")
    texts = sum(1 for i in inbox if i["kind"] == "text")
    if emails or texts:
        parts = [p for p in (_plural(emails, "new email") if emails else "",
                             _plural(texts, "new text") if texts else "") if p]
        bits.append("You have " + " and ".join(parts) + ".")
    todos = open_todos(phone, 10)
    if todos:
        bits.append(f"You have {_plural(len(todos), 'thing')} on your to-do list, starting with {todos[0]['title']}.")
    projects = unfinished_projects(phone, 3)
    if projects:
        names = " and ".join(p["name"] for p in projects[:2])
        bits.append(f"You've still got {names} in progress. Want to pick that back up?")
    return " ".join(bits)


# ----------------------------------------------------------------- lookup (Tavily free tier)
def lookup(query: str) -> str:
    import urllib.request
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        return "Looking things up isn't switched on yet."
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=json.dumps({"query": query, "max_results": 3, "include_answer": True}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    ans = (data.get("answer") or "").strip()
    if not ans:
        snippets = " ".join((r.get("content") or "")[:300] for r in (data.get("results") or [])[:3])
        if not snippets:
            return "I couldn't find anything on that."
        ans = llm.generate([{"role": "user", "parts": [{"text": f"Question: {query}\nSources: {snippets}"}]}],
                           "Answer in 1-2 short spoken sentences using only the sources.",
                           temperature=0.2, max_tokens=120)
    return ans
