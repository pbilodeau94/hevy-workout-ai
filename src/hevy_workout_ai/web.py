"""Streamlit dashboard — web equivalent of `hevy dashboard`."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from hevy_workout_ai.config import load_profile
from hevy_workout_ai.nutrition import (
    _load_log,
    estimate_maintenance,
    get_nutrition_adjustment,
    is_lifting_day_today,
    protein_target_g,
)
from hevy_workout_ai.recovery import get_recovery_adjustment


st.set_page_config(page_title="Fitness Dashboard", page_icon="🏋️", layout="wide")
st.title("🏋️ Fitness Dashboard")

today = date.today()
today_ord = today.toordinal()
window_start_ord = today_ord - 6

profile = load_profile()
adj = get_recovery_adjustment()
nut = get_nutrition_adjustment()
lift = is_lifting_day_today()
bw = nut.bodyweight_today or profile.get("bodyweight_lb")


# ── Today row of metrics ──────────────────────────────────────────────────────
st.subheader(f"Today — {today.isoformat()} ({'LIFT' if lift else 'REST'} day)")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Recovery", f"{adj.score:.0f}", f"{adj.band} · load x{adj.load_mult}")

if nut.protein_target_g:
    intake = nut.protein_today or 0
    c2.metric("Protein (g)", f"{intake:.0f} / {nut.protein_target_g:.0f}",
              f"gap {max(0, nut.protein_target_g - intake):.0f}g")
else:
    c2.metric("Protein", "—")

if nut.calories_today is not None and nut.maintenance_kcal is not None:
    c3.metric("Calories", f"{nut.calories_today:.0f} / {nut.maintenance_kcal:.0f}",
              f"{nut.deficit_kcal:+.0f} kcal")
elif nut.calories_today is not None:
    c3.metric("Calories", f"{nut.calories_today:.0f}")
else:
    c3.metric("Calories", "—")

if nut.fiber_target_g:
    fi = nut.fiber_today or 0
    c4.metric("Fiber (g)", f"{fi:.0f} / {nut.fiber_target_g:.0f}",
              f"gap {max(0, nut.fiber_target_g - fi):.0f}g")
else:
    c4.metric("Fiber", "—")


# ── Weight + Nutrition charts ─────────────────────────────────────────────────
entries = _load_log()
window = [
    e for e in entries
    if window_start_ord <= date.fromisoformat(str(e["date"])).toordinal() <= today_ord
]
df = pd.DataFrame(window)
if not df.empty:
    df["date"] = pd.to_datetime(df["date"])

left, right = st.columns(2)

with left:
    st.subheader("Weight (7d)")
    if not df.empty and "bodyweight_lb" in df and df["bodyweight_lb"].notna().any():
        st.line_chart(df.set_index("date")["bodyweight_lb"])
        maint = estimate_maintenance()
        if maint is not None:
            st.caption(f"Rolling maintenance ≈ {maint:.0f} kcal")
    else:
        st.info("No weight data in the last 7 days.")

with right:
    st.subheader("Nutrition (7d)")
    if not df.empty and {"calories_kcal", "protein_g"}.issubset(df.columns):
        cols = [c for c in ["calories_kcal", "protein_g", "fiber_g"] if c in df.columns]
        st.bar_chart(df.set_index("date")[cols])
    else:
        st.info("No nutrition data in the last 7 days.")


# ── Lifting ──────────────────────────────────────────────────────────────────
st.subheader("Lifting (7d, Hevy)")
try:
    from hevy_workout_ai.hevy_client import list_workouts

    data = list_workouts(page=1, page_size=10)
    workouts = data.get("workouts", data) if isinstance(data, dict) else []
    if not isinstance(workouts, list):
        workouts = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    rows = []
    for w in workouts:
        start = w.get("start_time")
        if not start:
            continue
        try:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt < cutoff:
            continue
        end = w.get("end_time")
        mins = None
        if end:
            try:
                dt_end = datetime.fromisoformat(end.replace("Z", "+00:00"))
                mins = int((dt_end - dt).total_seconds() / 60)
            except ValueError:
                pass
        rows.append({
            "date": dt.date().isoformat(),
            "title": w.get("title", "?"),
            "min": mins,
            "sets": sum(len(ex.get("sets", [])) for ex in w.get("exercises", [])),
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No Hevy workouts in the last 7 days.")
except Exception as e:
    st.warning(f"Hevy error: {e}")


# ── Cardio ───────────────────────────────────────────────────────────────────
st.subheader("Cardio (7d, Strava)")
try:
    from hevy_workout_ai.strava_client import list_recent_activities_with_calories

    acts = list_recent_activities_with_calories(days=7)
    if acts:
        rows = []
        for a in acts:
            rows.append({
                "date": (a.get("start_date_local") or "")[:10],
                "type": a.get("type", "?"),
                "name": (a.get("name") or "")[:40],
                "min": int(a.get("moving_time", 0) / 60),
                "kcal": a.get("calories"),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No Strava activities in the last 7 days.")
except Exception as e:
    st.warning(f"Strava not connected — run `hevy strava-auth`. ({e})")
