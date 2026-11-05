"""The "Likely" column: which Claude Code session, which project, minute by minute.

For anyone with Claude Code sessions on this computer (~/.claude/projects): sessions.py works it out from the sessions
and the activity tracker. Where the fi-time-from-activity skill is installed, its activity_blocks.py is used instead
(it carries its owner's own logging rules). Without Claude Code sessions the column never shows.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time

from . import config

SCRIPT = os.path.expanduser("~/.claude/skills/fi-time-from-activity/scripts/activity_blocks.py")
SESSIONS = os.path.expanduser("~/.claude/projects")
_cache: dict = {}


def available() -> bool:
    return os.path.isdir(SESSIONS)


def own_script() -> bool:
    """The fi-time-from-activity skill is installed: its script makes the column."""
    return os.path.exists(SCRIPT)


def enabled(settings: dict) -> bool:
    return settings.get("likely") != "off" and available()


def hm_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


_locks: dict = {}
TODAY_FOR = 180  # seconds a proposal for today is reused: the script reads every session of the day (~3 s)


def proposal(date: str, tz_name: str, today: str, fresh: bool, settings: dict | None = None) -> dict:
    """The proposal for a day: past days once, today again after three minutes (or on ↻). One run per day at a time;
    a request that arrives while it runs waits for it instead of starting another."""
    key = (date, tz_name)
    lock = _locks.setdefault(key, threading.Lock())
    with lock:
        cached = _cache.get(key)
        if cached and not fresh and (date < today or time.time() - cached[0] < TODAY_FOR):
            return cached[1]
        settings = settings or config.load()
        result = _run(date, tz_name) if own_script() else _built_in(date, settings)
        if not result.get("error"):
            _with_tasks(result, date, settings)
            _cache[key] = (time.time(), result)
        return result


def _with_tasks(result: dict, date: str, settings: dict) -> None:
    """The Productive tasks linked in each row's sessions, for the log dialog to offer first. A hint only: if the
    sessions cannot be read, the rows stay as they are."""
    from datetime import datetime
    from . import sessions
    try:
        t0 = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=config.tz(settings)).timestamp()
        sessions.attach_tasks(result.get("entries") or [], t0, sessions.task_links(t0))
    except Exception as exc:
        from . import logbook
        logbook.record("likely", f"Task links in Claude sessions: {type(exc).__name__}: {exc}", kind="warning")


def _built_in(date: str, settings: dict) -> dict:
    """sessions.py on the day's activity from the tracker that saw more of it."""
    from datetime import datetime, timedelta
    from . import sessions, trackers
    if not available():
        return {"error": "No Claude Code sessions on this computer.", "entries": []}
    tz = config.tz(settings)
    day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=tz)
    t0, t1 = day.timestamp(), (day + timedelta(days=1)).timestamp()
    found = trackers.both(t0, t1, settings.get("activitywatch_url") or config.DEFAULTS["activitywatch_url"],
                          trackers.classifier(settings.get("personal_patterns") or []))
    source = trackers.pick(found, "auto")
    names = {"aw": "ActivityWatch", "memtime": "MemTime"}
    return sessions.propose(t0, t1, found.get(source) or [], names.get(source, source) if found.get(source) else "no tracker")


def _run(date: str, tz_name: str) -> dict:
    if not own_script():
        return {"error": "The fi-time-from-activity skill is not installed.", "entries": []}
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    fd, out = tempfile.mkstemp(prefix=f"proposal-{date}-", suffix=".json", dir=config.CACHE_DIR)
    os.close(fd)
    try:
        run = subprocess.run([sys.executable, SCRIPT, date, "--tz", tz_name or "UTC", "--json", out],
                             capture_output=True, text=True, timeout=300)
        if run.returncode != 0:
            return {"error": (run.stderr or run.stdout).strip()[-400:] or "activity_blocks.py failed", "entries": []}
        try:
            with open(out, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {"error": "activity_blocks.py wrote no proposal", "entries": []}
    finally:
        try:
            os.remove(out)  # it holds session titles and first prompts: kept in memory, not on disk
        except OSError:
            pass
    rows = []
    for e in data.get("entries", []):
        if e.get("minutes", 0) < 1:
            continue
        s, en = hm_min(e["start"]), hm_min(e["end"])
        rows.append({"s": s, "e": en if en >= s else en + 1440, "minutes": e["minutes"], "project": e["project"],
                     "kind": e["kind"], "titles": e.get("titles", []), "messages": e.get("messages", 0),
                     "first_message": e.get("first_message", "")})
    return {"tracker": data.get("tracker"), "working": data.get("working"), "proposed": data.get("proposed"),
            "unplaced": data.get("unplaced", []), "logging": data.get("logging", 0), "entries": rows}
