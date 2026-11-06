"""Tasks for the Likely rows, learned from your own logging: the task you logged while working in the same Claude
Code session, else in the same repository, over the last LEARN_DAYS days. Older logging counts less: a day's votes
halve every HALF_LIFE days. Local and free; no one is asked anything.

On 30 days of one person's logging (337 rows), each day learned from the days before it only, the right task was
first for 43% of the minutes and among the first five for 57% (39% and 52% when every day counted the same).

A past day's Likely rows take a few seconds each to work out, so they are worked out once, in the background, and kept
in ~/.cache/pulsar/likely-days/<date>.json: the times, the repository and a hash of each session title (never the
titles or anything typed). Until they are all there, the suggestions use the days that are.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timedelta

from . import config, logbook

LEARN_DAYS = 60
HALF_LIFE = 7  # days: a day's votes count half as much this many days later (as in dayplan.py)
FORMAT = 1
DIR = os.path.join(config.CACHE_DIR, "likely-days")
_warming = threading.Lock()
_state = {"done": 0, "of": 0}


def key(title: str) -> str:
    from .sessions import norm_title
    t = norm_title(title)
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16] if len(t) >= 5 else ""


def _path(date: str) -> str:
    return os.path.join(DIR, f"{date}.json")


def _stored(date: str):
    try:
        with open(_path(date), encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc["rows"] if doc.get("format") == FORMAT and isinstance(doc.get("rows"), list) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def rows_of(proposal: dict) -> list:
    return [{"k": [k for k in (key(t) for t in e.get("titles") or []) if k], "p": e.get("project") or "",
             "s": e["s"], "e": e["e"]} for e in proposal.get("entries") or [] if e.get("e", 0) - e.get("s", 0) >= 5]


def _days_before(date: str) -> list:
    d = datetime.strptime(date, "%Y-%m-%d")
    return [(d - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, LEARN_DAYS + 1)]


def warm(date: str, settings: dict) -> None:
    """Work out, in the background, the past days not kept yet. One run at a time; a second call returns at once."""
    if not _warming.acquire(blocking=False):
        return

    def run():
        from . import likely
        try:
            todo = [d for d in _days_before(date) if _stored(d) is None]
            _state.update(done=LEARN_DAYS - len(todo), of=LEARN_DAYS)
            os.makedirs(DIR, mode=0o700, exist_ok=True)
            for d in todo:
                got = likely.proposal(d, config.timezone_name(settings), "9999-12-31", False, settings, keep=False)
                if not got.get("error"):
                    config.write_atomic(_path(d), json.dumps({"format": FORMAT, "rows": rows_of(got)}))
                _state["done"] += 1
        except Exception as exc:  # a hint: the suggestions just use fewer days
            logbook.record("learn", f"Learning tasks from past days: {type(exc).__name__}: {exc}", kind="warning")
        finally:
            _warming.release()

    threading.Thread(target=run, daemon=True, name="pulsar-learn").start()


def main_task(rows: list, a: float, b: float):
    """The task that took most of [a, b) (epoch seconds), when it took at least ten minutes or a third of it."""
    cover: dict = {}
    for r in rows:
        if r.get("start") is None or not r.get("taskId"):
            continue
        o = min(r["start"] + r["minutes"] * 60, b) - max(r["start"], a)
        if o > 0:
            cover[r["taskId"]] = cover.get(r["taskId"], 0) + o
    if not cover:
        return None
    best = max(cover, key=cover.get)
    return best if cover[best] >= min(600, (b - a) / 3) else None


def votes(days: dict, entries: list, t0_of, date: str) -> tuple:
    """({session key: {task: [weight, minutes]}}, {repository: {task: [weight, minutes]}}) from past days' rows and
    your entries, for the rows of `date`. The minutes are the real ones; the weight is the minutes halved every
    HALF_LIFE days before `date`, and sets the order. days: {date: rows}; t0_of(date) gives the day's midnight in
    epoch seconds."""
    by_day: dict = {}
    for r in entries:
        by_day.setdefault(r.get("date"), []).append(r)
    day0 = datetime.strptime(date, "%Y-%m-%d")
    sess: dict = {}
    repo: dict = {}

    def add(table: dict, at: str, task: str, w: float, m: float) -> None:
        got = table.setdefault(at, {}).setdefault(task, [0.0, 0])
        got[0] += w
        got[1] += m

    for d, rows in days.items():
        t0 = t0_of(d)
        fade = 0.5 ** (max((day0 - datetime.strptime(d, "%Y-%m-%d")).days, 0) / HALF_LIFE)
        for row in rows:
            task = main_task(by_day.get(d, []), t0 + row["s"] * 60, t0 + row["e"] * 60)
            if not task:
                continue
            m = row["e"] - row["s"]
            for k in row["k"]:
                add(sess, k, task, m * fade, m)
            add(repo, row["p"], task, m * fade, m)
    return sess, repo


def suggest(row: dict, sess: dict, repo: dict, names: dict, limit: int = 5) -> list:
    """The tasks for one row, the session's first, then the repository's, each in the order of its weight (recent
    logging first); each with why, in words, with the real minutes."""
    out: dict = {}
    s_votes: dict = {}
    for k in row["k"]:
        for t, (w, m) in sess.get(k, {}).items():
            got = s_votes.setdefault(t, [0.0, 0])
            got[0] += w
            got[1] += m
    for t, (w, m) in sorted(s_votes.items(), key=lambda kv: -kv[1][0]):
        out.setdefault(t, f"logged while in this Claude session: {_dur(m)}")
    for t, (w, m) in sorted(repo.get(row["p"], {}).items(), key=lambda kv: -kv[1][0]):
        out.setdefault(t, f"logged while in {row['p']}: {_dur(m)}")
    return [{"taskId": t, "title": names.get(t, {}).get("task") or "", "project": names.get(t, {}).get("project") or "",
             "why": why} for t, why in list(out.items())[:limit] if t in names]


def _dur(m: float) -> str:
    m = int(round(m))
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


def annotate(proposal: dict, date: str, settings: dict, entries_before: list, entries_today: list, tz) -> None:
    """Add "learned" (tasks, why) to each row of a day's proposal, and "learning" ({done, of}) while past days are
    still being worked out. Mutates the proposal; never raises."""
    try:
        warm(date, settings)
        days = {d: rows for d in _days_before(date) for rows in [_stored(d)] if rows}
        days[date] = rows_of(proposal)  # rows already logged today teach the ones after them
        t0_of = lambda d: datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=tz).timestamp()  # noqa: E731
        entries = [r for r in entries_before + entries_today if r.get("taskId")]
        sess, repo = votes(days, entries, t0_of, date)
        names = {r["taskId"]: r for r in sorted(entries, key=lambda r: r.get("date") or "")}
        for e, row in zip([e for e in proposal.get("entries") or [] if e.get("e", 0) - e.get("s", 0) >= 5],
                          days[date]):
            e["learned"] = suggest(row, sess, repo, names)
        proposal["learning"] = dict(_state) if _state["of"] and _state["done"] < _state["of"] else None
    except Exception as exc:
        logbook.record("learn", f"Suggesting tasks: {type(exc).__name__}: {exc}", kind="warning")
