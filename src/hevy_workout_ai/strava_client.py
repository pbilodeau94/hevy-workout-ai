"""Strava API v3 client with OAuth2 (auth code + refresh token).

One-time setup:
  1. Create an app at https://www.strava.com/settings/api → client_id + secret.
     Set "Authorization Callback Domain" to: localhost
  2. Set STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET in .env.
     STRAVA_REDIRECT_URI defaults to http://localhost:8765/callback.
  3. Run `hevy strava-auth` once. Tokens persist in .strava_tokens.json.

Peloton classes auto-sync to Strava as VirtualRide if you've linked them.
"""

from __future__ import annotations

import http.server
import os
import socketserver
import threading
import time as _time
import urllib.parse
import webbrowser
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from . import store

load_dotenv()

AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_BASE = "https://www.strava.com/api/v3"
SCOPES = "read,activity:read_all"


def _env(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"{key} not set in .env")
    return val


def _save_tokens(data: dict) -> None:
    store.set_tokens("strava", data)


def _load_tokens() -> dict:
    tokens = store.get_tokens("strava")
    if not tokens:
        raise RuntimeError("No Strava tokens. Run `hevy strava-auth` first.")
    return tokens


def _is_expired(tokens: dict) -> bool:
    return _time.time() >= tokens.get("expires_at", 0) - 60


def _refresh(tokens: dict) -> dict:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": _env("STRAVA_CLIENT_ID"),
            "client_secret": _env("STRAVA_CLIENT_SECRET"),
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


def _get(path: str, params: dict | None = None) -> list | dict:
    resp = httpx.get(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {_access_token()}"},
        params=params,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def authorize() -> None:
    """One-time OAuth flow. Opens browser, captures code, saves tokens."""
    client_id = _env("STRAVA_CLIENT_ID")
    redirect_uri = os.environ.get("STRAVA_REDIRECT_URI", "http://localhost:8765/callback")
    parsed = urllib.parse.urlparse(redirect_uri)
    port = parsed.port or 8765

    state = os.urandom(8).hex()
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPES,
        "state": state,
    })

    captured: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.urlparse(self.path).query
            captured.update(dict(urllib.parse.parse_qsl(qs)))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Strava auth complete. You can close this tab.</h1>")

        def log_message(self, *a, **kw):
            pass

    server = socketserver.TCPServer(("localhost", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f"Opening browser for Strava auth: {url}")
    webbrowser.open(url)

    while "code" not in captured:
        pass
    server.shutdown()

    if captured.get("state") != state:
        raise RuntimeError("OAuth state mismatch")

    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": _env("STRAVA_CLIENT_SECRET"),
            "code": captured["code"],
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    _save_tokens(resp.json())
    print("Strava tokens saved.")


CARDIO_TYPES = {"Ride", "VirtualRide", "Run", "VirtualRun", "Walk", "Hike", "Swim", "Rowing"}


def list_recent_activities(days: int = 7) -> list[dict]:
    """Return cardio activities (excludes WeightTraining) from the last `days` days."""
    after = int(_time.time() - days * 86400)
    data = _get("/athlete/activities", params={"after": after, "per_page": 50})
    if not isinstance(data, list):
        return []
    return [a for a in data if a.get("type") in CARDIO_TYPES]


def get_activity(activity_id: int | str) -> dict:
    """Full activity detail — includes calories, which the list endpoint omits."""
    data = _get(f"/activities/{activity_id}")
    return data if isinstance(data, dict) else {}


def list_load_points(days: int = 120) -> list:
    """Return LoadPoint rows (date, suffer_score) for Banister training-load math.

    Activities without `suffer_score` (no HR recorded) are skipped. Strava's
    suffer_score is HR-zone-weighted and maps onto TSS-like scales.

    Lifting (WeightTraining) is excluded — that's sourced from Hevy tonnage via
    hevy_load.list_load_points to avoid double-counting and to capture lifts
    that didn't have a paired Watch workout.
    """
    from .training_load import LoadPoint

    after = int(_time.time() - days * 86400)
    data = _get("/athlete/activities", params={"after": after, "per_page": 200})
    if not isinstance(data, list):
        return []
    out = []
    for a in data:
        if a.get("type") == "WeightTraining":
            continue
        score = a.get("suffer_score")
        start = a.get("start_date_local") or a.get("start_date")
        if score is None or not start:
            continue
        day = datetime.fromisoformat(start.replace("Z", "+00:00")).date()
        out.append(LoadPoint(day=day, load=float(score), source="strava"))
    return out


def list_recent_activities_with_calories(days: int = 7) -> list[dict]:
    """List cardio activities, then fetch detail for each to populate calories."""
    acts = list_recent_activities(days=days)
    enriched = []
    for a in acts:
        if a.get("calories") is None:
            try:
                detail = get_activity(a["id"])
                a = {**a, "calories": detail.get("calories")}
            except Exception:
                pass
        enriched.append(a)
    return enriched
