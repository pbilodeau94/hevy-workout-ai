"""CLI for hevy-workout-ai."""

from __future__ import annotations

import json
from datetime import date, time

import click

from .reminders import (
    schedule_cardio_reminders,
    schedule_pt_reminders,
    schedule_workout_reminders,
    wipe_future_pt_reminders,
    wipe_future_workout_reminders,
)
from .config import load_profile, load_state, save_state
from .generator import generate_routine, generate_week_routines, preview_routine
from .hevy_client import create_routine
from .nutrition import FALLBACK_AVERAGE_DAYS, estimate_maintenance, get_nutrition_adjustment, log_today
from .recovery import RECOVERY_FILE, get_recovery_adjustment


@click.group()
def cli():
    """AI-powered workout generator for Hevy."""


@cli.command()
def status():
    """Show current training profile, phase, and block info."""
    profile = load_profile()
    state = load_state()
    t = profile["training"]

    click.echo(f"Phase:      {t['current_phase'].replace('_', ' ').title()}")
    click.echo(f"Goal:       {t.get('goal', 'hypertrophy')}")
    click.echo(f"Days/week:  {t['days_per_week']}")
    click.echo(f"Experience: {t['experience_level']}")
    click.echo(f"Rest:       {t['rest']['upper_compound']}s upper / {t['rest']['lower_compound']}s lower / {t['rest']['upper_isolation']}s iso")

    click.echo(f"\nBlock:      {state['current_block']}  (week {state['current_week_in_block']}/{state['block_length_weeks']})")
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_names = [days[d] for d in state["training_days"]]
    click.echo(f"Schedule:   {', '.join(day_names)}")

    click.echo(f"\nEquipment:  {', '.join(profile['equipment'])}")
    db = profile.get("dumbbell_range", {})
    if db:
        click.echo(f"Dumbbells:  {db['min_lb']}-{db['max_lb']} lb (increments of {db['increment_lb']})")


@cli.command()
@click.option("--day", default=None, help="Day key (e.g. day_a, upper). Auto-picks if omitted.")
def generate(day: str | None):
    """Generate a single workout routine and preview it."""
    routine = generate_routine(day)
    click.echo(preview_routine(routine))


@cli.command(name="whoop-auth")
def whoop_auth():
    """One-time OAuth flow for Whoop. Opens browser, captures code."""
    from .whoop_client import authorize
    authorize()


@cli.command(name="strava-auth")
def strava_auth():
    """One-time OAuth flow for Strava. Opens browser, captures code."""
    from .strava_client import authorize
    authorize()


@cli.command()
@click.option("--days", default=120, type=int, help="History window (days). Default: 120")
def fitness(days: int):
    """Show CTL/ATL/TSB from Strava + Peloton loads (Banister)."""
    from rich.console import Console
    from rich.table import Table

    from .training_load import compute_series, current_state

    console = Console()
    points = []

    try:
        from .strava_client import list_load_points as strava_pts
        points.extend(strava_pts(days))
    except Exception as e:
        console.print(f"[yellow]Strava skipped: {e}[/yellow]")

    try:
        from .peloton_client import list_load_points as pel_pts
        points.extend(pel_pts(days))
    except Exception as e:
        console.print(f"[yellow]Peloton skipped: {e}[/yellow]")

    if not points:
        console.print("[red]No load data from either source.[/red]")
        return

    today = current_state(points)
    if today is None:
        console.print("[red]No series.[/red]")
        return

    ctl, atl, tsb = today.ctl, today.atl, today.tsb
    if tsb >= 10:
        verdict = "[green]Fresh — ready to push[/green]"
    elif tsb >= -10:
        verdict = "[cyan]Neutral — maintain[/cyan]"
    elif tsb >= -20:
        verdict = "[yellow]Fatigued — consider easier week[/yellow]"
    else:
        verdict = "[red]Deeply fatigued — deload[/red]"

    t = Table(title=f"Training Load ({today.day}) · {len(points)} activities / {days}d")
    t.add_column("Metric"); t.add_column("Value", justify="right"); t.add_column("Meaning")
    t.add_row("Fitness (CTL)", f"{ctl:.1f}", "42-day avg load")
    t.add_row("Fatigue (ATL)", f"{atl:.1f}", "7-day avg load")
    t.add_row("Form (TSB)", f"{tsb:+.1f}", verdict)
    console.print(t)

    series = compute_series(points)
    console.print("\n[dim]Last 7 days:[/dim]")
    for d in series[-7:]:
        console.print(f"  {d.day}  load={d.load:6.1f}  CTL={d.ctl:5.1f}  ATL={d.atl:5.1f}  TSB={d.tsb:+.1f}")


@cli.command()
def recovery():
    """Show today's recovery adjustment (Whoop/manual + nutrition)."""
    adj = get_recovery_adjustment()
    nut = get_nutrition_adjustment()
    click.echo(f"  Score:     {adj.score:.0f}  ({adj.band})")
    click.echo(f"  Source:    {adj.source}")
    click.echo(f"  Load mult: x{adj.load_mult}")
    click.echo(f"  Set delta: {adj.set_delta:+d}")
    click.echo(f"  Note:      {adj.note}")
    click.echo("")
    click.echo("  Nutrition:")
    if nut.calories_today is not None:
        click.echo(f"    Intake:      {nut.calories_today:.0f} kcal")
    if nut.maintenance_kcal is not None:
        click.echo(f"    Maintenance: {nut.maintenance_kcal:.0f} kcal (rolling 14d)")
    if nut.deficit_kcal is not None:
        click.echo(f"    Balance:     {nut.deficit_kcal:+.0f} kcal")
    if nut.protein_target_g is not None:
        day_label = "lift" if nut.is_lifting_day else "rest"
        intake = f"{nut.protein_today:.0f}" if nut.protein_today is not None else "?"
        gap = f", gap {nut.protein_gap_g:.0f}g" if nut.protein_gap_g and nut.protein_gap_g > 0 else ""
        click.echo(f"    Protein:     {intake} / {nut.protein_target_g:.0f} g target ({day_label} day{gap})")
    if nut.bodyweight_today is not None:
        click.echo(f"    Bodyweight:  {nut.bodyweight_today:.1f} lb")
    if nut.estimated_fields:
        click.echo(f"    (estimated from {FALLBACK_AVERAGE_DAYS}d avg: {', '.join(nut.estimated_fields)})")
    if nut.calories_today is None and nut.protein_today is None and nut.bodyweight_today is None:
        click.echo("    (no data logged today — run 'hevy-ai log-nutrition')")


@cli.command(name="log-nutrition")
@click.option("--bodyweight", type=float, help="Bodyweight (lb)")
@click.option("--calories", type=float, help="Calories consumed (kcal)")
@click.option("--protein", type=float, help="Protein (g)")
@click.option("--fiber", type=float, help="Fiber (g)")
def log_nutrition(bodyweight: float | None, calories: float | None, protein: float | None, fiber: float | None):
    """Log today's nutrition (upsert)."""
    if bodyweight is None and calories is None and protein is None and fiber is None:
        raise click.UsageError("Provide at least one of --bodyweight, --calories, --protein, --fiber")
    entry = log_today(bodyweight_lb=bodyweight, calories_kcal=calories, protein_g=protein, fiber_g=fiber)
    click.echo(f"  Logged {entry['date']}: {entry}")
    maint = estimate_maintenance()
    if maint is not None:
        click.echo(f"  Rolling maintenance: {maint:.0f} kcal")
    else:
        click.echo("  Maintenance: need 7+ days of data")


@cli.command(name="sync-garmin-weight")
@click.option("--days", default=365, type=int, help="Days of history to pull.")
def sync_garmin_weight(days: int):
    """Pull bodyweight from Garmin Index and upsert into nutrition_log.yaml."""
    from .garmin_client import get_weight_series
    series = get_weight_series(days=days)
    if not series:
        click.echo("  No weight entries returned from Garmin.")
        return
    for day, lb in series:
        log_today(bodyweight_lb=lb, on_date=day)
    click.echo(f"  Synced {len(series)} weigh-ins. Latest: {series[-1][0]} → {series[-1][1]} lb")


@cli.command(name="sync-whoop")
@click.option("--limit", default=25, type=int, help="How many recent records to pull.")
def sync_whoop(limit: int):
    """Pull recent Whoop recovery + sleep records into config/whoop_log.yaml."""
    from . import whoop_log
    r, s = whoop_log.sync(limit=limit)
    click.echo(f"  Synced {r} recovery, {s} sleep records → config/whoop_log.yaml")


@cli.command(name="set-recovery")
@click.argument("score", type=float)
@click.option("--hrv", type=float, help="HRV (ms)")
@click.option("--rhr", type=int, help="Resting HR (bpm)")
@click.option("--sleep", type=float, help="Sleep hours")
def set_recovery(score: float, hrv: float | None, rhr: int | None, sleep: float | None):
    """Manually set today's recovery score (e.g. from Apple Health via Claude iOS)."""
    import yaml
    data = {"date": date.today().isoformat(), "recovery_score": score}
    if hrv is not None:
        data["hrv_ms"] = hrv
    if rhr is not None:
        data["rhr_bpm"] = rhr
    if sleep is not None:
        data["sleep_hours"] = sleep
    RECOVERY_FILE.write_text(yaml.safe_dump(data, sort_keys=False))
    click.echo(f"  Wrote {RECOVERY_FILE}")
    click.echo(f"  {get_recovery_adjustment().note}")


@cli.command(name="log-doms")
@click.option("--legs", type=int, help="0-100 DOMS for legs (quads/glutes/hams/calves)")
@click.option("--push", type=int, help="0-100 DOMS for push (chest/shoulders/triceps)")
@click.option("--pull", type=int, help="0-100 DOMS for pull (back/biceps)")
@click.option("--core", type=int, help="0-100 DOMS for core")
def log_doms_cmd(legs: int | None, push: int | None, pull: int | None, core: int | None):
    """Log DOMS (0-100) per big muscle group. Decays linearly to 0 over 72h."""
    from .doms import log_doms
    scores = {k: v for k, v in {"legs": legs, "push": push, "pull": pull, "core": core}.items() if v is not None}
    if not scores:
        click.echo("  pass at least one of --legs/--push/--pull/--core")
        return
    st = log_doms(scores)
    click.echo(f"  DOMS (decayed): {st.summary() or 'all clear'}")
    for g in ("legs", "push", "pull", "core"):
        s = st.scores.get(g, 0.0)
        if s > 0:
            click.echo(f"    {g}: {int(s)} ({st.band(g)})")


@cli.command()
@click.option("--no-gym", is_flag=True, help="You can't get to the gym today.")
def recommend(no_gym: bool):
    """Today's trainer verdict: recovery / zone-2 / weights / dumbbell-only."""
    from .recommender import recommend as _rec
    from .training_load import current_state
    tsb = None
    try:
        from .strava_client import list_load_points as _s
        from .peloton_client import list_load_points as _p
        pts = []
        try: pts.extend(_s(120))
        except Exception: pass
        try: pts.extend(_p(120))
        except Exception: pass
        st = current_state(pts)
        if st is not None:
            tsb = st.tsb
    except Exception:
        pass
    v = _rec(tsb=tsb, no_gym=no_gym)
    click.echo(f"  ▶ {v.headline}")
    click.echo(f"    {v.rationale}")
    if v.push_routine:
        cmd = "hevy push --no-gym" if v.dumbbell_only else "hevy push"
        click.echo(f"    Push: {cmd}")


@cli.command(name="wipe-calendar")
@click.option("--calendar-name", default="Home", help="Calendar.app calendar name.")
def wipe_calendar(calendar_name: str):
    """Delete leftover Workout:/Cardio: events in Calendar.app (retired integration)."""
    from .reminders import wipe_future_calendar_events
    click.echo(f"  Wiping future Workout:/Cardio: events from '{calendar_name}'...")
    n = wipe_future_calendar_events(calendar_name=calendar_name)
    click.echo(f"  Deleted {n} event(s).")


@cli.command(name="verdict-apply")
@click.option("--no-gym", is_flag=True, help="You can't get to the gym today.")
@click.option("--list-name", default="Home", help="macOS Reminders list name.")
@click.option("--hour", default=7, help="Workout hour (24h). Default: 7")
def verdict_apply(no_gym: bool, list_name: str, hour: int):
    """Apply today's verdict: rewrite today's Reminder and push the right Hevy routine."""
    from datetime import time as _time
    from .recommender import recommend as _rec
    from .reminders import apply_verdict_today, _wipe_today_by_prefixes, _add_reminder
    from .training_load import current_state

    tsb = None
    try:
        from .strava_client import list_load_points as _s
        from .peloton_client import list_load_points as _p
        pts = []
        try: pts.extend(_s(120))
        except Exception: pass
        try: pts.extend(_p(120))
        except Exception: pass
        st = current_state(pts)
        if st is not None:
            tsb = st.tsb
    except Exception:
        pass

    v = _rec(tsb=tsb, no_gym=no_gym)
    click.echo(f"  ▶ {v.headline}")
    click.echo(f"    {v.rationale}\n")

    # Push to Hevy if the verdict calls for a lift
    if v.push_routine:
        from datetime import date as _date, datetime as _dt
        click.echo(f"  Generating routine (dumbbell_only={v.dumbbell_only})...")
        routine = generate_routine(dumbbell_only=v.dumbbell_only)
        title = routine["routine"]["title"]
        click.echo(f"  Pushing '{title}' to Hevy...")
        try:
            resp = create_routine(routine)
            created = resp.get("routine", resp)
            if isinstance(created, list):
                created = created[0]
            click.echo(f"  Created routine: {created.get('id', '?')}")
        except Exception as e:
            click.echo(f"  Hevy push error: {e}", err=True)

        # Rewrite today's Workout: reminder with the fresh routine
        from .reminders import _routine_body
        wiped = _wipe_today_by_prefixes(("Workout:", "Cardio:"), list_name)
        click.echo(f"  Wiped {wiped} reminder(s) for today.")
        suffix = " (DB only)" if v.dumbbell_only else ""
        rem_title = f"Workout: {title}{suffix}"
        due_dt = _dt.combine(_date.today(), _time(hour, 0))
        ok = _add_reminder(rem_title, due_dt, _routine_body(routine), list_name)
        click.echo(f"  Added reminder: {rem_title} ({'ok' if ok else 'failed'})")
    else:
        res = apply_verdict_today(v, list_name=list_name, workout_time=_time(hour, 0))
        click.echo(f"  Wiped {res['wiped']} reminder(s) for today.")
        if res["added"]:
            a = res["added"]
            click.echo(f"  Added reminder: {a['title']} ({'ok' if a['success'] else 'failed'})")


@cli.command()
def week():
    """Generate and preview all routines for the current week/block."""
    state = load_state()
    click.echo(f"  Block {state['current_block']}, Week {state['current_week_in_block']}/{state['block_length_weeks']}\n")
    routines = generate_week_routines()
    for i, routine in enumerate(routines, 1):
        click.echo(f"\n{'='*55}")
        click.echo(f"  Day {i}")
        click.echo(f"{'='*55}")
        click.echo(preview_routine(routine))


@cli.command()
@click.option("--day", default=None, help="Day key to generate and push.")
@click.option("--all-week", is_flag=True, help="Push all routines for the week.")
@click.option("--no-gym", is_flag=True, help="Dumbbell/bodyweight only.")
def push(day: str | None, all_week: bool, no_gym: bool):
    """Generate routine(s) and push them to Hevy."""
    if all_week:
        routines = generate_week_routines()
    else:
        routines = [generate_routine(day, dumbbell_only=no_gym)]

    for routine in routines:
        title = routine["routine"]["title"]
        click.echo(f"  Pushing '{title}' to Hevy...")
        try:
            resp = create_routine(routine)
            created = resp.get("routine", resp)
            if isinstance(created, list):
                created = created[0]
            rid = created.get("id", "?")
            click.echo(f"  Created routine: {rid}")
        except Exception as e:
            click.echo(f"  Error: {e}", err=True)


@cli.command()
@click.option("--start", default=None, help="Start date (YYYY-MM-DD). Defaults to next training day.")
@click.option("--hour", default=7, help="Workout hour (24h). Default: 7")
@click.option("--list-name", default="Home", help="macOS Reminders list name. Default: Home")
@click.option("--push-hevy/--no-push-hevy", default=True, help="Also push routines to Hevy.")
@click.option("--cardio/--no-cardio", default=True, help="Also add cardio (Peloton) reminders.")
@click.option("--pt/--no-pt", default=True, help="Also add daily PT reminders.")
@click.option("--pt-weeks", default=6, help="Weeks of daily PT to schedule. Default: 6")
@click.option("--wipe/--no-wipe", default=False, help="Wipe future Workout:/Cardio:/PT: reminders before scheduling.")
def schedule(start: str | None, hour: int, list_name: str, push_hevy: bool, cardio: bool, pt: bool, pt_weeks: int, wipe: bool):
    """Schedule a full training block: push to Hevy + macOS Reminders."""
    state = load_state()
    block = state["current_block"]
    block_weeks = state["block_length_weeks"]

    click.echo(f"  Scheduling Block {block} ({block_weeks} weeks)\n")

    if wipe:
        click.echo(f"  Wiping future Workout:/Cardio: reminders from '{list_name}'...")
        n = wipe_future_workout_reminders(list_name=list_name)
        click.echo(f"  Deleted {n} reminder(s).")
        click.echo(f"  Wiping future PT reminders from '{list_name}'...")
        nr = wipe_future_pt_reminders(list_name=list_name)
        click.echo(f"  Deleted {nr} reminder(s).\n")

    # Generate the week's routines (same exercises repeat each week)
    routines = generate_week_routines()

    # Preview
    for i, routine in enumerate(routines, 1):
        click.echo(f"{'='*55}")
        click.echo(f"  Day {i}")
        click.echo(f"{'='*55}")
        click.echo(preview_routine(routine))

    # Push to Hevy
    if push_hevy:
        click.echo(f"\n  Pushing {len(routines)} routines to Hevy...")
        for routine in routines:
            title = routine["routine"]["title"]
            try:
                resp = create_routine(routine)
                created = resp.get("routine", resp)
                if isinstance(created, list):
                    created = created[0]
                click.echo(f"    {title}: {created.get('id', '?')}")
            except Exception as e:
                click.echo(f"    {title}: Error - {e}", err=True)

    # Schedule on Reminders
    start_date = date.fromisoformat(start) if start else None
    workout_time = time(hour, 0)

    click.echo(f"\n  Adding {block_weeks * len(routines)} workout reminders to '{list_name}'...")
    results = schedule_workout_reminders(routines, start_date, workout_time, list_name)
    ok = sum(1 for r in results if r["success"])
    click.echo(f"  Created {ok}/{len(results)} workout reminders")
    for r in results:
        status = "ok" if r["success"] else "FAILED"
        click.echo(f"    {r['date']}  {r['title']}  [{status}]")

    # Cardio reminders (no Hevy routines)
    if cardio:
        click.echo(f"\n  Adding cardio reminders to '{list_name}'...")
        cardio_results = schedule_cardio_reminders(start_date, list_name)
        if cardio_results:
            ok_c = sum(1 for r in cardio_results if r["success"])
            click.echo(f"  Created {ok_c}/{len(cardio_results)} cardio reminders")
            for r in cardio_results:
                status = "ok" if r["success"] else "FAILED"
                click.echo(f"    {r['date']}  {r['title']}  [{status}]")
        else:
            click.echo("  (no cardio config found in profile.yaml)")

    # PT reminders (daily, separate horizon from block)
    if pt:
        click.echo(f"\n  Adding daily PT reminders ({pt_weeks} weeks) to '{list_name}'...")
        pt_results = schedule_pt_reminders(weeks=pt_weeks, start_date=start_date, list_name=list_name)
        ok_pt = sum(1 for r in pt_results if r["success"])
        click.echo(f"  Created {ok_pt}/{len(pt_results)} PT reminders")

    # Update state
    state["last_push"] = date.today().isoformat()
    save_state(state)
    click.echo(f"\n  Done! Block {block} scheduled.")


@cli.command()
def advance():
    """Advance to the next week or block.

    If at the end of a block, bumps to the next block (new exercises).
    """
    state = load_state()

    week = state["current_week_in_block"]
    block_len = state["block_length_weeks"]

    if week >= block_len:
        state["current_block"] += 1
        state["current_week_in_block"] = 1
        click.echo(f"  Advanced to Block {state['current_block']}, Week 1")
        click.echo("  New block = new exercises! Run 'hevy schedule' to set up.")
    else:
        state["current_week_in_block"] = week + 1
        click.echo(f"  Advanced to Block {state['current_block']}, Week {state['current_week_in_block']}/{block_len}")

    save_state(state)


@cli.command()
def dashboard():
    """7-day summary: recovery, weight, nutrition, lifting, cardio."""
    from datetime import datetime, timedelta, timezone

    from rich.console import Console
    from rich.table import Table

    from .nutrition import _load_log, estimate_maintenance, is_lifting_day_today, protein_target_g

    console = Console()
    today = date.today()
    today_ord = today.toordinal()
    window_start_ord = today_ord - 6

    profile = load_profile()
    adj = get_recovery_adjustment()
    nut = get_nutrition_adjustment()

    # ── Today ───────────────────────────────────────────────────────────────
    lift = is_lifting_day_today()
    bw = nut.bodyweight_today or profile.get("bodyweight_lb")
    target = protein_target_g(bw, lift) if bw else None

    t = Table(title=f"Today — {today.isoformat()} ({'LIFT' if lift else 'REST'} day)", show_header=False)
    t.add_column(style="cyan", width=14)
    t.add_column()
    t.add_row("Recovery", f"{adj.score:.0f} ({adj.band})  load x{adj.load_mult}  set delta {adj.set_delta:+d}")
    if target and nut.protein_today is not None:
        t.add_row("Protein", f"{nut.protein_today:.0f} / {target:.0f} g  (gap {max(0, target - nut.protein_today):.0f}g)")
    elif target:
        t.add_row("Protein", f"target {target:.0f} g (no intake logged)")
    if nut.calories_today is not None and nut.maintenance_kcal is not None:
        t.add_row("Calories", f"{nut.calories_today:.0f} / {nut.maintenance_kcal:.0f} kcal ({nut.deficit_kcal:+.0f})")
    elif nut.calories_today is not None:
        t.add_row("Calories", f"{nut.calories_today:.0f} kcal")
    console.print(t)

    # ── Weight (7d) ─────────────────────────────────────────────────────────
    entries = _load_log()
    window = [
        e for e in entries
        if window_start_ord <= date.fromisoformat(str(e["date"])).toordinal() <= today_ord
    ]
    weights = [(e["date"], e["bodyweight_lb"]) for e in window if e.get("bodyweight_lb") is not None]
    wt = Table(title="Weight (7d)")
    wt.add_column("Date"); wt.add_column("lb", justify="right")
    for d, w in weights:
        wt.add_row(str(d), f"{w:.1f}")
    if not weights:
        wt.add_row("—", "no data")
    maint = estimate_maintenance()
    if maint is not None:
        wt.caption = f"maintenance ≈ {maint:.0f} kcal"
    console.print(wt)

    # ── Nutrition (7d) ──────────────────────────────────────────────────────
    nt = Table(title="Nutrition (7d)")
    nt.add_column("Date"); nt.add_column("kcal", justify="right"); nt.add_column("protein g", justify="right")
    for e in window:
        nt.add_row(
            str(e["date"]),
            f"{e['calories_kcal']:.0f}" if e.get("calories_kcal") is not None else "—",
            f"{e['protein_g']:.0f}" if e.get("protein_g") is not None else "—",
        )
    if not window:
        nt.add_row("—", "no data", "")
    console.print(nt)

    # ── Lifting (7d) ────────────────────────────────────────────────────────
    lt = Table(title="Lifting (7d, Hevy)")
    lt.add_column("Date"); lt.add_column("Title"); lt.add_column("Min", justify="right"); lt.add_column("Sets", justify="right")
    try:
        from .hevy_client import list_workouts
        data = list_workouts(page=1, page_size=10)
        workouts = data.get("workouts", data) if isinstance(data, dict) else []
        if not isinstance(workouts, list):
            workouts = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        rows = 0
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
            mins = "?"
            if end:
                try:
                    dt_end = datetime.fromisoformat(end.replace("Z", "+00:00"))
                    mins = f"{int((dt_end - dt).total_seconds() / 60)}"
                except ValueError:
                    pass
            sets = sum(len(ex.get("sets", [])) for ex in w.get("exercises", []))
            lt.add_row(dt.date().isoformat(), w.get("title", "?"), str(mins), str(sets))
            rows += 1
        if rows == 0:
            lt.add_row("—", "no workouts", "", "")
    except Exception as e:
        lt.add_row("—", f"error: {e}", "", "")
    console.print(lt)

    # ── Cardio (7d, Strava) ─────────────────────────────────────────────────
    ct = Table(title="Cardio (7d, Strava)")
    ct.add_column("Date"); ct.add_column("Type"); ct.add_column("Name"); ct.add_column("Min", justify="right"); ct.add_column("kcal", justify="right")
    try:
        from .strava_client import list_recent_activities_with_calories
        acts = list_recent_activities_with_calories(days=7)
        if not acts:
            ct.add_row("—", "no activities", "", "", "")
        for a in acts:
            start = a.get("start_date_local", "")[:10]
            mins = f"{int(a.get('moving_time', 0) / 60)}"
            kcal = f"{a['calories']:.0f}" if a.get("calories") else "—"
            ct.add_row(start, a.get("type", "?"), a.get("name", "?")[:30], mins, kcal)
    except Exception as e:
        ct.add_row("—", "not connected", f"run `hevy strava-auth`  ({e})", "", "")
    console.print(ct)


@cli.command(name="export-workouts")
@click.option("--since", default=90, type=int, help="Days of history to export.")
@click.option("--out-dir", default="exports", help="Output directory.")
def export_workouts(since: int, out_dir: str):
    """Export Hevy workouts to CSV: long format (one row per set) + progression summary.

    Produces two files matching Hevy's official export schema:
      workouts_<today>.csv     — one row per set (tidy long format)
      progression_<today>.csv  — one row per exercise per day (top set + e1RM + volume)
    """
    import csv
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from .hevy_client import list_workouts

    cutoff = datetime.now(timezone.utc) - timedelta(days=since)
    set_rows: list[dict] = []
    seen_ids: set[str] = set()

    page = 1
    while True:
        data = list_workouts(page=page, page_size=10)
        workouts = data.get("workouts", []) if isinstance(data, dict) else []
        if not workouts:
            break
        stop = False
        for w in workouts:
            wid = w.get("id") or ""
            if wid and wid in seen_ids:
                continue
            seen_ids.add(wid)
            start = w.get("start_time")
            if not start:
                continue
            try:
                dt_start = datetime.fromisoformat(start.replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt_start < cutoff:
                stop = True
                continue
            end = w.get("end_time") or ""
            title = w.get("title") or ""
            desc = (w.get("description") or "").replace("\n", " ")
            for ex in w.get("exercises", []):
                ex_title = ex.get("title") or ""
                ex_notes = (ex.get("notes") or "").replace("\n", " ")
                superset_id = ex.get("superset_id")
                for idx, s in enumerate(ex.get("sets", [])):
                    w_kg = s.get("weight_kg")
                    reps = s.get("reps")
                    lb = round(w_kg * 2.20462, 2) if w_kg else w_kg
                    set_rows.append({
                        "title": title,
                        "start_time": start,
                        "end_time": end,
                        "description": desc,
                        "exercise_title": ex_title,
                        "superset_id": superset_id if superset_id is not None else "",
                        "exercise_notes": ex_notes,
                        "set_index": idx,
                        "set_type": s.get("type") or "normal",
                        "weight_lbs": lb if lb is not None else "",
                        "reps": reps if reps is not None else "",
                        "distance_miles": s.get("distance_meters", 0) / 1609.34 if s.get("distance_meters") else "",
                        "duration_seconds": s.get("duration_seconds") or "",
                        "rpe": s.get("rpe") if s.get("rpe") is not None else "",
                    })
        if stop:
            break
        page += 1
        if page > 50:
            break

    if not set_rows:
        click.echo(f"No workouts in the last {since} days.")
        return

    set_rows.sort(key=lambda r: (r["start_time"], r["exercise_title"], r["set_index"]))

    out_base = Path(out_dir)
    out_base.mkdir(parents=True, exist_ok=True)
    today_str = date.today().isoformat()
    long_path = out_base / f"workouts_{today_str}.csv"
    prog_path = out_base / f"progression_{today_str}.csv"

    fieldnames = [
        "title", "start_time", "end_time", "description",
        "exercise_title", "superset_id", "exercise_notes",
        "set_index", "set_type",
        "weight_lbs", "reps", "distance_miles", "duration_seconds", "rpe",
    ]
    with long_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(set_rows)

    # Progression summary — one row per (date, exercise). Epley e1RM.
    from collections import defaultdict

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in set_rows:
        if r["set_type"] == "warmup":
            continue
        if r["reps"] == "" or r["weight_lbs"] == "":
            continue
        day = r["start_time"][:10]
        groups[(day, r["exercise_title"])].append(r)

    def e1rm(weight: float, reps: int) -> float:
        if reps <= 1:
            return weight
        return round(weight * (1 + reps / 30.0), 1)

    prog_rows = []
    for (day, ex_title), srs in groups.items():
        top = max(srs, key=lambda r: (float(r["weight_lbs"]), int(r["reps"])))
        top_w = float(top["weight_lbs"])
        top_r = int(top["reps"])
        best_e1rm = max(e1rm(float(r["weight_lbs"]), int(r["reps"])) for r in srs)
        volume = sum(float(r["weight_lbs"]) * int(r["reps"]) for r in srs)
        prog_rows.append({
            "date": day,
            "exercise": ex_title,
            "working_sets": len(srs),
            "top_weight_lbs": top_w,
            "top_reps": top_r,
            "best_e1rm_lbs": best_e1rm,
            "volume_lbs": round(volume, 1),
        })
    prog_rows.sort(key=lambda r: (r["exercise"], r["date"]))

    with prog_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(prog_rows[0].keys()))
        writer.writeheader()
        writer.writerows(prog_rows)

    click.echo(f"Wrote {len(set_rows)} sets → {long_path}")
    click.echo(f"Wrote {len(prog_rows)} exercise-days → {prog_path}")


@cli.group()
def coach():
    """Ask the AI fitness coach (strength / injury / endurance)."""


@coach.command(name="ask")
@click.argument("message", nargs=-1, required=True)
@click.option("--resume/--no-resume", default=False, help="Continue last CLI ask session.")
@click.option("--allow-writes/--read-only", default=True,
              help="Allow coach to modify Hevy routines/workouts. Default: allow.")
def coach_ask(message: tuple[str, ...], resume: bool, allow_writes: bool):
    """One-shot question to the coach. Example: hevy coach ask 'plan today's lift'"""
    import asyncio

    from .coach import run_once

    prompt = " ".join(message)
    out = asyncio.run(run_once(prompt, surface="cli_oneshot", resume=resume,
                               allow_writes=allow_writes))
    click.echo(out)


@coach.command(name="chat")
@click.option("--reset", is_flag=True, help="Start a fresh session.")
@click.option("--allow-writes/--read-only", default=True,
              help="Allow coach to modify Hevy routines/workouts. Default: allow.")
def coach_chat(reset: bool, allow_writes: bool):
    """Interactive REPL with the coach. Type 'exit' or Ctrl-D to quit."""
    import asyncio

    from .coach import chat_stream, clear_session

    if reset:
        clear_session("cli_chat")
        click.echo("  (session cleared)")

    mode = "write" if allow_writes else "read-only"
    click.echo(f"Coach chat ({mode}) — type 'exit' to quit.\n")

    async def turn(prompt: str):
        async for kind, payload in chat_stream(prompt, surface="cli_chat",
                                                allow_writes=allow_writes):
            if kind == "text":
                click.echo(payload, nl=False)
            elif kind == "tool_use":
                click.echo(click.style(f"\n  [tool: {payload['name']}]", dim=True))
            elif kind == "done":
                click.echo("")
                return

    while True:
        try:
            msg = click.prompt("you", prompt_suffix="› ", default="", show_default=False)
        except (EOFError, click.exceptions.Abort):
            click.echo("")
            break
        if not msg.strip() or msg.strip().lower() in {"exit", "quit"}:
            break
        asyncio.run(turn(msg))


@coach.command(name="checkin")
@click.option("--allow-writes/--read-only", default=True,
              help="Allow the coach to edit today's routine. Default: allow.")
def coach_checkin(allow_writes: bool):
    """Proactive pre-workout check-in — coach reads state, modifies today's routine if needed."""
    import asyncio

    from .coach import proactive_check_in

    out = asyncio.run(proactive_check_in(allow_writes=allow_writes))
    click.echo(out)


@coach.command(name="log")
@click.option("-n", "--limit", default=20, type=int, help="How many recent entries.")
@click.option("--surface", default=None, help="Filter by surface (cli_chat, cli_oneshot, dashboard).")
def coach_log(limit: int, surface: str | None):
    """Show recent coach Q&A log entries."""
    import json as _json
    from .coach import LOG_FILE

    if not LOG_FILE.exists():
        click.echo("No coach log yet.")
        return
    lines = LOG_FILE.read_text().splitlines()
    entries = []
    for ln in lines:
        try:
            e = _json.loads(ln)
        except Exception:
            continue
        if surface and e.get("surface") != surface:
            continue
        entries.append(e)
    for e in entries[-limit:]:
        ts = e.get("ts", "")
        sf = e.get("surface", "")
        click.echo(click.style(f"\n[{ts}] {sf}", fg="yellow"))
        click.echo(click.style("you› ", dim=True) + e.get("prompt", ""))
        click.echo(click.style("coach› ", dim=True) + (e.get("response") or "").strip())


@coach.command(name="clear")
@click.argument("surface", default="cli_chat")
def coach_clear(surface: str):
    """Clear a coach session (cli_chat, cli_oneshot, dashboard)."""
    from .coach import clear_session
    clear_session(surface)
    click.echo(f"  Cleared session: {surface}")


@cli.command()
@click.option("--whoop-limit", default=25, type=int, help="Whoop records to pull.")
@click.option("--weight-days", default=14, type=int, help="Garmin weight history window.")
@click.option("--mf-days", default=7, type=int, help="MacroFactor nutrition window (rolling days).")
def refresh(whoop_limit: int, weight_days: int, mf_days: int):
    """Pull the latest data from all integrations (Whoop, Garmin weight, MacroFactor).

    Intended for daily scheduling (e.g. launchd at 23:59).
    """
    from datetime import datetime as _dt

    ts = _dt.now().isoformat(timespec="seconds")
    click.echo(f"[{ts}] refresh: starting")

    try:
        from . import whoop_log
        r, s = whoop_log.sync(limit=whoop_limit)
        click.echo(f"  whoop: {r} recovery, {s} sleep records")
    except Exception as e:
        click.echo(f"  whoop: FAILED — {e}")

    try:
        from .garmin_client import get_weight_series
        series = get_weight_series(days=weight_days)
        for day, lb in series:
            log_today(bodyweight_lb=lb, on_date=day)
        if series:
            click.echo(f"  garmin weight: {len(series)} weigh-ins, latest {series[-1][0]} → {series[-1][1]} lb")
        else:
            click.echo("  garmin weight: no entries")
    except Exception as e:
        click.echo(f"  garmin weight: FAILED — {e}")

    try:
        from .mf_sync import sync_nutrition
        rows = sync_nutrition(days=mf_days)
        if rows:
            latest = rows[-1]
            click.echo(
                f"  macrofactor: {len(rows)} days synced, latest {latest['date']} → "
                f"{latest['calories_kcal']:.0f} kcal / {latest['protein_g']:.0f} P / "
                f"{latest['fiber_g']:.0f} F ({latest['entry_count']} entries)"
            )
        else:
            click.echo("  macrofactor: no entries in window")
    except Exception as e:
        click.echo(f"  macrofactor: FAILED — {e}")

    click.echo(f"[{_dt.now().isoformat(timespec='seconds')}] refresh: done")


@cli.command("sync-nutrition")
@click.option("--days", default=7, type=int, help="Rolling window size in days.")
def sync_nutrition_cmd(days: int):
    """Sync MacroFactor food log into config/nutrition_log.yaml."""
    from .mf_sync import sync_nutrition

    rows = sync_nutrition(days=days)
    if not rows:
        click.echo("no entries in window")
        return
    for r in rows:
        click.echo(
            f"{r['date']}  {r['calories_kcal']:>6.0f} kcal  {r['protein_g']:>5.0f} P  "
            f"{r['fiber_g']:>4.0f} F  ({r['entry_count']} entries)"
        )


@cli.command()
@click.option("--port", default=8501, help="Streamlit port.")
def web(port: int):
    """Launch the Streamlit dashboard in a browser."""
    import subprocess
    from pathlib import Path

    app = Path(__file__).parent / "web.py"
    subprocess.run(["streamlit", "run", str(app), "--server.port", str(port)])


if __name__ == "__main__":
    cli()
