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
4. on free active minutes only: agent-only rows, calendar meetings with no call seen, work outside Claude (an editor
   with no session row too).

Then the pieces of one task in one sitting join, short ones join a neighbour close by in the same sitting (never
across an entry already logged, and nothing before a meeting someone attended joins it: it keeps its own time),
boundaries go on a grid (5 minutes, or Settings' "Round entries to": opts_for), nothing overlaps an entry already
logged, and the total is capped: proposed plus logged never exceeds active time plus attended meetings. What does not
fit is shown in `unplaced`, never placed.

Notes are never copied from session text: only a meeting's title that passes the note's checks (askclaude.guard_note:
anyone can put an event on a calendar), a repeat's usual note, a note Claude drafted
(Ask Claude, from the session's messages; its row starts unticked) for that task, or the subjects of the person's own commits in the work (commits.py), each marked so the
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

from . import attribution, commits as git_commits, config
from .attribution import CLIENT, INTERNAL, repo_key

GRID = 5             # entry boundaries on this grid, in minutes (Settings: "Round entries to", opts_for)
MIN_ENTRY = 10       # shorter pieces join a neighbour (never shorter than the grid)
MERGE_GAP = 30       # pieces of one task this close, in one sitting, are one entry
OUT_MIN = 10         # active minutes outside Claude from this long become an entry of their own
SITTING_GAP = config.DEFAULTS["away_after"]  # minutes without activity that end a sitting: Settings' "Away after" (opts_for)
CALL_GAP = 20        # a call goes on while its window shows again within this many minutes
CALL_MIN = 10        # a shorter call is a glance at a call window, not a meeting
MEETING_SLACK = 10   # a call this close to a calendar meeting is that meeting
HALF_LIFE = 7        # days: older logging counts half as much this many days later
LEARN_DAYS = 60
RECENT_DAYS = 30
RANK = {"low": 0, "medium": 1, "high": 2}
ORDER = ("rule", "link", "ticket", "session", "scoped", "repo", "sameday", "words")
BLOCKED = -1

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
NEW_PICK_MIN = 0.5   # the words a task you never logged to must share with the work to be picked (or a key named)
FIT_SHARE = attribution.FIT_SHARE  # a project with less of a repository's logged time than this is another client's
FIT_MIN = attribution.FIT_MIN      # minutes of a repository's (fading) votes before its projects say anything
CONTEST_MIN = 10     # a client's agent takes internal work's minutes (shared) only for this long at a stretch
CLASS_ORDER = {INTERNAL: 0, "mixed": 1, "unknown": 1, CLIENT: 2}  # what the cap cuts first


def opts_for(settings: dict) -> dict:
    """plan()'s grid and shortest entry from Settings' "Round entries to" (5, 10 or 15 minutes): 5 keeps today's 5 and
    10; 10 gives 10 and 10; 15 gives 15 and 15. And the end of a sitting from Settings' "Away after"."""
    step = settings.get("round_to")
    step = step if step in config.ROUNDING and not isinstance(step, bool) else GRID
    return {"grid": step, "min_entry": max(MIN_ENTRY, step), "sitting_gap": config.away_after(settings)}


def words(text: str, skip: frozenset = frozenset()) -> set:
    """The words of a text for matching task names: three letters or more, without common English and Ukrainian words
    ("not" once matched a task name) or `skip`."""
    from .learn import _STOP
    return {w for w in WORD.findall((text or "").lower()) if w not in GENERIC and w not in _STOP and w not in skip}


def fits(ctx: dict, repo: str, task) -> bool | None:
    """Whether a task's project is one this repository is logged to (FIT_SHARE of its votes): None when the
    repository has too little history or the task's project is not known."""
    names = ctx.get("names") or {}
    return attribution.fits((ctx.get("repo") or {}).get(repo_key(repo)) or {},
                            lambda t: (names.get(str(t)) or {}).get("project"), task)


def same_client(ctx: dict, repo: str, task) -> bool:
    """A task of the client this repository works for: its project is logged to the repository, or shares a word of
    four letters or more with the repository's name."""
    if fits(ctx, repo, task) is True:
        return True
    project = ((ctx.get("names") or {}).get(str(task)) or {}).get("project") or ""
    return bool(attribution.name_words(repo_key(repo)) & attribution.name_words(project))


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
    """What the days before `date` say: votes session key -> task, (session key, repository) -> task and repository ->
    task (each earlier Likely row votes, with its minutes, for the task logged over most of it, credited to the
    background row under it when that one's repository is the one logged to the task's project: attribution.credits;
    older days count less), the service last used per task, names, the tasks logged lately, repositories worked in but
    never logged (a side project), and each repository's billing class (attribution.repo_classes).

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
    sr: dict = defaultdict(lambda: defaultdict(float))
    seen: dict = defaultdict(float)
    logged: dict = defaultdict(float)
    # an entry with no start time cannot be matched to the work, so a day that has one says nothing about whether a
    # repository is logged (2026-09: the person's main repositories were set aside as "never logged")
    untimed = {d for d, rs in by_day.items() if any(r.get("start") is None for r in rs)}
    window = {d: rows for d, rows in days.items() if lo <= d < date and rows}
    for d, rows in window.items():
        if d in untimed:
            continue
        t0 = t0_of(d)
        for row in rows:
            m = row["e"] - row["s"]
            if m < 5 or row.get("bg"):
                continue
            _, cov = main_task(by_day.get(d, []), t0 + row["s"] * 60, t0 + row["e"] * 60)
            seen[repo_key(row.get("p"))] += m
            logged[repo_key(row.get("p"))] += min(cov, m)
    project_of = {str(r["taskId"]): r.get("project") or "" for r in before}

    def task_of(d, a, b):
        t0 = t0_of(d)
        return main_task(by_day.get(d, []), t0 + a * 60, t0 + b * 60)[0]

    # each logged stretch teaches the unit whose repository is logged to its project (attribution.credits)
    credited = attribution.credits(window, task_of, project_of.get)
    for d, keys, at, task, m in credited:
        age = (day0 - datetime.strptime(d, "%Y-%m-%d")).days
        w = 0.5 ** (age / half_life) if half_life else 1.0
        for k in keys:
            sess[k][task] += m * w
            sr[(k, at)][task] += m * w
        repo[at][task] += m * w
    service, names, last, project_service = {}, {}, {}, {}
    for r in sorted(before, key=lambda r: (r["date"], r.get("start") or 0)):
        t = str(r["taskId"])
        service[t] = (str(r.get("serviceId") or "") or None, r.get("service") or "")
        if r.get("project"):  # the newest per project: a task never logged to yet takes it (pick_service)
            project_service[r["project"]] = service[t]
        names[t] = {"task": r.get("task") or "", "project": r.get("project") or ""}
        last[t] = r["date"]
    lo30 = (day0 - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
    recent = [t for t, d in sorted(last.items(), key=lambda kv: kv[1], reverse=True) if d >= lo30]
    never = {p for p in seen if p and seen[p] >= 60 and logged[p] / seen[p] < 0.1}
    task_class, project_class = attribution.task_classes(before)
    return {"sess": sess, "repo": repo, "sr": sr, "service": service, "project_service": project_service,
            "names": names, "recent": recent, "never_logged": never,
            "skip": set(WORD.findall((person or "").lower())),
            "task_class": task_class, "project_class": project_class,
            "classes": attribution.repo_classes(credited, task_class)}


# ---- a task for one Likely row -------------------------------------------------------------------------------------

def _runs(minutes: list, gap: int = 2) -> list:
    """[(first, last)] of sorted minutes, joined across gaps of up to `gap` minutes."""
    out: list = []
    for x in minutes:
        if out and x - out[-1][1] <= gap:
            out[-1][1] = x
        else:
            out.append([x, x])
    return [tuple(r) for r in out]


def ticket_task(row: dict, ctx: dict, assigned: list):
    """The task whose ticket key the row's session titles or first message name, among the tasks logged lately and
    the ones assigned to you; None without one."""
    found = set(TICKET.findall(" ".join(list(row.get("titles") or []) + [row.get("first_message") or ""])))
    if not found:
        return None
    names = ctx.get("names") or {}
    by_id = {str(a["taskId"]): a for a in assigned or []}
    for tid in list(ctx.get("recent") or []) + list(by_id):
        title = (names.get(tid) or {}).get("task") or (by_id.get(tid) or {}).get("title") or ""
        if found & set(TICKET.findall(title)):
            return tid
    return None


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
            # the session's votes in this repository; its other votes only where they may carry over
            # (attribution.session_votes: a session's history applies per repository); the share counts every vote
            # it has here, or every vote of the session
            v, here = attribution.session_votes(keys, row.get("project"), ctx["sess"], ctx.get("sr") or {},
                                                (ctx.get("repo") or {}).get(repo_key(row.get("project"))) or {},
                                                lambda t: (names.get(str(t)) or {}).get("project"), bool(row.get("single")))
            tot = (sum(v.values()) if here else sum(m for k in keys for m in (ctx["sess"].get(k) or {}).values())) or 1
            if v:
                t, m, _ = _ranked(v)[0]
                share = m / tot
                # 40-60% was "medium" and was right no more often than "low" (replay, 34 days): it starts unticked
                conf = "high" if share >= 0.6 and m >= 30 else "low"
                out.append((t, "session", conf, f"you logged it while in this Claude session ({round(share * 100)}%)"))
        elif src == "repo":
            v = ctx["repo"].get(repo_key(row.get("project"))) or {}
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
            v = ctx["repo"].get(repo_key(row.get("project"))) or {}
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
                out.append((best[0], "words", "low", "name matches: " + ", ".join(sorted(best[2]))))
    return out


def admissible(row: dict, ctx: dict, task, source: str) -> bool:
    """Whether a task may be the pick for a row of this class (row["class"], attribution.segment_class): on internal
    work a client's task only from what the work itself names (a rule, a task link, a ticket key); on a client's
    repository never another client's task from the session's history. No pick is better than a wrong billable one."""
    if not task:
        return False
    cls, tc = row.get("class"), attribution.class_of_task(task, ctx)
    if cls == INTERNAL and tc == CLIENT and source not in ("rule", "link", "ticket"):
        return False
    if cls == CLIENT and source in ("session", "sameday") and tc == CLIENT and not same_client(ctx, row.get("project"), task):
        return False
    return True


def choose(row: dict, ctx: dict, rule_hit=None, sameday: dict | None = None, assigned: list | None = None,
           pick: dict | None = None) -> dict:
    """{task, source, conf, why, alts} for one Likely row. Claude's pick (Ask Claude) replaces an answer that is not
    sure (medium or low): on the hardest days it helped most there. A task the row's class does not admit (admissible)
    is never the pick; it stays among the alternatives."""
    every = candidates(row, ctx, rule_hit, sameday or {}, assigned or [])
    found = [f for f in every if admissible(row, ctx, f[0], f[1])]
    found += [(t, s_, "low", w) for t, s_, c, w in every if not admissible(row, ctx, t, s_)]
    if found and admissible(row, ctx, found[0][0], found[0][1]):
        task, src, conf, why = found[0]
    else:
        task, src, conf, why = None, "none", "low", "no match: pick the task"
        found = [(None, "none", "low", why)] + found
    # a task you never logged to that the work names (learn.new_tasks): the first alternative, and the pick over an
    # answer that is unsure and not one you gave (a rule, a link, a ticket key): never above a sure vote
    fresh = [x for x in row.get("learned") or [] if x.get("new")]
    new_alt = [(str(fresh[0]["taskId"]), "new", "low", fresh[0].get("why") or "")] if fresh else []
    weak = conf == "low" and src not in ("rule", "link", "ticket")
    if fresh and weak and (fresh[0].get("key") or fresh[0].get("score", 0) >= NEW_PICK_MIN) \
            and admissible(row, ctx, str(fresh[0]["taskId"]), "ticket" if fresh[0].get("key") else "new"):
        if task:
            new_alt = [(task, src, conf, why)]
        task, src, conf, why = str(fresh[0]["taskId"]), "new", "low", fresh[0].get("why") or ""
    alts = []
    claude_none = bool(pick) and not pick.get("tasks") and conf != "high"  # Claude found no task: keep ours, say so
    if pick and pick.get("tasks") and conf != "high" and admissible(row, ctx, str(pick["tasks"][0]["taskId"]), "claude"):
        first = pick["tasks"][0]
        alts = [(task, src, conf, why)] if task else []
        alts = [(t, s, c, f"Pulsar's guess: {w}") for t, s, c, w in alts]
        task, src, conf, why = str(first["taskId"]), "claude", "medium", f"Claude: {pick.get('why') or 'its pick'}"
        alts += [(str(t["taskId"]), "claude", "low", "Claude: another option") for t in pick["tasks"][1:]]
    seen = {task}
    for t, s, c, w in alts + new_alt + found[1:] + [(str(x["taskId"]), "learned", "low", x.get("why") or "")
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


DAY_MIN = 1440  # minutes in the planned day


class _Day:
    """One day being planned (plan): each minute's marks (work, logging, away, personal, its sitting), who has it
    (owner: a claim's index, BLOCKED or None), the claims, what each source knows of a task's name, and what each step
    of plan() leaves for the next ones."""

    def __init__(self, day: dict, ctx: dict, o: dict):
        self.day, self.ctx, self.o = day, ctx, o
        N = DAY_MIN
        self.work, self.logging, self.away = bytearray(N), bytearray(N), bytearray(N)
        self.personal = bytearray(N)  # a personal window on screen: never work, even inside a Likely row's span
        self.items = day.get("items") or []
        for it in self.items:
            if it.get("cat") == "personal":
                for x in range(max(0, int(it["s"])), min(N, int(math.ceil(it["e"])))):
                    self.personal[x] = 1
            if it.get("cat") != "work":
                continue  # personal windows never count as work
            lg = 1 if any(LOGGING.search(str(it.get(k) or "")) for k in ("app", "host", "url")) else 0
            for x in range(max(0, int(it["s"])), min(N, int(math.ceil(it["e"])))):
                self.work[x] = 1
                self.logging[x] = self.logging[x] or lg
        for w in (day.get("away") or []) + (day.get("gone") or []):  # gone: the clock's missing spring hour
            for x in range(max(0, int(math.ceil(w["s"]))), min(N, int(w["e"]))):
                self.away[x] = 1
        self.sitting: list = [None] * N
        marks = [x for x in range(N) if self.work[x]]
        if marks:
            a = p = marks[0]
            for x in marks[1:] + [None]:
                if x is None or x - p > o.get("sitting_gap", SITTING_GAP):
                    for y in range(a, p + 1):
                        self.sitting[y] = a
                    a = x
                if x is not None:
                    p = x
        self.owner: list = [None] * N
        for w in day.get("gone") or []:  # the clock's missing spring hour: no entry can start or run there
            for x in range(max(0, int(math.ceil(w["s"]))), min(N, int(w["e"]))):
                self.owner[x] = BLOCKED
        self.claims: list = []
        self.names: dict = {}  # taskId -> (title, project): what each source knows of a task

    def known(self, tid, title="", project=""):
        if tid and (title or project) and not (self.names.get(str(tid)) or ("", ""))[0]:
            self.names[str(tid)] = (title or "", project or "")

    def claim(self, c, minutes):
        self.claims.append(c)
        i = len(self.claims) - 1
        got = 0
        for x in minutes:
            if 0 <= x < DAY_MIN and self.owner[x] is None:
                self.owner[x] = i
                got += 1
        return i, got


def plan(day: dict, ctx: dict, opts: dict | None = None, keep: dict | None = None) -> dict:
    """The proposal for one day.

    day: {"items": activity [{s, e, app, title, host, url, cat}], "away": [{s, e}], "rows": Likely rows
          [{s, e, minutes, project, kind, titles, first_message, tasks, learned}], "logged": entries already in
          Productive [{s (None without a start), e (its end on the clock), minutes, taskId}], "meetings": calendar events [{s, e, title, uid,
          declined, all_day, tasks: [{taskId, title, project, n, of}]}], "repeats": repeats seen today [{s, e, minutes,
          taskId, title, project, serviceId, service, note, days}], "calls": call windows [{s, e}], "rules",
          "assigned": [{taskId, title, project}], "picks": Ask Claude's answer {"<s>|<project>": {tasks, why, note}},
          "gone": the clock's minutes that do not exist that day (server.missing_hour), never placed}
    ctx: learn(...).
    keep: a dict that, when given, receives who has each minute before the layout joins and moves them (ranges.py).
    Returns {"entries", "unplaced", "skipped", "uncovered", "totals"}; each entry
    {s, e, minutes, taskId, title, project, serviceId, service, note, noteFrom, confidence, why, source, kind,
     checked, flags, alts}.

    The steps, each a function below, in this order: who has each minute (_logged, _meetings, _rows, time spent
    logging blocked, _unattended, _outside), the pieces they make (_pieces), entries from them (_merge), laid out on
    the grid (_layout), then the caps (_caps) and what is left uncovered (_result)."""
    d = _Day(day, ctx, {"grid": GRID, "min_entry": MIN_ENTRY, "merge_gap": MERGE_GAP, "out_min": OUT_MIN, **(opts or {})})
    _logged(d)
    _meetings(d)
    _rows(d)
    # time spent logging (Pulsar, Productive): never proposed. An editor or terminal with no session row is work
    # outside Claude, below (it was set aside, and the person logged most of it: 624 minutes in 34 days)
    for x in range(DAY_MIN):
        if d.work[x] and d.owner[x] is None and d.logging[x]:
            d.owner[x] = BLOCKED
    _unattended(d)
    _outside(d)
    pieces = _pieces(d)
    if keep is not None:  # the log dialog's range (ranges.py): each minute's claim and piece, as placed here
        keep.update(owner=d.owner, claims=d.claims, pieces=pieces, work=d.work, away=d.away, logging=d.logging,
                    names=d.names, ctx=ctx,
                    gone=[(int(math.ceil(w["s"])), int(w["e"])) for w in day.get("gone") or []])
    entries = _merge(d, pieces)
    out, unplaced = _layout(d, entries)
    out, totals = _caps(d, out, unplaced, _evidence(d, pieces))
    return _result(d, out, unplaced, totals)


def _logged(d: _Day) -> None:
    """1. entries already logged: their minutes taken (d.busy), their tasks (d.done_tasks), and the minutes logged
    with no start, by task (d.untimed)."""
    d.logged = d.day.get("logged") or []
    d.busy = []
    for r in d.logged:
        if r.get("s") is None:
            continue  # no start: not on the timeline (counted in the total, below)
        # its end on the clock: an hour later than s + minutes over the hour the clocks skip in spring
        end = max(r["s"] + r["minutes"], r.get("e") or 0)
        d.claim({"kind": "logged"}, range(int(r["s"]), int(math.ceil(end))))
        d.busy.append((r["s"], end))
    d.busy.sort()
    d.done_tasks = {str(r.get("taskId")) for r in d.logged if r.get("taskId")}
    # minutes logged with no start, by task: they may be this very work, so their task's proposals give way first
    # under the cap and start unticked (an entry with no start is on no timeline, so no overlap check sees it)
    d.untimed = defaultdict(int)
    for r in d.logged:
        if r.get("s") is None and r.get("taskId"):
            d.untimed[str(r["taskId"])] += int(r["minutes"])


def _meetings(d: _Day) -> None:
    """2. meetings the person was in: repeats (a call was seen at the usual time), calendar meetings with a call seen,
    calls with no calendar entry. A repeat and a calendar meeting on the same call are one entry. Sets d.attended
    ([s, e, claim index]) and d.unattended (calendar meetings with no call seen, for _unattended)."""
    N, owner, claims, claim, known = DAY_MIN, d.owner, d.claims, d.claim, d.known
    calls = join_calls(d.day.get("calls") or [])
    attended = d.attended = []
    for rp in d.day.get("repeats") or []:
        tid = str(rp["taskId"])
        if tid in d.done_tasks:
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
    unattended = d.unattended = []
    from .calendar import note_of
    norm = lambda t: re.sub(r"\s+", " ", str(t or "").strip().lower())  # noqa: E731
    for m in sorted(d.day.get("meetings") or [], key=lambda m: m["s"]):
        if m.get("all_day") or m.get("declined") or m["e"] - m["s"] < 5:
            continue
        s, e = m["s"], m["e"]
        title = (m.get("title") or "").strip()
        # Anyone can put an event on the calendar, and its organizer can rename it: the title is a note only from the
        # person's own company and through the note's checks (calendar.note_of); one the checks refuse leaves the row
        # unticked ("meetingtitle"), as a series whose title changed since it was last logged does ("renamed")
        title_note = note_of(title, bool(m.get("external")))
        refused = "meetingtitle" if title and not m.get("external") and not title_note else ""
        # the same call only when the repeat covers half of this meeting: a repeat stretched over the next meeting
        # must not swallow it (its call was left with no task)
        same = next((x for x in attended if _overlap(x[0], x[1], s, e) >= 0.5 * (e - s)), None)
        if same:  # the repeat already stands for this call: one entry, with the meeting's title if it has no note
            c = claims[same[2]]
            if title_note and not c.get("note"):
                c["note"], c["noteFrom"] = title_note, "meeting"
            elif refused and not c.get("note"):
                c["flag"] = refused
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
        renamed = bool(t and t.get("lastTitle") is not None and norm(t["lastTitle"]) != norm(title))
        seen = any(_overlap(a - MEETING_SLACK, b + MEETING_SLACK, s, e) > 0 for a, b in calls)
        c = {"kind": "meeting", "source": "meeting", "task": str(t["taskId"]) if t else None, "conf": conf,
             "why": why, "note": title_note, "noteFrom": "meeting" if title_note else "", "titles": [], "meeting": title,
             "alts": [str(x["taskId"]) for x in (m.get("tasks") or [])[1:3]],
             **({"flag": "renamed" if renamed else refused} if renamed or refused else {})}
        if seen:
            i, _ = claim({**c, "attended": True}, range(int(s), int(math.ceil(e))))
            attended.append([s, e, i])
        else:
            unattended.append((m, c))
    # a call that started before its meeting or ran over it is that meeting: the call's free minutes right before and
    # after it go to it, up to the meeting's own length on each side (beyond that, the rest is a call of its own,
    # below); the next meeting's minutes are not free, so meetings back to back each keep their own
    grown: dict = {}  # claim index -> (grew before, grew after): no slack on a side the call already gave it
    for item in attended:
        s, e, i = item
        length = max(1, int(math.ceil(e)) - int(s))
        for a, b in calls:
            if _overlap(a, b, s, e) <= 0:
                continue
            after, x = [], int(math.ceil(e))
            while x < min(N, int(math.ceil(b)), int(math.ceil(e)) + length) and owner[x] is None:
                after.append(x)
                x += 1
            before, x = [], int(s) - 1
            while x >= max(0, int(a), int(s) - length) and owner[x] is None:
                before.append(x)
                x -= 1
            for x in after + before:
                owner[x] = i
            if after:
                item[1] = max(item[1], after[-1] + 1)
                claims[i]["why"] += f"; the call ran {len(after)} min over"
            if before:
                item[0] = min(item[0], before[-1])
                claims[i]["why"] += f"; the call started {len(before)} min early"
            was = grown.get(i, (False, False))
            grown[i] = (was[0] or bool(before), was[1] or bool(after))
    slack = lambda y, side: 0 if grown.get(y[2], (False, False))[side] else MEETING_SLACK  # noqa: E731
    for a, b in calls:  # what is left of each call once the meetings above have their minutes
        b = max(b, a + 5)
        near = [x for x in attended if _overlap(x[0] - MEETING_SLACK, x[1] + MEETING_SLACK, a, b) > 0]
        free = [x for x in range(int(a), min(N, int(math.ceil(b)))) if owner[x] is None
                and not any(y[0] - slack(y, 0) <= x < y[1] + slack(y, 1) for y in near)]
        if len(free) < CALL_MIN:
            continue  # a glance at a call window, or the edges of a meeting already placed
        i, got = claim({"kind": "meeting", "source": "call", "task": None, "conf": "low", "attended": True,
                        "why": "a call the tracker saw, with no calendar entry: pick the task", "note": "", "noteFrom": "",
                        "titles": []}, free)
        if got:
            attended.append([free[0], free[-1] + 1, i])


def _rows(d: _Day) -> None:
    """3. Likely rows at their own time: a client's work in front first; then a client's agent that ran attended while
    internal work was in front (shared: the client goes first); then the rest in front. What else the agents did
    meanwhile is noted, never placed (the skill script's agent-only rows, moved to free time, are not used at all).
    Sets d.shared_spans, d.bg_left (the agents' other minutes, by repository) and d.parallel."""
    N, day, ctx, owner, claims, claim, known = DAY_MIN, d.day, d.ctx, d.owner, d.claims, d.claim, d.known
    work, away, logging_, personal = d.work, d.away, d.logging, d.personal
    rows = [r for r in day.get("rows") or [] if (r.get("minutes") or r["e"] - r["s"]) >= 1 and r.get("kind") != "parallel"]
    back = sorted((r for r in day.get("background") or [] if r["e"] - r["s"] >= 1), key=lambda r: r["s"])
    picks = day.get("picks") or {}
    assigned = day.get("assigned") or []
    for a in assigned:
        known(str(a["taskId"]), a.get("title"), a.get("project"))
    for t, n in (ctx.get("names") or {}).items():
        known(t, n.get("task"), n.get("project"))
    sameday: dict = {}
    # the tasks logged to in the last 30 days: Claude's pick of any other task (one it found by searching Productive)
    # starts unticked, as does any row whose note Claude drafted, so neither goes through on one click
    recent = {str(t) for t in ctx.get("recent") or ()}
    never = ctx.get("never_logged") or ()

    def present(x: int, own: bool) -> bool:
        """The person at work on this minute: a work window, not away, not personal, not logging time (except in
        Pulsar's own repository, where the Pulsar window is the work)."""
        return bool(work[x]) and not away[x] and (own or not logging_[x])

    def prepare(r: dict, bg: bool) -> dict:
        """The claim of one row: its class (attribution.segment_class), its task (choose) and why."""
        text = " ".join([r.get("project") or ""] + list(r.get("titles") or []) + [r.get("first_message") or ""])
        # a rule matches the activity in front: never the background's work
        hit = rule_in([] if bg else d.items, day.get("rules") or [], r["s"], r["e"], text)
        if hit:
            known(str(hit["taskId"]), hit.get("title"), hit.get("project"))
        hint = (str(hit["taskId"]) if hit else None) or (str(r["tasks"][0]) if r.get("tasks") else None) \
            or ticket_task(r, ctx, assigned)
        r = {**r, "class": attribution.segment_class(r.get("project"), ctx, hint)}
        pick = None if bg else picks.get(f"{r.get('pick_s', r['s'])}|{r.get('project') or ''}")
        for t in (pick or {}).get("tasks") or []:
            known(str(t["taskId"]), t.get("title"), t.get("project"))
        for t in r.get("learned") or []:
            known(str(t["taskId"]), t.get("title"), t.get("project"))
        ch = choose(r, ctx, hit, sameday, assigned, pick)
        note = (pick or {}).get("note") or ""
        claude_note = bool(note and ch["task"] and str(pick["tasks"][0]["taskId"]) == ch["task"])
        # else the row's own commits (commits.py): they say what was done whatever the task
        note, note_from = (note, "claude") if claude_note else (git_commits.note(r.get("commits") or []), "commits")
        if not bg and ch["task"] and ch["source"] in ("rule", "link", "session") and ch["conf"] == "high":
            for k in _keys(r):
                sameday.setdefault(k, ch["task"])
        return {"kind": "focused", "task": ch["task"], "source": ch["source"], "conf": ch["conf"], "why": ch["why"],
                "project": r.get("project"), "titles": r.get("titles") or [], "alts": [x["taskId"] for x in ch["alts"]],
                "altWhy": {x["taskId"]: x["why"] for x in ch["alts"]}, "class": r["class"],
                "sessions": list(r.get("sessions") or []), "note": note, "noteFrom": note_from if note else "",
                "claudeNone": ch["claudeNone"], "claudeFound": ch["source"] == "claude" and str(ch["task"]) not in recent}

    front: list = []  # (row, claim)
    for r in sorted(rows, key=lambda r: r["s"]):
        if repo_key(r.get("project")) in never and not r.get("tasks"):
            claim({"kind": "skip", "project": r.get("project"), "why": f"{r.get('project')}: you never log this repository"},
                  [x for x in range(int(r["s"]), int(r["e"])) if not away[x]])
            continue
        front.append((r, prepare(r, False)))
    in_front: list = [None] * N  # the claim of the row in front at each minute (rows in front never overlap)
    for r, c in front:
        for x in range(int(r["s"]), min(N, int(math.ceil(r["e"])))):
            in_front[x] = c

    def span_of(r: dict, c: dict) -> list:
        own = bool(OWN_REPO.match(r.get("project") or ""))
        span = range(int(r["s"]), min(N, int(math.ceil(r["e"]))))
        if c["class"] == CLIENT:  # a billable minute needs the person there: a work window under it
            return [x for x in span if present(x, own)]
        return [x for x in span if not away[x] and (own or not logging_[x]) and not (personal[x] and not work[x])]

    for r, c in front:  # 3a. a client's work in front
        if c["class"] == CLIENT:
            claim(c, span_of(r, c))
    d.shared_spans = []
    for r in back:  # 3b. a client's agent, attended, over internal work in front: the client goes first (shared)
        if not r.get("qualifies") or (repo_key(r.get("project")) in never and not r.get("tasks")):
            continue
        c = prepare(r, True)
        # only a client's task, a known one: priority never makes a task billable, and never adds a minute
        if c["class"] != CLIENT or not c["task"] or attribution.class_of_task(c["task"], ctx) == INTERNAL:
            continue
        free = [x for x in range(int(r["s"]), min(N, int(math.ceil(r["e"]))))
                if present(x, False) and owner[x] is None and in_front[x] is not None and in_front[x]["class"] == INTERNAL]
        for a, b in _runs(free, 3):  # a glance elsewhere of up to two minutes does not cut the stretch
            minutes = [x for x in free if a <= x <= b]
            if len(minutes) < CONTEST_MIN:
                continue  # a short one stays with the work in front
            under: dict = defaultdict(int)
            for x in minutes:
                under[id(in_front[x])] += 1
            lead = next(f for _, f in front if id(f) == max(under, key=under.get))
            _, got = claim({**c, "kind": "shared", "shared": True, "with": lead}, minutes)
            if got:
                d.shared_spans.append((a, b + 1))
    for r, c in front:  # 3c. the rest of the work in front
        if c["class"] != CLIENT:
            claim(c, span_of(r, c))
    # what else the agents did while the person was at work: the minutes no claim of theirs took, by repository
    d.bg_left = {}
    d.parallel = bytearray(N)
    for r in back:
        for x in range(int(r["s"]), min(N, int(math.ceil(r["e"])))):
            if not present(x, False):
                continue
            d.parallel[x] = 1
            who = owner[x]
            if who not in (None, BLOCKED) and claims[who].get("kind") == "shared" \
                    and claims[who].get("project") == r.get("project"):
                continue
            d.bg_left.setdefault(r.get("project") or "", set()).add(x)


def _unattended(d: _Day) -> None:
    """4. calendar meetings with no call seen: on free active minutes only, unticked; the rest is listed as skipped
    (d.skipped)."""
    d.skipped = []
    for m, c in d.unattended:
        free = [x for x in range(int(m["s"]), min(DAY_MIN, int(math.ceil(m["e"])))) if d.work[x] and d.owner[x] is None]
        d.claim({**c, "kind": "unattended"}, free)  # the row says "No call seen" (logday.js)
        rest = int(m["e"] - m["s"]) - len(free)
        if rest >= 5:
            d.skipped.append({"s": m["s"], "e": m["e"], "minutes": rest, "title": m.get("title") or "",
                              "why": "on your calendar, no call seen and no activity: add it by hand if you were there"})


def _outside(d: _Day) -> None:
    """5. work outside Claude (a browser, Slack): stretches of unclaimed active minutes; a short one joins the entry
    beside it."""
    N, work, away, owner, claims = DAY_MIN, d.work, d.away, d.owner, d.claims
    runs, cur = [], None
    for x in range(N):
        if work[x] and owner[x] is None and not away[x]:  # never time away (or the clock's missing spring hour)
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
        if n >= d.o["out_min"]:
            d.claim({"kind": "outside", "task": None, "source": "neighbour", "conf": "low", "titles": [],
                     "why": "work outside Claude: the task of the work next to it"},
                    [x for x in range(a, b + 1) if work[x] and not away[x]])
        else:
            short.append((a, b))
    for a, b in short:  # a short one joins the entry beside it
        left = next((owner[x] for x in range(a - 1, max(0, a - 3), -1) if owner[x] not in (None, BLOCKED)), None)
        right = next((owner[x] for x in range(b + 1, min(N, b + 3)) if owner[x] not in (None, BLOCKED)), None)
        pick_i = next((i for i in (left, right) if i is not None and claims[i]["kind"] not in ("logged", "skip", "shared")),
                      None)  # never a shared stretch: that is the agent's time, not the work in front
        if pick_i is not None:
            for x in range(a, b + 1):
                if work[x] and owner[x] is None:
                    owner[x] = pick_i


def _pieces(d: _Day) -> list:
    """Runs of minutes per claim, in time order, with their claim's fields; an outside stretch takes the task of the
    work before it, else after."""
    owner, claims = d.owner, d.claims
    pieces, cur = [], None
    for x in range(DAY_MIN):
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
                p.update(task=q["task"], project=q.get("project"), alts=list(q.get("alts") or []),
                         claudeFound=bool(q.get("claudeFound")))
    return pieces


def _blocked_between(d: _Day, a, b) -> bool:
    """An entry already logged, or a shared stretch, between minutes a and b: laid out from a, a piece joined across it
    would stop at it, the rest not placed; a shared stretch stays where it was, so the same."""
    return any(a < x[1] and x[0] < b for x in d.busy + d.shared_spans)


def _away_between(d: _Day, p, q, a, b) -> bool:
    """A client's entry is never laid over time away: a break splits it."""
    billable_task = lambda e: e.get("class") == CLIENT or attribution.class_of_task(e.get("task"), d.ctx) == CLIENT  # noqa: E731
    return (billable_task(p) or billable_task(q)) and any(d.away[x] for x in range(max(0, a), min(DAY_MIN, b)))


def _merge(d: _Day, pieces: list) -> list:
    """Entries from the pieces: one task in one sitting is one entry, laid out from its first piece; a meeting someone
    attended keeps its own time, so what comes before it joins it only when it touches it. Pieces too short to log
    join a neighbour. Skipped work goes to d.skipped; the rest is returned."""
    o, sitting = d.o, d.sitting
    merged: list = []
    for p in pieces:
        m = merged[-1] if merged else None
        # one shared stretch is one entry, whatever minutes in it went to something else (its run allows two at a
        # time); it never joins anything else
        same = next((q for q in reversed(merged[-6:]) if p["kind"] == q["kind"] == "shared" and q["claim"] == p["claim"]),
                    None) if p["kind"] == "shared" else None
        if same is not None:
            same["minutes"] += p["minutes"]
            same["last"] = p["last"]
            same["parts"].append(p)
            continue
        if m and m.get("task") and m["task"] == p.get("task") and not {"skip", "shared"} & {m["kind"], p["kind"]} \
                and p["s"] - m["last"] <= o["merge_gap"] and sitting[m["last"]] == sitting[p["s"]] \
                and (not p.get("attended") or p["s"] - m["last"] <= (o["grid"] or 1)) \
                and not _blocked_between(d, m["last"] + 1, p["s"]) and not _away_between(d, m, p, m["last"] + 1, p["s"]):
            m["minutes"] += p["minutes"]
            m["last"] = p["last"]
            m["parts"].append(p)
        else:
            merged.append({**p, "parts": [p]})

    # pieces too short to log join a neighbour within MERGE_GAP in the same sitting (one far away pulled a 200-minute
    # afternoon entry to 00:40; laid out from the earlier start, a neighbour across a break would move into it), never
    # across an entry already logged; a meeting someone attended stays an entry of its own from 5 minutes, and keeps
    # its own time: work before it never joins it
    def joins(p, q, q_first):
        if q is None or q["kind"] == "skip" or (q.get("attended") and not q_first and not p.get("attended")):
            return False
        if "shared" in (p["kind"], q["kind"]):  # a shared stretch stays as it was, at its real time
            return False
        a, b = (q["last"], p["s"]) if q_first else (p["last"], q["s"])
        if _blocked_between(d, a + 1, b) or _away_between(d, p, q, a + 1, b):
            return False
        sa, sb = sitting[a], sitting[b]  # a minute with no activity, in a call, fits any sitting
        return b - a <= o["merge_gap"] and (sa is None or sb is None or sa == sb)

    if o["min_entry"]:
        changed = True
        while changed:
            changed = False
            for k, p in enumerate(merged):
                stays = p.get("attended") and p["minutes"] >= 5
                if p["minutes"] < o["min_entry"] and p["kind"] != "skip" and not stays:
                    nb = [q for q, first in ((merged[k - 1] if k else None, True),
                                             (merged[k + 1] if k + 1 < len(merged) else None, False))
                          if joins(p, q, first)]
                    if nb:
                        q = max(nb, key=lambda q: q["minutes"])
                        q["minutes"] += p["minutes"]
                        q["s"], q["last"] = min(q["s"], p["s"]), max(q["last"], p["last"])
                        q["parts"] += p["parts"]
                        merged.pop(k)
                        changed = True
                        break
    d.skipped += [{"s": m["s"], "e": m["last"] + 1, "minutes": m["minutes"], "project": m.get("project") or "",
                   "why": m.get("why") or ""} for m in merged if m["kind"] == "skip"]
    return [m for m in merged if m["kind"] != "skip"]


def _evidence(d: _Day, pieces: list) -> dict:
    """The minutes the person was seen at work on each task: in front, or its agent attended (shared). A billable
    entry never comes out longer than that (the evidence cap, _caps), whatever the layout and the grid did."""
    evidence: dict = defaultdict(int)
    for p in pieces:
        if p.get("task") and p["kind"] in ("focused", "shared", "outside"):
            evidence[str(p["task"])] += sum(1 for x in range(p["s"], p["last"] + 1)
                                            if d.owner[x] == p["claim"] and d.work[x] and not d.away[x])
    return evidence


def _layout(d: _Day, entries: list) -> tuple:
    """(entries laid out, unplaced). A shared stretch first, where it was (its client's agent and the work in front
    both ran then); then the rest in time order, no overlap with each other, with what is logged or a shared stretch,
    boundaries on the grid, except against an entry already logged: an entry starts right where it ends, or stops
    right where it starts, so the minutes up to the grid are taken in rather than left as a crumb of "not covered"
    shorter than a step."""
    N, g = DAY_MIN, d.o["grid"] or 1
    out, end, unplaced = [], 0, []
    fixed = [(a, b, "logged") for a, b in d.busy]
    for m in sorted((m for m in entries if m["kind"] == "shared"), key=lambda m: m["s"]):
        s = int(round(m["s"] / g) * g)
        e = min(N, int(round((s + m["minutes"]) / g) * g))  # its own minutes, from its real start
        for a, b, _ in sorted(fixed):
            if a < e and s < b:  # never over an entry already logged
                s, e = (s, int(a)) if s < a else (int(math.ceil(b)), e)
        if e - s < g:
            continue  # under a grid step: rounding
        out.append(_entry(m, s, e, d.names))
        fixed.append((s, e, "shared"))
    fixed.sort()
    for m in sorted((m for m in entries if m["kind"] != "shared"), key=lambda m: m["s"]):
        s = max(int(round(m["s"] / g) * g), end)
        e = int(round((s + m["minutes"]) / g) * g)
        cut, why = 0, "no room before an entry already logged"  # minutes an entry already logged left no room for
        for a, b, kind in fixed:  # an entry already logged: start right after it, or stop right before it
            if a <= s < b:
                length = e - s
                s = int(math.ceil(b))
                e = int(math.ceil((s + length) / g) * g)  # the end back on the grid, the length kept at least
            elif s < a < e:
                stop = max(s, int(a))
                cut += e - stop
                e = stop
                if kind == "shared":
                    why = "no room beside a shared entry"
        if s > m["last"]:  # moved past entries already logged beyond its own work: listed, never placed elsewhere
            unplaced.append({"minutes": m["minutes"], "task": m.get("task"), "project": m.get("project") or "",
                             "why": why})
            continue
        e = min(e, N)
        room = {"task": m.get("task"), "project": m.get("project") or "", "why": why}
        if e - s < (5 if m.get("attended") else g):
            if cut:  # squeezed between entries already logged: all of it listed, none placed
                unplaced.append({"minutes": m["minutes"], **room})
            continue  # else under half a grid step: rounding
        if cut:
            unplaced.append({"minutes": cut, **room})
        end = e
        out.append(_entry(m, s, e, d.names))
    out.sort(key=lambda e: e["s"])
    return out, unplaced


def _caps(d: _Day, out: list, unplaced: list, evidence: dict) -> tuple:
    """(entries, totals so far). The cap: proposed plus logged never more than active time plus attended meetings,
    the rest shown, not placed; then the evidence cap: a task that may be billable never gets more minutes than the
    person was seen at work on it. Entries whose task has time logged with no start are flagged."""
    N, g, o, ctx = DAY_MIN, d.o["grid"] or 1, d.o, d.ctx
    att = bytearray(N)
    for a, b, _ in d.attended:
        for x in range(max(0, int(a)), min(N, int(math.ceil(b)))):
            att[x] = 1
    active = sum(d.work)
    cap = sum(1 for x in range(N) if d.work[x] or att[x])
    logged_total = sum(int(r["minutes"]) for r in d.logged)
    allowed = max(0, cap - logged_total)
    over = sum(e["minutes"] for e in out) - allowed

    def cut_from(e, want: int, why: str) -> int:
        cut = min(e["minutes"], int(math.ceil(want / g) * g))
        if cut < e["minutes"] and (e["e"] - cut) % g:  # an end against an entry already logged: back on the grid
            cut = min(e["minutes"], e["e"] - int((e["e"] - cut) // g * g))
        if e["minutes"] - cut < o["min_entry"] and not (e["kind"] == "meeting" and e["minutes"] - cut >= 5):
            cut = e["minutes"]  # too short to stand alone once cut: all of it listed, none placed
        e["minutes"] -= cut
        e["e"] = e["s"] + e["minutes"]
        unplaced.append({"minutes": cut, "task": e["taskId"], "project": e["project"], "why": why})
        return cut

    # what goes first: internal work, then mixed or unknown, a client's last; within each the least sure, the latest
    first_cut = lambda e: (CLASS_ORDER.get(e.get("class"), 1), RANK[e["confidence"]], -e["s"])  # noqa: E731
    if over > 0:
        left = dict(d.untimed)  # first the task of an entry with no start, up to its minutes: it may be that work
        for e in sorted(out, key=first_cut):
            if over > 0 and left.get(str(e["taskId"]), 0) > 0:
                cut = cut_from(e, min(over, left[str(e["taskId"])]),
                               "more than your active time and meetings; its task has time logged with no start")
                left[str(e["taskId"])] -= cut
                over -= cut
        for e in sorted(out, key=first_cut):
            if over <= 0:
                break
            if e["minutes"]:
                over -= cut_from(e, over, "more than your active time and meetings")
        out = [e for e in out if e["minutes"] >= g]
    # the evidence cap: a task that may be billable (any not known to be internal) never gets more minutes than the
    # person was seen at work on it
    billable = lambda e: e.get("taskId") and e["kind"] not in ("meeting", "unattended") and (  # noqa: E731
        e.get("class") == CLIENT or attribution.class_of_task(e["taskId"], ctx) != INTERNAL)
    total: dict = defaultdict(int)
    for e in out:
        if billable(e):
            total[str(e["taskId"])] += e["minutes"]
    for e in sorted(out, key=lambda e: -e["s"]):
        t = str(e.get("taskId") or "")
        if billable(e) and e["minutes"] and total[t] > evidence.get(t, 0):
            total[t] -= cut_from(e, total[t] - evidence.get(t, 0), "more than the time this work was seen")
    out = [e for e in out if e["minutes"] >= g]
    for e in out:
        if d.untimed.get(str(e["taskId"])):
            e["flags"].append("untimed")
            e["checked"] = False
            e["untimed"] = d.untimed[str(e["taskId"])]
    return out, {"logged": logged_total, "active": active, "meetings": sum(att), "cap": cap}


def _result(d: _Day, out: list, unplaced: list, totals: dict) -> dict:
    """plan()'s answer: the entries, with the active time no entry covers (logged or proposed), the agents' other work
    noted by repository, and the totals."""
    N, g = DAY_MIN, d.o["grid"] or 1
    covered = bytearray(N)
    for a, b in d.busy + [(e["s"], e["e"]) for e in out]:
        for x in range(max(0, int(a)), min(N, int(math.ceil(b)))):
            covered[x] = 1
    uncovered, start = [], None  # only stretches of a step or more: a shorter one is rounding, not work to log
    for x in range(N + 1):
        gap = x < N and d.work[x] and not covered[x]
        if gap and start is None:
            start = x
        if not gap and start is not None:
            if x - start >= max(3, g):
                uncovered.append({"s": start, "e": x, "minutes": x - start})
            start = None
    # the agents' other work while the person was at work: noted by repository, never placed
    noted = [{"s": min(xs), "e": max(xs) + 1, "minutes": len(xs), "project": p, "why": "ran in parallel"}
             for p, xs in sorted(d.bg_left.items(), key=lambda kv: -len(kv[1])) if len(xs) >= 5]
    return {"entries": out, "unplaced": unplaced, "skipped": d.skipped + noted, "uncovered": uncovered,
            "totals": {"proposed": sum(e["minutes"] for e in out),
                       "checked": sum(e["minutes"] for e in out if e["checked"]),
                       **totals,
                       "notCovered": sum(u["minutes"] for u in uncovered),
                       "over": sum(u["minutes"] for u in unplaced if u["why"].startswith("more than your")),
                       "parallel": sum(d.parallel), "shared": sum(e["minutes"] for e in out if e.get("shared")),
                       "leftOut": sum(n["minutes"] for n in noted)}}


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
    if kind == "shared":  # a client's agent went first over internal work: ticked, with a note to check it
        flags.append("shared")
    for q in m["parts"]:  # a meeting whose title was refused as a note, or a series renamed since it was last logged
        if q.get("flag") and q["flag"] not in flags:
            flags.append(q["flag"])
    # a note only from a part of this task (a meeting's title, a repeat's usual note, Claude's draft for this task,
    # its commits);
    # an entry with no task keeps the note only when that part is most of it
    noted = [q for q in sorted(m["parts"], key=lambda q: -q["minutes"]) if q.get("note")
             and (q.get("task") == task if task else q is lead)]
    note, note_from = (noted[0]["note"], noted[0].get("noteFrom") or "") if noted else ("", "")
    # Claude's pick of a task not logged to in the last 30 days (found by searching Productive), and a note Claude
    # drafted: never ticked at the start, so a prompt injection in the work cannot log through one click
    if task and any(q.get("claudeFound") and q.get("task") == task for q in m["parts"]):
        flags.append("claudefound")
    if note and note_from == "claude":
        flags.append("claudenote")
    title, project = names.get(str(task), ("", "")) if task else ("", "")
    alts = []
    for q in sorted(m["parts"], key=lambda q: -q["minutes"]):
        for t in q.get("alts") or []:
            if t and t != task and t not in [a["taskId"] for a in alts]:
                n = names.get(str(t), ("", ""))
                alts.append({"taskId": str(t), "title": n[0], "project": n[1],
                             "why": (q.get("altWhy") or {}).get(t) or ""})
    by_class: dict = defaultdict(int)
    for q in m["parts"]:
        by_class[q.get("class") or ""] += q["minutes"]
    out = {"s": s, "e": e, "minutes": minutes, "taskId": task, "title": title, "project": project,
           "repo": lead.get("project") or "", "meeting": lead.get("meeting") or "",
           "serviceId": lead.get("service"), "service": "", "note": note or "", "noteFrom": note_from or "",
           "confidence": conf, "confidenceWords": CONF_WORDS[conf], "why": lead.get("why") or "",
           "source": lead.get("source") or "", "kind": kind, "checked": not [f for f in flags if f != "shared"],
           "flags": flags, "alts": alts[:3], "parts": len(m["parts"]),
           "class": max(by_class, key=by_class.get) or None,
           "sessions": sorted({x for q in m["parts"] for x in q.get("sessions") or []}),
           # Ask Claude looked at the work that carries this entry and found no task: the guess stays, flagged
           "claudeNone": bool(lead.get("claudeNone"))}
    if kind == "shared":
        w = lead.get("with") or {}
        wt = w.get("task")
        n = names.get(str(wt), ("", "")) if wt else ("", "")
        out["shared"] = True
        out["choice"] = "client"
        # the work that was in front: what Half each and Internal give the stretch (logday.js, mcptools prefer)
        out["sharedWith"] = {"taskId": wt, "title": n[0], "project": n[1], "repo": w.get("project") or "",
                             "why": w.get("why") or "", "source": w.get("source") or "",
                             "confidence": w.get("conf") or "low", "sessions": list(w.get("sessions") or [])}
    return out


CHOICES = ("client", "half", "internal")


def apply_choice(e: dict, choice: str, grid: int = GRID) -> list:
    """A shared entry as the person (or Claude Code's prefer) chose it, at its real time: client (as proposed), half
    (the first half the client's, the second the work in front's), internal (all of it the work in front's). Any
    other entry comes back as it is. Pure: the entry is not changed."""
    if not e.get("shared") or choice not in CHOICES or choice == "client":
        return [{**e, **({"choice": "client"} if e.get("shared") else {})}]
    w = e.get("sharedWith") or {}
    inner = {"taskId": w.get("taskId"), "title": w.get("title") or "", "project": w.get("project") or "",
             "serviceId": w.get("serviceId"), "service": w.get("service") or "", "budget": w.get("budget") or "",
             "billable": w.get("billable"), "services": w.get("services") or [], "repo": w.get("repo") or "",
             "why": w.get("why") or "", "source": w.get("source") or "", "confidence": w.get("confidence") or "low",
             "alts": [], "note": "", "noteFrom": "", "class": INTERNAL}
    inner["checked"] = bool(inner["taskId"] and inner["serviceId"])
    if choice == "internal":
        return [{**e, **inner, "choice": "internal"}]
    g = max(1, int(grid or 1))
    half = int(round(e["minutes"] / 2 / g) * g)
    if not g <= half <= e["minutes"] - g:
        return [{**e, "choice": "client"}]  # too short to halve on the grid
    first = {**e, "e": e["s"] + half, "minutes": half, "choice": "half"}
    second = {**e, **inner, "s": e["s"] + half, "minutes": e["minutes"] - half, "choice": "half", "halfOf": e.get("id")}
    return [first, second]


# ---- the service: the one last used on the task, if it still takes time on that date ------------------------------

def pick_service(services: list, last_id=None, last_name: str = "", task_service=None, project_last=None):
    """The service you used last on the task; else one of the same name (a newer budget); else the task's own; else,
    for a task you never logged to, the one you used last in its project (`project_last`: (id, name)); else the only
    one. None when it has to be picked (the log dialog does the same)."""
    by = {str(s["id"]): s for s in services}
    if last_id and str(last_id) in by:
        return by[str(last_id)]
    if last_name:
        same = [s for s in services if (s.get("name") or "").strip() == last_name.strip()]
        if same:
            return same[0]
    if task_service and str(task_service) in by:
        return by[str(task_service)]
    if project_last and not (last_id or last_name):
        pid, pname = project_last
        if pid and str(pid) in by:
            return by[str(pid)]
        same = [s for s in services if pname and (s.get("name") or "").strip() == pname.strip()]
        if same:
            return same[0]
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


def ticket_flags(entries: list, found: dict, date: str, settings: dict) -> None:
    """Each entry's ticket flags (flags.py) in "ticket": the task closed, no budget on the date, over its estimate with
    this row and the plan's earlier rows on it, no estimate where the project's tasks have them. Read only, and never a reason to untick: the person decides. found: task id ->
    (bookable_services answer or None, error)."""
    from concurrent.futures import ThreadPoolExecutor
    from . import flags
    projects = sorted({str(((b or {}).get("task") or {}).get("projectId") or "") for b, _ in found.values()
                       if b and not (b.get("task") or {}).get("estimate")} - {""})
    with ThreadPoolExecutor(4) as pool:  # read once per project here (kept a day), so each row below finds it
        list(pool.map(lambda pid: flags.project_share(pid, settings), projects))
    planned: dict = {}  # task id -> minutes of the plan's rows so far: the estimate counts them all, in time order
    for e in sorted(entries, key=lambda e: e.get("s") or 0):
        b = (found.get(e["taskId"]) or (None, None))[0] if e.get("taskId") else None
        if b:
            planned[e["taskId"]] = planned.get(e["taskId"], 0) + (e.get("minutes") or 0)
        e["ticket"] = flags.for_task(b.get("task"), b.get("services"), date, settings,
                                     minutes=planned[e["taskId"]]) if b else []


def gather(date: str, source: str = "auto", ignore_logged: bool = False, keep: dict | None = None,
           inputs: dict | None = None) -> dict:
    """The proposal for `date` from Pulsar's live data. ignore_logged (test servers only) plans as if nothing were
    logged that day, to show a whole proposal on a day already logged. keep: as for plan(). inputs: a dict to fill
    with what the day was planned from, for Log the day with Claude's digest (dayreview.digest)."""
    from concurrent.futures import ThreadPoolExecutor
    from . import askclaude, calendar, learn as learning, likely, logbook, server
    s = server.current_settings()
    tz = config.tz(s)
    t0, t1 = server.day_bounds(date, tz)
    if not s.get("person_id"):
        return {"error": "Connect Productive in Settings first.", "entries": []}
    try:
        day_runs = runs_for(date)  # first: with a journal that cannot be read, nothing is proposed
    except JournalBroken as exc:
        return {"error": str(exc), "entries": []}
    first28 = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=server.REPEAT_DAYS)).strftime("%Y-%m-%d")

    def proposal():
        if not likely.enabled(s):
            return {"entries": []}
        got = likely.proposal(date, config.timezone_name(s), server.today(tz), False, s)
        if got.get("error"):
            return {"entries": [], "error": got["error"]}
        shift = datetime.fromtimestamp(t0, tz).utcoffset() != datetime.fromtimestamp(t1 - 1, tz).utcoffset()

        def copied(list_):
            out = []
            for e in list_ or []:  # a copy: the cached proposal stays as it is
                r = {**e, "pick_s": e["s"]}  # Ask Claude names a row by its minute before any clock change
                if shift:  # the clocks change today: each row's start by the clock, its real length (as the page draws it)
                    r["s"], r["e"] = server.page_span(e, t0, tz)
                out.append(r)
            return out
        return {**got, "entries": copied(got.get("entries")), "background": copied(got.get("background"))}

    def suggest():
        try:
            return server.api_suggest({"date": date}).get("assigned") or []
        except Exception as exc:  # a hint only: the plan goes on without your assigned tasks, and Logs says why
            logbook.record("log the day", f"Your assigned tasks could not be read: {type(exc).__name__}: {exc}",
                           kind="warning", once_per=600)
            return []

    with ThreadPoolExecutor(8) as pool:
        jobs = {"act": pool.submit(server.api_activity, {"date": date, "source": source}),
                "logged": pool.submit(server.productive_rows, date, date, s),
                "history": pool.submit(server.history_rows, date, s, learning.LEARN_DAYS),
                "likely": pool.submit(proposal),
                "repeats": pool.submit(server.api_repeats, {"date": date, "source": source}),
                "cal": pool.submit(calendar.day, date, s),
                "past": pool.submit(calendar.history, first28, server.REPEAT_DAYS, s),
                "assigned": pool.submit(suggest),
                "fresh": pool.submit(server.new_task_source, date, s)}
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
            end = None if x.get("start") is None else round(server.clock(x["start"] + x["minutes"] * 60, t0, tz), 2)
            logged.append({"s": start, "e": end, "minutes": x["minutes"], "taskId": x.get("taskId"), "id": x.get("id")})
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
    bg_rows = got["likely"].get("background") or []
    if (likely_rows or bg_rows) and not hproblem:  # learned tasks per row, from the days before only
        learning.annotate({"entries": likely_rows, "background": bg_rows}, date, s, history, [], tz, got["fresh"])
    t0_of = lambda d: server.day_bounds(d, tz)[0]  # noqa: E731
    days = {d: learning._stored(d) for d in learning._days_before(date)}
    ctx = learn(date, [] if hproblem else history, {d: r for d, r in days.items() if r}, t0_of,
                person=str(s.get("person_name") or ""))
    # Ask Claude's kept answer (askclaude.keep: on disk, 60 days) steers the plan only while the Claude Code switch is on
    answer = askclaude.latest(date) if s.get("ask_claude") else None
    day = {"items": act.get("items") or [], "away": act.get("away") or [], "rows": likely_rows, "background": bg_rows,
           "logged": logged,
           "meetings": meetings, "repeats": got["repeats"].get("repeats") or [], "calls": calls,
           "rules": s.get("rules") or [], "assigned": got["assigned"], "picks": (answer or {}).get("picks") or {},
           "gone": server.missing_hour(t0, t1, tz)}
    opts = opts_for(s)
    result = plan(day, ctx, opts) if keep is None else plan(day, ctx, opts, keep=keep)
    result["roundTo"] = opts["grid"]

    # the service of each task, checked against what takes time on that date (and of the work a shared row was over)
    tasks = sorted({e["taskId"] for e in result["entries"] if e["taskId"]}
                   | {str(e["sharedWith"]["taskId"]) for e in result["entries"] if (e.get("sharedWith") or {}).get("taskId")})

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
        svc = pick_service(b.get("services") or [], e.get("serviceId") or last_id, last_name, task.get("serviceId"),
                           ctx.get("project_service", {}).get(e["project"]))
        e["services"] = [{"id": x["id"], "name": (x.get("name") or "").strip(), "budget": x.get("budget") or "",
                          "billable": x.get("billable")} for x in b.get("services") or []]
        if svc:
            e["serviceId"], e["service"], e["budget"], e["billable"] = svc["id"], (svc.get("name") or "").strip(), \
                svc.get("budget") or "", svc.get("billable")
        else:
            e["serviceId"] = None
            e["checked"] = False
            e["flags"].append("noservice")
    for e in result["entries"]:  # what Half each and Internal give a shared row: the front work's task and service
        w = e.get("sharedWith")
        if not w or not w.get("taskId"):
            continue
        b, _ = found.get(str(w["taskId"]), (None, None))
        task = (b or {}).get("task") or {}
        w["title"], w["project"] = w.get("title") or task.get("title") or "", w.get("project") or task.get("project") or ""
        last_id, last_name = ctx["service"].get(str(w["taskId"]), (None, ""))
        svc = pick_service((b or {}).get("services") or [], last_id, last_name, task.get("serviceId"),
                           ctx.get("project_service", {}).get(w["project"])) if b else None
        w["services"] = [{"id": x["id"], "name": (x.get("name") or "").strip(), "budget": x.get("budget") or "",
                          "billable": x.get("billable")} for x in (b or {}).get("services") or []]
        w.update({"serviceId": svc["id"], "service": (svc.get("name") or "").strip(), "budget": svc.get("budget") or "",
                  "billable": svc.get("billable")} if svc else {"serviceId": None, "service": ""})
    ticket_flags(result["entries"], found, date, s)
    # FI's rules in code (dayreview.fi_rules): suggestions only, accepted or rejected in the review
    from . import dayreview
    for i, e in enumerate(result["entries"]):
        e["id"] = f"r{i}"
    known = {tid: {**((b or {}).get("task") or {}), "taskId": tid} for tid, (b, _) in found.items() if b}
    result["suggestions"] = dayreview.fi_rules(result["entries"], date, got["fresh"] or [], known)
    for e in result["entries"]:
        for a in e["alts"]:
            if not a["title"] and a["taskId"] in found and found[a["taskId"]][0]:
                t = found[a["taskId"]][0].get("task") or {}
                a["title"], a["project"] = t.get("title") or "", t.get("project") or ""
    # rows a run of this day already sent: shown as such, and the writer refuses them again (one it logged only while
    # its entry is still there: deleted by hand, or moved to another day, it may be logged again)
    present = None if problem else {str(x.get("id")) for x in rows}
    sent = {sig(date, e2["start"], e2["minutes"], e2["taskId"], e2["serviceId"]): e2
            for run in day_runs for e2 in run["rows"] if still_sent(e2, present)}
    for e in result["entries"]:
        x = sent.get(sig(date, _hm(e["s"]), e["minutes"], e["taskId"], e["serviceId"]))
        if x:
            e["sent"] = x["status"]
            e["checked"] = False
    _shared_kept[date] = (time.time(), [(e["s"], e["e"], str(e.get("taskId") or "")) for e in result["entries"]
                                        if e.get("shared")])
    if inputs is not None:
        inputs.update(plan=result, items=day["items"], away=day["away"], rows=likely_rows, background=bg_rows,
                      meetings=meetings,
                      repeats=day["repeats"], pool=got["fresh"] or [], t0=t0, tz=tz,
                      target=config.target_minutes(s), recent=dayreview.recent_from_history(history, date),
                      logged=[] if ignore_logged else [
                          {"s": None if x.get("start") is None else round(server.clock(x["start"], t0, tz), 2),
                           "minutes": x["minutes"], "taskId": x.get("taskId"), "task": x.get("task") or "",
                           "service": x.get("service") or "", "note": x.get("note") or ""} for x in rows],
                      is_call=server.is_call, logging_rx=LOGGING)
    if got["likely"].get("error"):
        logbook.record("log the day", f"Likely: {got['likely']['error']}", kind="warning", once_per=600)
    # without the days before, the plan has no learned tasks and no last-used services: said, not hidden
    history_note = (f"Your earlier entries could not be read, so the tasks and services here are not learned from "
                    f"them: {hproblem}") if hproblem else None
    return {**result, "date": date, "ignoreLogged": ignore_logged, "fromCopy": bool(from_copy),
            "warning": " ".join(w for w in (problem if from_copy else None, history_note) if w) or None,
            "historyProblem": history_note, "likelyError": got["likely"].get("error"),
            "calendar": bool(got["cal"].get("connected")), "claude": bool(answer),
            "learning": got["likely"].get("learning")}


_shared_kept = config.Newest(32)  # date -> (when, [(s, e, task)]): the shared rows of the plan worked out last
SHARED_FOR = 900  # seconds pulsar_preview takes them as the day's


def shared_spans(date: str):
    """[(s, e, task)] of the shared rows in the last plan worked out for `date`, if within SHARED_FOR; else None."""
    kept = _shared_kept.get(date)
    return kept[1] if kept and time.time() - kept[0] < SHARED_FOR else None


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


class JournalBroken(ValueError):
    """The journal is there but cannot be read."""


def journal() -> dict:
    """The runs. No file is no runs; a file that cannot be read is never taken for none: a row whose answer never came
    would be sent again, and the next write would drop every run's Undo. Then nothing is written from Log the day or
    Claude Code until someone looks at it (the file stays as it is)."""
    path = journal_path()
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and isinstance(doc.get("runs"), list):
            return doc
        why = "it is not a journal"
    except FileNotFoundError:
        return {"runs": []}
    except (OSError, ValueError) as exc:
        why = type(exc).__name__
    from . import logbook
    msg = (f"Log the day's journal cannot be read ({why}), so Pulsar cannot tell what was already sent and writes "
           f"nothing from Log the day or Claude Code. Check the Logged column, then move {path} away to start a new one.")
    logbook.record("log the day", msg, kind="error", once_per=600)
    raise JournalBroken(msg)


def sig(date, start, minutes, task, service) -> str:
    return f"{date}|{start}|{int(minutes or 0)}|{task or ''}|{service or ''}"


def runs_for(date: str) -> list:
    return [r for r in journal()["runs"] if r.get("date") == date]


def still_sent(x: dict, present: set | None = None) -> bool:
    """Whether a run's row stands as sent: "sending" and "unknown" always (no answer came); "logged" (and "changed",
    which Undo left alone) only while its entry is among the day's entries just read (`present`, their ids; None when
    not read), so one deleted by hand or moved to another day can be logged again. "gone", "refused", "undone": no."""
    status = x.get("status")
    if status in ("sending", "unknown"):
        return True
    if status in ("logged", "changed"):
        return present is None or not x.get("id") or str(x["id"]) in present
    return False


def check(run: str, row: str, date: str, start: str, minutes: int, task: str, service: str,
          present: set | None = None) -> None:
    """Refuses a row this run already sent, or the same entry any run of that day sent and that still stands as sent
    (still_sent): a restart, a second tab or a retry never writes it twice."""
    if not RUN.match(run or "") or not ROW.match(row or "") or run[3:13] != date:
        raise ValueError("A Log the day run needs its run and row names.")
    mine = sig(date, start, minutes, task, service)
    for r in journal()["runs"]:
        for x in r.get("rows") or []:
            if ((r.get("run") == run and x.get("row") == row) or (r.get("date") == date and x.get("sig") == mine)) \
                    and still_sent(x, present):
                raise ValueError(_sent_reason(x))


def _sent_reason(x: dict) -> str:
    if x.get("status") in ("logged", "changed"):
        return f"This entry was logged at {x.get('at', '')[11:16]} (entry {x.get('id')}). It is not sent twice."
    return (f"This entry was sent at {x.get('at', '')[11:16]} and Pulsar never heard back. Check the Logged column; "
            "Pulsar will not send it again, so log it by hand if it is missing.")


def mark(run: str, date: str, row: str, entry: dict, status: str, **fields) -> None:
    """Records one row's state, written to disk at once (owner-only, atomic): "sending" goes in before the write."""
    with _journal_lock:
        doc = journal()
        # runs started in the last KEEP_DAYS days: counted from today, never from the entry's date (one far ahead
        # would drop today's runs, their Undo and their guard)
        cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
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


def mark_undone(run: str, entry_id: str, status: str = "undone") -> None:
    """A logged row Undo dealt with: "undone" (deleted), "gone" (not in Productive any more) or "changed" (changed
    by hand since): Undo stops offering it either way."""
    with _journal_lock:
        doc = journal()
        for r in doc["runs"]:
            if r.get("run") == run:
                for x in r.get("rows") or []:
                    if str(x.get("id")) == str(entry_id) and x.get("status") == "logged":
                        x["status"], x[status + "At"] = status, datetime.now().isoformat(timespec="seconds")
        config.write_atomic(journal_path(), json.dumps(doc, ensure_ascii=False))
