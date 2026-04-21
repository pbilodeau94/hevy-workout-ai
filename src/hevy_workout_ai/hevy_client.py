"""Thin wrapper around the Hevy REST API v1."""

from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.hevyapp.com/v1"


def _api_key() -> str:
    key = os.environ.get("HEVY_API_KEY", "")
    if not key:
        raise RuntimeError("HEVY_API_KEY not set. Add it to .env")
    return key


def _headers() -> dict:
    return {"api-key": _api_key(), "Content-Type": "application/json"}


def _get(path: str, params: dict | None = None) -> dict:
    resp = httpx.get(f"{BASE_URL}{path}", headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _post(path: str, json_body: dict) -> dict:
    resp = httpx.post(f"{BASE_URL}{path}", headers=_headers(), json=json_body, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ── Exercises ────────────────────────────────────────────────────────────────


def list_exercise_templates(page: int = 1, page_size: int = 100) -> dict:
    return _get("/exercise_templates", {"page": page, "pageSize": page_size})


# ── Exercise History ─────────────────────────────────────────────────────────


def get_exercise_history(exercise_template_id: str, page: int = 1, page_size: int = 5) -> dict:
    return _get(
        f"/exercise_history/{exercise_template_id}",
        {"page": page, "pageSize": page_size},
    )


# ── Routines ─────────────────────────────────────────────────────────────────


def list_routines(page: int = 1, page_size: int = 5) -> dict:
    return _get("/routines", {"page": page, "pageSize": page_size})


def create_routine(routine: dict) -> dict:
    """Create a routine in Hevy.

    routine should match:
    {
        "routine": {
            "title": str,
            "folder_id": int | None,
            "notes": str,
            "exercises": [
                {
                    "exercise_template_id": str,
                    "superset_id": int | None,
                    "rest_seconds": int | None,
                    "notes": str | None,
                    "sets": [
                        {
                            "type": "warmup" | "normal" | "failure" | "dropset",
                            "weight_kg": float | None,
                            "reps": int | None,
                        }
                    ]
                }
            ]
        }
    }
    """
    return _post("/routines", routine)


# ── Routine Folders ──────────────────────────────────────────────────────────


def list_routine_folders(page: int = 1, page_size: int = 10) -> dict:
    return _get("/routine_folders", {"page": page, "pageSize": page_size})


def create_routine_folder(title: str) -> dict:
    return _post("/routine_folders", {"routine_folder": {"title": title}})


# ── Workouts ─────────────────────────────────────────────────────────────────


def list_workouts(page: int = 1, page_size: int = 5) -> dict:
    return _get("/workouts", {"page": page, "pageSize": page_size})


def create_workout(workout: dict) -> dict:
    """Create a logged workout in Hevy.

    workout should match:
    {
        "workout": {
            "title": str,
            "description": str | None,
            "start_time": str (ISO 8601),
            "end_time": str (ISO 8601),
            "is_private": bool,
            "exercises": [
                {
                    "exercise_template_id": str,
                    "superset_id": int | None,
                    "notes": str | None,
                    "sets": [
                        {
                            "type": "warmup" | "normal" | "failure" | "dropset",
                            "weight_kg": float | None,
                            "reps": int | None,
                            "rpe": float | None,
                        }
                    ]
                }
            ]
        }
    }
    """
    return _post("/workouts", workout)
