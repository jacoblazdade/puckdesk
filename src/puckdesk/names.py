"""Name and team normalisation, so fantasy-site names match NHL ids."""

from __future__ import annotations

import re
import unicodedata

TEAM_ALIASES = {
    # Fantasy sites and the NHL don't always agree on abbreviations.
    "NJ": "NJD",
    "SJ": "SJS",
    "TB": "TBL",
    "LA": "LAK",
    "MON": "MTL",
    "WAS": "WSH",
    "CLS": "CBJ",
    "CLB": "CBJ",
    "NAS": "NSH",
    "CAL": "CGY",
    "ANH": "ANA",
    "UTAH": "UTA",
    "UHC": "UTA",
    "ARI": "UTA",
    "VEG": "VGK",
    "WIN": "WPG",
}


def team(abbrev: str | None) -> str:
    a = (abbrev or "").strip().upper()
    return TEAM_ALIASES.get(a, a)


_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv)\.?$")


def person(name: str) -> str:
    """'Emil Nygård' and 'emil nygard' both become 'emil nygard'."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace(".", " ").replace("-", " ").replace("'", "")
    s = re.sub(r"\s+", " ", s).strip()
    s = _SUFFIX.sub("", s).strip()
    return s


def last(name: str) -> str:
    """Normalised last name: 'Pierre-Luc Dubois' -> 'dubois', 'J.T. Miller' -> 'miller'."""
    parts = person(name).split(" ")
    return parts[-1] if parts else ""


def initial_key(name: str) -> str:
    """'A. Korhonen' and 'Aleksi Korhonen' share the key 'a korhonen'."""
    parts = person(name).split(" ")
    if len(parts) < 2:
        return person(name)
    return f"{parts[0][0]} {' '.join(parts[1:])}"
