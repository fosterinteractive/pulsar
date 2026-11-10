"""Parallel work: what the history says about a repository and a session, when one person runs several agents at once.

Pure functions on plain data: learn.py and dayplan.py call them, tests/test_attribution.py runs them on made-up days.

* Learning (credits): a logged minute teaches the unit whose repository is logged to that task's project, not
  always the one in front. While internal work was on screen and a client's agent ran in the background, the person
  logged the client: that teaches the client's repository and session, never the internal one (2026-11: a repository
  of Pulsar's own learned a client's task this way and proposed it for hours of Pulsar work).
* A session's history applies per repository (session_votes): the votes a session earned in one repository count
  there. Its other votes count only when the session has been in one repository all along, or for a task whose project
  this repository is already logged to; never in a repository with no history.
* Billing class (repo_class, segment_class): client, internal, mixed or unknown, from a rule, a task link or a ticket
  key in the work itself, else 60 days of the repository's logged minutes, else the project its name points to.
  Internal is a non-billable service or a project of the agency's own (INTERNAL_PREFIXES); fixed-price ongoing
  support is billable, so client. The task picked for the work never sets its class.
"""

from __future__ import annotations

import re
from collections import defaultdict

CLIENT, INTERNAL, MIXED, UNKNOWN = "client", "internal", "mixed", "unknown"
CLASS_SHARE = 0.75   # of a repository's logged minutes in one class, for that class
CLASS_MIN = 60       # minutes of billable history before a repository counts as a client's
SR_MIN = 5           # minutes of a session's votes in a repository before they say anything there
FIT_SHARE = 0.25     # a project with less of a repository's logged time than this is another client's there
FIT_MIN = 60         # minutes of a repository's (fading) votes before its projects say anything
ONE_PROJECT = 0.8    # of a session's votes, the share one project needs before they carry into a repository with too
                     # little history: a session that went from client to client says nothing about the next one
# The agency's own projects: never a client's, whatever their services say
INTERNAL_PREFIXES = ("FI-",)
# One repository under several names: Pulsar's first name, and its preview checkout
REPO_ALIASES = {"chronodeck": "pulsar", "pulsar-preview": "pulsar"}
_WORD = re.compile(r"[a-z0-9]+")
_NAME_STOP = frozenset({"site", "sites", "main", "web", "website", "dev", "app", "apps", "repo", "drupal", "theme",
                        "test", "tests", "local", "kb", "the", "and", "new", "old", "copy", "preview", "ongoing",
                        "support", "project", "projects"})


def repo_key(name) -> str:
    """The name a repository's history is kept under (REPO_ALIASES)."""
    name = str(name or "")
    return REPO_ALIASES.get(name.lower(), name)


def internal_project(project) -> bool:
    return str(project or "").startswith(INTERNAL_PREFIXES)


def entry_class(project, billable) -> str | None:
    """One logged entry's class: internal for the agency's own project or a non-billable service, client for a
    billable one, None when the service's billing is not known."""
    if internal_project(project):
        return INTERNAL
    if billable is False:
        return INTERNAL
    if billable is True:
        return CLIENT
    return None


def task_classes(history: list) -> tuple:
    """({task: class}, {project: class}) from the entries: a task as its last entry, a project as most of its
    minutes (mixed when neither class has CLASS_SHARE of them)."""
    tasks: dict = {}
    by_project: dict = defaultdict(lambda: defaultdict(float))
    for r in sorted((r for r in history if r.get("taskId")), key=lambda r: (str(r.get("date") or ""), r.get("start") or 0)):
        c = entry_class(r.get("project"), r.get("billable"))
        if c:
            tasks[str(r["taskId"])] = c
            if r.get("project"):
                by_project[r["project"]][c] += float(r.get("minutes") or 0)
    projects = {}
    for p, v in by_project.items():
        total = sum(v.values()) or 1
        top = max(v, key=v.get)
        projects[p] = top if v[top] / total >= CLASS_SHARE else MIXED
    for p in list(projects):
        if internal_project(p):
            projects[p] = INTERNAL
    return tasks, projects


def repo_class(minutes: dict) -> str | None:
    """A repository's class from its logged minutes by class ({client: m, internal: m}): client from CLASS_SHARE and
    CLASS_MIN minutes, internal from CLASS_SHARE, else mixed; None with no history."""
    c, i = float(minutes.get(CLIENT) or 0), float(minutes.get(INTERNAL) or 0)
    total = c + i
    if total <= 0:
        return None
    if c / total >= CLASS_SHARE:
        return CLIENT if c >= CLASS_MIN else MIXED
    if i / total >= CLASS_SHARE:
        return INTERNAL
    return MIXED


def name_words(text) -> set:
    """The words of a repository's or a project's name that can tell a client: four letters or more, not generic."""
    return {w for w in _WORD.findall(str(text or "").lower()) if len(w) >= 4 and w not in _NAME_STOP}


def name_class(repo, projects: dict) -> str | None:
    """The class of the project a repository's name points to (a word of four letters or more in both): None when no
    project, or projects of different classes, share a word with it."""
    words = name_words(repo)
    if not words:
        return None
    found = {c for p, c in projects.items() if words & name_words(p)}
    return found.pop() if len(found) == 1 and found <= {CLIENT, INTERNAL} else None


def class_of_task(task, ctx: dict) -> str | None:
    """A task's class: its own entries, else its project's, else the agency's own project by name."""
    if not task:
        return None
    task = str(task)
    got = (ctx.get("task_class") or {}).get(task)
    if got:
        return got
    project = ((ctx.get("names") or {}).get(task) or {}).get("project")
    if internal_project(project):
        return INTERNAL
    return (ctx.get("project_class") or {}).get(project) if project else None


def segment_class(repo, ctx: dict, hint_task=None) -> str:
    """The class of a stretch of work: from a rule, a task link or a ticket key in it (hint_task), else the repository's
    60 days of history, else the project its name points to; unknown otherwise. Never from the task picked for it."""
    c = class_of_task(hint_task, ctx) if hint_task else None
    if c in (CLIENT, INTERNAL):
        return c
    key = repo_key(repo)
    c = (ctx.get("classes") or {}).get(key)
    if c:
        return c
    return name_class(key, ctx.get("project_class") or {}) or UNKNOWN


def repo_classes(credits: list, task_class: dict) -> dict:
    """{repository: class} from the minutes credited to each (credits)."""
    by: dict = defaultdict(lambda: defaultdict(float))
    for _, _, repo, task, minutes in credits:
        c = task_class.get(str(task))
        if c:
            by[repo][c] += minutes
    return {r: c for r, c in ((r, repo_class(v)) for r, v in by.items()) if c}


# ---- learning ------------------------------------------------------------------------------------------------------

def credits(days: dict, task_of, project_of) -> list:
    """[(date, title keys, repository, task, minutes)]: what each stored day teaches, in date order.

    days: {date: rows}, rows as learn.py keeps them ({k: title keys, p: repository, s, e}, the background with bg);
    task_of(date, s, e): the task logged over most of that stretch (minutes of the day), or None; project_of(task).
    A front row's task goes to the background row under it whose repository was logged to that task's project more,
    on the days before, than the row's own (for the minutes they share); else to the row itself, as before."""
    acc: dict = defaultdict(lambda: defaultdict(float))  # repository -> project -> minutes credited on the days before
    out = []
    for d in sorted(days):
        rows = days[d] or []
        front = [r for r in rows if not r.get("bg")]
        back = [r for r in rows if r.get("bg")]
        today = []
        for row in front:
            m = row["e"] - row["s"]
            if m < 5:
                continue
            task = task_of(d, row["s"], row["e"])
            if not task:
                continue
            project = project_of(task) or ""
            own = repo_key(row.get("p"))
            best, best_w, best_o = None, acc[own].get(project, 0.0) if project else 0.0, 0
            for b in back:
                o = min(b["e"], row["e"]) - max(b["s"], row["s"])
                if o < 5 or not project:
                    continue
                w = acc[repo_key(b.get("p"))].get(project, 0.0)
                if w > best_w:
                    best, best_w, best_o = b, w, o
            if best is not None:
                today.append((d, list(best.get("k") or []), repo_key(best.get("p")), task, best_o))
            else:
                today.append((d, list(row.get("k") or []), own, task, m))
        for c in today:
            out.append(c)
            project = project_of(c[3])
            if project:
                acc[c[2]][project] += c[4]
    return out


def fits(votes: dict, project_of, task) -> bool | None:
    """Whether a task's project is one this repository is logged to (FIT_SHARE of its votes, {task: weight}): None when
    the repository has too little history (FIT_MIN) or the task's project is not known."""
    total = sum(votes.values()) if votes else 0
    project = project_of(task) if task else None
    if total < FIT_MIN or not project:
        return None
    mine = sum(w for t, w in votes.items() if project_of(t) == project)
    return mine / total >= FIT_SHARE


def projects_of(votes: dict, project_of) -> set:
    """The projects a repository is logged to (FIT_SHARE of its votes, {task: weight}); none with too little history
    (FIT_MIN)."""
    total = sum(votes.values()) if votes else 0
    if total < FIT_MIN:
        return set()
    by: dict = defaultdict(float)
    for t, w in votes.items():
        p = project_of(t)
        if p:
            by[p] += w
    return {p for p, w in by.items() if w / total >= FIT_SHARE}


def one_project(votes: dict, project_of) -> bool:
    """Whether a session's votes ({task: weight}) are mostly (ONE_PROJECT) one project's. 2026-11-09: one session
    upgraded the database of four clients' sites in turn; on the fifth site, which had no history yet, it offered the
    first client's task."""
    by: dict = defaultdict(float)
    for t, w in votes.items():
        by[project_of(t)] += w
    total = sum(by.values())
    return total > 0 and max(by.values()) / total >= ONE_PROJECT


def session_votes(keys: list, repo, sess: dict, sr: dict, repo_votes: dict, project_of, single: bool) -> tuple:
    """(votes {task: weight}, in this repository) for a session's title keys: its votes earned in this repository
    when they reach SR_MIN; else its other votes, only for a task this repository is logged to, or, when the session has
    been in this repository all along (single), for one whose fit is not known yet; never into a repository with no
    history otherwise, nor when its votes are of several projects (one_project)."""
    key = repo_key(repo)
    here: dict = defaultdict(float)
    for k in keys:
        for t, w in (sr.get((k, key)) or {}).items():
            here[t] += w
    if here and max(here.values()) >= SR_MIN:
        return dict(here), True
    wide: dict = defaultdict(float)
    for k in keys:
        for t, w in (sess.get(k) or {}).items():
            wide[t] += w
    out = {}
    single = single and one_project(wide, project_of)
    for t, w in wide.items():
        f = fits(repo_votes, project_of, t)
        if f is True or (single and f is None):
            out[t] = w
    return out, False
