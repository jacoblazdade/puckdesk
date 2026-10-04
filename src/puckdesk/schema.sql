-- puckdesk schema. Safe to run repeatedly.

create table if not exists players (
    id           integer primary key,           -- NHL player id
    full_name    text not null,
    norm_name    text not null,
    initial_key  text not null,
    team         text,
    position     text,                          -- C, L, R, D, G
    updated_at   timestamptz not null default now()
);
create index if not exists players_norm_name on players (norm_name);
create index if not exists players_initial_key on players (initial_key);

create table if not exists games (
    id         bigint primary key,              -- NHL game id
    season     integer not null,                -- e.g. 20262027
    game_type  integer not null,                -- 1 preseason, 2 regular, 3 playoffs
    game_date  date not null,                   -- local date as the NHL lists it
    start_utc  timestamptz,
    home       text not null,
    away       text not null,
    state      text
);
create index if not exists games_date on games (game_date);
create index if not exists games_home on games (home, game_date);
create index if not exists games_away on games (away, game_date);

create table if not exists skater_games (
    player_id     integer not null,
    game_id       bigint not null,
    game_date     date not null,
    season        integer not null,
    team          text,
    goals         integer not null default 0,
    assists       integer not null default 0,
    points        integer not null default 0,
    pp_points     integer not null default 0,
    sh_points     integer not null default 0,
    gwg           integer not null default 0,
    shots         integer not null default 0,
    hits          integer not null default 0,
    blocks        integer not null default 0,
    pim           integer not null default 0,
    faceoff_wins  integer not null default 0,
    plus_minus    integer not null default 0,
    toi_sec       integer,
    pp_toi_sec    integer,
    primary key (player_id, game_id)
);
create index if not exists skater_games_date on skater_games (player_id, game_date);

create table if not exists goalie_games (
    player_id      integer not null,
    game_id        bigint not null,
    game_date      date not null,
    season         integer not null,
    team           text,
    started        boolean not null default false,
    wins           integer not null default 0,
    saves          integer not null default 0,
    shots_against  integer not null default 0,
    goals_against  integer not null default 0,
    shutouts       integer not null default 0,
    toi_sec        integer,
    primary key (player_id, game_id)
);
create index if not exists goalie_games_date on goalie_games (player_id, game_date);

-- Whole-season totals, used as priors (last season) for this season's rates.
create table if not exists season_totals (
    player_id  integer not null,
    season     integer not null,
    kind       text not null,                   -- skater or goalie
    gp         integer not null,
    stats      jsonb not null,
    primary key (player_id, season, kind)
);

-- Roster tags: core, hold, stream. Keyed by league name and normalised player name.
create table if not exists tags (
    league      text not null,
    norm_name   text not null,
    name        text not null,
    tag         text not null check (tag in ('core', 'hold', 'stream')),
    updated_at  timestamptz not null default now(),
    primary key (league, norm_name)
);

create table if not exists digests (
    id          bigserial primary key,
    league      text not null,
    created_at  timestamptz not null default now(),
    payload     jsonb not null
);
create index if not exists digests_league on digests (league, created_at desc);
-- The LeagueState each digest was built from, so a digest can be replayed.
alter table digests add column if not exists state jsonb;

-- Yahoo OAuth tokens, once Yahoo approves API access.
create table if not exists oauth_tokens (
    provider       text primary key,
    access_token   text not null,
    refresh_token  text not null,
    expires_at     timestamptz not null,
    updated_at     timestamptz not null default now()
);

-- News and podcasts ---------------------------------------------------------------

create table if not exists podcast_episodes (
    id              bigserial primary key,
    podcast         text not null,
    guid            text not null unique,
    title           text not null,
    published       timestamptz,
    duration_sec    integer,
    audio_url       text,
    link            text,
    status          text not null default 'new',   -- new, transcribed, skipped, failed
    error           text,
    transcribed_at  timestamptz
);
create index if not exists podcast_episodes_published on podcast_episodes (published desc);

-- About a minute of transcript each, so search hits come with a timestamp.
create table if not exists transcript_windows (
    episode_id  bigint not null references podcast_episodes (id) on delete cascade,
    idx         integer not null,
    start_sec   real not null,
    end_sec     real not null,
    text        text not null,
    tsv         tsvector generated always as (to_tsvector('english', text)) stored,
    primary key (episode_id, idx)
);
create index if not exists transcript_windows_tsv on transcript_windows using gin (tsv);

create table if not exists articles (
    id          bigserial primary key,
    source      text not null,
    guid        text not null unique,
    title       text not null,
    link        text,
    published   timestamptz,
    categories  text[],
    content     text,
    tsv         tsvector generated always as (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(content, ''))) stored
);
create index if not exists articles_tsv on articles using gin (tsv);
create index if not exists articles_published on articles (published desc);

-- Beat-writer tweets collected by Game Day Tweets.
create table if not exists posts (
    id         text primary key,
    source     text not null default 'gamedaytweets',
    account    text not null,
    kind       text,                        -- lines, news, stats, goalies
    posted_at  timestamptz,
    text       text not null,
    url        text,
    tsv        tsvector generated always as (to_tsvector('english', text)) stored
);
create index if not exists posts_tsv on posts using gin (tsv);
create index if not exists posts_posted_at on posts (posted_at desc);


-- Daily Faceoff line combinations, one row per change.
create table if not exists team_lines (
    team        text not null,
    fetched_at  timestamptz not null default now(),
    updated_at  text,
    lines       jsonb not null,
    primary key (team, fetched_at)
);

create table if not exists lineup_changes (
    id           bigserial primary key,
    team         text not null,
    player       text not null,
    field        text not null,            -- line, pp_unit, goalie, injury
    old_value    text,
    new_value    text,
    detected_at  timestamptz not null default now()
);
create index if not exists lineup_changes_detected on lineup_changes (detected_at desc);

-- Starting goalie guesses from Game Day Tweets, per game date (US Eastern).
create table if not exists goalie_guesses (
    game_date   date not null,
    norm_name   text not null,
    name        text not null,
    team        text,
    status      text not null,             -- confirmed, starter, likely, ...
    fetched_at  timestamptz not null default now(),
    primary key (game_date, norm_name)
);

-- Birth dates from the NHL roster API, for age-based upside.
alter table players add column if not exists birth_date date;

-- Strategy per league (win_now, balanced, rebuild). leagues.toml has the
-- defaults; rows here are overrides set with the set_strategy tool.
create table if not exists league_strategy (
    league      text primary key,
    strategy    text not null check (strategy in ('win_now', 'balanced', 'rebuild')),
    note        text,
    updated_at  timestamptz not null default now()
);

-- Yahoo-wide % rostered, one snapshot per player and day, from every LeagueState.
create table if not exists rostered_snapshots (
    snap_date   date not null,
    norm_name   text not null,
    name        text not null,
    team        text,
    pct         real not null,
    primary key (snap_date, norm_name)
);
