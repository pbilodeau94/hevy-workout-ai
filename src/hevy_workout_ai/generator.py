"""Generate workouts from program templates + exercise pool."""

from __future__ import annotations

import random

from .config import load_exercises, load_profile, load_programs, load_pt_routine, load_state
from .doms import DomsState, adjustment_for as doms_adjustment_for, get_doms_state
from .progression import LastSession, get_last_sessions, progression_multiplier
from .recovery import RecoveryAdjustment, get_recovery_adjustment
from .weight_estimator import estimate_weight, get_all_history, snap_to_increment

KG_TO_LB = 2.20462


_EQUIPMENT_WEIGHTS = [
    ("(Barbell)", 5),
    ("(Machine)", 3),
    ("(Cable)", 3),
    ("(Dumbbell)", 1),
]


def _exercise_weight(ex: dict) -> int:
    """Weight an exercise for selection. Barbell compounds prioritized."""
    name = ex.get("name", "")
    for token, w in _EQUIPMENT_WEIGHTS:
        if token in name:
            return w
    return 2


def _weighted_choice(items: list[dict], rng: random.Random) -> dict:
    weights = [_exercise_weight(e) for e in items]
    return rng.choices(items, weights=weights, k=1)[0]


def _pick_exercises(
    pool: list[dict], n: int, prefer: list[str] | None = None, rng: random.Random | None = None
) -> list[dict]:
    """Pick n exercises from pool, preferring tags in order. Barbell-weighted."""
    if prefer is None:
        prefer = []
    r = rng or random

    picked: list[dict] = []
    remaining = list(pool)

    for tag in prefer:
        if len(picked) >= n:
            break
        matches = [e for e in remaining if e["tag"] == tag]
        if matches:
            choice = _weighted_choice(matches, r)
            picked.append(choice)
            remaining.remove(choice)

    while len(picked) < n and remaining:
        choice = _weighted_choice(remaining, r)
        picked.append(choice)
        remaining.remove(choice)

    return picked


def _build_exercise_muscle_map() -> dict[str, str]:
    """Map exercise_template_id -> muscle group name."""
    db = load_exercises()
    mapping = {}
    for muscle, exercises in db.items():
        for ex in exercises:
            mapping[ex["id"]] = muscle
    return mapping


def _interleave_by_muscle(items: list[tuple[str, dict]]) -> list[dict]:
    """Reorder (muscle, exercise) pairs so same-muscle items aren't consecutive.

    Greedy: at each step pick from the muscle with the most remaining items,
    breaking ties alphabetically, and skipping the previous muscle when possible.
    """
    buckets: dict[str, list[dict]] = {}
    for m, ex in items:
        buckets.setdefault(m, []).append(ex)

    result: list[dict] = []
    last = None
    while any(buckets.values()):
        ranked = sorted(
            ((m, b) for m, b in buckets.items() if b),
            key=lambda kv: (-len(kv[1]), kv[0]),
        )
        pick = next((m for m, _ in ranked if m != last), ranked[0][0])
        result.append(buckets[pick].pop(0))
        last = pick
    return result


def _make_block_rng(block: int, day_key: str) -> random.Random:
    """Create a seeded RNG for deterministic exercise selection within a block.

    Same block + day_key always produces the same exercises. Different blocks
    produce different exercises (variety across training blocks).
    """
    seed = f"block-{block}-{day_key}"
    return random.Random(seed)


def generate_pt_routine() -> dict:
    """Build a standalone daily PT routine payload for Hevy."""
    return {
        "routine": {
            "title": "PT Daily",
            "folder_id": None,
            "notes": "Daily PT routine (prescribed). Do every day.",
            "exercises": _pt_exercises_block(),
        }
    }


def generate_routine(
    day_key: str | None = None,
    *,
    use_history: bool = True,
    history_cache: dict[str, float] | None = None,
    last_sessions: dict[str, LastSession] | None = None,
    block: int | None = None,
    recovery: RecoveryAdjustment | None = None,
    doms: DomsState | None = None,
    dumbbell_only: bool = False,
) -> dict:
    """Generate a Hevy routine payload for the next workout day.

    If day_key is None, picks the first day in the current phase rotation.

    When block is set, exercise selection is deterministic (seeded by block
    number), so the same exercises repeat every week within the block.

    When use_history is True, fetches previous weights from Hevy and
    estimates weights for exercises without history using biomechanical ratios.
    """
    profile = load_profile()
    exercises_db = load_exercises()
    programs = load_programs()

    phase = profile["training"]["current_phase"]
    program = programs[phase]
    rest_cfg = profile["training"]["rest"]
    increment_lb = profile["dumbbell_range"]["increment_lb"]

    if day_key is None:
        day_key = program["rotation"][0]

    # Load block from state if not provided
    if block is None:
        state = load_state()
        block = state["current_block"]

    rng = _make_block_rng(block, day_key)
    day = program[day_key]

    if recovery is None:
        recovery = get_recovery_adjustment()

    if doms is None:
        doms = get_doms_state()

    # Fetch history once
    history = history_cache if history_cache is not None else {}
    if use_history and not history:
        history = get_all_history()

    last_sess = last_sessions if last_sessions is not None else {}
    if use_history and not last_sess:
        last_sess = get_last_sessions()

    tagged_exercises: list[tuple[str, dict]] = []
    used_ids: set[str] = set()

    for slot in day["exercises"]:
        muscle = slot["muscle"]
        doms_adj = doms_adjustment_for(muscle, doms)
        if doms_adj.skip:
            continue

        pool = [e for e in exercises_db.get(muscle, []) if e["id"] not in used_ids]
        if not pool:
            pool = exercises_db.get(muscle, [])
        if not pool:
            continue

        if dumbbell_only:
            db_pool = [e for e in pool if "(Dumbbell)" in e.get("name", "") or "(Bodyweight)" in e.get("name", "")]
            if db_pool:
                pool = db_pool

        if doms_adj.prefer_isolation:
            iso = [e for e in pool if e.get("tag") == "isolation"]
            if iso:
                pool = iso

        picks = _pick_exercises(pool, slot["pick"], slot.get("prefer"), rng=rng)
        for p in picks:
            used_ids.add(p["id"])
        rep_lo, rep_hi = slot["rep_range"]
        rest_key = slot.get("rest_key", "upper_isolation")
        rest = rest_cfg.get(rest_key, 60)

        for ex in picks:
            weight_kg = None
            source = None

            prog_mult = 1.0
            if use_history:
                weight_kg = estimate_weight(ex["id"], muscle, history)
                if weight_kg is not None:
                    if ex["id"] in history:
                        source = "history"
                        ls = last_sess.get(ex["id"])
                        if ls is not None:
                            prog_mult = progression_multiplier(ls, rep_lo, rep_hi)
                    else:
                        source = "estimated"
                    weight_kg *= recovery.load_mult * prog_mult * doms_adj.load_mult
                    weight_kg = snap_to_increment(weight_kg, increment_lb)

            if weight_kg is not None:
                lb = round(weight_kg * KG_TO_LB, 1)
                prog_tag = ""
                if prog_mult > 1.0:
                    prog_tag = " ↑"
                elif prog_mult < 1.0:
                    prog_tag = " ↓"
                note = f"{'Last' if source == 'history' else 'Est'}: {lb} lb{prog_tag}"
            else:
                note = None

            n_sets = max(2, slot["sets"] + recovery.set_delta)
            n_sets = max(1, int(round(n_sets * doms_adj.set_mult)))
            sets = []
            for _ in range(n_sets):
                sets.append({
                    "type": "normal",
                    "weight_kg": weight_kg,
                    "reps": None,
                    "rep_range": {"start": rep_lo, "end": rep_hi},
                })

            tagged_exercises.append((muscle, {
                "exercise_template_id": ex["id"],
                "superset_id": None,
                "rest_seconds": rest,
                "notes": note,
                "sets": sets,
            }))

    routine_exercises = _interleave_by_muscle(tagged_exercises)

    return {
        "routine": {
            "title": day["name"],
            "folder_id": None,
            "notes": f"{day['focus']} | {phase.replace('_', ' ').title()} phase | Block {block} | {recovery.note} | DOMS: {doms.summary()}",
            "exercises": routine_exercises,
        }
    }


def _pt_exercises_block() -> list[dict]:
    """Return the PT-prescribed exercises as Hevy routine-exercise dicts."""
    pt = load_pt_routine()
    items = []
    for ex in pt["exercises"]:
        note_parts = ["PT"]
        if ex.get("notes"):
            note_parts.append(ex["notes"])
        sets = [
            {
                "type": "normal",
                "weight_kg": None,
                "reps": ex["reps"],
                "rep_range": None,
            }
            for _ in range(ex["sets"])
        ]
        items.append({
            "exercise_template_id": ex["hevy_id"],
            "superset_id": None,
            "rest_seconds": 30,
            "notes": " — ".join(note_parts),
            "sets": sets,
        })
    return items


def generate_week_routines(*, use_history: bool = True, block: int | None = None) -> list[dict]:
    """Generate all routines for the current week."""
    profile = load_profile()
    programs = load_programs()
    phase = profile["training"]["current_phase"]
    program = programs[phase]
    days_per_week = profile["training"]["days_per_week"]

    if block is None:
        state = load_state()
        block = state["current_block"]

    history = get_all_history() if use_history else {}
    last_sess = get_last_sessions() if use_history else {}
    recovery = get_recovery_adjustment()

    rotation = program["rotation"]
    routines = []
    for i in range(days_per_week):
        day_key = rotation[i % len(rotation)]
        routines.append(
            generate_routine(
                day_key, use_history=use_history, history_cache=history,
                last_sessions=last_sess, block=block, recovery=recovery,
            )
        )

    return routines


def _build_name_lookup() -> dict[str, str]:
    """Build exercise_template_id -> name mapping from exercise DB + PT routine."""
    db = load_exercises()
    lookup = {}
    for exercises in db.values():
        for ex in exercises:
            lookup[ex["id"]] = ex["name"]
    for ex in load_pt_routine().get("exercises", []):
        if ex.get("hevy_id"):
            lookup[ex["hevy_id"]] = ex["name"]
    return lookup


def estimate_duration(routine: dict) -> float:
    """Estimate routine duration in minutes."""
    total_seconds = 0.0

    for ex in routine["routine"]["exercises"]:
        sets = ex["sets"]
        n_sets = len(sets)
        rest = ex.get("rest_seconds", 60)

        set_duration = 35 if rest >= 90 else 25
        exercise_time = (n_sets * set_duration) + ((n_sets - 1) * rest) + 30
        total_seconds += exercise_time

    return total_seconds / 60


def preview_routine(routine: dict) -> str:
    """Return a human-readable preview of a routine payload."""
    names = _build_name_lookup()
    r = routine["routine"]
    duration = estimate_duration(routine)
    lines = [
        f"  {r['title']}  (~{duration:.0f} min)",
        f"  {r.get('notes', '')}",
        "",
    ]

    for i, ex in enumerate(r["exercises"], 1):
        eid = ex["exercise_template_id"]
        name = names.get(eid, eid)
        sets = ex["sets"]
        n_sets = len(sets)
        rest = ex.get("rest_seconds", "?")
        note = ex.get("notes") or ""

        if sets and sets[0].get("rep_range"):
            rr = sets[0]["rep_range"]
            rep_str = f"{rr['start']}-{rr['end']} reps"
        else:
            rep_str = "reps TBD"

        weight = sets[0].get("weight_kg") if sets else None
        if weight is not None:
            lb = round(weight * KG_TO_LB, 1)
            marker = "*" if note.startswith("Est") else " "
            weight_str = f"  @ {lb} lb{marker}"
        else:
            weight_str = ""

        lines.append(
            f"  {i}. {name:<40} {n_sets}x {rep_str}{weight_str}  (rest {rest}s)"
        )

    lines.append("")
    lines.append("  * = estimated weight (no history)")
    return "\n".join(lines)
