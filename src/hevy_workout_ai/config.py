"""Load YAML config files."""

from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    with open(path) as f:
        return yaml.safe_load(f)


def load_profile() -> dict:
    from . import store
    p = store.get("profile")
    if p is not None:
        return p
    return load_yaml("profile.yaml")


def load_exercises() -> dict:
    return load_yaml("exercises.yaml")


def load_programs() -> dict:
    return load_yaml("programs.yaml")


def load_state() -> dict:
    from . import store
    return store.get("state") or {}


def load_pt_routine() -> dict:
    return load_yaml("pt_routine.yaml")


def save_state(state: dict) -> None:
    from . import store
    store.set("state", state)
