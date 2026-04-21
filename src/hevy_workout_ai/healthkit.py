"""Health Auto Export → nutrition_log.yaml bridge.

Health Auto Export (iOS app) can write daily JSON snapshots of HealthKit
metrics to iCloud Drive. MacroFactor logs calories/protein/fiber to HealthKit,
and Apple Health tracks Body Mass from the Health app + connected scales.

Expected metric names (Health Auto Export defaults):
  - "dietary_energy"   → calories_kcal (sum per day)
  - "protein"          → protein_g      (sum per day)
  - "fiber"            → fiber_g        (sum per day)
  - "weight_body_mass" → bodyweight_lb  (mean per day, converted from kg if needed)

Setup:
  1. Install Health Auto Export (iOS).
  2. Enable Automations → daily JSON export to iCloud Drive → HealthAutoExport.
  3. Select metrics: Dietary Energy, Protein, Fiber, Body Mass.
  4. Run `hevy sync-health` to ingest.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .nutrition import log_today

DEFAULT_EXPORT_DIRS = [
    Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/HealthAutoExport",
    Path.home() / "Library/Mobile Documents/iCloud~com~lybrary~healthautoexport/Documents",
    Path.home() / "Library/Mobile Documents/iCloud~com~healthauto~export/Documents",
    Path.home() / "Downloads/HealthAutoExport",
]

METRIC_MAP = {
    "dietary_energy": "calories_kcal",
    "active_energy": None,  # ignore (we want dietary)
    "protein": "protein_g",
    "fiber": "fiber_g",
    "dietary_fiber": "fiber_g",
    "weight_body_mass": "bodyweight_lb",
    "body_mass": "bodyweight_lb",
}

KG_TO_LB = 2.2046226218


def _find_export_dir(override: str | None) -> Path | None:
    if override:
        p = Path(override).expanduser()
        return p if p.exists() else None
    for p in DEFAULT_EXPORT_DIRS:
        if p.exists():
            return p
    return None


def _iter_json_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.json"))


def _parse_day(ts: str) -> str | None:
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts[:25] if len(ts) > 25 else ts, fmt).date().isoformat()
        except Exception:
            continue
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return None


def _extract_metrics(payload: dict) -> dict[str, dict[str, list[float]]]:
    """Return {day_iso: {field: [values]}} across all metrics in payload."""
    out: dict[str, dict[str, list[float]]] = {}
    metrics = payload.get("data", {}).get("metrics") or payload.get("metrics") or []
    if not isinstance(metrics, list):
        return out

    for m in metrics:
        name = str(m.get("name", "")).lower()
        field = METRIC_MAP.get(name)
        if not field:
            continue
        units = str(m.get("units", "")).lower()
        for entry in m.get("data", []) or []:
            ts = entry.get("date") or entry.get("startDate") or entry.get("timestamp")
            if not ts:
                continue
            day = _parse_day(str(ts))
            if not day:
                continue
            val = entry.get("qty") if entry.get("qty") is not None else entry.get("value")
            if val is None:
                continue
            val = float(val)
            if field == "bodyweight_lb" and units in ("kg", "kilogram", "kilograms"):
                val *= KG_TO_LB
            out.setdefault(day, {}).setdefault(field, []).append(val)
    return out


def sync_from_export(export_dir: str | None = None, days: int = 7) -> dict:
    """Read JSON exports, aggregate per-day, upsert into nutrition_log.yaml."""
    root = _find_export_dir(export_dir)
    if root is None:
        return {
            "found": False,
            "searched": [str(p) for p in ([Path(export_dir)] if export_dir else DEFAULT_EXPORT_DIRS)],
            "days": {},
            "source": None,
        }

    cutoff = date.today() - timedelta(days=days)
    agg: dict[str, dict[str, list[float]]] = {}
    for f in _iter_json_files(root):
        try:
            payload = json.loads(f.read_text())
        except Exception:
            continue
        parsed = _extract_metrics(payload)
        for day, fields in parsed.items():
            if date.fromisoformat(day) < cutoff:
                continue
            for field, values in fields.items():
                agg.setdefault(day, {}).setdefault(field, []).extend(values)

    written: dict[str, dict[str, float]] = {}
    for day, fields in agg.items():
        row: dict[str, float] = {}
        if "calories_kcal" in fields:
            row["calories_kcal"] = sum(fields["calories_kcal"])
        if "protein_g" in fields:
            row["protein_g"] = sum(fields["protein_g"])
        if "fiber_g" in fields:
            row["fiber_g"] = sum(fields["fiber_g"])
        if "bodyweight_lb" in fields:
            row["bodyweight_lb"] = sum(fields["bodyweight_lb"]) / len(fields["bodyweight_lb"])
        if row:
            log_today(
                bodyweight_lb=row.get("bodyweight_lb"),
                calories_kcal=row.get("calories_kcal"),
                protein_g=row.get("protein_g"),
                fiber_g=row.get("fiber_g"),
                on_date=day,
            )
            written[day] = {k: round(v, 1) for k, v in row.items()}

    return {"found": True, "searched": [str(root)], "source": str(root), "days": written}
