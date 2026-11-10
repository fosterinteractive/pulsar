"""Pause: time the person says is not work while it happens ("I'm stepping away"), kept on this Mac.

While paused, the time from `since` to now is a "not work" stretch that is still open (daymarks.py holds the closed
ones): the Day view draws it, and Log the day, the Report's "not logged", the end-of-day reminder and the Claude Code
tools count none of it (not_work). Resume closes it, from `since` to now or to the time given (when the person came
back), each day's part written to that day's marks, where a click counts it as work again like any other. The
trackers go on recording; Pulsar only does not count it. One owner-only file, ~/.cache/pulsar/pause.json:
{"since": epoch, "asked": epoch}.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta

from . import config, daymarks

PATH = os.path.join(config.CACHE_DIR, "pause.json")
ASK_ACTIVE = 10  # minutes of work the tracker saw during a pause before Pulsar asks whether it is still on
_lock = threading.Lock()


def _read() -> dict:
    try:
        with open(PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or not isinstance(doc.get("since"), (int, float)) or isinstance(doc.get("since"), bool):
        return {}
    return {k: float(v) for k, v in doc.items() if k in ("since", "asked") and isinstance(v, (int, float))}


def _write(doc: dict) -> None:
    os.makedirs(os.path.dirname(PATH), mode=0o700, exist_ok=True)
    if doc:
        config.write_atomic(PATH, json.dumps(doc))
    else:
        try:
            os.remove(PATH)
        except FileNotFoundError:
            pass


def state() -> dict:
    """{"paused", "since", "asked"}: epochs, None when not paused or never asked."""
    doc = _read()
    return {"paused": "since" in doc, "since": doc.get("since"), "asked": doc.get("asked")}


def start(now: float | None = None) -> dict:
    """Pauses from now. Already paused: the pause goes on from when it began."""
    with _lock:
        doc = _read()
        if "since" not in doc:
            _write({"since": float(time.time() if now is None else now)})
    return state()


def stay(now: float | None = None) -> dict:
    """The person said the pause goes on: Pulsar asks again only after another stretch (server.api_pause)."""
    with _lock:
        doc = _read()
        if "since" in doc:
            _write({**doc, "asked": float(time.time() if now is None else now)})
    return state()


def clock_span(since: float, until: float, date: str, tz) -> list | None:
    """The part of [since, until) on `date`, in minutes of the day by the clock as the page draws them, or None."""
    day = datetime.strptime(date, "%Y-%m-%d").date()

    def minute(t: float) -> float:
        local = datetime.fromtimestamp(t, tz)
        if local.date() < day:
            return 0.0
        if local.date() > day:
            return 1440.0
        return local.hour * 60 + local.minute + local.second / 60

    a, b = minute(since), minute(until)
    return [int(a), min(1440, int(b + 0.999))] if b > a else None


def _days(since: float, until: float, tz) -> list:
    first = datetime.fromtimestamp(since, tz).date()
    last = datetime.fromtimestamp(until, tz).date()
    return [(first + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((last - first).days + 1)]


def stop(tz, until: float | None = None, now: float | None = None) -> dict:
    """Ends the pause at `until` (now when not given; never before it began or after now): from `since` to there is
    written to each day's "not work" marks. {"paused": False, "marked": {date: [a, b]}, "was": {date: the marks before},
    "since"}: what restore() needs to undo it."""
    now = time.time() if now is None else now
    with _lock:
        doc = _read()
        if "since" not in doc:
            return {**state(), "marked": {}}
        since = doc["since"]
        end = now if until is None else min(now, max(since, float(until)))
        marked, was = {}, {}
        if end - since >= 60:  # under a minute marks nothing
            for date in _days(since, end, tz):
                span = clock_span(since, end, date, tz)
                if span:
                    was[date] = daymarks.get(date).get("notWork") or []
                    daymarks.change(date, add=span)
                    marked[date] = span
        _write({})
    return {**state(), "marked": marked, "was": was, "resumedSince": since}


def restore(since, marked, was, now: float | None = None) -> dict:
    """Undo of a resume: the pause on again from `since`, and each day's "not work" marks as they were before it
    (`marked` taken off, `was` put back). Nothing when paused again already."""
    now = time.time() if now is None else now
    if isinstance(since, bool) or not isinstance(since, (int, float)) or not now - 8 * 86400 <= since <= now:
        raise ValueError("since: when the pause began")
    with _lock:
        if "since" in _read():
            return state()
        for date, span in (marked if isinstance(marked, dict) else {}).items():
            if daymarks.DATE.fullmatch(str(date)) and daymarks.merge([span]):
                daymarks.change(date, remove=span)
                for x in daymarks.merge((was or {}).get(date) if isinstance(was, dict) else []):
                    daymarks.change(date, add=x)
        _write({"since": float(since)})
    return state()


def live(date: str, tz, now: float | None = None) -> list:
    """The open pause's part of `date`, [[a, b]] in clock minutes; [] when not paused or not on that day."""
    since = state()["since"]
    if since is None:
        return []
    span = clock_span(since, time.time() if now is None else now, date, tz)
    return [span] if span else []


def not_work(date: str, tz, now: float | None = None) -> list:
    """Everything of `date` that counts as not work: the marks and the open pause, [[a, b]] in clock minutes."""
    return daymarks.merge((daymarks.get(date).get("notWork") or []) + live(date, tz, now))


def elapsed(date: str, spans: list, tz, t0: float) -> list:
    """Clock-minute spans of `date` as minutes from `t0` (a day's start): what the Report counts in. The same numbers on
    every day but the two the clocks change."""
    day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=tz)
    out = []
    for a, b in spans:
        s = (day + timedelta(minutes=a)).timestamp()
        e = (day + timedelta(minutes=b)).timestamp() if b < 1440 else (day + timedelta(days=1)).timestamp()
        out.append(((s - t0) / 60, (e - t0) / 60))
    return out
