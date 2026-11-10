"""How well Pulsar picks for you: for each of your past days, the plan Log the day would make without that day's own
entries (dayplan.gather, blind), compared minute by minute with what you logged. For everyone, with Claude Code or
without. Worked out on this Mac, in the background, from your own entries, and never sent anywhere: Settings shows
the totals, "Copy the numbers" copies them, "Save the self-check" writes them to a file (download) for you to send if
you choose. It only measures: nothing here changes what any plan picks.

Per day (minutes of the day, meetings included):
- logged: minutes you logged with a start; untimed: minutes logged with none (left out of the measures by the minute);
- rightTask: logged minutes where the plan had the same task; rightClient: the same project, or both the agency's
  own (attribution.internal_project); noTask: logged minutes where the plan had no task (nothing, or no task found);
- wrongClient: minutes the plan billed to a client while you logged them to another client or to internal work;
- over, under: per client (project) and day, billable minutes the plan proposed beyond what you logged billable to it,
  and what you logged billable to it that it did not propose; billableProposed, billableLogged: their totals;
- bySource: per pick source (session, lastday, assigned, words, context, meeting, repeat, link…), the plan's minutes
  [picked, right (that task logged then), logged at all];
- from Log the day's journal (dayplan.mark keeps what was proposed, as first planned, beside what was sent): of the
  day's entries logged through Log the day, as proposed, with another task, time or service, or with no record of what
  was proposed (older rows, Pulsar in Claude Code); and byHand, the entries logged another way (the log dialog,
  Productive itself);
- shadow: the same measures for the day planned with each rule the self-check tries and no plan anyone sees uses,
  from the same data: "activity" (dayplan TOUCH_*: a guess gives way to an alternative you commented on or changed in
  Productive near it; one more read a day, your own activity) and "clients" (context.py's window picks may name a
  client's task too, not only internal ones).

What is kept: ~/.cache/pulsar/selfcheck/<date>.json, owner-only, KEEP_DAYS days: those numbers, the Pulsar version
that worked them out and a hash of the day's entries, never a task, project, client, note or window. A day is worked
out again when the version or the day's entries change; one a tracker, Claude Code's sessions, the calendar or
Productive did not answer for is not kept, and is tried again later.

The work (run_once), one run at a time, in a thread of its own, started by nudge while a signed-in page polls the
server (GET /api/trackers, every 30 seconds): never in tests or a read-only run (PULSAR_READ_ONLY: a replay, a demo;
Pulsar Preview runs it), never without a Productive connection, never while Log the
day's own learning (learn.warm, context.warm) runs. The next run SOON seconds later while work is left, IDLE once there
is none, LATER after something did not answer. Per run: one read of your entries of the last DAYS days (which days
changed). When a day needs working out: first the windows of the days before (context.py) not kept yet, up to READS
tracker reads; then up to READS // 2 days, with one read of your entries of the LEARN_DAYS before them, your assigned
tasks and the recent tasks (once a run), and per day one tracker read and one read of your Productive activity.
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
WEEK = 7
MIN_DAYS = 5      # fewer days worked out: "still collecting"
WEEK_MIN = 3      # days the last 7 need before they show
KEEP_DAYS = 62
READS = 6         # tracker reads a run may make: a past day's windows cost one, a day worked out two
SOON, IDLE, LATER = 300, 3600, 900  # seconds to the next run: work left, none, something did not answer
START_DELAY = 120  # after the server starts: the page's own first reads go first
FORMAT = 1
MEASURES = ("logged", "untimed", "rightTask", "rightClient", "noTask", "wrongClient", "over", "under",
            "billableLogged", "billableProposed", "proposed")
LOG_COUNTS = ("entries", "byHand", "viaLogTheDay", "asProposed", "taskChanged", "timeChanged", "serviceChanged",
              "unknown")
SHADOWS = ("activity", "clients")
N = 1440

_lock = threading.Lock()
_state = {"running": False, "next": time.time() + START_DELAY, "todo": None, "ran": None, "note": ""}
_failed: dict = {}  # date -> when it last could not be worked out: tried again, after the days that could


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


# ---- the work, in the background ---------------------------------------------------------------------------------

def compute_day(date: str, s: dict, shared: dict, sig: str) -> dict | None:
    """One day's numbers, or None when the plan is not whole (a tracker, Claude Code's sessions, the calendar or the
    journal did not answer): then nothing is kept and the day is tried again later."""
    from . import dayplan, server
    began = time.time()
    entries = shared["days"][date]
    got = dayplan.gather(date, blind={"day": entries, "history": shared["history"], "assigned": shared["assigned"],
                                      "fresh": shared["fresh"], "activity": shared.get("activity")})
    why = got.get("error") or got.get("blindProblem")
    if why:
        _state["note"] = f"{date} is tried again later: {why}"
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


def run_once(s: dict, today: str | None = None) -> int:
    """One run (see the module's text); the seconds to the next one."""
    return _run(s, today)["wait"]


def _run(s: dict, today: str | None = None, reads: int = READS, most: int | None = None,
         until: float | None = None) -> dict:
    """{"wait": seconds to the next run, "worked": days worked out and kept, "left": days still to work out (None
    before Productive answered), "note": why it stopped, or ""}. reads: the windows of the days before (context.py) a
    run may read; most: the days it may work out (else what the reads leave, two reads a day); until: no day is started
    after this time.time(). The tracker problems of its reads are its own (trackers.own_problems): reading past days
    never sets or clears the Activity header's warning for today."""
    with trackers.own_problems():
        return _run_days(s, today, reads, most, until)


def _run_days(s: dict, today: str | None, reads: int, most: int | None, until: float | None) -> dict:
    from . import dayplan, learn, productive, server
    out = {"wait": IDLE, "worked": 0, "left": None, "note": ""}

    def stop(wait: int, note: str = "") -> dict:
        _state["note"] = note or _state["note"]
        return {**out, "wait": wait, "note": note}
    if not s.get("person_id") or not productive.connection(s).get("kind"):
        return stop(IDLE, "Not connected to Productive.")
    if learn._warming.locked() or context_mod._warming.locked():  # Log the day's learning first: it is what plans learn
        return stop(SOON, "Log the day is learning from past days in the background: try again in a few minutes.")
    today = today or server.today(config.tz(s))
    first, last = _shift(today, -DAYS), _shift(today, -1)
    rows, problem, _, from_copy = server.productive_rows(first, last, s, bulk=True)
    if problem or from_copy:
        return stop(LATER, "Productive did not answer.")
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
    todo, sigs = [], {}
    for d, es in days.items():
        sigs[d] = entries_sig(es)
        doc = stored(d)
        if doc is None or doc.get("sig") != sigs[d]:
            todo.append((0, d))
        elif doc.get("version") != __version__:
            todo.append((1, d))
    # the newest first, the days that changed before a new version's, and a day that failed in the last hour after the
    # rest (a tracker that never answers for it must not hold up the others)
    todo.sort(key=lambda x: x[1], reverse=True)
    todo.sort(key=lambda x: (time.time() - _failed.get(x[1], 0) < 3600, x[0]))
    _state["todo"] = out["left"] = len(todo)
    if not todo:
        _state["note"] = ""
        return out
    url = s.get("activitywatch_url") or config.DEFAULTS["activitywatch_url"]

    def read_day(d):  # None unless a tracker read it whole: never kept empty, never logged (dayplan.past_items)
        return dayplan.past_items(d, url)
    # the windows of the days before (context.py), as Log the day keeps them: a plan for a day learns from them
    missing = [d for d in (_shift(today, -i) for i in range(1, context_mod.LEARN_DAYS + 1))
               if context_mod.stored(d) is None]
    for d in missing[:reads]:
        items = read_day(d)
        if items is None:
            return stop(LATER, "No tracker answered.")
        context_mod.store(d, items)
    if len(missing) > reads:
        return stop(SOON, "Still keeping the windows of the days before.")
    most = (reads - len(missing)) // 2 if most is None else most
    if most < 1:
        return stop(SOON)
    batch = [d for _, d in todo][:most]
    lo = _shift(min(batch), -learn.LEARN_DAYS)
    history = list(rows)
    if lo < first:
        older, problem, _, from_copy = server.productive_rows(lo, _shift(first, -1), s, bulk=True)
        if problem or from_copy:
            return stop(LATER, "Productive did not answer.")
        history = older + history
    try:  # hints, as in Log the day: without them the plan goes on, as Log the day's would
        assigned = productive.assigned_tasks(s)
    except Exception:
        assigned = None
    shared = {"days": days, "history": history, "assigned": assigned, "fresh": server.new_task_source(min(batch), s)}
    failed = 0
    for d in batch:
        if until is not None and time.time() > until:
            break
        try:  # for the "activity" shadow only: without it that one is left out for the day, the rest stands
            activity = productive.my_activity(d, d, s)
        except Exception:
            activity = None
        doc = compute_day(d, s, {**shared, "activity": activity}, sigs[d])
        if doc is None:
            failed += 1
            _failed[d] = time.time()
            continue
        store(d, doc, today)
        _failed.pop(d, None)
        out["worked"] += 1
        _state["todo"] = out["left"] = max(0, len(todo) - out["worked"])
    if failed:
        return stop(LATER, f"{failed} {'day' if failed == 1 else 'days'} could not be worked out now (a tracker, Claude "
                           "Code's sessions or the calendar did not answer) and will be tried again.")
    return {**out, "wait": SOON if out["left"] else IDLE}


def nudge(s: dict | None = None) -> bool:
    """Start a run in the background when one is due and none runs. Returns at once; never in tests, nor in a
    read-only run (a replay or a demo: its data is not one person's own days)."""
    if config.tests_offline() or config.read_only():
        return False
    with _lock:
        if _state["running"] or time.time() < _state["next"]:
            return False
        _state["running"] = True

    def run():
        wait = LATER
        try:
            wait = run_once(s if s is not None else config.load())
        except Exception as exc:  # the next run tries again; Logs says why
            logbook.record("self-check", f"How well Pulsar picks: {type(exc).__name__}: {exc}", kind="warning",
                           once_per=3600)
        finally:
            with _lock:
                _state.update(running=False, next=time.time() + wait,
                              ran=datetime.now().isoformat(timespec="minutes"))
    threading.Thread(target=run, daemon=True, name="pulsar-selfcheck").start()
    return True


REFRESH_DAYS = 10     # days pulsar_selfcheck(refresh) works out in the call: Claude Code waits two minutes for it
REFRESH_SECONDS = 45  # and no day is started after this long
REFRESH_WAIT = 20     # seconds it waits for a background run to end first


def refresh(s: dict, today: str | None = None) -> dict:
    """Pulsar in Claude Code's pulsar_selfcheck(refresh=true): the days not worked out yet, now, in the call, as a run
    does them (the windows of the days before first, all of them), up to REFRESH_DAYS days and REFRESH_SECONDS seconds.
    Waits for a background run to end, at most REFRESH_WAIT seconds. {"worked", "left", "note"}."""
    began = time.time()
    while True:
        with _lock:
            if not _state["running"]:
                _state["running"] = True
                break
        if time.time() - began > REFRESH_WAIT:
            return {"worked": 0, "left": _state["todo"], "note": "A background run is still working: ask again in a minute."}
        time.sleep(0.25)
    got = {"wait": LATER, "worked": 0, "left": _state["todo"], "note": ""}
    try:
        got = _run(s, today, reads=context_mod.LEARN_DAYS, most=REFRESH_DAYS, until=time.time() + REFRESH_SECONDS)
    except Exception as exc:  # said, and the background run tries again
        logbook.record("self-check", f"Refresh: {type(exc).__name__}: {exc}", kind="warning", once_per=3600)
        got["note"] = f"{type(exc).__name__} while working out days; the background run tries again."
    finally:
        with _lock:
            _state.update(running=False, next=time.time() + got["wait"])
    if got.get("left") and not got.get("note"):
        got["note"] = (f"{got['left']} more {'day' if got['left'] == 1 else 'days'} to work out: ask again with refresh, "
                       f"or Pulsar does them in the background, a few every five minutes.")
    return {k: got.get(k) for k in ("worked", "left", "note")}


def pending(s: dict, today: str, days: int = DAYS) -> list:
    """The days of the last `days` with entries, as Pulsar last read them (the copy of each day it keeps,
    server.productive_rows), not worked out yet for this version and these entries. Nothing is read from Productive."""
    from . import server
    out = []
    for i in range(1, days + 1):
        d = _shift(today, -i)
        kept = server.read_json(server._copy_path(d, s), None)
        rows = kept.get("rows") if isinstance(kept, dict) else None
        if not isinstance(rows, list) or not rows:
            continue
        doc = stored(d)
        if doc is None or doc.get("version") != __version__ or doc.get("sig") != entries_sig(rows):
            out.append(d)
    return out


# ---- what Settings shows, copies and saves -------------------------------------------------------------------------

def totals(docs: list) -> dict:
    """The sums of the measures over `docs`, and each shadow's over the days it has, beside the plan's own on those
    same days ("base")."""
    t: dict = {k: sum(int(x.get(k) or 0) for x in docs) for k in MEASURES}
    t["days"] = len(docs)
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


def log_percents(log: dict) -> dict | None:
    """Log the day's rows as proposed, with another task, another time, another service: percents of the rows logged
    through it with a record of what was proposed."""
    known = int(log.get("viaLogTheDay") or 0) - int(log.get("unknown") or 0)
    if known <= 0:
        return None
    return {k: round(100 * int(log.get(k) or 0) / known) for k in ("asProposed", "taskChanged", "timeChanged",
                                                                   "serviceChanged")}


def share_text(version: str, month: dict | None, week: dict | None, log: dict, rules: dict) -> str:
    """One short paragraph of percents to paste anywhere the person chooses: never a minute, a date, a task, a client
    or a name."""
    def part(p):
        f = lambda v: "n/a" if v is None else f"{v}%"  # noqa: E731
        return (f"right task {f(p['rightTask'])}, right client {f(p['rightClient'])}, no task {f(p['noTask'])}; "
                f"billable to another client {f(p['otherClient'])}, over {f(p['over'])}, under {f(p['under'])}")
    if not month or not month.get("days"):
        return f"Pulsar {version} self-check: still collecting, no day worked out yet."
    out = [f"Pulsar {version} self-check, {month['days']} days: {part(percents(month))}."]
    if week:
        out.append(f"Last {week['days']} days: {part(percents(week))}.")
    lp = log_percents(log)
    if lp:
        out.append(f"Log the day rows: {lp['asProposed']}% as proposed, {lp['taskChanged']}% another task, "
                   f"{lp['timeChanged']}% another time.")
    for name, label in (("activity", "Productive activity"), ("clients", "client tasks by window")):
        sh = (month.get("shadow") or {}).get(name) or {}
        if sh.get("days"):
            a, b = percents(sh), percents(sh["base"])
            out.append(f"Rule being tried, {label}: right task {a['rightTask']}% (now {b['rightTask']}%), "
                       f"right client {a['rightClient']}% (now {b['rightClient']}%).")
    return " ".join(out)


def summary(s: dict | None = None, today: str | None = None) -> dict:
    """The last DAYS finished days (and the last WEEK when WEEK_MIN of them are worked out): sums, never names."""
    from . import server
    s = s if s is not None else config.load()
    today = today or server.today(config.tz(s))
    docs = [doc for i in range(1, DAYS + 1) for doc in [stored(_shift(today, -i))] if doc]
    measured = [x for x in docs if not x.get("noActivity")]
    week = [x for x in measured if x["date"] >= _shift(today, -WEEK)]
    return {"version": __version__, "from": _shift(today, -DAYS), "to": _shift(today, -1),
            "days": len(measured), "noActivity": len(docs) - len(measured), "minDays": MIN_DAYS,
            "collecting": len(measured) < MIN_DAYS, "month": totals(measured),
            "week": totals(week) if len(week) >= WEEK_MIN else None, "weekDays": len(week),
            "log": {k: sum(int(x.get(k) or 0) for x in docs) for k in LOG_COUNTS},
            "older": sum(1 for x in docs if x.get("version") != __version__),
            "todo": _state["todo"], "running": _state["running"], "connected": bool(s.get("person_id")),
            "share": share_text(__version__, totals(measured), totals(week) if len(week) >= WEEK_MIN else None,
                                {k: sum(int(x.get(k) or 0) for x in docs) for k in LOG_COUNTS}, {})}


DAY_KEYS = ("date", "version", "noActivity", "took") + MEASURES + LOG_COUNTS
MEANING = {  # what each number is, in the file and in Pulsar in Claude Code's answer: read with them, never sent alone
    "logged": "minutes you logged with a start time (the measures by the minute count these)",
    "untimed": "minutes you logged with no start time, left out of the measures by the minute",
    "rightTask": "of the logged minutes, those where Pulsar's plan had the same task",
    "rightClient": "of the logged minutes, those where the plan had the same project, or both were internal",
    "noTask": "of the logged minutes, those where the plan had no task (nothing proposed, or no task found)",
    "wrongClient": "minutes the plan billed to a client while you logged them to another client or to internal work",
    "over": "per client and day, billable minutes the plan proposed beyond what you logged billable to it",
    "under": "per client and day, billable minutes you logged to it that the plan did not propose",
    "billableLogged": "billable minutes you logged", "billableProposed": "billable minutes the plan proposed",
    "proposed": "minutes the plan proposed",
    "bySource": "per pick source, the plan's minutes: [picked, right (that task logged then), logged at all]",
    "logTheDay": "the day's entries: logged through Log the day (as proposed, with another task, time or service, or "
                 "with no record of what was proposed) and added another way (byHand)",
    "shadow": "the same measures for the day planned with a rule being tried that no plan uses: activity (an unsure "
              "pick gives way to a task you updated in Productive near it), clients (work outside Claude Code may "
              "take a client's task by its window); base: the plan as it is, on the same days",
    "how": "for each past day, the plan Log the day would make without that day's own entries, compared minute by "
           "minute with what was logged, meetings included; worked out on this Mac, never sent anywhere",
}


def _day_out(doc: dict) -> dict:
    day = {k: doc[k] for k in DAY_KEYS if k in doc}
    day["bySource"] = {str(k)[:20]: [int(n) for n in v][:3] for k, v in (doc.get("bySource") or {}).items()
                       if isinstance(v, list)}
    day["shadow"] = {k: ({m: int(v.get(m) or 0) for m in MEASURES} if isinstance(v, dict) else None)
                     for k, v in (doc.get("shadow") or {}).items() if k in SHADOWS}
    return day


def report(s: dict | None = None, days: int = DAYS, today: str | None = None) -> dict:
    """Everything the self-check holds for the last `days` finished days, as "Save the self-check" writes it and Pulsar
    in Claude Code's pulsar_selfcheck answers: the version, the dates, how many days are worked out and which are
    still missing, the plan's settings that matter, the totals (and the last 7 days'), Log the day's counts and each
    day's numbers (per pick source, the rules being tried). Numbers only: no task, project, client, note, window,
    address or person; nothing to hash, so no id at all."""
    from . import dayplan, likely, server
    s = s if s is not None else config.load()
    today = today or server.today(config.tz(s))
    docs = [doc for i in range(days, 0, -1) for doc in [stored(_shift(today, -i))] if doc]
    measured = [x for x in docs if not x.get("noActivity")]
    week = [x for x in measured if x["date"] >= _shift(today, -WEEK)]
    missing = pending(s, today, days)
    adv, opts = config.advanced(s), dayplan.opts_for(s)
    return {"pulsar": __version__, "made": datetime.now().strftime("%Y-%m-%d"), "from": _shift(today, -days),
            "to": _shift(today, -1),
            "days": {"asked": days, "workedOut": len(measured), "noActivity": len(docs) - len(measured),
                     "missing": len(missing), "missingDates": missing,
                     "olderVersion": sum(1 for x in docs if x.get("version") != __version__),
                     "stillCollecting": len(measured) < MIN_DAYS, "enough": MIN_DAYS},
            "settings": {"awayAfter": opts["sitting_gap"], "roundTo": opts["grid"],
                         "memoryDays": adv.get("memory"), "joinGap": adv.get("join_gap"),
                         "outsideMin": adv.get("outside_min"), "meetingCards": adv.get("meeting_cards"),
                         "claudeCode": likely.enabled(s), "askClaude": bool(s.get("ask_claude"))},
            "totals": totals(measured),
            "last7": totals(week) if days > WEEK and len(week) >= WEEK_MIN else None,
            "logTheDay": {k: sum(int(x.get(k) or 0) for x in docs) for k in LOG_COUNTS},
            "perDay": [_day_out(x) for x in docs], "meaning": MEANING,
            "share": share_text(__version__, totals(measured),
                                totals(week) if days > WEEK and len(week) >= WEEK_MIN else None,
                                {k: sum(int(x.get(k) or 0) for x in docs) for k in LOG_COUNTS}, {})}


def brief(full: dict) -> dict:
    """What Pulsar in Claude Code's pulsar_selfcheck answers unless asked for detail: the percents and the line to
    share, and how many days they come from. No minutes, no dates of single days, no per-day numbers."""
    t, w = full["totals"], full["last7"]
    rules = {}
    for name in SHADOWS:
        sh = (t.get("shadow") or {}).get(name) or {}
        if sh.get("days"):
            rules[name] = {"days": sh["days"], "withRule": percents(sh), "now": percents(sh["base"])}
    return {"pulsar": full["pulsar"], "days": {k: full["days"][k] for k in ("asked", "workedOut", "missing",
                                                                           "stillCollecting", "enough")},
            "last30": percents(t) if t.get("days") else None, "last7": percents(w) if w else None,
            "logTheDay": log_percents(full["logTheDay"]), "rulesBeingTried": rules, "share": full["share"],
            "meaning": {"percents": "rightTask, rightClient, noTask: of the minutes logged; otherClient, over, under: "
                                    "of the billable minutes logged",
                        "how": MEANING["how"]}}


def download(s: dict | None = None, today: str | None = None) -> dict:
    """What "Save the self-check" writes: report() for the last DAYS days."""
    return report(s, DAYS, today)
