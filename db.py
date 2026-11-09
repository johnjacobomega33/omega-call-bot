"""Tiny Neon client over Neon's HTTPS SQL endpoint (stdlib only, no driver).

Uses DATABASE_URL (a normal postgres:// connection string). Each call is its
own implicit transaction; use single statements / CTEs for atomic changes.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse


class DBError(RuntimeError):
    pass


def _url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise DBError("DATABASE_URL not set")
    return url


def configured() -> bool:
    return bool(os.environ.get("DATABASE_URL", "").strip())


def query(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    url = _url()
    host = urlparse(url).hostname
    body = json.dumps({"query": sql, "params": list(params or [])}, default=str).encode("utf-8")
    req = urllib.request.Request(
        f"https://{host}/sql",
        data=body,
        headers={"Neon-Connection-String": url, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail).get("message", detail)
        except Exception:  # noqa: BLE001
            pass
        raise DBError(f"Neon HTTP {exc.code}: {str(detail)[:300]}") from exc
    return data.get("rows") or []


def one(sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None
