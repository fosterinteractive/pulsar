"""Pulsar for Claude Code: an MCP server over the running Pulsar's local API.

    python3 pulsar.py --mcp [--port 5610]
    claude mcp add pulsar -- /usr/bin/python3 -E -s -B "$HOME/Library/Application Support/Pulsar/current/pulsar.py" --mcp

MCP over stdio: JSON-RPC 2.0, one message per line (the MCP SDK needs Python 3.10+; this is the standard library).

Tools (all read only, except pulsar_log):
  pulsar_status()                      never fails: is Pulsar open, its version, whether writes are blocked and why, the date
  pulsar_day(date)                     the day in numbers: logged, active stretches with no entry, Likely, meetings
  pulsar_propose(date)                 Log the day's proposal: entries with task, service and confidence
  pulsar_session(date)                 the calling Claude Code session's own minutes, with the task Pulsar suggests
  pulsar_suggest(date, start, minutes) the tasks the log dialog would offer for one stretch
  pulsar_tasks(query)                  your recent tasks (with the service you used last) and a Productive search
  pulsar_preview(date, entries)        the dry run: what would be written, a code, and Pulsar's own summary of it
  pulsar_log(code, summary)            writes what the code holds (up to 40 entries). Marked so Claude Code asks the
                                       person before each call, also in auto or bypass mode (an undocumented mark,
                                       read by Claude Code 2.1.268 to 2.1.301; claudelink also adds an "ask" rule for
                                       it); the summary must be Pulsar's, word for word, so the prompt shows Pulsar's
                                       words. Refused in any other app (they ignore the mark).

What it never returns: window titles, apps, sites or addresses, typed messages, meeting titles, the titles of other
Claude sessions (also where one stands in for a repository: Places), notes of logged entries, rule words, the words
a learned task's notes share with other sessions, and notes drafted for another repository's work. Every answer is
built field by field from an allow-list; nothing from Pulsar is passed through as is. The calling session's own title
is returned by pulsar_session (it is that session).

How it reaches Pulsar: 127.0.0.1 only, no proxy, no redirects. Each time before it shows the key
(~/.config/pulsar/secret, as X-Pulsar-Key) it checks /api/whoami the way the launcher does, so a program that is not
this user's Pulsar never sees the key. Writes go through POST /api/log, the log dialog's path: a fresh read of the day,
the overlap check, the write lock, the Log the day journal (no entry sent twice) and config.writes_blocked()
(PULSAR_READ_ONLY, Preview). The key opens nothing more on Pulsar's side: no force, no change or delete of an entry,
no settings (server.key_refusal). It is not a wall: any program of this user can read the key file too, so the
person's yes in Claude Code guards this tool, not the key.
"""

from __future__ import annotations

import glob
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlencode

from . import __version__, config

MODERN = ("2026-07-28",)  # stateless: every request says its version in _meta; server/discover
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")  # the initialize handshake; newest first
META = "io.modelcontextprotocol/"
DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
HHMM = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
ID = re.compile(r"^[0-9]{1,12}$")
SESSION = re.compile(r"^[0-9a-f]{8}(?:-?[0-9a-f]{4}){0,3}(?:-?[0-9a-f]{12})?$")
CONFIRM_FOR = 600  # seconds a dry run's confirm code stays good
MOST_ENTRIES = 40
PROJECTS = os.path.expanduser("~/.claude/projects")


class ToolError(Exception):
    """Something the person or Claude should read: a tool result with isError, not a protocol error."""


# ---- talking to Pulsar ---------------------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # the key only ever goes to 127.0.0.1:PORT
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
_state = {"port": 5610}


def _key() -> str:
    try:
        with open(config.SECRET_FILE, encoding="utf-8") as fh:
            value = fh.read().strip()
    except OSError:
        raise ToolError("Pulsar has not been set up for this user (no ~/.config/pulsar/secret). Open Pulsar once.")
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ToolError("Pulsar's key file looks damaged. Open Pulsar once; it writes a new one.")
    return value


def _short(text, n: int = 200) -> str:
    """An error from Pulsar, first line only and cut short."""
    text = str(text or "").strip()
    return text.splitlines()[0][:n] if text else ""


def _request(path: str, params: dict | None = None, body: dict | None = None, key: str | None = None,
             timeout: float = 120) -> dict:
    url = f"http://127.0.0.1:{_state['port']}{path}" + (f"?{urlencode(params)}" if params else "")
    headers = {"Accept": "application/json"}
    data = None
    if key:
        headers["X-Pulsar-Key"] = key
    if body is not None:
        headers.update({"X-Pulsar": "1", "Content-Type": "application/json"})
        data = json.dumps(body).encode("utf-8")
    try:
        with _OPENER.open(urllib.request.Request(url, data=data, headers=headers), timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            got = json.loads(exc.read().decode("utf-8"))
        except Exception:
            got = {}
        if exc.code == 401:
            raise ToolError("Pulsar refused the key. Is the Pulsar on this port another user's?")
        return {"error": _short(got.get("error")) or f"HTTP {exc.code}", "_status": exc.code,
                **({"unclear": True} if got.get("unclear") is True else {})}  # it may have been written
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        raise ToolError(f"Pulsar is not answering on 127.0.0.1:{_state['port']}. Open Pulsar, then ask again. "
                        f"({type(getattr(exc, 'reason', exc)).__name__})")


def _verify() -> str:
    """The launcher's check: the server proves it holds this user's key (an HMAC of a fresh nonce) before it is shown
    the key. Before every call that sends it, not once per process: this process lives as long as the Claude Code
    session, and Pulsar may quit meanwhile and leave its port to another program."""
    key = _key()
    nonce = secrets.token_hex(16)
    proof = str(_request("/api/whoami", {"nonce": nonce}, timeout=5).get("proof") or "")
    want = hmac.new(key.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(proof.encode(), want.encode()):
        raise ToolError(f"Something on 127.0.0.1:{_state['port']} answered, but it is not this user's Pulsar. "
                        "Nothing was sent.")
    return key


def fetch(calls: dict) -> dict:
    """{name: (path, params)} at the same time, as the page does: one slow source never holds the others."""
    key = _verify()
    with ThreadPoolExecutor(max(1, len(calls))) as pool:
        jobs = {name: pool.submit(_request, path, params, None, key) for name, (path, params) in calls.items()}
        return {name: job.result() for name, job in jobs.items()}


def post(path: str, body: dict) -> dict:
    return _request(path, None, body, _verify())


# ---- small helpers -------------------------------------------------------------------------------------------

def hm(m) -> str | None:
    if m is None:
        return None
    m = int(round(float(m)))
    return f"{m // 60:02d}:{m % 60:02d}"


def mins(m) -> int:
    return int(round(float(m or 0)))


def overlap(a: float, b: float, s: float, e: float) -> float:
    return max(0.0, min(b, e) - max(a, s))


def ignore_logged() -> bool:
    """Test switch: every tool sees the day as if nothing were logged, to measure a proposal on a day already logged.
    Only on a read-only run: PULSAR_READ_ONLY here too (the server checks its own), else every tool refuses, so a
    test never runs half blind."""
    if not os.environ.get("PULSAR_MCP_IGNORE_LOGGED"):
        return False
    if not config.read_only():
        raise ToolError("PULSAR_MCP_IGNORE_LOGGED is a test switch for read-only runs only: set PULSAR_READ_ONLY=1 in "
                        "this MCP server's environment and run Pulsar read-only.")
    return True


def _likely_q(q: dict) -> dict:
    """The Likely column's query. In the test switch, its learned tasks must not see the day's own entries."""
    return {**q, **({"ignore_logged": "1"} if ignore_logged() else {})}


def _today(tz_name: str) -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz_name)).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _date_arg(args: dict, cfg: dict) -> str:
    today = _today(cfg.get("timezone") or "")
    d = str(args.get("date") or "today").strip().lower()
    if d == "today":
        return today
    if d == "yesterday":
        return (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    if not DATE.match(d):
        raise ToolError("date is YYYY-MM-DD, today or yesterday.")
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        raise ToolError(f"{d} is not a date.")
    return d


def _history_date(date: str) -> str:
    """The date the "logged lately" lists are read up to: the day before, in the test mode that hides the day's entries
    (they would give the answer away)."""
    if not ignore_logged():
        return date
    return (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")


def _config() -> dict:
    cfg = fetch({"config": ("/api/config", None)})["config"]
    if cfg.get("error"):
        raise ToolError(f"Pulsar: {cfg['error']}")
    return cfg


def task_out(t: dict, why: str | None = None) -> dict:
    """A task, from any of Pulsar's lists, with the allowed fields only."""
    out = {"taskId": str(t.get("taskId") or ""), "task": str(t.get("title") or t.get("task") or ""),
           "project": str(t.get("project") or "")}
    for k in ("serviceId", "service", "budget", "billable", "due", "status", "blocked"):
        if t.get(k) not in (None, ""):
            out[k] = t[k]
    if why:
        out["why"] = why
    return out


def safe_why(why: str) -> str:
    """Pulsar's reasons are made of repository names, counts and durations, except those that quote a meeting's title,
    a rule's words or Ask Claude's own words: those parts are replaced."""
    why = str(why or "")
    why = re.sub(r";? ?on your calendar as “[^”]*”", "; on your calendar", why)
    why = re.sub(r"your rule “[^”]*”", "one of your rules", why)
    if "the title says" in why:
        return "a word in the meeting's title is in the task's name"
    if why.startswith("Claude:"):
        return "Ask Claude's pick"
    # a learned task's reason quotes words shared by your past notes and other sessions' text: the words stay here
    why = re.sub(r"(your notes on it share words with this work):.*", r"\1", why)
    return why[:140]


# Where no file a session touched was in a repository, sessions.py and the fi-time-from-activity script name the work
# after the session's title (cut to 40 characters; the script first drops a leading "IMPORTANT!"), then "Claude Code"
# or "unknown". Such a name is another session's words, not a repository: it never leaves.
NAMELESS = {"claude code", "unknown"}


def _title_names(rows: list) -> set:
    """The project names in the day's Likely rows that are a session title standing in for a repository."""
    cuts = set()
    for r in rows:
        for t in r.get("titles") or []:
            t = str(t or "")
            cuts.update({t.strip()[:40], re.sub(r"^important!\s*", "", t, flags=re.I).strip()[:40],
                         re.sub(r"^important!\s*", "", t.strip(), flags=re.I).strip()[:40]})
    cuts.discard("")
    return {p for p in (str(r.get("project") or "") for r in rows) if p and (p in cuts or p.strip().lower() in NAMELESS)}


class Places:
    """The repositories a day's answers may name: those of its Likely rows, minus session titles standing in for one
    (above). Every repository and every reason that names one goes through here."""

    def __init__(self, rows: list):
        self.hidden = _title_names(rows)
        self.known = {str(r.get("project") or "") for r in rows} - self.hidden - {""}

    def repo(self, name) -> str:
        name = str(name or "")
        return name if name in self.known else ""

    def why(self, text) -> str:
        """Pulsar's reason, with a place it names kept only when it is one of the day's repositories: a learned task's
        reason can name a past day's, which may have been a session title."""
        text = str(text or "")

        def keep(m):
            name = m.group(2)
            return m.group(1) + (name if name in self.known or name == "this Claude session" else "other work") + m.group(3)
        text = re.sub(r"(logged (?:it )?while in )(.+?)( \(\d+%\)|: \d|$)", keep, text)
        text = re.sub(r"^(.+?)(: you never log this repository)",
                      lambda m: (m.group(1) if m.group(1) in self.known else "this work") + m.group(2), text)
        for name in sorted(self.hidden, key=len, reverse=True):
            text = text.replace(name, "other work")
        return safe_why(text)


def entry_out(x: dict) -> dict:
    return {"start": hm(x.get("s")), "end": hm(x.get("e")), "minutes": mins(x.get("minutes")),
            "project": str(x.get("project") or ""), "task": str(x.get("task") or ""), "taskId": x.get("taskId"),
            "service": str(x.get("service") or ""), "billable": x.get("billable")}


def _entries(got: dict, warnings: list) -> list:
    if ignore_logged():
        return []
    if got.get("error"):
        warnings.append("Logged: " + _short(got["error"]))
        return []
    if got.get("warning"):
        warnings.append("Logged: " + _short(got["warning"]))
    return got.get("entries") or []


def active_minutes(items: list) -> list:
    """Work minutes per minute of the day (fractions), personal windows left out: as the page's yellow rail."""
    active = [0.0] * 1441
    for it in items:
        if it.get("cat") == "personal":
            continue
        m, e = float(it["s"]), float(it["e"])
        while m < e and m < 1440:
            nxt = min(e, int(m) + 1)
            active[int(m)] += nxt - m
            m = nxt
    return active


def covered_minutes(entries: list) -> list:
    covered = [False] * 1441
    for e in entries:
        if e.get("s") is None:
            continue
        end = max(int(e["s"]) + mins(e["minutes"]), mins(e.get("e")))  # e: its end on the clock (Pulsar's)
        for m in range(max(0, int(e["s"])), min(1440, end)):
            covered[m] = True
    return covered


def runs(flags: list, least: int = 3) -> list:
    """[(start, end)] of the true stretches of a minute list, `least` minutes or longer."""
    out, start = [], None
    for m in range(len(flags) + 1):
        hit = m < len(flags) and flags[m]
        if hit and start is None:
            start = m
        if not hit and start is not None:
            if m - start >= least:
                out.append((start, m))
            start = None
    return out


# ---- pulsar_day ----------------------------------------------------------------------------------------------

def pulsar_day(args: dict) -> dict:
    cfg = _config()
    date = _date_arg(args, cfg)
    q = {"date": date}
    got = fetch({"entries": ("/api/entries", q), "activity": ("/api/activity", q), "likely": ("/api/proposal", _likely_q(q)),
                 "calendar": ("/api/calendar", q), "repeats": ("/api/repeats", q)})
    warnings: list = []
    act, lk, cal, rep = (got[k] for k in ("activity", "likely", "calendar", "repeats"))
    entries = _entries(got["entries"], warnings)
    items = [] if act.get("error") else act.get("items") or []
    if act.get("error"):
        warnings.append("Activity: " + _short(act["error"]))
    rows = [] if lk.get("error") or lk.get("disabled") else lk.get("entries") or []
    if lk.get("error"):
        warnings.append("Likely: " + _short(lk["error"]))
    events = cal.get("events") or []
    if cal.get("problem"):
        warnings.append("Meetings: " + _short(cal["problem"]))
    me = _session_ids()[0]
    places = Places(rows)

    logged = sum(mins(x["minutes"]) for x in entries)
    known = [x for x in entries if x.get("billable") is not None]
    billable = sum(mins(x["minutes"]) for x in known if x["billable"])
    not_billable = sum(mins(x["minutes"]) for x in known if not x["billable"])
    work = sum(float(x["e"]) - float(x["s"]) for x in items if x.get("cat") != "personal")
    personal = sum(float(x["e"]) - float(x["s"]) for x in items if x.get("cat") == "personal")
    active, covered = active_minutes(items), covered_minutes(entries)
    gap = sum(min(1.0, active[m]) for m in range(1440) if not covered[m])

    def likely_projects(a, b):
        cover: dict = {}
        for r in rows:
            o = overlap(a, b, float(r["s"]), float(r["e"]))
            if o > 0:
                p = places.repo(r.get("project"))
                cover[p] = cover.get(p, 0) + o
        return [{"project": p, "minutes": mins(m)} for p, m in sorted(cover.items(), key=lambda kv: -kv[1]) if mins(m)][:4]

    def in_meeting(a, b):
        return any(not ev.get("declined") and not ev.get("all_day")
                   and overlap(a, b, float(ev["s"]), float(ev["e"])) >= (b - a) / 2 for ev in events)

    unlogged = [{"start": hm(a), "end": hm(b), "minutes": b - a,
                 "activeMinutes": mins(sum(min(1.0, active[i]) for i in range(a, b))),
                 "likelyProjects": likely_projects(a, b), "inMeeting": in_meeting(a, b)}
                for a, b in runs([active[m] >= 0.5 and not covered[m] for m in range(1440)])]

    # Likely: rows of 5 minutes or more, and per project the minutes and the tasks learned for it, once (per row the
    # same tasks repeat: a busy day was 48 KB, over Claude Code's 10k-token warning)
    likely_rows, by_project = [], {}
    for r in rows:
        name = places.repo(r.get("project"))
        p = by_project.setdefault(name, {"project": name, "minutes": 0, "learned": []})
        p["minutes"] += mins(r["minutes"])
        for t in r.get("learned") or []:
            if len(p["learned"]) < 3 and t.get("taskId") not in {x["taskId"] for x in p["learned"]}:
                p["learned"].append(task_out(t, places.why(t.get("why"))))
        if mins(r["minutes"]) < 5:
            continue
        row = {"start": hm(r["s"]), "end": hm(r["e"]), "minutes": mins(r["minutes"]), "project": name,
               "kind": str(r.get("kind") or "")}
        if r.get("tasks"):
            row["linkedTaskIds"] = [str(t) for t in r["tasks"]][:4]
        if me and me in (r.get("sessions") or []):
            row["thisSession"] = True
        likely_rows.append(row)

    meetings = [{"start": None if ev.get("all_day") else hm(ev["s"]), "end": None if ev.get("all_day") else hm(ev["e"]),
                 "minutes": 0 if ev.get("all_day") else mins(float(ev["e"]) - float(ev["s"])), "allDay": bool(ev.get("all_day")),
                 "declined": bool(ev.get("declined")), "recurring": bool(ev.get("recurring"))} for ev in events]
    repeats = [{"start": hm(x.get("s")), "end": hm(x.get("e")), "minutes": mins(x.get("minutes")),
                **task_out(x, f"logged at about {hm(x.get('at'))} on {x.get('days')} of the last 28 days; a call was seen")}
               for x in rep.get("repeats") or []]
    return {
        "date": date, "timezone": cfg.get("timezone"),
        "totals": {"loggedMinutes": logged, "billableMinutes": billable, "notBillableMinutes": not_billable,
                   "billingUnknownMinutes": logged - billable - not_billable, "activeWorkMinutes": mins(work),
                   "personalMinutes": mins(personal), "activeNotLoggedMinutes": mins(gap),
                   "awayMinutes": mins(sum(float(x["e"]) - float(x["s"]) for x in act.get("away") or [] if not x.get("off"))),
                   "targetMinutes": cfg.get("targetMinutes")},
        "logged": [entry_out(x) for x in sorted(entries, key=lambda x: x.get("s") or 0)],
        "loggedWithoutStart": sum(1 for x in entries if x.get("s") is None),
        "unlogged": unlogged,
        "likely": likely_rows,
        "likelyByProject": sorted(by_project.values(), key=lambda p: -p["minutes"]),
        "meetings": meetings,
        "repeats": repeats,
        "sources": {"activity": act.get("source"), "likely": None if lk.get("disabled") else lk.get("tracker")},
        **({"testMode": "planned as if nothing were logged"} if ignore_logged() else {}),
        "warnings": warnings,
    }


# ---- pulsar_propose ------------------------------------------------------------------------------------------

# Drafted notes returned: from your commits or Ask Claude, and only for the calling session's own repository (both are
# made from that repository's work: another's is another client's words). A repeat's note is the note of past logged
# entries, and a meeting's is its title: never returned.
NOTE_SOURCES = ("commits", "claude")


def _caller_repo() -> str | None:
    """The repository the calling session works in: Claude Code gives every MCP server it starts CLAUDE_PROJECT_DIR.
    None when it did not, or the folder is in no repository."""
    from . import sessions
    folder = str(os.environ.get("CLAUDE_PROJECT_DIR") or "").strip()
    return sessions.project_of_path(folder) if os.path.isabs(folder) else None


def _same_repo(a: str, b: str | None) -> bool:
    """Loosely, as commits.matches: the names contain one another ("Acme" and acme-web)."""
    a, b = (re.sub(r"[^a-z0-9]", "", (x or "").split(" / ")[0].lower()) for x in (a, b))
    return len(a) >= 3 and len(b) >= 3 and (a in b or b in a)


def plan_entry(e: dict, places: Places) -> dict:
    out = {"start": hm(e["s"]), "end": hm(e["e"]), "minutes": mins(e["minutes"]),
           "taskId": str(e.get("taskId") or "") or None, "task": str(e.get("title") or ""),
           "project": str(e.get("project") or ""), "repository": places.repo(e.get("repo")),
           "serviceId": str(e.get("serviceId") or "") or None, "service": str(e.get("service") or ""),
           "billable": e.get("billable"),
           "confidence": e.get("confidence"), "pulsarWouldTick": bool(e.get("checked")),
           "why": places.why(e.get("why")), "flags": [f for f in e.get("flags") or [] if isinstance(f, str)]}
    if e.get("meeting"):
        out["meeting"] = True
    if e.get("note") and e.get("noteFrom") in NOTE_SOURCES and _same_repo(out["repository"], _caller_repo()):
        out["noteDraft"], out["noteFrom"] = str(e["note"])[:300], e["noteFrom"]
    if e.get("sent"):
        out["alreadySent"] = e["sent"]
    alts = [task_out(a) for a in e.get("alts") or [] if a.get("taskId")]
    if alts:  # the next guesses: often the right one when the pick's project does not fit the repository
        out["alternatives"] = [{"taskId": a["taskId"], "task": a["task"], "project": a["project"]} for a in alts][:3]
    if e.get("serviceProblem"):
        out["serviceProblem"] = _short(e["serviceProblem"], 160)
    return out


def _plan(date: str, rows: list | None = None) -> tuple:
    """(Log the day's plan, the day's Places). The plan names repositories from the Likely rows; without the rows
    given, they are read too, at the same time, to tell repositories from session titles."""
    params = {"date": date, **({"ignore_logged": "1"} if ignore_logged() else {})}
    calls = {"plan": ("/api/dayplan", params)}
    if rows is None:
        calls["likely"] = ("/api/proposal", _likely_q({"date": date}))
    got = fetch(calls)
    plan = got["plan"]
    if plan.get("error") and not plan.get("entries"):
        raise ToolError(f"Pulsar could not plan {date}: {_short(plan['error'])}")
    if rows is None:
        lk = got["likely"]
        rows = [] if lk.get("error") or lk.get("disabled") else lk.get("entries") or []
    return plan, Places(rows)


def pulsar_propose(args: dict) -> dict:
    cfg = _config()
    date = _date_arg(args, cfg)
    got, places = _plan(date)
    t = got.get("totals") or {}
    return {
        "date": date, "timezone": cfg.get("timezone"),
        "proposed": [plan_entry(e, places) for e in got.get("entries") or []],
        "notPlaced": [{"minutes": mins(u.get("minutes")), "taskId": u.get("task"), "project": places.repo(u.get("project")),
                       "why": places.why(u.get("why"))} for u in got.get("unplaced") or []],
        "activeNotCovered": [{"start": hm(u["s"]), "end": hm(u["e"]), "minutes": mins(u["minutes"])}
                             for u in got.get("uncovered") or []],
        "totals": {k: t.get(k) for k in ("proposed", "checked", "logged", "active", "meetings", "notCovered", "over")},
        "how": ("Pulsar's Log the day: each minute placed once (logged entries, then meetings, then Claude sessions, then "
                "other work); proposed plus logged never exceeds active time plus meetings. pulsarWouldTick=false means "
                "Pulsar is not sure (low confidence, no task, no service, agent-only time, a meeting with no call seen). "
                "The task is a guess from past logging: check that its project fits the repository the work was in; "
                "when it does not, an alternative from that client is often the right one. Say which ones you changed."),
        **({"testMode": "planned as if nothing were logged"} if ignore_logged() else {}),
        "warnings": [w for w in (_short(got.get("warning")), _short(got.get("likelyError"))) if w],
    }


# ---- pulsar_session ------------------------------------------------------------------------------------------

def _session_ids() -> tuple:
    """(the calling session's id, first 8 characters, or '', where it came from). Claude Code gives every MCP server
    it starts CLAUDE_CODE_SESSION_ID; PULSAR_MCP_SESSION_ID overrides it (tests)."""
    for var in ("PULSAR_MCP_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        raw = str(os.environ.get(var) or "").strip().lower()
        if raw and SESSION.match(raw):
            return raw[:8], raw, var
    return "", "", ""


def _session_title(full_id: str) -> str:
    """The calling session's own title (a title the person set wins over Claude's), from its file."""
    paths = glob.glob(os.path.join(PROJECTS, "*", f"{full_id}*.jsonl"))
    if not paths:
        return ""
    title, custom = "", False
    try:
        with open(paths[0], encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"ai-title"' not in line and '"custom-title"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                name = r.get("customTitle") or r.get("aiTitle")
                if r.get("type") in ("ai-title", "custom-title") and name:
                    if r.get("type") == "custom-title" or not custom:
                        title, custom = str(name), custom or r.get("type") == "custom-title"
    except OSError:
        return ""
    return title[:120]


def _session_task(row: dict, a: int, b: int, plan: list, places: Places):
    """The task for one of the session's stretches: Log the day's entry over it in the same repository; else the task
    learned for the row (logged before in this session or repository); else the entry over it, whatever its work."""
    with_task = [e for e in plan if e.get("taskId") and overlap(a, b, e["s"], e["e"]) > 0]
    same = [e for e in with_task if (e.get("repo") or "") == (row.get("project") or "")]
    keys = ("taskId", "task", "project", "serviceId", "service", "billable", "confidence", "why", "noteDraft", "noteFrom")
    if same:
        p = plan_entry(max(same, key=lambda e: overlap(a, b, e["s"], e["e"])), places)
        return {k: p.get(k) for k in keys if p.get(k) not in (None, "")}
    learned = [t for t in row.get("learned") or [] if t.get("taskId")]
    if learned:
        return {**task_out(learned[0], places.why(learned[0].get("why"))), "confidence": "low",
                "alternatives": [task_out(t) for t in learned[1:3]]}
    if with_task:
        p = plan_entry(max(with_task, key=lambda e: overlap(a, b, e["s"], e["e"])), places)
        return {**{k: p.get(k) for k in keys if p.get(k) not in (None, "")}, "confidence": "low",
                "why": "the entry Log the day proposes over this time, for other work"}
    return None


def pulsar_session(args: dict) -> dict:
    sid, full, var = _session_ids()
    if not sid:
        raise ToolError("Claude Code did not say which session this is (no CLAUDE_CODE_SESSION_ID), so Pulsar cannot "
                        "find its minutes. Use pulsar_propose for the whole day instead.")
    cfg = _config()
    date = _date_arg(args, cfg)
    q = {"date": date, **({"fresh": "1"} if date == _today(cfg.get("timezone") or "") else {})}
    got = fetch({"likely": ("/api/proposal", _likely_q({**q, "wait": "1"})), "entries": ("/api/entries", {"date": date})})
    warnings: list = []
    lk = got["likely"]
    if lk.get("error") or lk.get("disabled"):
        raise ToolError("Pulsar's Likely column is " + ("switched off." if lk.get("disabled") else _short(lk["error"])))
    entries = _entries(got["entries"], warnings)
    mine = [r for r in lk.get("entries") or [] if sid in (r.get("sessions") or [])]
    places = Places(lk.get("entries") or [])
    plan = _plan(date, lk.get("entries") or [])[0] if mine else {"entries": []}
    covered = covered_minutes(entries)
    rows, to_log, shared = [], [], []
    for r in sorted(mine, key=lambda r: float(r["s"])):
        a, b = int(float(r["s"])), int(round(float(r["e"])))
        suggestion = _session_task(r, a, b, plan.get("entries") or [], places)
        others = [x for x in r.get("sessions") or [] if x != sid]
        rows.append({"start": hm(a), "end": hm(b), "minutes": mins(r["minutes"]), "project": places.repo(r.get("project")),
                     "kind": str(r.get("kind") or ""), "withOtherSessions": len(others),
                     "alreadyLoggedMinutes": sum(1 for m in range(a, min(b, 1440)) if covered[m]),
                     **({"suggested": suggestion} if suggestion else {})})
        for s, e in runs([not covered[m] for m in range(a, min(b, 1440))], least=5):
            piece = {"start": hm(a + s), "minutes": e - s, "project": places.repo(r.get("project")),
                     **({"taskId": suggestion["taskId"], "task": suggestion.get("task"),
                         "serviceId": suggestion.get("serviceId")} if suggestion else {})}
            # a stretch another session also had: which one the minutes were for is not known, so it waits for a yes
            (shared if others else to_log).append(piece)
    return {"date": date, "session": sid, "sessionFrom": var, "title": _session_title(full),
            "rows": rows, "minutes": sum(r["minutes"] for r in rows),
            "notLoggedYet": to_log, "sharedNotLogged": shared,
            "note": ("The minutes Pulsar gave this Claude session that day (from the activity tracker and the session's "
                     "messages). notLoggedYet is ready for pulsar_preview, then the person's yes in pulsar_log. The "
                     "suggested task is a guess from past logging: you know what this session did, so when it does not "
                     "fit, find the right task with pulsar_tasks. sharedNotLogged are stretches another session also "
                     "had: ask before logging them here."
                     if rows else f"Pulsar has no minutes for this session on {date}. Likely is worked out from the "
                     "activity tracker; a session from another day needs that date."),
            **({"testMode": "planned as if nothing were logged"} if ignore_logged() else {}),
            "warnings": warnings}


# ---- pulsar_suggest ------------------------------------------------------------------------------------------

def _stretch(args: dict) -> tuple:
    start, minutes = str(args.get("start") or ""), args.get("minutes")
    m = HHMM.match(start)
    if not m:
        raise ToolError("start is HH:MM (24-hour).")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 1440:
        raise ToolError("minutes is a whole number from 1 to 1440.")
    a = int(m.group(1)) * 60 + int(m.group(2))
    if a + minutes > 1440:
        raise ToolError("An entry cannot run past midnight.")
    return a, a + minutes


def pulsar_suggest(args: dict) -> dict:
    cfg = _config()
    date = _date_arg(args, cfg)
    a, b = _stretch(args)
    q = {"date": date}
    got = fetch({"entries": ("/api/entries", q), "activity": ("/api/activity", q), "likely": ("/api/proposal", _likely_q(q)),
                 "calendar": ("/api/calendar", q), "repeats": ("/api/repeats", q),
                 "suggest": ("/api/tasks/suggest", {"date": _history_date(date)})})
    rows = got["likely"].get("entries") or [] if not got["likely"].get("error") else []
    places = Places(rows)
    events = got["calendar"].get("events") or []
    sug = got["suggest"] if not got["suggest"].get("error") else {}
    recent = sug.get("recent") or []
    known = {t.get("taskId"): t for t in recent}
    enrich = lambda t: {**known.get(t.get("taskId"), {}), **{k: v for k, v in t.items() if v not in (None, "")}}  # noqa: E731

    # 1. the lead group, as leadFor() in day.js: the Likely project that covers half the stretch, else the meeting
    lead_label, lead, cover = None, [], {}
    for r in rows:
        o = overlap(a, b, float(r["s"]), float(r["e"]))
        if o > 0:
            cover[r.get("project") or ""] = cover.get(r.get("project") or "", 0) + o
    project = max(cover, key=cover.get) if cover else None
    if project is not None and cover[project] >= (b - a) / 2:
        mine = sorted([r for r in rows if r.get("project") == project and overlap(a, b, float(r["s"]), float(r["e"])) > 0],
                      key=lambda r: -float(r["minutes"]))
        ids = list(dict.fromkeys(str(t) for r in mine for t in r.get("tasks") or []))[:4]
        if ids:
            named = fetch({"t": ("/api/tasks/estimates", {"ids": ",".join(ids)})})["t"].get("tasks") or {}
            lead += [task_out(enrich(named[i]), "linked in the Claude session") for i in ids if (named.get(i) or {}).get("title")]
        seen: set = set()
        for t in (t for r in mine for t in r.get("learned") or []):
            if t.get("taskId") not in seen:
                seen.add(t.get("taskId"))
                lead.append(task_out(enrich(t), places.why(t.get("why"))))
        lead_label = (f"For this work ({places.repo(project)})" if places.repo(project) else "For this work") if lead else None
    if not lead:
        best = max(((ev, overlap(a, b, float(ev["s"]), float(ev["e"]))) for ev in events
                    if not ev.get("declined") and not ev.get("all_day")), key=lambda p: p[1], default=None)
        if best and best[1] >= (b - a) / 2:
            ev = best[0]  # its title goes to Pulsar on this Mac, to match earlier meetings; it is never returned
            mt = fetch({"m": ("/api/meeting-tasks", {"date": date, "uid": ev.get("uid") or "", "title": ev.get("title") or ""})})["m"]
            lead = [task_out(enrich(t), places.why(t.get("why"))) for t in mt.get("tasks") or []]
            lead_label = "For the meeting" if lead else None

    usual = [task_out(enrich(x), f"logged at about {hm(x.get('at'))} on {x.get('days')} of the last 28 days")
             for x in got["repeats"].get("repeats") or []
             if overlap(a, b, float(x["s"]), float(x["e"])) >= min(b - a, float(x["e"]) - float(x["s"])) / 2]

    # the rules: matched here, in memory; only the rule's task leaves this function
    ruled: dict = {}
    for it in (got["activity"].get("items") or []) if not got["activity"].get("error") else []:
        o = overlap(a, b, float(it["s"]), float(it["e"]))
        if o <= 0 or it.get("cat") == "personal":
            continue
        text = f"{it.get('app') or ''} {it.get('title') or ''} {it.get('workspace') or ''} {it.get('host') or ''} {it.get('url') or ''}".lower()
        r = next((r for r in cfg.get("rules") or [] if str(r.get("match") or "").lower() and str(r["match"]).lower() in text), None)
        if r:
            t = ruled.setdefault(r["taskId"], {"taskId": r["taskId"], "title": r.get("title"), "project": r.get("project"), "m": 0.0})
            t["m"] += o
    rule_tasks = [task_out(enrich(t), f"a rule matched {mins(t['m'])} min of this time")
                  for t in sorted(ruled.values(), key=lambda t: -t["m"])]

    groups, seen_ids = [], set()
    for label, tasks in ((lead_label, lead), ("Usually at this time", usual), ("From your rules, in this time", rule_tasks),
                         ("You logged to these lately", [task_out(t, f"logged {t.get('count')} times, last {t.get('last')}")
                                                         for t in recent[:10]]),
                         ("Assigned to you", [task_out(t) for t in (sug.get("assigned") or [])[:10]])):
        kept = [t for t in tasks if t["taskId"] and t["taskId"] not in seen_ids]
        seen_ids.update(t["taskId"] for t in kept)
        if kept:
            groups.append({"label": label or "", "tasks": kept})
    warnings: list = [_short(sug["historyError"])] if sug.get("historyError") else []
    entries = _entries(got["entries"], warnings)
    no_start = [x for x in entries if x.get("s") is None]
    return {"date": date, "start": hm(a), "end": hm(b), "minutes": b - a,
            "picked": (lead or rule_tasks or [None])[0],
            "groups": groups,
            "overlapsLogged": [entry_out(x) for x in entries if x.get("s") is not None and float(x["s"]) < b and float(x["e"]) > a],
            "warnings": warnings + ([f"{len(no_start)} entries this day have no start time; the overlap check cannot see them."]
                                    if no_start else [])}


# ---- pulsar_tasks --------------------------------------------------------------------------------------------

def pulsar_tasks(args: dict) -> dict:
    cfg = _config()
    date = _date_arg(args, cfg)
    query = str(args.get("query") or "").strip()[:80]
    calls = {"suggest": ("/api/tasks/suggest", {"date": _history_date(date)})}
    if len(query) >= 2:
        calls["search"] = ("/api/tasks/search", {"q": query})
    got = fetch(calls)
    sug = got["suggest"] if not got["suggest"].get("error") else {}
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) >= 2]

    def hit(t):
        text = f"{t.get('title') or t.get('task') or ''} {t.get('project') or ''}".lower()
        return all(w in text for w in words)

    out, seen = [], set()
    for t in sug.get("recent") or []:
        if hit(t) and t.get("taskId") not in seen:
            seen.add(t.get("taskId"))
            out.append({**task_out(t), "loggedLast": t.get("last"), "timesIn30Days": t.get("count"),
                        "minutesIn30Days": t.get("minutes"), "group": "you logged to it lately (service: the one you used last)"})
    found = [t for t in (got.get("search") or {}).get("tasks") or []
             if not str(t.get("title") or "").endswith("(closed)") and str(t.get("status") or "").lower() != "closed"]
    for t in (sug.get("assigned") or []) + found:
        if (not words or hit(t) or t in found) and t.get("taskId") not in seen:
            seen.add(t.get("taskId"))
            item = task_out(t)
            item.pop("serviceId", None)  # a task's own service may not take time on the date: pulsar_preview picks one
            out.append({**item, "group": "assigned to you" if t in (sug.get("assigned") or []) else "Productive search"})
    warnings = [_short(v.get("error")) for v in got.values() if isinstance(v, dict) and v.get("error")]
    warnings += [_short(sug["historyError"])] if sug.get("historyError") else []
    return {"query": query, "tasks": out[:15], "more": max(0, len(out) - 15), "closedLeftOut": True, "warnings": warnings}


# ---- pulsar_preview, pulsar_log -----------------------------------------------------------------------------

_pending: dict = {}  # code -> {date, run, entries, summary, until}: what pulsar_preview checked
_pending_lock = threading.Lock()


def _clean(args: dict) -> tuple:
    """(date, [entry]) checked: start HH:MM, whole minutes, ids, notes; no two entries on the same minutes."""
    cfg = _config()
    date = _date_arg(args, cfg)
    if date > _today(cfg.get("timezone") or ""):
        raise ToolError(f"{date} has not begun yet: Pulsar logs only days that have. Nothing was sent.")
    raw = args.get("entries")
    if not isinstance(raw, list) or not raw:
        raise ToolError("entries is a list of {start, minutes, taskId, serviceId?, note?}.")
    if len(raw) > MOST_ENTRIES:
        raise ToolError(f"At most {MOST_ENTRIES} entries at a time.")
    out = []
    for i, e in enumerate(raw, 1):
        if not isinstance(e, dict):
            raise ToolError(f"Entry {i} is not an object.")
        a, b = _stretch(e)
        task, service = str(e.get("taskId") or ""), str(e.get("serviceId") or "")
        if not ID.match(task):
            raise ToolError(f"Entry {i}: taskId is a Productive task id (digits).")
        if service and not ID.match(service):
            raise ToolError(f"Entry {i}: serviceId is a Productive service id (digits).")
        out.append({"n": i, "start": hm(a), "minutes": b - a, "s": a, "e": b, "taskId": task, "serviceId": service or None,
                    "note": re.sub(r"\s+", " ", str(e.get("note") or "")).strip()[:2000]})
    ordered = sorted(out, key=lambda x: x["s"])
    for x, y in zip(ordered, ordered[1:]):
        if y["s"] < x["e"]:
            raise ToolError(f"Entries {x['n']} and {y['n']} overlap ({x['start']} {x['minutes']} min, "
                            f"{y['start']} {y['minutes']} min). Nothing was sent.")
    return date, out


def _resolve_services(date: str, entries: list) -> dict:
    """{n: problem} after setting each entry's service: the one asked for, if it takes time on that date; else the one
    you used last on the task, one of the same name in a newer budget, the task's own, or the only one (as the log
    dialog does). Also the task's name, project, service name, budget and billable, for the preview."""
    from .dayplan import pick_service
    tasks = sorted({e["taskId"] for e in entries})
    calls = {"suggest": ("/api/tasks/suggest", {"date": _history_date(date)})}  # the service used last, before the day
    calls.update({f"svc:{t}": ("/api/services", {"task": t, "date": date}) for t in tasks})
    got = fetch(calls)
    recent = {t.get("taskId"): t for t in (got["suggest"].get("recent") or [])}
    problems: dict = {}
    for e in entries:
        b = got[f"svc:{e['taskId']}"]
        if b.get("error"):
            problems[e["n"]] = f"Productive: {_short(b['error'])}"
            continue
        task, services = b.get("task") or {}, b.get("services") or []
        e["task"], e["project"] = str(task.get("title") or ""), str(task.get("project") or "")
        last = recent.get(e["taskId"]) or {}
        if e["serviceId"]:
            svc = next((s for s in services if str(s.get("id")) == e["serviceId"]), None)
            if not svc:
                problems[e["n"]] = "that service does not take time on this task's project on that date"
        else:
            svc = pick_service(services, last.get("serviceId"), last.get("service") or "", task.get("serviceId"))
            if not svc:
                problems[e["n"]] = "pick a service: " + "; ".join(
                    f"{s.get('id')} {str(s.get('name') or '').strip()} ({s.get('budget') or ''})" for s in services[:8])
        if svc:
            e.update({"serviceId": str(svc["id"]), "service": str(svc.get("name") or "").strip(),
                      "budget": str(svc.get("budget") or ""), "billable": svc.get("billable")})
        if (task.get("title") or "").endswith("(closed)"):
            problems.setdefault(e["n"], "the task is closed")
    return problems


def _preview(e: dict, extra: dict | None = None) -> dict:
    out = {"n": e["n"], "start": e["start"], "end": hm(e["e"]), "minutes": e["minutes"], "taskId": e["taskId"],
           "task": e.get("task") or "", "project": e.get("project") or "", "serviceId": e.get("serviceId"),
           "service": e.get("service") or "", "budget": e.get("budget") or "", "billable": e.get("billable"),
           "note": e["note"]}
    return {**out, **(extra or {})}


DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _one_line(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def summary_line(date: str, e: dict) -> str:
    """One entry as the person approves it, in Pulsar's words: 'Fri 6 Nov 2026 09:00-09:45 (45 min): Build (Acme), Dev
    in Acme 2026, billable. Note: "…"'. The names are Productive's, from the dry run; only the note is the model's,
    quoted (it cannot hold a straight double quote)."""
    d = datetime.strptime(date, "%Y-%m-%d")
    project = _one_line(e.get("project"))
    billing = {True: "billable", False: "not billable"}.get(e.get("billable"), "billing not known")
    note = f'Note: "{e["note"]}"' if e["note"] else "No note."
    budget = _one_line(e.get("budget"))
    return (f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year} {e['start']}-{hm(e['e'])} ({e['minutes']} min): "
            f"{_one_line(e.get('task')) or 'task ' + e['taskId']}" + (f" ({project})" if project else "")
            + f", {_one_line(e.get('service'))}" + (f" in {budget}" if budget else "") + f", {billing}. {note}")


def pulsar_preview(args: dict) -> dict:
    """The dry run: services resolved, notes checked, every entry through POST /api/log with "dry" (Pulsar's overlap
    check and journal). Nothing is written. With no problem, a code that holds exactly these entries, and the summary
    pulsar_log must be given back."""
    from .askclaude import guard_note
    date, entries = _clean(args)
    problems = _resolve_services(date, entries)
    for e in entries:
        if e["note"]:
            ok, why = guard_note(e["note"])
            if ok and '"' in e["note"]:
                ok, why = "", 'a straight double quote (")'
            if not ok:
                problems.setdefault(e["n"], f"the note has {why}: clients may see notes, so write the outcome in "
                                            "plain words, or leave it empty")
    run = f"ld-{date}-{secrets.token_hex(6)}"
    entries.sort(key=lambda x: x["s"])
    previews = []
    for e in entries:
        found = [problems[e["n"]]] if e["n"] in problems else []
        if e.get("serviceId") and e.get("task"):  # Pulsar's own checks too, so every problem shows at once
            got = post("/api/log", {"date": date, "start": e["start"], "minutes": e["minutes"], "taskId": e["taskId"],
                                    "serviceId": e["serviceId"], "note": e["note"], "run": run, "row": f"mcp-{e['n']}",
                                    "dry": True})
            if got.get("error"):
                found.append(_short(got["error"]))
            elif got.get("overlap") and not ignore_logged():  # the test switch's day has no entries: never show the real ones
                found.append("it would overlap an entry already logged: " + "; ".join(
                    f"{o.get('start')} {o.get('minutes')} min {o.get('project')}" for o in got["overlap"]))
        previews.append(_preview(e, {"problem": "; ".join(found)} if found else None))
    out = {"date": date, "entries": previews, "minutes": sum(e["minutes"] for e in entries)}
    if any(p.get("problem") for p in previews):
        return {**out, "code": None, "summary": None,
                "next": "Nothing can be logged until every problem is fixed. Fix them and preview again."}
    summary = "\n".join(summary_line(date, e) for e in entries)
    code, now = secrets.token_hex(4), time.time()
    with _pending_lock:
        for k in [k for k, v in _pending.items() if v["until"] < now]:
            _pending.pop(k, None)
        while len(_pending) >= 50:  # previews nobody logged: the oldest goes
            _pending.pop(min(_pending, key=lambda k: _pending[k]["until"]))
        _pending[code] = {"date": date, "run": run, "entries": entries, "summary": summary, "until": now + CONFIRM_FOR}
    return {**out, "code": code, "summary": summary,
            "next": ("Show the person this summary as it is and ask whether to log it, then stop and wait for their answer. "
                     "Call pulsar_log only after they say yes in a new message, with this code and this summary unchanged; "
                     "never in the same turn as this preview, unless they already said yes to exactly this summary. Claude "
                     "Code also asks them before pulsar_log runs, and nothing is written if they say no. The code works "
                     "once, for 10 minutes. Notes may be shown to clients.")}


def pulsar_log(args: dict, client: str = "") -> dict:
    """Writes what a pulsar_preview code holds, one entry at a time through POST /api/log (a fresh read of the day,
    the overlap check, the write lock, the journal), stopping at the first that is not logged, as Log the day does."""
    if client != "claude-code":
        raise ToolError(f"pulsar_log works only in Claude Code, which asks the person before every write. This app "
                        f"({_one_line(client)[:40] or 'it did not say which'}) does not, so nothing was written. Log "
                        "from Pulsar's window instead: Log the day, or the log dialog.")
    code, summary = str(args.get("code") or "").strip().lower(), args.get("summary")
    _config()  # Pulsar is open (else: "Open Pulsar"), before the code is used up
    now = time.time()
    with _pending_lock:
        for k in [k for k, v in _pending.items() if v["until"] < now]:
            _pending.pop(k, None)
        held = _pending.get(code)
        if not held:
            raise ToolError("That code is unknown or has expired (codes last 10 minutes and work once). Nothing was "
                            "written. Run pulsar_preview again.")
        if summary != held["summary"]:
            raise ToolError("summary is not Pulsar's text for this code, word for word. Nothing was written. Pass the "
                            "summary pulsar_preview returned, unchanged: it is what the person approves.")
        blocked = config.writes_blocked()  # this process's own switch, before anything is sent
        if blocked:
            raise ToolError(blocked)
        _pending.pop(code)
    date, results = held["date"], []
    unclear = " Check the Logged column; Pulsar's journal will not send it twice."
    for e in held["entries"]:
        try:
            got = post("/api/log", {"date": date, "start": e["start"], "minutes": e["minutes"], "taskId": e["taskId"],
                                    "serviceId": e["serviceId"], "note": e["note"], "run": held["run"],
                                    "row": f"mcp-{e['n']}"})
        except Exception as exc:  # Pulsar stopped answering mid-run, or an answer that is not JSON: may have happened
            why = str(exc) if isinstance(exc, ToolError) else f"{type(exc).__name__} while waiting for Pulsar's answer."
            results.append(_preview(e, {"status": "not known", "why": why + unclear}))
            break
        if got.get("created"):
            results.append(_preview(e, {"status": "logged", "entryId": str((got["created"] or {}).get("id") or ""),
                                        **({"warning": _short(got["warning"])} if got.get("warning") else {})}))
            continue
        if got.get("unclear") or int(got.get("_status") or 0) >= 500:  # sent, and no clear answer came back
            results.append(_preview(e, {"status": "not known",
                                        "why": (_short(got.get("error")) or "no clear answer") + "." + unclear}))
            break
        if got.get("overlap"):
            why = "it would overlap " + "; ".join(f"{o.get('start')} {o.get('project')}" for o in got["overlap"])
        else:
            why = _short(got.get("error")) or "no clear answer"
        results.append(_preview(e, {"status": "not logged", "why": why}))
        break  # the first one that is not logged stops the run, as Log the day does
    for e in held["entries"][len(results):]:  # after a stop: never sent
        results.append(_preview(e, {"status": "not sent"}))
    done = sum(1 for r in results if r["status"] == "logged")
    return {"date": date, "written": done, "of": len(held["entries"]), "results": results,
            "stopped": done < len(held["entries"]),
            "undo": "Pulsar's Log the day panel lists this run and can undo it." if done else ""}


# ---- pulsar_status -------------------------------------------------------------------------------------------

def pulsar_status(args: dict, client: str = "") -> dict:
    """Never fails: whether Pulsar answers, its version, whether writes are blocked and why, the date and time zone."""
    out: dict = {"pulsar": "open", "version": None, "mcpVersion": __version__}
    own = config.writes_blocked()  # this MCP server's own environment: pulsar_log checks it before sending
    blocked = [("This MCP server runs read-only (PULSAR_READ_ONLY in its environment): pulsar_log sends nothing."
                if config.read_only() else own)] if own else []
    cfg = {}
    try:
        cfg = _config()
        out["version"] = cfg.get("version")
        if cfg.get("writesBlocked"):  # Pulsar's own reason: read-only, or a Preview not allowed to write
            blocked.append(str(cfg["writesBlocked"]))
        out["serverBlocked"] = bool(cfg.get("writesBlocked"))
    except ToolError as exc:
        out.update({"pulsar": "not answering", "problem": str(exc), "next": "Ask the person to open Pulsar."})
    except Exception as exc:  # never fails: say what went wrong, without any data
        out.update({"pulsar": "not known", "problem": f"{type(exc).__name__} while asking Pulsar"})
    tz_name = str(cfg.get("timezone") or "") or config.system_timezone()
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tz_name)) if tz_name else datetime.now()
    except Exception:
        now = datetime.now()
    out.update({"writesBlocked": bool(blocked), "why": list(dict.fromkeys(blocked)),
                "logFromHere": client == "claude-code",
                **({} if client == "claude-code" else
                   {"logFromHereWhy": "pulsar_log works only in Claude Code, which asks the person before every write."}),
                "date": now.strftime("%Y-%m-%d"), "day": DAYS[now.weekday()], "time": now.strftime("%H:%M"),
                "timezone": tz_name or None})
    return out


# ---- MCP -----------------------------------------------------------------------------------------------------

DATE_SCHEMA = {"type": "string", "description": "YYYY-MM-DD, today or yesterday. Default: today in Pulsar's time zone."}
READ = {"readOnlyHint": True, "openWorldHint": False}
ENTRY_SCHEMA = {"type": "object", "required": ["start", "minutes", "taskId"], "additionalProperties": False,
                "properties": {"start": {"type": "string", "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9]$",
                                         "description": "HH:MM, 24-hour, in Pulsar's time zone"},
                               "minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
                               "taskId": {"type": "string", "description": "Productive task id"},
                               "serviceId": {"type": "string", "description": "Optional: the preview picks the service "
                                                                              "you used last on the task"},
                               "note": {"type": "string", "description": "Optional. Clients may see notes: the outcome "
                                                                         "in one or two plain sentences, or empty"}}}
TOOLS = [
    {"name": "pulsar_day", "title": "Pulsar: the day",
     "description": ("A day of work from Pulsar, the time tracker on this Mac: what is logged in Productive (project, task, "
                     "service, billable, start, minutes), the active stretches with no entry, the Likely rows (which "
                     "repository the minutes went to, with tasks learned from past logging; thisSession marks the "
                     "calling session's), meetings (times only), repeats, and totals against the target. Read only."),
     "inputSchema": {"type": "object", "properties": {"date": DATE_SCHEMA}, "additionalProperties": False},
     "annotations": {"title": "Pulsar: the day", **READ}},
    {"name": "pulsar_propose", "title": "Pulsar: propose the day's entries",
     "description": ("Pulsar's Log the day proposal for what is not logged yet: entries with start, minutes, task, "
                     "service, billable, confidence and why, the work it could not place, and active time no entry "
                     "covers. Use it to answer 'what did I not log' or 'propose my entries'. Read only."),
     "inputSchema": {"type": "object", "properties": {"date": DATE_SCHEMA}, "additionalProperties": False},
     "annotations": {"title": "Pulsar: propose the day's entries", **READ}},
    {"name": "pulsar_session", "title": "Pulsar: this Claude session",
     "description": ("The minutes Pulsar gave the calling Claude Code session on a day, each stretch with the task Pulsar "
                     "suggests and how much is already logged; notLoggedYet is ready for pulsar_preview. Use it for 'log "
                     "this session' or 'how long did this take'. Read only."),
     "inputSchema": {"type": "object", "properties": {"date": DATE_SCHEMA}, "additionalProperties": False},
     "annotations": {"title": "Pulsar: this Claude session", **READ}},
    {"name": "pulsar_suggest", "title": "Pulsar: tasks for a stretch",
     "description": ("The Productive tasks Pulsar's log dialog would offer for one stretch of a day, in its order (the "
                     "covering Likely work or meeting, usually at this time, your rules, logged lately, assigned to "
                     "you), the one it would pick first, and entries the stretch would overlap. Read only."),
     "inputSchema": {"type": "object", "required": ["start", "minutes"], "additionalProperties": False,
                     "properties": {"date": DATE_SCHEMA, "start": ENTRY_SCHEMA["properties"]["start"],
                                    "minutes": ENTRY_SCHEMA["properties"]["minutes"]}},
     "annotations": {"title": "Pulsar: tasks for a stretch", **READ}},
    {"name": "pulsar_tasks", "title": "Pulsar: find a task",
     "description": ("Productive tasks to log to: the ones you logged to in the last 30 days (with the service you used "
                     "last) and the open ones assigned to you, filtered by query; with a query, also a Productive "
                     "search by title. Read only."),
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "properties": {"query": {"type": "string", "description": "words of the task's or project's name"},
                                    "date": DATE_SCHEMA}},
     "annotations": {"title": "Pulsar: find a task", **READ}},
    {"name": "pulsar_preview", "title": "Pulsar: preview time entries",
     "description": ("Step 1 of logging time, and nothing is written: Pulsar checks the entries the way its log dialog "
                     "would. It resolves each service (the one you used last on the task when none is given), checks "
                     "notes (clients may see them), and runs every entry through Pulsar's own dry run (overlaps with "
                     "logged entries, the Log the day journal). Returns each entry with any problem and, when there is "
                     "none, a code and a summary: Pulsar's own words for exactly what would be written, one line per "
                     "entry. Show the person the summary as it is and wait for their yes, in a new message, before "
                     "pulsar_log."),
     "inputSchema": {"type": "object", "required": ["date", "entries"], "additionalProperties": False,
                     "properties": {"date": DATE_SCHEMA,
                                    "entries": {"type": "array", "minItems": 1, "maxItems": MOST_ENTRIES, "items": ENTRY_SCHEMA}}},
     "annotations": {"title": "Pulsar: preview time entries", **READ}},
    {"name": "pulsar_log", "title": "Pulsar: log time",
     "description": ("Step 2, only after the person has read pulsar_preview's summary and said yes to it in a new "
                     "message: never call it in the same turn as the preview, unless they already said yes to exactly that "
                     "summary. Writes to Productive, through Pulsar, the entries a pulsar_preview code holds (a fresh "
                     "read of the day, the overlap check, the write lock and the journal, so Log the day can undo them). "
                     "Give the code and the preview's summary unchanged: Claude Code shows the summary to the person and "
                     "asks them again before this runs, every time; if they say no, nothing is written. Works only in "
                     "Claude Code. The code works once, for 10 minutes. Each result is logged, not logged (with the "
                     "reason), not known (it may be in Productive: ask the person to look before logging it again) or "
                     "not sent (after a stop)."),
     "inputSchema": {"type": "object", "required": ["code", "summary"], "additionalProperties": False,
                     "properties": {"code": {"type": "string", "description": "The code pulsar_preview returned"},
                                    "summary": {"type": "string", "description": "The summary pulsar_preview returned, "
                                                                                 "word for word"}}},
     "annotations": {"title": "Pulsar: log time", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
     # Claude Code asks the person before every call, even in auto or bypass mode; allow rules cannot skip it, and
     # without a person to ask (dontAsk, -p) the call is denied. Other apps ignore the mark: pulsar_log refuses them.
     "_meta": {"anthropic/requiresUserInteraction": True}},
    {"name": "pulsar_status", "title": "Pulsar: status",
     "description": ("Never fails. Whether Pulsar is open on this Mac (every other tool needs it), its version, whether "
                     "writes to Productive are blocked and why (a read-only test run, Pulsar Preview), whether "
                     "pulsar_log works from this app, and today's date, time and time zone in Pulsar. Read only."),
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"title": "Pulsar: status", **READ}},
]
HANDLERS = {"pulsar_status": pulsar_status, "pulsar_day": pulsar_day, "pulsar_propose": pulsar_propose,
            "pulsar_session": pulsar_session, "pulsar_suggest": pulsar_suggest, "pulsar_tasks": pulsar_tasks,
            "pulsar_preview": pulsar_preview, "pulsar_log": pulsar_log}
WITH_CLIENT = ("pulsar_status", "pulsar_log")  # they answer differently by app
INSTRUCTIONS = ("Pulsar shows the person's working day on this Mac against their Productive time entries. Every tool "
                "needs Pulsar open; pulsar_status says whether it is, and the date and time. To answer what is not "
                "logged, or to propose entries: pulsar_propose. For the calling session: pulsar_session. To log: "
                "pulsar_preview (it writes nothing), show the person its summary as it is, ask, and stop. Call "
                "pulsar_log, with the code and that summary unchanged, only after the person says yes in a new message; "
                "never in the same turn as the preview, unless they already said yes to exactly that summary. Claude "
                "Code also asks the person before every write. Never write a note the person would not; clients may see "
                "notes.")
SERVER_INFO = {"name": "pulsar", "title": "Pulsar", "version": __version__}


def tool_result(data: dict, error: bool = False) -> dict:
    out = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, separators=(",", ":"))}],
           "isError": error}
    if not error:
        out["structuredContent"] = data
    return out


def handle(msg, state: dict):
    """One JSON-RPC message: the response, or None for a notification or a response to us.

    Both eras of the protocol. 2026-07-28 and later: no handshake; every request names its version and client in
    _meta, and is answered on its own. Earlier: initialize first, and the version and client hold for the process."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    if not isinstance(msg.get("method"), str):
        return None  # a response to a request we never send, or junk: nothing to answer
    method, mid = msg["method"], msg.get("id")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    if "id" not in msg:  # notifications/initialized, notifications/cancelled
        return None
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    modern = method == "server/discover" or (META + "protocolVersion" in meta and method != "initialize")

    def ok(result):
        if modern:
            result = {"resultType": "complete", **result, "_meta": {META + "serverInfo": SERVER_INFO}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message, data=None):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message, **({"data": data} if data else {})}}

    if modern:
        version = meta.get(META + "protocolVersion")
        if not isinstance(version, str) or not isinstance(meta.get(META + "clientCapabilities"), dict):
            return err(-32602, f"Invalid params: _meta needs {META}protocolVersion and {META}clientCapabilities")
        if version not in MODERN:
            return err(-32022, "Unsupported protocol version", {"supported": [*MODERN, *PROTOCOLS], "requested": version})
        info = meta.get(META + "clientInfo")
        client = str(info.get("name") or "") if isinstance(info, dict) else ""
    else:
        client = state.get("client", "")

    if method == "server/discover":
        return ok({"supportedVersions": [*MODERN, *PROTOCOLS], "capabilities": {"tools": {}},
                   "instructions": INSTRUCTIONS, "ttlMs": 3600000, "cacheScope": "public"})
    if method == "initialize":
        asked = str(params.get("protocolVersion") or "")
        state["protocol"] = asked if asked in PROTOCOLS else PROTOCOLS[0]
        info = params.get("clientInfo")
        state["client"] = str(info.get("name") or "") if isinstance(info, dict) else ""
        return ok({"protocolVersion": state["protocol"], "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS, **({"ttlMs": 3600000, "cacheScope": "public"} if modern else {})})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in HANDLERS:
            return err(-32602, f"Unknown tool: {name}")
        if not isinstance(args, dict):
            return err(-32602, "arguments must be an object")
        try:
            return ok(tool_result(HANDLERS[name](args, client) if name in WITH_CLIENT else HANDLERS[name](args)))
        except ToolError as exc:
            return ok(tool_result({"error": str(exc)}, error=True))
        except Exception as exc:  # never crash the server: say what went wrong, without any data
            print(f"pulsar --mcp: {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return ok(tool_result({"error": f"{type(exc).__name__} in {name}"}, error=True))
    return err(-32601, f"Method not found: {method}")


def main(port: int = 5610, stdin=None, stdout=None) -> int:
    _state["port"] = int(port)
    state: dict = {}
    inp, out = stdin or sys.stdin.buffer, stdout or sys.stdout.buffer
    for raw in inp:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = [r for r in (handle(m, state) for m in msg) if r] if isinstance(msg, list) else handle(msg, state)
        if reply:
            out.write(json.dumps(reply, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
            out.flush()
    return 0
