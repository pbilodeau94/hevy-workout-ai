"""Per-muscle-group DOMS (delayed-onset muscle soreness) tracking.

0-100 scale per coarse group (legs / push / pull / core), decays linearly to 0
over 72h since last log (DOMS typically peaks 24-72h then subsides — see
memory/doms_integration.md for literature refs).

Generator uses the effective (decayed) score to modulate today's session:
  <25  train as planned
  25-49  cap top-set load (-5%), no new exercises for group
  50-74  cut sets ~50%, prefer isolation over compounds (protective 40%-bout zone)
  75-100 skip the group entirely, substitute unaffected groups or PT/mobility
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import store

GROUPS = ("legs", "push", "pull", "core")

# Map fine-grained muscle keys (from exercises.yaml) -> coarse DOMS group.
MUSCLE_TO_GROUP = {
    "quads": "legs",
    "glutes_hams": "legs",
    "calves": "legs",
    "chest": "push",
    "shoulders": "push",
    "triceps": "push",
    "back": "pull",
    "biceps": "pull",
    "core": "core",
}

DECAY_HOURS = 72.0


@dataclass
class DomsState:
    """Effective (decayed) DOMS scores for today, per group."""
    scores: dict[str, float] = field(default_factory=dict)  # group -> 0-100
    raw: dict[str, dict] = field(default_factory=dict)      # group -> {score, logged_at}

    def for_muscle(self, muscle: str) -> float:
        grp = MUSCLE_TO_GROUP.get(muscle)
        if grp is None:
            return 0.0
        return self.scores.get(grp, 0.0)

    def band(self, group: str) -> str:
        s = self.scores.get(group, 0.0)
        if s < 25:
            return "none"
        if s < 50:
            return "mild"
        if s < 75:
            return "moderate"
        return "severe"

    def summary(self) -> str:
        parts = [f"{g}:{int(self.scores.get(g, 0))}" for g in GROUPS if self.scores.get(g, 0) >= 25]
        return " ".join(parts) if parts else "none"


def _now() -> datetime:
    return datetime.now()


def _decay(score: float, logged_at: datetime, ref: datetime | None = None) -> float:
    """Linear decay to 0 over DECAY_HOURS since logged_at."""
    ref = ref or _now()
    hours = (ref - logged_at).total_seconds() / 3600.0
    if hours <= 0:
        return score
    if hours >= DECAY_HOURS:
        return 0.0
    return round(score * (1.0 - hours / DECAY_HOURS), 1)


def _load_raw() -> dict:
    return store.get("doms_log") or {}


def _save_raw(data: dict) -> None:
    store.set("doms_log", data)


def log_doms(scores: dict[str, float | int], at: datetime | None = None) -> DomsState:
    """Write one or more group scores with a timestamp. Partial updates allowed.

    scores: mapping like {"legs": 85, "push": 10}. Groups not passed keep their
    prior entry (which still decays). Use score=0 to explicitly zero a group.
    """
    ts = (at or _now()).replace(microsecond=0)
    data = _load_raw()
    entries = data.get("entries", {})
    for g, v in scores.items():
        if g not in GROUPS:
            raise ValueError(f"unknown group {g!r}; expected one of {GROUPS}")
        v = max(0.0, min(100.0, float(v)))
        entries[g] = {"score": v, "logged_at": ts.isoformat()}
    data["entries"] = entries
    _save_raw(data)
    return get_doms_state()


def get_doms_state(ref: datetime | None = None) -> DomsState:
    """Return today's effective (decayed) per-group soreness."""
    data = _load_raw()
    entries = data.get("entries", {}) or {}
    st = DomsState()
    for g in GROUPS:
        rec = entries.get(g)
        if not rec:
            st.scores[g] = 0.0
            continue
        logged_at = rec["logged_at"]
        if isinstance(logged_at, str):
            logged_at = datetime.fromisoformat(logged_at)
        elif isinstance(logged_at, date):
            logged_at = datetime.combine(logged_at, datetime.min.time())
        eff = _decay(float(rec["score"]), logged_at, ref=ref)
        st.scores[g] = eff
        st.raw[g] = {"score": float(rec["score"]), "logged_at": logged_at.isoformat()}
    return st


# ---- Generator adjustments -------------------------------------------------

@dataclass
class GroupAdjustment:
    score: float
    band: str            # none | mild | moderate | severe
    skip: bool           # drop this muscle slot entirely
    set_mult: float      # multiply planned sets by this
    load_mult: float     # multiply top-set weight by this
    prefer_isolation: bool  # bias exercise pool away from heavy compounds


def adjustment_for(muscle: str, state: DomsState | None = None) -> GroupAdjustment:
    state = state or get_doms_state()
    grp = MUSCLE_TO_GROUP.get(muscle)
    score = state.scores.get(grp, 0.0) if grp else 0.0
    if score < 25:
        return GroupAdjustment(score, "none", False, 1.0, 1.0, False)
    if score < 50:
        return GroupAdjustment(score, "mild", False, 1.0, 0.95, False)
    if score < 75:
        return GroupAdjustment(score, "moderate", False, 0.5, 0.85, True)
    return GroupAdjustment(score, "severe", True, 0.0, 0.0, True)
