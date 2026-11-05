"""Your calendar's events for a day, read from the calendar's private iCal (ICS) address. Read only.

Where the address is: Google Calendar, Settings > (your calendar) > Integrate calendar > "Secret address in iCal
format"; Outlook, Settings > Calendar > Shared calendars > Publish a calendar > the ICS link. It is pasted once in
Pulsar's Settings and kept like the Productive token (config.get_calendar_url). Anyone holding it can read the whole
calendar, so it never goes into the page, the log or an error message.

day() gives one item per event on that day: {s, e, title, all_day, declined, recurring, uid, location?, url?}, with
s and e in minutes from the day's midnight on the wall clock, like the trackers' items, and cut to the day (an event
across midnight shows on both days). `url` is the meeting's Zoom, Meet, Teams or Webex link when there is one,
otherwise the event's own URL. `declined` is true when the calendar's owner declined it (known only when the calendar
is named after its owner's email address, as a Google primary calendar is).

Repeating events are expanded here with the standard library: RRULE (DAILY, WEEKLY, MONTHLY, YEARLY with INTERVAL,
COUNT, UNTIL, BYDAY, BYMONTHDAY, BYMONTH, BYSETPOS, WKST), RDATE, EXDATE, and occurrences moved or cancelled on
their own (RECURRENCE-ID). A rule with BYWEEKNO, BYYEARDAY or a sub-daily frequency shows its first occurrence only.
"""

from __future__ import annotations

import hashlib
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
import zlib
from datetime import date, datetime, time as dtime, timedelta, timezone, tzinfo
from typing import Callable, Optional

from . import __version__, config

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

MAX_BYTES = 30 * 1024 * 1024  # a calendar with years of history is a few MB
FRESH = 300                   # seconds a downloaded calendar is used before asking again
RETRY = 60                    # after a failed download, seconds before trying again
KEEP = 24 * 3600              # how old a copy may be and still stand in while the address cannot be reached
MAX_PERIODS = 5000            # days, weeks, months or years one repeating event is walked through for one day

Get = Callable[[str], str]


class CalendarError(Exception):
    """A calendar that could not be read, in words for the page. Never carries the address."""


# ---- download ------------------------------------------------------------------------------------

_CTX = ssl.create_default_context()


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not str(newurl).lower().startswith("https://"):
            raise CalendarError("The calendar address sent Pulsar to an address that is not HTTPS; it was not followed.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_address(url: str) -> str:
    """webcal:// is https:// for a calendar; anything else but https is refused (the address is a password)."""
    url = (url or "").strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not url.lower().startswith("https://"):
        raise CalendarError("The calendar address must start with https:// or webcal://.")
    return url


def download(url: str, timeout: float = 30) -> str:
    request = urllib.request.Request(https_address(url), headers={
        "User-Agent": f"Pulsar/{__version__}", "Accept": "text/calendar, */*;q=0.5", "Accept-Encoding": "gzip"})
    opener = urllib.request.build_opener(_HttpsOnly, urllib.request.HTTPSHandler(context=_CTX))
    try:
        with opener.open(request, timeout=timeout) as resp:
            raw = resp.read(MAX_BYTES + 1)
            encoding = (resp.headers.get("Content-Encoding") or "").strip().lower()
    except CalendarError:
        raise
    except urllib.error.HTTPError as exc:
        raise CalendarError(http_reason(exc.code)) from None
    except urllib.error.URLError as exc:
        raise CalendarError(f"The calendar could not be reached ({net_reason(exc.reason)}).") from None
    except Exception as exc:  # never the exception's own text: some of them quote the address
        raise CalendarError(f"The calendar could not be reached ({net_reason(exc)}).") from None
    return unpack(raw, encoding)


def http_reason(code: int) -> str:
    if code in (401, 403, 404, 410):
        return (f"The calendar address no longer works (HTTP {code}); it may have been reset. Copy the secret "
                "address again from your calendar's settings and paste it in Settings.")
    if code == 429:
        return "The calendar is limiting requests (HTTP 429); Pulsar tries again in a minute."
    return f"The calendar answered HTTP {code}."


def net_reason(reason) -> str:
    import socket
    if isinstance(reason, socket.gaierror):
        return "its address could not be looked up: offline, or a DNS problem"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "it took too long to answer"
    if isinstance(reason, ssl.SSLError):
        return "the secure connection failed"
    if isinstance(reason, (ConnectionRefusedError, ConnectionResetError)):
        return "the connection was closed"
    return type(reason).__name__


def unpack(raw: bytes, encoding: str = "") -> str:
    """The answer as text: unzipped when it came gzipped, refused when it is too large or not a calendar."""
    too_large = CalendarError("The calendar is larger than 30 MB; Pulsar did not read it.")
    if len(raw) > MAX_BYTES:
        raise too_large
    if encoding == "gzip":
        unzip = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            raw = unzip.decompress(raw, MAX_BYTES + 1)
        except zlib.error:
            raise CalendarError("The calendar's answer arrived damaged; Pulsar tries again shortly.") from None
        if len(raw) > MAX_BYTES or unzip.unconsumed_tail:
            raise too_large
    text = raw.decode("utf-8", "replace").lstrip("﻿")
    if "BEGIN:VCALENDAR" not in text[:4096].upper():
        raise CalendarError("That address gave a web page, not a calendar. Paste the secret address in iCal format "
                            "(it usually ends in .ics).")
    return text


# ---- reading the iCalendar text --------------------------------------------------------------------

def _unfold(text: str) -> list:
    """Lines as written, with the folded ones (a long line goes on after a newline and a space) joined again."""
    out: list = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        elif line:
            out.append(line)
    return out


def _split(line: str):
    """'DTSTART;TZID="A/B":20261105T093000' -> ("DTSTART", {"TZID": "A/B"}, "20261105T093000"); None without a value.
    A colon or semicolon inside a quoted parameter does not count."""
    colon = line.find(":")
    if colon < 0:
        return None
    head = line[:colon]
    if '"' in head:  # a quoted parameter may hold ':' or ';': walk it
        parts, cur, quoted = [], [], False
        for i, ch in enumerate(line):
            if ch == '"':
                quoted = not quoted
            elif not quoted and ch in ";:":
                parts.append("".join(cur))
                cur = []
                if ch == ":":
                    colon = i
                    break
                continue
            cur.append(ch)
        else:
            return None
    else:
        parts = head.split(";")
    params = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.strip().upper()] = v.strip().strip('"')
    return parts[0].strip().upper(), params, line[colon + 1:]


def parse(text: str) -> dict:
    """{"events": [{NAME: [(params, value), ...]}], "tz": the calendar's own zone, "owner": its owner's email or ""}.
    Properties of a reminder or anything else nested inside an event are left out."""
    cal: dict = {"events": [], "tz": "", "owner": ""}
    stack: list = []
    event = None
    for line in _unfold(text):
        got = _split(line)
        if got is None:
            continue
        name, params, value = got
        if name == "BEGIN":
            stack.append(value.strip().upper())
            if stack[-1] == "VEVENT":
                event = {}
        elif name == "END":
            kind = value.strip().upper()
            if kind in stack:
                while stack.pop() != kind:
                    pass
                if kind == "VEVENT" and event is not None:
                    cal["events"].append(event)
                    event = None
        elif event is not None and stack[-1:] == ["VEVENT"]:
            event.setdefault(name, []).append((params, value))
        elif stack == ["VCALENDAR"]:
            if name == "X-WR-TIMEZONE":
                cal["tz"] = value.strip()
            elif name == "X-WR-CALNAME" and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip()):
                cal["owner"] = value.strip().lower()
    return cal


def _text(v: str) -> str:
    return re.sub(r"\\([\\;,nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v or "").strip()


# Outlook names zones the Windows way; the usual ones, as IANA names.
WINDOWS = {
    "UTC": "UTC", "Coordinated Universal Time": "UTC", "GMT Standard Time": "Europe/London",
    "Greenwich Standard Time": "Atlantic/Reykjavik", "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest", "Central European Standard Time": "Europe/Warsaw",
    "Romance Standard Time": "Europe/Paris", "E. Europe Standard Time": "Europe/Chisinau",
    "FLE Standard Time": "Europe/Kiev", "GTB Standard Time": "Europe/Bucharest", "Russian Standard Time": "Europe/Moscow",
    "Turkey Standard Time": "Europe/Istanbul", "Israel Standard Time": "Asia/Jerusalem",
    "Eastern Standard Time": "America/New_York", "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver", "US Mountain Standard Time": "America/Phoenix",
    "Pacific Standard Time": "America/Los_Angeles", "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu", "Atlantic Standard Time": "America/Halifax",
    "Newfoundland Standard Time": "America/St_Johns", "Canada Central Standard Time": "America/Regina",
    "SA Pacific Standard Time": "America/Bogota", "E. South America Standard Time": "America/Sao_Paulo",
    "Arabian Standard Time": "Asia/Dubai", "India Standard Time": "Asia/Kolkata", "China Standard Time": "Asia/Shanghai",
    "Singapore Standard Time": "Asia/Singapore", "Tokyo Standard Time": "Asia/Tokyo",
    "AUS Eastern Standard Time": "Australia/Sydney", "New Zealand Standard Time": "Pacific/Auckland",
}
_zones: dict = {}


def _zone(name: str) -> Optional[tzinfo]:
    """A TZID as a zone: an IANA name, one inside a longer id ("/mozilla.org/20070129_1/Europe/Kyiv"), or a Windows
    name. None when it is none of these."""
    name = (name or "").strip().strip('"')
    if name in _zones:
        return _zones[name]
    found = None
    inside = re.search(r"([A-Za-z_]+/[A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+)?)$", name)
    for candidate in (name, WINDOWS.get(name, ""), inside.group(1) if inside else ""):
        if ZoneInfo and re.fullmatch(r"[A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+){0,2}", candidate or ""):
            try:
                found = ZoneInfo(candidate)
                break
            except Exception:
                continue
    _zones[name] = found
    return found


def _when(value: str, params: dict, zone: tzinfo):
    """(date, True) for a whole day, (aware datetime, False) for a moment, None when unreadable. A time without a
    zone of its own is in `zone`."""
    v = (value or "").strip()
    try:
        if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", v):
            return datetime.strptime(v[:8], "%Y%m%d").date(), True
        naive = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    if v.upper().endswith("Z"):
        return naive.replace(tzinfo=timezone.utc), False
    return naive.replace(tzinfo=zone), False


DURATION = re.compile(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?")


def _duration(v: str) -> Optional[timedelta]:
    m = DURATION.fullmatch((v or "").strip().upper())
    if not m or not any(m.groups()[1:]):
        return None
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    span = timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)
    return -span if m.group(1) == "-" else span


WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _rule(text: str) -> Optional[dict]:
    """An RRULE as a dict, or None for one Pulsar does not expand (the event then shows its first occurrence)."""
    parts = {}
    for p in (text or "").split(";"):
        k, _, v = p.partition("=")
        parts[k.strip().upper()] = v.strip().upper()
    freq = parts.get("FREQ")
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY") or "BYWEEKNO" in parts or "BYYEARDAY" in parts:
        return None

    def ints(key: str, top: int, signed: bool) -> list:
        out = set()
        for x in parts.get(key, "").split(","):
            try:
                n = int(x)
            except ValueError:
                continue
            if 1 <= abs(n) <= top and (signed or n > 0):
                out.add(n)
        return sorted(out)

    byday = []
    for x in parts.get("BYDAY", "").split(","):
        m = re.fullmatch(r"([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)", x.strip())
        if m:
            nth = int(m.group(1)) if m.group(1) else 0
            byday.append((nth or None, WEEKDAYS[m.group(2)]))
    try:
        interval = int(parts.get("INTERVAL") or 1)
        count = int(parts["COUNT"]) if parts.get("COUNT") else None
    except ValueError:
        return None
    return {"freq": freq, "interval": min(max(interval, 1), 1000), "count": count if count and count > 0 else None,
            "until": parts.get("UNTIL") or None, "byday": byday, "bymonthday": ints("BYMONTHDAY", 31, True),
            "bymonth": ints("BYMONTH", 12, False), "bysetpos": ints("BYSETPOS", 366, True),
            "wkst": WEEKDAYS.get(parts.get("WKST", "MO"), 0)}


# ---- repeating events ----------------------------------------------------------------------------

def _last_day(y: int, m: int) -> int:
    return ((date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)).day


def _monthday_ok(d: date, monthdays: list) -> bool:
    last = _last_day(d.year, d.month)
    return any(md == d.day or (md < 0 and last + 1 + md == d.day) for md in monthdays)


def _nth(days: list, nth: Optional[int]) -> list:
    """All of `days` (nth None), or the nth from the start (1, 2…) or from the end (-1, -2…)."""
    if nth is None:
        return days
    return [days[nth - 1 if nth > 0 else nth]] if 0 < abs(nth) <= len(days) else []


def _in_month(y: int, m: int, rule: dict, default_day: int) -> list:
    last = _last_day(y, m)
    monthdays = {md if md > 0 else last + 1 + md for md in rule["bymonthday"]}
    monthdays = {d for d in monthdays if 1 <= d <= last}
    if rule["byday"]:
        first = date(y, m, 1).weekday()
        hits: set = set()
        for nth, wd in rule["byday"]:
            hits.update(_nth(list(range(1 + (wd - first) % 7, last + 1, 7)), nth))
        days = hits & monthdays if rule["bymonthday"] else hits
    elif rule["bymonthday"]:
        days = monthdays
    else:
        days = {default_day} if default_day <= last else set()  # the 31st repeats only in months that have one
    return [date(y, m, d) for d in sorted(days)]


def _in_year(y: int, rule: dict, start: date) -> list:
    if rule["byday"] and not rule["bymonth"]:  # "the 20th Monday of the year": the ordinal counts within the year
        jan1, hits = date(y, 1, 1), set()
        length = (date(y + 1, 1, 1) - jan1).days
        for nth, wd in rule["byday"]:
            same = [jan1 + timedelta(days=k) for k in range((wd - jan1.weekday()) % 7, length, 7)]
            hits.update(_nth(same, nth))
        if rule["bymonthday"]:
            hits = {d for d in hits if _monthday_ok(d, rule["bymonthday"])}
        return sorted(hits)
    months = rule["bymonth"] or (range(1, 13) if rule["bymonthday"] else [start.month])
    return [d for m in months for d in _in_month(y, m, rule, start.day)]


def _period(start: date, rule: dict, i: int) -> tuple:
    """(the period's first day, the days in it that repeat the event) for the i-th day, week, month or year."""
    f, n = rule["freq"], rule["interval"]
    try:
        if f == "DAILY":
            anchor = start + timedelta(days=i * n)
            days = [anchor]
        elif f == "WEEKLY":
            week0 = start - timedelta(days=(start.weekday() - rule["wkst"]) % 7)
            anchor = week0 + timedelta(weeks=i * n)
            wanted = {wd for _, wd in rule["byday"]} or {start.weekday()}
            days = sorted(anchor + timedelta(days=(wd - rule["wkst"]) % 7) for wd in wanted)
        elif f == "MONTHLY":
            years, month = divmod(start.month - 1 + i * n, 12)
            anchor = date(start.year + years, month + 1, 1)
            days = _in_month(anchor.year, anchor.month, rule, start.day)
        else:
            anchor = date(start.year + i * n, 1, 1)
            days = _in_year(anchor.year, rule, start)
    except (ValueError, OverflowError):  # past the year 9999
        return date.max, []
    if rule["bymonth"] and f != "YEARLY":
        days = [d for d in days if d.month in rule["bymonth"]]
    if f == "DAILY" and rule["byday"]:
        days = [d for d in days if d.weekday() in {wd for _, wd in rule["byday"]}]
    if f in ("DAILY", "WEEKLY") and rule["bymonthday"]:
        days = [d for d in days if _monthday_ok(d, rule["bymonthday"])]
    if rule["bysetpos"] and days:
        days = sorted({days[p - 1 if p > 0 else p] for p in rule["bysetpos"] if abs(p) <= len(days)})
    return anchor, days


def _periods_before(start: date, rule: dict, lo: date) -> int:
    f, n = rule["freq"], rule["interval"]
    if f == "DAILY":
        span = (lo - start).days
    elif f == "WEEKLY":
        span = (lo - (start - timedelta(days=(start.weekday() - rule["wkst"]) % 7))).days // 7
    elif f == "MONTHLY":
        span = (lo.year - start.year) * 12 + lo.month - start.month
    else:
        span = lo.year - start.year
    return max(0, span // n)


def expand(start: datetime, rule: dict, lo: datetime, hi: datetime, until: Optional[datetime]) -> list:
    """The starts (naive, on the event's own clock) of a repeating event from lo to hi. Without COUNT the walk begins
    just before lo, so a daily meeting set up years ago costs as little as one set up last week."""
    out: list = []
    seen = 0
    i = 0 if rule["count"] else max(0, _periods_before(start.date(), rule, lo.date()) - 1)
    for _ in range(MAX_PERIODS):
        anchor, days = _period(start.date(), rule, i)
        if anchor > hi.date():
            break
        for d in days:
            t = datetime.combine(d, start.time())
            if t < start:
                continue
            if until is not None and t > until:
                return out
            seen += 1
            if rule["count"] and seen > rule["count"]:
                return out
            if lo <= t <= hi:
                out.append(t)
        i += 1
    return out


# ---- events -----------------------------------------------------------------------------------------

LINK = re.compile(r"https://(?:[A-Za-z0-9-]+\.)*(?:zoom\.us|meet\.google\.com|teams\.microsoft\.com|teams\.live\.com|"
                  r"webex\.com)(?:/[^\s\"'<>\\]*)?", re.I)


def _key(moment, all_day: bool) -> tuple:
    return ("d", moment.isoformat()) if all_day else ("t", int(moment.timestamp()))


def _event(raw: dict, owner: str, local: tzinfo, cal_zone: tzinfo) -> Optional[dict]:
    def first(name: str) -> tuple:
        got = raw.get(name)
        return got[0] if got else ({}, None)

    def when(params: dict, value: str):
        tzid = params.get("TZID")
        return _when(value, params, (_zone(tzid) or cal_zone) if tzid else local)

    got = when(*first("DTSTART"))
    if not got:
        return None
    start, all_day = got
    end = when(*first("DTEND")) if first("DTEND")[1] else None
    span = _duration(first("DURATION")[1] or "")
    if all_day:
        days = (end[0] - start).days if end and end[1] else span.days if span else 1
        length = max(1, days)
    elif end:
        moment = end[0] if not end[1] else datetime.combine(end[0], dtime(), start.tzinfo)
        length = max(0.0, moment.timestamp() - start.timestamp())
    else:
        length = max(0.0, span.total_seconds()) if span else 0.0
    zone = None if all_day else start.tzinfo

    rule = _rule(first("RRULE")[1] or "") if first("RRULE")[1] else None
    until = None
    if rule and rule["until"]:
        u = _when(rule["until"], {}, zone or local)
        if u:
            if u[1] or all_day:
                until = datetime.combine(u[0] if u[1] else u[0].date(), dtime(23, 59, 59))
            else:
                until = u[0].astimezone(zone).replace(tzinfo=None)

    def moments(name: str) -> list:
        out = []
        for params, value in raw.get(name, []):
            for v in value.split(","):
                m = when(params, v)
                if m:
                    out.append(m)
        return out

    skip = {_key(m, is_date) for m, is_date in moments("EXDATE")}
    rdates = [datetime.combine(m, dtime()) if is_date else m.astimezone(zone or local).replace(tzinfo=None)
              for m, is_date in moments("RDATE") if is_date == all_day]
    rid = when(*first("RECURRENCE-ID")) if first("RECURRENCE-ID")[1] else None

    url = None
    for name in ("X-GOOGLE-CONFERENCE", "LOCATION", "URL", "DESCRIPTION"):
        for _, value in raw.get(name, []):
            m = LINK.search(_text(value))
            if m:
                url = m.group(0).rstrip(".,;)")
                break
        if url:
            break
    if not url and re.match(r"https?://\S+$", (first("URL")[1] or "").strip()):
        url = first("URL")[1].strip()

    declined = bool(owner) and any(
        value.strip().lower().replace("mailto:", "") == owner and params.get("PARTSTAT", "").upper() == "DECLINED"
        for params, value in raw.get("ATTENDEE", []))
    return {"uid": (first("UID")[1] or "").strip(), "title": _text(first("SUMMARY")[1] or ""),
            "location": _text(first("LOCATION")[1] or "")[:300] or None, "url": url, "declined": declined,
            "all_day": all_day, "start": start, "length": length, "zone": zone, "rule": rule, "until": until,
            "skip": skip, "rdates": rdates, "rid": _key(*rid) if rid else None,
            "cancelled": (first("STATUS")[1] or "").strip().upper() == "CANCELLED"}


def build(cal: dict, local: tzinfo) -> list:
    """The calendar's events, ready to be placed on any day. An occurrence moved or cancelled on its own is taken out
    of its series; the moved one comes back as an event of its own. An event that cannot be read is left out."""
    cal_zone = _zone(cal.get("tz", "")) or local
    out, replaced = [], {}
    for raw in cal["events"]:
        try:
            ev = _event(raw, cal.get("owner", ""), local, cal_zone)
        except (ValueError, OverflowError, TypeError, KeyError, IndexError):
            continue
        if ev is None:
            continue
        if ev["rid"] is not None:
            replaced.setdefault(ev["uid"], set()).add(ev["rid"])
            ev["rule"], ev["rdates"] = None, []  # an occurrence of a series, not a series itself
        if not ev["cancelled"]:
            out.append(ev)
    for ev in out:
        if ev["rid"] is None and (ev["rule"] or ev["rdates"]):
            ev["skip"] = ev["skip"] | replaced.get(ev["uid"], set())
    return out


def _starts_on(ev: dict, t0: float, t1: float) -> list:
    """The starts (epoch seconds) of a timed event's occurrences that overlap [t0, t1)."""
    length = ev["length"]

    def overlaps(a: float) -> bool:
        return a < t1 and (a + length > t0 or (length == 0 and a >= t0))

    if not (ev["rule"] or ev["rdates"]):
        a = ev["start"].timestamp()
        return [a] if overlaps(a) else []
    zone = ev["zone"]
    first = ev["start"].replace(tzinfo=None)
    margin = timedelta(hours=3)  # a clock change between the two zones; each start is checked exactly below
    lo = datetime.fromtimestamp(t0 - length, zone).replace(tzinfo=None) - margin
    hi = datetime.fromtimestamp(t1, zone).replace(tzinfo=None) + margin
    starts = set(expand(first, ev["rule"], lo, hi, ev["until"]) if ev["rule"] else [])
    starts.update(r for r in ev["rdates"] if lo <= r <= hi)
    if lo <= first <= hi:
        starts.add(first)  # DTSTART is always the first occurrence
    out = []
    for naive in sorted(starts):
        a = naive.replace(tzinfo=zone).timestamp()
        if {("t", int(a)), ("d", naive.date().isoformat())} & ev["skip"]:
            continue
        if overlaps(a):
            out.append(a)
    return out


def _covers(ev: dict, day: date) -> bool:
    """Whether a whole-day event (or one of its occurrences) covers `day`."""
    first, length = ev["start"], ev["length"]
    if not (ev["rule"] or ev["rdates"]):
        return first <= day < first + timedelta(days=length)
    lo, hi = datetime.combine(day - timedelta(days=length - 1), dtime()), datetime.combine(day, dtime())
    starts = set(expand(datetime.combine(first, dtime()), ev["rule"], lo, hi, ev["until"]) if ev["rule"] else [])
    starts.update(r for r in ev["rdates"] if lo <= r <= hi)
    if lo.date() <= first <= day:
        starts.add(datetime.combine(first, dtime()))
    return any(("d", s.date().isoformat()) not in ev["skip"] for s in starts)


def events_on(events: list, day: str, tz: tzinfo) -> list:
    """The day's events in the page's form, all-day ones first, then by start."""
    d = datetime.strptime(day, "%Y-%m-%d").date()
    midnight = datetime.combine(d, dtime())
    t0 = midnight.replace(tzinfo=tz).timestamp()
    t1 = (midnight + timedelta(days=1)).replace(tzinfo=tz).timestamp()

    def minutes(ts: float) -> float:  # on the wall clock, as the trackers' items are on the day the clocks change
        return round((datetime.fromtimestamp(ts, tz).replace(tzinfo=None) - midnight).total_seconds() / 60, 2)

    def item(ev: dict, s: float, e: float) -> dict:
        out = {"s": s, "e": e, "title": ev["title"], "all_day": ev["all_day"], "declined": ev["declined"],
               "recurring": bool(ev["rule"] or ev["rdates"] or ev["rid"]), "uid": ev["uid"]}
        out.update({k: ev[k] for k in ("location", "url") if ev[k]})
        return out

    out = []
    for ev in events:
        if ev["all_day"]:
            if _covers(ev, d):
                out.append(item(ev, 0, 1440))
            continue
        for a in _starts_on(ev, t0, t1):
            out.append(item(ev, minutes(max(a, t0)), minutes(min(a + ev["length"], t1))))
    out.sort(key=lambda x: (not x["all_day"], x["s"], x["e"], x["title"]))
    return out


# ---- the calendar, kept a few minutes ------------------------------------------------------------------

_lock = threading.Lock()
_cache: dict = {}   # sha256 of the address -> {"at", "cal", "built": {zone name: events}}
_failed: dict = {}  # sha256 of the address -> (when, why)


def forget() -> None:
    """Drop the kept calendar: the address changed or was removed."""
    with _lock:
        _cache.clear()
        _failed.clear()


def _calendar(url: str, get: Get) -> tuple:
    """(kept, problem): the calendar downloaded at most every FRESH seconds. When the address cannot be read, the last
    copy (up to a day old) stands in and the problem says so; with no copy, CalendarError."""
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    with _lock:  # one download at a time: a second request waits for the first and uses its answer
        kept, now = _cache.get(key), time.time()
        if kept and now - kept["at"] < FRESH:
            return kept, None
        failed = _failed.get(key)
        if failed and now - failed[0] < RETRY:
            problem = failed[1]
        else:
            try:
                cal = parse(get(url))
            except CalendarError as exc:
                problem = str(exc)
                _failed[key] = (now, problem)
            else:
                _failed.pop(key, None)
                _cache.clear()  # one calendar at a time
                _cache[key] = kept = {"at": now, "cal": cal, "built": {}}
                return kept, None
        if kept and now - kept["at"] < KEEP:
            return kept, problem
        raise CalendarError(problem)


def day(date_iso: str, settings: dict, get: Optional[Get] = None) -> dict:
    """{"events", "source", "problem", "needsSetup", "fetched"} for one day. Never raises for a calendar that cannot
    be read: the problem comes back as text for the page."""
    url = config.get_calendar_url()
    if not url:
        return {"events": [], "source": None, "needsSetup": True, "fetched": None,
                "problem": "No calendar yet: paste your calendar's secret iCal address in Settings."}
    tz = config.tz(settings)
    try:
        kept, problem = _calendar(url, get or download)
    except CalendarError as exc:
        return {"events": [], "source": "ics", "needsSetup": False, "fetched": None, "problem": str(exc)}
    zone_name = config.timezone_name(settings) or "offset"
    built = kept["built"].get(zone_name)
    if built is None:
        built = kept["built"][zone_name] = build(kept["cal"], tz)
    when = datetime.fromtimestamp(kept["at"], tz).strftime("%H:%M")
    if problem:
        problem = f"{problem} Showing the calendar as it was at {when}."
    return {"events": events_on(built, date_iso, tz), "source": "ics", "needsSetup": False, "fetched": when,
            "problem": problem}


def check(settings: dict, get: Optional[Get] = None) -> dict:
    """For Settings, right after an address is saved: whether it reads, and how many events today has."""
    today = datetime.now(config.tz(settings)).strftime("%Y-%m-%d")
    got = day(today, settings, get)
    if got["problem"]:
        return {"ok": False, "message": got["problem"]}
    n = len(got["events"])
    return {"ok": True, "message": f"Calendar connected: {n} event{'' if n == 1 else 's'} today."}
