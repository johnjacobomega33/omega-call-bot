"""Gemini helpers (stdlib HTTP)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_MODEL = "gemini-3.5-flash-lite"


def model() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY", "").strip() or None


def generate(contents: list[dict[str, Any]], system: str, *, temperature: float = 0.7,
             max_tokens: int = 256, json_schema: dict | None = None, timeout: int = 25) -> str:
    key = api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    gen: dict[str, Any] = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if json_schema is not None:
        gen["responseMimeType"] = "application/json"
        gen["responseSchema"] = json_schema
    payload = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
               "generationConfig": gen}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model()}:generateContent",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini HTTP {exc.code}: {detail[:300]}") from exc
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError("Gemini returned no candidates")
    parts = ((cands[0].get("content") or {}).get("parts")) or []
    text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict)).strip()
    if not text:
        raise RuntimeError("Gemini returned empty reply")
    return text


def history_to_contents(history: list[Any], message: str) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or item.get("direction") or "").lower()
        text = str(item.get("content") or item.get("text") or item.get("message") or "")
        if not text:
            continue
        gem_role = "model" if role in ("assistant", "model", "bot", "agent", "outbound") else "user"
        contents.append({"role": gem_role, "parts": [{"text": text}]})
    contents.append({"role": "user", "parts": [{"text": message}]})
    return contents
