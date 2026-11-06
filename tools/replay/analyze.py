"""Score the replay: Log the day against what was logged, the log dialog's ranks, breakdowns, failure causes and
correctness checks. Prints a report; writes results.json.

    XDG_CACHE_HOME=... XDG_CONFIG_HOME=... PULSAR_READ_ONLY=1 python3 -E -s -B analyze.py <worktree> [--first D] [--last D]
"""
from __future__ import annotations

import json, os, re, sys, traceback
from collections import Counter, defaultdict
from datetime import datetime

import replay as R

PULSAR = re.compile(r"^pulsar(-preview)?$", re.I)
LOGGING = R.dayplan.LOGGING
EDITORS = re.compile(r"^(Cursor|Code|Visual Studio Code|Terminal|iTerm2|Warp|Claude)$", re.I)


def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


FIRST, LAST = arg("--first", "2026-09-07"), arg("--last", "2026-11-05")
SEQ = "--no-seq" not in sys.argv


EXCLUDE = set(arg("--exclude", "").split(",")) - {""}


def dates():
    out, d = [], FIRST
    while d <= LAST:
        if d not in EXCLUDE:
            out.append(d)
        d = R.ds(d, 1)
    return out


def n_repos_by_row(rows: list) -> list:
    proj_of: dict = defaultdict(set)
    for r in rows:
        for sid in r.get("sessions") or []:
            proj_of[sid].add(r.get("project") or "")
    return [max(1, len(set().union(*[proj_of[s] for s in r.get("sessions") or []]) if r.get("sessions") else 1))
            for r in rows]


def row_at(rows: list) -> list:
    """For each minute, the Likely row the planner would give it to: focused rows first, in time order."""
    at = [None] * R.N
    order = sorted(range(len(rows)), key=lambda i: (rows[i].get("kind") == "parallel", rows[i]["s"]))
    for i in order:
        for x in R.span(rows[i]["s"], rows[i]["e"]):
            if at[x] is None:
                at[x] = i
    return at


def category_of_truth(r, out, maps, rows, rat, attended_at) -> str:
    xs = list(R.span(r["s"], r["e"]))
    if xs and sum(1 for x in xs if attended_at[x]) >= 0.5 * len(xs):
        return "meeting"
    if xs and sum(1 for x in xs if rat[x] is not None and PULSAR.match(rows[rat[x]].get("project") or "")) >= 0.5 * len(xs):
        return "pulsar"
    return "internal" if R.INTERNAL(r.get("project")) else "client"


def category_of_entry(e, out) -> str:
    if e["kind"] in ("meeting", "unattended"):
        return "meeting"
    if PULSAR.match(e.get("repo") or ""):
        return "pulsar"
    if not e.get("taskId"):
        return "no task"
    proj = out["ctx"]["names"].get(e["taskId"], {}).get("project") or e.get("project") or ""
    return "internal" if R.INTERNAL(proj) else "client"


def score_day(out: dict) -> dict:
    day, res, tr, ctx, ex = out["day"], out["plan"], out["truth"], out["ctx"], out["ex"]
    maps = R.minute_maps(out)
    truth_at, prop_at, work, away = maps["truth"], maps["prop"], maps["work"], maps["away"]
    ents = res["entries"]
    rows = day["rows"]
    rat = row_at(rows)
    nrep = n_repos_by_row(rows)
    attended_at = bytearray(R.N)
    for m in ex["meetings"]:
        if not m.get("declined"):
            for x in R.span(m["s"], m["e"]):
                attended_at[x] = 1
    for c in day["calls"]:
        for x in R.span(c["s"], c["e"]):
            attended_at[x] = 1
    # amounts, time-agnostic (as the earlier logday evaluation)
    p, q = Counter(), Counter()
    for e in ents:
        p[e["taskId"] or "?"] += e["minutes"]
    for r in tr:
        q[r["taskId"] or "?"] += r["minutes"]
    P, Q = sum(p.values()), sum(q.values())
    M = sum(min(p[t], q.get(t, 0)) for t in p if t != "?")
    # minute by minute
    right_time = sum(1 for x in range(R.N) if prop_at[x] is not None and ents[prop_at[x]]["taskId"] in truth_at[x])
    timed_q = sum(len(truth_at[x]) and 1 for x in range(R.N))
    # per proposed entry
    erecs = []
    proposed_tasks = {e["taskId"] for e in ents if e["taskId"]}
    for i, e in enumerate(ents):
        xs = list(R.span(e["s"], e["e"]))
        rt = sum(1 for x in xs if e["taskId"] and e["taskId"] in truth_at[x])
        erecs.append({"date": out["date"], "s": e["s"], "e": e["e"], "minutes": e["minutes"], "task": e["taskId"],
                      "conf": e["confidence"], "source": e["source"] or "none", "kind": e["kind"], "checked": e["checked"],
                      "right_time": rt, "right_day": e["minutes"] if e["taskId"] in q else 0,
                      "cat": category_of_entry(e, out), "alts": [a["taskId"] for a in e["alts"]],
                      "nrep": Counter(nrep[rat[x]] if rat[x] is not None else 0 for x in xs).most_common(1)[0][0] if xs else 0})
    # per logged entry: the dialog, Log the day's row, failures
    blocks = R.likely_blocks(rows)
    skipped_at = bytearray(R.N)
    for sk in res["skipped"]:
        for x in R.span(sk["s"], sk["e"]):
            skipped_at[x] = 1
    logging_at = bytearray(R.N)
    for it in day["items"]:
        if it.get("cat") == "work" and any(LOGGING.search(str(it.get(k) or "")) for k in ("app", "host", "url")):
            for x in R.span(it["s"], it["e"]):
                logging_at[x] = 1
    known = set(ctx["names"])
    in_real_span = bytearray(R.N)
    for e in ents:
        for a, b in e.get("part_spans") or []:
            for x in R.span(a, b):
                in_real_span[x] = 1
    blocked_at = bytearray(R.N)
    for it in day["items"]:
        if it.get("cat") == "work" and EDITORS.search(it.get("app") or ""):
            for x in R.span(it["s"], it["e"]):
                blocked_at[x] = 1

    def near_tasks(x):
        return {ents[prop_at[y]]["taskId"] for y in range(max(0, x - 30), min(R.N, x + 31)) if prop_at[y] is not None}
    trecs = []
    causes_day = Counter()
    seq = R.dialog_sequential(out) if SEQ else {}
    for ti, r in enumerate(tr):
        rec = {"date": out["date"], "s": r["s"], "minutes": r["minutes"], "task": r["taskId"], "title": r.get("task") or "",
               "project": r.get("project") or "", "weekday": datetime.strptime(out["date"], "%Y-%m-%d").strftime("%a"),
               "known": r["taskId"] in known}
        if r["s"] is None:
            rec.update(cat="no start", right_time=0)
            causes_day["no start time in Productive"] += r["minutes"]
            trecs.append(rec)
            continue
        xs = list(R.span(r["s"], r["e"]))
        rec["cat"] = category_of_truth(r, out, maps, rows, rat, attended_at)
        rec["right_time"] = sum(1 for x in xs if prop_at[x] is not None and ents[prop_at[x]]["taskId"] == r["taskId"])
        rec["nrep"] = Counter(nrep[rat[x]] if rat[x] is not None else 0 for x in xs).most_common(1)[0][0] if xs else 0
        order, kind = R.dialog(r["s"], r["e"], day, ex, blocks)
        rec["dialog_kind"] = kind
        rec["dialog_rank"] = order.index(r["taskId"]) + 1 if r["taskId"] in order else None
        rec["dialog_n"] = len(order)
        if ti in seq:
            o2, k2 = seq[ti]
            rec["seq_kind"] = k2
            rec["seq_rank"] = o2.index(r["taskId"]) + 1 if r["taskId"] in o2 else None
        cov = Counter(prop_at[x] for x in xs if prop_at[x] is not None)
        if cov:
            ei = cov.most_common(1)[0][0]
            e = ents[ei]
            cands = list(dict.fromkeys([e["taskId"]] + [a["taskId"] for a in e["alts"]]))
            rec.update(plan_task=e["taskId"], plan_src=e["source"] or "none", plan_conf=e["confidence"],
                       plan_kind=e["kind"], plan_rank=cands.index(r["taskId"]) + 1 if r["taskId"] in cands else None)
        else:
            rec.update(plan_task=None, plan_src="nothing proposed", plan_conf=None, plan_kind=None, plan_rank=None)
        # failure causes, minute by minute
        cz = Counter()
        for x in xs:
            i = prop_at[x]
            T = r["taskId"]
            if i is not None and ents[i]["taskId"] == T:
                continue
            if i is None:
                if not work[x] and not attended_at[x]:
                    c = "no activity seen (away or off the computer)" if away[x] else "no activity seen (before/after the tracked day)"
                elif skipped_at[x]:
                    c = "set aside: a repository never logged"
                elif logging_at[x]:
                    c = "set aside: counted as time spent logging"
                elif in_real_span[x]:
                    c = "active, lost when an entry was laid out shorter than its span"
                elif blocked_at[x]:
                    c = "active in an editor or terminal with no session row"
                else:
                    c = "active but not proposed (short stretch, cap, rounding)"
            else:
                e = ents[i]
                if T in near_tasks(x):
                    c = "right task, proposed within 30 min (time offset)"
                elif T in proposed_tasks:
                    c = "right task, proposed elsewhere that day"
                elif T not in known:
                    c = "new task: not logged in the 60 days before"
                elif e["kind"] in ("meeting", "unattended"):
                    c = "meeting: wrong or no task"
                elif not e["taskId"]:
                    c = "row with no task"
                elif T in [a["taskId"] for a in e["alts"]]:
                    c = "right task among the alternatives"
                elif (ctx["names"].get(e["taskId"], {}).get("project") or "") == (r.get("project") or ""):
                    c = "right project, wrong task"
                elif e["kind"] == "outside":
                    c = "outside Claude: took a neighbour's task"
                else:
                    c = "wrong project"
            cz[c] += 1
        rec["causes"] = dict(cz)
        causes_day.update(cz)
        trecs.append(rec)
    # correctness
    checks = Counter()
    for e in ents:
        if e["e"] > R.N or e["s"] < 0:
            checks["entry past midnight"] += 1
        if e["e"] - e["s"] != e["minutes"]:
            checks["entry length differs from its span"] += 1
    for r in rows:
        if r["e"] > R.N or r["e"] < r["s"]:
            checks["Likely row past midnight"] += 1
    checks["duplicate minutes"] += maps["dup"]
    checks["Likely row past midnight"] += 0
    checks["entry past midnight"] += 0
    for e in ents:
        real = set(x for a, b in e.get("part_spans") or [] for x in R.span(a, b))
        laid = set(R.span(e["s"], e["e"]))
        checks["minutes laid out off the work they stand for"] += len(laid - real)
    night = bytearray(R.N)
    for w in day["away"]:
        if w.get("off"):
            for x in R.span(w["s"], w["e"]):
                night[x] = 1
    checks["proposed minutes in Off stretches (nights)"] += 0
    for x in range(R.N):
        if prop_at[x] is None:
            continue
        e = ents[prop_at[x]]
        if night[x]:
            checks["proposed minutes in Off stretches (nights)"] += 1
        if away[x] and not attended_at[x]:
            checks["proposed minutes on away time"] += 1
            if e["kind"] in ("focused", "agent", "outside"):
                checks["proposed work minutes on away time"] += 1
        if not work[x] and not attended_at[x]:
            checks["proposed minutes neither active nor in a meeting"] += 1
    checks["over the cap"] += 0
    if res["totals"]["proposed"] > max(0, res["totals"]["cap"] - res["totals"]["logged"]):
        checks["over the cap"] += 1
    checks["logged minutes"] += sum(1 for x in range(R.N) if truth_at[x])
    checks["logged minutes with no activity and no meeting"] += sum(1 for x in range(R.N) if truth_at[x] and not work[x] and not attended_at[x])
    return {"date": out["date"], "P": P, "Q": Q, "M": M, "right_time": right_time, "timed_q": timed_q,
            "prec_day": sum(v for t, v in p.items() if t in q and t != "?"), "rec_day": sum(v for t, v in q.items() if t in p),
            "checked_P": sum(e["minutes"] for e in ents if e["checked"]),
            "checked_right": sum(x["right_time"] for x in erecs if x["checked"]),
            "unchecked_right": sum(x["right_time"] for x in erecs if not x["checked"]),
            "entries": erecs, "truth": trecs, "causes": dict(causes_day), "checks": dict(checks),
            "totals": res["totals"], "active": res["totals"]["active"], "n_entries": len(ents), "n_truth": len(tr)}


def logged_checks(date: str, mode: str) -> Counter:
    out = R.run_day(date, logged_mode=mode)
    c = Counter({f"{mode}: proposed over logged minutes": 0, f"{mode}: proposed more than the room left under the cap": 0})
    if not out:
        return c
    busy = bytearray(R.N)
    for r in out["day"]["logged"]:
        for x in R.span(r["s"], r["s"] + r["minutes"]):
            busy[x] = 1
    for e in out["plan"]["entries"]:
        for x in R.span(e["s"], e["e"]):
            if busy[x]:
                c[f"{mode}: proposed over logged minutes"] += 1
    t = out["plan"]["totals"]
    if t["proposed"] > max(0, t["cap"] - t["logged"]):
        c[f"{mode}: proposed more than the room left under the cap"] += 1
    if t["logged"] > t["cap"]:
        c[f"{mode}: days logged above active+meetings (not a bug)"] += 1
    return c


def run_all(opts=None, quiet=False, with_logged_checks=False):
    days, crashes, extra = [], [], Counter()
    for d in dates():
        try:
            out = R.run_day(d, opts)
            if out is None:
                continue
            days.append(score_day(out))
            if with_logged_checks:
                extra.update(logged_checks(d, "half"))
                extra.update(logged_checks(d, "all"))
        except Exception as exc:
            crashes.append((d, f"{type(exc).__name__}: {exc}", traceback.format_exc()[-800:]))
    R.save_work_text()
    return days, crashes, extra


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "n/a"


def summary(days):
    P = sum(d["P"] for d in days); Q = sum(d["Q"] for d in days)
    M = sum(d["M"] for d in days); mx = sum(max(d["P"], d["Q"]) for d in days)
    rt = sum(d["right_time"] for d in days)
    tq = sum(d["timed_q"] for d in days)
    tr = [t for d in days for t in d["truth"] if t["s"] is not None]
    w = sum(t["minutes"] for t in tr)
    top = lambda k: sum(1 for t in tr if t.get("dialog_rank") and t["dialog_rank"] <= k)  # noqa: E731
    topm = lambda k: sum(t["minutes"] for t in tr if t.get("dialog_rank") and t["dialog_rank"] <= k)  # noqa: E731
    return {"days": len(days), "P": P, "Q": Q, "no_change": M / mx if mx else 0, "right_time": rt,
            "prec_time": rt / P if P else 0, "rec_time": rt / Q if Q else 0, "rec_timed": rt / tq if tq else 0,
            "prec_day": sum(d["prec_day"] for d in days) / P if P else 0, "rec_day": sum(d["rec_day"] for d in days) / Q if Q else 0,
            "n_entries": len(tr), "top1": top(1) / len(tr), "top3": top(3) / len(tr), "top5": top(5) / len(tr),
            "top1m": topm(1) / w, "top3m": topm(3) / w, "top5m": topm(5) / w,
            "seq_top1": sum(1 for t in tr if t.get("seq_rank") == 1) / len(tr),
            "seq_top3": sum(1 for t in tr if t.get("seq_rank") and t["seq_rank"] <= 3) / len(tr),
            "seq_top5": sum(1 for t in tr if t.get("seq_rank") and t["seq_rank"] <= 5) / len(tr),
            "seq_top1m": sum(t["minutes"] for t in tr if t.get("seq_rank") == 1) / w,
            "seq_top5m": sum(t["minutes"] for t in tr if t.get("seq_rank") and t["seq_rank"] <= 5) / w,
            "plan_top1m": sum(t["minutes"] for t in tr if t.get("plan_rank") == 1) / w,
            "plan_top4m": sum(t["minutes"] for t in tr if t.get("plan_rank")) / w}


def short(sm):
    return (f"days {sm['days']} | logged {sm['Q']} proposed {sm['P']} | no change {sm['no_change']:.1%} | "
            f"right task right time {sm['right_time']} min = P {sm['prec_time']:.1%} R {sm['rec_time']:.1%} | "
            f"task logged that day P {sm['prec_day']:.1%} R {sm['rec_day']:.1%} | dialog top1/3/5 "
            f"{sm['top1']:.1%}/{sm['top3']:.1%}/{sm['top5']:.1%} (by minutes {sm['top1m']:.1%}/{sm['top3m']:.1%}/{sm['top5m']:.1%})"
            f" | dialog in time order top1/3/5 {sm.get('seq_top1', 0):.1%}/{sm.get('seq_top3', 0):.1%}/{sm.get('seq_top5', 0):.1%}"
            f" (by minutes {sm.get('seq_top1m', 0):.1%}/{sm.get('seq_top5m', 0):.1%})"
            f" | plan row top1/4 by minutes {sm['plan_top1m']:.1%}/{sm['plan_top4m']:.1%}")


def table(title, groups, cols):
    print(f"\n### {title}")
    print(" | ".join(["group"] + [c[0] for c in cols]))
    for g, recs in sorted(groups.items(), key=lambda kv: -sum(r.get("minutes", 0) for r in kv[1])):
        print(" | ".join([str(g)] + [c[1](recs) for c in cols]))


if __name__ == "__main__":
    days, crashes, extra = run_all(with_logged_checks=True)
    sm = summary(days)
    print("## Headline\n" + short(sm))
    print("crashes:", len(crashes))
    for c in crashes:
        print(" ", c[0], c[1], "\n", c[2])
    E = [e for d in days for e in d["entries"]]
    T = [t for d in days for t in d["truth"] if t["s"] is not None]
    ecols = [("entries", lambda rs: str(len(rs))), ("minutes", lambda rs: str(sum(r["minutes"] for r in rs))),
             ("right time", lambda rs: pct(sum(r["right_time"] for r in rs), sum(r["minutes"] for r in rs))),
             ("task logged that day", lambda rs: pct(sum(r["right_day"] for r in rs), sum(r["minutes"] for r in rs)))]
    tcols = [("entries", lambda rs: str(len(rs))), ("minutes", lambda rs: str(sum(r["minutes"] for r in rs))),
             ("Log the day right time", lambda rs: pct(sum(r["right_time"] for r in rs), sum(r["minutes"] for r in rs))),
             ("dialog top1", lambda rs: pct(sum(1 for r in rs if r.get("dialog_rank") == 1), len(rs))),
             ("top3", lambda rs: pct(sum(1 for r in rs if r.get("dialog_rank") and r["dialog_rank"] <= 3), len(rs))),
             ("top5", lambda rs: pct(sum(1 for r in rs if r.get("dialog_rank") and r["dialog_rank"] <= 5), len(rs))),
             ("seq top1", lambda rs: pct(sum(1 for r in rs if r.get("seq_rank") == 1), len(rs))),
             ("seq top5", lambda rs: pct(sum(1 for r in rs if r.get("seq_rank") and r["seq_rank"] <= 5), len(rs))),
             ("in list", lambda rs: pct(sum(1 for r in rs if r.get("dialog_rank")), len(rs))),
             ("new task", lambda rs: pct(sum(r["minutes"] for r in rs if not r["known"]), sum(r["minutes"] for r in rs)))]

    def by(recs, key):
        g = defaultdict(list)
        for r in recs:
            g[key(r)].append(r)
        return g

    table("Log the day, proposed minutes by confidence (calibration)", by(E, lambda e: e["conf"]), ecols)
    table("Log the day, by ticked at the start", by(E, lambda e: "ticked" if e["checked"] else "unticked"), ecols)
    table("Log the day, by source of the pick", by(E, lambda e: e["source"]), ecols)
    table("Log the day, by source and confidence", by(E, lambda e: f"{e['source']}/{e['conf']}"), ecols)
    table("Log the day, by kind of proposed entry", by(E, lambda e: e["kind"]), ecols)
    table("Log the day, by kind of work (proposed)", by(E, lambda e: e["cat"]), ecols)
    table("Log the day, by repositories the session touched (proposed)", by(E, lambda e: min(e["nrep"], 3)), ecols)
    table("Logged entries, by kind of work", by(T, lambda t: t["cat"]), tcols)
    table("Logged entries, by weekday", by(T, lambda t: t["weekday"]), tcols)
    table("Logged entries, by repositories the session touched (0 = no Likely row)", by(T, lambda t: min(t["nrep"], 3)), tcols)
    table("Logged entries, by the dialog's first group", by(T, lambda t: t["dialog_kind"]), tcols)
    table("Logged entries, by Log the day's source over them", by(T, lambda t: t["plan_src"]), tcols)
    table("Logged entries, by whether the task was logged in the 60 days before", by(T, lambda t: "known" if t["known"] else "new"), tcols)
    causes = Counter()
    for d in days:
        causes.update(d["causes"])
    tot = sum(d["Q"] for d in days)
    print("\n### Failure causes, logged minutes Log the day got wrong (of", tot, "logged)")
    for c, m in causes.most_common():
        print(f"{m:6d}  {pct(m, tot):>6}  {c}")
    checks = Counter()
    for d in days:
        checks.update(d["checks"])
    checks.update(extra)
    print("\n### Correctness checks (counts; minutes where it says minutes)")
    for c, n in sorted(checks.items()):
        print(f"{n:6d}  {c}")
    print("\n### Per day")
    for d in days:
        mx = max(d["P"], d["Q"]) or 1
        print(f"{d['date']} logged {d['Q']:4d} prop {d['P']:4d} active {d['active']:4d} | no change {d['M'] / mx:.0%} | "
              f"right time {pct(d['right_time'], d['Q'])} | entries {d['n_entries']} vs {d['n_truth']}")
    json.dump({"summary": sm, "days": days, "crashes": crashes, "checks": dict(checks)},
              open(os.path.join(R.HERE, "results.json"), "w"), ensure_ascii=False, default=str)
