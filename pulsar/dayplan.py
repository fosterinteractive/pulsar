"""Log the day: one proposal for a whole day, from the signals Pulsar already has, for the person to review.

Nothing here writes to Productive. `plan` is pure (plain data in, plain data out, tested on made-up days); `gather`
reads one day through the rest of Pulsar and calls it. The journal at the end records each write the review sends
through POST /api/log, before it is sent, so a restart never sends one twice and Undo deletes only what its own run
created.

How each minute of the day is placed (the first claim wins):

1. entries already in Productive;
2. meetings the person attended: a repeat whose call the tracker saw, a calendar meeting with a call seen, a call
   with no calendar entry. A repeat and a calendar meeting on the same call are one entry; a Likely row in a meeting
   gives its minutes to the meeting;
3. Likely rows (Claude sessions), at their own time, never on away minutes or time spent logging;
4. on free active minutes only: agent-only rows, calendar meetings with no call seen, work outside Claude.

Then the pieces of one task in one sitting join, short ones join a neighbour, boundaries go on a 5-minute grid,
nothing overlaps an entry already logged, and the total is capped: proposed plus logged never exceeds active time
plus attended meetings. What does not fit is shown in `unplaced`, never placed.

Notes are never written from session text: only a meeting's title, a repeat's usual note, a note Claude drafted
(Ask Claude) for that task, or the subjects of the person's own commits in the work (commits.py), each marked so the
page can say "clients may see notes".
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

from . import commits as git_commits, config

GRID = 5             # entry boundaries on this grid, in minutes
MIN_ENTRY = 10       # shorter pieces join a neighbour
MERGE_GAP = 30       # pieces of one task this close, in one sitting, are one entry
OUT_MIN = 10         # active minutes outside Claude from this long become an entry of their own
SITTING_GAP = 30     # minutes without activity that end a sitting
CALL_GAP = 20        # a call goes on while its window shows again within this many minutes
CALL_MIN = 10        # a shorter call is a glance at a call window, not a meeting
MEETING_SLACK = 10   # a call this close to a calendar meeting is that meeting
HALF_LIFE = 7        # days: older logging counts half as much this many days later
LEARN_DAYS = 60
RECENT_DAYS = 30
RANK = {"low": 0, "medium": 1, "high": 2}
ORDER = ("rule", "link", "ticket", "session", "scoped", "repo", "sameday", "words")
BLOCKED = -1

EDITORS = re.compile(r"^(Cursor|Code|Visual Studio Code|Terminal|iTerm2|Warp|Claude)$", re.I)
# time spent logging time (Pulsar, Productive): never proposed, never given to a session (sessions.LOGGING)
LOGGING = re.compile(r"^Pulsar$|app\.productive\.io|(127\.0\.0\.1|localhost):56\d\d", re.I)
# ...except in Pulsar's own repository: there the Pulsar window is the work itself, testing what was just built
# (2026-11-06: 76 minutes of testing Pulsar were dropped as logging time)
OWN_REPO = re.compile(r"^pulsar(-preview)?$", re.I)
WORD = re.compile(r"[^\W_]{3,}")
TICKET = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,5}\b")
GENERIC = {"the", "and", "for", "with", "drupal", "site", "website", "issues", "issue", "fix", "update", "kb-dev",
           "dev", "local", "scripts", "found", "during", "ongoing", "support", "important", "analysis", "review",
           "check", "new", "task", "tasks", "from", "into", "about", "plan"}

CONF_WORDS = {"high": "likely right", "medium": "probably right", "low": "check"}


def words(text: str, skip: frozenset = frozenset()) -> set:
    return {w for w in WORD.findall((text or "").lower()) if w not in GENERIC and w not in skip}


def title_key(title: str) -> str:
    """The same key learn.py keeps for a session title (a hash of the normalised title)."""
    from .learn import key
    return key(title)


# ---- learning from the days before -------------------------------------------------------------------------------

def main_task(entries: list, a: float, b: float) -> tuple:
    """(the task that took most of [a, b) epoch seconds when it took ten minutes or a third of it, minutes covered)."""
    cover: dict = {}
    for r in entries:
        if r.get("start") is None or not r.get("taskId"):
            continue
        o = min(r["start"] + r["minutes"] * 60, b) - max(r["start"], a)
        if o > 0:
            cover[str(r["taskId"])] = cover.get(str(r["taskId"]), 0) + o
    if not cover:
        return None, 0.0
    best = max(cover, key=cover.get)
    return (best if cover[best] >= min(600, (b - a) / 3) else None), sum(cover.values()) / 60


def learn(date: str, history: list, days: dict, t0_of, half_life: float = HALF_LIFE, learn_days: int = LEARN_DAYS,
          person: str = "") -> dict:
    """What the days before `date` say: votes session key -> task and repository -> task (each earlier Likely row votes,
    with its minutes, for the task logged over most of it; older days count less), the service last used per task,
    names, the tasks logged lately, and repositories worked in but never logged (a side project).

    history: your entries before `date` ({date, start, minutes, taskId, serviceId, service, task, project});
    days: {date: [{"k": [title keys], "p": repository, "s", "e"}]}, as learn.py keeps them."""
    day0 = datetime.strptime(date, "%Y-%m-%d")
    lo = (day0 - timedelta(days=learn_days)).strftime("%Y-%m-%d")
    before = [r for r in history if lo <= str(r.get("date") or "") < date and r.get("taskId")]
    by_day: dict = defaultdict(list)
    for r in before:
        by_day[r["date"]].append(r)
    sess: dict = defaultdict(lambda: defaultdict(float))
    repo: dict = defaultdict(lambda: defaultdict(float))
    seen: dict = defaultdict(float)
    logged: dict = defaultdict(float)
    for d, rows in days.items():
        if not lo <= d < date or not rows:
            continue
        t0 = t0_of(d)
        age = (day0 - datetime.strptime(d, "%Y-%m-%d")).days
        w = 0.5 ** (age / half_life) if half_life else 1.0
        for row in rows:
            m = row["e"] - row["s"]
            if m < 5:
                continue
            task, cov = main_task(by_day.get(d, []), t0 + row["s"] * 60, t0 + row["e"] * 60)
            seen[row.get("p") or ""] += m
            logged[row.get("p") or ""] += min(cov, m)
            if not task:
                continue
            for k in row.get("k") or []:
                sess[k][task] += m * w
            repo[row.get("p") or ""][task] += m * w
    service, names, last = {}, {}, {}
    for r in sorted(before, key=lambda r: (r["date"], r.get("start") or 0)):
        t = str(r["taskId"])
        service[t] = (str(r.get("serviceId") or "") or None, r.get("service") or "")
        names[t] = {"task": r.get("task") or "", "project": r.get("project") or ""}
        last[t] = r["date"]
    lo30 = (day0 - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
    recent = [t for t, d in sorted(last.items(), key=lambda kv: kv[1], reverse=True) if d >= lo30]
    never = {p for p in seen if p and seen[p] >= 60 and logged[p] / seen[p] < 0.1}
    return {"sess": sess, "repo": repo, "service": service, "names": names, "recent": recent, "never_logged": never,
            "skip": set(WORD.findall((person or "").lower()))}


# ---- a task for one Likely row -------------------------------------------------------------------------------------

def _ranked(votes: dict) -> list:
    tot = sum(votes.values()) or 1
    return [(t, v, v / tot) for t, v in sorted(votes.items(), key=lambda kv: -kv[1])]


def _keys(row: dict) -> list:
    return [k for k in (title_key(t) for t in row.get("titles") or []) if k]


def candidates(row: dict, ctx: dict, rule_hit, sameday: dict, assigned: list) -> list:
    """Every source's answer for a row, in ORDER: [(task, source, confidence, why)]. The first is the pick, the
    rest are the alternatives offered beside it."""
    out = []
    names = ctx.get("names") or {}
    skip = frozenset(ctx.get("skip") or ())  # the person's own name is in many task titles: it says nothing
    assigned = assigned or []
    by_id = {str(a["taskId"]): a for a in assigned}

    def name(tid):
        n = names.get(tid) or {}
        a = by_id.get(tid) or {}
        return n.get("task") or a.get("title") or "", n.get("project") or a.get("project") or ""

    keys = _keys(row)
    for src in ORDER:
        if src == "rule" and rule_hit:
            out.append((str(rule_hit["taskId"]), "rule", "high", f"your rule “{rule_hit['match']}”"))
        elif src == "link" and row.get("tasks"):
            out.append((str(row["tasks"][0]), "link", "high", "a task link you pasted in this Claude session"))
        elif src == "ticket":
            found = set(TICKET.findall(" ".join(list(row.get("titles") or []) + [row.get("first_message") or ""])))
            if found:
                for tid in list(ctx.get("recent") or []) + list(by_id):
                    hit = found & set(TICKET.findall(name(tid)[0]))
                    if hit:
                        out.append((tid, "ticket", "high", f"{sorted(hit)[0]} is named in the session"))
                        break
        elif src == "session":
            v: dict = defaultdict(float)
            for k in keys:
                for t, m in (ctx["sess"].get(k) or {}).items():
                    v[t] += m
            if v:
                t, m, share = _ranked(v)[0]
                conf = "high" if share >= 0.6 and m >= 30 else "medium" if share >= 0.4 else "low"
                out.append((t, "session", conf, f"you logged it while in this Claude session ({round(share * 100)}%)"))
        elif src == "repo":
            v = ctx["repo"].get(row.get("project") or "") or {}
            if v:
                t, m, share = _ranked(v)[0]
                conf = "medium" if share >= 0.5 and m >= 60 else "low"
                out.append((t, "repo", conf, f"you logged it while in {row.get('project')} ({round(share * 100)}%)"))
        elif src == "sameday":
            for k in keys:
                if k in sameday:
                    out.append((sameday[k], "sameday", "medium", "the same session as another entry today"))
                    break
        elif src == "scoped":
            # the repository's projects (from its votes), then the task in them whose name shares most words with
            # the session's titles and first message
            v = ctx["repo"].get(row.get("project") or "") or {}
            projects = {name(t)[1] for t in v} - {""}
            if projects:
                w = words(" ".join(list(row.get("titles") or []) + [row.get("first_message") or ""]), skip)
                best = None
                pool = [t for t in ctx.get("recent") or [] if name(t)[1] in projects]
                pool += [t for t in by_id if name(t)[1] in projects]
                for tid in pool:
                    shared = w & words(name(tid)[0], skip)
                    if shared and (best is None or len(shared) > best[1]):
                        best = (tid, len(shared), shared)
                if best:
                    out.append((best[0], "scoped", "low",
                                "a task of this repository's project named " + ", ".join(sorted(best[2]))))
        elif src == "words":
            w = words(" ".join([row.get("project") or ""] + list(row.get("titles") or [])), skip)
            best = None
            for tid in list(ctx.get("recent") or []) + list(by_id):
                shared = w & words(" ".join(name(tid)), skip)
                if shared and (best is None or len(shared) > best[1]):
                    best = (tid, len(shared), shared)
            if best:
                out.append((best[0], "words", "low", "the task's name shares " + ", ".join(sorted(best[2]))))
    return out


def choose(row: dict, ctx: dict, rule_hit=None, sameday: dict | None = None, assigned: list | None = None,
           pick: dict | None = None) -> dict:
    """{task, source, conf, why, alts} for one Likely row. Claude's pick (Ask Claude) replaces an answer that is not
    sure (medium or low): on the hardest days it helped most there."""
    found = candidates(row, ctx, rule_hit, sameday or {}, assigned or [])
    if found:
        task, src, conf, why = found[0]
    else:
        task, src, conf, why = None, "none", "low", "no signal: pick the task"
    alts = []
    claude_none = bool(pick) and not pick.get("tasks") and conf != "high"  # Claude found no task: keep ours, say so
    if pick and pick.get("tasks") and conf != "high":
        first = pick["tasks"][0]
        alts = [(task, src, conf, why)] if task else []
        alts = [(t, s, c, f"Pulsar's guess: {w}") for t, s, c, w in alts]
        task, src, conf, why = str(first["taskId"]), "claude", "medium", f"Claude: {pick.get('why') or 'its pick'}"
        alts += [(str(t["taskId"]), "claude", "low", "Claude: another option") for t in pick["tasks"][1:]]
    seen = {task}
    for t, s, c, w in alts + found[1:] + [(str(x["taskId"]), "learned", "low", x.get("why") or "")
                                          for x in row.get("learned") or []]:
        if t and t not in seen:
            seen.add(t)
            alts.append((t, s, c, w))
    return {"task": task, "source": src, "conf": conf, "why": why, "claudeNone": claude_none,
            "alts": [{"taskId": t, "source": s, "why": w} for t, s, c, w in alts if t and t != task][:3]}


# ---- the day ------------------------------------------------------------------------------------------------------

def join_calls(spans) -> list:
    out: list = []
    for a, b in sorted(((s["s"], s["e"]) if isinstance(s, dict) else (s[0], s[1]) for s in spans), key=lambda x: x[0]):
        if out and a - out[-1][1] <= CALL_GAP:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _overlap(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _min_conf(a: str, b: str) -> str:
    return a if RANK[a] <= RANK[b] else b


def _worst(parts: list, minutes: int) -> str:
    """An entry is as sure as the part that carries most of its minutes, never surer than any big part."""
    by: dict = defaultdict(int)
    for q in parts:
        by[q.get("conf", "low")] += q["minutes"]
    main = max(by, key=by.get)
    big = [c for c, n in by.items() if n >= 0.25 * minutes]
    return min(big + [main], key=lambda c: RANK[c])


def rule_in(items: list, rules: list, a: float, b: float, text: str = ""):
    """The rule that matches most of [a, b) in the activity (at least 40% of it), or whose words are in `text`."""
    if not rules:
        return None
    by: dict = {}
    for it in items:
        o = _overlap(it["s"], it["e"], a, b)
        if o <= 0 or it.get("cat") != "work":
            continue
        t = f"{it.get('app')} {it.get('title')} {it.get('workspace') or ''} {it.get('host')} {it.get('url') or ''}".lower()
        r = next((r for r in rules if r.get("match") and r["match"].lower() in t), None)
        if r:
            by[r["match"]] = (by.get(r["match"], (0, r))[0] + o, r)
    top = max(by.values(), key=lambda x: x[0]) if by else None
    if top and top[0] >= 0.4 * max(1, b - a):
        return top[1]
    low = text.lower()
    return next((r for r in rules if r.get("match") and r["match"].lower() in low), None)


def plan(day: dict, ctx: dict, opts: dict | None = None) -> dict:
    """The proposal for one day.

    day: {"items": activity [{s, e, app, title, host, url, cat}], "away": [{s, e}], "rows": Likely rows
          [{s, e, minutes, project, kind, titles, first_message, tasks, learned}], "logged": entries already in
          Productive [{s (None without a start), minutes, taskId}], "meetings": calendar events [{s, e, title, uid,
          declined, all_day, tasks: [{taskId, title, project, n, of}]}], "repeats": repeats seen today [{s, e, minutes,
          taskId, title, project, serviceId, service, note, days}], "calls": call windows [{s, e}], "rules",
          "assigned": [{taskId, title, project}], "picks": Ask Claude's answer {"<s>|<project>": {tasks, why, note}}}
    ctx: learn(...).
    Returns {"entries", "unplaced", "skipped", "uncovered", "totals"}; each entry
    {s, e, minutes, taskId, title, project, serviceId, service, note, noteFrom, confidence, why, source, kind,
     checked, flags, alts}."""
    o = {"grid": GRID, "min_entry": MIN_ENTRY, "merge_gap": MERGE_GAP, "out_min": OUT_MIN, **(opts or {})}
    N = 1440
    work = bytearray(N)
    editor = bytearray(N)
    logging_ = bytearray(N)
    away = bytearray(N)
    items = day.get("items") or []
    for it in items:
        if it.get("cat") != "work":
            continue  # personal windows never count as work
        ed = 1 if EDITORS.search(it.get("app") or "") else 0
        lg = 1 if any(LOGGING.search(str(it.get(k) or "")) for k in ("app", "host", "url")) else 0
        for x in range(max(0, int(it["s"])), min(N, int(math.ceil(it["e"])))):
            work[x] = 1
            editor[x] = ed
            logging_[x] = logging_[x] or lg
    for w in day.get("away") or []:
        for x in range(max(0, int(math.ceil(w["s"]))), min(N, int(w["e"]))):
            away[x] = 1
    sitting: list = [None] * N
    marks = [x for x in range(N) if work[x]]
    if marks:
        a = p = marks[0]
        for x in marks[1:] + [None]:
            if x is None or x - p > SITTING_GAP:
                for y in range(a, p + 1):
                    sitting[y] = a
                a = x
            if x is not None:
                p = x

    owner: list = [None] * N
    claims: list = []
    names: dict = {}  # taskId -> (title, project): what each source knows of a task

    def known(tid, title="", project=""):
        if tid and (title or project) and not (names.get(str(tid)) or ("", ""))[0]:
            names[str(tid)] = (title or "", project or "")

    def claim(c, minutes):
        claims.append(c)
        i = len(claims) - 1
        got = 0
        for x in minutes:
            if 0 <= x < N and owner[x] is None:
                owner[x] = i
                got += 1
        return i, got

    # 1. entries already logged
    logged = day.get("logged") or []
    busy = []
    for r in logged:
        if r.get("s") is None:
            continue  # no start: not on the timeline (counted in the total, below)
        a, b = int(r["s"]), int(math.ceil(r["s"] + r["minutes"]))
        claim({"kind": "logged"}, range(a, b))
        busy.append((r["s"], r["s"] + r["minutes"]))
    busy.sort()
    done_tasks = {str(r.get("taskId")) for r in logged if r.get("taskId")}

    # 2. meetings the person was in: repeats (a call was seen at the usual time), calendar meetings with a call seen,
    # calls with no calendar entry. A repeat and a calendar meeting on the same call are one entry.
    calls = join_calls(day.get("calls") or [])
    attended = []  # [s, e, claim index]
    for rp in day.get("repeats") or []:
        tid = str(rp["taskId"])
        if tid in done_tasks:
            continue  # its task already has an entry today: the page stops offering it too
        known(tid, rp.get("title"), rp.get("project"))
        conf = "high" if (rp.get("days") or 0) >= 5 else "medium"
        i, _ = claim({"kind": "meeting", "source": "repeat", "task": tid, "conf": conf, "attended": True,
                      "why": f"you log this at {int(rp.get('at', rp['s'])) // 60:02d}:{int(rp.get('at', rp['s'])) % 60:02d} "
                             f"most days ({rp.get('days', 0)} days) and the tracker saw the call",
                      "note": (rp.get("note") or "").strip(), "noteFrom": "repeat" if (rp.get("note") or "").strip() else "",
                      "service": str(rp.get("serviceId") or "") or None, "titles": []},
                     range(int(rp["s"]), int(rp["e"])))
        attended.append([rp["s"], rp["e"], i])
    unattended = []
    for m in sorted(day.get("meetings") or [], key=lambda m: m["s"]):
        if m.get("all_day") or m.get("declined") or m["e"] - m["s"] < 5:
            continue
        s, e = m["s"], m["e"]
        title = (m.get("title") or "").strip()
        same = next((x for x in attended if _overlap(x[0], x[1], s, e) >= 0.5 * min(x[1] - x[0], e - s)), None)
        if same:  # the repeat already stands for this call: one entry, with the meeting's title if it has no note
            c = claims[same[2]]
            if title and not c.get("note"):
                c["note"], c["noteFrom"] = title, "meeting"
            if title:
                c["why"] += f"; on your calendar as “{title}”"
            continue
        in_log = sum(1 for x in range(int(s), int(e)) if 0 <= x < N and owner[x] is not None
                     and claims[owner[x]]["kind"] == "logged")
        if in_log >= 0.5 * (e - s):
            continue  # logged already
        t = (m.get("tasks") or [None])[0]
        if t:
            known(str(t["taskId"]), t.get("title"), t.get("project"))
            n, of = t.get("n") or 0, t.get("of") or 0
            conf = "high" if n >= 3 and n / of >= 0.6 else "medium" if n >= 2 and n / of >= 0.4 else "low"
            why = (f"you logged it during {n} of the last {of} meetings like this" if n else
                   t.get("why") or "a task whose name shares a word with the meeting")
        else:
            conf, why = "low", "a meeting on your calendar; no task found for it"
        seen = any(_overlap(a - MEETING_SLACK, b + MEETING_SLACK, s, e) > 0 for a, b in calls)
        c = {"kind": "meeting", "source": "meeting", "task": str(t["taskId"]) if t else None, "conf": conf,
             "why": why, "note": title, "noteFrom": "meeting" if title else "", "titles": [], "meeting": title,
             "alts": [str(x["taskId"]) for x in (m.get("tasks") or [])[1:3]]}
        if seen:
            i, _ = claim({**c, "attended": True}, range(int(s), int(math.ceil(e))))
            attended.append([s, e, i])
        else:
            unattended.append((m, c))
    for a, b in calls:  # what is left of each call once the meetings above have their minutes
        b = max(b, a + 5)
        near = [x for x in attended if _overlap(x[0] - MEETING_SLACK, x[1] + MEETING_SLACK, a, b) > 0]
        free = [x for x in range(int(a), min(N, int(math.ceil(b)))) if owner[x] is None
                and not any(y[0] - MEETING_SLACK <= x < y[1] + MEETING_SLACK for y in near)]
        if len(free) < CALL_MIN:
            continue  # a glance at a call window, or the edges of a meeting already placed
        i, got = claim({"kind": "meeting", "source": "call", "task": None, "conf": "low", "attended": True,
                        "why": "a call the tracker saw, with no calendar entry: pick the task", "note": "", "noteFrom": "",
                        "titles": []}, free)
        if got:
            attended.append([free[0], free[-1] + 1, i])

    # 3. Likely rows: focused work first, at its own time; agent-only rows after, on free active minutes only
    rows = [r for r in day.get("rows") or [] if (r.get("minutes") or r["e"] - r["s"]) >= 1]
    picks = day.get("picks") or {}
    assigned = day.get("assigned") or []
    for a in assigned:
        known(str(a["taskId"]), a.get("title"), a.get("project"))
    for t, n in (ctx.get("names") or {}).items():
        known(t, n.get("task"), n.get("project"))
    sameday: dict = {}
    for i in sorted(range(len(rows)), key=lambda i: (rows[i].get("kind") == "parallel", rows[i]["s"])):
        r = rows[i]
        agent = r.get("kind") == "parallel"
        if r.get("project") in (ctx.get("never_logged") or ()) and not r.get("tasks"):
            claim({"kind": "skip", "project": r.get("project"), "why": f"{r.get('project')}: you never log this repository"},
                  [x for x in range(int(r["s"]), int(r["e"])) if not away[x]])
            continue
        hit = rule_in(items, day.get("rules") or [], r["s"], r["e"],
                      " ".join([r.get("project") or ""] + list(r.get("titles") or []) + [r.get("first_message") or ""]))
        if hit:
            known(str(hit["taskId"]), hit.get("title"), hit.get("project"))
        pick = picks.get(f"{r.get('pick_s', r['s'])}|{r.get('project') or ''}")
        for t in (pick or {}).get("tasks") or []:
            known(str(t["taskId"]), t.get("title"), t.get("project"))
        for t in r.get("learned") or []:
            known(str(t["taskId"]), t.get("title"), t.get("project"))
        ch = choose(r, ctx, hit, sameday, assigned, pick)
        note = (pick or {}).get("note") or ""
        claude_note = bool(note and ch["task"] and str(pick["tasks"][0]["taskId"]) == ch["task"])
        # else the row's own commits (commits.py): they say what was done whatever the task
        note, note_from = (note, "claude") if claude_note else (git_commits.note(r.get("commits") or []), "commits")
        c = {"kind": "agent" if agent else "focused", "task": ch["task"], "source": ch["source"],
             "conf": _min_conf(ch["conf"], "medium") if agent else ch["conf"], "why": ch["why"],
             "project": r.get("project"), "titles": r.get("titles") or [], "alts": [x["taskId"] for x in ch["alts"]],
             "altWhy": {x["taskId"]: x["why"] for x in ch["alts"]},
             "note": note, "noteFrom": note_from if note else "",
             "claudeNone": ch["claudeNone"]}
        span = range(int(r["s"]), min(N, int(math.ceil(r["e"]))))
        own = bool(OWN_REPO.match(r.get("project") or ""))
        claim(c, [x for x in span if not away[x] and (own or not logging_[x]) and (work[x] or not agent)])
        if ch["task"] and ch["source"] in ("rule", "link", "session") and ch["conf"] == "high":
            for k in _keys(r):
                sameday.setdefault(k, ch["task"])

    # minutes Likely left out on purpose (time spent logging, a session it set aside): never proposed
    for x in range(N):
        if work[x] and owner[x] is None and (editor[x] or logging_[x]):
            owner[x] = BLOCKED

    # 4. calendar meetings with no call seen: on free active minutes only, unticked; the rest is listed as skipped
    skipped = []
    for m, c in unattended:
        free = [x for x in range(int(m["s"]), min(N, int(math.ceil(m["e"])))) if work[x] and owner[x] is None]
        claim({**c, "kind": "unattended", "why": c["why"] + "; no call was seen, so it starts unticked"}, free)
        rest = int(m["e"] - m["s"]) - len(free)
        if rest >= 5:
            skipped.append({"s": m["s"], "e": m["e"], "minutes": rest, "title": m.get("title") or "",
                            "why": "on your calendar, no call seen and no activity: add it by hand if you were there"})

    # work outside Claude (a browser, Slack): stretches of unclaimed active minutes
    runs, cur = [], None
    for x in range(N):
        if work[x] and owner[x] is None:
            if cur and x - cur[1] <= 2:
                cur[1], cur[2] = x, cur[2] + 1
            else:
                if cur:
                    runs.append(cur)
                cur = [x, x, 1]
    if cur:
        runs.append(cur)
    short = []
    for a, b, n in runs:
        if n >= o["out_min"]:
            claim({"kind": "outside", "task": None, "source": "neighbour", "conf": "low", "titles": [],
                   "why": "work outside Claude (a browser, Slack): the task of the work next to it"},
                  [x for x in range(a, b + 1) if work[x]])
        else:
            short.append((a, b))
    for a, b in short:  # a short one joins the entry beside it
        left = next((owner[x] for x in range(a - 1, max(0, a - 3), -1) if owner[x] not in (None, BLOCKED)), None)
        right = next((owner[x] for x in range(b + 1, min(N, b + 3)) if owner[x] not in (None, BLOCKED)), None)
        pick_i = left if left is not None else right
        if pick_i is not None and claims[pick_i]["kind"] not in ("logged", "skip"):
            for x in range(a, b + 1):
                if work[x] and owner[x] is None:
                    owner[x] = pick_i

    # pieces: runs of minutes per claim, in time order
    pieces, cur = [], None
    for x in range(N):
        i = owner[x] if owner[x] != BLOCKED else None
        if i is not None and cur and cur["claim"] == i and x - cur["last"] <= 2:
            cur["last"], cur["minutes"] = x, cur["minutes"] + 1
        elif i is not None:
            if cur:
                pieces.append(cur)
            cur = {"claim": i, "s": x, "last": x, "minutes": 1}
    if cur:
        pieces.append(cur)
    for p in pieces:
        p.update({k: v for k, v in claims[p["claim"]].items() if k not in ("s", "e")})
    pieces = [p for p in pieces if p["kind"] != "logged"]
    for k, p in enumerate(pieces):  # outside stretches take the task of the work before them, else after
        if p["kind"] == "outside" and not p.get("task"):
            near = ([pieces[k - 1]] if k else []) + pieces[k + 1:k + 2]
            nb = [q for q in near if q.get("task") and q["kind"] in ("focused", "agent")]  # never a meeting's task
            if any(q["kind"] == "skip" for q in near) and not nb:
                p["kind"], p["why"] = "skip", "next to work in a repository you never log"
            elif nb:
                q = nb[0]
                p.update(task=q["task"], project=q.get("project"), alts=list(q.get("alts") or []))

    # one task in one sitting: one entry
    merged: list = []
    for p in pieces:
        m = merged[-1] if merged else None
        if m and m.get("task") and m["task"] == p.get("task") and "skip" not in (m["kind"], p["kind"]) \
                and p["s"] - m["last"] <= o["merge_gap"] and sitting[m["last"]] == sitting[p["s"]]:
            m["minutes"] += p["minutes"]
            m["last"] = p["last"]
            m["parts"].append(p)
        else:
            merged.append({**p, "parts": [p]})
    # pieces too short to log join a neighbour; a meeting someone attended stays an entry of its own from 5 minutes
    if o["min_entry"]:
        changed = True
        while changed:
            changed = False
            for k, p in enumerate(merged):
                keep = p.get("attended") and p["minutes"] >= 5
                if p["minutes"] < o["min_entry"] and p["kind"] != "skip" and not keep:
                    nb = [q for q in (merged[k - 1] if k else None, merged[k + 1] if k + 1 < len(merged) else None)
                          if q is not None and q["kind"] != "skip"]
                    if nb:
                        q = max(nb, key=lambda q: q["minutes"])
                        q["minutes"] += p["minutes"]
                        q["s"], q["last"] = min(q["s"], p["s"]), max(q["last"], p["last"])
                        q["parts"] += p["parts"]
                        merged.pop(k)
                        changed = True
                        break
    skipped += [{"s": m["s"], "e": m["last"] + 1, "minutes": m["minutes"], "project": m.get("project") or "",
                 "why": m.get("why") or ""} for m in merged if m["kind"] == "skip"]
    entries = [m for m in merged if m["kind"] != "skip"]

    # lay out: in time order, no overlap with each other or with what is logged, boundaries on the grid
    g = o["grid"] or 1
    out, end, unplaced = [], 0, []
    for m in sorted(entries, key=lambda m: m["s"]):
        s = max(int(round(m["s"] / g) * g), end)
        e = int(round((s + m["minutes"]) / g) * g)
        for a, b in busy:  # an entry already logged: start after it, or stop where it starts
            if a <= s < b:
                length = e - s
                s = int(math.ceil(b / g) * g)
                e = s + length
            elif s < a < e:
                unplaced.append({"minutes": e - int(a), "task": m.get("task"), "project": m.get("project") or "",
                                 "why": "no room before an entry already logged"})
                e = int(a)
        e = min(e, N)
        if e - s < (5 if m.get("attended") else g):
            continue  # under half a grid step: rounding
        end = e
        out.append(_entry(m, s, e, names))

    # the cap: proposed plus logged never more than active time plus attended meetings; the rest is shown, not placed
    att = bytearray(N)
    for a, b, _ in attended:
        for x in range(max(0, int(a)), min(N, int(math.ceil(b)))):
            att[x] = 1
    active = sum(work)
    cap = sum(1 for x in range(N) if work[x] or att[x])
    logged_total = sum(int(r["minutes"]) for r in logged)
    allowed = max(0, cap - logged_total)
    over = sum(e["minutes"] for e in out) - allowed
    if over > 0:
        for e in sorted(out, key=lambda e: (RANK[e["confidence"]], -e["s"])):
            if over <= 0:
                break
            cut = min(e["minutes"], int(math.ceil(over / g) * g))
            e["minutes"] -= cut
            e["e"] = e["s"] + e["minutes"]
            over -= cut
            unplaced.append({"minutes": cut, "task": e["taskId"], "project": e["project"],
                             "why": "more than your active time and meetings"})
        out = [e for e in out if e["minutes"] >= g]
    # active time no entry covers, logged or proposed
    covered = bytearray(N)
    for a, b in busy + [(e["s"], e["e"]) for e in out]:
        for x in range(max(0, int(a)), min(N, int(math.ceil(b)))):
            covered[x] = 1
    uncovered, start = [], None
    for x in range(N + 1):
        gap = x < N and work[x] and not covered[x]
        if gap and start is None:
            start = x
        if not gap and start is not None:
            if x - start >= 3:
                uncovered.append({"s": start, "e": x, "minutes": x - start})
            start = None
    return {"entries": out, "unplaced": unplaced, "skipped": skipped, "uncovered": uncovered,
            "totals": {"proposed": sum(e["minutes"] for e in out),
                       "checked": sum(e["minutes"] for e in out if e["checked"]),
                       "logged": logged_total, "active": active, "meetings": sum(att), "cap": cap,
                       "notCovered": sum(u["minutes"] for u in uncovered),
                       "over": sum(u["minutes"] for u in unplaced if u["why"].startswith("more than"))}}


def _entry(m: dict, s: int, e: int, names: dict) -> dict:
    task = m.get("task")
    minutes = e - s
    conf = _worst(m["parts"], m["minutes"])
    # the part with most minutes says why
    lead = max(m["parts"], key=lambda q: q["minutes"])
    kinds: dict = defaultdict(int)
    for q in m["parts"]:
        kinds[q["kind"]] += q["minutes"]
    kind = max(kinds, key=kinds.get)
    flags = []
    if not task:
        flags.append("notask")
    if conf == "low":
        flags.append("low")
    if kinds.get("agent", 0) >= 0.5 * m["minutes"]:
        flags.append("agent")
    if kinds.get("unattended", 0) >= 0.5 * m["minutes"]:
        flags.append("unattended")
    # a note only from a part of this task (a meeting's title, a repeat's usual note, Claude's draft for this task,
    # its commits);
    # an entry with no task keeps the note only when that part is most of it
    noted = [q for q in sorted(m["parts"], key=lambda q: -q["minutes"]) if q.get("note")
             and (q.get("task") == task if task else q is lead)]
    note, note_from = (noted[0]["note"], noted[0].get("noteFrom") or "") if noted else ("", "")
    title, project = names.get(str(task), ("", "")) if task else ("", "")
    alts = []
    for q in sorted(m["parts"], key=lambda q: -q["minutes"]):
        for t in q.get("alts") or []:
            if t and t != task and t not in [a["taskId"] for a in alts]:
                n = names.get(str(t), ("", ""))
                alts.append({"taskId": str(t), "title": n[0], "project": n[1],
                             "why": (q.get("altWhy") or {}).get(t) or ""})
    return {"s": s, "e": e, "minutes": minutes, "taskId": task, "title": title, "project": project,
            "repo": lead.get("project") or "", "meeting": lead.get("meeting") or "",
            "serviceId": lead.get("service"), "service": "", "note": note or "", "noteFrom": note_from or "",
            "confidence": conf, "confidenceWords": CONF_WORDS[conf], "why": lead.get("why") or "",
            "source": lead.get("source") or "", "kind": kind, "checked": not flags, "flags": flags,
            "alts": alts[:3], "parts": len(m["parts"]),
            # Ask Claude looked at the work that carries this entry and found no task: the guess stays, flagged
            "claudeNone": bool(lead.get("claudeNone"))}


# ---- the service: the one last used on the task, if it still takes time on that date ------------------------------

def pick_service(services: list, last_id=None, last_name: str = "", task_service=None):
    """The service you used last on the task; else one of the same name (a newer budget); else the task's own; else
    the only one. None when it has to be picked (the log dialog does the same)."""
    by = {str(s["id"]): s for s in services}
    if last_id and str(last_id) in by:
        return by[str(last_id)]
    if last_name:
        same = [s for s in services if (s.get("name") or "").strip() == last_name.strip()]
        if same:
            return same[0]
    if task_service and str(task_service) in by:
        return by[str(task_service)]
    return services[0] if len(services) == 1 else None


# ---- reading one day through Pulsar ----------------------------------------------------------------------------

_services: dict = {}  # (task, date, person) -> (when, answer)


def _bookable(task: str, date: str, settings: dict) -> dict:
    from . import productive
    key = (task, date, settings.get("person_id"))
    kept = _services.get(key)
    if kept and time.time() - kept[0] < 600:
        return kept[1]
    got = productive.bookable_services(task, date, settings)
    _services[key] = (time.time(), got)
    for old in sorted(_services, key=lambda k: _services[k][0])[:-300]:
        _services.pop(old, None)
    return got


def gather(date: str, source: str = "auto", ignore_logged: bool = False) -> dict:
    """The proposal for `date` from Pulsar's live data. ignore_logged (test servers only) plans as if nothing were
    logged that day, to show a whole proposal on a day already logged."""
    from concurrent.futures import ThreadPoolExecutor
    from . import askclaude, calendar, learn as learning, likely, logbook, productive, server
    s = server.current_settings()
    tz = config.tz(s)
    t0, t1 = server.day_bounds(date, tz)
    if not s.get("person_id"):
        return {"error": "Connect Productive in Settings first.", "entries": []}
    first28 = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=server.REPEAT_DAYS)).strftime("%Y-%m-%d")

    def proposal():
        if not likely.enabled(s):
            return {"entries": []}
        got = likely.proposal(date, config.timezone_name(s), server.today(tz), False, s)
        if got.get("error"):
            return {"entries": [], "error": got["error"]}
        shift = datetime.fromtimestamp(t0, tz).utcoffset() != datetime.fromtimestamp(t1 - 1, tz).utcoffset()
        rows = []
        for e in got.get("entries") or []:  # a copy: the cached proposal stays as it is
            r = {**e, "pick_s": e["s"]}  # Ask Claude names a row by its minute before any clock change
            if shift:  # the clocks change today: rows by the clock, like the activity
                r["s"], r["e"] = round(server.clock(t0 + e["s"] * 60, t0, tz), 2), round(server.clock(t0 + e["e"] * 60, t0, tz), 2)
            rows.append(r)
        return {**got, "entries": rows}

    def suggest():
        try:
            return server.api_suggest({"date": date}).get("assigned") or []
        except Exception:
            return []

    with ThreadPoolExecutor(8) as pool:
        jobs = {"act": pool.submit(server.api_activity, {"date": date, "source": source}),
                "logged": pool.submit(server.productive_rows, date, date, s),
                "history": pool.submit(server.history_rows, date, s, learning.LEARN_DAYS),
                "likely": pool.submit(proposal),
                "repeats": pool.submit(server.api_repeats, {"date": date, "source": source}),
                "cal": pool.submit(calendar.day, date, s),
                "past": pool.submit(calendar.history, first28, server.REPEAT_DAYS, s),
                "assigned": pool.submit(suggest)}
        got = {k: j.result() for k, j in jobs.items()}
    rows, problem, _, from_copy = got["logged"]
    if problem and not ignore_logged:
        # without the day's entries a proposal could log time twice: none, and say why
        return {"error": f"Productive could not be read, so nothing is proposed. {problem}", "entries": []}
    history, hproblem = got["history"]
    logged = []
    if not ignore_logged:
        for x in rows:
            start = None if x.get("start") is None else round(server.clock(x["start"], t0, tz), 2)
            logged.append({"s": start, "minutes": x["minutes"], "taskId": x.get("taskId"), "id": x.get("id")})
    act = got["act"]
    calls = [{"s": it["s"], "e": it["e"]} for it in act.get("items") or [] if it.get("cat") == "work" and server.is_call(it)]
    meetings = [m for m in (got["cal"].get("events") or []) if not m.get("all_day")]
    skip = set(server.WORD.findall(str(s.get("person_name") or "").lower()))
    offered = []
    if meetings:
        try:
            sug = server.api_suggest({"date": date})
            offered = (sug.get("recent") or []) + (sug.get("assigned") or [])
        except Exception:
            offered = []
        hist28 = [r for r in history if r.get("date", "") >= first28]
        for m in meetings:
            m["tasks"] = server.meeting_tasks(m, got["past"], hist28, offered, skip)
    likely_rows = got["likely"].get("entries") or []
    if likely_rows and not hproblem:  # learned tasks per row, from the days before only (alternatives in the review)
        learning.annotate({"entries": likely_rows}, date, s, history, [], tz)
    t0_of = lambda d: server.day_bounds(d, tz)[0]  # noqa: E731
    days = {d: learning._stored(d) for d in learning._days_before(date)}
    ctx = learn(date, [] if hproblem else history, {d: r for d, r in days.items() if r}, t0_of,
                person=str(s.get("person_name") or ""))
    answer = askclaude.latest(date)
    day = {"items": act.get("items") or [], "away": act.get("away") or [], "rows": likely_rows, "logged": logged,
           "meetings": meetings, "repeats": got["repeats"].get("repeats") or [], "calls": calls,
           "rules": s.get("rules") or [], "assigned": got["assigned"], "picks": (answer or {}).get("picks") or {}}
    result = plan(day, ctx)

    # the service of each task, checked against what takes time on that date
    tasks = sorted({e["taskId"] for e in result["entries"] if e["taskId"]})

    def services(tid):
        try:
            return tid, _bookable(tid, date, s), None
        except Exception as exc:  # the row says so; the person picks one when logging
            return tid, None, str(exc)

    with ThreadPoolExecutor(4) as pool:
        found = {tid: (b, err) for tid, b, err in pool.map(services, tasks)}
    for e in result["entries"]:
        if not e["taskId"]:
            continue
        b, err = found.get(e["taskId"], (None, "not read"))
        if not b:
            e["serviceProblem"] = err or "Productive did not list its services"
            e["serviceId"] = None
            e["checked"] = False
            e["flags"].append("noservice")
            continue
        task = b.get("task") or {}
        e["title"] = e["title"] or task.get("title") or ""
        e["project"] = e["project"] or task.get("project") or ""
        last_id, last_name = ctx["service"].get(e["taskId"], (None, ""))
        svc = pick_service(b.get("services") or [], e.get("serviceId") or last_id, last_name, task.get("serviceId"))
        e["services"] = [{"id": x["id"], "name": (x.get("name") or "").strip(), "budget": x.get("budget") or "",
                          "billable": x.get("billable")} for x in b.get("services") or []]
        if svc:
            e["serviceId"], e["service"], e["budget"], e["billable"] = svc["id"], (svc.get("name") or "").strip(), \
                svc.get("budget") or "", svc.get("billable")
        else:
            e["serviceId"] = None
            e["checked"] = False
            e["flags"].append("noservice")
    for e in result["entries"]:
        for a in e["alts"]:
            if not a["title"] and a["taskId"] in found and found[a["taskId"]][0]:
                t = found[a["taskId"]][0].get("task") or {}
                a["title"], a["project"] = t.get("title") or "", t.get("project") or ""
    # rows a run of this day already sent: shown as such, and the writer refuses them again
    sent = {sig(date, e2["start"], e2["minutes"], e2["taskId"], e2["serviceId"]): e2
            for run in runs_for(date) for e2 in run["rows"] if e2.get("status") in ("sending", "unknown", "logged")}
    for e in result["entries"]:
        x = sent.get(sig(date, _hm(e["s"]), e["minutes"], e["taskId"], e["serviceId"]))
        if x:
            e["sent"] = x["status"]
            e["checked"] = False
    if got["likely"].get("error"):
        logbook.record("log the day", f"Likely: {got['likely']['error']}", kind="warning", once_per=600)
    return {**result, "date": date, "ignoreLogged": ignore_logged, "fromCopy": bool(from_copy),
            "warning": problem if from_copy else None, "likelyError": got["likely"].get("error"),
            "calendar": bool(got["cal"].get("connected")), "claude": bool(answer),
            "learning": got["likely"].get("learning")}


def _hm(m) -> str:
    m = int(round(m)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


# ---- the journal: every write a run sends, before it is sent ------------------------------------------------------

RUN = re.compile(r"^ld-\d{4}-\d{2}-\d{2}-[0-9a-f]{8,32}$")
ROW = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
KEEP_DAYS = 31
_journal_lock = threading.RLock()


def journal_path() -> str:
    return os.path.join(config.CACHE_DIR, "logday-journal.json")


def journal() -> dict:
    try:
        with open(journal_path(), encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and isinstance(doc.get("runs"), list):
            return doc
    except (OSError, ValueError):
        pass
    return {"runs": []}


def sig(date, start, minutes, task, service) -> str:
    return f"{date}|{start}|{int(minutes or 0)}|{task or ''}|{service or ''}"


def runs_for(date: str) -> list:
    return [r for r in journal()["runs"] if r.get("date") == date]


def check(run: str, row: str, date: str, start: str, minutes: int, task: str, service: str) -> None:
    """Refuses a row this run already sent, or the same entry any run of that day sent and that is logged or whose
    answer never came: a restart, a second tab or a retry never writes it twice."""
    if not RUN.match(run or "") or not ROW.match(row or "") or run[3:13] != date:
        raise ValueError("A Log the day run needs its run and row names.")
    mine = sig(date, start, minutes, task, service)
    for r in journal()["runs"]:
        for x in r.get("rows") or []:
            if r.get("run") == run and x.get("row") == row and x.get("status") in ("sending", "unknown", "logged"):
                raise ValueError(_sent_reason(x))
            if r.get("date") == date and x.get("sig") == mine and x.get("status") in ("sending", "unknown", "logged"):
                raise ValueError(_sent_reason(x))


def _sent_reason(x: dict) -> str:
    if x.get("status") == "logged":
        return f"This entry was logged at {x.get('at', '')[11:16]} (entry {x.get('id')}). It is not sent twice."
    return (f"This entry was sent at {x.get('at', '')[11:16]} and Pulsar never heard back. Check the Logged column; "
            "Pulsar will not send it again, so log it by hand if it is missing.")


def mark(run: str, date: str, row: str, entry: dict, status: str, **fields) -> None:
    """Records one row's state, written to disk at once (owner-only, atomic): "sending" goes in before the write."""
    with _journal_lock:
        doc = journal()
        cutoff = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
        runs = [r for r in doc["runs"] if str(r.get("started") or "")[:10] >= cutoff or r.get("run") == run][-200:]
        rec = next((r for r in runs if r.get("run") == run), None)
        if rec is None:
            rec = {"run": run, "date": date, "started": datetime.now().isoformat(timespec="seconds"), "rows": []}
            runs.append(rec)
        x = next((x for x in rec["rows"] if x.get("row") == row), None)
        if x is None:
            x = {"row": row}
            rec["rows"].append(x)
        x.update({"date": date, "start": entry.get("start"), "minutes": entry.get("minutes"), "taskId": entry.get("taskId"),
                  "serviceId": entry.get("serviceId"),
                  "sig": sig(date, entry.get("start"), entry.get("minutes"), entry.get("taskId"), entry.get("serviceId")),
                  "status": status, "at": datetime.now().isoformat(timespec="seconds"), **fields})
        config.write_atomic(journal_path(), json.dumps({"runs": runs}, ensure_ascii=False))


def undo_ids(doc: dict, run: str) -> list:
    """The entries this run created and still stands by: only its own rows marked logged, with their ids."""
    rec = next((r for r in doc.get("runs") or [] if r.get("run") == run), None)
    return [{"id": str(x["id"]), "row": x.get("row"), "date": x.get("date"), "start": x.get("start"),
             "minutes": x.get("minutes"), "taskId": x.get("taskId")}
            for x in (rec or {}).get("rows") or [] if x.get("status") == "logged" and x.get("id")]


def mark_undone(run: str, entry_id: str) -> None:
    with _journal_lock:
        doc = journal()
        for r in doc["runs"]:
            if r.get("run") == run:
                for x in r.get("rows") or []:
                    if str(x.get("id")) == str(entry_id) and x.get("status") == "logged":
                        x["status"], x["undoneAt"] = "undone", datetime.now().isoformat(timespec="seconds")
        config.write_atomic(journal_path(), json.dumps(doc, ensure_ascii=False))
