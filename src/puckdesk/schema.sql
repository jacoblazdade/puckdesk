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

-- Yahoo OAuth tokens, once Yahoo approves API access.
create table if not exists oauth_tokens (
    provider       text primary key,
    access_token   text not null,
    refresh_token  text not null,
    expires_at     timestamptz not null,
    updated_at     timestamptz not null default now()
);
