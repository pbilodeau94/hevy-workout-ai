"""Push PT, workout, and cardio schedules to macOS Reminders via osascript."""

from __future__ import annotations

import subprocess
from datetime import date, datetime, time, timedelta

from .config import load_profile, load_pt_routine, load_state


def _add_reminder(
    title: str,
    due_dt: datetime,
    body: str,
    list_name: str = "Home",
) -> bool:
    title_esc = title.replace('"', '\\"')
    body_esc = body.replace('"', '\\"').replace("\n", "\\n")
    due_str = due_dt.strftime("%B %d, %Y %I:%M:%S %p")

    script = f'''
    tell application "Reminders"
        tell list "{list_name}"
            make new reminder with properties {{name:"{title_esc}", body:"{body_esc}", remind me date:date "{due_str}"}}
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
        print(f"  Reminders error: {e.stderr.strip()}")
        return False


def schedule_pt_reminders(
    weeks: int = 6,
    start_date: date | None = None,
    list_name: str = "Home",
) -> list[dict]:
    """Create one daily PT reminder for `weeks` weeks. Time from pt_routine.yaml."""
    pt = load_pt_routine()
    hour = pt.get("calendar_hour", 19)
    remind_time = time(hour, 0)

    if start_date is None:
        start_date = date.today()

    lines = [f"- {ex['name']} — {ex['sets']}x{ex['reps']}" for ex in pt["exercises"]]
    body = "Daily PT routine (prescribed).\n" + "\n".join(lines)

    results = []
    for i in range(weeks * 7):
        due_date = start_date + timedelta(days=i)
        due_dt = datetime.combine(due_date, remind_time)
        success = _add_reminder("PT: Daily routine", due_dt, body, list_name)
        results.append({"date": due_date.isoformat(), "success": success})
    return results


def _wipe_by_prefixes(prefixes: tuple[str, ...], list_name: str = "Home") -> int:
    """Delete future incomplete reminders whose name starts with any given prefix."""
    clauses = " or ".join(f'name of r starts with "{p}"' for p in prefixes)
    script = f'''
    set n to 0
    tell application "Reminders"
        tell list "{list_name}"
            set rs to (every reminder whose completed is false)
            repeat with r in rs
                if ({clauses}) then
                    delete r
                    set n to n + 1
                end if
            end repeat
        end tell
    end tell
    return n
    '''
    try:
        out = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, check=True, timeout=120,
        )
        return int((out.stdout or "0").strip() or 0)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as e:
        stderr = getattr(e, "stderr", "") or ""
        print(f"  Reminders wipe error: {stderr.strip() or e}")
        return 0


def _wipe_today_by_prefixes(prefixes: tuple[str, ...], list_name: str = "Home") -> int:
    """Delete today's incomplete reminders whose name starts with any given prefix."""
    today = date.today()
    start_str = datetime.combine(today, time(0, 0)).strftime("%B %d, %Y %I:%M:%S %p")
    end_str = datetime.combine(today + timedelta(days=1), time(0, 0)).strftime("%B %d, %Y %I:%M:%S %p")
    clauses = " or ".join(f'name of r starts with "{p}"' for p in prefixes)
    script = f'''
    set n to 0
    set dStart to date "{start_str}"
    set dEnd to date "{end_str}"
    tell application "Reminders"
        tell list "{list_name}"
            set rs to (every reminder whose completed is false)
            repeat with r in rs
                try
                    set rd to remind me date of r
                    if rd ≥ dStart and rd < dEnd and ({clauses}) then
                        delete r
                        set n to n + 1
                    end if
                end try
            end repeat
        end tell
    end tell
    return n
    '''
    try:
        out = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, check=True, timeout=60,
        )
        return int((out.stdout or "0").strip() or 0)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as e:
        stderr = getattr(e, "stderr", "") or ""
        print(f"  Reminders wipe-today error: {stderr.strip() or e}")
        return 0


def apply_verdict_today(
    verdict,
    list_name: str = "Home",
    workout_time: time = time(7, 0),
) -> dict:
    """Rewrite today's Workout:/Cardio: reminder(s) to match the verdict.

    weights/dumbbell_day: leave existing reminder in place (just report).
    recovery_day: wipe today's Workout:/Cardio:, add a Recovery: reminder.
    zone2_bike: wipe today's Workout:, add a Cardio: Zone-2 bike reminder.
    """
    due_dt = datetime.combine(date.today(), workout_time)
    wiped = 0
    added = None

    if verdict.call == "recovery_day":
        wiped = _wipe_today_by_prefixes(("Workout:", "Cardio:"), list_name)
        title = "Recovery: 8k steps + 20 min walk / PT mobility"
        body = verdict.rationale
        ok = _add_reminder(title, due_dt, body, list_name)
        added = {"title": title, "success": ok}
    elif verdict.call == "zone2_bike":
        wiped = _wipe_today_by_prefixes(("Workout:",), list_name)
        title = "Cardio: Zone-2 bike — 40–60 min"
        body = verdict.rationale
        ok = _add_reminder(title, due_dt, body, list_name)
        added = {"title": title, "success": ok}
    # weights / dumbbell_day: no-op — existing Workout: reminder still valid

    return {"call": verdict.call, "wiped": wiped, "added": added}


def wipe_future_pt_reminders(list_name: str = "Home") -> int:
    return _wipe_by_prefixes(("PT:",), list_name)


def wipe_future_workout_reminders(list_name: str = "Home") -> int:
    return _wipe_by_prefixes(("Workout:", "Cardio:"), list_name)


def _next_weekday(start: date, weekday: int) -> date:
    days_ahead = weekday - start.weekday()
    if days_ahead < 0:
        days_ahead += 7
    return start + timedelta(days=days_ahead)


def _routine_body(routine: dict) -> str:
    from .generator import _build_name_lookup
    names = _build_name_lookup()
    lines = []
    for i, ex in enumerate(routine["routine"]["exercises"], 1):
        eid = ex["exercise_template_id"]
        name = names.get(eid, eid)
        sets = ex["sets"]
        n_sets = len(sets)
        weight = sets[0].get("weight_kg") if sets else None
        w_str = f" @ {round(weight * 2.20462, 1)} lb" if weight is not None else ""
        if sets and sets[0].get("rep_range"):
            rr = sets[0]["rep_range"]
            rep_str = f"{rr['start']}-{rr['end']}"
        else:
            rep_str = "?"
        lines.append(f"{i}. {name} — {n_sets}x{rep_str}{w_str}")
    return "\n".join(lines)


def wipe_future_calendar_events(
    calendar_name: str = "Home",
    prefixes: tuple[str, ...] = ("Workout:", "Cardio:"),
) -> int:
    """Delete future Calendar.app events (from the retired calendar.py integration)."""
    start_str = datetime.now().strftime("%B %d, %Y %I:%M:%S %p")
    clauses = " or ".join(f'summary of e starts with "{p}"' for p in prefixes)
    script = f'''
    set n to 0
    set dStart to date "{start_str}"
    tell application "Calendar"
        tell calendar "{calendar_name}"
            set es to (every event whose start date ≥ dStart)
            repeat with e in es
                try
                    if ({clauses}) then
                        delete e
                        set n to n + 1
                    end if
                end try
            end repeat
        end tell
    end tell
    return n
    '''
    try:
        out = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, check=True, timeout=180,
        )
        return int((out.stdout or "0").strip() or 0)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as e:
        stderr = getattr(e, "stderr", "") or ""
        print(f"  Calendar wipe error: {stderr.strip() or e}")
        return 0


def schedule_workout_reminders(
    routines: list[dict],
    start_date: date | None = None,
    workout_time: time = time(7, 0),
    list_name: str = "Home",
) -> list[dict]:
    """Schedule lifting workouts across the current block as macOS Reminders."""
    state = load_state()
    block_weeks = state["block_length_weeks"]
    training_days = state["training_days"]

    if start_date is None:
        start_date = _next_weekday(date.today(), training_days[0])

    results = []
    for week in range(block_weeks):
        week_start = start_date + timedelta(weeks=week)
        for i, weekday in enumerate(training_days):
            if i >= len(routines):
                break
            routine = routines[i]
            due_date = _next_weekday(week_start, weekday)
            due_dt = datetime.combine(due_date, workout_time)
            title = f"Workout: {routine['routine']['title']} (Wk {week + 1})"
            body = _routine_body(routine)
            success = _add_reminder(title, due_dt, body, list_name)
            results.append({"date": due_date.isoformat(), "title": title, "success": success})
    return results


def schedule_cardio_reminders(
    start_date: date | None = None,
    list_name: str = "Home",
) -> list[dict]:
    """Schedule cardio sessions across the current block as macOS Reminders."""
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
    remind_time = time(hour, 0)

    if start_date is None:
        training_days = state.get("training_days", [1])
        start_date = _next_weekday(date.today(), min(training_days + cardio_days))

    results = []
    for week in range(block_weeks):
        week_start = start_date + timedelta(weeks=week)
        for weekday in cardio_days:
            due_date = _next_weekday(week_start, weekday)
            is_long = weekday == long_weekday
            duration = long_dur if is_long else default_dur
            due_dt = datetime.combine(due_date, remind_time)
            if is_long:
                title = f"Cardio: Long ride (Peloton or outdoor) — {duration} min"
                body = "Long ride day. Pick Peloton class or outdoor route same-day."
            else:
                title = f"Cardio: Peloton — {duration} min"
                body = f"Build-up phase. Pick ride same-day. Minimum {duration} min."
            success = _add_reminder(title, due_dt, body, list_name)
            results.append({"date": due_date.isoformat(), "title": title, "success": success})
    return results
