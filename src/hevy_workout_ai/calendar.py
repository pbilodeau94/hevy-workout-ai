"""Push workout schedule to macOS Calendar (iCloud) via osascript."""

from __future__ import annotations

import subprocess
from datetime import date, datetime, time, timedelta

from .config import load_profile, load_state
from .generator import _build_name_lookup, estimate_duration


def _next_weekday(start: date, weekday: int) -> date:
    """Find the next date >= start that falls on the given weekday (0=Mon)."""
    days_ahead = weekday - start.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return start + timedelta(days=days_ahead)


def _routine_summary(routine: dict) -> str:
    """Build a short text summary of exercises for the calendar event notes."""
    names = _build_name_lookup()
    lines = []
    for i, ex in enumerate(routine["routine"]["exercises"], 1):
        eid = ex["exercise_template_id"]
        name = names.get(eid, eid)
        sets = ex["sets"]
        n_sets = len(sets)

        weight = sets[0].get("weight_kg") if sets else None
        if weight is not None:
            lb = round(weight * 2.20462, 1)
            w_str = f" @ {lb} lb"
        else:
            w_str = ""

        if sets and sets[0].get("rep_range"):
            rr = sets[0]["rep_range"]
            rep_str = f"{rr['start']}-{rr['end']}"
        else:
            rep_str = "?"

        lines.append(f"{i}. {name} — {n_sets}x{rep_str}{w_str}")

    return "\n".join(lines)


def _add_calendar_event(
    title: str,
    start_dt: datetime,
    end_dt: datetime,
    notes: str,
    calendar_name: str = "Home",
) -> bool:
    """Add an event to macOS Calendar via osascript."""
    # Escape quotes in strings for AppleScript
    title_esc = title.replace('"', '\\"')
    notes_esc = notes.replace('"', '\\"').replace("\n", "\\n")

    start_str = start_dt.strftime("%B %d, %Y %I:%M:%S %p")
    end_str = end_dt.strftime("%B %d, %Y %I:%M:%S %p")

    script = f'''
    tell application "Calendar"
        tell calendar "{calendar_name}"
            make new event with properties {{summary:"{title_esc}", start date:date "{start_str}", end date:date "{end_str}", description:"{notes_esc}"}}
        end tell
    end tell
    '''

    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, check=True, timeout=10,
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"  Calendar error: {e.stderr.strip()}")
        return False


def schedule_block(
    routines: list[dict],
    start_date: date | None = None,
    workout_time: time = time(7, 0),
    calendar_name: str = "Home",
) -> list[dict]:
    """Schedule a full training block on the iCloud calendar.

    Creates calendar events for each workout across all weeks in the block.

    Args:
        routines: List of routine dicts (one per training day in a week).
        start_date: First day of the block. Defaults to next Monday.
        workout_time: Time of day for workouts. Defaults to 7:00 AM.
        calendar_name: iCloud calendar name to add events to.

    Returns:
        List of dicts with event details (date, title, success).
    """
    state = load_state()
    block_weeks = state["block_length_weeks"]
    training_days = state["training_days"]  # e.g. [0, 2, 4] for Mon/Wed/Fri

    if start_date is None:
        start_date = _next_weekday(date.today(), training_days[0])

    events = []

    for week in range(block_weeks):
        week_start = start_date + timedelta(weeks=week)

        for i, weekday in enumerate(training_days):
            if i >= len(routines):
                break

            routine = routines[i]
            event_date = _next_weekday(week_start, weekday)
            duration_min = estimate_duration(routine)

            start_dt = datetime.combine(event_date, workout_time)
            end_dt = start_dt + timedelta(minutes=int(duration_min) + 5)

            title = routine["routine"]["title"]
            week_num = week + 1
            event_title = f"Workout: {title} (Wk {week_num})"
            notes = _routine_summary(routine)

            success = _add_calendar_event(event_title, start_dt, end_dt, notes, calendar_name)

            events.append({
                "date": event_date.isoformat(),
                "title": event_title,
                "duration_min": int(duration_min),
                "success": success,
            })

    return events


def schedule_cardio_block(
    start_date: date | None = None,
    calendar_name: str = "Home",
) -> list[dict]:
    """Schedule cardio events (Peloton / outdoor) across the current block.

    Reads cardio config from profile.yaml:
      cardio.days, cardio.default_duration_min, cardio.long_ride_weekday,
      cardio.long_ride_duration_min, cardio.hour.

    Creates calendar events only (no Hevy routines for cardio).
    """
    profile = load_profile()
    state = load_state()
    cardio = profile.get("cardio", {})
    if not cardio:
        return []

    block_weeks = state["block_length_weeks"]
    cardio_days = cardio.get("days", [])
    default_dur = cardio.get("default_duration_min", 45)
    long_weekday = cardio.get("long_ride_weekday")
    long_dur = cardio.get("long_ride_duration_min", 60)
    hour = cardio.get("hour", 18)
    workout_time = time(hour, 0)

    # Default start: next Monday of the training week
    if start_date is None:
        training_days = state.get("training_days", [1])
        start_date = _next_weekday(date.today(), min(training_days + cardio_days))

    events = []

    for week in range(block_weeks):
        week_start = start_date + timedelta(weeks=week)

        for weekday in cardio_days:
            event_date = _next_weekday(week_start, weekday)
            is_long = weekday == long_weekday
            duration = long_dur if is_long else default_dur

            start_dt = datetime.combine(event_date, workout_time)
            end_dt = start_dt + timedelta(minutes=duration)

            if is_long:
                title = f"Cardio: Long ride (Peloton or outdoor) — {duration} min"
                notes = "Long ride day. Pick Peloton class or outdoor route same-day."
            else:
                title = f"Cardio: Peloton — {duration} min"
                notes = f"Build-up phase. Pick ride same-day. Minimum {duration} min."

            success = _add_calendar_event(title, start_dt, end_dt, notes, calendar_name)

            events.append({
                "date": event_date.isoformat(),
                "title": title,
                "duration_min": duration,
                "success": success,
            })

    return events
