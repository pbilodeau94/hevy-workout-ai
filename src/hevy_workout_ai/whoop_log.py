"""Rolling daily log of Whoop recovery + sleep, keyed by wake date.

Whoop API returns the last ~N records on demand, so this cache lets the
dashboard read a longer history without hitting the API every render, and
accumulates data beyond what the API still returns.
"""

from __future__ import annotations

from datetime import datetime

from . import store

_MS_PER_HOUR = 3_600_000.0


def _load() -> dict[str, dict]:
    data = store.get("whoop_log") or []
    if isinstance(data, list):
        return {str(e["date"]): e for e in data}
    return data


def _save(by_date: dict[str, dict]) -> None:
    entries = [by_date[d] for d in sorted(by_date)]
    store.set("whoop_log", entries)


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def sync(limit: int = 25) -> tuple[int, int]:
    """Pull recent records from Whoop, upsert into the rolling log.

    Returns (recovery_count, sleep_count) added/updated.
    """
    from .whoop_client import get_recovery_records, get_sleep_records

    by_date = _load()

    recs = get_recovery_records(limit=limit)
    sleeps = get_sleep_records(limit=limit)

    # Sleep is keyed by wake-date (end); recovery is tied 1:1 to a sleep via sleep_id.
    sleep_by_id = {s["id"]: s for s in sleeps}

    r_count = 0
    for r in recs:
        if r.get("score_state") != "SCORED":
            continue
        sid = r.get("sleep_id")
        sleep = sleep_by_id.get(sid)
        # Prefer sleep end for the date so recovery aligns with the morning it was scored.
        if sleep and sleep.get("end"):
            day = _parse_ts(sleep["end"]).astimezone().date().isoformat()
        else:
            day = _parse_ts(r["created_at"]).astimezone().date().isoformat()

        entry = by_date.setdefault(day, {"date": day})
        s = r.get("score", {})
        entry["recovery_score"] = s.get("recovery_score")
        entry["hrv_ms"] = round(s["hrv_rmssd_milli"], 1) if s.get("hrv_rmssd_milli") is not None else None
        entry["rhr_bpm"] = round(s["resting_heart_rate"]) if s.get("resting_heart_rate") is not None else None
        entry["skin_temp_c"] = round(s["skin_temp_celsius"], 2) if s.get("skin_temp_celsius") is not None else None
        r_count += 1

    s_count = 0
    for sl in sleeps:
        if sl.get("score_state") != "SCORED" or sl.get("nap"):
            continue
        day = _parse_ts(sl["end"]).astimezone().date().isoformat()
        entry = by_date.setdefault(day, {"date": day})
        score = sl.get("score", {}) or {}
        stage = score.get("stage_summary", {}) or {}
        need = score.get("sleep_needed", {}) or {}

        in_bed_ms = stage.get("total_in_bed_time_milli", 0)
        awake_ms = stage.get("total_awake_time_milli", 0)
        asleep_h = max(0.0, (in_bed_ms - awake_ms) / _MS_PER_HOUR)
        need_h = (
            need.get("baseline_milli", 0)
            + need.get("need_from_sleep_debt_milli", 0)
            + need.get("need_from_recent_strain_milli", 0)
            + need.get("need_from_recent_nap_milli", 0)
        ) / _MS_PER_HOUR

        entry["sleep_hours"] = round(asleep_h, 2)
        entry["sleep_need_hours"] = round(need_h, 2)
        entry["sleep_debt_hours"] = round(max(0.0, need_h - asleep_h), 2)
        entry["sleep_performance_pct"] = score.get("sleep_performance_percentage")
        entry["sleep_efficiency_pct"] = (
            round(score["sleep_efficiency_percentage"], 1)
            if score.get("sleep_efficiency_percentage") is not None else None
        )
        rem_ms = stage.get("total_rem_sleep_time_milli", 0)
        sws_ms = stage.get("total_slow_wave_sleep_time_milli", 0)
        entry["rem_hours"] = round(rem_ms / _MS_PER_HOUR, 2)
        entry["sws_hours"] = round(sws_ms / _MS_PER_HOUR, 2)
        s_count += 1

    _save(by_date)
    return r_count, s_count


def load_log() -> list[dict]:
    """Return daily entries sorted ascending by date."""
    return [e for _, e in sorted(_load().items())]
