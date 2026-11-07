"""What the person says about a day's own time, kept on this Mac and never sent to Productive: the day is done (or, on
a day past its target, not done after all), and stretches of active time that were not work. The Day view greys a
done day's yellow rails and counts no stretch marked "not work"; Log the day proposes nothing there; Earlier days
leaves done days out. One owner-only file, ~/.cache/pulsar/day-marks.json: {date: {"done": true|false, "notWork":
[[start, end], ...]}} in minutes of the day as the page draws them, KEEP_DAYS days.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta

from . import config

PATH = os.path.join(config.CACHE_DIR, "day-marks.json")
KEEP_DAYS = 120
_lock = threading.Lock()
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _read() -> dict:
    try:
        with open(PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {d: m for d, m in doc.items() if DATE.fullmatch(d) and isinstance(m, dict)} if isinstance(doc, dict) else {}


def clean(m: dict) -> dict:
    """A day's marks as kept: "done" only when said (True or False), "notWork" as sorted spans that do not touch."""
    out: dict = {}
    if isinstance(m.get("done"), bool):
        out["done"] = m["done"]
    spans = merge(m.get("notWork") or [])
    if spans:
        out["notWork"] = spans
    return out


def merge(spans) -> list:
    """Spans [[a, b], ...] in minutes 0 to 1440: whole minutes, sorted, the ones that overlap or touch joined."""
    good = []
    for x in spans if isinstance(spans, list) else []:
        if isinstance(x, (list, tuple)) and len(x) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in x):
            a, b = max(0, int(x[0])), min(1440, int(x[1]))
            if b > a:
                good.append([a, b])
    out: list = []
    for a, b in sorted(good):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def cut(spans: list, a: int, b: int) -> list:
    """`spans` less [a, b)."""
    out = []
    for x, y in merge(spans):
        if y <= a or x >= b:
            out.append([x, y])
            continue
        if x < a:
            out.append([x, a])
        if y > b:
            out.append([b, y])
    return out


def get(date: str) -> dict:
    return clean(_read().get(date) or {})


def between(first: str, last: str) -> dict:
    """{date: marks} for the days from `first` to `last` that have any."""
    return {d: clean(m) for d, m in _read().items() if first <= d <= last and clean(m)}


def change(date: str, done=None, add=None, remove=None, undone: bool = False) -> dict:
    """Changes one day's marks and returns them: done True or False (undone: forget what was said, so the target
    decides again), add or remove a "not work" span [a, b)."""
    if not DATE.fullmatch(date or ""):
        raise ValueError("date=YYYY-MM-DD")
    with _lock:
        doc = _read()
        m = clean(doc.get(date) or {})
        if undone:
            m.pop("done", None)
        elif done is not None:
            if not isinstance(done, bool):
                raise ValueError("done is true or false")
            m["done"] = done
        if add is not None:
            if not merge([add]):
                raise ValueError("a span [start, end) in minutes of the day")
            m["notWork"] = merge((m.get("notWork") or []) + merge([add]))
        if remove is not None:
            r = merge([remove])
            if not r:
                raise ValueError("a span [start, end) in minutes of the day")
            m["notWork"] = cut(m.get("notWork") or [], *r[0])
        m = clean(m)
        if m:
            doc[date] = m
        else:
            doc.pop(date, None)
        oldest = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
        doc = {d: v for d, v in doc.items() if d >= oldest}
        os.makedirs(os.path.dirname(PATH), mode=0o700, exist_ok=True)
        config.write_atomic(PATH, json.dumps(doc, sort_keys=True))
    return m
