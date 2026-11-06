"""The local web server: the page, its files, and a small JSON API. Listens on 127.0.0.1 only.

Reads go to the trackers, Productive and Google Calendar. It writes your settings and caches (owner-only files), the
tokens (keychain or a private file), and a time entry in Productive only when you log one in the page. A POST must carry the X-Pulsar header, which a page on another site cannot
send without the browser asking this server first, and this server never says yes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import traceback

from . import __version__, askclaude, calendar, claudelink, config, dayplan, learn, likely, logbook, productive, report, sessions, trackers, updater, names

UPDATER = updater.Updater()


def _preview_info() -> dict:
    """Pulsar Preview: which branch and commit this copy runs, read once at start, for the chip at the top."""
    import subprocess
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    def git(*args):
        try:
            return subprocess.run(["git", "-C", here, *args], capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return {"branch": git("rev-parse", "--abbrev-ref", "HEAD"), "commit": git("rev-parse", "--short", "HEAD"),
            "subject": git("log", "-1", "--format=%s")[:120], "changed": bool(git("status", "--porcelain"))}


PREVIEW = _preview_info() if os.environ.get("PULSAR_PREVIEW") else None

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml"}
DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
PUBLIC = ("/", "/index.html", "/api/version", "/api/whoami")  # plus /static/: nothing with data in it
SIGN_IN = ("This browser is not signed in to Pulsar. Open Pulsar from its app icon (it signs the browser in). For "
           "another browser, run /usr/bin/python3 ~/Library/Application\\ Support/Pulsar/current/pulsar.py --signin-url "
           "(from a git checkout: python3 pulsar.py --signin-url) and open the address it prints.")


def is_id(v) -> bool:
    """A Productive id: ASCII digits only ("²" and "١٢٣" pass str.isdigit and must not)."""
    return isinstance(v, str) and v.isascii() and v.isdigit() and len(v) <= 15


def clock(epoch: float, t0: float, tz) -> float:
    """Minutes on the wall clock since the day's midnight. Equal to elapsed minutes except on the two days a year
    the clocks change, when the page would otherwise show everything after the change an hour off."""
    shift = (datetime.fromtimestamp(epoch, tz).utcoffset() - datetime.fromtimestamp(t0, tz).utcoffset()).total_seconds()
    return (epoch - t0 + shift) / 60


def clock_change(t0: float, t1: float, tz):
    """The minute of the day [t0, t1) (elapsed since midnight) at which the clocks change, or None on any other day."""
    first = datetime.fromtimestamp(t0, tz).utcoffset()
    lo, hi = 0, int((t1 - 1 - t0) // 60)
    if datetime.fromtimestamp(t0 + hi * 60, tz).utcoffset() == first:
        return None
    while hi - lo > 1:  # the first minute with the new offset
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if datetime.fromtimestamp(t0 + mid * 60, tz).utcoffset() == first else (lo, mid)
    return hi


def clock_spans(spans: list, t0: float, t1: float, tz, places: int = 3) -> list:
    """Spans in elapsed minutes ({s, e, ...}) placed by the wall clock, on a day the clocks change. A span across the
    change is cut there and each piece keeps its real length: a stretch over the repeated autumn hour no longer reads
    as negative (the old -40 min), one over the lost spring hour no longer an hour too long. Other days: unchanged."""
    change = clock_change(t0, t1, tz)
    if change is None:
        return spans
    out = []
    for it in spans:
        cuts = [(it["s"], change), (change, it["e"])] if it["s"] < change < it["e"] else [(it["s"], it["e"])]
        for a, b in cuts:
            start = clock(t0 + a * 60, t0, tz)
            out.append({**it, "s": round(start, places), "e": round(start + (b - a), places)})
    return out


def page_span(row: dict, t0: float, tz) -> tuple:
    """A Likely row's (s, e) as the page draws it: its start by the clock, its real length. The page names a row by
    this start (Ask Claude's picks, the session note), so the server turns it back the same way."""
    start = round(clock(t0 + row["s"] * 60, t0, tz), 2)
    return start, round(start + row["e"] - row["s"], 2)


def pick_key(start: float, project: str) -> str:
    """The key the page looks an Ask Claude pick up by, `${s}|${project}`, written the way JavaScript writes the
    number (600, not 600.0)."""
    start = float(start)
    return f"{int(start) if start.is_integer() else start}|{project or ''}"


# ---- helpers -----------------------------------------------------------------------------------

def day_bounds(date: str, tz) -> tuple:
    d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=tz)
    return d.timestamp(), (d + timedelta(days=1)).timestamp()


def today(tz) -> str:
    return datetime.now(tz).strftime("%Y-%m-%d")


def read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path: str, data) -> None:
    config.write_atomic(path, json.dumps(data, ensure_ascii=False))


_identify_failed = [0.0]


def current_settings() -> dict:
    """Settings, with who you are filled in the first time Productive can tell."""
    s = config.load()
    if s.get("person_id") and s.get("org_id"):
        return s
    if time.time() - _identify_failed[0] < 300:
        return s  # it failed a moment ago: do not ask Productive again on every request
    conn = productive.connection(s)
    if not conn["kind"]:
        return s
    try:
        found = productive.identify(s, conn)
    except Exception as exc:  # the page still asks for the IDs; the reason goes to Logs (a refused token, say)
        _identify_failed[0] = time.time()
        logbook.record("productive", f"Pulsar could not tell who you are in Productive: {exc}", kind="warning",
                       once_per=600)
        return s
    found = {k: v for k, v in found.items() if v not in (None, "") and k in config.DEFAULTS}
    return config.update(found) if found else s


_address: dict = {}  # org id -> (when, Productive's web address for it, or '')


def productive_address(s: dict) -> str:
    """Where a task opens in Productive's web app, for "Open in Productive": from links in the person's Claude sessions
    (sessions.productive_address), so only while Pulsar may read them. Kept six hours, or ten minutes while unknown."""
    org = str(s.get("org_id") or "")
    if not org or not likely.enabled(s):
        return ""
    when, found = _address.get(org, (0.0, ""))
    if time.time() - when > (6 * 3600 if found else 600):
        found = sessions.productive_address(org)
        _address[org] = (time.time(), found)
    return found


def public_config(s: dict) -> dict:
    conn = productive.connection(s)
    return {
        "version": __version__, "codename": names.codename(__version__)[0],
        "timezone": config.timezone_name(s), "timezoneSystem": config.system_timezone(), "timezoneSetting": s["timezone"],
        "targetMinutes": config.target_minutes(s), "targetHours": s["target_hours"], "personDayHours": s["person_day_hours"],
        "personalPatterns": s["personal_patterns"], "activitywatchUrl": s["activitywatch_url"], "rules": s["rules"],
        "update": {**UPDATER.public(), "skip": s["update_skip"], "asked": s["update_asked"]},
        "errors24h": sum(1 for e in logbook.read(1) if e["kind"] == "error"),
        "likely": {"setting": s["likely"], "available": likely.available(), "enabled": likely.enabled(s),
                   "askClaude": s["ask_claude"], "askClaudeModel": s["ask_claude_model"], "claudeFound": askclaude.available(),
                   "link": claudelink.status(PORT[0])},
        "productive": {"source": s["productive_source"], "active": conn["kind"], "problem": conn.get("problem"),
                       "keychainStuck": config.keychain_stuck(),
                       "tokenSaved": bool(config.get_token()), "orgId": s["org_id"], "orgName": s["org_name"],
                       "personId": s["person_id"], "personName": s["person_name"], "web": productive_address(s)},
        "calendar": calendar.status(s),  # configured, connected, account: never a token
        "preview": ({**PREVIEW, "writes": bool(s["preview_writes"])} if config.is_preview() else None),
        "writesBlocked": config.writes_blocked(),  # why nothing may be written now (read-only, Preview), or None
    }


def validate(body: dict) -> dict:
    """Settings from the page, checked field by field; anything wrong is refused with the reason."""
    out: dict = {}
    if "timezone" in body:
        tz = str(body["timezone"] or "").strip()
        if tz and not config._valid_zone(tz):
            raise ValueError(f"Unknown time zone: {tz}")
        out["timezone"] = tz
    if "target_hours" in body:
        v = body["target_hours"]
        if v in (None, ""):
            out["target_hours"] = None
        else:
            if isinstance(v, bool) or not isinstance(v, (int, float, str)):
                raise ValueError("The daily target is a number of hours.")
            try:
                v = float(v)
            except ValueError:
                raise ValueError("The daily target is a number of hours.")
            if not 0.5 <= v <= 24:
                raise ValueError("The daily target is between 0.5 and 24 hours.")
            out["target_hours"] = v
    if "personal_patterns" in body:
        pats = body["personal_patterns"]
        if isinstance(pats, str):
            pats = pats.splitlines()
        if not isinstance(pats, list) or not all(isinstance(p, str) for p in pats):
            raise ValueError("Personal windows are a list of words.")
        out["personal_patterns"] = [p.strip()[:80] for p in pats if p.strip()][:100]
    if "activitywatch_url" in body:
        url = str(body["activitywatch_url"] or "").strip() or config.DEFAULTS["activitywatch_url"]
        if not re.match(r"^https?://[^\s/]+(:\d+)?/?$", url):
            raise ValueError("The ActivityWatch address looks like http://localhost:5600")
        out["activitywatch_url"] = url.rstrip("/")
    if "likely" in body:
        if body["likely"] not in ("auto", "on", "off"):
            raise ValueError("likely is auto, on or off")
        out["likely"] = body["likely"]
    if "preview_writes" in body:
        if not isinstance(body["preview_writes"], bool):
            raise ValueError("preview_writes is true or false")
        out["preview_writes"] = body["preview_writes"]
    if "ask_claude" in body:
        if not isinstance(body["ask_claude"], bool):
            raise ValueError("ask_claude is true or false")
        out["ask_claude"] = body["ask_claude"]
    if "ask_claude_model" in body:
        if body["ask_claude_model"] not in askclaude.MODELS:
            raise ValueError("The model is sonnet, haiku or opus.")
        out["ask_claude_model"] = body["ask_claude_model"]
    if "productive_source" in body:
        if body["productive_source"] not in ("auto", "token", "claude-code"):
            raise ValueError("The Productive source is auto, token or claude-code")
        out["productive_source"] = body["productive_source"]
    if "update_skip" in body:  # "Skip this release" in the reminder
        v = str(body["update_skip"] or "")
        if v and not updater.VERSION_RE.fullmatch(v):
            raise ValueError("update_skip is a version such as 1.2.0.")
        out["update_skip"] = v
    if "update_asked" in body:  # when the reminder last showed (seconds since 1970)
        v = body["update_asked"]
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < 2 ** 40:
            raise ValueError("update_asked is a time in seconds.")
        out["update_asked"] = v
    if "rules" in body:
        rules = []
        if not isinstance(body["rules"], list) or not all(isinstance(r, dict) for r in body["rules"]):
            raise ValueError("Rules are a list.")
        for r in body["rules"]:
            match, tid = str(r.get("match") or "").strip()[:120], str(r.get("taskId") or "")
            if not match or not is_id(tid):
                raise ValueError("A rule needs words to match and a task.")
            rules.append({"match": match, "taskId": tid, "title": str(r.get("title") or "")[:200],
                          "project": str(r.get("project") or "")[:200]})
        out["rules"] = rules[:300]
    for key in ("org_id", "person_id"):
        if key in body:
            v = str(body[key] or "").strip()
            if v and not is_id(v):
                raise ValueError(f"{key.replace('_', ' ')} is a number")
            out[key] = v
    return out


def productive_rows(d0: str, d1: str, s: dict, bulk: bool = False) -> tuple:
    """(rows, problem, source, from_copy). Every answer is kept per day, so when Productive cannot be reached
    the day view and the report show the last copy of each day, and say so, instead of nothing. bulk: a long read
    in the background, which leaves Productive room for the day's own reads."""
    first, last = datetime.strptime(d0, "%Y-%m-%d"), datetime.strptime(d1, "%Y-%m-%d")
    dates = [(first + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((last - first).days + 1)]
    rows, problem, source = productive.entries(d0, d1, s, bulk=bulk)
    if problem and source:  # a connection that failed (no connection at all is a setup step, not an error)
        logbook.record("productive", problem)
    who = re.sub(r"\D", "", str(s.get("person_id") or "")) or "unknown"
    path = lambda d: os.path.join(config.CACHE_DIR, "days", who, f"{d}.json")  # noqa: E731
    if not problem:
        stamp = datetime.now(config.tz(s)).isoformat(timespec="minutes")
        by_date: dict = {d: [] for d in dates}
        for r in rows:
            by_date.setdefault(r["date"], []).append(r)
        for d in dates:
            if d <= today(config.tz(s)):  # a day still to come has nothing worth keeping
                write_json(path(d), {"fetched": stamp, "rows": by_date.get(d, [])})
        return rows, None, source, False
    copies = {d: read_json(path(d), None) for d in dates}
    kept = {d: c for d, c in copies.items() if isinstance(c, dict) and isinstance(c.get("rows"), list)}
    if not kept:
        return [], problem, source, False
    stamps = sorted(str(c.get("fetched") or "") for c in kept.values())
    try:
        oldest = datetime.fromisoformat(stamps[0]).strftime("%d.%m %H:%M")
    except ValueError:
        oldest = stamps[0] or "earlier"
    missing = len(dates) - len(kept)
    note = f"{problem} Showing the copy saved {oldest}" + (f"; {missing} of {len(dates)} days have none." if missing else ".")
    rows = [r for c in kept.values() for r in c["rows"] if isinstance(r, dict) and r.get("date") and "minutes" in r]
    return [{**r, "start": r.get("start")} for r in rows], note, source, True


def test_productive(s: dict) -> dict:
    day = today(config.tz(s))
    rows, problem, source = productive.entries(day, day, s)
    if problem:
        return {"ok": False, "message": problem}
    who = s.get("person_name") or f"person {s.get('person_id')}"
    via = "your API token" if source == "token" else "Claude Code's connection"
    return {"ok": True, "message": f"Connected as {who} through {via}."}


# ---- API -----------------------------------------------------------------------------------------

def api_activity(q: dict) -> dict:
    s = config.load()
    t0, t1 = day_bounds(q["date"], config.tz(s))
    found = trackers.both(t0, t1, s["activitywatch_url"], trackers.classifier(s["personal_patterns"]))
    source = trackers.pick(found, q.get("source", "auto"))
    minutes = {k: round(sum(x["e"] - x["s"] for x in v)) for k, v in found.items()}
    tz = config.tz(s)
    # away time from elapsed minutes; then, on a day the clocks change, everything by the clock (each piece its real length)
    items, gaps = clock_spans(found[source], t0, t1, tz), clock_spans(away(found[source]), t0, t1, tz, 2)
    for name, problem in list(trackers.PROBLEMS.items()):
        logbook.record(f"tracker {name}", problem, kind="warning")
    return {"source": source, "minutes": minutes, "items": items, "problems": dict(trackers.PROBLEMS), "away": gaps}


AWAY_MIN = 5    # minutes without anything on screen that count as being away from the computer
OFF_MIN = 180   # from this long it is time off (a night, an afternoon off), not a break: shown, never added up


def away(items: list) -> list:
    """The stretches between the day's first and last activity when nothing was on screen for at least AWAY_MIN
    minutes: ActivityWatch's AFK time, MemTime's offline time, a computer asleep. [{s, e, off}] in minutes; off is a
    stretch of OFF_MIN or more."""
    out, end = [], None
    for it in sorted(items, key=lambda x: x["s"]):
        if end is not None and it["s"] - end >= AWAY_MIN:
            out.append({"s": round(end, 2), "e": round(it["s"], 2), "off": it["s"] - end >= OFF_MIN})
        end = it["e"] if end is None else max(end, it["e"])
    return out


def api_entries(q: dict) -> dict:
    s = current_settings()
    date, tz = q["date"], config.tz(s)
    t0, _ = day_bounds(date, tz)
    rows, problem, source, from_copy = productive_rows(date, date, s)
    if problem and not from_copy:
        return {"error": problem, "entries": [], "needsSetup": source is None}
    out = []
    for x in rows:
        start = None if x.get("start") is None else round(clock(x["start"], t0, tz), 2)
        # its end on the clock: over the hour the clocks skip in spring it ends an hour later than start + minutes
        end = None if start is None else max(round(start + x["minutes"], 2),
                                             round(clock(x["start"] + x["minutes"] * 60, t0, tz), 2))
        out.append({**{k: v for k, v in x.items() if k not in ("start", "date")}, "s": start, "e": end})
    out.sort(key=lambda x: (x["s"] is None, x["s"] or 0))
    return {"entries": out, "source": source, "fetched": None if from_copy else datetime.now(tz).strftime("%d.%m %H:%M"),
            "warning": problem, "needsSetup": bool(problem) and source is None}


def api_calendar(q: dict) -> dict:
    """The day's meetings from Google Calendar (pulsar/calendar.py), in minutes from midnight. fresh=1 (the ↻ button)
    asks Google at once instead of using the copy kept for five minutes."""
    got = calendar.day(q["date"], config.load(), fresh=q.get("fresh") == "1")
    if got["problem"]:
        logbook.record("calendar", got["problem"], kind="warning", once_per=600)
    return got


def api_proposal(q: dict) -> dict:
    """The Likely column. ignore_logged=1 (read-only test servers only, as for Log the day) learns the rows' tasks from
    the days before only, never from the day's own entries, so a day already logged can be planned blind."""
    ignore = q.get("ignore_logged") == "1"
    if ignore and not config.read_only():
        raise ValueError("ignore_logged is for read-only test servers only.")
    s = config.load()
    if not likely.enabled(s):
        return {"disabled": True, "entries": []}
    date, tz = q["date"], config.tz(s)
    # quick=1, the page's first ask: the rows as soon as there are any, their hints (hintsPending) on the second ask
    result = likely.proposal(date, config.timezone_name(s), today(tz), q.get("fresh") == "1", s,
                             quick=q.get("quick") == "1" and q.get("wait") != "1")
    t0, t1 = day_bounds(date, tz)
    if not result.get("error"):  # the tasks you logged in the same session or repository: a hint, never an error
        ps = current_settings()
        if ps.get("person_id"):
            if not history_ready(date, ps) and q.get("wait") != "1":
                # 60 days of history is a slow read: the rows go out now, without their tasks, while it is read in
                # the background; the page asks once more (wait=1) and gets them
                history_in_background(date, ps)
                result = {**result, "learnPending": True}
            else:
                before, _ = history_rows(date, ps, days=learn.LEARN_DAYS)
                same_day, problem, _, _ = productive_rows(date, date, ps) if not ignore else ([], "", None, None)
                if ignore:  # a copy: the cached rows, which the page reads too, keep their own tasks
                    result = {**result, "entries": [dict(e) for e in result.get("entries") or []]}
                learn.annotate(result, date, s, before, [] if problem else same_day, tz)  # elapsed minutes, as entries
    if not result.get("error") and clock_change(t0, t1, tz) is not None:
        # the clocks change today: rows by the clock, like the activity, each its real length (a copy: the cached
        # proposal stays in elapsed minutes, which learning and Ask Claude use)
        result = {**result, "entries": [{**e, **dict(zip(("s", "e"), page_span(e, t0, tz)))} for e in result["entries"]]}
    return result


def api_ask_claude(body: dict) -> dict:
    """The Ask Claude button: a task for each Likely row of the day (askclaude.py). Only when switched on in Settings;
    a POST, because it runs Claude and costs the person's usage."""
    s = current_settings()
    date = str(body.get("date") or "")
    if not DATE.match(date):
        raise ValueError("date=YYYY-MM-DD")
    if not s["ask_claude"]:
        raise ValueError("Ask Claude is off. Switch it on in Settings first.")
    tz = config.tz(s)
    proposal = likely.proposal(date, config.timezone_name(s), today(tz), False, s)
    if proposal.get("error"):
        raise ValueError(proposal["error"])
    d0, _ = askclaude.history_span(date)
    before, problem = history_rows(date, s, days=learn.LEARN_DAYS)  # one read, kept an hour, shared with Likely
    # each row's likely tasks (learned from the days before, never from this day's own entries): whose notes Claude
    # sees as examples of how this person writes them. A copy, so the cached proposal stays as it is.
    rows = [dict(e) for e in proposal.get("entries") or []]
    if not problem:
        learn.annotate({**proposal, "entries": rows}, date, s, before, [], tz)
    t0, t1 = day_bounds(date, tz)
    try:
        answer = askclaude.ask(date, rows, t0, [r for r in before if r.get("date", "") >= d0],
                               model=s["ask_claude_model"], history=before, tz=tz)
        if clock_change(t0, t1, tz) is not None:  # the page names its rows by their start on the clock
            by_start = {pick_key(r["s"], r.get("project")): pick_key(page_span(r, t0, tz)[0], r.get("project")) for r in rows}
            answer = {**answer, "picks": {by_start.get(k, k): v for k, v in (answer.get("picks") or {}).items()}}
        return answer
    except askclaude.AskError as exc:
        logbook.record("ask claude", str(exc), kind="warning")
        return {"error": str(exc), "picks": {}, "cost": exc.cost}


def api_likely_note(body: dict) -> dict:
    """The log dialog's note for a Likely block, from its session and commits (askclaude.note, Haiku). Only when the
    Claude Code switch is on; a POST, because it runs Claude."""
    s = current_settings()
    date, project = str(body.get("date") or ""), str(body.get("project") or "")
    if not DATE.match(date):
        raise ValueError("date=YYYY-MM-DD")
    try:
        a, b = float(body.get("s")), float(body.get("e"))
    except (TypeError, ValueError):
        raise ValueError("s and e are minutes of the day") from None
    if not s["ask_claude"]:
        raise ValueError("Claude Code is off. Switch it on in Settings first.")
    tz = config.tz(s)
    t0 = day_bounds(date, tz)[0]
    proposal = likely.proposal(date, config.timezone_name(s), today(tz), False, s)
    # s and e are the page's: by the clock on a day the clocks change, so the rows are compared as the page drew them
    rows = [dict(r) for r in proposal.get("entries") or [] if r.get("project") == project
            and min(page_span(r, t0, tz)[1], b) - max(page_span(r, t0, tz)[0], a) > 0]
    if not rows:
        return {"note": "", "why": "no Likely work at that time"}
    before, problem = history_rows(date, s, days=learn.LEARN_DAYS)
    if not problem:
        learn.annotate({**proposal, "entries": rows}, date, s, before, [], tz)
    lead = max(rows, key=lambda r: r["e"] - r["s"])
    row = {"s": min(r["s"] for r in rows), "e": max(r["e"] for r in rows), "project": project,
           "titles": sorted({t for r in rows for t in r.get("titles") or []}),
           "commits": [c for r in sorted(rows, key=lambda r: r["s"]) for c in r.get("commits") or []],
           "learned": lead.get("learned") or [], "tasks": lead.get("tasks") or []}
    try:
        return askclaude.note(date, row, t0, [] if problem else before, tz=tz)
    except askclaude.AskError as exc:
        logbook.record("ask claude", str(exc), kind="warning")
        return {"note": "", "error": str(exc)}


def api_report(q: dict) -> dict:
    s = current_settings()
    d0, d1 = q.get("from", ""), q.get("to", "")
    if not (DATE.match(d0) and DATE.match(d1)):
        raise ValueError("from=YYYY-MM-DD&to=YYYY-MM-DD")
    first, last = datetime.strptime(d0, "%Y-%m-%d"), datetime.strptime(d1, "%Y-%m-%d")
    if last < first or (last - first).days > 92:
        raise ValueError("Pick a range of 1 to 93 days.")
    tz = config.tz(s)
    dates = [(first + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((last - first).days + 1)]
    bounds = [day_bounds(d, tz) for d in dates]
    # Productive (network) and the trackers (local) at the same time: a 30-day report is about a second faster
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(2) as pool:
        rows_job = pool.submit(productive_rows, d0, d1, s)
        sources = trackers.both(bounds[0][0], bounds[-1][1], s["activitywatch_url"], trackers.classifier(s["personal_patterns"]))
        rows, problem, source, from_copy = rows_job.result()
    data = report.build(dates, bounds, sources, rows, today(tz), tz=tz)
    data["warning"], data["fromCopy"], data["needsSetup"] = problem, from_copy, bool(problem) and source is None
    # no activity at all in the range: say why, if a tracker said (otherwise every day just reads "Active 0")
    if not any(sources.values()):
        data["trackerWarning"] = " ".join(trackers.PROBLEMS.values()) or (
            "No activity was recorded in this range. Pulsar reads ActivityWatch or MemTime: is one of them installed "
            "and running?")
    data["targetMinutes"] = config.target_minutes(s)
    return data


def api_settings(body: dict) -> dict:
    if not isinstance(body, dict):
        raise ValueError("Settings are a JSON object.")
    changes = validate(body)
    token = str(body.get("token") or "").strip()
    if token and not config.TOKEN_RE.match(token):  # check before anything is removed or replaced
        raise ValueError("That does not look like a Productive token (letters, digits, - and _ only).")
    before = config.load()
    connection = bool(token or body.get("remove_token")) or any(
        k in changes and changes[k] != before.get(k) for k in ("productive_source", "org_id", "person_id"))
    if body.get("remove_token") and not token:
        config.delete_token()
    if token or body.get("remove_token") or ("productive_source" in changes and changes["productive_source"] != before["productive_source"]):
        if token:
            config.set_token(token)
        # The connection changed, so it may be someone else's: find the person again. An id the page merely sent
        # back unchanged does not count; only one the user typed differently is kept.
        typed = {k: changes[k] for k in ("person_id", "org_id") if k in changes and changes[k] != before.get(k)}
        changes.update({"person_id": "", "org_id": "", "person_name": "", "org_name": "", "person_day_hours": None, **typed})
    _identify_failed[0] = 0.0
    s = config.update(changes)
    # the Claude Code switch (claudelink.py): Pulsar adds itself to Claude Code, or takes back what it added; on
    # "relink", it adds itself again (moved, or removed by hand)
    link = None
    if ("ask_claude" in changes and changes["ask_claude"] != before["ask_claude"]) or (body.get("relink") and s["ask_claude"]):
        link = claudelink.connect(PORT[0], s) if s["ask_claude"] else claudelink.disconnect(s)
        config.update({"claude_rules_added": link["rulesAdded"]})
        if not link["ok"]:
            logbook.record("claude code", link["message"], kind="warning")
    s = current_settings()
    # Productive is asked again only when the connection changed: saving a rule or a time zone needs no test
    return {"config": public_config(s), "test": test_productive(s) if connection else None, "link": link}


# ---- tasks, services, logging ----------------------------------------------------------------------------

_recent = config.Newest(8)  # (date, person) -> (until, suggestions)


def api_estimates(q: dict) -> dict:
    ids = list(dict.fromkeys(i for i in (q.get("ids") or "").split(",") if is_id(i)))[:80]
    try:
        return {"tasks": productive.task_details(ids, current_settings())}
    except productive.ProductiveError as exc:  # not connected (an expired Claude Code token): the page says so; not an error
        return {"tasks": {}, "error": str(exc)}


def api_suggest(q: dict) -> dict:
    """Tasks to offer first: the ones you logged to in the last 30 days (with the service you used last),
    then the open ones assigned to you."""
    s = current_settings()
    key = (q["date"], s.get("person_id"))
    if key not in _recent or time.time() > _recent[key][0]:
        d1 = q["date"]
        d0 = (datetime.strptime(d1, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
        rows, _, _, _ = productive_rows(d0, d1, s)
        by_task: dict = {}
        for r in sorted(rows, key=lambda r: (r["date"], r.get("start") or 0)):
            if not r.get("taskId"):
                continue
            t = by_task.setdefault(r["taskId"], {"taskId": r["taskId"], "title": r["task"], "project": r["project"],
                                                 "minutes": 0, "count": 0})
            t.update({"serviceId": r.get("serviceId"), "service": r["service"], "budget": r["budget"],
                      "billable": r.get("billable"), "last": r["date"]})
            t["minutes"] += r["minutes"]
            t["count"] += 1
        recent = sorted(by_task.values(), key=lambda t: (t["last"], t["count"]), reverse=True)
        problem = ""
        try:
            mine = productive.assigned_tasks(s)
        except Exception as exc:  # the dialog still offers the rest, and says why this group is empty
            mine = []
            problem = f"Your assigned tasks could not be read: {exc}"
            logbook.record("productive", problem, kind="warning", once_per=600)
        known = {t["taskId"]: t for t in mine}
        for t in recent:  # what Productive says of the tasks you logged to: due date, status, blocked
            t.update({k: known[t["taskId"]][k] for k in ("due", "status", "blocked")} if t["taskId"] in known else {})
        assigned = urgent_first([t for t in mine if t["taskId"] not in by_task], today(config.tz(s)))
        # kept 10 minutes; one without the assigned tasks only a minute, so the next dialog tries again
        _recent[key] = (time.time() + (60 if problem else 600),
                        {"recent": recent, "assigned": assigned, **({"assignedError": problem} if problem else {})})
    return _recent[key][1]


def urgent_first(tasks: list, today_iso: str) -> list:
    """Overdue, due today, blocked or in progress, then the rest (each by due date, then as Productive listed them)."""
    def rank(item):
        i, t = item
        due = t.get("due") or ""
        group = 0 if due and due < today_iso else 1 if due == today_iso else \
            2 if t.get("blocked") or "progress" in (t.get("status") or "").lower() else 3
        return group, due or "9999-12-31", i
    return [t for _, t in sorted(enumerate(tasks), key=rank)]


# ---- entries that repeat: the daily meeting --------------------------------------------------------------

REPEAT_DAYS = 28    # how far back the history goes
REPEAT_NEAR = 5     # minutes: entries starting this close count as the same time (a meeting starts on the minute)
REPEAT_MIN_DAYS = 3
REPEAT_SHARE = 0.5  # of the days a task was logged, how many must be at that time: work logged all day is not a repeat
REPEAT_STALE = 10   # days: a repeat not seen this long has stopped (a meeting that was cancelled)
CALL_WINDOW = 75    # minutes around the usual time to look for the call (wide enough for an hour's clock change)
CALL_GAP = 20       # minutes: the call goes on while its window shows again within this; people work during calls
_repeats = config.Newest(8)  # (date, person) -> (when, patterns)


def is_call(it: dict) -> bool:
    """A window of a call in progress: Zoom's meeting window, a Meet or Teams call, a Slack huddle, Webex."""
    app, title = it.get("app") or "", it.get("title") or ""
    where = f"{it.get('host') or ''} {it.get('url') or ''}"
    if re.search(r"zoom", app, re.I):
        return bool(re.search(r"meeting|webinar", title, re.I))
    if re.search(r"teams", app, re.I) or "teams.microsoft" in where:
        return bool(re.search(r"meeting|call", title, re.I))
    if re.search(r"slack", app, re.I):
        return bool(re.search(r"huddle", title, re.I))
    if re.search(r"webex|facetime", app, re.I):
        return True
    return bool(re.search(r"meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}|zoom\.us/(wc|j)/", where)
                or re.match(r"Meet\s*[-–]\s*[a-z]{3}-[a-z]{4}-[a-z]{3}", title) or "Google Meet" in title)


def repeating(rows: list, tz, date: str) -> list:
    """Tasks logged at the same time of day on at least three days of the history, and on at least half the days
    they were logged at all (the daily meeting, not a task worked on all day): the usual start and length, the note
    written most often, and the weekdays it happened on."""
    by_task: dict = {}
    for r in rows:
        if r.get("start") is None or not r.get("taskId") or r.get("date", "") >= date:
            continue
        t = datetime.fromtimestamp(r["start"], tz)
        by_task.setdefault(r["taskId"], []).append((t.hour * 60 + t.minute, t.weekday(), r))
    out = []
    for task_id, xs in by_task.items():
        best: list = []
        for at, _, _ in xs:
            near = [x for x in xs if abs(x[0] - at) <= REPEAT_NEAR]
            if len({x[2]["date"] for x in near}) > len({x[2]["date"] for x in best}):
                best = near
        per_day: dict = {}
        for x in best:  # one entry a day: the longest at that time
            d = x[2]["date"]
            if d not in per_day or x[2]["minutes"] > per_day[d][2]["minutes"]:
                per_day[d] = x
        logged_days = {x[2]["date"] for x in xs}
        if len(per_day) < max(REPEAT_MIN_DAYS, REPEAT_SHARE * len(logged_days)):
            continue
        last = max(per_day)
        if (datetime.strptime(date, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days > REPEAT_STALE:
            continue
        starts = sorted(x[0] for x in per_day.values())
        lengths = sorted(x[2]["minutes"] for x in per_day.values())
        notes: dict = {}
        for d in sorted(per_day):
            n = (per_day[d][2].get("note") or "").strip()
            if n:
                notes[n] = notes.get(n, 0) + 1
        latest = per_day[last][2]
        out.append({"taskId": task_id, "title": latest.get("task") or "", "project": latest.get("project") or "",
                    "serviceId": latest.get("serviceId"), "service": latest.get("service") or "",
                    "at": starts[len(starts) // 2], "minutes": lengths[len(lengths) // 2],
                    "note": max(notes, key=lambda n: notes[n]) if notes else "", "days": len(per_day),
                    "weekdays": sorted({x[1] for x in per_day.values()})})
    return sorted(out, key=lambda x: x["at"])


def repeat_suggestions(patterns: list, items: list, weekday: int) -> list:
    """The repeats seen in a day's activity: a call near the usual time, on a weekday it happened on before. The
    call runs from its first window to its last (windows less than 20 minutes apart), at most half as long again as
    usual; a start within 10 minutes of the usual time, or of an hour either side (the clocks changed), is the usual
    (people join a few minutes late, or open the call's window later)."""
    spans: list = []
    for it in sorted((it for it in items if it.get("cat") == "work" and is_call(it)), key=lambda it: it["s"]):
        if spans and it["s"] - spans[-1][1] <= CALL_GAP:
            spans[-1][1] = max(spans[-1][1], it["e"])
        else:
            spans.append([it["s"], it["e"], it.get("app") or "a call"])
    out = []
    for p in patterns:
        near = [x for x in spans if abs(x[0] - p["at"]) <= CALL_WINDOW]
        if weekday not in p["weekdays"] or not near:
            continue
        a, b, app = min(near, key=lambda x: abs(x[0] - p["at"]))
        start = next((u for u in (p["at"], p["at"] - 60, p["at"] + 60) if abs(a - u) <= 10), int(round(a / 5) * 5))
        minutes = max(5, min(int(round((b - start) / 5) * 5), int(round(p["minutes"] * 1.5 / 5) * 5)))
        out.append({**p, "s": start, "e": start + minutes, "minutes": minutes, "seen": app})
    return out


def api_repeats(q: dict) -> dict:
    """Entries you log at the same time most days, offered on a day the tracker saw the call. Nothing is written."""
    s = current_settings()
    if not s.get("person_id"):
        return {"repeats": []}
    date, tz = q["date"], config.tz(s)
    key = (date, s.get("person_id"))
    if key not in _repeats or time.time() - _repeats[key][0] > 3600:
        rows, problem = history_rows(date, s)
        if problem:
            return {"repeats": [], "error": problem}
        _repeats[key] = (time.time(), repeating(rows, tz, date))
    t0, t1 = day_bounds(date, tz)
    found = trackers.both(t0, t1, s["activitywatch_url"], trackers.classifier(s["personal_patterns"]))
    items = found[trackers.pick(found, q.get("source", "auto"))]
    items = clock_spans(items, t0, t1, tz)  # by the clock on a day the clocks change, like the usual times
    return {"repeats": repeat_suggestions(_repeats[key][1], items, datetime.strptime(date, "%Y-%m-%d").weekday())}


_rows_before = config.Newest(10)  # (date, person) -> (when, rows)
_rows_lock = threading.Lock()
_reading = config.Newest(32)     # (date, person) -> a lock held while that history is read
HISTORY_FOR = 3600               # seconds a history read is kept


def history_ready(date: str, s: dict) -> bool:
    """The history for `date` has been read in the last hour, so history_rows answers at once."""
    with _rows_lock:
        kept = _rows_before.get((date, s.get("person_id")))
    return bool(kept and time.time() - kept[0] < HISTORY_FOR)


def history_rows(date: str, s: dict, days: int = REPEAT_DAYS) -> tuple:
    """(your entries of the `days` days before `date`, problem), kept an hour: the history the repeats, the meetings
    and the Likely rows learn from."""
    # One read per date covers every caller: the longest history anyone asks for, sliced for the shorter ones (it used
    # to be three reads, 60 + 28 + 31 days, before the day's own entries could load). The columns ask at the same
    # moment, so a caller that comes while it is being read waits for that read instead of starting another.
    span = max(days, learn.LEARN_DAYS)
    key = (date, s.get("person_id"))
    first = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    with _rows_lock:
        reading = _reading.get(key) or threading.Lock()
        _reading[key] = reading
    with reading:
        with _rows_lock:
            kept = _rows_before.get(key)
        if not (kept and time.time() - kept[0] < HISTORY_FOR):
            d0 = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=span)).strftime("%Y-%m-%d")
            d1 = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            rows, problem, _, from_copy = productive_rows(d0, d1, s, bulk=True)
            if problem and not from_copy:
                return [], problem
            kept = (time.time(), rows)
            with _rows_lock:
                _rows_before[key] = kept
    return [r for r in kept[1] if r.get("date", "") >= first], None


def history_in_background(date: str, s: dict) -> None:
    """Start reading the history for `date`, unless it is being read already."""
    with _rows_lock:
        reading = _reading.get((date, s.get("person_id")))
    if reading and reading.locked():
        return

    def run():
        try:
            history_rows(date, s, days=learn.LEARN_DAYS)
        except Exception as exc:  # a hint for Likely: the page asks again and gets the rows without learned tasks
            logbook.record("learn", f"Reading your history from Productive: {type(exc).__name__}: {exc}", kind="warning")
    threading.Thread(target=run, daemon=True, name="pulsar-history").start()


# ---- a meeting's task --------------------------------------------------------------------------------

WORD = re.compile(r"#?[^\W_]{3,}|#\d+")
GENERIC_WORDS = {"meeting", "meetings", "sync", "call", "calls", "weekly", "daily", "monthly", "check", "catch", "chat",
                 "with", "and", "the", "for", "review", "update", "updates", "team", "internal", "zoom", "google", "meet",
                 "standup", "stand", "about", "from", "into", "project", "projects", "session", "discussion", "kick",
                 "kickoff", "follow", "quick", "time", "foster", "interactive", "ongoing", "support"}


def words_of(text: str, skip: set) -> set:
    return {w for w in WORD.findall((text or "").lower()) if w not in GENERIC_WORDS and w not in skip}


def meeting_tasks(meeting: dict, past: list, rows: list, offered: list, skip: set = frozenset()) -> list:
    """Tasks for a meeting, most likely first. First the tasks you logged during the earlier meetings of the same
    series (or the same title), then your tasks that share a word with its title (people's names you go by, and words
    every meeting has, do not count). Each with why, in words."""
    title = (meeting.get("title") or "").strip()
    norm = re.sub(r"\s+", " ", title.lower())
    same = [p for p in past if not p.get("declined")
            and ((meeting.get("uid") and p.get("uid") == meeting["uid"]) or re.sub(r"\s+", " ", p.get("title", "").lower()) == norm)]
    counts: dict = {}
    seen: dict = {}
    for p in same:  # each earlier meeting votes for the task that took most of it (not the call before it, running over)
        cover: dict = {}
        for r in rows:
            if r.get("start") is None or not r.get("taskId"):
                continue
            overlap = min(r["start"] + r["minutes"] * 60, p["e"]) - max(r["start"], p["s"])
            if overlap > 0:
                cover[r["taskId"]] = cover.get(r["taskId"], 0) + overlap
                seen[r["taskId"]] = r
        best = max(cover, key=cover.get) if cover else None
        if best and cover[best] >= min(600, (p["e"] - p["s"]) / 3):  # ten minutes of it, or a third of a short one
            counts[best] = counts.get(best, 0) + 1
    out = [{"taskId": t, "title": seen[t].get("task") or "", "project": seen[t].get("project") or "",
            "why": f"logged during {n} of the last {len(same)} like it", "n": n, "of": len(same)}
           for t, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    words = words_of(title, skip)
    if words:
        scored = []
        for t in offered:
            shared = words & words_of(f"{t.get('title', '')} {t.get('project', '')}", skip)
            if shared:
                meetingish = "meeting" in (t.get("title") or "").lower()
                scored.append((len(shared) + (0.5 if meetingish else 0), t, shared))
        have = {o["taskId"] for o in out}
        for _, t, shared in sorted(scored, key=lambda x: -x[0]):
            if t["taskId"] not in have and len(out) < 5:
                have.add(t["taskId"])
                out.append({"taskId": t["taskId"], "title": t.get("title") or "", "project": t.get("project") or "",
                            "why": "the title says " + ", ".join(f"“{w}”" for w in sorted(shared))})
    return out[:5]


def api_meeting_tasks(q: dict) -> dict:
    """The tasks to offer first when logging a meeting. A hint: what cannot be read is left out, never an error."""
    s = current_settings()
    date = q["date"]
    meeting = {"uid": (q.get("uid") or "")[:300], "title": (q.get("title") or "")[:300]}
    first = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=REPEAT_DAYS)).strftime("%Y-%m-%d")
    past = calendar.history(first, REPEAT_DAYS, s)
    rows, _ = history_rows(date, s)
    try:
        offered = api_suggest({"date": date})
        offered = (offered.get("recent") or []) + (offered.get("assigned") or [])
    except Exception:
        offered = []
    skip = set(WORD.findall(str(s.get("person_name") or "").lower()))  # your own name is in many task titles
    return {"tasks": meeting_tasks(meeting, past, rows, offered, skip)}


def api_search(q: dict) -> dict:
    text = (q.get("q") or "").strip()
    return {"tasks": productive.search_tasks(text, current_settings()) if len(text) >= 2 else []}


def api_services(q: dict) -> dict:
    if not is_id(q.get("task") or ""):
        raise ValueError("task=<id>&date=YYYY-MM-DD")
    return productive.bookable_services(q["task"], q["date"], current_settings())


def api_log(body: dict) -> dict:
    """One time entry, written only from a click in the page. Re-reads the day first: other sessions log too,
    and an entry on top of another one is shown to you before anything is written."""
    s = current_settings()
    date, start = str(body.get("date") or ""), str(body.get("start") or "")
    minutes, task_id, service_id = body.get("minutes"), str(body.get("taskId") or ""), str(body.get("serviceId") or "")
    if not (is_id(task_id) and is_id(service_id)):
        raise ValueError("Pick a task and a service.")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 24 * 60:
        raise ValueError("Minutes must be a whole number from 1 to 1440.")
    m = re.fullmatch(r"([01][0-9]|2[0-3]):([0-5][0-9])", start)
    if not (DATE.match(date) and m):
        raise ValueError("Pick a date and a start time (HH:MM).")
    tz = config.tz(s)
    begin = datetime.strptime(f"{date} {start}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)  # raises on 2026-02-30
    a, b = begin.timestamp(), begin.timestamp() + minutes * 60
    if b > day_bounds(date, tz)[1]:
        raise ValueError("An entry cannot run past midnight; log the part after it on the next day.")
    # Log the day sends each row with its run and row names: the journal records it before the write, and refuses a
    # row that run (or any run of that day) already sent, so a restart or a retry never writes it twice
    journal = None
    if body.get("run") is not None or body.get("row") is not None:
        journal = {"run": str(body.get("run") or ""), "row": str(body.get("row") or ""),
                   "entry": {"start": start, "minutes": minutes, "taskId": task_id, "serviceId": service_id}}
    if body.get("dry"):
        if journal:
            dayplan.check(journal["run"], journal["row"], date, start, minutes, task_id, service_id)
        return _log_checked(body, s, date, begin, minutes, task_id, service_id, tz, a, b)
    key = (date, start, minutes, task_id, service_id)
    with _log_lock:  # one write at a time: two tabs, or two quick clicks, cannot both pass the overlap check
        if journal:
            dayplan.check(journal["run"], journal["row"], date, start, minutes, task_id, service_id)
        if time.time() - _just_logged.get(key, 0) < 60:
            raise ValueError("This entry was logged a moment ago. Check the Logged column before logging it again.")
        result = _log_checked(body, s, date, begin, minutes, task_id, service_id, tz, a, b, journal)
        if "created" in result:
            _just_logged[key] = time.time()
        return result


SEEN = {"start": "start", "minutes": "length", "taskId": "task", "serviceId": "service", "note": "note"}


def _hhmm(epoch, tz) -> str | None:
    """A start as the page shows it: the minute it falls in."""
    return None if epoch is None else datetime.fromtimestamp(epoch, tz).strftime("%H:%M")


def _own_entry(body: dict, s: dict) -> tuple:
    """(the entry as Productive has it now, every entry of that day): read afresh, so a change never works from a
    stale copy. Refuses one that is not there any more, one Productive does not show as yours, and, given `seen` (the
    entry as the page showed it: start, minutes, task, service, note), one that has changed since, so a click never
    lands on another entry or undoes someone's newer change. "Approved" alone is not refused: where a budget needs no
    approval, Productive approves every entry at once and still lets its author change it; where an approver has
    approved it, Productive refuses the change itself and the page shows its reason."""
    entry_id, date, person = str(body.get("id") or ""), str(body.get("date") or ""), str(s.get("person_id") or "")
    if not (is_id(entry_id) and DATE.match(date)):
        raise ValueError("id and date are needed.")
    if not person.isdigit():  # the day's read could then hold anyone's entries
        raise ValueError("Pulsar does not know your Productive person ID yet, so it changes nothing. Open Settings: it "
                         "fills in by itself once Productive answers, or type it there.")
    existing, problem, _ = productive.entries(date, date, s)
    if problem:
        raise RuntimeError(problem)
    mine = next((x for x in existing if str(x.get("id")) == entry_id), None)
    if not mine:
        raise ValueError("That entry is not in Productive on that day any more. The Logged column has been refreshed.")
    if str(mine.get("personId") or "") != person:
        raise ValueError("Productive did not show that entry as yours, so Pulsar leaves it alone.")
    seen = body.get("seen")
    if seen is not None:
        if not isinstance(seen, dict):
            raise ValueError("seen is the entry as the page showed it.")
        now = {"start": _hhmm(mine.get("start"), config.tz(s)), "minutes": int(mine["minutes"]),
               "taskId": str(mine.get("taskId") or ""), "serviceId": str(mine.get("serviceId") or ""),
               "note": str(mine.get("note") or "").strip()}
        was = {"start": seen.get("start") or None, "minutes": seen.get("minutes"), "taskId": str(seen.get("taskId") or ""),
               "serviceId": str(seen.get("serviceId") or ""), "note": str(seen.get("note") or "").strip()}
        changed = [SEEN[k] for k in SEEN if k in seen and was[k] != now[k]]
        if changed:
            raise ValueError(f"That entry changed in Productive since you looked at it (its {', '.join(changed)}), so "
                             "nothing was written. The Logged column has been refreshed: look at it again.")
    return mine, existing


def api_entry_update(body: dict) -> dict:
    """Changes one of your entries: a new start, length, note, task or service. From the Edit dialog or a dragged
    edge in the Logged column. The same checks as logging: a fresh read, overlaps shown first, one write at a time.
    Only what differs from the entry as Productive has it is written, so an unchanged note or service is left as it
    is; start and minutes may be left out to keep them."""
    s = current_settings()
    tz = config.tz(s)
    start, minutes, note = body.get("start"), body.get("minutes"), body.get("note")
    if minutes is not None and (isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 24 * 60):
        raise ValueError("Minutes must be a whole number from 1 to 1440.")
    if start is not None and not re.fullmatch(r"([01][0-9]|2[0-3]):([0-5][0-9])", str(start)):
        raise ValueError("Pick a start time (HH:MM).")
    for k in ("taskId", "serviceId"):
        if body.get(k) and not is_id(str(body[k])):
            raise ValueError(f"{k} is a Productive id.")
    if not isinstance(body.get("seen"), dict):
        raise ValueError("The entry as you saw it (seen) is needed, so a change never lands on one that changed meanwhile.")
    with _log_lock:
        mine, existing = _own_entry(body, s)
        change: dict = {}
        if start is not None and start != _hhmm(mine.get("start"), tz):
            change["started_at"] = datetime.strptime(f"{mine['date']} {start}", "%Y-%m-%d %H:%M").replace(
                tzinfo=tz).isoformat(timespec="seconds")
        if minutes is not None and minutes != int(mine["minutes"]):
            change["minutes"] = minutes
        if note is not None and str(note).strip() != str(mine.get("note") or "").strip():
            if len(str(note)) > 2000:
                raise ValueError(f"The note is {len(str(note))} characters; Pulsar sends at most 2000. Shorten it, or "
                                 "change it in Productive.")
            change["note"] = str(note)
        for k in ("task", "service"):
            if body.get(k + "Id") and str(body[k + "Id"]) != str(mine.get(k + "Id") or ""):
                change[k + "_id"] = str(body[k + "Id"])
        a = datetime.fromisoformat(change["started_at"]).timestamp() if "started_at" in change else mine.get("start")
        m = change.get("minutes", int(mine["minutes"]))
        if a is not None and ("started_at" in change or "minutes" in change):
            if a + m * 60 > day_bounds(mine["date"], tz)[1]:
                raise ValueError("An entry cannot run past midnight.")
            clashes = [x for x in existing if x is not mine and x.get("start") is not None
                       and x["start"] < a + m * 60 and x["start"] + x["minutes"] * 60 > a]
        else:
            clashes = []  # where it sits does not change
        overlap = [{"start": datetime.fromtimestamp(x["start"], tz).strftime("%H:%M"), "minutes": x["minutes"],
                    "project": x["project"], "task": x.get("task")} for x in sorted(clashes, key=lambda x: x["start"])]
        if body.get("dry"):
            return {"dry": True, "wouldChange": {"id": mine["id"], **change}, "overlap": overlap}
        if not change:
            return {"unchanged": mine["id"]}
        if clashes and not body.get("force"):
            return {"overlap": overlap}
        productive.update_entry(mine["id"], change, s)
        _recent.clear()
        return {"updated": mine["id"]}


def api_entry_delete(body: dict) -> dict:
    """Deletes one of your entries, after the page asked "Delete …?" and only as it showed it (`seen`). Productive
    keeps it in its Recycle Bin. With `run` (Undo in Log the day), only an entry that run created, and only as it wrote it."""
    s = current_settings()
    run = body.get("run")
    if run is None and not isinstance(body.get("seen"), dict):
        raise ValueError("The entry as you saw it (seen) is needed, so a delete never lands on another entry.")
    with _log_lock:
        mine, _ = _own_entry(body, s)
        if run is not None:
            made = {x["id"]: x for x in dayplan.undo_ids(dayplan.journal(), str(run))}
            x = made.get(str(mine["id"]))
            if not x:
                raise ValueError("That entry was not created by this Log the day run, so Undo leaves it alone.")
            tz = config.tz(s)
            now = (datetime.fromtimestamp(mine["start"], tz).strftime("%H:%M") if mine.get("start") is not None else None,
                   int(mine["minutes"]), str(mine.get("taskId") or ""))
            if now != (x["start"], int(x["minutes"]), str(x["taskId"] or "")):
                raise ValueError("That entry was changed after this run logged it, so Undo leaves it alone. Delete it "
                                 "from its card if you mean to.")
        if body.get("dry"):
            return {"dry": True, "wouldDelete": mine["id"]}
        productive.delete_entry(mine["id"], s)
        if run is not None:
            dayplan.mark_undone(str(run), mine["id"])
        _recent.clear()
        return {"deleted": mine["id"]}


def api_dayplan(q: dict) -> dict:
    """Log the day: the whole day proposed for review (dayplan.py). Nothing is written. ignore_logged=1 plans as if
    nothing were logged that day; only a read-only test server (PULSAR_READ_ONLY) accepts it."""
    ignore = q.get("ignore_logged") == "1"
    if ignore and not config.read_only():
        raise ValueError("ignore_logged is for read-only test servers only.")
    source = q.get("source", "auto")
    if source not in ("auto", "aw", "memtime"):
        raise ValueError("source is auto, aw or memtime")
    return dayplan.gather(q["date"], source, ignore)


def api_dayplan_journal(q: dict) -> dict:
    """The Log the day runs of a day, as the journal has them: what each row became, and what Undo would delete."""
    return {"runs": [{**r, "undo": dayplan.undo_ids({"runs": [r]}, r["run"])} for r in dayplan.runs_for(q["date"])]}


_log_lock = threading.Lock()
_just_logged: dict = {}  # (date, start, minutes, task, service) -> when it was written


def _log_checked(body: dict, s: dict, date: str, begin, minutes: int, task_id: str, service_id: str, tz, a: float,
                 b: float, journal: dict | None = None) -> dict:
    def note(status, **fields):  # a Log the day row: its state in the journal, on disk before the next step
        if journal:
            dayplan.mark(journal["run"], date, journal["row"], journal["entry"], status, **fields)

    if not body.get("dry") and config.writes_blocked():  # refused before the journal or the log hears of it
        raise ValueError(config.writes_blocked())
    existing, problem, _ = productive.entries(date, date, s)
    if problem:
        note("refused", reason=f"Not sent: {problem}")
        raise RuntimeError(problem)
    clashes = [x for x in existing if x.get("start") is not None and x["start"] < b and x["start"] + x["minutes"] * 60 > a]
    if body.get("dry"):  # everything up to the write, for tests: nothing reaches Productive
        return {"dry": True, "wouldWrite": {"date": date, "started_at": begin.isoformat(timespec="seconds"), "minutes": minutes,
                                            "task": task_id, "service": service_id},
                "overlap": [{"start": datetime.fromtimestamp(x["start"], tz).strftime("%H:%M"), "minutes": x["minutes"],
                             "project": x["project"]} for x in clashes]}
    if clashes and not body.get("force"):
        overlap = [{"start": datetime.fromtimestamp(x["start"], tz).strftime("%H:%M"), "minutes": x["minutes"],
                    "project": x["project"], "task": x["task"]} for x in sorted(clashes, key=lambda x: x["start"])]
        note("refused", reason="Not sent: it would overlap " + "; ".join(f"{o['start']} {o['project']}" for o in overlap))
        return {"overlap": overlap}
    note("sending")
    try:
        created = productive.create_entry({"date": date, "minutes": minutes, "started_at": begin.isoformat(timespec="seconds"),
                                           "note": str(body.get("note") or "")[:2000], "task_id": task_id,
                                           "service_id": service_id}, s)
    except productive.ProductiveUnclear as exc:
        note("unknown", reason=str(exc))
        raise
    except productive.ProductiveError as exc:  # a clear refusal: nothing was written
        note("refused", reason=str(exc))
        raise
    except BaseException as exc:  # anything else after sending: it may have happened
        note("unknown", reason=f"{type(exc).__name__}: {exc}")
        raise
    note("logged", id=str(created.get("id") or ""))
    _recent.clear()
    return {"created": created}


# ---- HTTP --------------------------------------------------------------------------------------------

def key_refusal(path: str, body: dict):
    """Why a POST made with the key alone (no page session) is refused, or None. The key is a file any program of this
    user can read, a Claude Code session included, so it opens only what its two holders need: the launcher quits
    Pulsar, and Pulsar in Claude Code (mcp.py) logs new entries through the Log the day journal, with the overlap
    check. Never `force`, and nothing else: no change or delete of an entry, no settings, no update, no Ask Claude.
    A program of this user can still do more by other means (HOW-IT-WORKS.md, Security); this closes the plain curl."""
    if path == "/api/quit":
        return None
    if path == "/api/log":
        if body.get("force"):
            return "An entry that overlaps another is written only from Pulsar's window."
        if body.get("run") is None or body.get("row") is None:
            return "Without Pulsar's window, an entry is logged only as part of a run Log the day can list and undo."
        return None
    return "This needs Pulsar's window: the key may only read, quit Pulsar and log new entries from Claude Code."


class Handler(BaseHTTPRequestHandler):
    server_version = "Pulsar"
    timeout = 20  # a client that stops sending mid-request frees its thread instead of holding it forever

    def log_message(self, *a):  # quiet
        pass

    def send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                         "connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser left (a new day, a reload): nothing went wrong

    def send_json(self, data, code: int = 200):
        self.send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def local(self) -> bool:
        # Only this machine, by name: a page elsewhere that rebinds a name to 127.0.0.1 is refused.
        return (self.headers.get("Host") or "").split(":")[0] in ("127.0.0.1", "localhost")

    def cross_site(self) -> bool:
        """A request another website's page made: browsers say so in Sec-Fetch-Site, or (for a WebSocket handshake,
        which carries no Sec-Fetch headers) in Origin and Upgrade."""
        origin = self.headers.get("Origin")
        return (self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site")
                or bool(origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"))
                or bool(self.headers.get("Upgrade")))

    def page(self) -> bool:
        """The page's session token (from localStorage, in a header no other site or port ever gets)."""
        return config.valid_session(self.headers.get("X-Pulsar-Session") or "")

    def signed_in(self) -> bool:
        """The page, or the key itself: the launcher and Pulsar in Claude Code (mcp.py). The key alone may do less on a
        POST (key_refusal)."""
        return self.page() or config.is_key(self.headers.get("X-Pulsar-Key") or "")

    def do_GET(self):
        if not self.local():
            return self.send(403, b"forbidden", "text/plain")
        url = urlparse(self.path)
        if url.path == calendar.CALLBACK:  # Google's answer to a sign-in: its own narrow rules, below
            return self.google_callback(url)
        navigation = self.headers.get("Sec-Fetch-Mode") == "navigate" and url.path == "/"
        if self.cross_site() and not navigation:  # a link from elsewhere may open the page, nothing more
            return self.send_json({"error": "refused"}, 403)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/api/whoami":  # lets the launcher check it is talking to this user's Pulsar, not an impostor
            nonce = q.get("nonce", "")
            if not re.fullmatch(r"[0-9a-f]{16,64}", nonce):
                return self.send_json({"error": "nonce"}, 400)
            proof = hmac.new(config.session_secret().encode(), nonce.encode(), hashlib.sha256).hexdigest()
            return self.send_json({"proof": proof})
        if url.path not in PUBLIC and not url.path.startswith("/static/") and not self.signed_in():
            if url.path.startswith("/icon/"):
                return self.send(401, b"", "image/png")
            return self.send_json({"error": SIGN_IN, "signIn": True}, 401)
        try:
            if url.path in ("/", "/index.html"):
                return self.file(os.path.join(WEB, "index.html"))
            m = re.fullmatch(r"/static/((?:js/)?[a-z0-9_-]+\.(?:css|js|svg|png))", url.path)
            if m:
                return self.file(os.path.join(WEB, m.group(1)))
            if url.path.startswith("/icon/"):
                path = trackers.ICONS.path(url.path[6:])
                return self.file(path) if path else self.send(404, b"", "image/png")
            if url.path == "/api/version":  # polled by the page to notice a restart into a new version
                return self.send_json({"version": __version__, "codename": names.codename(__version__)[0]})
            if url.path == "/api/config":
                return self.send_json(public_config(current_settings()))
            if url.path == "/api/changelog":  # what each release changed, from this copy's CHANGELOG.md
                from . import changelog
                return self.send_json({"releases": changelog.read()})
            if url.path == "/api/report":
                return self.send_json(api_report(q))
            if url.path in ("/api/logs", "/api/logs/download"):
                days = int(q.get("days") or 1)
                if days not in (1, 7, 30):
                    raise ValueError("days is 1, 7 or 30")
                if url.path == "/api/logs":
                    return self.send_json({"entries": logbook.read(days)})
                name = f"pulsar-log-{datetime.now().strftime('%Y-%m-%d')}-last-{days}-day{'s' if days > 1 else ''}.txt"
                body = logbook.as_text(days).encode("utf-8")
                self.send_response(200)
                for k, v in (("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body))),
                             ("Content-Disposition", f'attachment; filename="{name}"'), ("Cache-Control", "no-store"),
                             ("X-Content-Type-Options", "nosniff")):
                    self.send_header(k, v)
                self.end_headers()
                return self.wfile.write(body)
            if url.path == "/api/tasks/estimates":
                return self.send_json(api_estimates(q))
            if url.path == "/api/tasks/search":
                return self.send_json(api_search(q))
            if url.path == "/api/calendar/status":  # polled by Settings while the person signs in to Google
                return self.send_json(calendar.status(config.load()))
            routes = {"/api/activity": api_activity, "/api/entries": api_entries, "/api/proposal": api_proposal,
                      "/api/tasks/suggest": api_suggest, "/api/services": api_services, "/api/repeats": api_repeats,
                      "/api/calendar": api_calendar, "/api/meeting-tasks": api_meeting_tasks,
                      "/api/dayplan": api_dayplan, "/api/dayplan/journal": api_dayplan_journal}
            if url.path not in routes:
                return self.send(404, b"not found", "text/plain")
            if not DATE.match(q.get("date", "")):
                return self.send_json({"error": "date=YYYY-MM-DD"}, 400)
            return self.send_json(routes[url.path](q))
        except ValueError as exc:  # a bad date or parameter: the request's fault
            return self.send_json({"error": str(exc)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return  # the browser left before the answer: not an error
        except Exception as exc:  # shown in the page, the server keeps running
            logbook.record(f"GET {url.path}", f"{type(exc).__name__}: {exc}", detail=traceback.format_exc())
            return self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self):
        if not self.local():
            return self.send(403, b"forbidden", "text/plain")
        if self.cross_site() or self.headers.get("X-Pulsar") != "1":
            return self.send_json({"error": "refused"}, 403)
        path = urlparse(self.path).path
        if path == "/api/session":  # a one-time sign-in code for a session token; the only POST open without one
            try:
                length = max(0, min(int(self.headers.get("Content-Length") or 0), 4096))  # a negative one would read to EOF
                code = json.loads(self.rfile.read(length) or b"{}").get("code")
            except (ValueError, AttributeError):
                code = None
            if isinstance(code, str) and config.take_signin_code(code):
                return self.send_json({"token": config.new_session()})
            return self.send_json({"error": "This sign-in link has expired. Open Pulsar from its app icon again.",
                                   "signIn": True}, 401)
        if not self.signed_in():
            return self.send_json({"error": SIGN_IN, "signIn": True}, 401)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self.send_json({"error": "bad Content-Length"}, 400)
        if length < 0:
            return self.send_json({"error": "bad Content-Length"}, 400)
        if length > 65536:
            return self.send_json({"error": "too large"}, 413)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("The request body must be a JSON object.")
            refused = None if self.page() else key_refusal(path, body)
            if refused:
                return self.send_json({"error": refused}, 403)
            if urlparse(self.path).path == "/api/settings":
                return self.send_json(api_settings(body))
            if urlparse(self.path).path == "/api/session/end":  # sign this browser out
                config.end_session(self.headers.get("X-Pulsar-Session") or "")
                return self.send_json({"ok": True})
            if urlparse(self.path).path == "/api/client-error":  # an error in the page itself
                logbook.record("page", str(body.get("message") or "")[:500],
                               detail=f"{str(body.get('source') or '')[:200]}:{body.get('line')}\n{str(body.get('stack') or '')[:3000]}")
                return self.send_json({"ok": True})
            if urlparse(self.path).path == "/api/productive/test":
                return self.send_json({"test": test_productive(current_settings())})
            if urlparse(self.path).path == "/api/update/check":
                return self.send_json({"update": UPDATER.check()})
            if urlparse(self.path).path == "/api/update/install":
                return self.send_json({"update": UPDATER.install_now()})
            if urlparse(self.path).path == "/api/update/apply":
                if not UPDATER.ready:
                    return self.send_json({"error": "No update is ready."}, 400)
                threading.Timer(0.5, UPDATER.apply).start()  # answer first, then restart into the new version
                return self.send_json({"ok": True})
            if urlparse(self.path).path == "/api/log":
                return self.send_json(api_log(body))
            if urlparse(self.path).path == "/api/entry/update":
                return self.send_json(api_entry_update(body))
            if urlparse(self.path).path == "/api/entry/delete":
                return self.send_json(api_entry_delete(body))
            if urlparse(self.path).path == "/api/likely/ask":
                return self.send_json(api_ask_claude(body))
            if urlparse(self.path).path == "/api/likely/note":
                return self.send_json(api_likely_note(body))
            if urlparse(self.path).path == "/api/calendar/connect":
                url = calendar.start(self.server.server_address[1])
                try:
                    opened = bool(calendar.open_browser(url))  # the default browser; the page also offers the link
                except Exception:
                    opened = False
                return self.send_json({"url": url, "opened": opened})
            if urlparse(self.path).path == "/api/calendar/disconnect":
                return self.send_json(calendar.disconnect(config.load()))
            if urlparse(self.path).path == "/api/quit":
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return self.send_json({"ok": True})
            return self.send(404, b"not found", "text/plain")
        except (ValueError, RuntimeError, productive.ProductiveError, calendar.CalendarError) as exc:
            if not isinstance(exc, ValueError):  # Productive refused or did not answer: worth a line in Logs
                logbook.record(f"POST {urlparse(self.path).path}", str(exc), kind="warning")
            return self.send_json({"error": str(exc)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # never drop the connection: say what went wrong
            logbook.record(f"POST {urlparse(self.path).path}", f"{type(exc).__name__}: {exc}", detail=traceback.format_exc())
            return self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def google_callback(self, url):
        """Google sends the browser back here after the person allows Pulsar (or does not). The request arrives as a
        top-level navigation from accounts.google.com, in whichever browser opened the sign-in, which may hold no
        Pulsar session: so the cross-site rule and the sign-in check above cannot apply to it. Instead:
        * only a page load counts: a request a page elsewhere makes as an image, a frame or a fetch is refused, so no
          other site can read the answer (and the answer is a short page with no data in it anyway);
        * it does nothing at all without a `state` this server handed out for a sign-in started in Settings (which
          needs the session) in the last 10 minutes and not used yet; the state is used up on first sight;
        * with one, it can do one thing only: finish that sign-in (calendar.finish: trade the code and the PKCE
          verifier for tokens). It takes no other parameter, reads nothing else and sends the browser nowhere."""
        mode, dest = self.headers.get("Sec-Fetch-Mode"), self.headers.get("Sec-Fetch-Dest")
        if (mode and mode != "navigate") or (dest and dest != "document") or self.headers.get("Upgrade"):
            return self.send(403, b"forbidden", "text/plain")
        q = {k: v[0] for k, v in parse_qs(url.query).items() if k in ("state", "code", "error")}
        try:
            result = calendar.finish(q)
        except Exception as exc:  # the keychain said no, say: the person sees why, the log keeps the detail
            logbook.record("calendar", f"Connecting Google Calendar failed: {type(exc).__name__}: {exc}",
                           detail=traceback.format_exc())
            result = {"ok": False, "known": True, "message": f"Pulsar could not finish connecting ({type(exc).__name__}). "
                                                             "See Logs in Pulsar, then try again."}
        if not result["known"]:
            logbook.record("calendar", "An answer from Google arrived for a sign-in Pulsar was not waiting for; it was "
                                       "ignored.", kind="warning")
        import html
        title = "Google Calendar is connected" if result["ok"] else "Google Calendar is not connected"
        page = (f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="color-scheme" content="light dark">'
                f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>Pulsar: {title}</title>'
                f'<body style="font: 15px/1.5 -apple-system, system-ui, sans-serif; max-width: 34em; margin: 12vh auto; '
                f'padding: 0 20px"><h1 style="font-size: 20px">{title}</h1><p>{html.escape(result["message"])}</p>'
                f'</body></html>')
        return self.send(200 if result["ok"] else 400, page.encode("utf-8"), "text/html; charset=utf-8")

    def file(self, path: str):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            return self.send(404, b"not found", "text/plain")
        return self.send(200, data, TYPES.get(os.path.splitext(path)[1], "application/octet-stream"))


PORT = [5610]  # the port this server listens on: Claude Code starts Pulsar's MCP server with it (claudelink.py)


def serve(port: int) -> ThreadingHTTPServer:
    PORT[0] = port
    config.ensure_dirs()
    config.session_secret()  # created before anyone needs it (the launcher reads it)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    UPDATER.start()
    return httpd
