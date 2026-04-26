"""Peloton workout fetcher via pylotoncycle (session-cookie auth).

One-time setup:
  1. Add PELOTON_EMAIL + PELOTON_PASSWORD to .env.
  2. `uv pip install pylotoncycle` (already in pyproject deps).

Peloton exposes `total_work` (kilojoules of mechanical work) per ride,
which is a direct, power-based load measurement — better than HR-derived
suffer_score where available. We feed both into the same Banister pipeline.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

KJ_TO_LOAD = 0.25
"""Scale kJ → suffer-score-equivalent so Strava + Peloton loads combine sanely.

A typical 45-min bootcamp yields ~300 kJ; at 0.25× that's 75 "load units",
roughly matching a comparable-effort Strava suffer_score. Tune in profile.yaml
later if the mix feels off."""


def _env(key: str) -> str:
    val = os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"{key} not set in .env")
    return val


def _client():
    try:
        import pylotoncycle
    except ImportError as e:
        raise RuntimeError("pylotoncycle not installed. `uv pip install pylotoncycle`.") from e
    return pylotoncycle.PylotonCycle(_env("PELOTON_EMAIL"), _env("PELOTON_PASSWORD"))


def list_recent_workouts(days: int = 120) -> list[dict]:
    """Return recent Peloton workouts with total_work + timing fields.

    Bypasses pylotoncycle's GetRecentWorkouts (which fails on instructor
    enrichment in v0.9.1) and hits /api/user/{id}/workouts directly via
    the authenticated session.
    """
    c = _client()
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    workouts = c.GetWorkoutList(num_workouts=500)
    return [
        w for w in workouts
        if (w.get("start_time") or w.get("created_at") or 0) >= cutoff
    ]


def list_load_points(days: int = 120) -> list:
    """Return LoadPoint rows sourced from Peloton total_work (kJ)."""
    from .training_load import LoadPoint

    workouts = list_recent_workouts(days=days)
    out = []
    for w in workouts:
        kj = w.get("total_work")
        start = w.get("start_time") or w.get("created_at")
        if not kj or not start:
            continue
        day = datetime.fromtimestamp(start, tz=timezone.utc).date()
        kj_value = float(kj) / 1000.0
        out.append(LoadPoint(day=day, load=kj_value * KJ_TO_LOAD, source="peloton"))
    return out
