"""Load YAML config files."""

from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    with open(path) as f:
        return yaml.safe_load(f)


def load_profile() -> dict:
    return load_yaml("profile.yaml")


def load_exercises() -> dict:
    return load_yaml("exercises.yaml")


def load_programs() -> dict:
    return load_yaml("programs.yaml")


def load_state() -> dict:
    return load_yaml("state.yaml")


def save_state(state: dict) -> None:
    path = CONFIG_DIR / "state.yaml"
    with open(path, "w") as f:
        yaml.dump(state, f, default_flow_style=False, sort_keys=False)
