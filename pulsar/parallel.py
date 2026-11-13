"""Parallel work, for the Report: what Claude Code agents did in the background while you were at other work, by
project, and how much of it your entries cover (the owner, 2026-11-13: "скільки паралельної роботи було не затрекано
за місяць ... з статистикою по проектах"). Read-only, on this Mac.

The days come from the past days' Likely columns kept on this Mac (likely.py, Settings > Likely > Keep past days): the
work in front and the agents in the background, at their real time. A day not kept has no figures.

  - in parallel: an agent's minute in its repository while the work in front was another repository's (as the
    Log the day's "ran in parallel" line counts it);
  - a repository's project: the project you logged most while that repository was the work in front, over the
    period, from 30 minutes; with less, the repository's own name stands for it;
  - not logged (an estimate): each day, a project's minutes in parallel less what you logged to it beyond its work in
    front that day, never below zero.
"""

from __future__ import annotations

import math
from collections import defaultdict

MAP_MIN = 30   # minutes logged while a repository was in front before it stands for that project
SHOW_MIN = 5   # a project with fewer minutes in parallel over the period is left out


def _front(day: dict) -> dict:
    """{minute: repository} of the work in front of one kept day."""
    out: dict = {}
    for r in day.get("entries") or []:
        if r.get("kind") == "background" or not r.get("project"):
            continue
        for x in range(max(0, int(r["s"])), int(math.ceil(r["e"]))):
            out[x] = r["project"]
    return out


def summary(dates: list, rows: list, load_day, t0_of) -> dict:
    """{"days": days kept, "of": days asked, "parallel", "notLogged", "projects": [{name, mapped, parallel, front,
    logged, notLogged}]}. rows: Productive entries ({date, start epoch or None, minutes, project}); load_day(date): a
    kept Likely day ({entries, background}) or None; t0_of(date): the day's midnight, epoch."""
    days = {d: load_day(d) for d in dates}
    kept = [d for d in dates if days[d]]
    fronts = {d: _front(days[d]) for d in kept}
    by_date: dict = defaultdict(list)
    for r in rows:
        by_date[r.get("date")].append(r)
    votes: dict = defaultdict(lambda: defaultdict(int))  # repository -> project -> minutes logged while in front
    for d in kept:
        t0 = t0_of(d)
        for r in by_date.get(d, []):
            if not r.get("start") or not r.get("project"):
                continue
            a = int((r["start"] - t0) // 60)
            for x in range(a, a + int(r.get("minutes") or 0)):
                if x in fronts[d]:
                    votes[fronts[d][x]][r["project"]] += 1
    project_of = {repo: max(v, key=lambda p: (v[p], p)) for repo, v in votes.items() if max(v.values()) >= MAP_MIN}
    out: dict = defaultdict(lambda: {"parallel": 0, "front": 0, "logged": 0, "notLogged": 0})
    for d in kept:
        front = fronts[d]
        par: dict = defaultdict(set)
        for b in days[d].get("background") or []:
            repo = b.get("project") or ""
            for x in range(max(0, int(b["s"])), int(math.ceil(b["e"]))):
                if x in front and front[x] != repo:
                    par[repo].add(x)
        if not par:
            continue
        in_front: dict = defaultdict(int)
        for repo in front.values():
            in_front[project_of.get(repo, repo)] += 1
        parallel: dict = defaultdict(int)
        for repo, xs in par.items():
            parallel[project_of.get(repo, repo)] += len(xs)
        logged: dict = defaultdict(int)
        for r in by_date.get(d, []):
            logged[r.get("project") or ""] += int(r.get("minutes") or 0)
        for p, m in parallel.items():
            o = out[p]
            o["parallel"] += m
            o["front"] += in_front.get(p, 0)
            o["logged"] += logged.get(p, 0)
            o["notLogged"] += max(0, m - max(0, logged.get(p, 0) - in_front.get(p, 0)))
    mapped = set(project_of.values())
    items = sorted(({"name": p, "mapped": p in mapped, **v} for p, v in out.items() if v["parallel"] >= SHOW_MIN),
                   key=lambda x: (-x["parallel"], x["name"]))
    return {"days": len(kept), "of": len(dates), "parallel": sum(x["parallel"] for x in items),
            "notLogged": sum(x["notLogged"] for x in items), "projects": items}
