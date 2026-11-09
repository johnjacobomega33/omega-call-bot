# omega-call-bot

Gemini-backed call-bot placeholder on Render free tier. Not wired to AgentPhone yet.

## Endpoints

- `GET /` — health (`bot placeholder ok`)
- `GET /health/llm` — `{"model": "...", "keyConfigured": bool}`
- `POST /chat` — body `{"message": "...", "history": [{"role":"user"|"assistant","content":"..."}, ...]}` → `{"reply":"..."}`. Returns `503 {"error":"GEMINI_API_KEY not set"}` when the key is missing.

## Env

- `GEMINI_API_KEY` — required for `/chat` to call Gemini (leave unset until you add a free-tier key)
- `GEMINI_MODEL` — defaults to `gemini-3.5-flash-lite`
- `PORT` — Render injects this

## Notes

- Free-tier Gemini may use prompts to improve Google's products (see Gemini API terms / pricing "Used to improve our products").
- Stdlib only — no third-party packages.
