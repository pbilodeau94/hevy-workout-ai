"""Recovery-based workout autoregulation.

Recovery sources (in priority order):
  1. recovery.yaml in config/ — manually populated (e.g. by Apple Health via
     Claude iOS app + a Shortcut, or pasted by hand). Wins if dated today.
  2. Whoop API — last scored recovery from cycle ending today.

The recovery score (0-100) maps to a load multiplier and set delta that the
generator applies to every exercise in the routine.

Bands (Whoop-style):
  red    (< 34)   load x0.90, drop 1 set per exercise (min 2)
  yellow (34-66)  load x1.00, sets unchanged
  green  (>= 67)  load x1.025, sets unchanged
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

from .config import CONFIG_DIR

RECOVERY_FILE = CONFIG_DIR / "recovery.yaml"


@dataclass
class RecoveryAdjustment:
    score: float            # 0-100
    band: str               # red | yellow | green
    load_mult: float        # multiply working weight by this
    set_delta: int          # add (usually <=0) to planned sets per exercise
    source: str             # "whoop" | "manual" | "default"
    note: str               # human-readable summary


def _band(score: float) -> tuple[str, float, int]:
    if score < 34:
        return "red", 0.90, -1
    if score < 67:
        return "yellow", 1.00, 0
    return "green", 1.025, 0


def _from_score(score: float, source: str, extra: str = "") -> RecoveryAdjustment:
    band, mult, delta = _band(score)
    note = f"Recovery {score:.0f} ({band}) via {source}"
    if extra:
        note += f" — {extra}"
    return RecoveryAdjustment(
        score=score, band=band, load_mult=mult, set_delta=delta,
        source=source, note=note,
    )


def _read_manual() -> RecoveryAdjustment | None:
    """Read config/recovery.yaml. Expected format:

        date: 2026-04-21
        recovery_score: 72         # required, 0-100
        hrv_ms: 78.3               # optional
        rhr_bpm: 52                # optional
        sleep_hours: 7.4           # optional
        notes: "felt good"         # optional
    """
    if not RECOVERY_FILE.exists():
        return None
    data = yaml.safe_load(RECOVERY_FILE.read_text()) or {}
    if "recovery_score" not in data:
        return None
    file_date = data.get("date")
    if isinstance(file_date, str):
        file_date = date.fromisoformat(file_date)
    if file_date != date.today():
        return None
    extras = []
    for k, label in [("hrv_ms", "HRV"), ("rhr_bpm", "RHR"), ("sleep_hours", "sleep")]:
        if k in data:
            extras.append(f"{label} {data[k]}")
    return _from_score(
        float(data["recovery_score"]),
        source="manual",
        extra=", ".join(extras),
    )


def _read_whoop() -> RecoveryAdjustment | None:
    try:
        from .whoop_client import get_latest_recovery
        rec = get_latest_recovery()
    except Exception:
        return None
    if not rec or rec.get("score_state") != "SCORED":
        return None
    score = rec.get("score", {}).get("recovery_score")
    if score is None:
        return None
    extras = []
    s = rec["score"]
    if "hrv_rmssd_milli" in s:
        extras.append(f"HRV {s['hrv_rmssd_milli']:.0f}ms")
    if "resting_heart_rate" in s:
        extras.append(f"RHR {s['resting_heart_rate']:.0f}")
    return _from_score(float(score), source="whoop", extra=", ".join(extras))


def get_recovery_adjustment() -> RecoveryAdjustment:
    """Return today's combined recovery + nutrition adjustment, fallback neutral."""
    base = (
        _read_manual()
        or _read_whoop()
        or RecoveryAdjustment(
            score=50.0, band="yellow", load_mult=1.0, set_delta=0,
            source="default", note="No recovery data — neutral (yellow)",
        )
    )

    from .nutrition import get_nutrition_adjustment
    nut = get_nutrition_adjustment()

    bits: list[str] = []
    if nut.protein_target_g is not None:
        day_label = "lift" if nut.is_lifting_day else "rest"
        intake = f"{nut.protein_today:.0f}" if nut.protein_today is not None else "?"
        bits.append(f"protein {intake}/{nut.protein_target_g:.0f}g ({day_label} day)")
    bits.extend(nut.notes)

    if not bits and nut.set_delta == 0:
        return base

    return RecoveryAdjustment(
        score=base.score,
        band=base.band,
        load_mult=base.load_mult,
        set_delta=base.set_delta + nut.set_delta,
        source=base.source,
        note=base.note + " | nutrition: " + (", ".join(bits) if bits else "ok"),
    )
