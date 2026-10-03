"""Dobber's monthly "Top 300 Keeper League Skaters" table, read from the
article text the media job stores (DobberHockey feed).

Rows look like "210 Max Shabanov MIN  41.7 208 208 -2": rank, name, team,
"y" for defensemen, rating, last month's rank, the month before, change.
The article repeats rows in later tables (risers, new entries); the first
row per player wins, which is the main ranking.
"""

from __future__ import annotations

import re

from . import names

TITLE_PATTERN = "Top 300 Keeper League Skaters%"

_ROW = re.compile(
    r"^\s*(\d{1,3})\s+(.+?)\s+([A-Z]{2,3})\s+(y\s+)?(\d+(?:\.\d+)?)\s+(\d+|NR)\s+(\d+|NR)\s+(-?\d+|NEW)\s*$"
)


def parse(text: str) -> list[dict]:
    out, seen = [], set()
    for line in (text or "").replace("\xa0", " ").splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        name = m.group(2).strip()
        key = names.person(name)
        if key in seen:
            continue
        seen.add(key)
        change = m.group(8)
        out.append(
            {
                "rank": int(m.group(1)),
                "name": name,
                "team": names.team(m.group(3)),
                "defense": bool(m.group(4)),
                "rating": float(m.group(5)),
                "previous_rank": None if m.group(6) == "NR" else int(m.group(6)),
                "change": None if change == "NEW" else int(change),
                "new": change == "NEW",
            }
        )
    return out
