"""A booking's time of day, from its note (the owner, 2026-11-13). Productive gives a booking dates and an amount, never
a time of day, so a manager who plans "from 14:00 to 16:00" can say it only in the note; Pulsar reads it there for
the Planned column (productive.bookings).

What counts as a time:
  - a range: "14:00-16:00", "14:00 – 16", "9.30 to 11:00", "2pm-4pm", "2-4 pm", "2:30–4pm", "from 14 to 16",
    "з 14 до 16", "з 14:00 по 16:00";
  - a start alone: "14:00", "о 14:00", "at 2pm", "from 2 pm": the booking is then as long as its time that day; an end
    after it ("о 14:00, до 16:00") makes it a range; a clock after "до", "by", "until" or "deadline" is no start;
  - a time zone right after it, or in brackets: "ET", "EST", "Toronto", "PT", "UTC", "Kyiv", "за Києвом" and the
    like; without one, the person's own (Settings > Time zone).
Bare numbers ("14-16", "з 14") count only after a word that says time ("from", "з", "at", "о"), so "pages 2-3",
"v1.2-1.4" or "3 tasks" are never one. A point stands for a colon only before 00, 15, 30 or 45 ("14.00", "9.30"):
"18.11" is a date. A time that falls outside the day once in the person's zone is no time."""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_AMPM = r"(a\.?\s?m\.?|p\.?\s?m\.?)"
_CLOCK = rf"(\d{{1,2}})(?::(\d{{2}})|\.(00|15|30|45)(?!\d))?\s*{_AMPM}?"
_SEP = r"\s*(?:-|–|—|to|till|until|до|по)\s*"
_FROM = r"(?:from|between|at|starting|start|з|із|зі|від|о|об|с)"
_ZONE = r"\s*\(?\s*(et|est|edt|eastern|toronto|montreal|ct|cst|cdt|central|mt|mst|mdt|mountain|edmonton|pt|pst|pdt|pacific|" \
        r"vancouver|utc|gmt|kyiv|kiev|київ\w*|києв\w*|за\s+києвом|по\s+києву|eet|eest|cet|cest)\b\.?\)?"
_END = re.compile(rf"\s*,?\s*(?:до|по|to|till|until|-|–|—)\s*{_CLOCK}(?![\w:])", re.I)  # "о 14:00, до 16:00"
_NOT_START = {"до", "по", "by", "until", "till", "deadline", "due", "before", "дедлайн"}
_RANGE = re.compile(rf"(?<![\w.:])(?:({_FROM})\s+)?{_CLOCK}{_SEP}{_CLOCK}(?![\w:])(?:{_ZONE})?", re.I)
_START = re.compile(rf"(?<![\w.:])(?:({_FROM})\s+)?{_CLOCK}(?![\w:])(?:{_ZONE})?", re.I)
ZONES = {"et": "America/Toronto", "est": "America/Toronto", "edt": "America/Toronto", "eastern": "America/Toronto",
         "toronto": "America/Toronto", "montreal": "America/Toronto", "ct": "America/Chicago", "cst": "America/Chicago",
         "cdt": "America/Chicago", "central": "America/Chicago", "mt": "America/Edmonton", "mst": "America/Edmonton",
         "mdt": "America/Edmonton", "mountain": "America/Edmonton", "edmonton": "America/Edmonton",
         "pt": "America/Vancouver", "pst": "America/Vancouver", "pdt": "America/Vancouver", "pacific": "America/Vancouver",
         "vancouver": "America/Vancouver", "utc": "UTC", "gmt": "UTC", "kyiv": "Europe/Kyiv", "kiev": "Europe/Kyiv",
         "eet": "Europe/Kyiv", "eest": "Europe/Kyiv", "cet": "Europe/Berlin", "cest": "Europe/Berlin"}


def _zone(word):
    w = re.sub(r"\s+", " ", (word or "").lower()).strip(" .()")
    if not w:
        return None
    if w.startswith(("київ", "києв", "за києвом", "по києву")):
        return ZoneInfo("Europe/Kyiv")
    return ZoneInfo(ZONES[w]) if w in ZONES else None


def _hm(h: str, m: str | None, ampm: str | None) -> int | None:
    """Minutes of the day, or None when it is not a time of day."""
    h, m = int(h), int(m or 0)
    if m > 59:
        return None
    if ampm:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ampm.lower().startswith("p") else 0)
    elif h > 24 or (h == 24 and m):
        return None
    return h * 60 + m


def _strong(m: str | None, ampm: str | None) -> bool:
    """A time on its own, with no word before it: "14:00" or "2pm", never a bare "14"."""
    return bool(ampm) or m is not None


def find(note: str) -> dict | None:
    """{"s", "e" (None for a start alone), "zone" (a ZoneInfo or None), "text"}: the first time in the note, in its own
    clock (minutes of the day), or None."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", note or ""))  # a note can be rich text
    for m in _RANGE.finditer(text):
        word, h1, c1, d1, a1, h2, c2, d2, a2, zone = m.groups()
        m1, m2 = c1 or d1, c2 or d2
        if not (word or _strong(m1, a1) or _strong(m2, a2)) or (word and word.lower() in ("at", "о", "об")
                                                                    and not (_strong(m1, a1) or _strong(m2, a2))):
            continue
        if a2 and not a1:  # "2-4pm": the first takes the second's half of the day, unless that puts it after the end
            a1 = a2 if int(h1) % 12 <= int(h2) % 12 else ("am" if a2.lower().startswith("p") else "pm")
        s, e = _hm(h1, m1, a1), _hm(h2, m2, a2)
        if s is None or e is None:
            continue
        if e <= s and not (a1 or a2) and e < 12 * 60 and e + 12 * 60 > s:  # "з 11 до 1": one in the afternoon
            e += 12 * 60
        if e <= s:
            continue
        return {"s": s, "e": e, "zone": _zone(zone), "text": m.group(0).strip()}
    for m in _START.finditer(text):
        word, h1, c1, d1, a1, zone = m.groups()
        m1 = c1 or d1
        if not _strong(m1, a1):  # a start alone needs a clock ("14:00", "2pm"), whatever word comes before it
            continue
        before = re.findall(r"[\w']+", text[:m.start()].lower())
        if not word and before and before[-1] in _NOT_START:  # "до 17:00", "deadline 17:00": an end, not a start
            continue
        s = _hm(h1, m1, a1)
        if s is None or s >= 24 * 60:
            continue
        end, e = _END.match(text, m.end()), None
        if end:
            h2, c2, d2, a2 = end.groups()
            e = _hm(h2, c2 or d2, a2)
            e = e if e is not None and e > s else None
        return {"s": s, "e": e, "zone": _zone(zone), "text": (text[m.start():end.end()] if e else m.group(0)).strip()}
    return None


def on_day(note: str, date: str, tz, minutes: int) -> dict | None:
    """{"s", "e", "text"}: the note's time on `date` in the person's zone `tz`, minutes of that day; a start alone runs
    as long as the booking's `minutes` that day (15 at least). None without a time, or when it falls outside that day
    (10 PM in Toronto is the next morning in Kyiv)."""
    got = find(note)
    if not got:
        return None
    day = datetime.strptime(date, "%Y-%m-%d")
    midnight = day.replace(tzinfo=tz)
    zone = got["zone"] or tz

    def mine(at: int) -> float:  # that clock's minute in the person's zone, as minutes from their midnight
        when = (day + timedelta(minutes=at)).replace(tzinfo=zone)
        return (when - midnight).total_seconds() / 60

    s = mine(got["s"])
    e = mine(got["e"]) if got["e"] is not None else s + max(15, minutes)
    if not 0 <= s < 24 * 60:
        return None
    return {"s": int(round(s)), "e": int(round(min(e, 24 * 60))), "text": got["text"]}
