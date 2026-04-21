"""Nutrition log + reverse-engineered maintenance (MacroFactor-style).

Appends daily bodyweight / calories / protein to config/nutrition_log.yaml,
then estimates maintenance kcal from the rolling relationship between
intake and bodyweight trend:

    maintenance ≈ mean(calories) + slope_lb_per_day × 3500

Returns a NutritionAdjustment with a set_delta stackable onto the recovery
adjustment. Large deficits or low protein trigger extra deloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import mean

import yaml

from .config import CONFIG_DIR, load_profile, load_state

NUTRITION_FILE = CONFIG_DIR / "nutrition_log.yaml"

KCAL_PER_LB = 3500.0
MIN_DAYS_FOR_MAINTENANCE = 7
TREND_WINDOW_DAYS = 14
WEIGHT_SMOOTHING_DAYS = 7
PROTEIN_LIFT_G_PER_LB = 1.1
PROTEIN_REST_G_PER_LB = 0.95
FIBER_G_PER_1000_KCAL = 14.0
FIBER_DEFAULT_TARGET_G = 30.0
DEFICIT_TRIGGER_KCAL = -600


@dataclass
class NutritionAdjustment:
    set_delta: int
    notes: list[str]
    calories_today: float | None
    protein_today: float | None
    fiber_today: float | None
    bodyweight_today: float | None
    maintenance_kcal: float | None
    deficit_kcal: float | None
    protein_target_g: float | None
    protein_gap_g: float | None
    fiber_target_g: float | None
    fiber_gap_g: float | None
    is_lifting_day: bool


def _reference_weight_lb(bodyweight_lb: float | None = None) -> float | None:
    """Goal weight if set in profile, else current bodyweight."""
    try:
        profile = load_profile()
        gw = profile.get("goal_weight_lb")
        if gw:
            return float(gw)
    except Exception:
        pass
    return bodyweight_lb


def protein_target_g(bodyweight_lb: float, is_lifting_day: bool) -> float:
    rate = PROTEIN_LIFT_G_PER_LB if is_lifting_day else PROTEIN_REST_G_PER_LB
    ref = _reference_weight_lb(bodyweight_lb) or bodyweight_lb
    return ref * rate


def fiber_target_g(calories_kcal: float | None) -> float:
    if calories_kcal is None:
        return FIBER_DEFAULT_TARGET_G
    return max(FIBER_DEFAULT_TARGET_G, FIBER_G_PER_1000_KCAL * calories_kcal / 1000.0)


def is_lifting_day_today() -> bool:
    try:
        state = load_state()
    except Exception:
        return False
    days = state.get("training_days", [])
    return date.today().weekday() in days


def _load_log() -> list[dict]:
    if not NUTRITION_FILE.exists():
        return []
    data = yaml.safe_load(NUTRITION_FILE.read_text()) or []
    return data if isinstance(data, list) else []


def _save_log(entries: list[dict]) -> None:
    entries_sorted = sorted(entries, key=lambda e: e["date"])
    NUTRITION_FILE.write_text(yaml.safe_dump(entries_sorted, sort_keys=False))


def log_today(
    *,
    bodyweight_lb: float | None = None,
    calories_kcal: float | None = None,
    protein_g: float | None = None,
    fiber_g: float | None = None,
    on_date: str | None = None,
) -> dict:
    """Upsert an entry. Missing fields overwrite only if provided."""
    day = on_date or date.today().isoformat()
    entries = _load_log()
    existing = next((e for e in entries if str(e.get("date")) == day), None)
    if existing is None:
        existing = {"date": day}
        entries.append(existing)

    if bodyweight_lb is not None:
        existing["bodyweight_lb"] = float(bodyweight_lb)
    if calories_kcal is not None:
        existing["calories_kcal"] = float(calories_kcal)
    if protein_g is not None:
        existing["protein_g"] = float(protein_g)
    if fiber_g is not None:
        existing["fiber_g"] = float(fiber_g)

    _save_log(entries)
    return existing


def _smooth_weight(entries: list[dict]) -> list[tuple[int, float]]:
    """Return (day_index, smoothed_weight_lb) pairs using centered moving avg."""
    weighed = [e for e in entries if e.get("bodyweight_lb") is not None]
    if len(weighed) < MIN_DAYS_FOR_MAINTENANCE:
        return []

    by_date = {date.fromisoformat(str(e["date"])).toordinal(): e["bodyweight_lb"] for e in weighed}
    ords = sorted(by_date)
    d0 = ords[0]

    smoothed = []
    for o in ords:
        window = [by_date[x] for x in ords if abs(x - o) <= WEIGHT_SMOOTHING_DAYS // 2]
        smoothed.append((o - d0, mean(window)))
    return smoothed


def _slope_lb_per_day(pts: list[tuple[int, float]]) -> float:
    n = len(pts)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x_mean = mean(xs)
    y_mean = mean(ys)
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    den = sum((x - x_mean) ** 2 for x in xs)
    return num / den if den else 0.0


def estimate_maintenance() -> float | None:
    """Rolling maintenance kcal from intake + weight trend. None if insufficient data."""
    entries = _load_log()
    today_ord = date.today().toordinal()
    window = [
        e for e in entries
        if (today_ord - date.fromisoformat(str(e["date"])).toordinal()) <= TREND_WINDOW_DAYS
        and e.get("calories_kcal") is not None
    ]
    if len(window) < MIN_DAYS_FOR_MAINTENANCE:
        return None

    mean_kcal = mean(e["calories_kcal"] for e in window)
    smoothed = _smooth_weight(window)
    if len(smoothed) < MIN_DAYS_FOR_MAINTENANCE:
        return mean_kcal

    slope = _slope_lb_per_day(smoothed)
    return mean_kcal + slope * KCAL_PER_LB


def get_nutrition_adjustment() -> NutritionAdjustment:
    """Stackable nutrition signal: protein + deficit → extra set deltas."""
    entries = _load_log()
    today = date.today().isoformat()
    today_entry = next((e for e in entries if str(e.get("date")) == today), {})

    cal = today_entry.get("calories_kcal")
    prot = today_entry.get("protein_g")
    fib = today_entry.get("fiber_g")
    bw = today_entry.get("bodyweight_lb")

    if bw is None:
        profile = load_profile()
        bw = profile.get("bodyweight_lb")

    maintenance = estimate_maintenance()
    deficit = (cal - maintenance) if (cal is not None and maintenance is not None) else None

    lifting = is_lifting_day_today()
    ref = _reference_weight_lb(bw)
    target = protein_target_g(ref, lifting) if ref else None
    gap = max(0.0, target - prot) if (target is not None and prot is not None) else None

    fib_target = fiber_target_g(cal)
    fib_gap = max(0.0, fib_target - fib) if fib is not None else None

    set_delta = 0
    notes: list[str] = []

    if deficit is not None and deficit < DEFICIT_TRIGGER_KCAL:
        set_delta -= 1
        notes.append(f"deficit {deficit:+.0f} kcal")

    return NutritionAdjustment(
        set_delta=set_delta,
        notes=notes,
        calories_today=cal,
        protein_today=prot,
        fiber_today=fib,
        bodyweight_today=bw,
        maintenance_kcal=maintenance,
        deficit_kcal=deficit,
        protein_target_g=target,
        protein_gap_g=gap,
        fiber_target_g=fib_target,
        fiber_gap_g=fib_gap,
        is_lifting_day=lifting,
    )
