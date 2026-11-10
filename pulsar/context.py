"""Tasks for work outside Claude Code, learned from the window in front: the Slack conversation, the web page (its
site and the first parts of its path), the first and last parts of a window's title, the app. Each minute you logged
to a task before votes, for that task, for every key of the window in front then; older days count less (as learn.py).
A window whose votes are sure (SHARE of LEAST faded minutes or more, its most precise key first) names the task of the
work there, when that task is internal (dayplan._context): a client's task is left for you to pick. Used only where no Claude Code session was in front: for someone without Claude Code
that is the whole day, for anyone else a sitting spent wholly in Slack, a browser or a document.

What is kept on this Mac (context-days/<date>.json, KEEP_DAYS days, never sent anywhere): per day, the minutes each
window was in front and a hash of each of its keys, never the titles, the pages or the conversations themselves.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timedelta

from . import config, logbook

# Replayed (dayplan._context): a share of 0.6 from 5 faded minutes named the right task on 28.3% of the clean minutes,
# 0.7 from 10 on 24.0%, with the same billing (only internal tasks are named this way)
SHARE = 0.6    # the share of a key's votes its task needs
LEAST = 5.0    # the faded minutes a key needs before it says anything
RUN = 5        # minutes at least, in a row, for a stretch to take a task this way
GAP = 2        # minutes between two of a stretch's minutes at most
HALF_LIFE = 7  # days: a day's votes count half as much this many days later (as learn.py)
LEARN_DAYS = 60
KEEP_DAYS = 62
FORMAT = 1
DIR = os.path.join(config.CACHE_DIR, "context-days")
SPLIT = re.compile(r" - | \| | · | – | — ")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", (text or "").lower())).strip()


def keys(it: dict) -> list:
    """The keys of a window, most precise first, hashed: a Slack conversation (from its window title); a page's site
    and the first parts of its path (numbers left out: one key for every ticket of a tracker, one per site); the first
    part of the title and, outside a browser, its last part (a document, an editor's folder); the app."""
    app, title = str(it.get("app") or ""), str(it.get("title") or "")
    url, host = str(it.get("url") or ""), str(it.get("host") or "").lower()
    out = []
    if app == "Slack":
        conv = re.sub(r"\s*-\s*\d+ new items?.*$", "", title.split(" - ")[0]).strip().lower()
        return [_hash("s|" + conv)] if conv else []
    if host:
        path = re.sub(r"^[a-z]+://[^/]+", "", url).split("?")[0].split("#")[0].strip("/").lower()
        segs = [re.sub(r"^\d+$", "#", s) for s in path.split("/") if s][:4]
        out += ["u|" + host + "/" + "/".join(segs[:k]) for k in range(len(segs), 0, -1)] + ["u|" + host]
    parts = [p for p in SPLIT.split(title) if p.strip()]
    if parts:
        out.append("t|" + app + "|" + _norm(parts[0]))
        if len(parts) > 1 and not host:
            out.append("l|" + app + "|" + _norm(parts[-1]))
    if not host and app:
        out.append("a|" + app)
    return [_hash(k) for k in out]


def front(items: list, n: int = 1440) -> list:
    """Per minute of the day, the index in items of the work window in front (the latest to start), or None."""
    fr: list = [None] * n
    for i, it in enumerate(items or []):
        if it.get("cat") == "work":
            for x in range(max(0, int(it["s"])), min(n, int(-(-float(it["e"]) // 1)))):
                fr[x] = i
    return fr


def day_rows(items: list) -> dict:
    """What a day keeps: {"keys": [[hashed keys]], "runs": [[s, e, index into keys]]}, minutes of the day, a run per
    stretch of one window in front."""
    fr = front(items)
    table: list = []
    at: dict = {}
    runs: list = []
    for x, i in enumerate(fr):
        if i is None:
            continue
        ks = tuple(keys(items[i]))
        if not ks:
            continue
        j = at.setdefault(ks, len(table))
        if j == len(table):
            table.append(list(ks))
        if runs and runs[-1][2] == j and runs[-1][1] == x:
            runs[-1][1] = x + 1
        else:
            runs.append([x, x + 1, j])
    return {"keys": table, "runs": runs}


def sound(doc) -> bool:
    """A day kept whole, in this format: keys a list of lists of hashes, runs [s, e, index into keys] of numbers."""
    if not isinstance(doc, dict) or doc.get("format", FORMAT) != FORMAT:
        return False
    table, runs = doc.get("keys"), doc.get("runs")
    return isinstance(table, list) and isinstance(runs, list) \
        and all(isinstance(k, list) and all(isinstance(h, str) for h in k) for k in table) \
        and all(isinstance(r, list) and len(r) == 3 and all(isinstance(v, (int, float)) for v in r)
                and isinstance(r[2], int) and 0 <= r[2] < len(table) for r in runs)


def votes(days: dict, entries: list, t0_of, date: str, half_life: float = HALF_LIFE,
          learn_days: int = LEARN_DAYS, tz=None) -> dict:
    """{key: {task: faded minutes}}: every minute of your entries (with a start) on a kept day votes for its task with
    every key of the window in front then. days: {date: day_rows(...)}. tz: the person's time zone; the activity is
    by the clock (server.clock_spans), so on a day the clocks change an entry's start is too."""
    day0 = datetime.strptime(date, "%Y-%m-%d")
    lo = (day0 - timedelta(days=learn_days)).strftime("%Y-%m-%d")
    by_day: dict = {}
    for r in entries:
        if r.get("start") is not None and r.get("taskId") and lo <= str(r.get("date") or "") < date:
            by_day.setdefault(r["date"], []).append(r)
    out: dict = {}
    for d, doc in days.items():
        if not doc or d not in by_day or not sound(doc):
            continue
        w = 0.5 ** ((day0 - datetime.strptime(d, "%Y-%m-%d")).days / half_life) if half_life else 1.0
        at: list = [None] * 1440
        for s, e, j in doc.get("runs") or []:
            for x in range(max(0, int(s)), min(1440, int(e))):
                at[x] = j
        table, t0 = doc.get("keys") or [], t0_of(d)
        for r in by_day[d]:
            start = float(r["start"])
            shift = 0.0 if tz is None else (datetime.fromtimestamp(start, tz).utcoffset()
                                            - datetime.fromtimestamp(t0, tz).utcoffset()).total_seconds()
            a = int(round((start - t0 + shift) / 60))
            task = str(r["taskId"])
            for x in range(max(0, a), min(1440, a + int(r.get("minutes") or 0))):
                if at[x] is None or at[x] >= len(table):
                    continue
                for k in table[at[x]]:
                    v = out.setdefault(k, {})
                    v[task] = v.get(task, 0.0) + w
    return out


def pick(ks: list, learned: dict):
    """(task, share, faded minutes) from the most precise of the keys whose votes are sure, else None."""
    for k in ks:
        v = learned.get(k)
        if not v:
            continue
        total = sum(v.values())
        if total < LEAST:
            continue
        task, n = max(v.items(), key=lambda kv: kv[1])
        if n / total >= SHARE:
            return task, n / total, total
    return None


# ---- kept on this Mac ----------------------------------------------------------------------------------------------

_warming = threading.Lock()


def _path(date: str) -> str:
    return os.path.join(DIR, f"{date}.json")


def stored(date: str):
    try:
        with open(_path(date), encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) and doc.get("format") == FORMAT and sound(doc) else None
    except (OSError, ValueError):
        return None


def store(date: str, items: list) -> None:
    """Keep a day that is over (its windows' hashed keys); an old day kept falls out. A folder that cannot be written
    keeps nothing: the tasks outside Claude come from fewer days, and Log the day goes on."""
    try:
        os.makedirs(DIR, mode=0o700, exist_ok=True)
        config.write_atomic(_path(date), json.dumps({"format": FORMAT, **day_rows(items)}, separators=(",", ":")))
    except OSError as exc:
        logbook.record("learn", f"Past windows could not be kept: {type(exc).__name__}: {exc}", kind="warning",
                       once_per=3600)
        return
    lo = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for f in os.listdir(DIR):
        if f.endswith(".json") and f[:10] < lo:
            try:
                os.remove(os.path.join(DIR, f))
            except OSError:
                pass


def days_before(date: str) -> dict:
    d0 = datetime.strptime(date, "%Y-%m-%d")
    out = {}
    for i in range(1, LEARN_DAYS + 1):
        d = (d0 - timedelta(days=i)).strftime("%Y-%m-%d")
        doc = stored(d)
        if doc:
            out[d] = doc
    return out


def warm(date: str, today: str, read_items) -> None:
    """Keep, in the background, the past days not kept yet: read_items(date) -> the day's activity items (the tracker,
    read only). One run at a time; a second call returns at once."""
    if not _warming.acquire(blocking=False):
        return

    def run():
        try:
            d0 = datetime.strptime(date, "%Y-%m-%d")
            for i in range(1, LEARN_DAYS + 1):
                d = (d0 - timedelta(days=i)).strftime("%Y-%m-%d")
                if d < today and stored(d) is None:
                    items = read_items(d)
                    if items is not None:
                        store(d, items)
        except Exception as exc:  # a hint: the tasks outside Claude just come from fewer days
            logbook.record("learn", f"Learning from past windows: {type(exc).__name__}: {exc}", kind="warning")
        finally:
            _warming.release()
    threading.Thread(target=run, daemon=True, name="pulsar-context").start()
