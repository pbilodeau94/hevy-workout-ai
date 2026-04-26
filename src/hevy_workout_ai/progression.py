"""Closed-loop load progression based on previous session performance.

Pulls each exercise's last *completed* working sets from Hevy and decides:
  - bump load (+2.5%) when last session was easy (top of rep range, low RPE)
  - hold when last session was on target
  - deload (-2.5%) when last session was hard (bottom of range, high RPE)

The output multiplier stacks with recovery.load_mult in generator.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import load_exercises
from .hevy_client import get_exercise_history

BUMP_MULT = 1.025
DELOAD_MULT = 0.975


@dataclass
class LastSession:
    weight_kg: float
    max_reps: int            # best normal-set reps from the latest workout
    min_reps: int            # worst normal-set reps from the latest workout
    rpe: float | None        # highest RPE logged across the sets, or None


def _extract_last_session(entries: list[dict]) -> LastSession | None:
    if not entries:
        return None
    latest_id = entries[0].get("workout_id")
    sets = [
        e for e in entries
        if e.get("workout_id") == latest_id
        and e.get("set_type") == "normal"
        and e.get("weight_kg") is not None
        and e.get("reps") is not None
    ]
    if not sets:
        return None
    weight = max(s["weight_kg"] for s in sets)
    reps = [s["reps"] for s in sets]
    rpes = [s["rpe"] for s in sets if s.get("rpe") is not None]
    return LastSession(
        weight_kg=weight,
        max_reps=max(reps),
        min_reps=min(reps),
        rpe=max(rpes) if rpes else None,
    )


def get_last_sessions() -> dict[str, LastSession]:
    """Fetch last-session summary for every exercise in the DB with history."""
    db = load_exercises()
    out: dict[str, LastSession] = {}
    for exercises in db.values():
        for ex in exercises:
            try:
                resp = get_exercise_history(ex["id"], page=1, page_size=10)
                entries = resp.get("exercise_history", [])
            except Exception:
                continue
            last = _extract_last_session(entries)
            if last is not None:
                out[ex["id"]] = last
    return out


def progression_multiplier(last: LastSession, rep_lo: int, rep_hi: int) -> float:
    """Decide the load multiplier for the next session.

    Heuristic:
      - min_reps < rep_lo OR RPE >= 9.5  → deload 2.5%
      - max_reps >= rep_hi AND (RPE is None OR RPE <= 7.5) → bump 2.5%
      - otherwise → hold
    """
    if last.min_reps < rep_lo:
        return DELOAD_MULT
    if last.rpe is not None and last.rpe >= 9.5:
        return DELOAD_MULT
    rpe_ok = last.rpe is None or last.rpe <= 7.5
    if last.max_reps >= rep_hi and rpe_ok:
        return BUMP_MULT
    return 1.0
