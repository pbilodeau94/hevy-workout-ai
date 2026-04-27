"""Per-user persistent state.

Two backends:

- **local** — reads/writes files in `config/` exactly as before. Active when
  `SUPABASE_URL` is unset. This is Phil's Mac dev path.
- **supabase** — reads/writes to the Postgres tables in `db/schema.sql` via the
  PostgREST API. Active when `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` are set.
  Keyed by `USER_ID` env (default ``"default"``).

Call sites should use this module instead of touching files directly. The local
backend is byte-compatible with the prior layout so launchd jobs keep working.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import yaml

from .config import CONFIG_DIR

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Map logical keys to (path, format). Used by the local backend.
_LOCAL_KV: dict[str, tuple[Path, str]] = {
    "state":          (CONFIG_DIR / "state.yaml",          "yaml"),
    "profile":        (CONFIG_DIR / "profile.yaml",        "yaml"),
    "whoop_log":      (CONFIG_DIR / "whoop_log.yaml",      "yaml"),
    "nutrition_log":  (CONFIG_DIR / "nutrition_log.yaml",  "yaml"),
    "mf_cache":       (CONFIG_DIR / "mf_cache.yaml",       "yaml"),
    "doms_log":       (CONFIG_DIR / "doms_log.yaml",       "yaml"),
    "recovery":       (CONFIG_DIR / "recovery.yaml",       "yaml"),
    "coach_session":  (CONFIG_DIR / "coach_session.json",  "json"),
}
_LOCAL_TOKENS: dict[str, Path] = {
    "whoop":         REPO_ROOT / ".whoop_tokens.json",
    "strava":        REPO_ROOT / ".strava_tokens.json",
    "macrofactor":   CONFIG_DIR / ".mf_refresh_token",  # raw string, not JSON
}
_COACH_LOG_FILE = CONFIG_DIR / "coach_log.jsonl"


def _user_id() -> str:
    return os.environ.get("USER_ID", "default")


def _supa_enabled() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"))


def _supa_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1" + path
    key = os.environ["SUPABASE_SERVICE_KEY"]
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        **kwargs.pop("headers", {}),
    }
    r = httpx.request(method, url, headers=headers, timeout=15.0, **kwargs)
    if r.status_code >= 400:
        raise RuntimeError(
            f"Supabase {method} {path} -> {r.status_code}: {r.text[:500]}"
        )
    return r


# ---------------------------------------------------------------------------
# KV blobs (small dicts/lists stored whole)
# ---------------------------------------------------------------------------

def get(key: str) -> Any | None:
    """Return the value for ``key`` or ``None`` if missing."""
    if _supa_enabled():
        r = _supa_request(
            "GET",
            f"/user_kv?user_id=eq.{_user_id()}&key=eq.{key}&select=value",
        )
        rows = r.json()
        return rows[0]["value"] if rows else None

    path, fmt = _LOCAL_KV[key]
    if not path.exists():
        return None
    text = path.read_text()
    if not text.strip():
        return None
    return yaml.safe_load(text) if fmt == "yaml" else json.loads(text)


def set(key: str, value: Any) -> None:  # noqa: A001 — KV-store API
    """Upsert ``key`` to ``value``."""
    if _supa_enabled():
        _supa_request(
            "POST",
            "/user_kv",
            headers={"Prefer": "resolution=merge-duplicates"},
            json={"user_id": _user_id(), "key": key, "value": value},
        )
        return

    path, fmt = _LOCAL_KV[key]
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "yaml":
        path.write_text(yaml.dump(value, default_flow_style=False, sort_keys=False))
    else:
        path.write_text(json.dumps(value, indent=2))


# ---------------------------------------------------------------------------
# Auth tokens (per provider)
# ---------------------------------------------------------------------------

def get_tokens(provider: str) -> dict | str | None:
    """Return the stored token bundle for ``provider``.

    MacroFactor is special: locally it's a single string in a 0600 file. In
    Supabase it's wrapped as ``{"refresh_token": "..."}`` — callers should pass
    that shape explicitly when using the Supabase backend.
    """
    if _supa_enabled():
        r = _supa_request(
            "GET",
            f"/auth_tokens?user_id=eq.{_user_id()}&provider=eq.{provider}&select=tokens",
        )
        rows = r.json()
        return rows[0]["tokens"] if rows else None

    path = _LOCAL_TOKENS[provider]
    if not path.exists():
        return None
    text = path.read_text().strip()
    if provider == "macrofactor":
        return text or None
    return json.loads(text) if text else None


def set_tokens(provider: str, tokens: dict | str) -> None:
    if _supa_enabled():
        payload = tokens if isinstance(tokens, dict) else {"refresh_token": tokens}
        _supa_request(
            "POST",
            "/auth_tokens",
            headers={"Prefer": "resolution=merge-duplicates"},
            json={"user_id": _user_id(), "provider": provider, "tokens": payload},
        )
        return

    path = _LOCAL_TOKENS[provider]
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(tokens, str):
        path.write_text(tokens)
    else:
        path.write_text(json.dumps(tokens, indent=2))
    if provider == "macrofactor":
        path.chmod(0o600)


# ---------------------------------------------------------------------------
# Coach log (append-only)
# ---------------------------------------------------------------------------

def append_coach_log(payload: dict) -> None:
    if _supa_enabled():
        _supa_request(
            "POST",
            "/coach_log",
            json={"user_id": _user_id(), "payload": payload},
        )
        return
    _COACH_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _COACH_LOG_FILE.open("a") as f:
        f.write(json.dumps(payload) + "\n")


def read_coach_log(limit: int = 100) -> list[dict]:
    if _supa_enabled():
        r = _supa_request(
            "GET",
            f"/coach_log?user_id=eq.{_user_id()}&order=ts.desc&limit={limit}&select=ts,payload",
        )
        return r.json()
    if not _COACH_LOG_FILE.exists():
        return []
    lines = _COACH_LOG_FILE.read_text().splitlines()
    return [json.loads(line) for line in lines[-limit:] if line.strip()]
