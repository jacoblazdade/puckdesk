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
     slot and injury status;
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
4. Finish with at most three lines per league: the moves to make before
   waivers and the swing categories. If `matchup.goalie_minimum` shows a real
   chance of falling short, say how many appearances are still needed. If a
   move says `confirm_goalie_start`, say it needs the 17:00 starter check.
   Mention any lineup change or podcast/article mention in the digest that
   affects a suggested move.
5. On Sundays, also call puckdesk `search_media` for my players and the top
   free agents over the last 7 days, and add what Keeping Karlsson and
   DobberHockey said about next week's adds and drops (with the episode
   timestamp).

If a connector call fails, say which one and still run whatever you can.
