"""Daily training verdict — synthesize recovery, DOMS, and TSB into one call.

Priority order (first match wins):
  1. severe DOMS anywhere, red recovery (<34), or deeply fatigued (TSB <= -20)
     -> recovery_day (steps + light mobility/PT only)
  2. moderate leg DOMS (50-74) or yellow recovery
     -> zone2_bike (protective 40% bout, no eccentric load on sore groups)
  3. user flagged no_gym
     -> dumbbell_day (DB/bodyweight only, still respects DOMS)
  4. default
     -> weights (today's generated routine as-is)
"""

from __future__ import annotations

from dataclasses import dataclass

from .doms import DomsState, GROUPS, get_doms_state
from .recovery import RecoveryAdjustment, get_recovery_adjustment


@dataclass
class Verdict:
    call: str           # recovery_day | zone2_bike | dumbbell_day | weights
    headline: str       # short title for UI
    rationale: str      # one-sentence why
    prescription: str   # what to do (action banner)
    push_routine: bool  # whether there's a Hevy routine to push
    dumbbell_only: bool # hint to generator


def _tsb(tsb: float | None) -> str:
    if tsb is None:
        return "unknown"
    if tsb >= 10:
        return "fresh"
    if tsb >= -10:
        return "neutral"
    if tsb >= -20:
        return "fatigued"
    return "deep_fatigue"


def recommend(
    recovery: RecoveryAdjustment | None = None,
    doms: DomsState | None = None,
    tsb: float | None = None,
    ctl: float | None = None,
    no_gym: bool = False,
) -> Verdict:
    recovery = recovery or get_recovery_adjustment()
    doms = doms or get_doms_state()

    severe = [g for g in GROUPS if doms.scores.get(g, 0) >= 75]
    moderate = [g for g in GROUPS if 50 <= doms.scores.get(g, 0) < 75]
    red = recovery.score < 34
    yellow = 34 <= recovery.score < 67
    # Scale-invariant deep-fatigue: TSB ratio (Form/Fitness) ≤ -1.2.
    # CTL floor of 10 keeps the ratio sensible when fitness is very low.
    tsb_ratio = (tsb / max(ctl or 0, 10)) if tsb is not None else None
    deep = tsb_ratio is not None and tsb_ratio <= -1.2

    if severe or red or deep:
        reasons = []
        if severe:
            reasons.append(f"severe DOMS ({', '.join(severe)})")
        if red:
            reasons.append(f"red recovery ({recovery.score:.0f})")
        if deep:
            reasons.append(f"deeply fatigued (TSB {tsb:+.0f}, {tsb_ratio*100:+.0f}% of fitness)")
        return Verdict(
            call="recovery_day",
            headline="Recovery day",
            rationale="; ".join(reasons),
            prescription="Hit 8k steps + 20 min easy walk or PT/mobility only.",
            push_routine=False,
            dumbbell_only=False,
        )

    leg_mod = doms.scores.get("legs", 0) >= 50
    if leg_mod or yellow:
        reasons = []
        if leg_mod:
            reasons.append(f"moderate leg DOMS ({int(doms.scores['legs'])})")
        if yellow:
            reasons.append(f"yellow recovery ({recovery.score:.0f})")
        return Verdict(
            call="zone2_bike",
            headline="Zone-2 bike",
            rationale="; ".join(reasons),
            prescription="40–60 min easy cardio — protects the repeated-bout effect without eccentric load.",
            push_routine=False,
            dumbbell_only=False,
        )

    if no_gym:
        return Verdict(
            call="dumbbell_day",
            headline="Dumbbell-only lift",
            rationale="No gym today — DOMS still applied.",
            prescription="DB/bodyweight variant of today's session.",
            push_routine=True,
            dumbbell_only=True,
        )

    return Verdict(
        call="weights",
        headline="Weights (full gym)",
        rationale=f"Recovery {recovery.score:.0f} ({recovery.band}), DOMS {doms.summary() or 'clear'}.",
        prescription="Train as planned.",
        push_routine=True,
        dumbbell_only=False,
    )
