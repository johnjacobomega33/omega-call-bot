# AgentPhone webhook + bot placeholder — deployed 2026-11-08 (America/Los_Angeles)

## Webhook receiver
- Public base URL: https://agentphone-webhook.onrender.com
- Webhook URL: https://agentphone-webhook.onrender.com/webhook
- Events dump: GET /events?key=<EVENTS_KEY>
- Health: GET / → `ok`
- Repo: https://github.com/johnjacobomega33/agentphone-webhook (public; made public so Render could clone without GitHub OAuth)
- Host: Render free web service `agentphone-webhook` (Oregon), workspace Jacob's workspace
- Dashboard: https://dashboard.render.com/web/srv-dboiq2ou01pc73fdbedg
- EVENTS_KEY stored at /workspace/agentphone/events_key.txt (chmod 600) — not printed here
- Env on Render: EVENTS_KEY set; AGENTPHONE_WEBHOOK_SECRET="" placeholder

## Bot placeholder
- Public URL: https://omega-call-bot.onrender.com
- GET / → `bot placeholder ok`
- Repo: https://github.com/johnjacobomega33/omega-call-bot (public)
- Host: Render free web service `omega-call-bot` (Oregon)
- Dashboard: https://dashboard.render.com/web/srv-dboiq3dg1s2s73fb3ns0

## Verified
- GET /webhook host → 200 ok
- POST /webhook → {"ok":true}; event appeared in GET /events
- GET bot / → 200 bot placeholder ok

## Caveats
- Free services spin down after ~15 min inactivity; first request after sleep can cold-start ~30–60s
- Events are in-memory only (last 100); lost on sleep/redeploy
- $0 — free plan only, no card added
- GitHub MCP lacked create-repo; used `gh` CLI (already authed as johnjacobomega33)

## Gemini brain (2026-11-08 America/Los_Angeles)

- Model default: `gemini-3.5-flash-lite` (env `GEMINI_MODEL`; free of charge on Standard per https://ai.google.dev/gemini-api/docs/pricing#gemini-3.5-flash-lite)
- Docs: https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite
- Free-tier RPM/RPD: per-model quotas are shown in AI Studio (https://aistudio.google.com/rate-limit / https://ai.google.dev/gemini-api/docs/rate-limits). Independently observed for Flash-Lite 3.5: ~15 RPM / 500 RPD (https://imisofts.com/blog/gemini-api-free-tier-limits/). RPD resets midnight Pacific.
- Free tier may use prompts to improve Google products ("Used to improve our products: Yes" on pricing).
- Endpoints: `GET /`, `GET /health/llm`, `POST /chat`
- `GEMINI_MODEL=gemini-3.5-flash-lite` set on Render via MCP `update_environment_variables`
- `GEMINI_API_KEY` intentionally unset until a free key is added
- To set the key later: run `/workspace/agentphone/set_gemini_key.sh` with `GEMINI_API_KEY` (and `RENDER_API_KEY`) in the env, **or** call Render MCP:

```
CallDynamicTool
  namespace: user-Render
  toolName: update_environment_variables
  arguments: {
    "serviceId": "srv-dboiq3dg1s2s73fb3ns0",
    "envVars": [{"key": "GEMINI_API_KEY", "value": "<the free-tier key>"}]
  }
```

That MCP call merges the var and triggers a redeploy. Do not paste the key into chat or commit it.
- Commit: https://github.com/johnjacobomega33/omega-call-bot/commit/dcbb8b8f039dcb65c06f36af1551a685baf7f81a
- Not wired to AgentPhone yet.


## Pixie: passcode + calendar + reminders (2026-11-08 ~7:50 PM PST)

- Commits: https://github.com/johnjacobomega33/omega-call-bot/commit/b57f9e569c5375571fca51de879e1bc60e9f5e8c (feature), .../commit/9fdeb010afe357ea981bbdb9bcd6c19393b0ff70 and .../commit/cbf92d53348c44baec3f76d5cfd402249c8d09a3 (wording, hang up after goodbye). Local clone: /workspace/agentphone/omega-call-bot
- Modules: server.py (routes), voice.py (call flow), security.py (digits/hash/lockout), assistant.py (Gemini intents + data), reminders.py, db.py (Neon HTTPS SQL, no driver), schema.sql + migrate.py, tests/
- New Render env on omega-call-bot: DATABASE_URL, BOT_API_KEY (secrets/bot_api_key.txt), PASSCODE_PEPPER (secrets/passcode_pepper.txt — changing it invalidates every passcode), AGENTPHONE_WEBHOOK_SECRET, TAVILY_API_KEY, ASSISTANT_NAME=Pixie, REMINDER_DRY_RUN=true, KEYPAD_ENABLED=false
- Endpoints: GET /health, GET /api/greeting, POST /voice (AgentPhone-signed or X-Bot-Key), POST /api/{passcode/set,passcode/verify,appointments/parse,appointments/add,appointments/list,appointments/cancel,todos/add|list|complete,projects/add|list|update,briefing,chatlog,inbox,reminders/run} (X-Bot-Key; personal ones also need phone+passcode and count toward lockout)
- Neon tables (bold-truth-49590606/neondb): callers, appointments, chat_logs, call_sessions, todos, projects, inbox_items (STUB: nothing syncs emails/texts yet). call_summaries reused for per-call summaries.
- Lockout: 3 wrong -> callers.locked_until = now()+5 min; success resets failed_attempts.
- AgentPhone agent cmw8mtv1k9fddy5wjbbb7155w: name "Pixie (Omega Test Bot)", voice 11labs-Marissa (was custom_voice_ea22ba5fdfaa18f39c274851c1), beginMessage = Pixie greeting (GET /api/greeting). Voice choice doesn't change price (webhook voice $0.13/min flat).
- NOT WIRED: the project webhook still points at agentphone-webhook.onrender.com/webhook (logger, returns no speech). To make the number talk to Pixie: POST /v1/agents/cmw8mtv1k9fddy5wjbbb7155w/webhook {"url":"https://omega-call-bot.onrender.com/voice"} and set AGENTPHONE_WEBHOOK_SECRET on Render to the secret that call returns. Caveat: Render free cold start (30-60 s) can exceed AgentPhone's 30 s voice timeout on the first turn.
- Keypad: AgentPhone docs only cover the agent SENDING DTMF ({"digits": ...} in responses); no inbound keypad event is documented, so passcodes are spoken. Code accepts data.digits/data.dtmf if it ever appears; set KEYPAD_ENABLED=true to mention keypad in the greeting.

### Reminder scheduler
- reminders.py claims due rows atomically (FOR UPDATE SKIP LOCKED) and calls POST https://api.agentphone.ai/v1/calls (initialGreeting = reminder text + short hosted systemPrompt so it says goodbye; hosted voice $0.22/min, ~30 s ≈ $0.11/call).
- Safety: REMINDER_DRY_RUN must be exactly "false" to dial; number must be in REMINDER_ALLOWED_NUMBERS (comma list or "*"); +1555 never dialed; stale (>30 min past start) skipped; 402 not retried; 409/429/5xx retried up to 3x.
- Schedule: GitHub Actions cron every 10 min (free on public repos; never wakes Render). Template: deploy/github-actions-reminders.yml. NOT INSTALLED: gh token lacks `workflow` scope and the GitHub MCP 403'd. Repo secrets DATABASE_URL + AGENTPHONE_API_KEY and variable REMINDER_DRY_RUN=true are already set. To install: `gh auth refresh -h github.com -s workflow` (browser device code), then copy the template to .github/workflows/reminders.yml and push — or paste it in GitHub web UI "Add file".
- Render cron jobs have no free plan, so not used. Manual trigger: POST /api/reminders/run with X-Bot-Key (wakes Render).
- Go live (spends credit): repo variables REMINDER_DRY_RUN=false and REMINDER_ALLOWED_NUMBERS=+1XXXXXXXXXX. AgentPhone docs say signup credit alone may not unlock outbound calling (402).

### Verified live (fake +15550000001/2, rows deleted afterward)
- 401 for unsigned /voice and keyless /api; signed /voice full first-call setup (set+confirm code, name), add/list/cancel appointment, to-do add/complete, project add; second call wrong->"Hmm, that didn't match. Let's try once more."->right->briefing; API 3 wrong -> locked 5 min (right code refused, list 423) -> after expiry right code ok; reminders/run dry_run; chat_logs store "[passcode entered]" only.
- tests: tests/test_unit.py (6 pass), tests/test_live.py (5 pass, Neon + Gemini); E2E script /workspace/agentphone/tests/live_e2e.py


## Pending (not deployed) — branch `pixie-groundwork` (2026-11-08 evening PT)

Groundwork only. **Not** on Render. **Not** on Neon production (`main` / `br-winter-flower-bfkaq2dl`). AgentPhone webhook **unchanged**.

### Git
- Feature branch: `pixie-groundwork` (push branch only; do not merge to `main` until Jacob approves — main auto-deploys).
- Neon dev branch: `pixie-groundwork` (`br-falling-firefly-bfo5lp06`), parent = production main.

### Added on the branch
- Schema: `units`, `callers.unit`, `callers.minutes_balance`, `minute_transactions`, `payments`, `contacts`, call_sessions billing timestamps
- Seed: `migrations/units_seed.json` + `migrations/seed_units.py` (Unit 1–28, MA1, MA2 from 2016 DSH list)
- Code: `billing.py`; admin APIs under `/api/admin/...`; voice intents `check_balance` / `minutes_remaining`; deduct minutes on `call_ended`
- Docs (outside repo path also): `/workspace/agentphone/docs/mailed-check-process.md`, `website-draft.md`

### Still needs Jacob tomorrow
1. Approve merge → Render deploy (or keep off main until ready)
2. Approve pointing AgentPhone webhook at `/voice` (explicitly blocked tonight)
3. Pick mailing address / payee / deposit workflow
4. Confirm website placeholders (phone, email, address)
5. Apply same migration to production Neon when ready (tonight: **dev branch only**)
