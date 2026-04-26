"""Lifting load — Whoop HR-derived (matches Strava suffer scale), tonnage fallback.

Hevy doesn't expose HR. For each lift, find the Whoop workout that overlaps
its time window and use Whoop strain × 10 as the load — same units as Strava
suffer_score. If no Whoop record overlaps (lift logged outside watch wear),
fall back to tonnage / 500 + bodyweight per-set credit.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .hevy_client import list_workouts
from .training_load import LoadPoint
from .whoop_client import get_workout_records

STRAIN_TO_LOAD = 10.0
TONNAGE_DIVISOR = 500.0
BODYWEIGHT_PER_SET = 0.4
WORK_SET_TYPES = {"normal", "failure", "dropset"}
OVERLAP_TOLERANCE = timedelta(minutes=10)


def _tonnage_load(workout: dict) -> float:
    tonnage = 0.0
    bw_sets = 0
    for ex in workout.get("exercises", []):
        for s in ex.get("sets", []):
            if s.get("type") not in WORK_SET_TYPES:
                continue
            reps = s.get("reps") or 0
            if not reps:
                continue
            wt = s.get("weight_kg") or 0
            if wt > 0:
                tonnage += wt * reps
            else:
                bw_sets += 1
    return tonnage / TONNAGE_DIVISOR + bw_sets * BODYWEIGHT_PER_SET


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _whoop_strain_for(start: datetime, end: datetime, whoop: list[dict]) -> float | None:
    """Return strain from the Whoop workout that overlaps [start, end], or None."""
    best = None
    best_overlap = timedelta(0)
    for w in whoop:
        ws, we = w.get("start"), w.get("end")
        score = (w.get("score") or {}).get("strain")
        if not ws or not we or score is None:
            continue
        wsd, wed = _parse(ws), _parse(we)
        overlap = min(end, wed) - max(start, wsd)
        if overlap > best_overlap and overlap > -OVERLAP_TOLERANCE:
            best_overlap = max(overlap, timedelta(0))
            best = float(score)
    return best


def list_load_points(days: int = 120) -> list[LoadPoint]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        whoop = get_workout_records(start=cutoff)
    except Exception:
        whoop = []

    out: list[LoadPoint] = []
    page = 1
    while True:
        r = list_workouts(page=page, page_size=10)
        for w in r.get("workouts", []):
            start = w.get("start_time")
            end = w.get("end_time") or start
            if not start:
                continue
            st = _parse(start)
            if st < cutoff:
                return out
            et = _parse(end) if end else st + timedelta(hours=1)
            strain = _whoop_strain_for(st, et, whoop)
            if strain is not None:
                load = strain * STRAIN_TO_LOAD
                source = "hevy+whoop"
            else:
                load = _tonnage_load(w)
                source = "hevy"
            if load > 0:
                out.append(LoadPoint(day=st.date(), load=round(load, 1), source=source))
        if page >= r.get("page_count", 1):
            break
        page += 1
    return out
