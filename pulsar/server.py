"""The local web server: the page, its files, and a small JSON API. Listens on 127.0.0.1 only.

Reads go to the trackers and Productive. It writes your settings and caches (owner-only files), the token (keychain
or a private file), and a time entry in Productive only when you log one in the page. A POST must carry the X-Pulsar header, which a page on another site cannot
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

from . import __version__, config, likely, logbook, productive, report, trackers, updater, names

UPDATER = updater.Updater()

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


def public_config(s: dict) -> dict:
    conn = productive.connection(s)
    return {
        "version": __version__, "codename": names.codename(__version__)[0],
        "timezone": config.timezone_name(s), "timezoneSystem": config.system_timezone(), "timezoneSetting": s["timezone"],
        "targetMinutes": config.target_minutes(s), "targetHours": s["target_hours"], "personDayHours": s["person_day_hours"],
        "personalPatterns": s["personal_patterns"], "activitywatchUrl": s["activitywatch_url"], "rules": s["rules"],
        "update": {**UPDATER.public(), "skip": s["update_skip"], "asked": s["update_asked"]},
        "errors24h": sum(1 for e in logbook.read(1) if e["kind"] == "error"),
        "likely": {"setting": s["likely"], "available": likely.available(), "enabled": likely.enabled(s)},
        "productive": {"source": s["productive_source"], "active": conn["kind"], "problem": conn.get("problem"),
                       "tokenSaved": bool(config.get_token()), "orgId": s["org_id"], "orgName": s["org_name"],
                       "personId": s["person_id"], "personName": s["person_name"]},
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


def productive_rows(d0: str, d1: str, s: dict) -> tuple:
    """(rows, problem, source, from_copy). Every answer is kept per day, so when Productive cannot be reached
    the day view and the report show the last copy of each day, and say so, instead of nothing."""
    first, last = datetime.strptime(d0, "%Y-%m-%d"), datetime.strptime(d1, "%Y-%m-%d")
    dates = [(first + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((last - first).days + 1)]
    rows, problem, source = productive.entries(d0, d1, s)
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
    return {"ok": True, "message": f"Connected as {who} through {via}: {len(rows)} entries today."}


# ---- API -----------------------------------------------------------------------------------------

def api_activity(q: dict) -> dict:
    s = config.load()
    t0, t1 = day_bounds(q["date"], config.tz(s))
    found = trackers.both(t0, t1, s["activitywatch_url"], trackers.classifier(s["personal_patterns"]))
    source = trackers.pick(found, q.get("source", "auto"))
    minutes = {k: round(sum(x["e"] - x["s"] for x in v)) for k, v in found.items()}
    tz = config.tz(s)
    if datetime.fromtimestamp(t0, tz).utcoffset() != datetime.fromtimestamp(t1 - 1, tz).utcoffset():
        for it in found[source]:  # the clocks change today: place everything by the clock, not by elapsed time
            it["s"], it["e"] = round(clock(t0 + it["s"] * 60, t0, tz), 3), round(clock(t0 + it["e"] * 60, t0, tz), 3)
    for name, problem in list(trackers.PROBLEMS.items()):
        logbook.record(f"tracker {name}", problem, kind="warning")
    return {"source": source, "minutes": minutes, "items": found[source], "problems": dict(trackers.PROBLEMS)}


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
        out.append({**{k: v for k, v in x.items() if k not in ("start", "date")},
                    "s": start, "e": None if start is None else round(start + x["minutes"], 2)})
    out.sort(key=lambda x: (x["s"] is None, x["s"] or 0))
    return {"entries": out, "source": source, "fetched": None if from_copy else datetime.now(tz).strftime("%d.%m %H:%M"),
            "warning": problem, "needsSetup": bool(problem) and source is None}


def api_proposal(q: dict) -> dict:
    s = config.load()
    if not likely.enabled(s):
        return {"disabled": True, "entries": []}
    return likely.proposal(q["date"], config.timezone_name(s), today(config.tz(s)), q.get("fresh") == "1", s)


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
    s = current_settings()
    return {"config": public_config(s), "test": test_productive(s)}


# ---- tasks, services, logging ----------------------------------------------------------------------------

_recent: dict = {}


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
    if key not in _recent or time.time() - _recent[key][0] > 600:
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
        try:
            mine = productive.assigned_tasks(s)
        except Exception:
            mine = []
        known = {t["taskId"]: t for t in mine}
        for t in recent:  # what Productive says of the tasks you logged to: due date, status, blocked
            t.update({k: known[t["taskId"]][k] for k in ("due", "status", "blocked")} if t["taskId"] in known else {})
        assigned = urgent_first([t for t in mine if t["taskId"] not in by_task], today(config.tz(s)))
        _recent[key] = (time.time(), {"recent": recent, "assigned": assigned})
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
    if body.get("dry"):
        return _log_checked(body, s, date, begin, minutes, task_id, service_id, tz, a, b)
    key = (date, start, minutes, task_id, service_id)
    with _log_lock:  # one write at a time: two tabs, or two quick clicks, cannot both pass the overlap check
        if time.time() - _just_logged.get(key, 0) < 60:
            raise ValueError("This entry was logged a moment ago. Check the Logged column before logging it again.")
        result = _log_checked(body, s, date, begin, minutes, task_id, service_id, tz, a, b)
        if "created" in result:
            _just_logged[key] = time.time()
        return result


_log_lock = threading.Lock()
_just_logged: dict = {}  # (date, start, minutes, task, service) -> when it was written


def _log_checked(body: dict, s: dict, date: str, begin, minutes: int, task_id: str, service_id: str, tz, a: float,
                 b: float) -> dict:
    existing, problem, _ = productive.entries(date, date, s)
    if problem:
        raise RuntimeError(problem)
    clashes = [x for x in existing if x.get("start") is not None and x["start"] < b and x["start"] + x["minutes"] * 60 > a]
    if body.get("dry"):  # everything up to the write, for tests: nothing reaches Productive
        return {"dry": True, "wouldWrite": {"date": date, "started_at": begin.isoformat(timespec="seconds"), "minutes": minutes,
                                            "task": task_id, "service": service_id},
                "overlap": [{"start": datetime.fromtimestamp(x["start"], tz).strftime("%H:%M"), "minutes": x["minutes"],
                             "project": x["project"]} for x in clashes]}
    if clashes and not body.get("force"):
        return {"overlap": [{"start": datetime.fromtimestamp(x["start"], tz).strftime("%H:%M"), "minutes": x["minutes"],
                             "project": x["project"], "task": x["task"]} for x in sorted(clashes, key=lambda x: x["start"])]}
    created = productive.create_entry({"date": date, "minutes": minutes, "started_at": begin.isoformat(timespec="seconds"),
                                       "note": str(body.get("note") or "")[:2000], "task_id": task_id,
                                       "service_id": service_id}, s)
    _recent.clear()
    return {"created": created}


# ---- HTTP --------------------------------------------------------------------------------------------

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

    def signed_in(self) -> bool:
        """The page's session token (from localStorage, in a header no other site or port ever gets), or the launcher
        showing the key itself."""
        return config.valid_session(self.headers.get("X-Pulsar-Session") or "") or \
            config.is_key(self.headers.get("X-Pulsar-Key") or "")

    def do_GET(self):
        if not self.local():
            return self.send(403, b"forbidden", "text/plain")
        url = urlparse(self.path)
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
            routes = {"/api/activity": api_activity, "/api/entries": api_entries, "/api/proposal": api_proposal,
                      "/api/tasks/suggest": api_suggest, "/api/services": api_services}
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
            if urlparse(self.path).path == "/api/quit":
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return self.send_json({"ok": True})
            return self.send(404, b"not found", "text/plain")
        except (ValueError, RuntimeError, productive.ProductiveError) as exc:
            if not isinstance(exc, ValueError):  # Productive refused or did not answer: worth a line in Logs
                logbook.record(f"POST {urlparse(self.path).path}", str(exc), kind="warning")
            return self.send_json({"error": str(exc)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # never drop the connection: say what went wrong
            logbook.record(f"POST {urlparse(self.path).path}", f"{type(exc).__name__}: {exc}", detail=traceback.format_exc())
            return self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def file(self, path: str):
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            return self.send(404, b"not found", "text/plain")
        return self.send(200, data, TYPES.get(os.path.splitext(path)[1], "application/octet-stream"))


def serve(port: int) -> ThreadingHTTPServer:
    config.ensure_dirs()
    config.session_secret()  # created before anyone needs it (the launcher reads it)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    UPDATER.start()
    return httpd
