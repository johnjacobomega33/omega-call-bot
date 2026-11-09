# omega-call-bot

Phone assistant ("Pixie") for the Omega test line. Python stdlib + Neon (HTTPS SQL) + Gemini.
Test mode only — not yet the live handler for the AgentPhone number.

## Call flow (`POST /voice`, AgentPhone voice webhook)
1. AgentPhone speaks the agent `beginMessage` (`GET /api/greeting` shows the text).
2. Passcode gate: first call sets a 6-digit code (said twice), later calls verify it.
   Spoken digits are parsed ("one two three…", "123 456", "double five", "twelve thirty four…").
   3 wrong tries → 5-minute lockout stored in `callers.locked_until`. Codes are stored only as
   salted scrypt hashes (+ server pepper `PASSCODE_PEPPER`); passcode turns are logged as `[passcode entered]`.
3. After verifying: briefing (calendar, emails/texts *stub*, to-dos, unfinished projects) → "So, how can I help you today?"
4. Gemini JSON-mode intent extraction: add/list/cancel appointments, to-dos, projects, chat log,
   messages, capabilities ("tell me what you can do"), lookup (Tavily), small talk.

## Endpoints
Public: `GET /`, `GET /health`, `GET /health/llm`, `GET /api/greeting`, `POST /chat`.
`POST /voice` — needs a valid AgentPhone signature (`AGENTPHONE_WEBHOOK_SECRET`) or `X-Bot-Key`.
`X-Bot-Key: $BOT_API_KEY` required for `POST /api/...`:
`passcode/set {phone,passcode,name?}`, `passcode/verify {phone,passcode}`, `appointments/parse {text}`,
and (each needs `phone`+`passcode`, counted toward lockout) `appointments/add {text | title,date,time}`,
`appointments/list`, `appointments/cancel {target,target_date?}`, `todos/add|list|complete`,
`projects/add|list|update`, `briefing`, `chatlog`, `inbox`; plus `reminders/run`.

## Reminders
`reminders.py` claims due rows atomically and places an AgentPhone outbound call
(`POST /v1/calls`). **Dry-run by default** (`REMINDER_DRY_RUN` must be exactly `false` to dial,
and the number must be in `REMINDER_ALLOWED_NUMBERS` or that list must be `*`; `+1555…` is never dialed).
Scheduled by a GitHub Actions workflow every 10 minutes (free on public repos); template in `deploy/github-actions-reminders.yml`, installed at `.github/workflows/reminders.yml` when present.

## Dev
`python migrate.py` (schema.sql) · `python -m unittest tests.test_unit` · live: `tests.test_live` with `DATABASE_URL`, `GEMINI_API_KEY`.
