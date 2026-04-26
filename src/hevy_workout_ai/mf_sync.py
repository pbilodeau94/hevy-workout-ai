"""Automated MacroFactor nutrition sync.

Uses a Node shim (`scripts/mf_raw_dump.mjs`) that talks directly to MF's API
via `@sjawhar/macrofactor-mcp`'s `MacroFactorClient`. Reads raw Firestore
food docs (so `k:"n"` recipe entries are not silently dropped — see
`memory/mf_sync_raw.md`), sums macros with the same `userQty*unitWeight/
servingGrams` multiplier MF itself uses, and upserts into
`config/nutrition_log.yaml` via `nutrition.log_today`.

Credentials are read from macOS Keychain:
  service=macrofactor, account=<user email>, password=<MF password>
A refresh token is cached at `config/.mf_refresh_token` (mode 600) after the
first successful login to avoid re-hitting the password login endpoint daily.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import date, timedelta
from pathlib import Path

from . import store
from .config import CONFIG_DIR, load_profile
from .nutrition import log_today

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIM_PATH = REPO_ROOT / "scripts" / "mf_raw_dump.mjs"
REFRESH_TOKEN_FILE = CONFIG_DIR / ".mf_refresh_token"

KEYCHAIN_SERVICE = "macrofactor"
PASSWORD_FILE = Path.home() / ".config" / "hevy" / "macrofactor_password"
# Fiber is stored as a top-level nutrient field keyed by USDA nutrient id.
FIBER_NUTRIENT_IDS = ("291",)


def _keychain_get(account: str, service: str = KEYCHAIN_SERVICE) -> str | None:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", service, "-w"],
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip() or None
    except subprocess.CalledProcessError:
        return None


def _password_file_get() -> str | None:
    if not PASSWORD_FILE.exists():
        return None
    try:
        return PASSWORD_FILE.read_text().strip() or None
    except Exception:
        return None


def _load_cached_refresh_token() -> str | None:
    try:
        tok = store.get_tokens("macrofactor")
    except Exception:
        return None
    if isinstance(tok, dict):
        tok = tok.get("refresh_token")
    return (tok or "").strip() or None if tok else None


def _save_cached_refresh_token(tok: str) -> None:
    store.set_tokens("macrofactor", tok)


def _multiplier(entry: dict) -> float:
    """Mirror MacroFactorClient FoodEntry.multiplier(): y*w/g, default 1."""
    try:
        sg = float(entry.get("g") or 0)
        if sg <= 0:
            return 1.0
        uq = float(entry.get("y") if entry.get("y") is not None else 1)
        uw = float(entry.get("w") if entry.get("w") is not None else sg)
        return uq * uw / sg
    except (TypeError, ValueError):
        return 1.0


def _num(v) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _sum_day(doc: dict) -> dict | None:
    """Sum calories/protein/fiber from a raw MF food-log doc.

    Includes every non-deleted entry regardless of `k` (so `k:"n"` recipes
    are counted). Returns None if the doc is empty / errored.
    """
    if not isinstance(doc, dict) or doc.get("__error"):
        return None

    cal = prot = fib = 0.0
    count = 0
    for key, val in doc.items():
        if not isinstance(val, dict):
            continue
        # AI-generated recipes (k:"n") routinely arrive with d:true even though
        # the user consumed them — MF's AI flow soft-deletes the parent recipe
        # after generation. Only honor d:true for non-AI entries.
        if val.get("d") is True and val.get("k") != "n":
            continue
        # Entries have macro fields c/p/f plus optional nutrient IDs. Non-entry
        # metadata fields won't have `c`/`p`.
        if val.get("c") is None and val.get("p") is None:
            continue
        m = _multiplier(val)
        cal += _num(val.get("c")) * m
        prot += _num(val.get("p")) * m
        for nid in FIBER_NUTRIENT_IDS:
            if nid in val:
                fib += _num(val.get(nid)) * m
                break
        count += 1

    if count == 0:
        return None
    return {
        "calories_kcal": round(cal, 1),
        "protein_g": round(prot, 1),
        "fiber_g": round(fib, 1),
        "entry_count": count,
    }


def _run_shim(dates: list[str], email: str, password: str | None) -> dict:
    import tempfile

    env = os.environ.copy()
    env["MACROFACTOR_USERNAME"] = email
    if password:
        env["MACROFACTOR_PASSWORD"] = password
    cached = _load_cached_refresh_token()
    if cached:
        env["MACROFACTOR_REFRESH_TOKEN"] = cached

    with tempfile.NamedTemporaryFile(suffix=".mf_token", delete=False) as tf:
        out_path = Path(tf.name)
    env["MF_REFRESH_TOKEN_OUT_FILE"] = str(out_path)

    try:
        proc = subprocess.run(
            ["node", str(SHIM_PATH), *dates],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0 and cached and password and "auth failed" in (proc.stderr or ""):
            env.pop("MACROFACTOR_REFRESH_TOKEN", None)
            proc = subprocess.run(
                ["node", str(SHIM_PATH), *dates],
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
        if proc.returncode != 0:
            raise RuntimeError(f"mf_raw_dump.mjs failed ({proc.returncode}): {proc.stderr.strip()}")

        if out_path.exists():
            new_tok = out_path.read_text().strip()
            if new_tok and new_tok != cached:
                _save_cached_refresh_token(new_tok)

        return json.loads(proc.stdout)
    finally:
        try:
            out_path.unlink()
        except FileNotFoundError:
            pass


def sync_nutrition(days: int = 7, email: str | None = None) -> list[dict]:
    """Pull a rolling window of MF food logs, sum macros, upsert into log.

    Returns a list of per-day dicts: {date, calories_kcal, protein_g, fiber_g, entry_count}.
    Days with no MF entries are skipped (not zeroed) to avoid clobbering
    manually-logged values.
    """
    if email is None:
        email = os.environ.get("MACROFACTOR_EMAIL") or _profile_email_fallback()
    if not email:
        raise RuntimeError(
            "no MacroFactor email configured "
            "(set profile.yaml:macrofactor_email, env MACROFACTOR_EMAIL, or pass email=)"
        )

    password = _keychain_get(email) or _password_file_get()
    cached = _load_cached_refresh_token()
    if not password and not cached:
        raise RuntimeError(
            f"no MacroFactor password found: tried Keychain (service={KEYCHAIN_SERVICE!r}, "
            f"account={email!r}), {PASSWORD_FILE}, and no cached refresh token at {REFRESH_TOKEN_FILE}"
        )

    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days)]
    dates.sort()

    docs = _run_shim(dates, email=email, password=password)

    results = []
    for d in dates:
        summed = _sum_day(docs.get(d) or {})
        if summed is None:
            continue
        log_today(
            on_date=d,
            calories_kcal=summed["calories_kcal"],
            protein_g=summed["protein_g"],
            fiber_g=summed["fiber_g"],
        )
        results.append({"date": d, **summed})
    return results


def _profile_email_fallback() -> str | None:
    try:
        profile = load_profile()
        return profile.get("macrofactor_email") or None
    except Exception:
        return None
