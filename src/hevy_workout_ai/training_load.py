"""Banister impulse-response training load: CTL / ATL / TSB.

Source-agnostic. Feed it a list of (date, load) points from any provider
(Strava suffer_score, Peloton kJ, etc.) and it returns daily Fitness /
Fatigue / Form series.

Formulas (Banister 1975, Coggan adaptation — also what Strava documents):
    CTL_t = CTL_{t-1} + (load_t - CTL_{t-1}) / 42   # Fitness, 42d EWMA
    ATL_t = ATL_{t-1} + (load_t -  ATL_{t-1}) / 7   # Fatigue, 7d EWMA
    TSB_t = CTL_t - ATL_t                           # Form

The /42 and /7 smoothing constants are the documented defaults but Strava
does not publish its exact implementation, so local numbers will be
directionally correct rather than a perfect replica of the app.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

CTL_TAU = 42
ATL_TAU = 7


@dataclass
class LoadPoint:
    day: date
    load: float
    source: str = ""


@dataclass
class LoadDay:
    day: date
    load: float
    ctl: float
    atl: float
    tsb: float


def aggregate_daily(points: list[LoadPoint]) -> dict[date, float]:
    """Sum loads per day across all sources."""
    out: dict[date, float] = {}
    for p in points:
        out[p.day] = out.get(p.day, 0.0) + p.load
    return out


def compute_series(
    points: list[LoadPoint],
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[LoadDay]:
    """Compute CTL/ATL/TSB for every day from start..end (inclusive).

    Missing days count as zero load (rest days still decay fitness & fatigue).
    If start/end are omitted, uses the min/max day in points.
    """
    if not points:
        return []

    daily = aggregate_daily(points)
    if start is None:
        start = min(daily)
    if end is None:
        end = max(daily)

    out: list[LoadDay] = []
    ctl = 0.0
    atl = 0.0
    d = start
    while d <= end:
        load = daily.get(d, 0.0)
        ctl += (load - ctl) / CTL_TAU
        atl += (load - atl) / ATL_TAU
        out.append(LoadDay(day=d, load=load, ctl=ctl, atl=atl, tsb=ctl - atl))
        d += timedelta(days=1)
    return out


def current_state(points: list[LoadPoint], today: date | None = None) -> LoadDay | None:
    """Return today's CTL/ATL/TSB (or the most recent day if today is in past)."""
    if not points:
        return None
    end = today or max(p.day for p in points)
    series = compute_series(points, end=end)
    return series[-1] if series else None
