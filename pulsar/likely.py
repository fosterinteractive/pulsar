"""The "Likely" column: which Claude Code session, which project, minute by minute.

Only for people who work in Claude Code and have the fi-time-from-activity skill, whose activity_blocks.py
does the work. Everyone else never sees the column.
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
    return os.path.exists(SCRIPT) and os.path.isdir(SESSIONS)


def enabled(settings: dict) -> bool:
    return settings.get("likely") != "off" and available()


def hm_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


_locks: dict = {}
TODAY_FOR = 180  # seconds a proposal for today is reused: the script reads every session of the day (~3 s)


def proposal(date: str, tz_name: str, today: str, fresh: bool) -> dict:
    """The proposal for a day: past days once, today again after three minutes (or on ↻). One run per day at a time;
    a request that arrives while it runs waits for it instead of starting another."""
    key = (date, tz_name)
    lock = _locks.setdefault(key, threading.Lock())
    with lock:
        cached = _cache.get(key)
        if cached and not fresh and (date < today or time.time() - cached[0] < TODAY_FOR):
            return cached[1]
        result = _run(date, tz_name)
        if not result.get("error"):
            _cache[key] = (time.time(), result)
        return result


def _run(date: str, tz_name: str) -> dict:
    if not available():
        return {"error": "Needs Claude Code sessions and the fi-time-from-activity skill.", "entries": []}
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
