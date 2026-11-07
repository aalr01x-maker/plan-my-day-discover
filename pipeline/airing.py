"""Movies > Airing This Week (owner, 7 Nov 2026): one list, movies only, not from TMDB.

Which movies and their dates come from Wikipedia's "List of American films of <year>", whose tables
give the exact opening day. Wikidata gives each film's IMDb id. Both are read through their official
APIs. IMDb's own calendar and Box Office Mojo are not used: they prohibit automated reading.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from . import common

WIKIPEDIA = "https://en.wikipedia.org/w/api.php"
WIKIDATA = "https://www.wikidata.org/w/api.php"
DAYS = 7
MONTHS = {m: i for i, m in enumerate(("January", "February", "March", "April", "May", "June", "July", "August",
                                      "September", "October", "November", "December"), 1)}
TITLE = re.compile(r"''\[\[([^\]|]+)(?:\|([^\]]+))?\]\]''|''([^'\[\]]+)''")


def page_name(year: int) -> str:
    return f"List of American films of {year}"


def parse_list(wikitext: str, year: int) -> list[dict]:
    """Every film in the release tables: the month and day cells span rows, so they carry over."""
    month = day = None
    out = []
    start = wikitext.find("== January")
    for row in wikitext[max(start, 0):].split("\n|-"):
        if (m := re.search(r'aria-label="(\w+)"', row)) and m.group(1) in MONTHS:
            month, day = m.group(1), None
        if (d := re.search(r"'''(\d{1,2})'''", row)):
            day = int(d.group(1))
        if not month or not day:
            continue
        for line in row.split("\n"):
            if "'''" in line or not line.lstrip().startswith(("|", "{{")):
                continue
            if (hit := TITLE.search(line)):
                try:
                    when = date(year, MONTHS[month], day)
                except ValueError:
                    break
                page = hit.group(1).strip() if hit.group(1) else None
                out.append({"date": when.isoformat(), "title": (hit.group(2) or hit.group(1) or hit.group(3)).strip(), "page": page})
                break
    return out


def in_window(rows: list[dict], first_day: date) -> list[dict]:
    start, end = first_day.isoformat(), (first_day + timedelta(days=DAYS - 1)).isoformat()
    return sorted((r for r in rows if start <= r["date"] <= end), key=lambda r: (r["date"], r["title"].lower()))


def imdb_ids(pages: list[str]) -> dict[str, str]:
    """Wikipedia page -> IMDb id, from Wikidata."""
    found: dict[str, str] = {}
    for i in range(0, len(pages), 50):
        data = common.get(WIKIDATA, params={"action": "wbgetentities", "sites": "enwiki", "titles": "|".join(pages[i:i + 50]),
                                            "props": "claims|sitelinks", "sitefilter": "enwiki", "format": "json"})
        for entity in (data.get("entities") or {}).values():
            page = ((entity.get("sitelinks") or {}).get("enwiki") or {}).get("title")
            claims = (entity.get("claims") or {}).get("P345") or []
            value = ((claims[0].get("mainsnak") or {}).get("datavalue") or {}).get("value") if claims else None
            if page and value:
                found[page] = value
    return found


def this_week(first_day: date) -> list[dict]:
    """This week's films with their IMDb ids. Raises if a list can't be read, so the caller can keep the last one."""
    rows: list[dict] = []
    for year in sorted({first_day.year, (first_day + timedelta(days=DAYS - 1)).year}):
        data = common.get(WIKIPEDIA, params={"action": "parse", "page": page_name(year), "prop": "wikitext", "format": "json", "formatversion": 2})
        parsed = parse_list((data.get("parse") or {}).get("wikitext") or "", year)
        if not parsed:
            raise ValueError(f"no films found on {page_name(year)}")
        rows += parsed
    week = in_window(rows, first_day)
    ids = imdb_ids(sorted({r["page"] for r in week if r["page"]}))
    return [{**r, "imdb": ids.get(r["page"])} for r in week]
