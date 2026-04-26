"""Training load from Whoop daily strain — single source of truth.

Whoop strain (0–21, exponential, HR-derived) covers everything the wrist
sees: lifts, walks, rides, background activity. Using day strain instead of
per-workout strain avoids double-counting and removes the need to dedupe
against Strava/Hevy.

Strain → suffer-equivalent: ×10 to land in the same numeric ballpark as the
Strava suffer_score the EWMA was originally tuned against (Whoop strain 14
≈ suffer 140 — both "hard hour"). Pure scale factor, doesn't change ratios.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .training_load import LoadPoint
from .whoop_client import get_cycle_records

STRAIN_TO_LOAD = 10.0


def list_load_points(days: int = 120) -> list[LoadPoint]:
    start = datetime.now(timezone.utc) - timedelta(days=days)
    records = get_cycle_records(start=start)
    out: list[LoadPoint] = []
    for r in records:
        score = (r.get("score") or {}).get("strain")
        st = r.get("start")
        if score is None or not st:
            continue
        day = datetime.fromisoformat(st.replace("Z", "+00:00")).date()
        out.append(LoadPoint(day=day, load=float(score) * STRAIN_TO_LOAD, source="whoop"))
    return out
