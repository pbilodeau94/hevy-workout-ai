# Multi-instance refactor plan

Goal: ship the app so one (non-technical) friend can run their own instance on
Streamlit Cloud, and Phil can use his own instance from his phone. Free tier
only. Two separate Streamlit Cloud deployments, one shared Supabase project for
state persistence.

## Confirmed scope

- Two Streamlit Cloud deploys: `phil` fork, `friend` fork. Each forks the repo.
- Friend's instance ships **without** the AI coach tab (no Anthropic key
  required). They use their own Claude / ChatGPT for coaching conversations.
- Friend's enabled integrations: MacroFactor, Hevy, Whoop, Strava.
- HealthKit, Apple Reminders, launchd jobs are Phil-only and gated behind
  `platform.system() == "Darwin"` + file-existence checks.
- Phil's Mac launchd jobs keep running and write to Supabase (so his phone
  always sees fresh data without him pushing buttons).

## Storage classification

### Read-only, stays in repo as YAML
- `config/profile.yaml` — preferences, no secrets
- `config/exercises.yaml` — exercise database
- `config/programs.yaml` — program templates
- `config/pt_routine.yaml` — PT exercises

These are forked with the repo. Friend can edit their own copy via GitHub web
UI if they want to customize.

### Writeable per-user state → Supabase
| File | Purpose | Table |
|---|---|---|
| `config/state.yaml` | small key/value state (last-workout date, etc.) | `user_state` |
| `config/whoop_log.yaml` | Whoop recovery + sleep history | `whoop_log` |
| `config/nutrition_log.yaml` | daily bodyweight / kcal / protein | `nutrition_log` |
| `config/mf_cache.yaml` | MF day-cache | `mf_cache` |
| `config/coach_session.json` | Anthropic session IDs per surface | `coach_session` (Phil only) |
| `config/coach_log.jsonl` | coach Q&A append log | `coach_log` (Phil only) |
| `config/doms_log.yaml` | DOMS check-in history | `doms_log` |
| `config/recovery.yaml` | manual recovery overrides | `user_state` (subkey) |
| `config/.mf_refresh_token` | MF refresh token | `auth_tokens` |
| `.whoop_tokens.json` | Whoop OAuth tokens | `auth_tokens` |
| `.strava_tokens.json` | Strava OAuth tokens | `auth_tokens` |

All tables keyed by `user_id` (text, e.g. `phil`, `friend`).

## Open questions / assumptions made

1. **Supabase project** — assuming a new project named `hevy-coach` (cleaner
   than reusing existing). Phil to create + paste URL/anon-key/service-key into
   `.env` and Streamlit secrets. Not creating yet.
2. **Launchd jobs** — keeping them, pointing them at Supabase via the Store
   layer (they read `USER_ID=phil` from the plist env).
3. **Coach memory** — per-instance only, friend's instance ships without coach.
4. **MacroFactor auth on Streamlit Cloud** — Keychain isn't available. Code
   falls back to env vars (`MF_EMAIL`, `MF_PASSWORD`) which friend pastes into
   Streamlit secrets. Refresh token still cached in Supabase.

## Work breakdown (3 PRs)

### PR 1 — Storage layer (this PR)
- `db/schema.sql` — Supabase tables
- `src/hevy_workout_ai/store.py` — `Store` class, USER_ID-keyed
- Local-file fallback when `SUPABASE_URL` is unset (so Phil's local dev keeps
  working unchanged during the migration)
- Replace direct file I/O at every call site (whoop_log, mf_sync, coach, doms,
  recovery, nutrition, generator-state, cli)
- Migration script: read existing YAML/JSON → write to Supabase under
  `user_id=phil`

### PR 2 — OAuth + onboarding + sync buttons
- `?oauth=whoop` / `?oauth=strava` callback pages in `web.py`
- Register Streamlit Cloud redirect URIs with existing Whoop/Strava dev apps
- Onboarding page: paste MF email/password, paste Hevy API key, click Connect
  Whoop, click Connect Strava
- "Sync now" buttons on Coach/Fitness tabs (Whoop, MF, Strava) for cloud
  instances that don't have launchd
- Friend-fork branch: strip coach tab + Anthropic dep

### PR 3 — Mobile polish + setup doc
- Tighten Coach + workout views for narrow screens (single-column layout under
  640px)
- `SETUP.md` with screenshots: fork → Streamlit Cloud deploy → secrets paste →
  onboarding flow

## Estimated effort
~10 hours total. PR 1 is the bulk (~5h), PR 2 ~3h, PR 3 ~2h.

## Out of scope
- App Store / TestFlight distribution
- Native HealthKit on friend's instance
- Push notifications (web app limitation)
- Sharing data between Phil and friend (separate user_ids, no cross-reads)
