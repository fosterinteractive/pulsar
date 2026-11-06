"""Replay harness (read only). Replay Log the day and the log dialog over the days collected by collect.py / cal_collect.py, each day as if
nothing were logged yet, using only what came before it, and compare with what was logged.

    cd tools/replay && D=<a private folder>; PULSAR_REPLAY_DIR=$D XDG_CACHE_HOME=$D/xdg/cache XDG_CONFIG_HOME=$D/xdg/config PULSAR_READ_ONLY=1 \
        python3 -E -s -B replay.py <pulsar worktree>

No network: everything comes from data/. Learning reads the scratch copy of ~/.cache/pulsar/likely-days.
"""
from __future__ import annotations

import json, math, os, sys, traceback
from collections import defaultdict
from datetime import datetime, timedelta

import replay_dir

HERE = replay_dir.folder()  # data/ and results go here: required, never inside a git checkout
assert os.environ.get("PULSAR_READ_ONLY") == "1"
for v in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
    assert os.environ.get(v) and not os.path.realpath(os.environ[v]).startswith(os.path.expanduser("~/.")), v  # copies, never the real dirs
WT = sys.argv[1]
sys.path.insert(0, WT)
from pulsar import config, dayplan, learn as learning, server  # noqa: E402

assert learning.DIR.startswith(os.environ["XDG_CACHE_HOME"])
learning.warm = lambda *a, **k: None  # the stored days are all there are; never run the script in the background

_orig_entry = dayplan._entry


def _entry_with_span(m, s, e, names):  # the merged piece's real first and last minute, to see the layout's shift
    out = _orig_entry(m, s, e, names)
    out["span_s"], out["span_e"] = m["s"], m["last"] + 1
    out["part_spans"] = [(q["s"], q["last"] + 1) for q in m["parts"]]
    return out


dayplan._entry = _entry_with_span

# the words of a row's work (session titles, what was typed, commits) do not depend on what is logged: read each once
# and keep them in data/worktext.json (private: it holds typed text), so a replay reads the session files once
_WT_PATH = None
_WT: dict = {}
_orig_work_text = getattr(learning, "work_text", None)


def _work_text_kept(entry, t0, projects_dir=None):
    global _WT_PATH
    if _WT_PATH is None:
        _WT_PATH = os.path.join(DATA, "worktext.json")
        try:
            _WT.update(json.load(open(_WT_PATH, encoding="utf-8")))
        except (OSError, ValueError):
            pass
    k = f"{t0}|{entry['s']}|{entry['e']}|{'/'.join(entry.get('titles') or [])}|{'/'.join(entry.get('commits') or [])}"
    if k not in _WT:
        _WT[k] = _orig_work_text(entry, t0, projects_dir)
        _WT["__dirty"] = 1
    return _WT[k]


def save_work_text():
    if _WT.pop("__dirty", None) and _WT_PATH:
        fd = os.open(_WT_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(_WT, fh, ensure_ascii=False)


if _orig_work_text:
    learning.work_text = _work_text_kept

DATA = os.path.join(HERE, "data")
ENT = json.load(open(os.path.join(DATA, "entries.json")))
ROWS = [r for r in ENT["rows"]]
for r in ROWS:
    r["taskId"] = str(r["taskId"]) if r.get("taskId") else None
CAL = json.load(open(os.path.join(DATA, "calendar.json")))
ASSIGNED = json.load(open(os.path.join(DATA, "assigned.json")))
S = config.load()
TZ = config.tz(S)
PERSON = str(S.get("person_name") or "")
SKIP = set(server.WORD.findall(PERSON.lower()))
N = 1440
INTERNAL = lambda project: (project or "").startswith("FI-")  # noqa: E731


def ds(d, k):
    return (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=k)).strftime("%Y-%m-%d")


def t0_of(d):
    return server.day_bounds(d, TZ)[0]


def suggest_lists(date: str, assigned_snapshot: list) -> tuple:
    """server.api_suggest for `date`, the day itself left out (nothing logged yet that day)."""
    d0 = ds(date, -30)
    by_task: dict = {}
    for r in sorted((r for r in ROWS if d0 <= r["date"] < date), key=lambda r: (r["date"], r.get("start") or 0)):
        if not r.get("taskId"):
            continue
        t = by_task.setdefault(r["taskId"], {"taskId": r["taskId"], "title": r["task"], "project": r["project"],
                                             "minutes": 0, "count": 0})
        t.update({"serviceId": r.get("serviceId"), "service": r.get("service"), "last": r["date"]})
        t["minutes"] += r["minutes"]
        t["count"] += 1
    recent = sorted(by_task.values(), key=lambda t: (t["last"], t["count"]), reverse=True)
    mine = [{**a, "taskId": str(a["taskId"])} for a in assigned_snapshot]
    assigned = server.urgent_first([t for t in mine if t["taskId"] not in by_task], date)
    return recent, assigned


def load_day(date: str) -> dict | None:
    p = os.path.join(DATA, f"day-{date}.json")
    return json.load(open(p)) if os.path.exists(p) else None


def build(date: str, doc: dict, opts: dict) -> tuple:
    """(day, ctx, extras) exactly as dayplan.gather builds them, from data/ and the days before `date` only."""
    hist60 = [r for r in ROWS if ds(date, -learning.LEARN_DAYS) <= r["date"] < date]
    first28 = ds(date, -server.REPEAT_DAYS)
    hist28 = [r for r in hist60 if r["date"] >= first28]
    items = doc["activity"]["items"]
    recent, assigned = suggest_lists(date, [] if opts.get("no_assigned") else ASSIGNED)
    offered = recent + assigned
    meetings = [dict(m) for m in CAL["days"].get(date) or [] if not m.get("all_day")]
    t0, t1 = server.day_bounds(date, TZ)
    past = [m for m in CAL["moments"] if t0_of(first28) <= m["s"] < t0]
    for m in meetings:
        m["tasks"] = server.meeting_tasks(m, past, hist28, offered, SKIP)
    patterns = server.repeating(hist28, TZ, date)
    reps = server.repeat_suggestions(patterns, items, datetime.strptime(date, "%Y-%m-%d").weekday())
    rows = [dict(r) for r in doc["rows"]]
    learning.annotate({"entries": rows}, date, S, hist60, [], TZ)
    days = {d: learning._stored(d) for d in learning._days_before(date)}
    ctx = dayplan.learn(date, hist60, {d: r for d, r in days.items() if r}, t0_of, person=PERSON)
    day = {"items": items, "away": doc["activity"]["away"], "rows": rows, "logged": [],
           "meetings": [] if opts.get("no_meetings") else meetings,
           "repeats": [] if opts.get("no_repeats") else reps, "calls": doc["calls"], "rules": S.get("rules") or [],
           "assigned": assigned, "picks": {}}
    return day, ctx, {"recent": recent, "assigned": assigned, "meetings": meetings, "reps": reps, "hist60": hist60,
                      "patterns": patterns}


def truth(date: str) -> list:
    t0 = t0_of(date)
    out = []
    for r in ROWS:
        if r["date"] != date:
            continue
        s = None if r.get("start") is None else round(server.clock(r["start"], t0, TZ), 2)
        out.append({**r, "s": s, "e": None if s is None else s + r["minutes"]})
    return out


def span(a, b):
    return range(max(0, int(a)), min(N, int(math.ceil(b))))


# ---- the log dialog -----------------------------------------------------------------------------------------------

def likely_blocks(rows: list, gap: int = 15) -> list:
    """Likely blocks as the column draws them, roughly: neighbouring rows of one project."""
    out = []
    for r in sorted(rows, key=lambda r: r["s"]):
        b = next((b for b in reversed(out) if b["project"] == r.get("project") and r["s"] - b["e"] <= gap), None)
        if b and b is out[-1]:
            b["e"] = max(b["e"], r["e"])
            b["rows"].append(r)
        else:
            out.append({"project": r.get("project"), "s": r["s"], "e": r["e"], "rows": [r]})
    return out


def dialog_lead(a: float, b: float, day: dict, ex: dict, blocks: list) -> tuple:
    """(the first group's task ids, which group): a repeat's dashed card when one is at that time, else leadFor (the
    Likely block over half of [a, b), else the meeting)."""
    cover = lambda s, e: max(0.0, min(b, e) - max(a, s))  # noqa: E731
    rep = max(ex["reps"], key=lambda r: cover(r["s"], r["e"]), default=None)
    if rep and cover(rep["s"], rep["e"]) >= 0.5 * min(b - a, rep["e"] - rep["s"]):
        return [str(rep["taskId"])], "repeat"
    lk = max(blocks, key=lambda k: cover(k["s"], k["e"]), default=None)
    if lk and cover(lk["s"], lk["e"]) >= (b - a) / 2:
        rows = sorted((r for r in lk["rows"]), key=lambda r: -r.get("minutes", r["e"] - r["s"]))
        ids = list(dict.fromkeys(str(t) for r in rows for t in r.get("tasks") or []))
        learned = list(dict.fromkeys(str(t["taskId"]) for r in rows for t in r.get("learned") or []))
        if ids or learned:
            return ids + learned, "likely"
    mt = max((m for m in ex["meetings"] if not m.get("declined")), key=lambda m: cover(m["s"], m["e"]), default=None)
    if mt and cover(mt["s"], mt["e"]) >= (b - a) / 2:
        return [str(t["taskId"]) for t in mt.get("tasks") or []], "meeting"
    return [], "none"


def dialog(a: float, b: float, day: dict, ex: dict, blocks: list) -> tuple:
    """(the ordered task ids the dialog offers for [a, b), which lead group): the lead, then rules (Nick has none),
    "You logged to these lately" (10), "Assigned to you" (10), each task once."""
    lead, kind = dialog_lead(a, b, day, ex, blocks)
    order = list(dict.fromkeys(lead + [t["taskId"] for t in ex["recent"][:10]] + [t["taskId"] for t in ex["assigned"][:10]]))
    return order, kind


# ---- one day ------------------------------------------------------------------------------------------------------

def run_day(date: str, opts: dict | None = None, logged_mode: str = "none") -> dict | None:
    opts = opts or {}
    doc = load_day(date)
    tr = truth(date)
    if doc is None or not tr:
        return None
    day, ctx, ex = build(date, doc, opts)
    if opts.get("patch_day"):
        opts["patch_day"](day, ctx, ex)
    if logged_mode != "none":
        timed = sorted([r for r in tr if r["s"] is not None], key=lambda r: r["s"])
        keep = timed if logged_mode == "all" else timed[: len(timed) // 2]
        day["logged"] = [{"s": r["s"], "minutes": r["minutes"], "taskId": r["taskId"]} for r in keep]
    res = dayplan.plan(day, ctx, opts.get("plan_opts"))
    return {"date": date, "doc": doc, "day": day, "ctx": ctx, "ex": ex, "plan": res, "truth": tr}


def minute_maps(out: dict) -> dict:
    day, res, tr = out["day"], out["plan"], out["truth"]
    truth_at = [set() for _ in range(N)]
    for r in tr:
        if r["s"] is not None and r.get("taskId"):
            for x in span(r["s"], r["e"]):
                truth_at[x].add(r["taskId"])
    prop_at = [None] * N
    dup = 0
    for i, e in enumerate(res["entries"]):
        for x in span(e["s"], e["e"]):
            if prop_at[x] is not None:
                dup += 1
            prop_at[x] = i
    work = bytearray(N)
    for it in day["items"]:
        if it.get("cat") == "work":
            for x in span(it["s"], it["e"]):
                work[x] = 1
    away = bytearray(N)
    for w in day["away"]:
        for x in span(w["s"], w["e"]):
            away[x] = 1
    return {"truth": truth_at, "prop": prop_at, "dup": dup, "work": work, "away": away}


def suggest_lists_with(date: str, today_before: list, assigned_snapshot: list) -> tuple:
    """api_suggest when the entries in `today_before` are already logged that day (Log and next, in time order)."""
    d0 = ds(date, -30)
    rows = [r for r in ROWS if d0 <= r["date"] < date] + today_before
    by_task: dict = {}
    for r in sorted(rows, key=lambda r: (r["date"], r.get("start") or 0)):
        if not r.get("taskId"):
            continue
        t = by_task.setdefault(r["taskId"], {"taskId": r["taskId"], "title": r["task"], "project": r["project"],
                                             "minutes": 0, "count": 0})
        t.update({"last": r["date"]})
        t["minutes"] += r["minutes"]
        t["count"] += 1
    recent = sorted(by_task.values(), key=lambda t: (t["last"], t["count"]), reverse=True)
    mine = [{**a, "taskId": str(a["taskId"])} for a in assigned_snapshot]
    assigned = server.urgent_first([t for t in mine if t["taskId"] not in by_task], date)
    return recent, assigned


def dialog_sequential(out: dict) -> dict:
    """{index of truth entry: (order, kind)} with the day's earlier entries (by start) already logged: the "lately"
    list has them first, learned tasks learn from them, a repeat's card is gone once its task has an entry."""
    date, day, ex = out["date"], out["day"], out["ex"]
    timed = sorted([(i, r) for i, r in enumerate(out["truth"]) if r["s"] is not None], key=lambda x: x[1]["s"])
    res = {}
    for k, (i, r) in enumerate(timed):
        before = [x for _, x in timed[:k]]
        recent, assigned = suggest_lists_with(date, before, ASSIGNED)
        rows = [dict(x) for x in out["doc"]["rows"]]
        learning.annotate({"entries": rows}, date, S, ex["hist60"], before, TZ)
        done = {x["taskId"] for x in before}
        ex2 = {**ex, "recent": recent, "assigned": assigned, "reps": [p for p in ex["reps"] if str(p["taskId"]) not in done]}
        res[i] = dialog(r["s"], r["e"], {**day, "rows": rows}, ex2, likely_blocks(rows))
    return res
