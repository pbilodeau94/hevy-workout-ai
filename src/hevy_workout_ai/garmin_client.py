"""Garmin Connect client for bodyweight (Index scale).

One-time setup:
  1. Set GARMIN_EMAIL and GARMIN_PASSWORD in .env.
  2. First call triggers login; session tokens are cached under ~/.garminconnect/
     via garth, so subsequent calls reuse them.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

KG_TO_LB = 2.20462
TOKENSTORE = Path.home() / ".garminconnect"


def _client():
    from garminconnect import Garmin  # lazy import

    try:
        g = Garmin()
        g.login(str(TOKENSTORE))
        return g
    except Exception:
        email = os.environ.get("GARMIN_EMAIL")
        pw = os.environ.get("GARMIN_PASSWORD")
        if not email or not pw:
            raise RuntimeError("Set GARMIN_EMAIL and GARMIN_PASSWORD in .env")
        g = Garmin(email=email, password=pw)
        g.login()
        TOKENSTORE.mkdir(parents=True, exist_ok=True)
        g.client.dump(str(TOKENSTORE))
        return g


def get_weight_series(days: int = 30) -> list[tuple[str, float]]:
    """Return [(YYYY-MM-DD, bodyweight_lb)] from Garmin, one point per day.

    If multiple weigh-ins happen on the same day, the daily average is used
    (matches Garmin Connect's displayed value).
    """
    g = _client()
    end = date.today()
    start = end - timedelta(days=days)
    data = g.get_body_composition(start.isoformat(), end.isoformat())

    daily = (data or {}).get("dateWeightList") or []
    out: list[tuple[str, float]] = []
    for d in daily:
        ts_ms = d.get("date") or d.get("samplePk")
        w_g = d.get("weight")
        if ts_ms is None or w_g is None:
            continue
        day = date.fromtimestamp(ts_ms / 1000).isoformat()
        lb = (w_g / 1000.0) * KG_TO_LB
        out.append((day, round(lb, 1)))

    # dedup per day (keep last)
    by_day: dict[str, float] = {}
    for day, lb in out:
        by_day[day] = lb
    return sorted(by_day.items())


def get_latest_weight_lb() -> float | None:
    series = get_weight_series(days=14)
    return series[-1][1] if series else None
