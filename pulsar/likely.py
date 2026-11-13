"""The "Likely" column: which Claude Code session, which project, minute by minute, and what the agents did in the
background meanwhile.

For anyone with Claude Code sessions on this computer (~/.claude/projects): sessions.py works it out from the sessions
and the activity tracker, at the real time of the work. Where the fi-time-from-activity skill is installed, its
project names are used (its own rules for repositories, its name for time spent logging time, and its naming of each
turn from every string of its tool calls); its own proposal is not: it lays a sitting's minutes out one project after another and moves the agents' parallel time into free time,
so its rows are not where the work was. Without Claude Code sessions the column never shows.
"""

from __future__ import annotations

import importlib.util
import json
import os
import threading
import time
from datetime import datetime, timedelta

from . import config

SCRIPT = os.path.expanduser("~/.claude/skills/fi-time-from-activity/scripts/activity_blocks.py")
SESSIONS = os.path.expanduser("~/.claude/projects")
_cache = config.Newest(16)  # (date, time zone) -> (when, proposal): the days looked at lately


def available() -> bool:
    return os.path.isdir(SESSIONS)


def own_script() -> bool:
    """The fi-time-from-activity skill is installed: its project names and its naming of turns are used
    (skill_names, skill_turns)."""
    return os.path.exists(SCRIPT)


def enabled(settings: dict) -> bool:
    return settings.get("likely") != "off" and available()


_locks: dict = {}
TODAY_FOR = 180  # seconds a proposal for today is reused: working it out reads every session of the day (~3 s)


def proposal(date: str, tz_name: str, today: str, fresh: bool, settings: dict | None = None, keep: bool = True,
             quick: bool = False) -> dict:
    """The proposal for a day: a past day once, today again after three minutes (or on ↻). A day worked out while it
    was still today is worked out again once it is over, never kept as it was at that hour. So is a past day worked
    out while no tracker answered (ActivityWatch not up yet at login): it is reused for three minutes, as today is, and
    never kept on disk, so it is worked out again once a tracker answers (learn.warm does the same with its days). One
    run per day at a time; a request that arrives while it runs waits for it instead of starting another. keep=False
    (learning's 60 past days, which it stores on disk) leaves the days in memory, the ones the page shows, where they
    are, and never takes one of them that is not final.

    The rows' hints (the tasks linked in their sessions and the commits made during them: reading every session of
    the day and running git, a second or more) are added in the background. quick=True (the page's first ask) gets
    the rows without waiting for them, marked hintsPending; every other caller waits for them."""
    key = (date, tz_name)
    lock = _locks.setdefault(key, threading.Lock())
    with lock:
        # (when, proposal, OVER: worked out once the day was over, final / TODAY: before / BLIND: over, no tracker)
        cached = _cache.get(key)
        if not cached and not fresh and keep and date < today:  # a past day worked out before: from disk, at once
            stored = _load_day(date, tz_name)
            if stored is not None:
                _cache[key] = cached = (time.time(), stored, OVER)
        got = cached[1] if cached else None
        recent = cached and time.time() - cached[0] < TODAY_FOR
        if not (cached and not fresh and (cached[2] == OVER or (recent and (date >= today or (keep and cached[2] == BLIND))))):
            settings = settings or config.load()
            result = _run(date, tz_name, config.tz(settings), settings) if own_script() else _built_in(date, settings)
            if result.get("error"):
                return result
            if not keep:  # learning's past days: the task links only, nothing kept
                _with_tasks(result, date, settings, False)
                return result
            done, got = threading.Event(), result
            state = TODAY if date >= today else BLIND if _blind(result, settings) else OVER
            _cache[key], _hints[key] = (time.time(), result, state), done
            threading.Thread(target=_add_hints, args=(key, result, date, settings, done), daemon=True,
                             name="pulsar-hints").start()
    while True:  # the hints of the newest run (a ↻ meanwhile starts another)
        done = _hints.get(key)
        if not done or done.is_set():
            break
        if quick:
            return {**(_cache.get(key) or (0, got, False))[1], "hintsPending": True}
        if not done.wait(60):
            break
    return (_cache.get(key) or (0, got, False))[1]


_hints = config.Newest(16)  # (date, time zone) -> set once the cached proposal carries its hints
OVER, TODAY, BLIND = "over", "today", "blind"  # what a cached day is (proposal)


def _blind(result: dict, settings: dict) -> bool:
    """Worked out with no tracker's data while no tracker answers: the rows are the sessions alone, not the day."""
    if result.get("tracker") != "no tracker":
        return False
    from . import trackers
    return not trackers.answering(settings.get("activitywatch_url") or config.DEFAULTS["activitywatch_url"])


def _add_hints(key: tuple, result: dict, date: str, settings: dict, done: threading.Event) -> None:
    """The hints for a proposal just worked out, on a copy of its rows: the copy takes its place in the cache, so a
    proposal already handed out is never changed by them."""
    try:
        rows = [dict(e) for e in result.get("entries") or []]
        bg = [dict(e) for e in result.get("background") or []]
        _with_tasks({**result, "entries": rows, "background": bg}, date, settings, True)
        with _publish:
            now = _cache.get(key)
            if now and now[1] is result:  # not replaced by a newer run meanwhile
                hinted = {**result, "entries": rows, **({"background": bg} if "background" in result else {})}
                _cache[key] = (now[0], hinted, now[2])
                if now[2] == OVER:  # a day that was over, seen by a tracker: it will not change, so it is kept on disk
                    _save_day(date, key[1], hinted)
    finally:
        done.set()


_publish = threading.Lock()

# Past days' Likely columns, kept on this Mac so a day looked at again (or after a restart) shows at once instead of
# being worked out again (the script reads every session of the day: seconds). Rows with their session titles and
# first messages, so only keep_days() days are kept (31 unless Settings says more); ↻ works a day out again.
DAYS_DIR = os.path.join(config.CACHE_DIR, "likely-full")
DAYS_FORMAT = 2  # 2: rows at the real time of the work, and the background (1: the script's own layout)
KEEP_DAYS = 31  # the default of Settings > Claude Code > Keep past days (config.LIKELY_KEEP)


def keep_days(settings: dict | None = None) -> int:
    """How many past days' Likely columns are kept: the setting, else KEEP_DAYS. Learning keeps its own shorter
    record (learn.py, likely-days) and reads only its LEARN_DAYS, so this changes no prediction: 60 or 90 days of
    learning came out the same on 26 days (2026-11-13), and older days weigh little at a 7-day half-life."""
    v = (config.load() if settings is None else settings).get("likely_keep_days")
    return v if v in config.LIKELY_KEEP and not isinstance(v, bool) else KEEP_DAYS


def _day_path(date: str) -> str:
    return os.path.join(DAYS_DIR, f"{date}.json")


def _load_day(date: str, tz_name: str):
    try:
        with open(_day_path(date), encoding="utf-8") as fh:
            doc = json.load(fh)
        if doc.get("format") == DAYS_FORMAT and doc.get("timezone") == tz_name and isinstance(doc.get("proposal"), dict):
            return doc["proposal"]
    except (OSError, ValueError, AttributeError):
        pass
    return None


def keep_day(date: str, tz_name: str, result: dict, settings: dict) -> None:
    """A past day learning just worked out in the background (learn.warm), kept for the day view as _add_hints keeps
    one the page asked for, with its commits: opening it then reads it from disk instead of working it out again.
    Never a day with no tracker's data (it may only be ActivityWatch not up yet), and never over one kept already."""
    if result.get("error") or result.get("tracker") == "no tracker" or _load_day(date, tz_name) is not None:
        return
    kept = {**result, "entries": [dict(e) for e in result.get("entries") or []],
            **({"background": [dict(e) for e in result["background"]]} if "background" in result else {})}
    _with_tasks(kept, date, settings, True)
    _save_day(date, tz_name, kept)


def _save_day(date: str, tz_name: str, result: dict) -> None:
    try:
        os.makedirs(DAYS_DIR, mode=0o700, exist_ok=True)
        config.write_atomic(_day_path(date), json.dumps({"format": DAYS_FORMAT, "timezone": tz_name,
                                                         "proposal": result}, ensure_ascii=False))
        oldest = (datetime.now() - timedelta(days=keep_days())).strftime("%Y-%m-%d")
        for name in os.listdir(DAYS_DIR):
            if name.endswith(".json") and name[:10] < oldest:
                os.remove(os.path.join(DAYS_DIR, name))
    except OSError:
        pass  # a copy only: the day is worked out again next time


def _with_tasks(result: dict, date: str, settings: dict, commits: bool = True) -> None:
    """The Productive tasks linked in each row's sessions, for the log dialog to offer first, and your commits during
    each row, for the note (commits.py; not for the past days learning reads). Hints only: if the sessions or a
    repository cannot be read, the rows stay as they are."""
    from . import commits as git_commits, logbook, sessions
    t0 = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=config.tz(settings)).timestamp()
    try:
        rows = (result.get("entries") or []) + (result.get("background") or [])
        # rows from sessions.py carry their own links (repoLinks); only older ones need every session read again
        sessions.attach_tasks(rows, t0, sessions.task_links(t0) if any("repoLinks" not in r for r in rows) else [])
    except Exception as exc:
        logbook.record("likely", f"Pulsar could not read the task links in Claude sessions: {exc}", kind="warning",
                       detail=type(exc).__name__)
    if commits:
        try:
            git_commits.attach(result.get("entries") or [], t0)
        except Exception as exc:
            logbook.record("likely", f"Pulsar could not read your commits for the notes: {exc}", kind="warning",
                           detail=type(exc).__name__)


def _built_in(date: str, settings: dict, names: tuple = (None, None), turns: dict | None = None) -> dict:
    """sessions.py on the day's activity from the tracker that saw more of it. names: (namer, logging name), see
    skill_names; turns: see skill_turns."""
    from . import sessions, trackers
    if not available():
        return {"error": "No Claude Code sessions on this computer.", "entries": []}
    tz = config.tz(settings)
    day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=tz)
    t0, t1 = day.timestamp(), (day + timedelta(days=1)).timestamp()
    found = trackers.both(t0, t1, settings.get("activitywatch_url") or config.DEFAULTS["activitywatch_url"],
                          trackers.classifier(settings.get("personal_patterns") or []))
    source = trackers.pick(found, "auto")
    labels = {"aw": "ActivityWatch", "memtime": "MemTime"}
    namer, logging_name = names
    return sessions.propose(t0, t1, found.get(source) or [], labels.get(source, source) if found.get(source) else "no tracker",
                            namer=namer, logging_name=logging_name, turns=turns)


def _run(date: str, tz_name: str, tz=None, settings: dict | None = None) -> dict:
    """With the fi-time-from-activity skill installed: Pulsar's own reading of the day, with the skill's names, and each
    turn named as the skill names it (skill_turns)."""
    return _built_in(date, settings or config.load(), skill_names(), skill_turns())


_kept: dict = {}  # the script's modification time -> ((namer, logging name), turns)


def _skill() -> tuple:
    """((namer, logging name), turns) from the fi-time-from-activity script, loaded once per version of the file;
    ((None, None), None) when it is not installed or cannot be read: then Pulsar's own names and weights are used."""
    from . import logbook, sessions
    try:
        mtime = os.path.getmtime(SCRIPT)
    except OSError:
        return (None, None), None
    if mtime in _kept:
        return _kept[mtime]
    got: tuple = ((None, None), None)
    try:
        spec = importlib.util.spec_from_file_location("pulsar_skill_names", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        projects_in = mod.projects_in
        logging_name = getattr(mod, "LOGGING", None)
        cache = config.Newest(20000)

        def namer(path: str):
            if path not in cache:
                counts: dict = {}
                projects_in([path], counts)
                cache[path] = (max(counts, key=counts.get) if counts else None) or sessions.project_of_path(path)
            return cache[path]
        strings_of = getattr(mod, "tool_strings", None)
        turns = {"strings": strings_of, "count": projects_in} if callable(strings_of) else None
        got = ((namer, logging_name if isinstance(logging_name, str) else None), turns)
    except Exception as exc:  # the script changed shape: Pulsar's own names, and Logs says why
        logbook.record("likely", f"The fi-time-from-activity skill's project names could not be read, so Pulsar names "
                                 f"the projects itself: {exc}", kind="warning", detail=type(exc).__name__, once_per=3600)
    _kept.clear()
    _kept[mtime] = got
    return got


def skill_names() -> tuple:
    """(namer, logging name) from the fi-time-from-activity script's own rules (its projects_in and LOGGING); (None,
    None) when it is not installed or cannot be read, and then Pulsar's own names (the git repository) are used. A path
    the script names nothing keeps Pulsar's own name."""
    return _skill()[0]


def skill_turns() -> dict | None:
    """The script's own naming of a turn, {"strings": its tool_strings, "count": its projects_in} (sessions.analyse):
    every string of the turn's tool calls and what was typed, counted, as the script names it; on one person's 52 days
    it named the client logged more often than Pulsar's weights (33.9% against 30.4% of minutes with the right task
    at the right time, after Log the day). None when the script is not installed or cannot be read: then the weights
    name the turns."""
    return _skill()[1]
