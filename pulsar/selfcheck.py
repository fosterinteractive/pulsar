"""How well Pulsar picks for you (Settings > Self-check): for each of your last DAYS finished days with entries, the
plan Log the day would make without that day's own entries (dayplan.gather, blind), compared minute by minute with
what you logged. For everyone, with Claude Code or without; worked out on this Mac from your own entries, never sent
anywhere. It only measures: nothing here changes what any plan picks.

Per day (minutes of the day, meetings included), kept as numbers and shown only as percents of the last DAYS days:
- logged: minutes you logged with a start; untimed: minutes logged with none (left out of the measures by the minute);
- rightTask: logged minutes where the plan had the same task; rightClient: the same project, or both the agency's
  own (attribution.internal_project); noTask: logged minutes where the plan had no task (nothing, or no task found);
- wrongClient: minutes the plan billed to a client while you logged them to another client or to internal work;
- over, under: per client (project) and day, billable minutes the plan proposed beyond what you logged billable to it,
  and what you logged billable to it that it did not propose; billableProposed, billableLogged: their totals;
- bySource: per pick source, the plan's minutes [picked, right (that task logged then), logged at all];
- from Log the day's journal (dayplan.mark keeps what was proposed, as first planned, beside what was sent): of the
  day's entries logged through Log the day, as proposed, with another task, time or service, or with no record of what
  was proposed; byHand, the entries logged another way;
- shadow: the same measures for the day planned with each rule the self-check tries and no plan anyone sees uses, from
  the same data: "activity" (dayplan TOUCH_*: an unsure pick gives way to an alternative you commented on or changed
  in Productive near it; one more read a day, your own activity) and "clients" (context.py's window picks may name a
  client's task too, not only internal ones).

The work (check) runs only when the person clicks Check (start): one run at a time, in a thread of its own, the page
polling its progress (view). It reads your entries of the last DAYS days once; a day already worked out by this
version, with the same entries, is taken from what is kept; the others are worked out after the windows of the
earlier days that are not kept yet (context.py), with one read of your entries of the LEARN_DAYS before them, your
assigned tasks and the recent tasks, and per day one tracker read and one read of your Productive activity. Its
timing (seconds, days, tracker and Productive reads) is in its result and in the error log, once per run.

What is kept: ~/.cache/pulsar/selfcheck/<date>.json, owner-only, KEEP_DAYS days: those numbers, the Pulsar version
that worked them out and a hash of the day's entries, never a task, project, client, note or window. A day a
tracker, Claude Code's sessions, the calendar or Productive did not answer for is not kept, and is worked out at the
next Check.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from datetime import datetime, timedelta

from . import __version__, attribution, config, context as context_mod, logbook, trackers

DAYS = 30         # the finished days looked at
MIN_DAYS = 5      # fewer days worked out: "still collecting"
KEEP_DAYS = 62
WAIT_LEARNING = 300  # seconds a Check waits for Log the day's own learning from past days (learn.warm) to end
FORMAT = 1
MEASURES = ("logged", "untimed", "rightTask", "rightClient", "noTask", "wrongClient", "over", "under",
            "billableLogged", "billableProposed", "proposed")
LOG_COUNTS = ("entries", "byHand", "viaLogTheDay", "asProposed", "taskChanged", "timeChanged", "serviceChanged",
              "unknown")
SHADOWS = ("activity", "clients")
RULES = (("activity", "Productive activity"), ("clients", "Client tasks by window"))
# how a plan picked each task (dayplan.candidates' sources), as the line to share names them
PICKS = {"session": "session", "lastday": "last task in project", "repeat": "repeat", "link": "task link",
         "meeting": "meeting", "words": "name words", "seen": "task page", "assigned": "assigned to you",
         "neighbour": "neighbour", "sameday": "same day", "new": "new task", "ticket": "ticket key", "rule": "rule",
         "work": "work words", "repo": "repository", "context": "window"}
N = 1440

_lock = threading.Lock()
# the Check running or last run: running, step ("windows" or "days"), done and of (its progress), last (its result)
_state: dict = {"running": False, "step": "", "done": 0, "of": 0, "last": None}


def _dir() -> str:
    return os.path.join(config.CACHE_DIR, "selfcheck")


def _shift(date: str, days: int) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


# ---- the measures (pure) -----------------------------------------------------------------------------------------

def _span(s, e) -> range:
    return range(max(0, int(s)), min(N, int(math.ceil(float(e)))))


def _same_project(e: dict, r: dict) -> bool:
    task = str(e.get("taskId") or "")
    return (bool(task) and task == str(r.get("taskId") or "")) \
        or (bool(e.get("project")) and e.get("project") == r.get("project"))


def _same_client(e: dict, r: dict) -> bool:
    return _same_project(e, r) or (attribution.internal_project(e.get("project"))
                                   and attribution.internal_project(r.get("project")))


def compare(proposed: list, logged: list) -> dict:
    """One day's measures (see the module's text). proposed: a plan's entries [{s, e, minutes, taskId, project,
    billable, source}], minutes of the day; logged: your entries [{s (None without a start), minutes, taskId,
    project, billable}]."""
    at: list = [None] * N
    for i, e in enumerate(proposed):
        for x in _span(e["s"], e["e"]):
            if at[x] is None:
                at[x] = i
    here: list = [None] * N
    out = {k: 0 for k in MEASURES}
    for r in logged:
        m = int(r.get("minutes") or 0)
        if r.get("s") is None:
            out["untimed"] += m
            continue
        for x in _span(r["s"], float(r["s"]) + m):
            here[x] = (here[x] or []) + [r]
    by_source: dict = {}
    for x in range(N):
        rs = here[x] or []
        e = proposed[at[x]] if at[x] is not None else None
        task = str((e or {}).get("taskId") or "")
        right = bool(task) and any(str(r.get("taskId") or "") == task for r in rs)
        if e is not None:
            out["proposed"] += 1
            b = by_source.setdefault(str(e.get("source") or "none") if task else "none", [0, 0, 0])
            b[0] += 1
            b[1] += right
            b[2] += bool(rs)
        if rs:
            out["logged"] += 1
            out["noTask"] += not task
            out["rightTask"] += right
            out["rightClient"] += bool(task) and any(_same_client(e, r) for r in rs)
            if task and e.get("billable") is True and not any(_same_project(e, r) for r in rs):
                out["wrongClient"] += 1
    key = lambda x: x.get("project") or "task:" + str(x.get("taskId") or "")  # noqa: E731
    planned: dict = {}
    for e in proposed:
        if e.get("taskId") and e.get("billable") is True:
            planned[key(e)] = planned.get(key(e), 0) + int(e.get("minutes") or len(_span(e["s"], e["e"])))
    billed: dict = {}
    for r in logged:
        if r.get("billable") is True:
            billed[key(r)] = billed.get(key(r), 0) + int(r.get("minutes") or 0)
    out["over"] = sum(max(0, v - billed.get(k, 0)) for k, v in planned.items())
    out["under"] = sum(max(0, v - planned.get(k, 0)) for k, v in billed.items())
    out["billableProposed"], out["billableLogged"] = sum(planned.values()), sum(billed.values())
    out["bySource"] = by_source
    return out


def acceptance(runs: list, entries: list) -> dict:
    """From Log the day's journal (the day's runs) and the day's entries: how many of them a run logged, and of those
    how many as proposed (the same task, service, start and minutes as first planned), with another task, another
    time, another service, or with no record of what was proposed; byHand: the entries no run logged."""
    present = {str(x.get("id")) for x in entries if x.get("id")}
    rows = {}
    for r in runs:
        for x in r.get("rows") or []:
            if x.get("status") in ("logged", "changed") and str(x.get("id") or "") in present:
                rows[str(x["id"])] = x
    out = {k: 0 for k in LOG_COUNTS}
    out.update(entries=len(entries), byHand=len(entries) - len(rows), viaLogTheDay=len(rows))
    for x in rows.values():
        p = x.get("proposed")
        if not isinstance(p, dict):
            out["unknown"] += 1
        elif str(p.get("taskId") or "") != str(x.get("taskId") or ""):
            out["taskChanged"] += 1
        elif p.get("start") != x.get("start") or p.get("minutes") != x.get("minutes"):
            out["timeChanged"] += 1
        elif str(p.get("serviceId") or "") != str(x.get("serviceId") or ""):
            out["serviceChanged"] += 1
        else:
            out["asProposed"] += 1
    return out


def entries_sig(entries: list) -> str:
    """A hash of a day's entries as they stand (id, start, length, task, service): it changes when the day does."""
    keyed = sorted((str(r.get("id") or ""), -1 if r.get("start") is None else float(r["start"]),
                    int(r.get("minutes") or 0), str(r.get("taskId") or ""), str(r.get("serviceId") or ""))
                   for r in entries)
    return hashlib.sha256(json.dumps(keyed).encode("utf-8")).hexdigest()[:16]


# ---- kept on this Mac ----------------------------------------------------------------------------------------------

def stored(date: str):
    try:
        with open(os.path.join(_dir(), f"{date}.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("format") == FORMAT and doc.get("date") == date \
        and isinstance(doc.get("version"), str) else None


def store(date: str, doc: dict, today: str) -> None:
    """One day's numbers, owner-only and atomic; days older than KEEP_DAYS fall out."""
    os.makedirs(_dir(), mode=0o700, exist_ok=True)
    config.write_atomic(os.path.join(_dir(), f"{date}.json"), json.dumps(doc, separators=(",", ":")))
    lo = _shift(today, -KEEP_DAYS)
    for f in os.listdir(_dir()):
        if f.endswith(".json") and f[:10] < lo:
            try:
                os.remove(os.path.join(_dir(), f))
            except OSError:
                pass


# ---- the Check -------------------------------------------------------------------------------------------------

def compute_day(date: str, s: dict, shared: dict, sig: str) -> dict | None:
    """One day's numbers, or None when the plan is not whole (a tracker, Claude Code's sessions, the calendar or the
    journal did not answer): then nothing is kept and the day is tried again later."""
    from . import dayplan, server
    began = time.time()
    entries = shared["days"][date]
    got = dayplan.gather(date, blind={"day": entries, "history": shared["history"], "assigned": shared["assigned"],
                                      "fresh": shared["fresh"], "activity": shared.get("activity")})
    why = got.get("error") or got.get("blindProblem")
    if why:  # not kept: the next Check works it out again
        return None
    try:
        runs = dayplan.runs_for(date)
    except dayplan.JournalBroken:
        return None
    doc = {"format": FORMAT, "version": __version__, "date": date, "sig": sig,
           "at": datetime.now().isoformat(timespec="seconds"), **acceptance(runs, entries)}
    if got.get("noActivity"):  # the tracker answered and saw nothing that day: kept, and left out of the measures
        doc["noActivity"] = True
    else:
        tz = config.tz(s)
        t0 = server.day_bounds(date, tz)[0]
        logged = [{"s": None if x.get("start") is None else server.clock(x["start"], t0, tz),
                   "minutes": x.get("minutes"), "taskId": x.get("taskId"), "project": x.get("project"),
                   "billable": x.get("billable")} for x in entries]
        doc.update(compare(got["entries"], logged))
        doc["shadow"] = {k: None if v is None else {m: n for m, n in compare(v, logged).items() if m in MEASURES}
                         for k, v in (got.get("shadow") or {}).items()}
    doc["took"] = round(time.time() - began, 2)
    return doc


def check(s: dict, today: str | None = None) -> dict:
    """One Check, in the calling thread (start runs it in its own): {"ok", "note", "timing": {seconds, days (worked
    out now), reused (taken from what is kept), failed (not whole: worked out at the next Check), trackerReads,
    productiveReads (requests; one may have several pages)}}. Progress goes to _state as it goes."""
    from . import dayplan, learn, productive, server
    began = time.time()
    timing = {"seconds": 0.0, "days": 0, "reused": 0, "failed": 0, "trackerReads": 0, "productiveReads": 0}

    def done(ok: bool, note: str = "") -> dict:
        timing["seconds"] = round(time.time() - began, 1)
        return {"ok": ok, "note": note, "timing": timing}
    if not s.get("person_id") or not productive.connection(s).get("kind"):
        return done(False, "Connect Productive first: the self-check compares Pulsar's plan with what you logged.")
    while learn._warming.locked() or context_mod._warming.locked():  # Log the day's learning first: plans learn from it
        if time.time() - began > WAIT_LEARNING:
            return done(False, "Pulsar is still learning from your past days. Check again in a few minutes.")
        _state["step"] = "learning"
        time.sleep(1)
    today = today or server.today(config.tz(s))
    first, last = _shift(today, -DAYS), _shift(today, -1)
    rows, problem, _, from_copy = server.productive_rows(first, last, s, bulk=True)
    timing["productiveReads"] += 1
    if problem or from_copy:
        return done(False, "Productive did not answer. Check again later.")
    days: dict = {}
    for r in rows:
        if first <= str(r.get("date") or "") <= last:
            days.setdefault(r["date"], []).append(r)
    for i in range(1, DAYS + 1):  # a day whose entries are all gone (deleted, moved) has nothing to compare any more
        d = _shift(today, -i)
        if d not in days and stored(d) is not None:
            try:
                os.remove(os.path.join(_dir(), f"{d}.json"))
            except OSError:
                pass
    sigs = {d: entries_sig(es) for d, es in days.items()}
    todo = sorted((d for d in days if (stored(d) or {}).get("sig") != sigs[d]
                   or (stored(d) or {}).get("version") != __version__), reverse=True)
    timing["reused"] = len(days) - len(todo)
    _state.update(step="days", done=timing["reused"], of=len(days))
    if not todo:
        return done(True)
    url = s.get("activitywatch_url") or config.DEFAULTS["activitywatch_url"]
    # the windows of the days before (context.py), as Log the day keeps them: a plan for a day learns from them
    missing = [d for d in (_shift(today, -i) for i in range(1, context_mod.LEARN_DAYS + 1))
               if context_mod.stored(d) is None]
    _state.update(step="windows", done=0, of=len(missing))
    with trackers.own_problems():  # past days: never the Activity header's warning for today
        for n, d in enumerate(missing, 1):
            items = dayplan.past_items(d, url)
            timing["trackerReads"] += 1
            if items is None:
                return done(False, "No tracker answered. Is ActivityWatch running? Check again then.")
            context_mod.store(d, items)
            _state["done"] = n
        lo = _shift(min(todo), -learn.LEARN_DAYS)
        history = list(rows)
        if lo < first:
            older, problem, _, from_copy = server.productive_rows(lo, _shift(first, -1), s, bulk=True)
            timing["productiveReads"] += 1
            if problem or from_copy:
                return done(False, "Productive did not answer. Check again later.")
            history = older + history
        try:  # hints, as in Log the day: without them the plan goes on, as Log the day's would
            assigned = productive.assigned_tasks(s)
        except Exception:
            assigned = None
        shared = {"days": days, "history": history, "assigned": assigned,
                  "fresh": server.new_task_source(min(todo), s)}
        timing["productiveReads"] += 2
        _state.update(step="days", done=timing["reused"], of=len(days))
        for d in todo:
            try:  # for the "activity" rule only: without it that one is left out for the day, the rest stands
                activity = productive.my_activity(d, d, s)
            except Exception:
                activity = None
            timing["productiveReads"] += 1
            timing["trackerReads"] += 1  # the day's own activity, in gather
            doc = compute_day(d, s, {**shared, "activity": activity}, sigs[d])
            if doc is None:
                timing["failed"] += 1
            else:
                store(d, doc, today)
                timing["days"] += 1
            _state["done"] += 1
    if timing["failed"]:
        return done(True, f"{timing['failed']} {'day' if timing['failed'] == 1 else 'days'} could not be worked out "
                          "now (a tracker, Claude Code's sessions or the calendar did not answer): Check again later.")
    return done(True)


def start(s: dict | None = None) -> bool:
    """The Check button: work out the last DAYS days in a thread of its own. False when one runs already (the second
    click does nothing). Its timing is logged once (Settings > Updates > Error log)."""
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, step="", done=0, of=0)

    def run():
        got = {"ok": False, "note": "", "timing": {}}
        try:
            got = check(s if s is not None else config.load())
            t = got["timing"]
            logbook.record("self-check", f"Checked in {t['seconds']} s: {t['days']} days worked out, {t['reused']} "
                           f"kept, {t['failed']} not whole; {t['trackerReads']} tracker reads, "
                           f"{t['productiveReads']} Productive requests.", kind="info", once_per=0)
        except Exception as exc:  # said in the tab and in Logs
            logbook.record("self-check", "The self-check stopped on an error.", kind="warning",
                           detail=f"{type(exc).__name__}: {exc}", once_per=600)
            got["note"] = "The check stopped on an error. Check again; the error log (Settings > Updates) has the details."
        finally:
            with _lock:
                _state.update(running=False, step="", last={**got, "at": datetime.now().isoformat(timespec="minutes")})
    threading.Thread(target=run, daemon=True, name="pulsar-selfcheck").start()
    return True


# ---- what the tab shows -----------------------------------------------------------------------------------------

def totals(docs: list) -> dict:
    """The sums of the measures over `docs`, and each shadow's over the days it has, beside the plan's own on those
    same days ("base")."""
    t: dict = {k: sum(int(x.get(k) or 0) for x in docs) for k in MEASURES}
    t["days"] = len(docs)
    t["bySource"] = {}
    for x in docs:
        for k, v in (x.get("bySource") or {}).items():
            b = t["bySource"].setdefault(k, [0, 0, 0])
            for i in range(3):
                b[i] += int(v[i] or 0)
    t["shadow"] = {}
    for name in SHADOWS:
        have = [x for x in docs if isinstance((x.get("shadow") or {}).get(name), dict)]
        t["shadow"][name] = {**{k: sum(int(x["shadow"][name].get(k) or 0) for x in have) for k in MEASURES},
                             "days": len(have),
                             "base": {k: sum(int(x.get(k) or 0) for x in have) for k in MEASURES}}
    return t


def percents(t: dict | None) -> dict | None:
    """Totals as whole percents, the only numbers meant to be passed on: the right task, the right client and no task
    of the minutes logged; billable time on another client, over and under, of the billable minutes logged. None where
    there is nothing to divide by."""
    if not t:
        return None
    pc = lambda a, b: round(100 * a / b) if b else None  # noqa: E731
    return {"rightTask": pc(t["rightTask"], t["logged"]), "rightClient": pc(t["rightClient"], t["logged"]),
            "noTask": pc(t["noTask"], t["logged"]), "otherClient": pc(t["wrongClient"], t["billableLogged"]),
            "over": pc(t["over"], t["billableLogged"]), "under": pc(t["under"], t["billableLogged"])}


def picks(t: dict | None) -> list:
    """[(rule, % right, % of the plan's minutes)] for each way the plan picked a task (PICKS), the most minutes first:
    right of the minutes it picked that you logged at all. A rule under 1% of the minutes is left out, and so are the
    minutes with no task (no task, above, says how many)."""
    by = (t or {}).get("bySource") or {}
    total = sum(b[0] for b in by.values())
    out = []
    for k, (proposed, right, logged) in by.items():
        if k == "none" or not total or not logged or 100 * proposed / total < 1:
            continue
        out.append((PICKS.get(k, k), round(100 * right / logged), round(100 * proposed / total)))
    return sorted(out, key=lambda x: (-x[2], x[0]))


def log_percents(log: dict) -> dict | None:
    """Log the day's rows as proposed, with another task, another time, another service: percents of the rows logged
    through it with a record of what was proposed."""
    known = int(log.get("viaLogTheDay") or 0) - int(log.get("unknown") or 0)
    if known <= 0:
        return None
    return {k: round(100 * int(log.get(k) or 0) / known) for k in ("asProposed", "taskChanged", "timeChanged",
                                                                   "serviceChanged")}


def share_text(version: str, month: dict | None, log: dict) -> str:
    """One short paragraph of percents to paste anywhere the person chooses: never a minute, a date, a task, a client
    or a name."""
    def part(p):
        f = lambda v: "n/a" if v is None else f"{v}%"  # noqa: E731
        return (f"right task {f(p['rightTask'])}, right client {f(p['rightClient'])}, no task {f(p['noTask'])}; "
                f"billable to another client {f(p['otherClient'])}, over {f(p['over'])}, under {f(p['under'])}")
    if not month or not month.get("days"):
        return f"Pulsar {version} self-check: no day worked out yet."
    out = [f"Pulsar {version} self-check, {month['days']} days: {part(percents(month))}."]
    lp = log_percents(log)
    if lp:
        out.append(f"Log the day rows: {lp['asProposed']}% as proposed, {lp['taskChanged']}% another task, "
                   f"{lp['timeChanged']}% another time.")
    for name, label in RULES:
        sh = (month.get("shadow") or {}).get(name) or {}
        if sh.get("days"):
            a, b = percents(sh), percents(sh["base"])
            out.append(f"Rule being tried, {label}: right task {a['rightTask']}% (now {b['rightTask']}%), "
                       f"right client {a['rightClient']}% (now {b['rightClient']}%).")
    # which way of picking a task is right how often, so a rule that fails for someone shows in their line too
    # (2026-11-11: one rule picked a client's task for a whole day of internal work; only the days showed it)
    by = picks(month)
    if by:
        out.append("Picks by rule: " + ", ".join(f"{name} {right}% right ({share}% of minutes)"
                                                 for name, right, share in by) + ".")
    return " ".join(out)


def view(s: dict | None = None, today: str | None = None) -> dict:
    """What the tab shows: whether a Check runs and how far it is, and the percents of the last DAYS days as kept
    (only percents, never minutes or names): the six measures, Log the day's rows, and each rule being tried beside the
    plan as it is on the same days; "share", the line Copy copies."""
    from . import server
    s = s if s is not None else config.load()
    today = today or server.today(config.tz(s))
    docs = [doc for i in range(1, DAYS + 1) for doc in [stored(_shift(today, -i))] if doc]
    measured = [x for x in docs if not x.get("noActivity")]
    month = totals(measured)
    log = {k: sum(int(x.get(k) or 0) for x in docs) for k in LOG_COUNTS}
    rules = []
    for name, label in RULES:
        sh = month["shadow"][name]
        if sh["days"]:
            rules.append({"name": label, "days": sh["days"], "withRule": percents(sh), "now": percents(sh["base"])})
    with _lock:
        st = {k: _state[k] for k in ("running", "step", "done", "of", "last")}
    return {**st, "connected": bool(s.get("person_id")), "days": len(measured), "minDays": MIN_DAYS,
            "older": sum(1 for x in docs if x.get("version") != __version__),
            "percents": percents(month) if measured else None, "logTheDay": log_percents(log), "rules": rules,
            "share": share_text(__version__, month, log)}
