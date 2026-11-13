"""The local web server: the page, its files, and a small JSON API. Listens on 127.0.0.1 only.

Reads go to the trackers, Productive and Google Calendar. It writes your settings and caches (owner-only files), the
tokens (keychain or a private file), a time entry in Productive only when you log one in the page, and ActivityWatch's
AFK timeout only when you click Fix in Settings (awsetup.py). A POST must carry the X-Pulsar header, which a page on another site cannot
send without the browser asking this server first, and this server never says yes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import traceback

from . import __version__, appupdate, askclaude, awsetup, calendar, claudelink, claudeuses, config, daymarks, dayplan, dayreview, flags, github, learn, likely, logbook, mcptools, newtask, productive, pause, ranges, report, selfcheck, sessions, trackers, trackhealth, updater, names
from .mcp import whoami_proof

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
# Never a command here: a program that gets this answer (a Claude Code session calling the API) must not be told how
# to sign itself in. The person signs in from the window; README.md says how to add a browser.
SIGN_IN = "This browser is not signed in to Pulsar. Open Pulsar from its app icon (it signs the browser in)."


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


def missing_hour(t0: float, t1: float, tz) -> list:
    """The minutes of the clock that do not exist on the day the clocks go forward ([{s, e}], e.g. 03:00-04:00 in
    Kyiv), else []: nothing may start in them, so Log the day treats them as time away."""
    change = clock_change(t0, t1, tz)
    if change is None:
        return []
    jump = int(round((datetime.fromtimestamp(t0 + change * 60, tz).utcoffset()
                      - datetime.fromtimestamp(t0 + (change - 1) * 60, tz).utcoffset()).total_seconds() / 60))
    return [{"s": change, "e": change + jump}] if jump > 0 else []


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
        "roundTo": s["round_to"], "minEntry": dayplan.MIN_ENTRY, "awayAfter": config.away_after(s), "workingDays": s["working_days"],
        "advanced": config.advanced(s),
        "activitywatch": awsetup.status(config.away_after(s)),  # its AFK timeout, read from its file; no program started
        "update": {**UPDATER.public(), "skip": s["update_skip"], "asked": s["update_asked"]},
        "errors24h": logbook.unseen(s.get("logs_seen") or 0),
        "likely": {"setting": s["likely"], "available": likely.available(), "enabled": likely.enabled(s),
                   "askClaude": s["ask_claude"], "askClaudeModel": claudeuses.model("ask", s), "claudeFound": askclaude.available(),
                   "askProductive": s["claude_ask_productive"], "link": claudelink.status(PORT[0], s)},
        "productive": {"source": s["productive_source"], "active": conn["kind"], "problem": conn.get("problem"),
                       "keychainStuck": config.keychain_stuck(),
                       "tokenSaved": bool(config.get_token()), "orgId": s["org_id"], "orgName": s["org_name"],
                       "personId": s["person_id"], "personName": s["person_name"], "web": productive_address(s)},
        "claude": {"uses": claudeuses.public(s)},  # everything Pulsar asks Claude, with each model (Settings > Claude)
        "calendar": calendar.status(s),  # configured, connected, account: never a token
        "preview": ({**(PREVIEW or {}), "writes": bool(s["preview_writes"])} if config.is_preview() else None),
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
    if "claude_ask_productive" in body:
        if not isinstance(body["claude_ask_productive"], bool):
            raise ValueError("claude_ask_productive is true or false")
        out["claude_ask_productive"] = body["claude_ask_productive"]
    if "ask_claude_model" in body:
        if body["ask_claude_model"] not in claudeuses.MODELS:
            raise ValueError("The model is sonnet, haiku or opus.")
        out["ask_claude_model"] = body["ask_claude_model"]
    if "claude_models" in body:  # one model per use (claudeuses.py); an unknown use or name keeps the default
        out["claude_models"] = claudeuses.clean(body["claude_models"])
        # Ask Claude's own key in step: an older Pulsar reads it, and the old choice never shows through the default
        out["ask_claude_model"] = claudeuses.model("ask", {"claude_models": out["claude_models"]})
    if "round_to" in body:
        v = body["round_to"]
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v)
        if isinstance(v, bool) or v not in config.ROUNDING:
            raise ValueError("Entries round to 5, 10 or 15 minutes.")
        out["round_to"] = v
    if "away_after" in body:
        out["away_after"] = away_minutes(body["away_after"])
    if "working_days" in body:
        v = body["working_days"]
        days = sorted({int(d) for d in v if str(d).isdigit() and 0 <= int(d) <= 6}) if isinstance(v, list) else []
        if not days:
            raise ValueError("Pick at least one working day.")
        out["working_days"] = days
    if "advanced" in body:  # merged into what is saved; only choices other than the default are kept
        v = body["advanced"]
        if not isinstance(v, dict):
            raise ValueError("Advanced settings are an object.")
        kept = dict(config.load().get("advanced") or {})
        for k, x in v.items():
            c = config._choice(k, x)
            if c is None:
                raise ValueError(f"Advanced: {k} is one of {', '.join(str(c) for c in config.ADVANCED.get(k, ()))}"
                                 if k in config.ADVANCED else f"Advanced: no setting {k}")
            kept[k] = c
        out["advanced"] = {k: x for k, x in kept.items() if k in config.ADVANCED and x != config.ADVANCED[k][0]}
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
    if "logs_seen" in body:  # "Mark as seen" in the error log (seconds since 1970)
        v = body["logs_seen"]
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < 2 ** 40:
            raise ValueError("logs_seen is a time in seconds.")
        out["logs_seen"] = v
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
                raise ValueError(f"The {'organization' if key == 'org_id' else 'person'} ID is a number.")
            out[key] = v
    return out


def away_minutes(v) -> int:
    """Settings' "Away after" from the page: 10, 15, 20 or 30 minutes."""
    if isinstance(v, str) and v.strip().isdigit():
        v = int(v)
    if isinstance(v, bool) or v not in config.AWAY_AFTER:
        raise ValueError("Away after is 10, 15, 20 or 30 minutes.")
    return int(v)


def api_aw_fix(body: dict) -> dict:
    """Settings' Fix: ActivityWatch's AFK timeout set to "Away after" (`minutes`, saved too, so the two agree), then
    ActivityWatch restarted (awsetup.fix). The page's session only (key_refusal); refused in a test or read-only run."""
    minutes = away_minutes(body.get("minutes"))
    s = config.load()
    res = awsetup.fix(minutes, s["activitywatch_url"])
    if res["changed"] or res["ok"]:
        s = config.update({"away_after": minutes})
    if res["changed"] and not res["ok"]:  # the file changed, the restart did not finish: worth a line in Logs
        logbook.record("activitywatch", res["message"], kind="warning", once_per=0)
    return {"result": res, "awayAfter": config.away_after(s), "activitywatch": awsetup.status(config.away_after(s))}


def _when(t: float, tz, now: float) -> str:
    """A moment as the page says it: "13:10" on the day of `now`, "Mon 17:40" on another day."""
    local = datetime.fromtimestamp(t, tz)
    return local.strftime("%H:%M") if local.date() == datetime.fromtimestamp(now, tz).date() else local.strftime("%a %H:%M")


def api_pause(q: dict) -> dict:
    """The pause (pause.py) as the page shows it. With `ask`, also whether to ask if it is still on: paused for
    Settings' "Ask about a pause after" or longer, with pause.ASK_ACTIVE minutes of work seen since it began, and not
    asked within that stretch; `ask=2` (Resume clicked) skips those two and asks only from when. `back`: when the person came back, the start of the last stretch of work the tracker saw
    (pieces closer than Away after are one stretch)."""
    s = config.load()
    tz, st, now = config.tz(s), pause.state(), time.time()
    if not st["paused"]:
        return {"paused": False}
    since = st["since"]
    out = {"paused": True, "since": since, "sinceText": _when(since, tz, now), "minutes": round((now - since) / 60)}
    if not q.get("ask"):
        return out
    after = config.advanced(s)["pause_ask"]
    out["ask"] = False
    if q.get("ask") != "2" and (not after or now - since < after * 60 or (st["asked"] and now - st["asked"] < after * 60)):
        return out  # ask=2: Resume was clicked, so the question is only from when
    found = trackers.both(since, now, s["activitywatch_url"], trackers.classifier(s["personal_patterns"]),
                          config.away_after(s))
    items = sorted((x for x in found[trackers.pick(found, "auto")] if x.get("cat") != "personal"), key=lambda x: x["s"])
    work = sum(x["e"] - x["s"] for x in items)
    out["workMinutes"] = round(work)
    if work < pause.ASK_ACTIVE:
        return out
    back, end = None, None
    for x in items:
        if back is None or x["s"] - end > config.away_after(s):
            back = x["s"]
        end = x["e"] if end is None else max(end, x["e"])
    out["ask"] = True
    if back is not None and back >= 1:
        out["back"] = since + back * 60
        out["backText"] = _when(out["back"], tz, now)
    return out


def api_pause_change(body: dict) -> dict:
    """{on: true}: pause from now. {on: false, until}: resume, the pause marked not work up to `until` (an epoch; now
    when not given), with what Undo needs. {stay: true}: the pause goes on, asked again after another stretch.
    {undo: {since, marked, was}}: the resume taken back (pause.restore)."""
    s = config.load()
    marked: dict = {}
    extra: dict = {}
    if isinstance(body.get("undo"), dict):  # Undo after a resume: the pause back as it was
        u = body["undo"]
        pause.restore(u.get("since"), u.get("marked"), u.get("was"))
    elif body.get("stay") is True:
        pause.stay()
    elif body.get("on") is True:
        pause.start()
    elif body.get("on") is False:
        until = body.get("until")
        if until is not None and (isinstance(until, bool) or not isinstance(until, (int, float))):
            raise ValueError("until: a time in seconds")
        got = pause.stop(config.tz(s), until=until)  # not paused (a second click, another window): nothing to undo
        marked = got["marked"]
        extra = {"was": got["was"], "resumedSince": got["resumedSince"]} if "resumedSince" in got else {}
    else:
        raise ValueError("on: true or false, stay: true, or undo")
    return {**api_pause({}), "marked": marked, **extra}


def api_trackers_open(body: dict) -> dict:
    """The trackers notice's button: opens ActivityWatch or MemTime, or restarts ActivityWatch (`restart`), then says
    how they stand. The page's session only; refused in a test or a read-only run."""
    tracker = body.get("tracker")
    if tracker not in ("aw", "memtime"):
        raise ValueError("tracker: aw or memtime")
    s = config.load()
    res = trackhealth.start(tracker, s["activitywatch_url"], restart=body.get("restart") is True)
    if not res["ok"]:
        logbook.record(f"tracker {tracker}", res["message"], kind="warning")
    return {"result": res}


def api_aw_login(body: dict) -> dict:
    """Whether ActivityWatch opens at login, or (`add`) adding it, through System Events (awsetup.check_login,
    add_login). The page's session only; refused in a test or a read-only run."""
    res = awsetup.add_login() if body.get("add") is True else awsetup.check_login()
    s = config.load()
    return {"result": res, "awayAfter": config.away_after(s), "activitywatch": awsetup.status(config.away_after(s))}


def _copy_path(date: str, s: dict) -> str:
    """Where the copy of one day's entries is kept (productive_rows), per person."""
    who = re.sub(r"\D", "", str(s.get("person_id") or "")) or "unknown"
    return os.path.join(config.CACHE_DIR, "days", who, f"{date}.json")


def productive_rows(d0: str, d1: str, s: dict, bulk: bool = False) -> tuple:
    """(rows, problem, source, from_copy). Every answer is kept per day, so when Productive cannot be reached
    the day view and the report show the last copy of each day, and say so, instead of nothing. bulk: a long read
    in the background, which leaves Productive room for the day's own reads."""
    first, last = datetime.strptime(d0, "%Y-%m-%d"), datetime.strptime(d1, "%Y-%m-%d")
    dates = [(first + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((last - first).days + 1)]
    rows, problem, source = productive.entries(d0, d1, s, bulk=bulk)
    if problem and source:  # a connection that failed (no connection at all is a setup step, not an error)
        logbook.record("productive", problem)
    path = lambda d: _copy_path(d, s)  # noqa: E731
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
    found = trackers.both(t0, t1, s["activitywatch_url"], trackers.classifier(s["personal_patterns"]), config.away_after(s))
    source = trackers.pick(found, q.get("source", "auto"))
    minutes = {k: round(sum(x["e"] - x["s"] for x in v)) for k, v in found.items()}
    tz = config.tz(s)
    # away time from elapsed minutes; then, on a day the clocks change, everything by the clock (each piece its real length)
    items = clock_spans(found[source], t0, t1, tz)
    now = time.time()
    until = (min(now, t1) - t0) / 60 if t0 <= now < t1 else None  # today: a pause that is still going on shows too
    gaps = clock_spans(away(found[source], config.away_after(s), until), t0, t1, tz, 2)
    for name, problem in list(trackers.PROBLEMS.items()):
        logbook.record(f"tracker {name}", problem, kind="warning")
    return {"source": source, "minutes": minutes, "items": items, "problems": dict(trackers.PROBLEMS), "away": gaps}


AWAY_LONG = 60  # minutes: a gap from Away after up to this is a pause; from this long the person was away (2026-11-07)
OFF_MIN = 180   # from this long it is time off (a night, an afternoon off), not a break: shown, never added up


def away(items: list, least: float | None = None, until: float | None = None) -> list:
    """The stretches between the day's first and last activity when nothing was on screen for at least `least`
    minutes, Settings' "Away after" (config.away_after) when not given: ActivityWatch's AFK time, MemTime's offline
    time, a computer asleep. [{s, e, off}] in minutes; off is a stretch of OFF_MIN or more. `until` (today: the
    minute it is now): away since the last activity counts too once it is that long, marked open, so a pause shows
    while it lasts and not only once the next activity closes it."""
    least = config.away_after() if least is None else least
    out, end = [], None
    for it in sorted(items, key=lambda x: x["s"]):
        if end is not None and it["s"] - end >= least:
            out.append({"s": round(end, 2), "e": round(it["s"], 2), "off": it["s"] - end >= OFF_MIN})
        end = it["e"] if end is None else max(end, it["e"])
    if until is not None and end is not None and until - end >= least:
        out.append({"s": round(end, 2), "e": round(until, 2), "off": until - end >= OFF_MIN, "open": True})
    for g in out:  # a pause (under AWAY_LONG), away (an hour or more), or off (OFF_MIN or more): drawn apart
        g["kind"] = "off" if g["off"] else "away" if g["e"] - g["s"] >= AWAY_LONG else "pause"
    return out


def api_entries(q: dict) -> dict:
    """The day's entries from Productive. copy=1: the copy kept from the last read instead, at once and with no
    request to Productive ({"copy": true}, or {"copy": false} when there is none), for the page to draw while it asks
    Productive itself (an entry changes there by other people and other tools too)."""
    s = current_settings()
    date, tz = q["date"], config.tz(s)
    if q.get("copy") == "1":
        kept = read_json(_copy_path(date, s), None)
        if not (isinstance(kept, dict) and isinstance(kept.get("rows"), list)):
            return {"entries": [], "copy": False}
        rows = [r for r in kept["rows"] if isinstance(r, dict) and r.get("date") and "minutes" in r]
        try:
            stamp = datetime.fromisoformat(str(kept.get("fetched"))).strftime("%d.%m %H:%M")
        except ValueError:
            stamp = None
        return {"entries": _entry_rows(rows, date, s), "copy": True, "fetched": stamp, "warning": None,
                "needsSetup": False}
    rows, problem, source, from_copy = productive_rows(date, date, s)
    if problem and not from_copy:
        return {"error": problem, "entries": [], "needsSetup": source is None}
    return {"entries": _entry_rows(rows, date, s), "source": source,
            "fetched": None if from_copy else datetime.now(tz).strftime("%d.%m %H:%M"),
            "warning": problem, "needsSetup": bool(problem) and source is None}


def _entry_rows(rows: list, date: str, s: dict) -> list:
    """Productive's rows of one day as the page draws them: minutes on the day's clock, in time order, the ones
    Pulsar in Claude Code wrote marked."""
    tz = config.tz(s)
    t0, _ = day_bounds(date, tz)
    out = []
    for x in rows:
        start = None if x.get("start") is None else round(clock(x["start"], t0, tz), 2)
        # its end on the clock: over the hour the clocks skip in spring it ends an hour later than start + minutes
        end = None if start is None else max(round(start + x["minutes"], 2),
                                             round(clock(x["start"] + x["minutes"] * 60, t0, tz), 2))
        out.append({**{k: v for k, v in x.items() if k not in ("start", "date")}, "s": start, "e": end})
    mine = from_claude_code(date)
    for x in out:
        if str(x.get("id") or "") in mine:
            x["fromClaudeCode"] = True
    out.sort(key=lambda x: (x["s"] is None, x["s"] or 0))
    return out


def from_claude_code(date: str) -> set:
    """The ids of the day's entries Pulsar in Claude Code wrote (the Log the day journal marks its runs), for the page
    to say so."""
    try:
        runs = dayplan.runs_for(date)
    except ValueError:  # a journal that cannot be read: the page says so elsewhere
        return set()
    return {str(x.get("id")) for r in runs for x in r.get("rows") or []
            if x.get("via") == mcptools.VIA and x.get("status") == "logged" and x.get("id")}


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
                learn.annotate(result, date, s, before, [] if problem else same_day, tz,  # elapsed minutes, as entries
                               new_task_source(date, ps))
    if not result.get("error") and clock_change(t0, t1, tz) is not None:
        # the clocks change today: rows by the clock, like the activity, each its real length (a copy: the cached
        # proposal stays in elapsed minutes, which learning and Ask Claude use)
        result = {**result, "entries": [{**e, **dict(zip(("s", "e"), page_span(e, t0, tz)))} for e in result["entries"]]}
    return result


def page_picks(answer: dict, rows: list, t0: float, t1: float, tz) -> dict:
    """Ask Claude's picks named as the page names its rows: by their start on the clock, on the day the clocks change."""
    if clock_change(t0, t1, tz) is None:
        return answer
    by_start = {pick_key(r["s"], r.get("project")): pick_key(page_span(r, t0, tz)[0], r.get("project")) for r in rows}
    return {**answer, "picks": {by_start.get(k, k): v for k, v in (answer.get("picks") or {}).items()}}


def api_likely_picks(q: dict) -> dict:
    """Ask Claude's last answer for a day, from this run or kept on disk: the page shows its picks again when the day
    is opened. Never asks Claude."""
    s = current_settings()
    answer = askclaude.latest(q["date"]) if s["ask_claude"] else None
    if not answer or not answer.get("picks"):
        return {"picks": {}}
    tz = config.tz(s)
    t0, t1 = day_bounds(q["date"], tz)
    rows = []
    if clock_change(t0, t1, tz) is not None:
        rows = likely.proposal(q["date"], config.timezone_name(s), today(tz), False, s, quick=True).get("entries") or []
    return {"picks": page_picks(answer, rows, t0, t1, tz)["picks"], "kept": bool(answer.get("kept"))}


def api_ask_claude(body: dict) -> dict:
    """The Ask Claude button: a task for each Likely row of the day (askclaude.py). Only when switched on in Settings;
    a POST, because it runs Claude and costs the person's usage."""
    s = current_settings()
    date = str(body.get("date") or "")
    if not DATE.match(date):
        raise ValueError("date=YYYY-MM-DD")
    if not s["ask_claude"]:
        raise ValueError("Claude Code is off. Switch it on in Settings > Claude.")
    tz = config.tz(s)
    proposal = likely.proposal(date, config.timezone_name(s), today(tz), False, s)
    if proposal.get("error"):
        raise ValueError(proposal["error"])
    d0, _ = askclaude.history_span(date)
    before, problem = history_rows(date, s, days=learn.LEARN_DAYS)  # one read, kept an hour, shared with Likely
    if problem:  # Claude picks from the tasks you logged to, and its answer is kept: with none, it is not asked
        why = f"Your earlier entries could not be read from Productive, so Claude was not asked: {problem}"
        logbook.record("ask claude", why, kind="warning", once_per=600)
        return {"error": why, "picks": {}, "cost": 0}
    # each row's likely tasks (learned from the days before, never from this day's own entries): whose notes Claude
    # sees as examples of how this person writes them. A copy, so the cached proposal stays as it is.
    rows = [dict(e) for e in proposal.get("entries") or []]
    learn.annotate({**proposal, "entries": rows}, date, s, before, [], tz, new_task_source(date, s))  # as Likely does
    t0, t1 = day_bounds(date, tz)
    try:
        answer = askclaude.ask(date, rows, t0, [r for r in before if r.get("date", "") >= d0],
                               model=claudeuses.model("ask", s), history=before, tz=tz)
        return page_picks(answer, rows, t0, t1, tz)
    except askclaude.AskError as exc:
        logbook.record("ask claude", str(exc), kind="warning")
        return {"error": str(exc), "picks": {}, "cost": exc.cost}


def api_likely_note(body: dict) -> dict:
    """The log dialog's note for a Likely block, or for a stretch picked another way (the yellow rail, a drag), or for a
    row of Log the day: from the sessions and commits of the project's Likely rows that overlap s to e, whole rows (the
    background ones when no session of it was in front), with askclaude.note and the model chosen for "note", Sonnet by
    default.
    Only when the Claude Code switch is on; a POST, because it runs Claude."""
    s = current_settings()
    date, project = str(body.get("date") or ""), str(body.get("project") or "")
    if not DATE.match(date):
        raise ValueError("date=YYYY-MM-DD")
    try:
        a, b = float(body.get("s")), float(body.get("e"))
    except (TypeError, ValueError):
        raise ValueError("s and e are minutes of the day") from None
    if not s["ask_claude"]:
        raise ValueError("Claude Code is off. Switch it on in Settings > Claude.")
    tz = config.tz(s)
    t0 = day_bounds(date, tz)[0]
    proposal = likely.proposal(date, config.timezone_name(s), today(tz), False, s)
    # s and e are the page's: by the clock on a day the clocks change, so the rows are compared as the page drew them
    def over(key):
        return [dict(r) for r in proposal.get(key) or [] if r.get("project") == project
                and min(page_span(r, t0, tz)[1], b) - max(page_span(r, t0, tz)[0], a) > 0]
    # a session in front first; else one whose agent ran in the background (Log the day's shared rows)
    rows = over("entries") or over("background")
    if not rows:
        return {"note": "", "why": "no Likely work at that time"}
    if body.get("reuse"):  # Log the day: a note already drafted for this work, as the Likely column has it
        kept = askclaude.kept_note(date, project, min(r["s"] for r in rows), max(r["e"] for r in rows))
        if kept:
            return {"note": kept, "why": "", "cost": 0, "kept": True}
    before, problem = history_rows(date, s, days=learn.LEARN_DAYS)
    if not problem:
        learn.annotate({**proposal, "entries": rows}, date, s, before, [], tz, new_task_source(date, s))
    lead = max(rows, key=lambda r: r["e"] - r["s"])
    row = {"s": min(r["s"] for r in rows), "e": max(r["e"] for r in rows), "project": project,
           "titles": sorted({t for r in rows for t in r.get("titles") or []}),
           "commits": [c for r in sorted(rows, key=lambda r: r["s"]) for c in r.get("commits") or []],
           "learned": lead.get("learned") or [], "tasks": lead.get("tasks") or []}
    try:
        return askclaude.note(date, row, t0, [] if problem else before, tz=tz, model=claudeuses.model("note", s))
    except askclaude.AskError as exc:
        logbook.record("ask claude", str(exc), kind="warning")
        return {"note": "", "error": str(exc)}


def api_app_reinstall() -> dict:
    """"Reinstall Pulsar": the newer app the latest release describes, in place of this one (appupdate.py). Only on
    the person's click; when this account cannot change the app's folder, the install line to run instead."""
    try:
        return {"app": appupdate.reinstall(UPDATER.state.get("app") or {})}
    except appupdate.NotWritable as exc:
        return {"error": str(exc), "installLine": appupdate.install_line()}
    except Exception as exc:  # a download, a check or the swap failed: nothing was changed, say why
        logbook.record("updater", f"Reinstalling Pulsar.app failed: {exc}")
        return {"error": str(exc)}


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
        sources = trackers.both(bounds[0][0], bounds[-1][1], s["activitywatch_url"], trackers.classifier(s["personal_patterns"]),
                                config.away_after(s))
        rows, problem, source, from_copy = rows_job.result()
    not_work = {d: pause.elapsed(d, nw, tz, bounds[i][0]) for i, d in enumerate(dates) if (nw := pause.not_work(d, tz))}
    data = report.build(dates, bounds, sources, rows, today(tz), tz=tz, not_work=not_work)
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
    if (body.get("token") or body.get("remove_token")) and config.read_only() and config._keychain():
        raise ValueError("A read-only run does not change the token saved in the keychain: it is the real Pulsar's too.")
    changes = validate(body)
    token = str(body.get("token") or "").strip()
    if token and not config.TOKEN_RE.match(token):  # check before anything is removed or replaced
        raise ValueError("That does not look like a Productive API token (letters, digits, - and _ only).")
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
    changed = lambda k: k in changes and changes[k] != before[k]  # noqa: E731
    if changed("ask_claude") or (s["ask_claude"] and (body.get("relink") or changed("claude_ask_productive"))):
        link = claudelink.connect(PORT[0], s) if s["ask_claude"] else claudelink.disconnect(s)
        config.update({"claude_rules_added": link["rulesAdded"], "claude_denies_added": link["deniesAdded"]})
        if not link["ok"]:
            logbook.record("claude code", link["message"], kind="warning")
    tidied = claudelink.tidy(current_settings())  # the Productive ask rules go unless chosen (claudelink.tidy)
    if tidied:
        config.update({"claude_rules_added": tidied["rulesAdded"]})
    s = current_settings()
    aw = sync_activitywatch(s)
    # Productive is asked again only when the connection changed: saving a rule or a time zone needs no test
    return {"config": public_config(s), "test": test_productive(s) if connection else None, "link": link,
            "activitywatch": aw}


def sync_activitywatch(s: dict):
    """One away time everywhere (2026-11-10): when ActivityWatch's AFK timeout is not Away after, Save sets it and
    restarts ActivityWatch (awsetup.fix). Not from Pulsar Preview, whose settings are its own while ActivityWatch is the
    real Pulsar's too; not in a test or read-only run. None when nothing was to change."""
    minutes = config.away_after(s)
    st = awsetup.status(minutes)
    if not st["installed"] or st["problem"] or not st["fromFile"] or not st["differs"] or config.is_preview() \
            or awsetup.blocked():
        return None  # no file yet: ActivityWatch's default stands, and Pulsar never makes its file
    res = awsetup.fix(minutes, s["activitywatch_url"])
    if res["changed"] and not res["ok"]:
        logbook.record("activitywatch", res["message"], kind="warning", once_per=0)
    return res


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
        rows, history_error, _, _ = productive_rows(d0, d1, s)  # a saved copy, or nothing, when Productive fails
        problem = ""
        try:
            mine = productive.assigned_tasks(s)
        except Exception as exc:  # the dialog still offers the rest, and says why this group is empty
            mine = []
            problem = f"Your assigned tasks could not be read: {exc}"
            logbook.record("productive", problem, kind="warning", once_per=600)
        lists = suggest_lists(rows, mine, today(config.tz(s)))
        # kept 10 minutes; one with a part that could not be read only a minute, so the next dialog tries again
        _recent[key] = (time.time() + (60 if problem or history_error else 600),
                        {**lists, **({"assignedError": problem} if problem else {}),
                         **({"historyError": f"Your last 30 days could not be read: {history_error}"}
                            if history_error else {})})
    return _recent[key][1]


def suggest_lists(rows: list, mine: list, today_iso: str) -> dict:
    """{"recent", "assigned"}: the tasks of `rows` (your entries) newest first, each with the service used last on it,
    and the tasks of `mine` (assigned to you) not among them, the urgent ones first as of `today_iso`. api_suggest's
    lists, and the self-check's for a past day (dayplan.gather, blind)."""
    by_task: dict = {}
    for r in sorted(rows, key=lambda r: (r["date"], r.get("start") or 0)):
        if not r.get("taskId"):
            continue
        t = by_task.setdefault(r["taskId"], {"taskId": r["taskId"], "title": r.get("task") or "",
                                             "project": r.get("project") or "", "minutes": 0, "count": 0})
        t.update({"serviceId": r.get("serviceId"), "service": r.get("service") or "", "budget": r.get("budget") or "",
                  "billable": r.get("billable"), "last": r["date"]})
        t["minutes"] += r["minutes"]
        t["count"] += 1
    recent = sorted(by_task.values(), key=lambda t: (t["last"], t["count"]), reverse=True)
    known = {t["taskId"]: t for t in mine}
    for t in recent:  # what Productive says of the tasks you logged to: due date, status, blocked
        t.update({k: known[t["taskId"]][k] for k in ("due", "status", "blocked") if k in known[t["taskId"]]}
                 if t["taskId"] in known else {})
    return {"recent": recent, "assigned": urgent_first([t for t in mine if t["taskId"] not in by_task], today_iso)}


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
REPEAT_STRICT = (5, 0.7)  # Advanced > Daily meeting cards: Strict (days, share)
REPEAT_STALE = 10   # days: a repeat not seen this long has stopped (a meeting that was cancelled)
REPEAT_ENTRIES = 2  # entries of the task in a day, at most, for that day to count: a meeting is one entry, or two
CALL_WINDOW = 75    # minutes around the usual time to look for the call (wide enough for an hour's clock change)
CALL_GAP = 20       # minutes: the call goes on while its window shows again within this; people work during calls
_repeats = config.Newest(8)  # (date, person, Advanced > Daily meeting cards) -> (when, patterns)


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
    # a browser tab of a call: Zoom's web client, or a join link by the pattern Log the day keys meetings with
    # (dayplan.JOIN_KEY: one definition, rule order 2.2; /w/ and /my/ rooms and four-letter Meet codes were keys
    # and no call), or a Meet call by its title
    return bool(dayplan.JOIN_KEY.search(where) or re.search(r"zoom\.us/wc/", where)
                or re.match(r"Meet\s*[-–]\s*[a-z]{3,4}-[a-z]{4}-[a-z]{3,4}", title) or "Google Meet" in title)


def repeating(rows: list, tz, date: str, strict: bool = False) -> list:
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
        # a meeting is one entry a day of about its usual length; work that only started at that time on three days is
        # not (2026-11-11: a feature's evening work, 62, 3 and 56 minutes long, came back as a 15-minute call; a task
        # logged 9 to 15 times a day had started at 19:02 on three of them)
        entries: dict = {}
        for x in xs:
            entries[x[2]["date"]] = entries.get(x[2]["date"], 0) + 1
        per_day = {d: x for d, x in per_day.items() if entries[d] <= REPEAT_ENTRIES}
        if per_day:
            spread = sorted(x[2]["minutes"] for x in per_day.values())
            usual = spread[len(spread) // 2]
            per_day = {d: x for d, x in per_day.items() if usual / 2 <= x[2]["minutes"] <= usual * 2}
        logged_days = {x[2]["date"] for x in xs}
        min_days, share = REPEAT_STRICT if strict else (REPEAT_MIN_DAYS, REPEAT_SHARE)
        if len(per_day) < max(min_days, share * len(logged_days)):
            continue
        last = max(per_day)
        if (datetime.strptime(date, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days > REPEAT_STALE:
            continue
        starts = sorted(x[0] for x in per_day.values())
        lengths = sorted(x[2]["minutes"] for x in per_day.values())
        notes: dict = {}
        for d in sorted(per_day):
            n = (per_day[d][2].get("note") or "").strip()
            if n and askclaude.english(n):  # notes in English only: an old note in Ukrainian is not offered again
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
    cards = config.advanced(s)["meeting_cards"]
    if cards == "off":  # Advanced > Daily meeting cards: Off (Log the day takes its repeats from here too)
        return {"repeats": []}
    date, tz = q["date"], config.tz(s)
    key = (date, s.get("person_id"), cards)
    if key not in _repeats or time.time() - _repeats[key][0] > 3600:
        rows, problem = history_rows(date, s)
        if problem:
            return {"repeats": [], "error": problem}
        _repeats[key] = (time.time(), repeating(rows, tz, date, strict=cards == "strict"))
    t0, t1 = day_bounds(date, tz)
    found = trackers.both(t0, t1, s["activitywatch_url"], trackers.classifier(s["personal_patterns"]), config.away_after(s))
    items = found[trackers.pick(found, q.get("source", "auto"))]
    items = clock_spans(items, t0, t1, tz)  # by the clock on a day the clocks change, like the usual times
    return {"repeats": repeat_suggestions(_repeats[key][1], items, datetime.strptime(date, "%Y-%m-%d").weekday())}


def day_repeats(rows: list, items: list, date: str, s: dict) -> list:
    """The repeats seen in a day's windows (`items`, by the clock, as api_activity gives them), learned from `rows`,
    your entries before the day: api_repeats from what was read already (the self-check: dayplan.gather, blind)."""
    cards = config.advanced(s)["meeting_cards"]
    if cards == "off":
        return []
    patterns = repeating(rows, config.tz(s), date, strict=cards == "strict")
    return repeat_suggestions(patterns, items, datetime.strptime(date, "%Y-%m-%d").weekday())


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
    and the Likely rows learn from, less the entries marked not an example (daymarks.no_learn)."""
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
            # a saved copy (Productive failed; some days may be missing) is kept a minute, not an hour: then asked again
            kept = (time.time() - (HISTORY_FOR - 60 if from_copy else 0), rows)
            with _rows_lock:
                _rows_before[key] = kept
    skip = daymarks.no_learn()  # entries marked not an example teach nothing (daymarks.py)
    return [r for r in kept[1] if r.get("date", "") >= first and str(r.get("id") or "") not in skip], None


_fresh: dict = {}  # (date, person) -> (when, tasks)
_fresh_lock = threading.Lock()


def new_task_source(date: str, s: dict):
    """The tasks worked on in the NEW_DAYS before `date` (productive.recent_tasks), for the tasks you never logged to
    (learn.new_tasks); kept ten minutes, one read at a time. None when they cannot be read: a hint, never an error."""
    key = (date, s.get("person_id"))
    with _fresh_lock:
        kept = _fresh.get(key)
        if kept and time.time() - kept[0] < 600:
            return kept[1]
        since = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=learn.NEW_DAYS)).strftime("%Y-%m-%d")
        try:
            got = productive.recent_tasks(since, s)
        except productive.ProductiveError as exc:  # not connected, refused, unreachable: said, and the rows go without
            logbook.record("learn", f"Recent tasks could not be read: {exc}", kind="warning", once_per=600)
            return None
        except Exception as exc:  # anything else is a bug: said as one
            logbook.record("learn", f"Recent tasks could not be read, because of a fault in Pulsar ({type(exc).__name__}: "
                           f"{exc}).", kind="warning", once_per=600)
            return None
        _fresh[key] = (time.time(), got)
        for old in sorted(_fresh, key=lambda k: _fresh[k][0])[:-4]:
            del _fresh[old]
        return got


def history_in_background(date: str, s: dict) -> None:
    """Start reading the history for `date`, unless it is being read already."""
    with _rows_lock:
        reading = _reading.get((date, s.get("person_id")))
    if reading and reading.locked():
        return

    def run():
        try:
            history_rows(date, s, days=learn.LEARN_DAYS)
            new_task_source(date, s)  # read with the history, so the second ask has both
        except Exception as exc:  # a hint for Likely: the page asks again and gets the rows without learned tasks
            logbook.record("learn", f"Your earlier entries could not be read from Productive ({type(exc).__name__}: {exc}).",
                           kind="warning")
    threading.Thread(target=run, daemon=True, name="pulsar-history").start()


# ---- a meeting's task --------------------------------------------------------------------------------

WORD = re.compile(r"#?[^\W_]{3,}|#\d+")
GENERIC_WORDS = {"meeting", "meetings", "sync", "call", "calls", "weekly", "daily", "monthly", "check", "catch", "chat",
                 "with", "and", "the", "for", "review", "update", "updates", "team", "internal", "zoom", "google", "meet",
                 "standup", "stand", "about", "from", "into", "project", "projects", "session", "discussion", "kick",
                 "kickoff", "follow", "quick", "time", "foster", "interactive", "ongoing", "support"}


def words_of(text: str, skip: set) -> set:
    """The telling words of a meeting's title or a task's name, each by its stem (learn._stem: "upgrades" and "upgrade",
    "students" and "student" are one word; 2026-11-09 they were not, and another client's "D11 Upgrade" won)."""
    return {learn._stem(w.lstrip("#")) if not w.startswith("#") else w for w in WORD.findall((text or "").lower())
            if w not in GENERIC_WORDS and w not in skip}


def meeting_tasks(meeting: dict, past: list, rows: list, offered: list, skip: set = frozenset()) -> list:
    """Tasks for a meeting, most likely first. First the tasks you logged during the earlier meetings of the same
    series (or the same title), then your tasks that share a word with its title (people's names you go by, and words
    every meeting has, do not count). Each with why, in words; a series' task also with `lastTitle`, the title of the
    latest earlier meeting you logged it in (a series renamed since starts unticked in Log the day)."""
    title = (meeting.get("title") or "").strip()
    norm = re.sub(r"\s+", " ", title.lower())
    same = [p for p in past if not p.get("declined")
            and ((meeting.get("uid") and p.get("uid") == meeting["uid"]) or re.sub(r"\s+", " ", p.get("title", "").lower()) == norm)]
    counts: dict = {}
    seen: dict = {}
    last: dict = {}  # task -> (start, title) of the latest earlier meeting that voted for it
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
            if p["s"] >= last.get(best, (float("-inf"), ""))[0]:
                last[best] = (p["s"], str(p.get("title") or ""))
    out = [{"taskId": t, "title": seen[t].get("task") or "", "project": seen[t].get("project") or "",
            "why": f"logged in {n} of the last {len(same)} of these meetings", "n": n, "of": len(same), "lastTitle": last[t][1]}
           for t, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    words = words_of(title, skip)
    # the projects of the work logged within an hour of it: a meeting about that client's work names its tasks
    around = {r.get("project") for r in rows if r.get("start") is not None and r.get("project")
              and r["start"] < meeting.get("e", 0) + 3600 and r["start"] + r["minutes"] * 60 > meeting.get("s", 0) - 3600}
    if words:
        scored = []
        for t in offered:
            shared = words & words_of(f"{t.get('title', '')} {t.get('project', '')}", skip)
            if shared:
                meetingish = "meeting" in (t.get("title") or "").lower()
                scored.append((len(shared) + (0.5 if meetingish else 0) + (1 if t.get("project") in around else 0),
                               t, shared))
        have = {o["taskId"] for o in out}
        for _, t, shared in sorted(scored, key=lambda x: -x[0]):
            if t["taskId"] not in have and len(out) < 5:
                have.add(t["taskId"])
                said = sorted({w for w in WORD.findall(title.lower()) if words_of(w, skip) & shared})  # as written
                out.append({"taskId": t["taskId"], "title": t.get("title") or "", "project": t.get("project") or "",
                            "why": "the title says " + ", ".join(f"“{w}”" for w in said or sorted(shared))})
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
    except Exception as exc:  # fewer tasks offered for the meeting: said in the log
        logbook.record("meetings", "Your recent and assigned tasks could not be read, so fewer tasks are offered for "
                       "meetings.", kind="warning", detail=f"{type(exc).__name__}: {exc}", once_per=3600)
        offered = []
    skip = set(WORD.findall(str(s.get("person_name") or "").lower()))  # your own name is in many task titles
    return {"tasks": meeting_tasks(meeting, past, rows, offered, skip)}


def api_search(q: dict) -> dict:
    """Every task whose title holds the words, yours first (rank_search). `date` (optional) is the day being logged:
    "yours" means logged to in the 30 days before it, or assigned to you (api_suggest, kept ten minutes)."""
    text = (q.get("q") or "").strip()
    if len(text) < 2:
        return {"tasks": []}
    s = current_settings()
    found = productive.search_tasks(text, s)
    try:
        sug = api_suggest({"date": q["date"] if DATE.match(q.get("date") or "") else today(config.tz(s))})
    except Exception as exc:  # the results still come, in Productive's order
        logbook.record("productive", f"Search could not put your tasks first: {exc}", kind="warning", once_per=600)
        sug = {}
    return {"tasks": rank_search(found, text, sug.get("recent") or [], sug.get("assigned") or [])}


SEARCH_MAX = 30


def rank_search(found: list, text: str, recent: list, assigned: list) -> list:
    """Search results in the order a person looks for them: tasks you logged to lately (most recent first), then open
    tasks assigned to you, then everyone else's, closed tasks last; otherwise Productive's order. Your own tasks whose
    title or project holds every word (productive.has_words) are added when Productive's answer left them out. Each of yours says so: "yours" is
    "logged" or "assigned"."""
    words = productive.search_words(text) or [text.strip()]
    logged = {t["taskId"]: i for i, t in enumerate(recent) if t.get("taskId")}
    mine = {t["taskId"] for t in assigned if t.get("taskId")}
    extra = [t for t in [*recent, *assigned] if productive.has_words(words, t.get("title"), t.get("project"))]
    out, seen = [], set()
    for t in [*found, *extra]:
        tid = t.get("taskId")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        yours = "logged" if tid in logged else "assigned" if tid in mine else ""
        out.append({**t, **({"yours": yours} if yours else {})})

    def rank(t):
        closed = (t.get("title") or "").endswith(" (closed)")
        tier = 3 if closed else 0 if t.get("yours") == "logged" else 1 if t.get("yours") == "assigned" else 2
        return tier, logged.get(t["taskId"], 0) if tier == 0 else 0
    return sorted(out, key=rank)[:SEARCH_MAX]  # sorted() keeps Productive's order within a tier


# ---- a new task from the log dialog (newtask.py, web/js/newtask.js) -----------------------------------

_projects = config.Newest(4)  # person -> (until, active projects)


def api_task_projects(q: dict) -> dict:
    """The projects a new task can go to: every active one you can see, those you logged to in the last 30 days
    first (most minutes first), then by name; and `pick`, the likeliest (newtask.guess_project) from the picked task's
    project (`hint`) or the work's repository (`repo`). Read only; the list is kept ten minutes."""
    s = current_settings()
    key = s.get("person_id")
    try:
        if key not in _projects or time.time() > _projects[key][0]:
            _projects[key] = (time.time() + 600, productive.active_projects(s))
    except productive.ProductiveError as exc:
        return {"projects": [], "error": f"Productive did not list your projects: {exc}"}
    lately: dict = {}
    for t in api_suggest({"date": q["date"]}).get("recent") or []:  # kept ten minutes, the log dialog's own list
        lately[t.get("project")] = lately.get(t.get("project"), 0) + (t.get("minutes") or 0)
    projects = sorted(({**p, "lately": round(lately.get(p["name"], 0))} for p in _projects[key][1]),
                      key=lambda p: (-p["lately"], p["name"].lower()))
    return {"projects": projects, "pick": newtask.guess_project(projects, str(q.get("hint") or "")[:200],
                                                                str(q.get("repo") or "")[:200])}


def api_task_places(q: dict) -> dict:
    """Where in a project a new task can go (newtask.places). Read only."""
    if not is_id(q.get("project") or ""):
        raise ValueError("project=<id>&date=YYYY-MM-DD")
    s = current_settings()
    try:
        found = productive.project_places(q["project"], s)
    except productive.ProductiveError as exc:
        return {"parents": [], "lists": [], "pick": "",
                "error": f"Productive did not say where a task can go in this project: {exc}"}
    return newtask.places(found, q["date"], str(s.get("person_id") or ""))


_statuses = config.Newest(30)       # project -> (until, its workflow's statuses)
_task_services = config.Newest(30)  # (project, date) -> (until, the services that take time then)


def api_task_fields(q: dict) -> dict:
    """The status, estimate and service to offer for a new task, each with why (newtask.propose), and the project's
    statuses and services to pick from. `place` is parent:<id>, list:<id> or none; `title`, the parent's title as the
    form shows it, is used only when the parent cannot be read. Read only: the statuses and services are kept 10
    minutes, the parent 5 (task_details), the project's estimates a day (flags.project_share). A read that fails leaves
    its proposal empty, with the reason, never an error."""
    project, place, date = q.get("project") or "", str(q.get("place") or ""), q["date"]
    kind, _, pid = place.partition(":")
    if not is_id(project) or kind not in ("parent", "list", "none") or (kind != "none" and not is_id(pid)) or \
            (kind == "none" and pid):
        raise ValueError("project=<id>&place=parent:<id>|list:<id>|none&date=YYYY-MM-DD")
    s = current_settings()

    def kept(cache, key, read):
        if key not in cache or time.time() > cache[key][0]:
            cache[key] = (time.time() + 600, read())
        return cache[key][1]

    def parent():
        got = productive.task_details([pid], s).get(pid)
        return got or {"title": str(q.get("title") or "")[:200], "unread": True}

    reads = {"statuses": lambda: kept(_statuses, project, lambda: productive.workflow_statuses(project, s)),
             "services": lambda: kept(_task_services, (project, date), lambda: productive.project_services(project, date, s)),
             "sizes": lambda: (flags.project_share(project, s, sizes=True) or {}).get("sizes"),
             "parent": parent if kind == "parent" else (lambda: None)}

    def one(name):
        try:
            return name, reads[name]()
        except Exception as exc:  # a proposal left empty, said so; the form still opens
            what = {"statuses": "the project's statuses", "services": "the project's services",
                    "sizes": "the estimates of the project's tasks", "parent": "the parent task"}.get(name, name)
            logbook.record("new task", f"Could not read {what} to propose: {exc}", kind="warning", once_per=600)
            return name, {"title": str(q.get("title") or "")[:200], "unread": True} if name == "parent" else None
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(3) as pool:  # one of Claude Code's four connection slots stays free
        got = dict(pool.map(one, reads))
    return newtask.propose(got["statuses"], kind, got["parent"], got["services"], got["sizes"], date)


def _work_rows(date: str, a: float, b: float, s: dict, tz) -> tuple:
    """(the Likely rows of the project with most minutes in [a, b) that overlap it, that project): whole rows, as the
    session note takes them. a and b are the page's minutes (by the clock on the day the clocks change)."""
    if not likely.enabled(s):
        return [], ""
    t0 = day_bounds(date, tz)[0]
    proposal = likely.proposal(date, config.timezone_name(s), today(tz), False, s)
    by: dict = {}
    for r in proposal.get("entries") or []:
        x, y = page_span(r, t0, tz)
        m = min(y, b) - max(x, a)
        if m > 0 and r.get("project"):
            by.setdefault(r["project"], [0, []])
            by[r["project"]][0] += m
            by[r["project"]][1].append(dict(r))
    if not by:
        return [], ""
    project = max(by, key=lambda p: by[p][0])
    return by[project][1], project


def api_task_draft(body: dict) -> dict:
    """A title and description for a new task, from the work done in that time (newtask.py): made on this Mac, or with
    "claude": true (the Claude Code switch on) by Claude, with the model chosen for "task". A POST, because Claude may
    run. `meeting` (the page sends it only when the time was a meeting) is used when no work was seen."""
    s = current_settings()
    date = str(body.get("date") or "")
    if not DATE.match(date):
        raise ValueError("date=YYYY-MM-DD")
    try:
        a, b = float(body.get("s")), float(body.get("e"))
    except (TypeError, ValueError):
        raise ValueError("s and e are minutes of the day") from None
    tz = config.tz(s)
    rows, project = _work_rows(date, a, b, s, tz)
    if not body.get("claude"):
        return {**newtask.local_draft(rows, str(body.get("meeting") or "")[:200]), "repo": project}
    if not s["ask_claude"]:
        raise ValueError("Claude Code is off. Switch it on in Settings > Claude.")
    if not rows:
        return {"title": "", "description": "", "why": "no Likely work at that time"}
    before, problem = history_rows(date, s, days=learn.LEARN_DAYS)
    try:
        return newtask.claude_draft(date, rows, day_bounds(date, tz)[0], [] if problem else before,
                                    str(body.get("projectName") or "")[:200], tz=tz, model=claudeuses.model("task", s))
    except askclaude.AskError as exc:
        logbook.record("ask claude", str(exc), kind="warning")
        return {"title": "", "description": "", "error": str(exc)}


_task_lock = threading.Lock()
_just_created: dict = {}  # (project, title) -> (until when a second create is refused, why)
JUST_CREATED = 60       # seconds: created, so a second click would make it twice
MAYBE_CREATED = 600     # seconds: Productive's answer was unclear, so it may exist; time to search for it first


def api_task_create(body: dict) -> dict:
    """One new task in Productive, assigned to you, only from a click in the page (the key alone is refused,
    key_refusal): {"task": {taskId, title, project, projectId}}. With `statusId` (one of the project's open statuses)
    and `estimate` (whole minutes), never a service (productive.create_task). The same title in the same project
    is refused for a minute after it was created, and for ten minutes after an unclear answer (a timeout, a server
    error, a reply that is not Productive's success sentence): the task may exist then, and a second click would
    create it twice. The page then says to search for it first."""
    s = current_settings()
    project, parent, task_list, status = (str(body.get(k) or "") for k in ("projectId", "parentId", "taskListId", "statusId"))
    title = re.sub(r"\s+", " ", str(body.get("title") or "")).strip()
    description = str(body.get("description") or "").strip()
    estimate = body.get("estimate")
    if not is_id(project):
        raise ValueError("Pick a project.")
    if (parent and not is_id(parent)) or (task_list and not is_id(task_list)) or (parent and task_list):
        raise ValueError("Pick where in the project the task goes.")
    known = _statuses.get(project)
    if status and (not is_id(status) or (known and status not in {x["id"] for x in known[1] if x["category"] in ("1", "2")})):
        raise ValueError("Pick one of the project's statuses.")
    if estimate in (None, ""):
        estimate = None
    elif isinstance(estimate, bool) or not isinstance(estimate, int) or not 1 <= estimate <= productive.ESTIMATE_MAX:
        raise ValueError(f"The estimate must be whole minutes, from 1 to {productive.ESTIMATE_MAX // 60} hours.")
    if not title:
        raise ValueError("Write the task's title.")
    if len(title) > newtask.TITLE_LIMIT:
        raise ValueError(f"Productive takes titles of up to {newtask.TITLE_LIMIT} characters; this one has {len(title)}.")
    if len(description) > newtask.DESC_LIMIT:
        raise ValueError(f"The description is too long ({len(description)} characters; at most {newtask.DESC_LIMIT}).")
    if any(ord(c) < 32 and c not in "\n\t" for c in title + description):
        raise ValueError("The title or description has a character nobody sees; type it again.")
    if config.writes_blocked():  # refused here as well as in productive.https: nothing is sent
        raise ValueError(config.writes_blocked())
    key = (project, title.lower())
    with _task_lock:  # one at a time: two quick clicks cannot both pass the check below
        until, why = _just_created.get(key, (0, ""))
        if time.time() < until:
            raise ValueError(why)
        try:
            created = productive.create_task({"project_id": project, "parent_id": parent, "task_list_id": task_list,
                                              "title": title, "description": description, "status_id": status,
                                              "estimate": estimate}, s)
        except Exception as exc:
            if isinstance(exc, productive.ProductiveUnclear) or not isinstance(exc, productive.ProductiveError):
                _just_created[key] = (time.time() + MAYBE_CREATED, "Productive's answer a moment ago did not say "
                                      "whether this task was created, so it may exist. Search for it in the Log dialog's "
                                      "task search before creating it again.")
            raise
        _just_created[key] = (time.time() + JUST_CREATED, "This task was created a moment ago. Search for it in the "
                              "Log dialog's task search before creating it again.")
    _recent.clear()  # the dialog's lists read Productive again
    name = next((p["name"] for _, ps in list(_projects.values()) for p in ps if p["id"] == project), "")
    return {"task": {"taskId": created["taskId"], "title": title, "project": name, "projectId": project}}


def api_services(q: dict) -> dict:
    """The services that take time on that date; with flags=1 (the page), the task's ticket flags too (flags.py), with
    minutes=N the time about to be logged (over the estimate)."""
    if not is_id(q.get("task") or ""):
        raise ValueError("task=<id>&date=YYYY-MM-DD")
    s = current_settings()
    got = productive.bookable_services(q["task"], q["date"], s)
    if q.get("flags") == "1":
        try:
            minutes = max(0, min(1440, int(q.get("minutes") or 0)))
        except ValueError:
            minutes = 0
        got["flags"] = flags.for_task(got.get("task"), got.get("services"), q["date"], s, minutes=minutes)
    return got


def too_short(minutes: int) -> str:
    """Why an entry of `minutes` is not written, or "": none is shorter than Log the day's shortest (dayplan.MIN_ENTRY),
    whichever way it comes (the dialog, a dragged edge, Claude Code). 2026-11-11: project managers found entries of 2 to
    9 minutes in their projects. Only Undo may put a shorter entry back as it was ("restore")."""
    least = dayplan.MIN_ENTRY
    return (f"An entry is at least {least} minutes: make it {least}, or add these minutes to the entry of the same "
            f"task before or after it.") if minutes < least else ""


def api_log(body: dict, via: str = "") -> dict:
    """One time entry (_api_log). A Log the day row Pulsar refuses itself, before anything is sent, also goes in Logs:
    its reason was only in the open window, so once that closed nothing told why (2026-11-12: every part of a split
    was refused for its row name, and the cause could not be found afterwards)."""
    try:
        return _api_log(body, via)
    except ValueError as exc:
        if body.get("run") is not None and not body.get("dry"):
            logbook.record("log the day", f"Not sent: {str(body.get('date') or '')[:10]} {str(body.get('start') or '')[:5]}, "
                           f"{body.get('minutes')} min, task {str(body.get('taskId') or '')[:15]}, row "
                           f"{str(body.get('row') or '')[:40]!r}: {exc}", kind="warning")
        raise


def _api_log(body: dict, via: str = "") -> dict:
    """One time entry, written from a click in the page, or from Pulsar in Claude Code (mcptools.pulsar_log, which
    passes via="claude-code": its rows say so in the journal). Re-reads the day first: other sessions log too, and an
    entry on top of another one is shown to you before anything is written."""
    s = current_settings()
    date, start = str(body.get("date") or ""), str(body.get("start") or "")
    minutes, task_id, service_id = body.get("minutes"), str(body.get("taskId") or ""), str(body.get("serviceId") or "")
    if not (is_id(task_id) and is_id(service_id)):
        raise ValueError("Pick a task and a service.")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 24 * 60:
        raise ValueError("Minutes must be a whole number from 1 to 1440.")
    if too_short(minutes) and not body.get("restore"):
        raise ValueError(too_short(minutes))
    m = re.fullmatch(r"([01][0-9]|2[0-3]):([0-5][0-9])", start)
    if not (DATE.match(date) and m):
        raise ValueError("Pick a date and a start time (HH:MM).")
    tz = config.tz(s)
    begin = datetime.strptime(f"{date} {start}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)  # raises on 2026-02-30
    _real_time(begin, tz)
    a, b = begin.timestamp(), begin.timestamp() + minutes * 60
    if b > day_bounds(date, tz)[1]:
        raise ValueError("An entry cannot run past midnight; log the part after it on the next day.")
    if body.get("pieces") is not None:  # the dialog: this time, as the pieces around what is logged in it (_log_pieces)
        if body.get("dry"):
            return _log_pieces(body, s, date, a, b, task_id, service_id, tz)
        with _log_lock:
            return _log_pieces(body, s, date, a, b, task_id, service_id, tz)
    # Log the day sends each row with its run and row names: the journal records it before the write, and refuses a
    # row that run (or any run of that day) already sent, so a restart or a retry never writes it twice
    journal = None
    if body.get("run") is not None or body.get("row") is not None:
        proposed = dayplan.proposed_fields(body.get("proposed"))  # as first planned: the self-check (selfcheck.py)
        journal = {"run": str(body.get("run") or ""), "row": str(body.get("row") or ""),
                   "entry": {"start": start, "minutes": minutes, "taskId": task_id, "serviceId": service_id},
                   **({"via": via} if via else {}), **({"proposed": proposed} if proposed else {})}
    if body.get("dry"):
        return _log_checked(body, s, date, begin, minutes, task_id, service_id, tz, a, b, journal)
    key = (date, start, minutes, task_id, service_id)
    with _log_lock:  # one write at a time: two tabs, or two quick clicks, cannot both pass the overlap check
        if time.time() - _just_logged.get(key, 0) < 60:
            raise ValueError("This entry was logged a moment ago. Check the Logged column before logging it again.")
        result = _log_checked(body, s, date, begin, minutes, task_id, service_id, tz, a, b, journal)
        if "created" in result:
            _just_logged[key] = time.time()
            productive.forget_task(task_id)  # its time worked changed: the next estimate check reads it again
            if ((journal or {}).get("proposed") or {}).get("source") == "logging":
                remember_logging_task(task_id, s)
        return result


def remember_logging_task(task_id: str, s: dict) -> None:
    """The task a "time spent logging" row of Log the day was just logged to: the next such row comes on it
    (dayplan._logging), so the person picks it once. Its name comes from Productive with the plan."""
    if str((s.get("logging_task") or {}).get("taskId") or "") != task_id:
        config.update({"logging_task": {"taskId": task_id, "title": "", "project": ""}})


SEEN = {"start": "start", "minutes": "length", "taskId": "task", "serviceId": "service", "note": "note"}


class EntryGone(ValueError):
    """The entry is not in Productive on that day any more."""


def _seen_whole(seen) -> bool:
    """`seen` is the entry as the page showed it, every field: one left out would never be compared."""
    return isinstance(seen, dict) and set(SEEN) <= set(seen)


def _real_time(when: datetime, tz) -> None:
    """Refuses a start the clocks skip that day (02:30 the night they go forward): it would be stored an hour later."""
    if datetime.fromtimestamp(when.timestamp(), tz).strftime("%H:%M") != when.strftime("%H:%M"):
        raise ValueError(f"{when.strftime('%H:%M')} does not exist on {when.strftime('%Y-%m-%d')}: the clocks go "
                         "forward then. Pick a start after the change.")


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
        raise EntryGone("That entry is not in Productive on that day any more. The Logged column has been refreshed.")
    if str(mine.get("personId") or "") != person:
        raise ValueError("Productive did not show that entry as yours, so Pulsar leaves it alone.")
    seen = body.get("seen")
    if seen is not None:
        if not _seen_whole(seen):
            raise ValueError("seen is the entry as the page showed it: start, minutes, taskId, serviceId and note.")
        now = {"start": _hhmm(mine.get("start"), config.tz(s)), "minutes": int(mine["minutes"]),
               "taskId": str(mine.get("taskId") or ""), "serviceId": str(mine.get("serviceId") or ""),
               "note": str(mine.get("note") or "").strip()}
        was = {"start": seen.get("start") or None, "minutes": seen.get("minutes"), "taskId": str(seen.get("taskId") or ""),
               "serviceId": str(seen.get("serviceId") or ""), "note": str(seen.get("note") or "").strip()}
        changed = [SEEN[k] for k in SEEN if was[k] != now[k]]
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
    if not _seen_whole(body.get("seen")):
        raise ValueError("The entry as you saw it (seen) is needed, so a change never lands on one that changed meanwhile.")
    with _log_lock:
        mine, existing = _own_entry(body, s)
        change: dict = {}
        if start is not None and start != _hhmm(mine.get("start"), tz):
            # in the hour the clocks repeat, the same one of the two the entry is in now
            fold = datetime.fromtimestamp(mine["start"], tz).fold if mine.get("start") is not None else 0
            when = datetime.strptime(f"{mine['date']} {start}", "%Y-%m-%d %H:%M").replace(tzinfo=tz, fold=fold)
            _real_time(when, tz)
            change["started_at"] = when.isoformat(timespec="seconds")
        if minutes is not None and minutes != int(mine["minutes"]):
            if too_short(minutes) and not body.get("restore"):  # a short entry may still move, or keep its length
                raise ValueError(too_short(minutes))
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
    if run is None and not _seen_whole(body.get("seen")):
        raise ValueError("The entry as you saw it (seen) is needed, so a delete never lands on another entry.")
    with _log_lock:
        if run is None:
            mine, _ = _own_entry(body, s)
        else:  # Undo: an entry deleted or changed by hand is left, said, and no longer offered (the rest carry on)
            made = {x["id"]: x for x in dayplan.undo_ids(dayplan.journal(), str(run))}
            x = made.get(str(body.get("id") or ""))
            if not x:
                raise ValueError("That entry was not logged by this Log the day, so Undo leaves it alone.")
            try:
                mine, _ = _own_entry(body, s)
            except EntryGone:
                if not body.get("dry"):
                    dayplan.mark_undone(str(run), x["id"], "gone")
                return {"left": x["id"], "why": "it is not in Productive on that day any more (deleted or moved by hand)"}
            tz = config.tz(s)
            now = (datetime.fromtimestamp(mine["start"], tz).strftime("%H:%M") if mine.get("start") is not None else None,
                   int(mine["minutes"]), str(mine.get("taskId") or ""))
            if now != (x["start"], int(x["minutes"]), str(x["taskId"] or "")):
                if not body.get("dry"):
                    dayplan.mark_undone(str(run), x["id"], "changed")
                return {"left": x["id"], "why": "it was changed after Log the day logged it; delete it from its card "
                                                "if you mean to"}
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


def api_dayplan_review(body: dict) -> dict:
    """Log the day with Claude (dayreview.py): the day's plan, FI's rules and a digest of the day go to the person's own
    Claude Code, isolated as Ask Claude is; its suggestions come back checked, for the review panel to accept or
    reject. Nothing is written. A POST, because it runs Claude; the page's session only (key_refusal). The last review
    of a day is kept (askclaude.stash, "review") and given back while the plan is the same (review_key); "again": true
    asks Claude anyway, "kept_only": true never asks it (the panel opening)."""
    s = current_settings()
    date = str(body.get("date") or "")
    if not DATE.match(date):
        raise ValueError("date=YYYY-MM-DD")
    if not s["ask_claude"]:
        raise ValueError("Claude Code is off. Switch it on in Settings > Claude.")
    ignore = body.get("ignore_logged") is True
    if ignore and not config.read_only():
        raise ValueError("ignore_logged is for read-only test servers only.")
    source = str(body.get("source") or "auto")
    if source not in ("auto", "aw", "memtime"):
        raise ValueError("source is auto, aw or memtime")
    inputs: dict = {}
    plan = dayplan.gather(date, source, ignore, inputs=inputs)
    if plan.get("error"):
        return {"error": plan["error"], "suggestions": []}
    rows = [{"id": e["id"], "s": e["s"], "e": e["e"], "taskId": e.get("taskId")} for e in plan["entries"]]
    model = claudeuses.model("review", s)
    key = review_key(plan["entries"], model, source, ignore)
    # the same plan, reviewed before (also before a restart): no new run, unless the person asks again ("again")
    again = None if body.get("again") is True else askclaude.stashed(date, "review", key)
    if again is not None:
        return {**again, "rows": rows, "cost": 0, "kept": True}
    if body.get("kept_only") is True:  # the panel opening: what is kept, never a run of Claude
        return {"suggestions": [], "rows": rows, "none": True}
    for r in inputs["rows"]:  # what was typed and Claude's replies, as Ask Claude reads them (capped there)
        if r["e"] - r["s"] >= 10:
            r["steps"] = askclaude.steps(inputs["t0"], r.get("titles") or [], r["s"], r["e"])
    doc, cut, size = dayreview.digest(date, inputs)
    try:
        got = dayreview.review(date, doc, plan["entries"], model=model, settings=s)
    except dayreview.ReviewError as exc:
        logbook.record("log the day", f"Review with Claude: {exc}", kind="warning")
        return {"error": str(exc), "suggestions": [], "cost": exc.cost, "rows": rows}
    if got["dropped"]:
        logbook.record("log the day", f"Review with Claude: left out {len(got['dropped'])} suggestions: "
                       f"{'; '.join(got['dropped'][:5])}", kind="warning")
    askclaude.stash(date, "review", key, {k: v for k, v in got.items() if k != "cost"})
    return {**got, "rows": rows, "digestTokens": size, "cut": cut}


def review_key(entries: list, model: str, source: str, ignore: bool) -> str:
    """What a review of the day was about: the plan's rows as proposed (time, task, kind, work, meeting), the model,
    the tracker and the test view. The same key reuses the review kept for the day; any change asks Claude again."""
    rows = [[e.get("s"), e.get("e"), e.get("taskId"), e.get("kind"), e.get("repo"), e.get("meeting"), e.get("serviceId")]
            for e in entries]
    return hashlib.sha256(json.dumps([rows, model, source, bool(ignore)], sort_keys=True).encode()).hexdigest()[:32]


def api_range(q: dict) -> dict:
    """The log dialog for a stretch picked in the Day view (a drag, a rail, + Log, Log and next): what [s, e) holds by
    Log the day's minute ownership (ranges.py), the task and note of the part that holds most of it, and the entries
    a split would make when it mixes kinds of work. Nothing is written. ignore_logged=1 as for /api/dayplan."""
    date = q.get("date", "")
    if not DATE.match(date):
        raise ValueError("date=YYYY-MM-DD")
    try:
        a, b = float(q["s"]), float(q["e"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("s and e are minutes of the day") from None
    if not 0 <= a < b <= 1440:
        raise ValueError("s and e are minutes of the day, s before e")
    ignore = q.get("ignore_logged") == "1"
    if ignore and not config.read_only():
        raise ValueError("ignore_logged is for read-only test servers only.")
    source = q.get("source", "auto")
    if source not in ("auto", "aw", "memtime"):
        raise ValueError("source is auto, aw or memtime")
    keep: dict = {}
    got = dayplan.gather(date, source, ignore, keep)
    if got.get("error") or not keep:
        return {"error": got.get("error") or "The day could not be worked out.", "groups": [], "split": []}
    s = current_settings()
    res = ranges.analyse(keep, a, b, dayplan.opts_for(s))

    def bookable(tid):
        try:
            return dayplan._bookable(tid, date, s)
        except Exception as exc:  # the entry says so; the person picks the service when logging; Logs has why
            logbook.record("log dialog", f"The services of task {tid} could not be read for the split: "
                           f"{type(exc).__name__}: {exc}", kind="warning", once_per=600)
            return None

    ranges.with_services(res, keep["ctx"], bookable)
    return {**res, "date": date, "roundTo": got.get("roundTo"), "ignoreLogged": ignore}


def api_dayplan_journal(q: dict) -> dict:
    """The Log the day runs of a day, as the journal has them: what each row became, and what Undo would delete."""
    return {"runs": [{**r, "undo": dayplan.undo_ids({"runs": [r]}, r["run"])} for r in dayplan.runs_for(q["date"])]}


_log_lock = threading.Lock()
_just_logged: dict = {}  # (date, start, minutes, task, service) -> when it was written
MAX_PIECES = 8


def _clashes(existing: list, a: float, b: float) -> list:
    """The entries with a start that [a, b) would overlap (one with no start is on no timeline: nothing to check)."""
    return sorted((x for x in existing if x.get("start") is not None and x["start"] < b and x["start"] + x["minutes"] * 60 > a),
                  key=lambda x: x["start"])


def _overlap_rows(clashes: list, tz) -> list:
    return [{"start": datetime.fromtimestamp(x["start"], tz).strftime("%H:%M"), "minutes": x["minutes"],
             "project": x["project"], "task": x.get("task")} for x in clashes]


def pieces_around(a: float, b: float, clashes: list, tz, opts: dict) -> tuple:
    """The parts of [a, b) no entry in `clashes` covers, offered by the log dialog instead of "Log anyway": the time
    asked for less the time already logged, never more and never moved. A boundary at an entry goes to Settings' grid
    (dayplan.opts_for), away from the entry; the start and end the person set stay. A part shorter than the shortest
    entry Log the day makes is left out. ([{start "HH:MM", minutes}], the minutes left out)"""
    g, shortest = opts["grid"] or 1, opts["min_entry"]

    def snap(t: float, up: bool) -> int:
        local = datetime.fromtimestamp(t, tz)
        m = local.hour * 60 + local.minute + local.second / 60
        to = (math.ceil(m / g - 1e-9) if up else math.floor(m / g + 1e-9)) * g
        return int(round(t + (to - m) * 60))

    spans, cur = [], a
    for x in sorted(clashes, key=lambda x: x["start"]):
        if x["start"] > cur:
            spans.append((cur, min(b, snap(x["start"], False))))
        cur = max(cur, snap(x["start"] + x["minutes"] * 60, True))
    if cur < b:
        spans.append((cur, b))
    pieces, short = [], 0
    for p, q in spans:
        minutes = int(round((q - p) / 60))
        if minutes >= shortest:
            pieces.append({"start": datetime.fromtimestamp(p, tz).strftime("%H:%M"), "minutes": minutes})
        elif minutes > 0:
            short += minutes
    return pieces, short


def _log_pieces(body: dict, s: dict, date: str, a: float, b: float, task_id: str, service_id: str, tz) -> dict:
    """The log dialog's "Log N entries around it": the time [a, b) the person asked for, as the pieces around the
    entries already in it, one entry each, with the same task, service and note. The pieces sent must be the ones this
    fresh read of the day gives: when the day changed since the dialog asked, nothing is written and the new pieces come
    back. Then each piece goes through the same path as one entry (a fresh read, the overlap check, never forced),
    under the write lock the caller holds. Not a Log the day run: no journal and no Undo, as for any entry the dialog
    logs; a piece that lands and an answer that is lost show as an entry in the next check, so it is never offered
    again."""
    if body.get("force"):
        raise ValueError("Pieces around logged time are never forced. Log the whole time anyway, or the pieces.")
    if body.get("run") is not None or body.get("row") is not None:
        raise ValueError("Log the day sends its rows one at a time, never as pieces.")
    sent = body.get("pieces")
    if not (isinstance(sent, list) and 1 <= len(sent) <= MAX_PIECES and all(isinstance(p, dict) for p in sent)):
        raise ValueError(f"pieces is a list of 1 to {MAX_PIECES} {{start, minutes}}.")
    sent = [{"start": str(p.get("start") or ""), "minutes": p.get("minutes")} for p in sent]
    if not body.get("dry") and config.writes_blocked():
        raise ValueError(config.writes_blocked())
    existing, problem, _ = productive.entries(date, date, s)
    if problem:
        raise RuntimeError(problem)
    clashes = _clashes(existing, a, b)
    pieces, short = pieces_around(a, b, clashes, tz, dayplan.opts_for(s))
    if pieces != sent:
        return {"overlap": _overlap_rows(clashes, tz), "pieces": pieces, "tooShort": short, "changed": True}
    if body.get("dry"):
        return {"dry": True, "overlap": _overlap_rows(clashes, tz), "wouldWrite": [
            {"date": date, "started_at": _begin(date, p["start"], tz).isoformat(timespec="seconds"), "minutes": p["minutes"],
             "task": task_id, "service": service_id} for p in pieces]}
    created: list = []
    for p in pieces:
        begin = _begin(date, p["start"], tz)
        pa = begin.timestamp()
        key = (date, p["start"], p["minutes"], task_id, service_id)
        try:
            if time.time() - _just_logged.get(key, 0) < 60:
                raise ValueError("This entry was logged a moment ago. Check the Logged column before logging it again.")
            got = _log_checked({**body, "force": False}, s, date, begin, p["minutes"], task_id, service_id, tz, pa,
                               pa + p["minutes"] * 60)
        except Exception as exc:
            if not created:
                raise
            return {"created": created, "pieces": pieces, "notLogged": pieces[len(created):], "why": str(exc),
                    "unclear": isinstance(exc, productive.ProductiveUnclear)
                    or not isinstance(exc, (ValueError, productive.ProductiveError))}
        if "overlap" in got:  # an entry logged between this piece's read and the one before
            if not created:
                return {**got, "pieces": pieces, "changed": True}
            return {"created": created, "pieces": pieces, "notLogged": pieces[len(created):],
                    "why": "it would now overlap " + "; ".join(f"{o['start']} {o['project']}" for o in got["overlap"]) + "."}
        _just_logged[key] = time.time()
        created.append(got["created"])
    return {"created": created, "pieces": pieces}


def _begin(date: str, start: str, tz) -> datetime:
    return datetime.strptime(f"{date} {start}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)


def _log_checked(body: dict, s: dict, date: str, begin, minutes: int, task_id: str, service_id: str, tz, a: float,
                 b: float, journal: dict | None = None) -> dict:
    def note(status, **fields):  # a Log the day row: its state in the journal, on disk before the next step
        if journal and not body.get("dry"):
            dayplan.mark(journal["run"], date, journal["row"], journal["entry"], status,
                         **({"via": journal["via"]} if journal.get("via") else {}),
                         **({"proposed": journal["proposed"]} if journal.get("proposed") else {}), **fields)

    if not body.get("dry") and config.writes_blocked():  # refused before the journal or the log hears of it
        raise ValueError(config.writes_blocked())
    existing, problem, _ = productive.entries(date, date, s)
    if journal:  # a row already sent is refused; one logged counts only while its entry is still in the day just read
        dayplan.check(journal["run"], journal["row"], date, journal["entry"]["start"], journal["entry"]["minutes"],
                      task_id, service_id, None if problem else {str(x.get("id")) for x in existing})
    if problem:
        note("refused", reason=f"Not sent: {problem}")
        raise RuntimeError(problem)
    clashes = _clashes(existing, a, b)
    # with an overlap, the pieces around it the dialog offers (pieces_around): never for a Log the day row
    around = {} if journal or not clashes else dict(zip(("pieces", "tooShort"),
                                                         pieces_around(a, b, clashes, tz, dayplan.opts_for(s))))
    if body.get("dry"):  # everything up to the write, for tests: nothing reaches Productive
        return {"dry": True, "wouldWrite": {"date": date, "started_at": begin.isoformat(timespec="seconds"), "minutes": minutes,
                                            "task": task_id, "service": service_id},
                "overlap": [{k: o[k] for k in ("start", "minutes", "project")} for o in _overlap_rows(clashes, tz)], **around}
    if clashes and not body.get("force"):
        overlap = _overlap_rows(clashes, tz)
        note("refused", reason="Not sent: it would overlap " + "; ".join(f"{o['start']} {o['project']}" for o in overlap))
        return {"overlap": overlap, **around}
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
    except BaseException as exc:  # anything else after sending: it may have happened, and is said so, never "not logged"
        note("unknown", reason=f"No clear answer from Productive ({type(exc).__name__}: {exc}), so the entry may or may not "
                                "exist. Check the Logged column before logging again.")
        if isinstance(exc, Exception):
            raise productive.ProductiveUnclear(f"No clear answer from Productive ({type(exc).__name__}: {exc}), so the "
                                               "entry may or may not exist. Check the Logged column (it has been "
                                               "refreshed) before logging again.") from exc
        raise
    _recent.clear()
    try:
        note("logged", id=str(created.get("id") or ""))
    except Exception as exc:  # written all the same: the row stays "sending", never sent again, but Undo cannot offer it
        logbook.record("log the day", f"Entry {created.get('id')} was logged, but Pulsar could not record it for Undo: "
                       f"{type(exc).__name__}: {exc}", kind="error")
        return {"created": created, "warning": "Logged, but Pulsar could not keep a record of it, so Undo will not "
                                               "offer this entry."}
    return {"created": created}


# ---- HTTP --------------------------------------------------------------------------------------------

KEY_ONLY = ("Only Pulsar's window can do this. Pulsar's key alone allows three things: Pulsar's tools in Claude Code, "
            "checking that this is your Pulsar, and quitting Pulsar.")


def key_refusal(path: str, body: dict = None):
    """Why a POST made with the key alone (no page session) is refused, or None. The key is a file any program of this
    user can read, a Claude Code session included, so it opens only what its two holders need: the launcher quits
    Pulsar, and Pulsar in Claude Code (mcp.py) asks the server-side tools (POST /api/mcp/<tool>, mcptools.py), whose
    answers are already cut down and whose only write is pulsar_preview's code with pulsar_log. Nothing else: no read
    of the day as the page sees it, no POST /api/log, no change or delete of an entry, no settings, no update, no Ask
    Claude, no change to ActivityWatch. A program of this user can still do more by other means (HOW-IT-WORKS.md,
    Security); this closes the plain curl."""
    if path == "/api/quit" or MCP_ROUTE.fullmatch(path):
        return None
    return KEY_ONLY


MCP_ROUTE = re.compile(r"/api/mcp/([a-z_]{1,40})")


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
        """The page, or the key itself: the launcher and Pulsar in Claude Code (mcp.py). The key alone opens only what
        key_refusal lets through, and no GET but the public ones."""
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
            key = config.session_secret()
            # proof1 is bound to the port this server listens on: a program on another port that passes the question
            # on to this Pulsar gets an answer for the wrong port (mcp.whoami_proof). proof, the older form, stays for
            # the launchers of Pulsar.app 1.4.0 and earlier, which check a server that updated itself since.
            return self.send_json({"proof": hmac.new(key.encode(), nonce.encode(), hashlib.sha256).hexdigest(),
                                   "proof1": whoami_proof(key, self.server.server_address[1], nonce)})
        if url.path not in PUBLIC and not url.path.startswith("/static/") and not self.signed_in():
            if url.path.startswith("/icon/"):
                return self.send(401, b"", "image/png")
            return self.send_json({"error": SIGN_IN, "signIn": True}, 401)
        if url.path not in PUBLIC and not url.path.startswith("/static/") and not self.page():  # the key alone
            if url.path.startswith("/icon/"):
                return self.send(403, b"", "image/png")
            return self.send_json({"error": KEY_ONLY}, 403)
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
            if url.path == "/api/daymarks" and "from" in q:  # Earlier days: the marks of a range of days
                if not (DATE.match(q.get("from") or "") and DATE.match(q.get("to") or "")):
                    raise ValueError("from=YYYY-MM-DD&to=YYYY-MM-DD")
                return self.send_json({"days": daymarks.between(q["from"], q["to"])})
            if url.path == "/api/pause":  # the pause, and (ask=1) whether to ask if it is still on
                return self.send_json(api_pause(q))
            if url.path == "/api/trackers":  # polled by the page: whether the trackers record (its notice)
                s = config.load()
                return self.send_json(trackhealth.health(s["activitywatch_url"], config.tz(s)))
            if url.path == "/api/selfcheck":  # Settings > Self-check: a Check's progress, and percents (never names)
                return self.send_json(selfcheck.view(config.load()))
            if url.path == "/api/github":  # polled by Settings while the person signs in to GitHub (github.py)
                return self.send_json(github.status())
            if url.path == "/api/calendar/status":  # polled by Settings while the person signs in to Google
                return self.send_json(calendar.status(config.load()))
            routes = {"/api/activity": api_activity, "/api/entries": api_entries, "/api/proposal": api_proposal,
                      "/api/tasks/suggest": api_suggest, "/api/services": api_services, "/api/repeats": api_repeats,
                      "/api/calendar": api_calendar, "/api/meeting-tasks": api_meeting_tasks,
                      "/api/dayplan": api_dayplan, "/api/dayplan/journal": api_dayplan_journal, "/api/range": api_range,
                      "/api/likely/picks": api_likely_picks, "/api/task/projects": api_task_projects,
                      "/api/task/places": api_task_places, "/api/task/fields": api_task_fields,
                      "/api/daymarks": lambda q: daymarks.get(q["date"])}
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
            return self.send_json({"error": "Pulsar hit an error it did not expect. The error log (Settings > Updates) has "
                                            "the details."}, 500)

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
            return self.send_json({"error": "Too much to send to Pulsar at once (over 64 KB)."}, 413)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("The request body must be a JSON object.")
            refused = None if self.page() else key_refusal(path, body)
            if refused:
                return self.send_json({"error": refused}, 403)
            tool = MCP_ROUTE.fullmatch(path)
            if tool:  # Pulsar in Claude Code: the tool's answer, computed here (mcptools.py), ASCII only
                code, answer = mcptools.call(tool.group(1), body)
                return self.send(code, json.dumps(answer, ensure_ascii=True).encode("ascii"),
                                 "application/json; charset=utf-8")
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
            if urlparse(self.path).path == "/api/update/app":  # "Reinstall Pulsar": the app itself (appupdate.py)
                return self.send_json(api_app_reinstall())
            if urlparse(self.path).path == "/api/app/started":  # a new app's page says it started (appupdate.py)
                return self.send_json(appupdate.started(str(body.get("version") or "")[:20]))
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
            if urlparse(self.path).path == "/api/task/draft":
                return self.send_json(api_task_draft(body))
            if urlparse(self.path).path == "/api/task/create":
                return self.send_json(api_task_create(body))
            if urlparse(self.path).path == "/api/daymarks":  # the day done, time not work: this Mac only
                return self.send_json(daymarks.change(str(body.get("date") or ""), done=body.get("done"),
                                                      add=body.get("add"), remove=body.get("remove"),
                                                      undone=body.get("undone") is True,
                                                      nolearn=body.get("nolearn"), learn=body.get("learn")))
            if urlparse(self.path).path == "/api/dayplan/review":
                return self.send_json(api_dayplan_review(body))
            if urlparse(self.path).path == "/api/activitywatch/fix":  # Settings: ActivityWatch's AFK timeout
                return self.send_json(api_aw_fix(body))
            if urlparse(self.path).path == "/api/activitywatch/login":  # Settings: ActivityWatch in Login Items
                return self.send_json(api_aw_login(body))
            if urlparse(self.path).path == "/api/pause":  # pause, resume, or keep the pause on
                return self.send_json(api_pause_change(body))
            if urlparse(self.path).path == "/api/trackers/open":  # the trackers notice: open or restart one
                return self.send_json(api_trackers_open(body))
            if urlparse(self.path).path == "/api/selfcheck/check":  # Settings > Self-check > Check: in the background
                s = config.load()
                return self.send_json({"started": selfcheck.start(s), **selfcheck.view(s)})
            if urlparse(self.path).path == "/api/calendar/connect":
                url = calendar.start(self.server.server_address[1])
                try:
                    opened = bool(calendar.open_browser(url))  # the default browser; the page also offers the link
                except Exception:
                    opened = False
                return self.send_json({"url": url, "opened": opened})
            if urlparse(self.path).path == "/api/github/signin":  # Settings > Updates: the GitHub device flow
                got = github.start()
                try:
                    opened = bool(calendar.open_browser(got["url"]))  # the default browser; the page also offers the link
                except Exception:
                    opened = False
                return self.send_json({"opened": opened, "github": github.status()})
            if urlparse(self.path).path == "/api/github/signout":
                github.sign_out()
                return self.send_json({"update": UPDATER.public()})
            if urlparse(self.path).path == "/api/calendar/disconnect":
                return self.send_json(calendar.disconnect(config.load()))
            if urlparse(self.path).path == "/api/quit":
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return self.send_json({"ok": True})
            return self.send(404, b"not found", "text/plain")
        except (ValueError, RuntimeError, productive.ProductiveError, calendar.CalendarError) as exc:
            if not isinstance(exc, ValueError):  # Productive refused or did not answer: worth a line in Logs
                logbook.record(f"POST {urlparse(self.path).path}", str(exc), kind="warning")
            # unclear: it may have been written (a timeout, a 5xx after sending); never to be read as "not logged"
            return self.send_json({"error": str(exc), **({"unclear": True} if isinstance(exc, productive.ProductiveUnclear)
                                                         else {})}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # never drop the connection: say what went wrong
            logbook.record(f"POST {urlparse(self.path).path}", f"{type(exc).__name__}: {exc}", detail=traceback.format_exc())
            return self.send_json({"error": "Pulsar hit an error it did not expect, so this "
                                            "may or may not have happened. The error log (Settings > Updates) has the "
                                            "details.", "unclear": True}, 500)

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
                                                             "See the error log in Settings > Updates, then try again."}
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
    claudelink.tidy_saved()  # the Productive ask rules of 1.3 and 1.4 go, unless chosen in Settings (claudelink.tidy)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    UPDATER.start()
    return httpd
