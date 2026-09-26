# Morning digest: scheduled task prompt for Claude

Set this up as a scheduled task in Claude once both connectors work:
**puckdesk** (this server) and a Yahoo connector (Flaim until Yahoo approves
direct API access). Run it daily at 07:00 Cologne time; Monday's run should be
earlier (06:00) during the weeks when Europe and the US are on different clock
changes, because waivers then clear at 08:00.

---

For each of my two Yahoo fantasy hockey leagues:

1. From the Yahoo connector, get: league settings (scoring categories, roster
   positions, weekly add limit), my roster with injury statuses, this week's
   opponent and their roster, both teams' category totals so far this week,
   the adds I've used this week, and the best available players: about 40
   skaters across C, LW, RW and D, plus 8 goalies, marked FA or on waivers
   (with the date they clear).
2. Call puckdesk `league_state_guide` once, then build a LeagueState for the
   league exactly in that shape. Use the league names "League 1" and
   "League 2". Leave tags out; the server has them.
3. Call puckdesk `morning_digest` with it.
4. Update my digest artifact with the result for both leagues.
5. Finish with at most three lines per league: the moves to make before
   waivers and the swing categories. If a move says `confirm_goalie_start`,
   say it needs the 17:00 starter check.

If a connector call fails, say which one and still run whatever you can.
