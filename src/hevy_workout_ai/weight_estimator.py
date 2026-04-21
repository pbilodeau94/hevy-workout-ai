"""Estimate weights for exercises without history.

Uses known exercise history + biomechanical ratios to estimate starting
weights for exercises you haven't done yet. All weights in kg internally.
"""

from __future__ import annotations

from .config import load_exercises
from .hevy_client import get_exercise_history

KG_TO_LB = 2.20462
LB_TO_KG = 0.453592

# Ratio multipliers relative to a "reference" exercise per muscle group.
# e.g. if you bench 50 lb DBs, incline is typically ~85% of that.
# These are conservative starting points — better to start light.
EXERCISE_RATIOS: dict[str, dict[str, float]] = {
    # Chest: reference = Bench Press (Dumbbell)
    "chest": {
        "3601968B": 1.0,    # Bench Press (Dumbbell) — reference
        "07B38369": 0.85,   # Incline Bench Press
        "12017185": 0.50,   # Chest Fly
        "D3E2AB55": 0.45,   # Incline Chest Fly
        "756EE329": 0.90,   # Floor Press
        "F72FA239": 0.75,   # Squeeze Press
        "BE89C631": 0.70,   # Hex Press
        "18487FA7": 0.80,   # Decline Bench Press
        "A351AED7": 0.45,   # Decline Chest Fly
    },
    # Back: reference = Dumbbell Row
    "back": {
        "F1E57334": 1.0,    # Dumbbell Row — reference
        "23E92538": 0.70,   # Bent Over Row (both hands, lighter per hand)
        "914F3A96": 0.70,   # Chest Supported Incline Row
        "67280085": 0.60,   # Pullover
        "1B89CA1B": 0.50,   # Renegade Row (stability limited)
    },
    # Shoulders: reference = Overhead Press (Dumbbell)
    "shoulders": {
        "6AC96645": 1.0,    # Overhead Press — reference
        "9930DF71": 1.0,    # Seated Overhead Press
        "878CD1D0": 1.0,    # Shoulder Press
        "A69FF221": 0.85,   # Arnold Press
        "422B08F1": 0.40,   # Lateral Raise
        "9372FFAA": 0.40,   # Seated Lateral Raise
        "E5988A0A": 0.35,   # Rear Delt Reverse Fly
        "B582299E": 0.35,   # Chest Supported Reverse Fly
        "8293E554": 0.40,   # Front Raise
        "797F0782": 0.50,   # Upright Row
    },
    # Quads: estimate from bench press × 0.8 as starting heuristic
    # (goblet squat uses one DB, so total weight is 1x not 2x)
    # Quads: all bilateral (no single-leg balance exercises)
    "quads": {
        "3D0C7C75": 1.0,    # Goblet Squat — reference (single DB)
        "DCFF3E9F": 0.70,   # Squat (Dumbbell) — per hand
        "05293BCA": 0.80,   # Sumo Squat
    },
    # Glutes/Hams: reference = Romanian Deadlift
    "glutes_hams": {
        "72CFFAD5": 1.0,    # RDL — reference
        "5F4E6DD3": 1.0,    # Deadlift (Dumbbell)
    },
    # Biceps: reference = Bicep Curl (Dumbbell)
    "biceps": {
        "37FCC2BB": 1.0,    # Bicep Curl — reference
        "7E3BC8B6": 1.0,    # Hammer Curl
        "8BAB2735": 0.80,   # Seated Incline Curl
        "724CDE60": 0.75,   # Concentration Curl
        "90427D4A": 0.70,   # Spider Curl
        "FAB6EB2F": 0.80,   # Preacher Curl
        "72297E8C": 0.80,   # Waiter Curl
        "123EE239": 0.85,   # Zottman Curl
    },
    # Triceps: estimate from bench press × 0.35
    "triceps": {
        "3765684D": 1.0,    # Triceps Extension — reference
        "68F8A292": 0.90,   # Skullcrusher
        "6127A3AD": 0.70,   # Kickback
        "8347DFD1": 0.80,   # Single Arm Extension
    },
}

# Cross-muscle estimation: if we know bench press, we can estimate
# other muscle groups' reference exercises.
# All ratios are relative to Bench Press (Dumbbell) per-hand weight.
CROSS_MUSCLE_FROM_BENCH: dict[str, float] = {
    "chest": 1.0,
    "back": 1.0,        # DB row ≈ bench press weight
    "shoulders": 0.50,  # OHP ≈ 50% of bench
    "quads": 1.20,      # Goblet squat (single DB) ≈ 120% of bench per-hand
    "glutes_hams": 0.80,  # RDL per hand ≈ 80% of bench
    "biceps": 0.40,     # Curl ≈ 40% of bench
    "triceps": 0.35,    # Extension ≈ 35% of bench
}


def get_all_history() -> dict[str, float]:
    """Fetch the last weight (kg) for every exercise in our DB that has history."""
    db = load_exercises()
    history: dict[str, float] = {}

    for exercises in db.values():
        for ex in exercises:
            try:
                resp = get_exercise_history(ex["id"], page=1, page_size=5)
                entries = resp.get("exercise_history", [])
                if not entries:
                    continue
                latest_id = entries[0]["workout_id"]
                weights = [
                    e["weight_kg"]
                    for e in entries
                    if e["workout_id"] == latest_id
                    and e["set_type"] == "normal"
                    and e["weight_kg"] is not None
                ]
                if weights:
                    history[ex["id"]] = max(weights)
            except Exception:
                continue

    return history


def _find_reference_weight(muscle: str, history: dict[str, float]) -> float | None:
    """Find the known weight for the reference exercise in a muscle group."""
    ratios = EXERCISE_RATIOS.get(muscle, {})
    for eid, ratio in ratios.items():
        if ratio == 1.0 and eid in history:
            return history[eid]
    # Fallback: use any known exercise in this muscle group
    for eid in ratios:
        if eid in history:
            return history[eid] / ratios[eid]
    return None


def estimate_weight(exercise_id: str, muscle: str, history: dict[str, float]) -> float | None:
    """Estimate weight for an exercise based on history and ratios.

    Returns weight in kg, or None if we can't estimate.
    """
    # Direct history — use it
    if exercise_id in history:
        return history[exercise_id]

    # Try within-muscle estimation
    ref_weight = _find_reference_weight(muscle, history)
    if ref_weight is not None:
        ratio = EXERCISE_RATIOS.get(muscle, {}).get(exercise_id)
        if ratio is not None:
            return round(ref_weight * ratio, 2)

    # Try cross-muscle estimation from bench press
    bench_weight = history.get("3601968B")  # Bench Press (Dumbbell)
    if bench_weight is not None:
        cross_ratio = CROSS_MUSCLE_FROM_BENCH.get(muscle)
        if cross_ratio is not None:
            muscle_ref = bench_weight * cross_ratio
            within_ratio = EXERCISE_RATIOS.get(muscle, {}).get(exercise_id, 1.0)
            return round(muscle_ref * within_ratio, 2)

    return None


def snap_to_increment(weight_kg: float, increment_lb: float = 2.5) -> float:
    """Round a kg weight down to the nearest dumbbell increment in lb, then back to kg."""
    lb = weight_kg * KG_TO_LB
    snapped_lb = round(lb / increment_lb) * increment_lb
    snapped_lb = max(snapped_lb, increment_lb)  # minimum 1 increment
    return round(snapped_lb * LB_TO_KG, 2)
