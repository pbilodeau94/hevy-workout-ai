"""CLI for hevy-workout-ai."""

from __future__ import annotations

import json
from datetime import date, time

import click

from .calendar import schedule_block, schedule_cardio_block
from .config import load_profile, load_state, save_state
from .generator import generate_routine, generate_week_routines, preview_routine
from .hevy_client import create_routine
from .nutrition import estimate_maintenance, get_nutrition_adjustment, log_today
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
    if nut.protein_per_lb is not None:
        click.echo(f"    Protein:     {nut.protein_today:.0f} g ({nut.protein_per_lb:.2f} g/lb)")
    if nut.bodyweight_today is not None:
        click.echo(f"    Bodyweight:  {nut.bodyweight_today:.1f} lb")
    if nut.calories_today is None and nut.protein_today is None and nut.bodyweight_today is None:
        click.echo("    (no data logged today — run 'hevy-ai log-nutrition')")


@cli.command(name="log-nutrition")
@click.option("--bodyweight", type=float, help="Bodyweight (lb)")
@click.option("--calories", type=float, help="Calories consumed (kcal)")
@click.option("--protein", type=float, help="Protein (g)")
def log_nutrition(bodyweight: float | None, calories: float | None, protein: float | None):
    """Log today's nutrition (upsert). Pipe from Apple Health via Claude iOS."""
    if bodyweight is None and calories is None and protein is None:
        raise click.UsageError("Provide at least one of --bodyweight, --calories, --protein")
    entry = log_today(bodyweight_lb=bodyweight, calories_kcal=calories, protein_g=protein)
    click.echo(f"  Logged {entry['date']}: {entry}")
    maint = estimate_maintenance()
    if maint is not None:
        click.echo(f"  Rolling maintenance: {maint:.0f} kcal")
    else:
        click.echo("  Maintenance: need 7+ days of data")


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
def push(day: str | None, all_week: bool):
    """Generate routine(s) and push them to Hevy."""
    if all_week:
        routines = generate_week_routines()
    else:
        routines = [generate_routine(day)]

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
@click.option("--calendar", default="Home", help="iCloud calendar name. Default: Home")
@click.option("--push-hevy/--no-push-hevy", default=True, help="Also push routines to Hevy.")
@click.option("--cardio/--no-cardio", default=True, help="Also add cardio (Peloton) events to calendar.")
def schedule(start: str | None, hour: int, calendar: str, push_hevy: bool, cardio: bool):
    """Schedule a full training block: push to Hevy + iCloud calendar.

    Generates the same exercises for the entire block (seeded by block number).
    Creates calendar events for every workout day across all weeks.
    """
    state = load_state()
    block = state["current_block"]
    block_weeks = state["block_length_weeks"]

    click.echo(f"  Scheduling Block {block} ({block_weeks} weeks)\n")

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

    # Schedule on calendar
    start_date = date.fromisoformat(start) if start else None
    workout_time = time(hour, 0)

    click.echo(f"\n  Adding {block_weeks * len(routines)} events to '{calendar}' calendar...")
    events = schedule_block(routines, start_date, workout_time, calendar)

    ok = sum(1 for e in events if e["success"])
    click.echo(f"  Created {ok}/{len(events)} calendar events")

    for e in events:
        status = "ok" if e["success"] else "FAILED"
        click.echo(f"    {e['date']}  {e['title']}  ({e['duration_min']} min)  [{status}]")

    # Cardio events (calendar only, no Hevy routines)
    if cardio:
        click.echo(f"\n  Adding cardio events to '{calendar}' calendar...")
        cardio_events = schedule_cardio_block(start_date, calendar)
        if cardio_events:
            ok_c = sum(1 for e in cardio_events if e["success"])
            click.echo(f"  Created {ok_c}/{len(cardio_events)} cardio events")
            for e in cardio_events:
                status = "ok" if e["success"] else "FAILED"
                click.echo(f"    {e['date']}  {e['title']}  ({e['duration_min']} min)  [{status}]")
        else:
            click.echo("  (no cardio config found in profile.yaml)")

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


if __name__ == "__main__":
    cli()
