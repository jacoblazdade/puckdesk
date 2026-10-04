# Morning digest: scheduled task prompt for Claude

Schedule this in Claude once both connectors work: **puckdesk** (this server)
and Flaim, the Yahoo connector (until Yahoo approves direct API access). Run it
daily at 06:50 Cologne time. From 25 Oct to 1 Nov 2026 and from 14 to 28 Mar
2027, Europe and the US are on different clock-change dates and waivers clear
at 08:00 instead of 09:00; 06:50 still leaves an hour.

The Puckdesk Digest page reads the stored digest by itself, so the task only
has to compute it and send a short summary.

---

For each of my two Yahoo fantasy hockey leagues, **hockey1234123**
(477.l.60199, my team "Kapri's Papi") and **The League** (477.l.42782, my team
"Dude Where's Makar?"):

1. From Flaim, get:
   - `get_matchups`: this week's opponent and every category value for both
     teams, including GA and SA (Yahoo shows them as display-only stats) and
     goalie appearances (GP) if they're there;
   - `get_roster` for my team and the opponent: positions, today's lineup
     slot, injury status and % rostered if shown;
   - `get_free_agents`: about 40 skaters across C, LW, RW and D plus 8
     goalies, with % rostered;
   - `get_transactions`: everything since Monday 00:00 ET, and at least the
     last 2 days.
2. Call puckdesk `league_state_guide` once, then build a LeagueState for the
   league exactly in that shape. The league needs only its name; the server
   has the settings, week dates, waiver rules and goalie minimum. Don't mark
   waiver players or count adds yourself: pass the transactions and the
   server works both out. Leave tags out; the server has them.
3. Call puckdesk `morning_digest` with it. The result is stored and shows up
   on the Puckdesk Digest page.
4. Read `league_info.strategy` and `league_info.strategy_note` and follow
   them when you pick and explain moves:
   - **win_now** (hockey1234123): this week's category wins come first.
   - **rebuild** (The League): long-term value comes first. Favour young,
     high-upside adds and early breakouts that can be sold for picks; never
     drop a young upside player for a short-term stream. Each move's
     `asset_value` and reasons show why.
   - **balanced**: weigh both.
5. Read the digest's `mentions` (at most 8 players, the newest real item
   each) and call puckdesk `set_news_notes` with one takeaway per player:
   one line, 20 words at most, about what it means for the move or the
   player (for example "Moved to PP1 at Friday's practice; add before
   Monday."). Skip players whose item says nothing new. The notes are stored
   on the digest (`latest_digest` returns them as `news_notes`).
6. Finish with at most three lines per league: the moves to make before
   waivers and the swing categories (for rebuild, also the best `sell_high`
   and `breakout_watch` names). If `matchup.goalie_minimum` shows a real
   chance of falling short, say how many appearances are still needed
   (`so_far_from` shows where the count came from). Pass goalie GP as
   `goalie_appearances` only if the Yahoo matchup shows it. If a
   move says `confirm_goalie_start`, say it needs the 17:00 starter check.
7. On Sundays, also call puckdesk `search_media` for my players and the top
   free agents over the last 7 days, and add what Keeping Karlsson and
   DobberHockey said about next week's adds and drops (with the episode
   timestamp).

If a connector call fails, say which one and still run whatever you can.
