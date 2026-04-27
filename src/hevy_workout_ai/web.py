"""Streamlit dashboard — web equivalent of `hevy dashboard`."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import altair as alt
import pandas as pd
import streamlit as st

# Streamlit Cloud injects secrets via st.secrets, not env vars. Promote both
# an [env] table and flat top-level scalar keys into os.environ so the Store
# + API clients pick them up regardless of how secrets.toml is structured.
try:
    _secrets = dict(st.secrets)
    _env_table = _secrets.pop("env", None)
    if _env_table:
        for _k, _v in dict(_env_table).items():
            os.environ.setdefault(_k, str(_v))
    for _k, _v in _secrets.items():
        if isinstance(_v, (str, int, float, bool)):
            os.environ.setdefault(_k, str(_v))
except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
    pass

from hevy_workout_ai.config import load_profile
from hevy_workout_ai.nutrition import (
    _load_log,
    estimate_maintenance,
    get_nutrition_adjustment,
    is_lifting_day_today,
)
from hevy_workout_ai.recovery import get_recovery_adjustment


# ── Page setup & theme ───────────────────────────────────────────────────────
st.set_page_config(page_title="Fitness Dashboard", page_icon="📊", layout="wide")


# Pull fresh data on each page load (once per session — survives reruns).
# MF sync depends on macOS-only tools (`security`, `node`); skip on cloud
# deploys where a separate Mac launchd job pushes nutrition into Supabase.
if "_synced_at_load" not in st.session_state and os.environ.get("MF_SYNC_ENABLED", "1") == "1":
    st.session_state["_synced_at_load"] = True
    try:
        from hevy_workout_ai.mf_sync import sync_nutrition
        sync_nutrition(days=3)
    except Exception as _e:
        st.warning(f"MacroFactor sync failed: {_e}")

st.markdown(
    """
    <style>
      /* Claude-inspired warm palette */
      :root {
        --bg: #FAF9F5;
        --surface: #F5F1E8;
        --ink: #1F1E1D;
        --muted: #6B6861;
        --line: #E8E2D3;
        --accent: #D97757;
      }
      html, body, [data-testid="stAppViewContainer"], .stApp {
        background: var(--bg) !important;
        color: var(--ink);
        font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      }
      #MainMenu, footer, header[data-testid="stHeader"] { visibility: hidden; height: 0; }
      .block-container {
        padding-top: 2.5rem;
        padding-bottom: 4rem;
        max-width: 1240px;
      }
      h1, h2, h3, h4 {
        font-family: "Tiempos Text", "Charter", Georgia, ui-serif, serif;
        letter-spacing: -0.015em;
        color: var(--ink);
        font-weight: 500;
      }
      h4 { margin-top: 0.5rem; margin-bottom: 0.25rem; font-size: 1.15rem; }
      h5 {
        font-family: "Tiempos Text", "Charter", Georgia, ui-serif, serif;
        font-weight: 500;
        font-size: 0.95rem;
        color: var(--ink);
        margin: 1.25rem 0 0.35rem 0;
        letter-spacing: -0.005em;
      }
      .section-sub {
        color: var(--muted);
        font-size: 0.85rem;
        margin: -0.25rem 0 0.75rem 0;
      }
      p, .stCaption, [data-testid="stCaptionContainer"] {
        color: var(--muted);
      }
      [data-testid="stMetric"] {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 10px;
        padding: 14px 18px;
      }
      [data-testid="stMetricValue"] {
        font-family: "Tiempos Text", "Charter", Georgia, ui-serif, serif;
        font-size: 1.7rem;
        font-weight: 500;
        color: var(--ink);
        letter-spacing: -0.01em;
      }
      [data-testid="stMetricLabel"] {
        font-weight: 500;
        font-size: 0.78rem;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: var(--muted);
      }
      [data-testid="stMetricDelta"] { color: var(--muted) !important; font-size: 0.82rem; }
      hr { margin: 2rem 0 !important; border-color: var(--line) !important; opacity: 1; }
      /* Tabs */
      [data-baseweb="tab-list"] {
        gap: 1.5rem;
        border-bottom: 1px solid var(--line);
      }
      [data-baseweb="tab"] {
        background: transparent !important;
        color: var(--muted) !important;
        padding: 0.4rem 0 !important;
        font-size: 0.95rem;
      }
      [data-baseweb="tab"][aria-selected="true"] {
        color: var(--ink) !important;
        font-weight: 500;
      }
      [data-baseweb="tab-highlight"] { background: var(--accent) !important; height: 2px !important; }
      /* Radio pill group */
      [data-testid="stRadio"] > div { gap: 0.25rem; }
      [data-testid="stRadio"] label {
        background: transparent;
        border: 1px solid var(--line);
        border-radius: 999px;
        padding: 3px 12px !important;
        font-size: 0.82rem;
        color: var(--muted);
      }
      /* Alerts */
      [data-testid="stAlert"] {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 8px;
        color: var(--muted);
      }
      /* Dataframe */
      [data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 8px; }
      /* Header pill */
      .day-pill {
        display: inline-block; background: var(--surface); border: 1px solid var(--line);
        border-radius: 999px; padding: 2px 10px; font-size: 0.72rem; letter-spacing: 0.06em;
        text-transform: uppercase; color: var(--accent); margin-left: 10px; vertical-align: middle;
        font-weight: 500;
      }
      .header-meta { color: var(--muted); font-size: 0.85rem; text-align: right; padding-top: 0.6rem; }
      /* Action banner (top) + Rationale banner (below) */
      .action-banner {
        background: var(--surface); border: 1px solid var(--line);
        border-left: 4px solid var(--accent); border-radius: 10px;
        padding: 18px 22px; margin: 0.4rem 0 0.5rem 0;
      }
      .action-banner .eyebrow-call {
        display: block; font-size: 0.7rem; letter-spacing: 0.1em;
        text-transform: uppercase; color: var(--accent); font-weight: 600;
        margin-bottom: 8px;
      }
      .action-banner .call {
        font-family: "Tiempos Text", "Charter", Georgia, ui-serif, serif;
        font-size: 1.5rem; font-weight: 500; color: var(--ink);
        letter-spacing: -0.015em; line-height: 1.3;
      }
      .rationale-banner {
        background: transparent; border: 1px solid var(--line); border-radius: 10px;
        padding: 10px 18px; margin: 0 0 1rem 0;
        color: var(--muted); font-size: 0.88rem; line-height: 1.5;
      }
      .rationale-banner .label {
        color: var(--muted); font-weight: 600; text-transform: uppercase;
        font-size: 0.65rem; letter-spacing: 0.08em; margin-right: 8px;
      }
      /* Primary button = terracotta */
      .stButton button[kind="primary"] {
        background: var(--accent) !important; border-color: var(--accent) !important;
        color: #FAF9F5 !important; border-radius: 8px !important; font-weight: 500;
      }
      .stButton button[kind="primary"]:hover {
        background: #C76A4D !important; border-color: #C76A4D !important;
      }
      /* Section eyebrow */
      .eyebrow {
        font-size: 0.72rem; letter-spacing: 0.08em; text-transform: uppercase;
        color: var(--muted); margin: 0 0 0.5rem 2px; font-weight: 500;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# Warm, muted palette matching Claude's brand
PALETTE = {
    "weight": "#6B6861",
    "calories": "#D97757",   # accent terracotta
    "protein": "#8A9A5B",    # muted sage
    "steps": "#C89F5F",      # warm tan
    "target": "#C7BFA8",     # faded khaki
}

alt.themes.enable("default")


def _day_axis(dates: list[date]) -> alt.X:
    return alt.X(
        "day:O",
        title=None,
        axis=alt.Axis(
            labelAngle=0, labelFontSize=11, tickColor="transparent",
            domainColor="#E8E2D3", labelColor="#6B6861",
        ),
        sort=[d.isoformat() for d in dates],
    )


def _clean_y(title: str) -> alt.Y:
    return alt.Y(
        alt.repeat("row") if False else alt.Undefined,
        title=title,
        axis=alt.Axis(
            labelFontSize=11, tickCount=4, grid=True, gridColor="#EDE7D8",
            domainOpacity=0, tickOpacity=0, labelColor="#6B6861", titleColor="#6B6861",
            titleFontWeight="normal", titleFontSize=11,
        ),
    )


def _bar(df: pd.DataFrame, y_field: str, color: str, target: float | None, y_title: str) -> alt.Chart:
    base = alt.Chart(df).encode(x=_day_axis(df["day_date"].tolist()))
    bars = base.mark_bar(size=22, cornerRadiusTopLeft=4, cornerRadiusTopRight=4, color=color).encode(
        y=alt.Y(f"{y_field}:Q", title=y_title, axis=alt.Axis(
            labelFontSize=11, tickCount=4, grid=True, gridColor="#EDE7D8",
            domainOpacity=0, tickOpacity=0, labelColor="#6B6861", titleColor="#6B6861",
            titleFontWeight="normal", titleFontSize=11,
        )),
        tooltip=[alt.Tooltip("day:O", title="Date"), alt.Tooltip(f"{y_field}:Q", title=y_title, format=",.0f")],
    )
    layers = [bars]
    if target is not None:
        rule_df = pd.DataFrame({"target": [target]})
        rule = alt.Chart(rule_df).mark_rule(
            color=PALETTE["target"], strokeDash=[4, 3], size=1.5,
        ).encode(y="target:Q")
        layers.append(rule)
    return alt.layer(*layers).properties(height=200).configure_view(strokeWidth=0)


def _line(df: pd.DataFrame, y_field: str, color: str, y_title: str) -> alt.Chart:
    sub = df.dropna(subset=[y_field])
    base = alt.Chart(sub).encode(x=_day_axis(df["day_date"].tolist()))
    line = base.mark_line(color=color, strokeWidth=2.5, interpolate="monotone").encode(
        y=alt.Y(f"{y_field}:Q", scale=alt.Scale(zero=False, nice=True), title=y_title,
                axis=alt.Axis(labelFontSize=11, tickCount=4, grid=True,
                              gridColor="#EDE7D8", domainOpacity=0, tickOpacity=0,
                              labelColor="#6B6861", titleColor="#6B6861",
                              titleFontWeight="normal", titleFontSize=11)),
    )
    dots = base.mark_circle(color=color, size=70).encode(
        y=f"{y_field}:Q",
        tooltip=[alt.Tooltip("day:O", title="Date"),
                 alt.Tooltip(f"{y_field}:Q", title=y_title, format=".1f")],
    )
    return alt.layer(line, dots).properties(height=200).configure_view(strokeWidth=0)


# ── Header ───────────────────────────────────────────────────────────────────
today = date.today()
today_ord = today.toordinal()
days = [date.fromordinal(today_ord - 6 + i) for i in range(7)]

profile = load_profile()
adj = get_recovery_adjustment()
nut = get_nutrition_adjustment()
lift = is_lifting_day_today()
bw = nut.bodyweight_today or profile.get("bodyweight_lb")
maint = estimate_maintenance()

day_label = "LIFT" if lift else "REST"
_meta_bits = []
_meta_bits.append(f"TDEE {maint:.0f} kcal" if maint else "TDEE —")
if bw:
    _meta_bits.append(f"Weight {bw:.1f} lb")

_h_l, _h_r = st.columns([3, 2])
with _h_l:
    st.markdown(
        f"### {today.strftime('%A, %B %-d')}"
        f"<span class='day-pill'>{day_label} day</span>",
        unsafe_allow_html=True,
    )
with _h_r:
    st.markdown(f"<div class='header-meta'>{' · '.join(_meta_bits)}</div>",
                unsafe_allow_html=True)


# ── Today's call (always visible above tabs) ────────────────────────────────
from hevy_workout_ai.recommender import recommend as _recommend
from hevy_workout_ai.training_load import current_state as _current_tsb


@st.cache_data(ttl=300)
def _tsb_value():
    pts = []
    try:
        from hevy_workout_ai.strava_client import list_load_points as _spts
        pts.extend(_spts(120))
    except Exception:
        pass
    try:
        from hevy_workout_ai.hevy_load import list_load_points as _hpts
        pts.extend(_hpts(120))
    except Exception:
        pass
    st_ = _current_tsb(pts)
    if st_ is None:
        return None, None
    return st_.tsb, st_.ctl


_no_gym = st.session_state.get("no_gym_flag", False)
_override = st.session_state.get("override_train", False)
_tsb_now, _ctl_now = _tsb_value()
_verdict = _recommend(tsb=_tsb_now, ctl=_ctl_now, no_gym=_no_gym)
if _override and _verdict.call != "weights":
    from hevy_workout_ai.recommender import Verdict as _Verdict
    _orig_reason = _verdict.rationale
    _verdict = _Verdict(
        call="weights",
        headline="Weights (override)",
        rationale=f"Overridden — system flagged: {_orig_reason}",
        prescription="Train as planned (auto-recommendation overridden).",
        push_routine=True,
        dumbbell_only=False,
    )
_emoji = {"recovery_day": "🛌", "zone2_bike": "🚴", "dumbbell_day": "🏋️", "weights": "💪"}.get(_verdict.call, "•")

st.markdown(
    f"<div class='action-banner'>"
    f"<span class='eyebrow-call'>Today's call · {_verdict.headline}</span>"
    f"<div class='call'>{_emoji} {_verdict.prescription}</div>"
    f"</div>"
    f"<div class='rationale-banner'>"
    f"<span class='label'>Why</span>{_verdict.rationale}"
    f"</div>",
    unsafe_allow_html=True,
)

_act_l, _act_r = st.columns([2, 3])
with _act_l:
    if _verdict.push_routine:
        if st.button("Push to Hevy", key="push_routine_btn", type="primary"):
            from hevy_workout_ai.generator import generate_routine as _gen
            from hevy_workout_ai.hevy_client import create_routine as _create
            try:
                _routine = _gen(dumbbell_only=_verdict.dumbbell_only)
                _resp = _create(_routine)
                _created = _resp.get("routine", _resp)
                if isinstance(_created, list):
                    _created = _created[0]
                st.success(f"Pushed: {_routine['routine']['title']} (id {_created.get('id', '?')})")
            except Exception as e:
                st.error(f"Push failed: {e}")
with _act_r:
    st.toggle("Can't get to the gym today", key="no_gym_flag")
    st.toggle("Override → train as planned", key="override_train",
              help="Force a full lifting session even if the system recommends recovery/zone-2.")


# ── Metric tiles (always visible) ────────────────────────────────────────────
entries = _load_log()
win_entries = [
    e for e in entries
    if (today_ord - 6) <= date.fromisoformat(str(e["date"])).toordinal() <= today_ord
]
grid = pd.DataFrame({"day_date": days, "day": [d.isoformat() for d in days]})
if win_entries:
    raw = pd.DataFrame(win_entries)
    raw["day_date"] = pd.to_datetime(raw["date"]).dt.date
    df = grid.merge(raw.drop(columns=["date"]), on="day_date", how="left")
else:
    df = grid.copy()

steps_today = None
if "steps" in df.columns:
    v = df.loc[df["day_date"] == today, "steps"]
    if len(v) and pd.notna(v.iloc[0]):
        steps_today = int(v.iloc[0])

st.markdown("<div class='eyebrow'>Today</div>", unsafe_allow_html=True)
c1, c2, c3, c4, c5 = st.columns(5)
from hevy_workout_ai.doms import get_doms_state as _get_doms_for_tile
_doms_for_tile = _get_doms_for_tile()
def _doms_band_mult(s: float) -> float:
    if s < 25: return 1.0
    if s < 50: return 0.95
    if s < 75: return 0.85
    return 1.0  # severe → skipped entirely, not derated
_doms_mults = [_doms_band_mult(s) for s in _doms_for_tile.scores.values() if s > 0]
_doms_mult = min(_doms_mults) if _doms_mults else 1.0
_parts = [adj.band, f"Whoop x{adj.load_mult}"]
if _doms_mult < 1.0:
    _parts.append(f"DOMS x{_doms_mult:.2f}")
c1.metric("Recovery", f"{adj.score:.0f}", " · ".join(_parts), delta_color="off")

if nut.protein_target_g:
    intake = nut.protein_today or 0
    c2.metric("Protein", f"{intake:.0f} / {nut.protein_target_g:.0f} g",
              f"{max(0, nut.protein_target_g - intake):.0f} g to go",
              delta_color="inverse")
else:
    c2.metric("Protein", "—")

if nut.calories_today is not None and nut.calorie_target_kcal is not None:
    c3.metric("Calories", f"{nut.calories_today:.0f} / {nut.calorie_target_kcal:.0f}",
              f"{nut.calorie_gap_kcal:+.0f} kcal", delta_color="off")
elif nut.calories_today is not None:
    c3.metric("Calories", f"{nut.calories_today:.0f}")
else:
    c3.metric("Calories", "—")

if nut.fiber_target_g:
    fi = nut.fiber_today or 0
    c4.metric("Fiber", f"{fi:.0f} / {nut.fiber_target_g:.0f} g",
              f"{max(0, nut.fiber_target_g - fi):.0f} g to go",
              delta_color="inverse")
else:
    c4.metric("Fiber", "—")

c5.metric("Steps", f"{steps_today:,}" if steps_today is not None else "—",
          "8,000 goal", delta_color="off")

st.markdown("---")


_show_coach = os.environ.get("SHOW_COACH", "0") == "1"
if _show_coach:
    tab_fitness, tab_nutrition, tab_coach = st.tabs(["Fitness", "Nutrition", "Coach"])
else:
    tab_fitness, tab_nutrition = st.tabs(["Fitness", "Nutrition"])
    tab_coach = None


with tab_fitness:
    fit_readiness, fit_activity = st.tabs(["Readiness", "Activity"])

with fit_readiness:
    # ── DOMS ──────────────────────────────────────────────────────────────────
    st.markdown("#### Soreness (DOMS)")
    from hevy_workout_ai.doms import GROUPS as _DOMS_GROUPS, get_doms_state as _get_doms, log_doms as _log_doms
    _doms_state = _get_doms()

    def _on_doms_change():
        _log_doms({g: int(st.session_state[f"doms_{g}"]) for g in _DOMS_GROUPS})

    _cols = st.columns(len(_DOMS_GROUPS))
    for _i, _g in enumerate(_DOMS_GROUPS):
        with _cols[_i]:
            st.slider(
                _g.capitalize(),
                0, 100,
                int(_doms_state.scores.get(_g, 0)),
                step=5,
                key=f"doms_{_g}",
                on_change=_on_doms_change,
            )
    st.markdown(f'<p class="section-sub">Current (decayed): {_doms_state.summary() or "all clear"}. Scores decay linearly to 0 over 72h.</p>', unsafe_allow_html=True)

    # ── Training load ─────────────────────────────────────────────────────────
    st.markdown("#### Training load · Fitness / Fatigue / Form")

    @st.cache_data(ttl=900)
    def _load_training_points(days: int = 120):
        from hevy_workout_ai.training_load import LoadPoint
        pts: list[LoadPoint] = []
        errors = []
        try:
            from hevy_workout_ai.strava_client import list_load_points as _spts
            pts.extend(_spts(days))
        except Exception as e:
            errors.append(f"Strava: {e}")
        try:
            from hevy_workout_ai.peloton_client import list_load_points as _ppts
            pts.extend(_ppts(days))
        except Exception as e:
            errors.append(f"Peloton: {e}")
        return pts, errors

    tl_pts, tl_errs = _load_training_points(120)
    if tl_pts:
        from hevy_workout_ai.training_load import compute_series
        series = compute_series(tl_pts)
        latest = series[-1]

        tlc1, tlc2, tlc3 = st.columns(3)
        tlc1.metric("Fitness (CTL)", f"{latest.ctl:.0f}", help="42-day weighted avg of load")
        tlc2.metric("Fatigue (ATL)", f"{latest.atl:.0f}", help="7-day weighted avg of load")
        tlc3.metric("Form (TSB)", f"{latest.tsb:+.0f}",
                  help="CTL − ATL. Positive = fresh, negative = fatigued.")

        if latest.tsb >= 10:
            st.markdown('<p class="section-sub">Fresh — ready to push.</p>', unsafe_allow_html=True)
        elif latest.tsb >= -10:
            st.markdown('<p class="section-sub">Neutral — maintain.</p>', unsafe_allow_html=True)
        elif latest.tsb >= -20:
            st.markdown('<p class="section-sub">Fatigued — consider an easier week.</p>', unsafe_allow_html=True)
        else:
            st.markdown('<p class="section-sub">Deeply fatigued — deload.</p>', unsafe_allow_html=True)

        df_tl = pd.DataFrame([
            {"Date": d.day, "Fitness": d.ctl, "Fatigue": d.atl, "Form": d.tsb}
            for d in series
        ])
        melted = df_tl.melt("Date", var_name="Metric", value_name="Value")
        tl_chart = (
            alt.Chart(melted)
            .mark_line(strokeWidth=2)
            .encode(
                x=alt.X("Date:T", axis=alt.Axis(title=None)),
                y=alt.Y("Value:Q", axis=alt.Axis(title=None)),
                color=alt.Color("Metric:N", legend=alt.Legend(title=None, orient="top")),
            )
            .properties(height=220)
        )
        st.altair_chart(tl_chart, use_container_width=True)
    else:
        st.info("No training-load data yet. " + " · ".join(tl_errs) if tl_errs else "No data.")

with fit_readiness:
    # ── Recovery / HRV / Sleep ────────────────────────────────────────────────
    from hevy_workout_ai.whoop_log import load_log as _load_whoop

    whoop_entries = _load_whoop()
    if whoop_entries:
        st.markdown("#### Recovery, HRV & sleep")
        whoop_range = st.radio(
            "whoop_range", [14, 30, 60, 90], index=2, horizontal=True,
            format_func=lambda n: f"{n}d", label_visibility="collapsed",
        )
        wdf_all = pd.DataFrame(whoop_entries)
        wdf_all["date"] = pd.to_datetime(wdf_all["date"])
        cutoff_w = pd.Timestamp(today) - pd.Timedelta(days=whoop_range)
        wdf_all = wdf_all[wdf_all["date"] >= cutoff_w].sort_values("date")

        def _simple_line(df: pd.DataFrame, y_field: str, color: str, y_title: str,
                         y_domain=None, area: bool = False,
                         extra_tooltip: list | None = None) -> alt.Chart:
            sub = df.dropna(subset=[y_field])
            y_scale = alt.Scale(domain=y_domain) if y_domain else alt.Scale(zero=False, nice=True)
            base = alt.Chart(sub).encode(
                x=alt.X("date:T", title=None, axis=alt.Axis(
                    format="%b %d", labelFontSize=11, grid=False,
                    domainOpacity=0, tickOpacity=0, labelColor="#6B6861")),
            )
            y_enc = alt.Y(f"{y_field}:Q", title=y_title, scale=y_scale,
                          axis=alt.Axis(labelFontSize=11, tickCount=5, grid=True,
                                        gridColor="#EDE7D8", domainOpacity=0,
                                        tickOpacity=0, labelColor="#6B6861",
                                        titleColor="#6B6861",
                                        titleFontWeight="normal", titleFontSize=11))
            tooltip = [alt.Tooltip("date:T", title="Date", format="%b %d"),
                       alt.Tooltip(f"{y_field}:Q", title=y_title, format=".1f")]
            if extra_tooltip:
                tooltip.extend(extra_tooltip)
            layers = []
            if area:
                layers.append(base.mark_area(color=color, opacity=0.15,
                                             interpolate="monotone").encode(y=y_enc))
            layers.append(base.mark_line(color=color, strokeWidth=2.5,
                                         interpolate="monotone").encode(y=y_enc, tooltip=tooltip))
            return alt.layer(*layers).properties(height=220).configure_view(strokeWidth=0)

        st.markdown("##### Recovery score")
        st.markdown('<p class="section-sub">Daily Whoop recovery (0–100)</p>', unsafe_allow_html=True)
        if wdf_all["recovery_score"].notna().any():
            st.altair_chart(
                _simple_line(wdf_all, "recovery_score", "#8A9A5B", "Recovery %", area=True),
                use_container_width=True)
        else:
            st.info("No recovery data.")

        st.markdown("##### HRV")
        st.markdown('<p class="section-sub">Daily points · 14-day rolling mean · dashed = window average</p>', unsafe_allow_html=True)
        if wdf_all["hrv_ms"].notna().any():
            hdf = wdf_all[["date", "hrv_ms"]].dropna().copy()
            hdf["hrv_trend"] = hdf["hrv_ms"].rolling(window=14, min_periods=3).mean()
            baseline_h = hdf["hrv_ms"].mean()
            base_h = alt.Chart(hdf).encode(
                x=alt.X("date:T", title=None, axis=alt.Axis(
                    format="%b %d", labelFontSize=11, grid=False,
                    domainOpacity=0, tickOpacity=0, labelColor="#6B6861")),
            )
            y_h = alt.Y("hrv_trend:Q", title="HRV (ms)",
                        scale=alt.Scale(zero=False, nice=True),
                        axis=alt.Axis(labelFontSize=11, tickCount=5, grid=True,
                                      gridColor="#EDE7D8", domainOpacity=0,
                                      tickOpacity=0, labelColor="#6B6861",
                                      titleColor="#6B6861",
                                      titleFontWeight="normal", titleFontSize=11))
            dots = base_h.mark_circle(color="#B97A8A", size=18, opacity=0.25).encode(
                y=alt.Y("hrv_ms:Q"),
                tooltip=[alt.Tooltip("date:T", title="Date", format="%b %d"),
                         alt.Tooltip("hrv_ms:Q", title="HRV", format=".1f")],
            )
            trend = base_h.mark_line(color="#B97A8A", strokeWidth=2.5,
                                     interpolate="monotone").encode(y=y_h)
            baseline = alt.Chart(pd.DataFrame({"b": [baseline_h]})).mark_rule(
                color=PALETTE["target"], strokeDash=[4, 3], size=1.5).encode(y="b:Q")
            st.altair_chart(
                alt.layer(dots, trend, baseline).properties(height=220).configure_view(strokeWidth=0),
                use_container_width=True,
            )
        else:
            st.info("No HRV data.")

        st.markdown("##### Sleep")
        st.markdown('<p class="section-sub">Hours slept · dashed line = sleep need</p>', unsafe_allow_html=True)
        if wdf_all["sleep_hours"].notna().any():
            sdf = wdf_all.dropna(subset=["sleep_hours"]).copy()
            base = alt.Chart(sdf).encode(
                x=alt.X("date:T", title=None, axis=alt.Axis(
                    format="%b %d", labelFontSize=11, grid=False,
                    domainOpacity=0, tickOpacity=0, labelColor="#6B6861")),
            )
            y_hours = alt.Y("sleep_hours:Q", title="Hours",
                            scale=alt.Scale(zero=False, nice=True),
                            axis=alt.Axis(labelFontSize=11, tickCount=6, grid=True,
                                          gridColor="#EDE7D8", domainOpacity=0,
                                          tickOpacity=0, labelColor="#6B6861",
                                          titleColor="#6B6861",
                                          titleFontWeight="normal", titleFontSize=11))
            bars = base.mark_bar(color="#7C9BB5", size=6, opacity=0.85).encode(
                y=y_hours,
                tooltip=[alt.Tooltip("date:T", title="Date", format="%b %d"),
                         alt.Tooltip("sleep_hours:Q", title="Slept", format=".1f"),
                         alt.Tooltip("sleep_need_hours:Q", title="Needed", format=".1f"),
                         alt.Tooltip("sleep_debt_hours:Q", title="Debt", format=".1f")],
            )
            need_line = base.transform_filter("datum.sleep_need_hours != null").mark_line(
                color="#C89F5F", strokeWidth=2, interpolate="monotone", strokeDash=[4, 3],
            ).encode(y="sleep_need_hours:Q")
            st.altair_chart(
                alt.layer(bars, need_line).properties(height=220).configure_view(strokeWidth=0),
                use_container_width=True,
            )
        else:
            st.info("No sleep data.")

with fit_activity:
    # ── Steps · 7d ────────────────────────────────────────────────────────────
    st.markdown("#### Steps · last 7 days")
    STEP_GOAL = 8000
    if "steps" in df.columns and df["steps"].notna().any():
        st.altair_chart(_bar(df, "steps", PALETTE["steps"], STEP_GOAL, "steps"),
                        use_container_width=True)
    else:
        st.info("No step data.")

    # ── Activity log ──────────────────────────────────────────────────────────
    st.markdown("#### Activity · last 7 days")
    act_l, act_c = st.columns(2)

    with act_l:
        st.markdown("##### Lifting")
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
                    "Date": dt.date().isoformat(),
                    "Workout": w.get("title", "?"),
                    "Min": mins,
                    "Sets": sum(len(ex.get("sets", [])) for ex in w.get("exercises", [])),
                })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                st.info("No Hevy workouts in the last 7 days.")
        except Exception as e:
            st.warning(f"Hevy error: {e}")

    with act_c:
        st.markdown("##### Cardio")
        try:
            from hevy_workout_ai.strava_client import list_recent_activities_with_calories

            @st.cache_data(ttl=900)
            def _strava_recent(days: int):
                return list_recent_activities_with_calories(days=days)

            acts = _strava_recent(7)
            if acts:
                rows = []
                for a in acts:
                    rows.append({
                        "Date": (a.get("start_date_local") or "")[:10],
                        "Type": a.get("type", "?"),
                        "Name": (a.get("name") or "")[:30],
                        "Min": int(a.get("moving_time", 0) / 60),
                        "Kcal": a.get("calories"),
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                st.info("No Strava activities in the last 7 days.")
        except Exception as e:
            msg = str(e)
            if "429" in msg:
                st.info("Strava rate-limited (429). Try again in ~15 min.")
            else:
                st.warning(f"Strava not connected — run `hevy strava-auth`. ({e})")

with fit_activity:
    # ── Exercise progression ──────────────────────────────────────────────────
    st.markdown("#### Exercise progression · last 90 days")

    @st.cache_data(ttl=300)
    def _load_progression(days: int = 90) -> pd.DataFrame:
        from hevy_workout_ai.hevy_client import list_workouts

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows: list[dict] = []
        seen: set[str] = set()
        page = 1
        while page <= 50:
            data = list_workouts(page=page, page_size=10)
            ws = data.get("workouts", []) if isinstance(data, dict) else []
            if not ws:
                break
            stop = False
            for w in ws:
                wid = w.get("id") or ""
                if wid in seen:
                    continue
                seen.add(wid)
                start = w.get("start_time")
                if not start:
                    continue
                try:
                    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if dt < cutoff:
                    stop = True
                    continue
                for ex in w.get("exercises", []):
                    title = ex.get("title") or "?"
                    for s in ex.get("sets", []):
                        if s.get("type") == "warmup":
                            continue
                        kg = s.get("weight_kg")
                        reps = s.get("reps")
                        if not kg or not reps:
                            continue
                        lb = kg * 2.20462
                        e1rm = lb if reps <= 1 else lb * (1 + reps / 30.0)
                        rows.append({
                            "date": dt.date(),
                            "exercise": title,
                            "weight_lbs": lb,
                            "reps": int(reps),
                            "e1rm_lbs": e1rm,
                            "volume_lbs": lb * int(reps),
                        })
            if stop:
                break
            page += 1
        return pd.DataFrame(rows)

    try:
        prog = _load_progression(90)
        if prog.empty:
            st.info("No lifting history in the last 90 days.")
        else:
            per_day = (
                prog.groupby(["exercise", "date"], as_index=False)
                .agg(
                    top_weight_lbs=("weight_lbs", "max"),
                    best_e1rm_lbs=("e1rm_lbs", "max"),
                    volume_lbs=("volume_lbs", "sum"),
                    working_sets=("reps", "count"),
                )
                .sort_values(["exercise", "date"])
            )

            ex_counts = per_day.groupby("exercise").size().sort_values(ascending=False)
            default_ex = ex_counts.index[0] if len(ex_counts) else None
            options = ex_counts.index.tolist()

            picks = st.multiselect(
                "Exercises", options, default=[default_ex] if default_ex else [],
                max_selections=5,
            )

            if picks:
                sub = per_day[per_day["exercise"].isin(picks)].copy()
                sub["date"] = pd.to_datetime(sub["date"])

                _prog_metric = st.radio(
                    "prog_metric",
                    ["Estimated 1RM", "Top set", "Volume"],
                    index=0, horizontal=True, label_visibility="collapsed",
                )

                def _line_chart(field: str, title: str) -> alt.Chart:
                    return (
                        alt.Chart(sub)
                        .mark_line(strokeWidth=2.5, interpolate="monotone", point=alt.OverlayMarkDef(size=70))
                        .encode(
                            x=alt.X("date:T", title=None, axis=alt.Axis(
                                format="%b %d", labelFontSize=11, tickColor="transparent",
                                domainColor="#E8E2D3", labelColor="#6B6861", grid=False)),
                            y=alt.Y(f"{field}:Q", title=title, scale=alt.Scale(zero=False),
                                    axis=alt.Axis(labelFontSize=11, tickCount=5, grid=True,
                                                  gridColor="#EDE7D8", domainOpacity=0,
                                                  tickOpacity=0, labelColor="#6B6861",
                                                  titleColor="#6B6861", titleFontWeight="normal",
                                                  titleFontSize=11)),
                            color=alt.Color("exercise:N", legend=alt.Legend(
                                title=None, orient="top", labelFontSize=11)),
                            tooltip=[
                                alt.Tooltip("exercise:N", title="Exercise"),
                                alt.Tooltip("date:T", title="Date", format="%b %d"),
                                alt.Tooltip(f"{field}:Q", title=title, format=".1f"),
                            ],
                        )
                        .properties(height=260)
                        .configure_view(strokeWidth=0)
                    )

                _field_map = {
                    "Estimated 1RM": ("best_e1rm_lbs", "e1RM (lb)"),
                    "Top set": ("top_weight_lbs", "Top weight (lb)"),
                    "Volume": ("volume_lbs", "Volume (lb)"),
                }
                _f, _t = _field_map[_prog_metric]
                st.altair_chart(_line_chart(_f, _t), use_container_width=True)
            else:
                st.caption("Pick one or more exercises above to view progression.")
    except Exception as e:
        st.warning(f"Progression error: {e}")


with tab_nutrition:
    # ── Weight trend ──────────────────────────────────────────────────────────
    st.markdown("#### Weight trend")
    weight_range = st.radio(
        "weight_range", [30, 90, 180, 365], index=1, horizontal=True,
        format_func=lambda n: f"{n}d", label_visibility="collapsed",
    )

    all_entries = [e for e in entries if e.get("bodyweight_lb") is not None]
    if all_entries:
        wdf = pd.DataFrame(all_entries)[["date", "bodyweight_lb"]]
        wdf["date"] = pd.to_datetime(wdf["date"])
        cutoff = pd.Timestamp(today) - pd.Timedelta(days=weight_range)
        wdf = wdf[wdf["date"] >= cutoff].sort_values("date")
        smooth_window = 21 if weight_range >= 180 else (14 if weight_range >= 60 else 7)
        trend_df = (
            wdf.set_index("date")["bodyweight_lb"]
            .asfreq("D").interpolate("linear")
            .rolling(window=smooth_window, min_periods=3, center=True).mean()
            .reset_index()
            .rename(columns={"bodyweight_lb": "trend_lb"})
            .dropna()
        )

        goal_w = profile.get("goal_weight_lb")
        base = alt.Chart(wdf).encode(
            x=alt.X("date:T", axis=alt.Axis(labelFontSize=11, grid=False,
                                             domainOpacity=0, tickOpacity=0,
                                             labelColor="#6B6861", title=None)),
        )
        dots = base.mark_circle(color=PALETTE["weight"], size=50, opacity=0.55).encode(
            y=alt.Y("bodyweight_lb:Q", scale=alt.Scale(zero=False, nice=True), title="lb",
                    axis=alt.Axis(labelFontSize=11, tickCount=5, grid=True,
                                  gridColor="#EDE7D8", domainOpacity=0, tickOpacity=0,
                                  labelColor="#6B6861", titleColor="#6B6861",
                                  titleFontWeight="normal", titleFontSize=11)),
            tooltip=[alt.Tooltip("date:T", title="Date"),
                     alt.Tooltip("bodyweight_lb:Q", title="Weight", format=".1f")],
        )
        trend = alt.Chart(trend_df).mark_line(
            color=PALETTE["weight"], strokeWidth=2.5, interpolate="monotone",
        ).encode(x="date:T", y="trend_lb:Q")
        layers = [dots, trend]
        if goal_w:
            goal_rule = alt.Chart(pd.DataFrame({"g": [goal_w]})).mark_rule(
                color=PALETTE["target"], strokeDash=[4, 3], size=1.5,
            ).encode(y="g:Q")
            layers.append(goal_rule)
        chart = alt.layer(*layers).properties(height=240).configure_view(strokeWidth=0)
        st.altair_chart(chart, use_container_width=True)

        last_w = wdf.iloc[-1]["bodyweight_lb"]
        if len(trend_df) >= 2:
            delta = trend_df.iloc[-1]["trend_lb"] - trend_df.iloc[0]["trend_lb"]
        else:
            delta = wdf.iloc[-1]["bodyweight_lb"] - wdf.iloc[0]["bodyweight_lb"]
        trend_last = trend_df.iloc[-1]["trend_lb"] if len(trend_df) else last_w
        mc = st.columns(3)
        mc[0].metric("Latest", f"{last_w:.1f} lb")
        mc[1].metric(f"{weight_range}d change",
                     f"{delta:+.1f} lb",
                     delta_color="inverse" if goal_w and goal_w < last_w else "normal")
        if goal_w:
            mc[2].metric("To goal", f"{trend_last - goal_w:+.1f} lb")
    else:
        st.info("No weight data. Run `hevy sync-garmin-weight`.")

    # ── 7-day macro bars ──────────────────────────────────────────────────────
    st.markdown("#### Calories & protein · last 7 days")
    n_cols = st.columns(2)

    with n_cols[0]:
        st.caption("Calories")
        if "calories_kcal" in df.columns and df["calories_kcal"].notna().any():
            st.altair_chart(
                _bar(df, "calories_kcal", PALETTE["calories"],
                     nut.calorie_target_kcal, "kcal"),
                use_container_width=True)
        else:
            st.info("No calorie data.")

    with n_cols[1]:
        st.caption("Protein")
        if "protein_g" in df.columns and df["protein_g"].notna().any():
            st.altair_chart(
                _bar(df, "protein_g", PALETTE["protein"], nut.protein_target_g, "g"),
                use_container_width=True)
        else:
            st.info("No protein data.")


if tab_coach is not None:
  with tab_coach:
    import asyncio

    from hevy_workout_ai.coach import PROACTIVE_BRIEFING, chat_stream, clear_session

    st.caption(
        "Strength · injury prevention · endurance. Reads Whoop, Hevy, nutrition, Strava — "
        "can also modify Hevy routines/workouts when write mode is on."
    )

    if "coach_history" not in st.session_state:
        st.session_state.coach_history = []

    cc = st.columns([1.2, 1.6, 4, 1.4])
    if cc[0].button("New session"):
        clear_session("dashboard")
        st.session_state.coach_history = []
        st.rerun()
    run_checkin = cc[1].button(
        "Pre-workout check-in",
        help="Autonomous: coach reads recovery/soreness and adjusts today's routine.",
    )
    allow_writes = cc[3].toggle(
        "Allow Hevy writes", value=True,
        help="When on, the coach can create/update routines, workouts, and exercise templates in Hevy.",
    )

    if run_checkin:
        st.session_state.coach_history.append(
            {"role": "user", "content": "*Running pre-workout check-in…*"}
        )
        st.session_state._pending_prompt = PROACTIVE_BRIEFING

    for turn in st.session_state.coach_history:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])
            for t in turn.get("tools", []):
                st.caption(f"↳ used **{t}**")

    prompt = st.chat_input("Ask about your training, recovery, nutrition…")
    if not prompt and st.session_state.get("_pending_prompt"):
        prompt = st.session_state.pop("_pending_prompt")
    if prompt:
        st.session_state.coach_history.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        async def _collect(p: str):
            parts: list[str] = []
            tools: list[str] = []
            async for kind, payload in chat_stream(p, surface="dashboard", allow_writes=allow_writes):
                if kind == "text":
                    parts.append(payload)
                elif kind == "tool_use":
                    tools.append(payload["name"])
                elif kind == "done":
                    final = payload or "".join(parts)
                    return final, tools
            return "".join(parts), tools

        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    text, tools = asyncio.run(_collect(prompt))
                except Exception as e:
                    text, tools = f"Error: {e}", []
            st.markdown(text)
            for t in tools:
                st.caption(f"↳ used **{t}**")

        st.session_state.coach_history.append(
            {"role": "assistant", "content": text, "tools": tools}
        )
