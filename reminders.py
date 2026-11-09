"""Find due appointment reminders and place AgentPhone reminder calls.

DRY-RUN by default: REMINDER_DRY_RUN must be exactly "false" to place real calls, and
even then only numbers in REMINDER_ALLOWED_NUMBERS (comma list, or "*") are dialed.
Fictional +1555 numbers are never dialed.

CLI (used by the GitHub Actions schedule):  python reminders.py
Env: DATABASE_URL, REMINDER_DRY_RUN, REMINDER_ALLOWED_NUMBERS, AGENTPHONE_API_KEY,
     AGENTPHONE_AGENT_ID, AGENTPHONE_NUMBER_ID, ASSISTANT_NAME
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import assistant as A
import db

STALE_AFTER = timedelta(minutes=30)  # don't call about appointments that already started >30m ago
MAX_ATTEMPTS = 3


def dry_run() -> bool:
    return os.environ.get("REMINDER_DRY_RUN", "true").strip().lower() != "false"


def allowed(phone: str) -> bool:
    if phone.startswith("+1555"):
        return False
    allow = [p.strip() for p in os.environ.get("REMINDER_ALLOWED_NUMBERS", "").split(",") if p.strip()]
    return "*" in allow or phone in allow


def mask(phone: str) -> str:
    return phone[:5] + "***" + phone[-2:] if len(phone) > 7 else "***"


def reminder_text(title: str, starts_at: datetime, tz: str) -> str:
    name = A.ASSISTANT_NAME
    return (f"Hi, it's {name}, your personal assistant, with a quick reminder: "
            f"{title}, {A.say_time(starts_at, tz)}. Have a great day!")


def place_call(to: str, greeting: str) -> dict:
    body = {
        "agentId": os.environ.get("AGENTPHONE_AGENT_ID", "cmw8mtv1k9fddy5wjbbb7155w"),
        "toNumber": to,
        "initialGreeting": greeting,
        # hosted mini-prompt so the call ends politely without needing the webhook
        "systemPrompt": (f"You are {A.ASSISTANT_NAME}, a friendly assistant. You just read a reminder. "
                         "If asked, repeat it. Otherwise say goodbye and end the call. Keep it under 30 seconds."),
    }
    num = os.environ.get("AGENTPHONE_NUMBER_ID", "").strip()
    if num:
        body["fromNumberId"] = num
    req = urllib.request.Request(
        "https://api.agentphone.ai/v1/calls", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {os.environ['AGENTPHONE_API_KEY'].strip()}",
                 "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"ok": True, "status": resp.status, "body": json.loads(resp.read() or b"{}")}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "body": exc.read().decode("utf-8", "replace")[:300]}


def run_due(limit: int = 20, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    dry = dry_run()
    claimed = db.query(
        "WITH due AS (SELECT id FROM appointments WHERE NOT reminded AND cancelled_at IS NULL "
        "AND remind_at <= $1 ORDER BY remind_at LIMIT $2 FOR UPDATE SKIP LOCKED) "
        "UPDATE appointments a SET reminded=true, reminder_status='claimed', "
        "reminder_attempts=a.reminder_attempts+1, reminder_attempted_at=now() FROM due "
        "WHERE a.id=due.id RETURNING a.id, a.caller_phone, a.title, a.starts_at, a.timezone, a.reminder_attempts",
        [now.isoformat(), limit])
    results = []
    for r in claimed:
        start = A._ts(r["starts_at"])
        text = reminder_text(r["title"], start, r["timezone"])
        entry = {"id": r["id"], "to": r["caller_phone"], "text": text}
        if start < now - STALE_AFTER:
            status = "skipped:stale"
        elif dry:
            status = "dry_run"
            print(f"[DRY RUN] would call {mask(r['caller_phone'])} about appointment {r['id']}", flush=True)
        elif not allowed(r["caller_phone"]):
            status = "skipped:not_allowed"
        else:
            res = place_call(r["caller_phone"], text)
            entry["api_status"] = res["status"]
            if res["ok"]:
                status = "called"
                db.query("UPDATE appointments SET reminder_call_id=$2 WHERE id=$1",
                         [r["id"], str((res["body"] or {}).get("id") or "")])
            elif res["status"] in (409, 429) or res["status"] >= 500:
                status = f"failed:{res['status']}"
                if int(r["reminder_attempts"]) < MAX_ATTEMPTS:  # transient -> retry next run
                    db.query("UPDATE appointments SET reminded=false WHERE id=$1", [r["id"]])
            else:
                status = f"failed:{res['status']}"  # e.g. 402 funding: don't retry
        db.query("UPDATE appointments SET reminder_status=$2 WHERE id=$1", [r["id"], status])
        entry["status"] = status
        results.append(entry)
    return {"dry_run": dry, "checked_at": now.isoformat(), "processed": len(results), "results": results}


if __name__ == "__main__":
    out = run_due()
    # GitHub Actions logs on a public repo are public: print only ids/statuses, never text or numbers
    print(json.dumps({"dry_run": out["dry_run"], "processed": out["processed"],
                      "results": [{"id": e["id"], "status": e["status"]} for e in out["results"]]}))
