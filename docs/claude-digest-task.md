# Morning digest: scheduled task prompt for Claude

Schedule this in Claude once both connectors work: **puckdesk** (this server)
and a Yahoo connector (Flaim until Yahoo approves direct API access). Run it
daily at 06:50 Cologne time. From 25 Oct to 1 Nov 2026 and from 14 to 28 Mar
2027, Europe and the US are on different clock-change dates and waivers clear
at 08:00 instead of 09:00; 06:50 still leaves an hour.

The Puckdesk Digest page reads the stored digest by itself, so the task only
has to compute it and send a short summary.

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
3. Call puckdesk `morning_digest` with it. The result is stored and shows up
   on the Puckdesk Digest page.
4. Finish with at most three lines per league: the moves to make before
   waivers and the swing categories. If a move says `confirm_goalie_start`,
   say it needs the 17:00 starter check.

If a connector call fails, say which one and still run whatever you can.
