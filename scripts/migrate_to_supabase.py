"""Push existing local config files into Supabase under USER_ID.

Usage:
  SUPABASE_URL=https://xxx.supabase.co \
  SUPABASE_SERVICE_KEY=sb_secret_... \
  USER_ID=phil \
  python scripts/migrate_to_supabase.py

Reads each local KV / token path via the local backend, then upserts via the
Supabase backend. Safe to re-run — uses PostgREST upsert semantics.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import yaml  # noqa: E402

from hevy_workout_ai import store  # noqa: E402
from hevy_workout_ai.store import _LOCAL_KV, _LOCAL_TOKENS, _COACH_LOG_FILE  # noqa: E402


def _read_local_kv(key: str):
    path, fmt = _LOCAL_KV[key]
    if not path.exists():
        return None
    text = path.read_text()
    if not text.strip():
        return None
    return yaml.safe_load(text) if fmt == "yaml" else json.loads(text)


def _read_local_tokens(provider: str):
    path = _LOCAL_TOKENS[provider]
    if not path.exists():
        return None
    text = path.read_text().strip()
    if not text:
        return None
    if provider == "macrofactor":
        return text
    return json.loads(text)


def main() -> None:
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY")):
        sys.exit("SUPABASE_URL + SUPABASE_SERVICE_KEY must be set.")
    user_id = os.environ.get("USER_ID", "default")
    print(f"Migrating to Supabase as user_id={user_id!r}")

    for key in _LOCAL_KV:
        val = _read_local_kv(key)
        if val is None:
            print(f"  kv  {key}: skip (empty)")
            continue
        store.set(key, val)
        n = len(val) if hasattr(val, "__len__") else "?"
        print(f"  kv  {key}: pushed (size={n})")

    for provider in _LOCAL_TOKENS:
        tok = _read_local_tokens(provider)
        if tok is None:
            print(f"  tok {provider}: skip (missing)")
            continue
        store.set_tokens(provider, tok)
        print(f"  tok {provider}: pushed")

    if _COACH_LOG_FILE.exists():
        lines = [ln for ln in _COACH_LOG_FILE.read_text().splitlines() if ln.strip()]
        for ln in lines:
            try:
                store.append_coach_log(json.loads(ln))
            except json.JSONDecodeError:
                continue
        print(f"  log coach_log: pushed {len(lines)} entries")
    else:
        print("  log coach_log: skip (missing)")

    print("Done.")


if __name__ == "__main__":
    main()
