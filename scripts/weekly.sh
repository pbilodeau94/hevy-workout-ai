#!/bin/bash
# Weekly automation: advance the week, and if a new block starts, schedule it.
# Run by launchd every Sunday evening.

set -e

PROJECT_DIR="$HOME/Developer/hevy-workout-ai"
VENV="$PROJECT_DIR/.venv/bin/activate"
LOG="$PROJECT_DIR/logs/weekly.log"

mkdir -p "$PROJECT_DIR/logs"

{
    echo "=== $(date) ==="

    source "$VENV"
    cd "$PROJECT_DIR"

    # Read current state
    WEEK=$(python3 -c "from hevy_workout_ai.config import load_state; print(load_state()['current_week_in_block'])")
    BLOCK_LEN=$(python3 -c "from hevy_workout_ai.config import load_state; print(load_state()['block_length_weeks'])")
    BLOCK=$(python3 -c "from hevy_workout_ai.config import load_state; print(load_state()['current_block'])")

    echo "Current: Block $BLOCK, Week $WEEK/$BLOCK_LEN"

    # Advance the week
    hevy advance

    # If we just started a new block, schedule it
    NEW_WEEK=$(python3 -c "from hevy_workout_ai.config import load_state; print(load_state()['current_week_in_block'])")
    if [ "$NEW_WEEK" -eq 1 ]; then
        echo "New block started! Scheduling..."
        hevy schedule --push-hevy
    fi

    echo "Done."
    echo ""
} >> "$LOG" 2>&1
