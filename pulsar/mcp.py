"""Pulsar for Claude Code: an MCP server over the running Pulsar's local API.

    python3 pulsar.py --mcp [--port 5610]
    claude mcp add pulsar -- /usr/bin/python3 -E -s -B "$HOME/Library/Application Support/Pulsar/current/pulsar.py" --mcp

MCP over stdio: JSON-RPC 2.0, one message per line (the MCP SDK needs Python 3.10+; this is the standard library).

Tools (all read only, except pulsar_log):
  pulsar_day(date)                     the day in numbers: logged, active stretches with no entry, Likely, meetings
  pulsar_propose(date)                 Log the day's proposal: entries with task, service and confidence
  pulsar_session(date)                 the calling Claude Code session's own minutes, with the task Pulsar suggests
  pulsar_suggest(date, start, minutes) the tasks the log dialog would offer for one stretch
  pulsar_tasks(query)                  your recent tasks (with the service you used last) and a Productive search
  pulsar_log(date, entries, confirm)   a dry run with a confirm code; with the code, the entries are written

What it never returns: window titles, apps, sites or addresses, typed messages, meeting titles, the titles of other
Claude sessions, notes of logged entries, rule words. Every answer is built field by field from an allow-list; nothing
from Pulsar is passed through as is. The calling session's own title is returned by pulsar_session (it is that session).

How it reaches Pulsar: 127.0.0.1 only, no proxy, no redirects. Before it shows the key (~/.config/pulsar/secret, as
X-Pulsar-Key) it checks /api/whoami the way the launcher does, so a program that is not this user's Pulsar never sees
the key. Writes go through POST /api/log, the log dialog's path: a fresh read of the day, the overlap check, the write
lock, the Log the day journal (no entry sent twice) and config.writes_blocked() (PULSAR_READ_ONLY, Preview).
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

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")  # newest first
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
_state = {"port": 5610, "verified": False}


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
        return {"error": _short(got.get("error")) or f"HTTP {exc.code}", "_status": exc.code}
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        raise ToolError(f"Pulsar is not answering on 127.0.0.1:{_state['port']}. Open Pulsar, then ask again. "
                        f"({type(getattr(exc, 'reason', exc)).__name__})")


def _verify() -> str:
    """The launcher's check: the server proves it holds this user's key (an HMAC of a fresh nonce) before it is shown
    the key. Once per process; a restart of Pulsar keeps the same key."""
    key = _key()
    if not _state["verified"]:
        nonce = secrets.token_hex(16)
        proof = str(_request("/api/whoami", {"nonce": nonce}, timeout=5).get("proof") or "")
        want = hmac.new(key.encode(), nonce.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(proof.encode(), want.encode()):
            raise ToolError(f"Something on 127.0.0.1:{_state['port']} answered, but it is not this user's Pulsar. "
                            "Nothing was sent.")
        _state["verified"] = True
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
    """Test switch: plan a day as if nothing were logged (the server accepts it only when it is read-only)."""
    return bool(os.environ.get("PULSAR_MCP_IGNORE_LOGGED"))


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
    """Pulsar's reasons are made of repository names, counts and durations, except those that quote a meeting's title
    or Ask Claude's own words: those parts are replaced."""
    why = str(why or "")
    why = re.sub(r";? ?on your calendar as “[^”]*”", "; on your calendar", why)
    if "the title says" in why:
        return "a word in the meeting's title is in the task's name"
    if why.startswith("Claude:"):
        return "Ask Claude's pick"
    return why[:140]


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
        for m in range(max(0, int(e["s"])), min(1440, int(e["s"]) + mins(e["minutes"]))):
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
    got = fetch({"entries": ("/api/entries", q), "activity": ("/api/activity", q), "likely": ("/api/proposal", q),
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
                cover[r.get("project") or ""] = cover.get(r.get("project") or "", 0) + o
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
        p = by_project.setdefault(str(r.get("project") or ""), {"project": str(r.get("project") or ""), "minutes": 0,
                                                                "learned": []})
        p["minutes"] += mins(r["minutes"])
        for t in r.get("learned") or []:
            if len(p["learned"]) < 3 and t.get("taskId") not in {x["taskId"] for x in p["learned"]}:
                p["learned"].append(task_out(t, safe_why(t.get("why"))))
        if mins(r["minutes"]) < 5:
            continue
        row = {"start": hm(r["s"]), "end": hm(r["e"]), "minutes": mins(r["minutes"]), "project": str(r.get("project") or ""),
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

NOTE_SOURCES = ("repeat", "commits", "claude")  # a meeting's note is its title: never returned


def plan_entry(e: dict) -> dict:
    out = {"start": hm(e["s"]), "end": hm(e["e"]), "minutes": mins(e["minutes"]),
           "taskId": str(e.get("taskId") or "") or None, "task": str(e.get("title") or ""),
           "project": str(e.get("project") or ""), "repository": str(e.get("repo") or ""),
           "serviceId": str(e.get("serviceId") or "") or None, "service": str(e.get("service") or ""),
           "billable": e.get("billable"),
           "confidence": e.get("confidence"), "pulsarWouldTick": bool(e.get("checked")),
           "why": safe_why(e.get("why")), "flags": [f for f in e.get("flags") or [] if isinstance(f, str)]}
    if e.get("meeting"):
        out["meeting"] = True
    if e.get("note") and e.get("noteFrom") in NOTE_SOURCES:
        out["noteDraft"], out["noteFrom"] = str(e["note"])[:300], e["noteFrom"]
    if e.get("sent"):
        out["alreadySent"] = e["sent"]
    alts = [task_out(a) for a in e.get("alts") or [] if a.get("taskId")]
    if alts and not e.get("checked"):  # only where Pulsar is not sure
        out["alternatives"] = [{"taskId": a["taskId"], "task": a["task"], "project": a["project"]} for a in alts][:2]
    if e.get("serviceProblem"):
        out["serviceProblem"] = _short(e["serviceProblem"], 160)
    return out


def _plan(date: str) -> dict:
    params = {"date": date, **({"ignore_logged": "1"} if ignore_logged() else {})}
    got = fetch({"plan": ("/api/dayplan", params)})["plan"]
    if got.get("error") and not got.get("entries"):
        raise ToolError(f"Pulsar could not plan {date}: {_short(got['error'])}")
    return got


def pulsar_propose(args: dict) -> dict:
    cfg = _config()
    date = _date_arg(args, cfg)
    got = _plan(date)
    t = got.get("totals") or {}
    return {
        "date": date, "timezone": cfg.get("timezone"),
        "proposed": [plan_entry(e) for e in got.get("entries") or []],
        "notPlaced": [{"minutes": mins(u.get("minutes")), "taskId": u.get("task"), "project": str(u.get("project") or ""),
                       "why": safe_why(u.get("why"))} for u in got.get("unplaced") or []],
        "activeNotCovered": [{"start": hm(u["s"]), "end": hm(u["e"]), "minutes": mins(u["minutes"])}
                             for u in got.get("uncovered") or []],
        "totals": {k: t.get(k) for k in ("proposed", "checked", "logged", "active", "meetings", "notCovered", "over")},
        "how": ("Pulsar's Log the day: each minute placed once (logged entries, then meetings, then Claude sessions, then "
                "other work); proposed plus logged never exceeds active time plus meetings. pulsarWouldTick=false means "
                "Pulsar is not sure (low confidence, no task, no service, agent-only time, a meeting with no call seen)."),
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


def _session_task(row: dict, a: int, b: int, plan: list):
    """The task for one of the session's stretches: Log the day's entry over it in the same repository; else the task
    learned for the row (logged before in this session or repository); else the entry over it, whatever its work."""
    with_task = [e for e in plan if e.get("taskId") and overlap(a, b, e["s"], e["e"]) > 0]
    same = [e for e in with_task if (e.get("repo") or "") == (row.get("project") or "")]
    keys = ("taskId", "task", "project", "serviceId", "service", "billable", "confidence", "why", "noteDraft", "noteFrom")
    if same:
        p = plan_entry(max(same, key=lambda e: overlap(a, b, e["s"], e["e"])))
        return {k: p.get(k) for k in keys if p.get(k) not in (None, "")}
    learned = [t for t in row.get("learned") or [] if t.get("taskId")]
    if learned:
        return {**task_out(learned[0], safe_why(learned[0].get("why"))), "confidence": "low",
                "alternatives": [task_out(t) for t in learned[1:3]]}
    if with_task:
        p = plan_entry(max(with_task, key=lambda e: overlap(a, b, e["s"], e["e"])))
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
    got = fetch({"likely": ("/api/proposal", {**q, "wait": "1"}), "entries": ("/api/entries", {"date": date})})
    warnings: list = []
    lk = got["likely"]
    if lk.get("error") or lk.get("disabled"):
        raise ToolError("Pulsar's Likely column is " + ("switched off." if lk.get("disabled") else _short(lk["error"])))
    entries = _entries(got["entries"], warnings)
    mine = [r for r in lk.get("entries") or [] if sid in (r.get("sessions") or [])]
    plan = _plan(date) if mine else {"entries": []}
    covered = covered_minutes(entries)
    rows, to_log = [], []
    for r in sorted(mine, key=lambda r: float(r["s"])):
        a, b = int(float(r["s"])), int(round(float(r["e"])))
        suggestion = _session_task(r, a, b, plan.get("entries") or [])
        others = [x for x in r.get("sessions") or [] if x != sid]
        rows.append({"start": hm(a), "end": hm(b), "minutes": mins(r["minutes"]), "project": str(r.get("project") or ""),
                     "kind": str(r.get("kind") or ""), "withOtherSessions": len(others),
                     "alreadyLoggedMinutes": sum(1 for m in range(a, min(b, 1440)) if covered[m]),
                     **({"suggested": suggestion} if suggestion else {})})
        for s, e in runs([not covered[m] for m in range(a, min(b, 1440))], least=5):
            to_log.append({"start": hm(a + s), "minutes": e - s, "project": str(r.get("project") or ""),
                           **({"taskId": suggestion["taskId"], "task": suggestion.get("task"),
                               "serviceId": suggestion.get("serviceId")} if suggestion else {})})
    return {"date": date, "session": sid, "sessionFrom": var, "title": _session_title(full),
            "rows": rows, "minutes": sum(r["minutes"] for r in rows),
            "notLoggedYet": to_log,
            "note": ("The minutes Pulsar gave this Claude session that day (from the activity tracker and the session's "
                     "messages). notLoggedYet is ready for pulsar_log; a dry run first, then the person's yes."
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
    got = fetch({"entries": ("/api/entries", q), "activity": ("/api/activity", q), "likely": ("/api/proposal", q),
                 "calendar": ("/api/calendar", q), "repeats": ("/api/repeats", q), "suggest": ("/api/tasks/suggest", q)})
    rows = got["likely"].get("entries") or [] if not got["likely"].get("error") else []
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
                lead.append(task_out(enrich(t), safe_why(t.get("why"))))
        lead_label = f"For this work ({project})" if lead else None
    if not lead:
        best = max(((ev, overlap(a, b, float(ev["s"]), float(ev["e"]))) for ev in events
                    if not ev.get("declined") and not ev.get("all_day")), key=lambda p: p[1], default=None)
        if best and best[1] >= (b - a) / 2:
            ev = best[0]  # its title goes to Pulsar on this Mac, to match earlier meetings; it is never returned
            mt = fetch({"m": ("/api/meeting-tasks", {"date": date, "uid": ev.get("uid") or "", "title": ev.get("title") or ""})})["m"]
            lead = [task_out(enrich(t), safe_why(t.get("why"))) for t in mt.get("tasks") or []]
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
    warnings: list = []
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
    calls = {"suggest": ("/api/tasks/suggest", {"date": date})}
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
            item.pop("serviceId", None)  # a task's own service may not take time on the date: pulsar_log picks one
            out.append({**item, "group": "assigned to you" if t in (sug.get("assigned") or []) else "Productive search"})
    warnings = [_short(v.get("error")) for v in got.values() if isinstance(v, dict) and v.get("error")]
    return {"query": query, "tasks": out[:15], "more": max(0, len(out) - 15), "closedLeftOut": True, "warnings": warnings}


# ---- pulsar_log ----------------------------------------------------------------------------------------------

_pending: dict = {}  # confirm code -> {fp, date, run, entries, until}
_pending_lock = threading.Lock()


def _clean(args: dict) -> tuple:
    """(date, [entry]) checked: start HH:MM, whole minutes, ids, notes; no two entries on the same minutes."""
    cfg = _config()
    date = _date_arg(args, cfg)
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


def _fingerprint(date: str, entries: list) -> str:
    """What a confirm code stands for: the day and each entry's start, minutes, task and note (the service was
    resolved by the dry run and is kept with the code)."""
    canon = json.dumps([date] + [[e["start"], e["minutes"], e["taskId"], e["note"]] for e in entries], ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _resolve_services(date: str, entries: list) -> dict:
    """{n: problem} after setting each entry's service: the one asked for, if it takes time on that date; else the one
    you used last on the task, one of the same name in a newer budget, the task's own, or the only one (as the log
    dialog does). Also the task's name, project, service name, budget and billable, for the preview."""
    from .dayplan import pick_service
    tasks = sorted({e["taskId"] for e in entries})
    calls = {"suggest": ("/api/tasks/suggest", {"date": date})}
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


def pulsar_log(args: dict) -> dict:
    from .askclaude import guard_note
    date, entries = _clean(args)
    fp = _fingerprint(date, entries)
    code = str(args.get("confirm") or "").strip().lower()
    now = time.time()
    if code:
        with _pending_lock:
            for k in [k for k, v in _pending.items() if v["until"] < now]:
                _pending.pop(k, None)
            held = _pending.get(code)
            if not held:
                raise ToolError("That confirm code is unknown or has expired (codes last 10 minutes and work once). "
                                "Nothing was written. Run pulsar_log without confirm again.")
            if held["fp"] != fp:
                raise ToolError("These entries are not the ones the code was given for. Nothing was written. Run "
                                "pulsar_log without confirm on the entries you mean, and show the person the preview.")
            for e, kept in zip(entries, held["entries"]):
                if e["serviceId"] and e["serviceId"] != kept["serviceId"]:
                    raise ToolError(f"Entry {e['n']}: the service is not the one in the dry run. Nothing was written.")
            _pending.pop(code)
        blocked = config.writes_blocked()  # this process's own switch, before anything is sent
        if blocked:
            return {"written": 0, "results": [], "refused": blocked}
        results = []
        for e in sorted(held["entries"], key=lambda x: x["s"]):
            got = post("/api/log", {"date": date, "start": e["start"], "minutes": e["minutes"], "taskId": e["taskId"],
                                    "serviceId": e["serviceId"], "note": e["note"], "run": held["run"], "row": f"mcp-{e['n']}"})
            if got.get("created"):
                results.append(_preview(e, {"status": "logged", "entryId": str((got["created"] or {}).get("id") or "")}))
                continue
            if got.get("overlap"):
                why = "it would overlap " + "; ".join(f"{o.get('start')} {o.get('project')}" for o in got["overlap"])
            else:
                why = _short(got.get("error")) or "no clear answer"
            results.append(_preview(e, {"status": "not logged", "why": why}))
            break  # the first one that is not logged stops the run, as Log the day does
        done = sum(1 for r in results if r["status"] == "logged")
        return {"date": date, "written": done, "of": len(entries), "results": results,
                "stopped": done < len(entries),
                "undo": "Pulsar's Log the day panel lists this run and can undo it." if done else ""}

    # the dry run: services resolved, notes checked, and each entry through POST /api/log with "dry"
    problems = _resolve_services(date, entries)
    for e in entries:
        if e["note"]:
            ok, why = guard_note(e["note"])
            if not ok:
                problems.setdefault(e["n"], f"the note has {why}: clients may see notes, so write the outcome in "
                                            "plain words, or leave it empty")
    run = f"ld-{date}-{secrets.token_hex(6)}"
    previews = []
    for e in sorted(entries, key=lambda x: x["s"]):
        found = [problems[e["n"]]] if e["n"] in problems else []
        if e.get("serviceId") and e.get("task"):  # Pulsar's own checks too, so every problem shows at once
            got = post("/api/log", {"date": date, "start": e["start"], "minutes": e["minutes"], "taskId": e["taskId"],
                                    "serviceId": e["serviceId"], "note": e["note"], "run": run, "row": f"mcp-{e['n']}",
                                    "dry": True})
            if got.get("error"):
                found.append(_short(got["error"]))
            elif got.get("overlap"):
                found.append("it would overlap an entry already logged: " + "; ".join(
                    f"{o.get('start')} {o.get('minutes')} min {o.get('project')}" for o in got["overlap"]))
        previews.append(_preview(e, {"problem": "; ".join(found)} if found else None))
    bad = [p for p in previews if p.get("problem")]
    out = {"date": date, "dryRun": True, "entries": previews, "minutes": sum(e["minutes"] for e in entries)}
    if bad:
        return {**out, "confirm": None,
                "next": "Nothing can be written until every problem is fixed. Fix them and run the dry run again."}
    code = secrets.token_hex(3)
    with _pending_lock:
        _pending[code] = {"fp": fp, "date": date, "run": run, "entries": entries, "until": now + CONFIRM_FOR}
    return {**out, "confirm": code,
            "next": ("Show the person this list (time, task, service, minutes, note) and ask. Only after they say yes, "
                     "call pulsar_log again with the same date and entries and confirm=" + code + ". The code works "
                     "once, for 10 minutes. Notes may be shown to clients.")}


# ---- MCP -----------------------------------------------------------------------------------------------------

DATE_SCHEMA = {"type": "string", "description": "YYYY-MM-DD, today or yesterday. Default: today in Pulsar's time zone."}
READ = {"readOnlyHint": True, "openWorldHint": False}
ENTRY_SCHEMA = {"type": "object", "required": ["start", "minutes", "taskId"], "additionalProperties": False,
                "properties": {"start": {"type": "string", "pattern": "^([01][0-9]|2[0-3]):[0-5][0-9]$",
                                         "description": "HH:MM, 24-hour, in Pulsar's time zone"},
                               "minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
                               "taskId": {"type": "string", "description": "Productive task id"},
                               "serviceId": {"type": "string", "description": "Optional: the dry run picks the service "
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
                     "suggests and how much is already logged; notLoggedYet is ready for pulsar_log. Use it for 'log "
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
    {"name": "pulsar_log", "title": "Pulsar: log time",
     "description": ("Log time entries in Productive through Pulsar (its fresh read of the day, overlap check, write "
                     "lock and journal). Two calls. Without confirm it is a dry run: nothing is written; it returns "
                     "what would be written and a confirm code. Show that to the person. Only after they say yes, call "
                     "again with the same date and entries and confirm=<code>. An entry that would overlap one already "
                     "logged is never written."),
     "inputSchema": {"type": "object", "required": ["date", "entries"], "additionalProperties": False,
                     "properties": {"date": DATE_SCHEMA,
                                    "entries": {"type": "array", "minItems": 1, "maxItems": MOST_ENTRIES, "items": ENTRY_SCHEMA},
                                    "confirm": {"type": "string", "description": "The code the dry run returned; only "
                                                                                 "after the person said yes."}}},
     "annotations": {"title": "Pulsar: log time", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True}},
]
HANDLERS = {"pulsar_day": pulsar_day, "pulsar_propose": pulsar_propose, "pulsar_session": pulsar_session,
            "pulsar_suggest": pulsar_suggest, "pulsar_tasks": pulsar_tasks, "pulsar_log": pulsar_log}
INSTRUCTIONS = ("Pulsar shows the person's working day on this Mac against their Productive time entries. To answer what "
                "is not logged, or to propose entries: pulsar_propose. For the calling session: pulsar_session. To log: "
                "pulsar_log without confirm (a dry run), show the person the list, and only after their yes call it "
                "again with the confirm code. Never write a note the person would not; clients may see notes.")


def tool_result(data: dict, error: bool = False) -> dict:
    out = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, separators=(",", ":"))}],
           "isError": error}
    if not error:
        out["structuredContent"] = data
    return out


def handle(msg, state: dict):
    """One JSON-RPC message: the response, or None for a notification or a response to us."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    if not isinstance(msg.get("method"), str):
        return None  # a response to a request we never send, or junk: nothing to answer
    method, mid, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if "id" not in msg:  # notifications/initialized, notifications/cancelled
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        asked = str(params.get("protocolVersion") or "")
        state["protocol"] = asked if asked in PROTOCOLS else PROTOCOLS[0]
        return ok({"protocolVersion": state["protocol"], "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "pulsar", "title": "Pulsar", "version": __version__},
                   "instructions": INSTRUCTIONS})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in HANDLERS:
            return err(-32602, f"Unknown tool: {name}")
        if not isinstance(args, dict):
            return err(-32602, "arguments must be an object")
        try:
            return ok(tool_result(HANDLERS[name](args)))
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
