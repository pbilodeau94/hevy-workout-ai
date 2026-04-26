"""Whoop API v2 client with OAuth2 (authorization code + refresh token).

One-time setup:
  1. Register an app at https://developer.whoop.com → get client_id + secret.
  2. Set WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET, WHOOP_REDIRECT_URI in .env.
     For a local CLI, redirect_uri can be http://localhost:8765/callback.
  3. Run `hevy-ai whoop-auth` once. It opens a browser, captures the code,
     exchanges it for tokens, and writes them to .whoop_tokens.json.

After that, `get_recovery()` auto-refreshes and returns today's recovery score.
"""

from __future__ import annotations

import http.server
import os
import socketserver
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

from . import store

load_dotenv()

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API_BASE = "https://api.prod.whoop.com/developer/v2"
SCOPES = "read:recovery read:cycles read:sleep read:workout offline"


def _env(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"{key} not set in .env")
    return val


def _save_tokens(data: dict) -> None:
    data["fetched_at"] = datetime.now(timezone.utc).isoformat()
    store.set_tokens("whoop", data)


def _load_tokens() -> dict:
    tokens = store.get_tokens("whoop")
    if not tokens:
        raise RuntimeError("No Whoop tokens. Run `hevy-ai whoop-auth` first.")
    return tokens


def _is_expired(tokens: dict) -> bool:
    fetched = datetime.fromisoformat(tokens["fetched_at"])
    expires_in = tokens.get("expires_in", 3600)
    return datetime.now(timezone.utc) >= fetched + timedelta(seconds=expires_in - 60)


def _refresh(tokens: dict) -> dict:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": _env("WHOOP_CLIENT_ID"),
            "client_secret": _env("WHOOP_CLIENT_SECRET"),
            "scope": SCOPES,
        },
        timeout=15,
    )
    resp.raise_for_status()
    new_tokens = resp.json()
    _save_tokens(new_tokens)
    return new_tokens


def _access_token() -> str:
    tokens = _load_tokens()
    if _is_expired(tokens):
        tokens = _refresh(tokens)
    return tokens["access_token"]


def _get(path: str, params: dict | None = None) -> dict:
    resp = httpx.get(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {_access_token()}"},
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def authorize() -> None:
    """Run one-time OAuth flow. Opens browser, captures code, saves tokens."""
    client_id = _env("WHOOP_CLIENT_ID")
    redirect_uri = os.environ.get("WHOOP_REDIRECT_URI", "http://localhost:8765/callback")
    parsed = urllib.parse.urlparse(redirect_uri)
    port = parsed.port or 8765

    state = os.urandom(8).hex()
    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

    captured = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.urlparse(self.path).query
            params = dict(urllib.parse.parse_qsl(qs))
            captured.update(params)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Whoop auth complete. You can close this tab.</h1>")

        def log_message(self, *a, **kw):
            pass

    server = socketserver.TCPServer(("localhost", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    print(f"Opening browser for Whoop auth: {url}")
    webbrowser.open(url)

    while "code" not in captured:
        pass
    server.shutdown()

    if captured.get("state") != state:
        raise RuntimeError("OAuth state mismatch")

    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": captured["code"],
            "client_id": client_id,
            "client_secret": _env("WHOOP_CLIENT_SECRET"),
            "redirect_uri": redirect_uri,
        },
        timeout=15,
    )
    resp.raise_for_status()
    _save_tokens(resp.json())
    print("Whoop tokens saved.")


def get_recovery_records(limit: int = 25) -> list[dict]:
    """Return up to `limit` recent recovery records (newest first)."""
    data = _get("/recovery", params={"limit": limit})
    return data.get("records", []) or []


def get_sleep_records(limit: int = 25) -> list[dict]:
    """Return up to `limit` recent sleep records (newest first)."""
    data = _get("/activity/sleep", params={"limit": limit})
    return data.get("records", []) or []


def get_latest_recovery() -> dict | None:
    """Return the most recent recovery record, or None.

    Shape (Whoop v2):
      {
        "cycle_id": ..., "sleep_id": ..., "user_id": ...,
        "created_at": ..., "updated_at": ...,
        "score_state": "SCORED",
        "score": {
          "user_calibrating": false,
          "recovery_score": 67.0,        # 0-100
          "resting_heart_rate": 52.0,
          "hrv_rmssd_milli": 78.3,
          "spo2_percentage": 97.2,
          "skin_temp_celsius": 33.4
        }
      }
    """
    data = _get("/recovery", params={"limit": 1})
    records = data.get("records", [])
    return records[0] if records else None


def get_workout_records(start: datetime | None = None, max_pages: int = 40) -> list[dict]:
    """Return Whoop per-workout records (newest first), paginated.

    Each record exposes `score.strain` (0–21) for the workout window plus
    `start`/`end` ISO timestamps — used to match lifts logged in Hevy that
    have no HR data of their own.
    """
    out: list[dict] = []
    params: dict = {"limit": 25}
    if start is not None:
        params["start"] = start.isoformat().replace("+00:00", "Z")
    for _ in range(max_pages):
        data = _get("/activity/workout", params=params)
        records = data.get("records", []) or []
        out.extend(records)
        token = data.get("next_token")
        if not token or not records:
            break
        params = {"limit": 25, "nextToken": token}
        if start is not None:
            params["start"] = start.isoformat().replace("+00:00", "Z")
    return out


def get_cycle_records(start: datetime | None = None, max_pages: int = 20) -> list[dict]:
    """Return Whoop daily cycles (newest first), paginated via next_token.

    Each cycle covers one physiological day and exposes `score.strain` (0–21),
    Whoop's HR-derived total daily load — captures lifts, walks, and rides
    via continuous wrist HR without needing per-activity logging.
    """
    out: list[dict] = []
    params: dict = {"limit": 25}
    if start is not None:
        params["start"] = start.isoformat().replace("+00:00", "Z")
    for _ in range(max_pages):
        data = _get("/cycle", params=params)
        records = data.get("records", []) or []
        out.extend(records)
        token = data.get("next_token")
        if not token or not records:
            break
        params = {"limit": 25, "nextToken": token}
        if start is not None:
            params["start"] = start.isoformat().replace("+00:00", "Z")
    return out
