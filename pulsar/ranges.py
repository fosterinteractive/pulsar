"""What a stretch of time picked in the Day view holds, for the log dialog (GET /api/range).

The minutes come from Log the day (dayplan.plan, with `keep`): who has each minute of the day, restricted to [s, e).
They make parts by kind (Claude work by repository, a meeting, a call, work outside Claude, a repository you never
log, already logged, away, no work seen), each with Log the day's task for it (task, confidence, why) and a note
source (its commits, Claude's draft for that task, a meeting's title, a repeat's usual note). The work parts are then
grouped by task: the group with most minutes leads the dialog, and when no group holds most of the range, or two
hold MIXED_MIN minutes or more each, the range is "mixed" and `split` says how it would go as separate entries.

Pure: plain data in, plain data out. Nothing here reads or writes anything.
"""

from __future__ import annotations

import math
from collections import defaultdict

from . import attribution, dayplan

# A range is worth splitting when its biggest task holds less than this share of its work minutes, or when two tasks
# have MIXED_MIN minutes or more each, counted as the split would make them (a short piece joins its neighbour).
# Tuned on the replay of one person's days (21 September to 5 November 2026): with 0.75 the hint shows for 8.8% of
# the 487 entries logged as one (one task) and for 53.0% of 436 runs of two or three entries back to back with
# different tasks (89.5% of the 258 whose work Log the day tells apart); 0.6 gave 6.2% and 44.7%, 0.7 8.4% and 48.9%,
# 0.8 10.3% and 55.5% (docs 5.1).
MIXED_SHARE = 0.75
MIXED_MIN = 20
WORK = ("claude", "meeting", "call", "outside", "skip")
LABEL = {"meeting": "a meeting", "call": "a call", "outside": "work outside Claude", "logged": "already logged",
         "away": "away", "idle": "no work seen", "other": "logging time"}


def _minute(keep: dict, at: list, x: int) -> tuple:
    """(kind, part key, claim index or None, piece or None) for one minute of the day."""
    owner, claims = keep["owner"], keep["claims"]
    o = owner[x] if 0 <= x < len(owner) else None
    if o is None or o == dayplan.BLOCKED:
        if keep["away"][x] if 0 <= x < len(keep["away"]) else False:
            return "away", ("away",), None, None
        if keep["work"][x] if 0 <= x < len(keep["work"]) else False:
            return "other", ("other",), None, None  # time spent logging: Log the day never proposes it
        return "idle", ("idle",), None, None
    c = claims[o]
    if c["kind"] == "logged":
        return "logged", ("logged",), o, None
    p = at[x]
    kind = (p or c)["kind"]
    if kind in ("focused", "agent", "shared"):
        return "claude", ("claude", c.get("project") or ""), o, p
    if kind == "skip":
        return "skip", ("skip", c.get("project") or ""), o, p
    if kind == "outside":
        return "outside", ("outside",), o, p
    if c.get("source") == "call":
        return "call", ("call", o), o, p
    return "meeting", ("meeting", o), o, p  # a repeat, a calendar meeting (attended or not)


def _label(kind: str, repo: str) -> str:
    return (repo or "Claude Code") if kind in ("claude", "skip") else LABEL.get(kind, kind)


def _class(g: dict, ctx: dict) -> str | None:
    """A group's class: its task's (attribution.class_of_task), else its project's (a task never logged to)."""
    got = attribution.class_of_task(g.get("taskId"), ctx) if g.get("taskId") else None
    project = g.get("project") or ""
    if got is None and project:
        got = attribution.INTERNAL if attribution.internal_project(project) else (ctx.get("project_class") or {}).get(project)
    return got


def analyse(keep: dict, s: float, e: float, opts: dict | None = None) -> dict:
    """The parts of [s, e) (minutes of the day, as the page draws them), the task groups, the one that leads, whether
    the range is mixed, and the entries a split would make. keep: what dayplan.plan(..., keep=) filled in."""
    o = {"grid": dayplan.GRID, "min_entry": dayplan.MIN_ENTRY, **(opts or {})}
    a, b = max(0, int(math.floor(s))), min(len(keep["owner"]), int(math.ceil(e)))
    claims, names = keep["claims"], keep.get("names") or {}
    at: list = [None] * len(keep["owner"])
    for p in keep.get("pieces") or []:
        for x in range(p["s"], p["last"] + 1):
            if keep["owner"][x] == p["claim"]:
                at[x] = p

    def task_of(o_, p):
        if o_ is None:
            return None
        return str((p or {}).get("task") or claims[o_].get("task") or "") or None

    parts: dict = {}
    marks = []  # per minute: the group key of a work minute, "logged", or None
    for x in range(a, b):
        kind, key, o_, p = _minute(keep, at, x)
        part = parts.get(key)
        if part is None:
            c = claims[o_] if o_ is not None else {}
            part = parts[key] = {"kind": kind, "label": _label(kind, key[1] if len(key) > 1 and isinstance(key[1], str) else ""),
                                 "repo": (c.get("project") or "") if kind in ("claude", "skip") else "",
                                 "meeting": (c.get("meeting") or "") if kind in ("meeting", "call") else "",
                                 "minutes": 0, "s": x, "tasks": defaultdict(int), "claims": defaultdict(int)}
        part["minutes"] += 1
        if kind in WORK:
            tid = task_of(o_, p) if kind != "skip" else None
            part["tasks"][tid] += 1
            part["claims"][o_] += 1
            marks.append(("task", tid) if tid else key)
        else:
            marks.append("logged" if kind == "logged" else None)

    # groups: the work parts by task (a part with no task stands alone)
    groups: dict = {}
    for key, part in parts.items():
        if part["kind"] not in WORK:
            continue
        for tid, n in part["tasks"].items():
            gk = ("task", tid) if tid else key
            g = groups.setdefault(gk, {"taskId": tid, "minutes": 0, "parts": defaultdict(int), "claims": defaultdict(int)})
            g["minutes"] += n
            g["parts"][key] += n
    for x, gk in zip(range(a, b), marks):  # each claim's minutes in each group, for its why and note
        if gk not in (None, "logged"):
            o_ = keep["owner"][x]
            groups[gk]["claims"][o_] += 1
    work = sum(g["minutes"] for g in groups.values())
    out_groups = []
    for gk, g in groups.items():
        main = max(g["parts"], key=g["parts"].get)
        lead = claims[max(g["claims"], key=g["claims"].get)]
        tid = g["taskId"]
        # the note of the claim with most minutes here that has one for this task (as dayplan._entry): its commits,
        # Claude's draft, a meeting's title, a repeat's usual note
        noted = [claims[c] for c, _ in sorted(g["claims"].items(), key=lambda kv: -kv[1])
                 if claims[c].get("note") and (str(claims[c].get("task") or "") == tid if tid else claims[c] is lead)]
        title, project = names.get(str(tid), ("", "")) if tid else ("", "")
        part = parts[main]
        conf = lead.get("conf") or "low"
        out_groups.append({
            "key": "|".join(map(str, gk)), "taskId": tid, "title": title, "project": project,
            "minutes": g["minutes"], "share": round(g["minutes"] / work, 3) if work else 0,
            "kind": part["kind"], "label": part["label"], "repo": part["repo"], "meeting": part["meeting"],
            "confidence": conf, "confidenceWords": dayplan.CONF_WORDS.get(conf, ""),
            "why": lead.get("why") or "", "source": lead.get("source") or "",
            "note": noted[0]["note"] if noted else "", "noteFrom": (noted[0].get("noteFrom") or "") if noted else "",
            # Claude's pick of a task found by searching Productive: as Log the day, never logged in one click
            "claudeFound": bool(tid) and any(claims[c].get("claudeFound") and str(claims[c].get("task") or "") == tid
                                             for c in g["claims"]),
            "alts": [{"taskId": str(t), "title": names.get(str(t), ("", ""))[0], "project": names.get(str(t), ("", ""))[1],
                      "why": (lead.get("altWhy") or {}).get(t) or ""}
                     for t in lead.get("alts") or [] if t and str(t) != tid and names.get(str(t), ("", ""))[0]][:3],
            "_gk": gk})
    out_groups.sort(key=lambda g: -g["minutes"])
    # a client's work leads when it holds at least as much of the stretch as internal work does, whatever its tasks:
    # 2026-11-09, a client's 51 minutes in two tasks (32 and 19) and the tool's own 34 led with the tool's task
    ctx = keep.get("ctx") or {}
    cls = {id(g): _class(g, ctx) for g in out_groups}
    client = sum(g["minutes"] for g in out_groups if cls[id(g)] == attribution.CLIENT)
    if client and client >= sum(g["minutes"] for g in out_groups if cls[id(g)] == attribution.INTERNAL):
        out_groups.sort(key=lambda g: cls[id(g)] != attribution.CLIENT)  # stable: by minutes within each
    dominant = out_groups[0] if out_groups else None
    share = dominant["share"] if dominant else 0
    split = _split(marks, a, o) if work else []
    in_split = {r[2] for r in split}  # told apart where the split names them, else where the range does
    _unique_labels([g for g in out_groups if g["_gk"] in in_split] if len(in_split) >= 2 else out_groups)
    by_gk = {g["_gk"]: g for g in out_groups}
    entries, kept = [], defaultdict(int)  # kept: each group's minutes of work in the split
    for st, n, gk, w in split:
        g = by_gk[gk]
        kept[g["key"]] += w
        # real minutes: the night the clocks go forward, the hour they skip is on the clock but never happened
        n -= sum(max(0, min(st + n, ge) - max(st, gs)) for gs, ge in keep.get("gone") or [])
        entries.append({"s": st, "minutes": n, "start": _hm(st), "group": g["key"], "taskId": g["taskId"],
                        "title": g["title"], "project": g["project"], "label": g["label"], "kind": g["kind"],
                        "note": g["note"], "noteFrom": g["noteFrom"], "confidence": g["confidence"], "why": g["why"],
                        "claudeFound": g["claudeFound"]})
    # mixed, as the split would make it: a short piece that joins its neighbour is part of it
    top = max(kept.values(), default=0) / (sum(kept.values()) or 1)
    mixed = len(kept) >= 2 and (top < o.get("mixed_share", MIXED_SHARE) or sum(1 for w in kept.values() if w >= MIXED_MIN) >= 2)
    for g in out_groups:
        g.pop("_gk")
        g["split"] = sum(x["minutes"] for x in entries if x["group"] == g["key"])
        g["splitWork"] = kept.get(g["key"], 0)
    return {"s": a, "e": b, "minutes": b - a, "work": work, "share": share, "splitShare": round(top, 3), "mixed": mixed,
            "dominant": dominant, "groups": out_groups, "split": entries if mixed else [],
            "parts": [{k: v for k, v in p.items() if k not in ("tasks", "claims")}
                      | {"taskId": max(p["tasks"], key=p["tasks"].get) if p["tasks"] else None}
                      for p in sorted(parts.values(), key=lambda p: p["s"])]}


def _unique_labels(groups: list) -> None:
    """Two groups with the same label (two tasks in one repository) are told apart by their task."""
    seen = defaultdict(int)
    for g in groups:
        seen[g["label"]] += 1
    for g in groups:
        if seen[g["label"]] > 1 and g["title"]:
            title = g["title"] if len(g["title"]) <= 40 else g["title"][:40].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
            g["label"] = f"{g['label']} ({title})"


def _split(marks: list, a: int, o: dict) -> list:
    """[(start, minutes, group key, minutes of work)]: the range as separate entries. Minutes that are not work (away, no activity,
    logging time) go with the work before them, else after; time already logged stays out. Pieces with fewer minutes
    of work than the shortest entry join a neighbour (never across an entry already logged), or are left out when none
    is next to them; boundaries inside the range go on the grid; the range's own start and end stay where the person
    put them."""
    g, shortest = o["grid"] or 1, o["min_entry"]
    lab = list(marks)
    for i in range(len(lab)):  # not work: with the work before it, in the same stretch between logged entries
        if lab[i] is None and i and lab[i - 1] not in (None, "logged"):
            lab[i] = lab[i - 1]
    for i in range(len(lab) - 2, -1, -1):  # ...else with the work after it
        if lab[i] is None and lab[i + 1] not in (None, "logged"):
            lab[i] = lab[i + 1]
    runs = []  # [start, minutes, key, minutes of work]
    for i, k in enumerate(lab):
        if runs and runs[-1][2] == k and runs[-1][0] + runs[-1][1] == a + i:
            runs[-1][1] += 1
            runs[-1][3] += marks[i] not in (None, "logged")
        else:
            runs.append([a + i, 1, k, int(marks[i] not in (None, "logged"))])

    def touching(r, q):  # q may take r's minutes: next to it, and neither is logged time or nothing
        return q[2] not in (None, "logged") and (q[0] + q[1] == r[0] or r[0] + r[1] == q[0])

    changed = True
    while changed:  # the shortest piece first joins its bigger neighbour, then the same key next to each other joins
        changed = False
        short = sorted((r for r in runs if r[2] not in (None, "logged") and r[3] < shortest), key=lambda r: r[3])
        for r in short:
            i = runs.index(r)
            nb = [q for q in (runs[i - 1] if i else None, runs[i + 1] if i + 1 < len(runs) else None)
                  if q is not None and touching(r, q)]
            if nb:
                q = max(nb, key=lambda q: q[3])
                q[0], q[1], q[3] = min(q[0], r[0]), q[1] + r[1], q[3] + r[3]
                runs.pop(i)
                changed = True
                break
        joined = []
        for r in runs:
            if joined and joined[-1][2] == r[2] and joined[-1][0] + joined[-1][1] == r[0]:
                joined[-1][1] += r[1]
                joined[-1][3] += r[3]
                changed = True
            else:
                joined.append(r)
        runs = joined
    # boundaries between two pieces of work go on the grid
    for r, q in zip(runs, runs[1:]):
        if r[2] in (None, "logged") or q[2] in (None, "logged"):
            continue
        cut = r[0] + r[1]
        snapped = int(round(cut / g) * g)
        if r[0] < snapped < q[0] + q[1]:
            r[1], q[1], q[0] = snapped - r[0], q[0] + q[1] - snapped, snapped
    return [tuple(r) for r in runs if r[2] not in (None, "logged") and r[1] > 0 and r[3] >= shortest]


def with_services(res: dict, ctx: dict, bookable) -> None:
    """Each split entry's service, as Log the day picks it (the one last used on the task, checked against the services
    that take time on that date: dayplan.pick_service), and the task's name where the plan knew only its id.
    bookable(task id) -> productive.bookable_services' answer, or None when it could not be read. Mutates res."""
    found = {}
    for x in res.get("split") or []:
        tid = x.get("taskId")
        if not tid:
            continue
        if tid not in found:
            found[tid] = bookable(tid)
        b = found[tid]
        if not b:
            x["serviceProblem"] = "Productive did not list this task's services"
            continue
        task = b.get("task") or {}
        x["title"] = x.get("title") or task.get("title") or ""
        x["project"] = x.get("project") or task.get("project") or ""
        last_id, last_name = (ctx.get("service") or {}).get(tid, (None, ""))
        svc = dayplan.pick_service(b.get("services") or [], last_id, last_name, task.get("serviceId"),
                                   (ctx.get("project_service") or {}).get(x["project"]))
        if svc:
            x["serviceId"], x["service"] = str(svc["id"]), (svc.get("name") or "").strip()
            x["budget"], x["billable"] = svc.get("budget") or "", svc.get("billable")
        else:
            x["serviceProblem"] = "No service of this task takes time on this date" if not b.get("services") \
                else "Several services: log it on its own to pick one"
    for g in res.get("groups") or []:
        if g.get("taskId") and not g.get("title") and g["taskId"] not in found:
            found[g["taskId"]] = bookable(g["taskId"])
        b = found.get(g.get("taskId"))
        if b and not g.get("title"):
            g["title"], g["project"] = (b.get("task") or {}).get("title") or "", (b.get("task") or {}).get("project") or ""


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"
