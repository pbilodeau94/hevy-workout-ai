-- Schema for the multi-instance store. Apply once in the Supabase SQL editor.
--
-- Design: minimal. Everything writeable is either a small JSONB blob keyed by
-- (user_id, key), an append-only log row, or an OAuth/refresh-token bundle.
-- The data is small (single-user fitness logs), so we don't normalize.

create table if not exists user_kv (
  user_id    text        not null,
  key        text        not null,
  value      jsonb       not null,
  updated_at timestamptz not null default now(),
  primary key (user_id, key)
);

-- Append-only coach Q&A log. Phil only; friend's fork ships without the coach.
create table if not exists coach_log (
  id      bigserial primary key,
  user_id text        not null,
  ts      timestamptz not null default now(),
  payload jsonb       not null
);
create index if not exists coach_log_user_ts on coach_log (user_id, ts desc);

-- Per-provider auth tokens (Whoop, Strava, MacroFactor refresh, Hevy key, etc.)
create table if not exists auth_tokens (
  user_id    text        not null,
  provider   text        not null,
  tokens     jsonb       not null,
  updated_at timestamptz not null default now(),
  primary key (user_id, provider)
);

-- RLS: enable but for v1 we use the service-role key from the server, so no
-- per-row policies are needed. If we later expose direct DB access from the
-- browser we add policies here.
alter table user_kv     enable row level security;
alter table coach_log   enable row level security;
alter table auth_tokens enable row level security;
