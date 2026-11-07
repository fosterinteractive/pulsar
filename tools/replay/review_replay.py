"""Replay "Log the day with Claude" (pulsar/dayreview.py), read only, on the days collected for the replay: each day
planned as if nothing were logged, from the days before it only (replay.py), then FI's rules in code, then, with
--claude, Claude's review of the day's digest. Every suggestion is taken as accepted, and the day is scored against
what was really logged: the right task at the right time, the right task in the right amount, the right service.

    cd tools/replay && D=<a private folder>; PULSAR_REPLAY_DIR=$D XDG_CACHE_HOME=$D/xdg/cache XDG_CONFIG_HOME=$D/xdg/config \
        PULSAR_READ_ONLY=1 python3 -E -s -B review_replay.py <pulsar worktree> <first day> <last day> \
        [--claude haiku|sonnet] [--plan-only] [--days 2026-10-29,2026-11-03]

Claude runs with no tools at all here (no Productive), so it can only name tasks the digest names. Its answers are
kept in the private folder (review-<date>-<model>-<variant>.json) and used again unless --again. Nothing is written
anywhere else.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict

import replay as R
from pulsar import dayreview  # noqa: E402  (replay put the worktree on sys.path)

ARGS = sys.argv[2:]
first, last = ARGS[0], ARGS[1]
MODEL = ARGS[ARGS.index("--claude") + 1] if "--claude" in ARGS else None
VARIANT = "plan" if "--plan-only" in ARGS else "digest"
ONLY = set(ARGS[ARGS.index("--days") + 1].split(",")) if "--days" in ARGS else None
AGAIN = "--again" in ARGS
POOL = json.load(open(os.path.join(R.DATA, "tasks.json")))["tasks"]
POOL_BY = {t["taskId"]: t for t in POOL}


def pool_for(date: str) -> list:
    """The task pool as it was on the day: never a task created after it."""
    return [t for t in POOL if (t.get("created") or "")[:10] <= date]


def services_known(date: str, hist: list) -> dict:
    """project -> [{id, name}] from the person's own entries before the day: the stand-in for the services that take
    time on the date (the replay has no Productive). The newest id of each name."""
    out: dict = defaultdict(dict)
    for r in sorted(hist, key=lambda r: r["date"]):
        if r.get("serviceId") and r.get("project"):
            out[r["project"]][(r.get("service") or "").strip()] = str(r["serviceId"])
    return {p: [{"id": i, "name": n} for n, i in v.items()] for p, v in out.items()}


def with_services(out: dict) -> list:
    """The plan's rows with ids, their service as Log the day picks it (the one used last on the task) and the
    project's services, as gather() gives them."""
    ctx = out["ctx"]
    svcs = services_known(out["date"], out["ex"]["hist60"])
    rows = []
    for i, e in enumerate(out["plan"]["entries"]):
        r = {**e, "id": f"r{i}"}
        t = POOL_BY.get(str(r.get("taskId") or "")) or {}
        r["title"] = r.get("title") or t.get("title") or ""
        r["project"] = r.get("project") or t.get("project") or ""
        last_id, last_name = ctx["service"].get(str(r.get("taskId") or ""), (None, ""))
        r["services"] = svcs.get(r["project"], [])
        r["serviceId"], r["service"] = (str(last_id), (last_name or "").strip()) if last_id else (None, "")
        rows.append(r)
    return rows


def apply(rows: list, sugg: list, only_sure: bool = False) -> list:
    """The rows with every suggestion accepted (tasks, services, minutes, splits, unticks); questions and tasks to
    create change nothing."""
    by = {r["id"]: dict(r) for r in rows}
    extra = []
    for s in sugg:
        if only_sure and not s.get("sure"):
            continue
        r = by.get(s.get("row"))
        if not r:
            continue
        k = s["kind"]
        if k == "task":
            r.update(taskId=s["taskId"], title=s.get("title") or "", serviceId=None, service="")
            t = POOL_BY.get(s["taskId"]) or {}
            r["project"] = t.get("project") or s.get("project") or r.get("project")
        elif k == "service":
            r.update(serviceId=s["serviceId"], service=s["service"])
        elif k == "minutes":
            r["minutes"] = s["minutes"]
            r["e"] = r["s"] + s["minutes"]
        elif k == "untick":
            r["gone"] = True
        elif k == "split":
            at = r["s"]
            for j, p in enumerate(s["split"]):
                part = {**r, "id": f"{r['id']}.{j}", "taskId": p["taskId"], "title": p.get("title") or "",
                        "s": at, "e": at + p["minutes"], "minutes": p["minutes"], "serviceId": None, "service": ""}
                at += p["minutes"]
                extra.append(part)
            r["gone"] = True
    return [r for r in list(by.values()) + extra if not r.get("gone")]


def last_service(out: dict, tid: str) -> str:
    return ((out["ctx"]["service"].get(str(tid or "")) or (None, ""))[1] or "").strip()


def score(out: dict, rows: list) -> dict:
    tr = [r for r in out["truth"] if r["s"] is not None and r.get("taskId")]
    at = [None] * R.N
    for r in rows:
        for x in R.span(r["s"], r["e"]):
            at[x] = r
    right = svc_ok = svc_n = 0
    for t in tr:
        for x in R.span(t["s"], t["e"]):
            p = at[x]
            if p and str(p.get("taskId")) == str(t["taskId"]):
                right += 1
                svc_n += 1
                name = (p.get("service") or last_service(out, p["taskId"]) or "").strip()
                svc_ok += name == (t.get("service") or "").strip()
    logged = Counter()
    for t in out["truth"]:
        if t.get("taskId"):
            logged[str(t["taskId"])] += t["minutes"]
    prop = Counter()
    for r in rows:
        if r.get("taskId"):
            prop[str(r["taskId"])] += r["minutes"]
    amount = sum(min(prop[k], v) for k, v in logged.items())
    total_t = sum(t["minutes"] for t in tr)
    return {"truth": sum(logged.values()), "timed": total_t, "right_time": right, "amount": amount,
            "svc_ok": svc_ok, "svc_n": svc_n, "proposed": sum(r["minutes"] for r in rows)}


def created_match(out: dict, s: dict) -> bool:
    """A task to create is right when a task was created that day or the day before in that project and logged to
    that day."""
    d = out["date"]
    for t in out["truth"]:
        p = POOL_BY.get(str(t.get("taskId"))) or {}
        if (p.get("created") or "")[:10] in (d, R.ds(d, -1)) and (p.get("project") or "") == s.get("project"):
            return True
    return False


def day_inputs(out: dict, rows: list) -> dict:
    ev = R.evidence(out["date"]) or {}
    likely = []
    for r in out["day"]["rows"]:
        x = ev.get((round(r.get("pick_s", r["s"]), 2), round(r["e"], 2))) or {}
        likely.append({**r, "steps": x.get("steps") or [], "commits": x.get("commits") or r.get("commits") or []})
    return {"plan": {**out["plan"], "entries": rows}, "items": out["day"]["items"], "away": out["day"]["away"],
            "rows": likely, "meetings": out["ex"]["meetings"], "logged": [], "repeats": out["ex"]["reps"],
            "recent": out["ex"]["recent"], "pool": pool_for(out["date"]), "target": 480,
            "is_call": R.server.is_call, "logging_rx": R.dayplan.LOGGING}


def claude_review(out: dict, rows: list) -> dict:
    d = day_inputs(out, rows)
    doc, cut, tokens = dayreview.digest(out["date"], d)
    if VARIANT == "plan":
        doc = dayreview.plan_only(out["date"], d)
    path = os.path.join(R.HERE, f"review-{out['date']}-{MODEL}-{VARIANT}.json")
    if os.path.exists(path) and not AGAIN:
        kept = json.load(open(path))
        raw = kept["raw"]
    else:
        holder = {}

        def run(cmd, **kw):  # the real Claude Code, isolated as dayreview.review starts it; the raw answer kept
            import subprocess
            done = subprocess.run(cmd, **kw)
            holder["stdout"] = done.stdout
            return done
        try:
            res = dayreview.review(out["date"], doc, rows, run=run, model=MODEL, tools=False)
        except dayreview.ReviewError as exc:
            print(out["date"], "review failed:", exc, file=sys.stderr)
            return {"suggestions": [], "cost": exc.cost or 0, "tokens": {}, "dropped": [], "size": tokens, "cut": cut}
        raw = json.loads(holder["stdout"])
        json.dump({"raw": raw, "digest_tokens": tokens, "cut": cut}, open(path, "w"), ensure_ascii=False)
        os.chmod(path, 0o600)
    got = raw.get("structured_output") or {}
    known = dayreview.known_tasks(doc)
    pool = {t["taskId"]: t for t in pool_for(out["date"])}
    for s in got.get("suggestions") or []:  # what Productive would name for the page (task_details), as of the day
        for tid in [s.get("taskId")] + [p.get("taskId") for p in s.get("split") or [] if isinstance(p, dict)]:
            tid = str(tid or "")
            if tid in pool and not (known.get(tid) or {}).get("title"):
                known[tid] = {"title": pool[tid]["title"], "project": pool[tid].get("project") or ""}
    sugg, dropped = dayreview.validate(got.get("suggestions") or [], rows, known)
    u = raw.get("usage") or {}
    return {"suggestions": sugg, "dropped": dropped, "cost": raw.get("total_cost_usd") or 0, "size": tokens, "cut": cut,
            "tokens": {"in": (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                       + (u.get("cache_creation_input_tokens") or 0), "out": u.get("output_tokens") or 0}}


def pct(a, b):
    return f"{100 * a / b:5.1f}%" if b else "  -  "


tot = defaultdict(Counter)
kinds = Counter()
useful = Counter()
cost = 0.0
sizes = []
d = first
while d <= last:
    date, d = d, R.ds(d, 1)
    if ONLY and date not in ONLY:
        continue
    out = R.run_day(date)
    if not out:
        continue
    rows = with_services(out)
    rules = dayreview.fi_rules(rows, date, pool_for(date), {t: POOL_BY[t] for t in POOL_BY})
    for s in rules:
        kinds["rule " + s["kind"] + (" (sure)" if s.get("sure") else "")] += 1
        if s["kind"] == "create_task":
            useful["rule create_task right project"] += created_match(out, s)
    before = score(out, rows)
    after_rules = score(out, apply(rows, rules, only_sure=True))
    tot["before"].update(before)
    tot["rules"].update(after_rules)
    line = f"{date} before {pct(before['right_time'], before['timed'])} rules {pct(after_rules['right_time'], after_rules['timed'])}"
    if MODEL:
        rv = claude_review(out, apply(rows, rules, only_sure=True))
        cost += rv["cost"] or 0
        sizes.append((date, rv["size"], rv["tokens"].get("in"), rv["cut"]))
        sure_rows = apply(rows, rules, only_sure=True)
        truth_task_at = {}
        for t in out["truth"]:
            if t["s"] is not None:
                for x in R.span(t["s"], t["e"]):
                    truth_task_at[x] = str(t.get("taskId"))
        for s in rv["suggestions"]:
            kinds["claude " + s["kind"]] += 1
            r = next((r for r in sure_rows if r["id"] == s["row"]), None)
            if r:  # was the row wrong where the suggestion points?
                xs = list(R.span(r["s"], r["e"]))
                wrong = sum(1 for x in xs if truth_task_at.get(x) and truth_task_at[x] != str(r.get("taskId")))
                useful[f"claude {s['kind']} on a row that was wrong"] += wrong >= len(xs) / 2
            if s["kind"] == "create_task":
                useful["claude create_task right project"] += created_match(out, s)
        after = score(out, apply(sure_rows, rv["suggestions"]))
        tot["claude"].update(after)
        # the same, taking only the suggestions that name a task or a service (the minutes ones left to the person)
        tot["claude, tasks only"].update(score(out, apply(sure_rows, [s for s in rv["suggestions"]
                                                                     if s["kind"] in ("task", "split", "service")])))
        line += f" claude {pct(after['right_time'], after['timed'])} ({len(rv['suggestions'])} suggestions, ${rv['cost'] or 0:.3f}, digest ~{rv['size']} tokens, in {rv['tokens'].get('in')}; dropped {rv['dropped']})"
    print(line, flush=True)

print()
for k, v in tot.items():
    print(f"{k:18s} right task at the right time {pct(v['right_time'], v['timed'])} of {v['timed']} timed minutes; "
          f"right task, right amount {pct(v['amount'], v['truth'])}; right service where the task is right "
          f"{pct(v['svc_ok'], v['svc_n'])}; proposed {v['proposed']} of {v['truth']} logged")
print("suggestions:", dict(kinds))
print("useful:", dict(useful))
if MODEL:
    print(f"Claude ({MODEL}, {VARIANT}): ${cost:.3f} for {len(sizes)} days")
    for x in sizes:
        print("  digest", x)
