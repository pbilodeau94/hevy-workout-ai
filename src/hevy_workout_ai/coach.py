"""Claude-powered fitness coach.

Three domains: strength/hypertrophy, injury prevention & PT, endurance (Z2 cardio).
Uses Claude Agent SDK which piggybacks on Claude Code auth — no API key required.

Surfaces:
  - `run_once(prompt)`        — one-shot query (CLI `hevy coach <msg>`)
  - `chat_stream(prompt, ...)` — async generator for interactive use
                                 (CLI `hevy coach chat` + Streamlit Coach tab)

Tools exposed to the model:
  - In-process read tools wrapping our own modules (profile, whoop, nutrition,
    recovery, weight trend, Strava).
  - External `hevy-mcp` (npx) for Hevy routines/workouts/exercise templates.

Writes are deferred — allowed_tools currently excludes mutating hevy-mcp tools.
Later step: enable update-routine / create-routine behind a can_use_tool gate.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from . import store
from .config import load_profile, load_state

load_dotenv()


def _log_qa(surface: str, prompt: str, response: str) -> None:
    """Append a Q&A turn to the coach log."""
    try:
        store.append_coach_log({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "surface": surface,
            "prompt": prompt,
            "response": response,
        })
    except Exception:
        pass


# ─── Read-only tool implementations ──────────────────────────────────────────


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _profile_summary() -> str:
    p = load_profile()
    s = load_state()
    t = p["training"]
    return json.dumps({
        "goal_weight_lb": p.get("goal_weight_lb"),
        "avoid_tags": p.get("avoid_tags", []),
        "equipment": p.get("equipment", []),
        "training": {
            "days_per_week": t["days_per_week"],
            "session_duration_minutes": t["session_duration_minutes"],
            "goal": t.get("goal"),
            "experience_level": t.get("experience_level"),
            "current_phase": t.get("current_phase"),
            "sets_per_exercise": t.get("sets_per_exercise"),
            "rpe": t.get("rpe"),
        },
        "block": {
            "current_block": s.get("current_block"),
            "week_in_block": s.get("current_week_in_block"),
            "block_length_weeks": s.get("block_length_weeks"),
            "training_days": s.get("training_days"),
        },
    }, indent=2)


def _whoop_recent(days: int) -> str:
    from . import whoop_log
    log = whoop_log.load_log()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    recent = [e for e in log if str(e.get("date", "")) >= cutoff]
    return json.dumps(recent, indent=2, default=str) if recent else "[]"


def _recovery_today() -> str:
    from .recovery import get_recovery_adjustment
    a = get_recovery_adjustment()
    return json.dumps({
        "score": a.score, "band": a.band, "load_mult": a.load_mult,
        "set_delta": a.set_delta, "source": a.source, "note": a.note,
    }, indent=2)


def _nutrition_recent(days: int) -> str:
    from .nutrition import _load_log, estimate_maintenance, get_nutrition_adjustment
    entries = _load_log()
    cutoff_ord = date.today().toordinal() - days
    window = [e for e in entries if date.fromisoformat(str(e["date"])).toordinal() >= cutoff_ord]
    adj = get_nutrition_adjustment()
    return json.dumps({
        "today": {
            "calories": adj.calories_today, "protein_g": adj.protein_today,
            "fiber_g": adj.fiber_today, "bodyweight_lb": adj.bodyweight_today,
            "protein_target_g": adj.protein_target_g, "is_lifting_day": adj.is_lifting_day,
        },
        "maintenance_kcal": adj.maintenance_kcal or estimate_maintenance(),
        "window": window,
    }, indent=2, default=str)


def _workouts_recent(days: int) -> str:
    from .hevy_client import list_workouts
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    page = 1
    while page <= 10:
        data = list_workouts(page=page, page_size=10)
        workouts = data.get("workouts", []) if isinstance(data, dict) else []
        if not workouts:
            break
        stop = False
        for w in workouts:
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
            exercises = []
            for ex in w.get("exercises", []):
                sets = [
                    {"reps": s.get("reps"), "weight_kg": s.get("weight_kg"),
                     "rpe": s.get("rpe"), "type": s.get("type")}
                    for s in ex.get("sets", [])
                ]
                exercises.append({"title": ex.get("title"), "sets": sets})
            out.append({"date": dt.date().isoformat(), "title": w.get("title"),
                        "exercises": exercises})
        if stop:
            break
        page += 1
    return json.dumps(out, indent=2)


def _strava_recent(days: int) -> str:
    try:
        from .strava_client import list_recent_activities_with_calories
        acts = list_recent_activities_with_calories(days=days)
    except Exception as e:
        return f"Strava unavailable: {e}"
    out = [{
        "date": (a.get("start_date_local") or "")[:10],
        "type": a.get("type"),
        "name": a.get("name"),
        "duration_min": round(a.get("moving_time", 0) / 60),
        "distance_mi": round(a.get("distance", 0) / 1609.34, 2) if a.get("distance") else None,
        "avg_hr": a.get("average_heartrate"),
        "calories": a.get("calories"),
    } for a in acts]
    return json.dumps(out, indent=2)


def _weight_trend(days: int) -> str:
    from .nutrition import _load_log, _slope_lb_per_day, _smooth_weight
    entries = _load_log()
    cutoff_ord = date.today().toordinal() - days
    window = [e for e in entries if date.fromisoformat(str(e["date"])).toordinal() >= cutoff_ord
              and e.get("bodyweight_lb") is not None]
    if not window:
        return "[]"
    smoothed = _smooth_weight(window)
    slope = _slope_lb_per_day(smoothed) if smoothed else 0.0
    return json.dumps({
        "slope_lb_per_week": round(slope * 7, 3),
        "points": [{"date": str(e["date"]), "lb": e["bodyweight_lb"]} for e in window],
    }, indent=2)


# ─── Build SDK server + options ──────────────────────────────────────────────


def _build_tools():
    from claude_agent_sdk import tool

    @tool("get_profile", "User training profile, equipment, goals, limitations, current block/week.", {})
    async def get_profile(_: dict) -> dict:
        return _ok(_profile_summary())

    @tool("get_whoop", "Recent Whoop recovery + sleep log. days=14 default.",
          {"days": int})
    async def get_whoop(args: dict) -> dict:
        return _ok(_whoop_recent(int(args.get("days", 14))))

    @tool("get_recovery_today", "Today's combined recovery adjustment (Whoop + nutrition stacking).", {})
    async def get_recovery_today(_: dict) -> dict:
        return _ok(_recovery_today())

    @tool("get_nutrition", "Nutrition log + maintenance TDEE + today's protein/calorie state.",
          {"days": int})
    async def get_nutrition(args: dict) -> dict:
        return _ok(_nutrition_recent(int(args.get("days", 14))))

    @tool("get_recent_workouts", "Recent lifting workouts from Hevy (dates, exercises, sets, reps, weights, RPE).",
          {"days": int})
    async def get_recent_workouts(args: dict) -> dict:
        return _ok(_workouts_recent(int(args.get("days", 14))))

    @tool("get_cardio", "Recent Strava activities (type, duration, HR, distance, kcal).",
          {"days": int})
    async def get_cardio(args: dict) -> dict:
        return _ok(_strava_recent(int(args.get("days", 14))))

    @tool("get_weight_trend", "Bodyweight trend with smoothed slope in lb/week.",
          {"days": int})
    async def get_weight_trend(args: dict) -> dict:
        return _ok(_weight_trend(int(args.get("days", 30))))

    return [get_profile, get_whoop, get_recovery_today,
            get_nutrition, get_recent_workouts, get_cardio, get_weight_trend]


SYSTEM_PROMPT = """You are a fitness coach for a 3x/week intermediate lifter cutting toward a 145 lb goal weight.

Coverage:
  - Strength / hypertrophy: progressive overload, RPE/RIR autoregulation, phase/block structure.
  - Injury prevention & PT: movement screens, load management, avoiding flares. User has vestibular
    dysfunction — NEVER recommend single-leg or balance work (lunges, split squats, step-ups, SL RDLs).
  - Endurance: Z2 / polarized cardio, HRV-gated intensity, using Whoop recovery to gate hard days.

How to operate:
  - Pull context with the read tools before giving advice. Don't guess weights, sets, or current phase —
    call get_profile, get_recent_workouts, get_recovery_today, etc. The hevy-mcp tools give you live
    routines, exercise templates, and workout history.
  - Tie recommendations to what the data actually shows. If recovery is red, say so; if the last session
    already hit the target, say so.
  - Be concise. Specific reps/sets/weights/time beat vague encouragement. No hype, no emojis.
  - Hevy access mode depends on the surface. When write tools are in your allowed list
    (create-routine, update-routine, create-routine-folder, create-workout, update-workout,
    create-exercise-template), you may modify Hevy data directly. Before a write, briefly state
    what you're about to change and why, then call the tool. Prefer update-routine over
    create-routine when editing an existing plan. If write tools are NOT in your allowed list,
    stay read-only and describe what you would do instead.
"""


def _build_options(
    *,
    session_id: str | None = None,
    permission_callback=None,
    allow_writes: bool = False,
):
    from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server

    coach_server = create_sdk_mcp_server(
        name="coach_data", version="1.0.0", tools=_build_tools(),
    )

    hevy_key = os.environ.get("HEVY_API_KEY", "")

    mcp_servers = {
        "coach_data": coach_server,
        "hevy-mcp": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "hevy-mcp"],
            "env": {"HEVY_API_KEY": hevy_key},
        },
    }

    read_tools = [
        "mcp__coach_data__get_profile",
        "mcp__coach_data__get_whoop",
        "mcp__coach_data__get_recovery_today",
        "mcp__coach_data__get_nutrition",
        "mcp__coach_data__get_recent_workouts",
        "mcp__coach_data__get_cardio",
        "mcp__coach_data__get_weight_trend",
        "mcp__hevy-mcp__get-routines",
        "mcp__hevy-mcp__get-routine",
        "mcp__hevy-mcp__get-routine-folders",
        "mcp__hevy-mcp__get-workouts",
        "mcp__hevy-mcp__get-workout",
        "mcp__hevy-mcp__get-workout-count",
        "mcp__hevy-mcp__get-exercise-templates",
        "mcp__hevy-mcp__get-exercise-template",
        "mcp__hevy-mcp__search-exercise-templates",
        "mcp__hevy-mcp__get-exercise-history",
    ]
    write_tools = [
        "mcp__hevy-mcp__create-routine",
        "mcp__hevy-mcp__update-routine",
        "mcp__hevy-mcp__create-routine-folder",
        "mcp__hevy-mcp__create-workout",
        "mcp__hevy-mcp__update-workout",
        "mcp__hevy-mcp__create-exercise-template",
    ]
    allowed = read_tools + (write_tools if allow_writes else [])

    kwargs = dict(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers=mcp_servers,
        allowed_tools=allowed,
        permission_mode="default",
    )
    if session_id:
        kwargs["resume"] = session_id
    if permission_callback:
        kwargs["can_use_tool"] = permission_callback

    return ClaudeAgentOptions(**kwargs)


# ─── Session persistence ─────────────────────────────────────────────────────


def _load_sessions() -> dict:
    return store.get("coach_session") or {}


def _save_session(surface: str, session_id: str) -> None:
    data = _load_sessions()
    data[surface] = session_id
    store.set("coach_session", data)


def get_session_id(surface: str) -> str | None:
    return _load_sessions().get(surface)


# ─── Entry points ────────────────────────────────────────────────────────────


async def run_once(prompt: str, *, surface: str = "cli_oneshot",
                   resume: bool = False, allow_writes: bool = False) -> str:
    """One-shot query. Returns final assistant text. Does not persist session by default."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock, query

    sid = get_session_id(surface) if resume else None
    options = _build_options(session_id=sid, allow_writes=allow_writes)

    final_text_parts: list[str] = []
    new_session_id: str | None = None

    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, SystemMessage):
            data = getattr(msg, "data", None) or {}
            if isinstance(data, dict) and data.get("session_id"):
                new_session_id = data["session_id"]
        elif isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    final_text_parts.append(block.text)
        elif isinstance(msg, ResultMessage):
            if getattr(msg, "result", None):
                _log_qa(surface, prompt, msg.result)
                return msg.result

    if new_session_id and resume:
        _save_session(surface, new_session_id)
    final = "\n".join(final_text_parts).strip()
    _log_qa(surface, prompt, final)
    return final


async def chat_stream(prompt: str, *, surface: str = "cli_chat",
                      allow_writes: bool = False):
    """Async generator yielding (kind, payload) tuples:
       ("text", str), ("tool_use", {"name": ..., "input": ...}),
       ("tool_result", str), ("done", final_text).

    Maintains a persistent session keyed by `surface`.
    """
    from claude_agent_sdk import (
        AssistantMessage, ClaudeSDKClient, ResultMessage, SystemMessage,
        TextBlock, ToolUseBlock, UserMessage,
    )

    sid = get_session_id(surface)
    options = _build_options(session_id=sid, allow_writes=allow_writes)

    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        final_parts: list[str] = []
        async for msg in client.receive_response():
            if isinstance(msg, SystemMessage):
                data = getattr(msg, "data", None) or {}
                if isinstance(data, dict) and data.get("session_id"):
                    _save_session(surface, data["session_id"])
            elif isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        final_parts.append(block.text)
                        yield ("text", block.text)
                    elif isinstance(block, ToolUseBlock):
                        yield ("tool_use", {"name": block.name, "input": block.input})
            elif isinstance(msg, UserMessage):
                for block in getattr(msg, "content", []) or []:
                    content = getattr(block, "content", None)
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                yield ("tool_result", c.get("text", ""))
                    elif isinstance(content, str):
                        yield ("tool_result", content)
            elif isinstance(msg, ResultMessage):
                final = getattr(msg, "result", None) or "\n".join(final_parts).strip()
                _log_qa(surface, prompt, final)
                yield ("done", final)
                return

        final = "\n".join(final_parts).strip()
        _log_qa(surface, prompt, final)
        yield ("done", final)


PROACTIVE_BRIEFING = """Run a pre-workout check-in for today.

Steps (do them in order, using tools — don't guess):
  1. Call get_recovery_today. Note the band (red/yellow/green) and any flags.
  2. Call get_recent_workouts(days=4) to identify which muscle groups were hit recently
     and which sets came in at high RPE (likely still sore).
  3. Call get_profile to confirm today's phase / block / day-of-week.
  4. Check today's planned Hevy routine via get-routines / get-routine if one exists.
  5. Decide: is today's plan appropriate given recovery + soreness?
     - If recovery < 34 (red): propose a deload — cut sets, drop load ~10%, or swap to
       lighter accessories / mobility. Avoid taxing soreness areas.
     - If 34-66 (yellow): hold sets, respect the planned load multiplier from recovery.
     - If >= 67 (green): go as planned; consider a small top-set push on the main lift.
  6. If changes are warranted AND write tools are available, call update-routine to apply
     them. State what you changed and why in one short paragraph before the tool call.
     If read-only, describe the adjusted plan without writing.
  7. Finish with a 3-line summary: recovery band, biggest adjustment, and one focus cue.

Be decisive. Do not ask the user for confirmation before editing — they invoked you to
act. Keep the final summary under 80 words."""


async def proactive_check_in(*, surface: str = "proactive", allow_writes: bool = True) -> str:
    """Autonomous pre-workout check: reads state, adjusts today's routine if needed."""
    return await run_once(
        PROACTIVE_BRIEFING, surface=surface, resume=False, allow_writes=allow_writes,
    )


def clear_session(surface: str) -> None:
    data = _load_sessions()
    if surface in data:
        del data[surface]
        store.set("coach_session", data)
