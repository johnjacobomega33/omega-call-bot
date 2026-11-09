"""Omega call-bot: Gemini-backed /chat placeholder (not wired to AgentPhone yet)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_MODEL = "gemini-3.5-flash-lite"
SYSTEM_PROMPT = (
    "You are a friendly, concise phone companion. "
    "Keep every reply to 1–3 short spoken sentences. "
    "No lists, no markdown, no stage directions."
)


def _model() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _api_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    return key or None


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _text_response(handler: BaseHTTPRequestHandler, status: int, text: str) -> None:
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    if not raw:
        return {}
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def _history_to_contents(history: list[Any], message: str) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        text = item.get("content")
        if text is None:
            text = item.get("text") or item.get("message") or ""
        text = str(text)
        if not text:
            continue
        # Gemini roles: user | model
        if role in ("assistant", "model", "bot"):
            gem_role = "model"
        else:
            gem_role = "user"
        contents.append({"role": gem_role, "parts": [{"text": text}]})
    contents.append({"role": "user", "parts": [{"text": message}]})
    return contents


def _call_gemini(message: str, history: list[Any]) -> str:
    key = _api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    model = _model()
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": _history_to_contents(history, message),
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 256,
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini HTTP {exc.code}: {detail[:400]}") from exc
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini returned no candidates")
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    texts = [str(p.get("text") or "") for p in parts if isinstance(p, dict)]
    reply = "".join(texts).strip()
    if not reply:
        raise RuntimeError("Gemini returned empty reply")
    return reply


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.log_date_time_string(), fmt % args), flush=True)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", ""):
            _text_response(self, 200, "bot placeholder ok")
            return
        if path == "/health/llm":
            _json_response(
                self,
                200,
                {"model": _model(), "keyConfigured": bool(_api_key())},
            )
            return
        _text_response(self, 404, "not found")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/chat":
            _text_response(self, 404, "not found")
            return
        if not _api_key():
            _json_response(self, 503, {"error": "GEMINI_API_KEY not set"})
            return
        try:
            body = _read_json(self)
        except (ValueError, json.JSONDecodeError) as exc:
            _json_response(self, 400, {"error": f"invalid JSON: {exc}"})
            return
        message = str(body.get("message") or "").strip()
        if not message:
            _json_response(self, 400, {"error": "message is required"})
            return
        history = body.get("history") or []
        if not isinstance(history, list):
            _json_response(self, 400, {"error": "history must be an array"})
            return
        try:
            reply = _call_gemini(message, history)
        except Exception as exc:  # noqa: BLE001 — surface to caller
            _json_response(self, 502, {"error": str(exc)})
            return
        _json_response(self, 200, {"reply": reply})


def main():
    port = int(os.environ.get("PORT", "10000"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
