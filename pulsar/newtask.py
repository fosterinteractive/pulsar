"""A new Productive task from the log dialog, for work no task fits: a draft of its title and description from the
work done in that time, and where in the project it can go. The task itself is written by productive.create_task,
only after a click in the page (server.api_task_create).

The draft (`local_draft`) is made on this Mac from the commits made then (their subjects, already checked by
commits.clean), or is a meeting's title when the time was a meeting. With the Claude Code switch on, `claude_draft`
asks Claude (the model chosen in Settings for "task", claudeuses.py; Sonnet by default) for a better one from the same
work plus what was typed and Claude's replies (askclaude.steps), with the same isolation as the session note
(askclaude.note): no tools, no MCP server, none of the person's settings, auto memory off, a JSON schema, and the
draft checked on this Mac before the page sees it. Measured in HOW-IT-WORKS 5.1.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading

from . import askclaude, claudeuses, config, logbook, productive

TITLE_MAX = 90     # characters in a drafted title (Productive takes 140)
TITLE_LIMIT = 140  # Productive's own limit on a task's title
DESC_MAX = 700     # characters in a drafted description
DESC_LIMIT = 5000  # what the page may send
# FI's maintenance projects get, per period, a "service request and contingency" task, and each issue is a subtask
# of it (fi-productive-process: "create a subtask per issue"). Their names vary: "ACME - Contingency Hours Nov 2026",
# "GLOBEX-OS - Service Request/Contingency - Nov/Dec 2026", "INITECH - Service Request & Contingency - Oct/Nov 2026".
PERIOD_TASK = re.compile(r"contingenc|service request|support request", re.I)
# a remark on the time itself, which a task never carries ("The review time is logged as billable work"): such a
# sentence is left out of a drafted description, and a title with one is refused
TIME_REMARK = re.compile(r"\b(?:logg(?:ed|ing) (?:the |my |his |her |their )?(?:time|hours)"
                         r"|(?:time|hours) (?:is |are |was |were )?(?:logged|tracked|recorded|billed)|billable"
                         r"|time entr\w*|time ?(?:records?|tracker|tracking)|timesheet)\b", re.I)
# a task that holds a period's or a kind's time, never one issue: not an example of how an issue's task is named
CONTAINER = re.compile(r"contingenc|service request|core (?:site )?updates|ongoing support|reporting|meetings?\b"
                       r"|estimations?\b|\bpm\b|project management|scrum|stand-?up|check-?ins?\b", re.I)


def clean_title(text) -> str:
    """One line, no leading "IMPORTANT!" (some session titles carry it), no quotes around it or full stop after it,
    a capital first letter, cut at a word to TITLE_MAX."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    t = re.sub(r"^important!\s*", "", t, flags=re.I).strip().strip('"“”\'').strip()
    t = re.sub(r"(…|\.\.\.|\.)\s*$", "", t).strip()
    if len(t) > TITLE_MAX:
        t = t[:TITLE_MAX + 1].rsplit(" ", 1)[0].rstrip(",;:-– ")
    return t[:1].upper() + t[1:]


def guard_title(text) -> tuple:
    """(the title, None) when a drafted title may be offered, else ("", why): the note's checks (askclaude.guard_note:
    no path, web address, token, code, tool's name, invisible character, remark on time) and one sentence."""
    t, bad = askclaude.guard_note(clean_title(text))
    if bad:
        return "", bad
    if TIME_REMARK.search(t):
        return "", "a remark on the time"
    if askclaude._SENTENCE_END.findall(t) or "\n" in str(text or "").strip():  # its own full stop is gone already
        return "", "more than one line"
    return t, None


def guard_description(text) -> tuple:
    """(the description, None) or ("", why): every line through the note's checks (a client may see the task), at
    most DESC_MAX characters. Lines may be a short list ("- ..."). A sentence about the time itself (TIME_REMARK) is
    left out, not the whole description."""
    lines = [re.sub(r"[ \t]+", " ", x).strip() for x in str(text or "").replace("\r", "").split("\n")]
    lines = [" ".join(s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", x) if not TIME_REMARK.search(s)).strip()
             for x in lines]
    lines = [x for x in lines if x and x not in ("-", "*", "•")]
    if not lines:
        return "", None
    kept: list = []
    for line in lines:  # whole sentences while they fit: a long draft loses its end, never half a sentence
        room = DESC_MAX - sum(len(x) + 1 for x in kept)
        if len(line) > room:
            fit = ""
            for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line):
                if len(fit) + len(s) + 1 > room:
                    break
                fit = f"{fit} {s}".strip()
            if fit and not re.fullmatch(r"[-*•]", fit):
                kept.append(fit)
            break
        kept.append(line)
    if not kept:
        return "", "too long"
    for line in kept:  # the note's checks themselves, folded (askclaude.guard_note also limits length and sentences)
        why = askclaude.text_problem(line)
        if why:
            return "", why
    return "\n".join(kept), None


def _longest(rows: list) -> dict:
    return max(rows, key=lambda r: r.get("e", 0) - r.get("s", 0)) if rows else {}


def local_draft(rows: list, meeting: str = "") -> dict:
    """{"title", "description", "from"} made on this Mac: a meeting's title when the time was a meeting (the page
    says so only when no work was seen in it), else the first commit subject made then, and all of them as a list
    when there are several. "from" says which: meeting, commits or none. Never the session's title: on 14 ranges
    where a task was really created, it was a long session's first topic or in another language, and named the task
    in none of them."""
    if meeting:
        title, _ = guard_title(meeting)
        return {"title": title, "description": "", "from": "meeting" if title else "none"}
    subjects = list(dict.fromkeys(c for r in sorted(rows, key=lambda r: r.get("s", 0)) for c in r.get("commits") or []))
    if subjects:
        title, _ = guard_title(subjects[0])
        if title:
            desc = "\n".join(f"- {s}" for s in subjects[:6]) if len(subjects) > 1 else ""
            return {"title": title, "description": desc, "from": "commits"}
    return {"title": "", "description": "", "from": "none"}


SCHEMA = {"type": "object", "required": ["title", "description"],
          "properties": {"title": {"type": "string", "maxLength": TITLE_MAX},
                         "description": {"type": "string", "maxLength": DESC_MAX}}}
PROMPT = """Draft a new task in Productive, the project tool of a web agency, for the work below: done on {date} from \
{start} to {end}, in the repository {repo}, for which no task exists yet. "steps" are what the person typed and \
Claude's reply at the end of each step, which says what was done; "commits" are the subjects of the commits they \
made then; "sessions" are the titles of the work sessions.
TITLE: the issue or deliverable this work is for, as the agency names its tasks: short (at most {title_max} \
characters, usually 4 to 9 words), the thing itself, not the activity ("Firefox: ordered list numbering continues \
across lists", "Charts: horizontal bar option", "Live search out of date"). An "Area: what" form is common. No \
ticket numbers, no person's name, no dates, no period ("Contingency - Nov 2026" is a container, never this task). \
"their_tasks" are titles of tasks this person used lately: follow their style, never copy one.
DESCRIPTION: one to three short sentences, or up to four "- " lines, that tell a project manager what the task is \
and what was done, in plain words. A client may read it. Say only what the work shows; never write about these \
notes themselves (what the steps show or leave out).
Never put in either: file names or paths, web addresses, code, error messages or log lines, passwords, keys or \
tokens, the tools that helped (no Claude, Pulsar, agents or AI tools), anyone's private matters, how long it took, \
or that the time was logged or is billable.
Write in English, always. Both are empty when the work does not show what \
it was.

WORK:
{work}"""
_cache: dict = {}
_lock = threading.Lock()


def examples(history: list, project: str = "", limit: int = 8, since: str = "") -> list:
    """Titles of the person's own recent tasks, those of `project` first: how their organization names tasks. Only
    tasks of their own time entries (`history`) on or after `since` (the 30 days of Ask Claude's "your tasks",
    askclaude.history_span): a task's name is anyone's words in Productive, so no other task's goes to Claude.
    Containers (contingency, core updates, meetings…) and any title the guard would refuse are left out."""
    newest = sorted((r for r in history if r.get("task") and str(r.get("date") or "") >= since),
                    key=lambda r: r.get("date") or "", reverse=True)
    out: list = []
    for same in (True, False):
        for r in newest:
            if len(out) >= limit:
                return out
            if (r.get("project") == project) != same or not project and same:
                continue
            title = re.sub(r"^\[[A-Z]+-\d+\]\s*", "", str(r["task"])).strip()  # an old ticket key is not the style
            if title and title not in out and not CONTAINER.search(title) and not askclaude.guard_note(title)[1]:
                out.append(title)
    return out


def claude_draft(date: str, rows: list, t0: float, history: list = (), project: str = "", run=subprocess.run,
                 tz=None, model: str = None) -> dict:
    """A title and description by Claude (`model`, chosen in Settings for "task"; Sonnet by default) from the rows'
    sessions (steps with Claude's replies), commits and session titles: {"title", "description", "why", "cost"}.
    Either may be "" when the guard refused it (why says which). Raises askclaude.AskError with a sentence for the
    page when Claude Code fails."""
    model = claudeuses.chosen("task", model)
    rows = sorted(rows, key=lambda r: r.get("s", 0))
    a, b = min(r["s"] for r in rows), max(r["e"] for r in rows)
    titles = list(dict.fromkeys(t for r in rows for t in r.get("titles") or []))
    said = askclaude.steps(t0, titles, a, b)
    made = list(dict.fromkeys(c for r in rows for c in r.get("commits") or []))[:10]
    if not said and not made:
        return {"title": "", "description": "", "why": "nothing in the session or the commits says what was done"}
    repo = _longest(rows).get("project") or ""
    sig = (date, a, b, repo, len(said), len(made), said[-1]["reply"][:40] if said else "", model)
    with _lock:
        if sig in _cache:
            return _cache[sig]
    work = json.dumps({"sessions": [clean_title(t) for t in titles[:3]], "steps": said, "commits": made,
                       "their_tasks": examples(history, project, since=askclaude.history_span(date)[0])},
                      ensure_ascii=False, indent=1)
    prompt = PROMPT.format(date=date, start=askclaude.clock_at(t0, a, tz), end=askclaude.clock_at(t0, b, tz),
                           repo=repo, title_max=TITLE_MAX, work=work)
    binary = productive._claude_binary()
    if not binary:
        raise askclaude.AskError("Claude Code was not found on this Mac.")
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    try:  # no tools, no MCP servers, no settings' hooks: only the text above goes to Claude
        done = run([binary, "-p", "--model", model, "--no-session-persistence", "--output-format", "json",
                    "--json-schema", json.dumps(SCHEMA), "--max-budget-usd", str(claudeuses.cap("task", model)),
                    "--tools", "", "--permission-mode", "dontAsk", "--strict-mcp-config", "--setting-sources", ""],
                   input=prompt, capture_output=True, text=True, timeout=90, cwd=config.CACHE_DIR, env=askclaude._env())
    except subprocess.TimeoutExpired:
        raise askclaude.AskError("Claude took too long to draft the task.") from None
    except OSError as exc:
        raise askclaude.AskError(logbook.said("claude code", "Claude Code could not be started on this Mac. Check that it is installed: run "
                                 "claude in Terminal.", exc)) from None
    res = askclaude._answer(done, "draft", "task", model)
    got = res.get("structured_output") if isinstance(res.get("structured_output"), dict) else {}
    title, bad_t = guard_title(got.get("title"))
    desc, bad_d = guard_description(got.get("description"))
    # another client's name in this client's task (a client may read it), or the person's own, as for a note
    own, others = askclaude._own_projects(_longest(rows), history) + [project], [h.get("project") for h in history]
    person = askclaude._person()
    if title and askclaude.foreign_name(title, own, others, person):
        title, bad_t = "", "a name from another project"
    if desc and askclaude.foreign_name(desc, own, others, person):
        desc, bad_d = "", "a name from another project"
    why = "; ".join(f"the {what} was left out ({bad})" for what, bad in (("title", bad_t), ("description", bad_d))
                    if bad)
    answer = {"title": title, "description": desc, "why": why, "cost": res.get("total_cost_usd")}
    with _lock:
        _cache[sig] = answer
        for old in list(_cache)[:-60]:
            del _cache[old]
    return answer


MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def places(found: dict, date: str, person: str) -> dict:
    """Where a new task can go in a project (`found`: productive.project_places), and which to offer first:
    {"parents": [{taskId, title}], "lists": [{id, name}], "pick": "parent:<id>" | "list:<id>" | "none" | ""}.
    - parents: the open tasks that hold a period's issues (PERIOD_TASK), newest first. FI's maintenance projects put
      each issue under the period's contingency task as a subtask, so the one whose name has the date's month (and
      year) comes first, else the newest.
    - else the task list of your newest open task in the project, while that list is active;
    - else the only task list; with none, directly in the project ("none"); with several, "": the page asks.
    A subtask cannot have subtasks, so only top-level tasks are offered as parents (Productive refuses the rest)."""
    tasks, lists = found.get("tasks") or [], found.get("lists") or []
    month = MONTHS[int(date[5:7]) - 1] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or "") else ""
    year = (date or "")[:4]

    def fit(t):
        words = re.findall(r"[a-z]+|\d{4}", t["title"].lower())
        return (bool(month) and any(w.startswith(month) for w in words), year in words, t.get("created") or "")
    parents = sorted((t for t in tasks if PERIOD_TASK.search(t.get("title") or "")),
                     key=lambda t: t.get("created") or "", reverse=True)
    out = {"parents": [{"taskId": t["taskId"], "title": t["title"]} for t in parents],
           "lists": [{"id": x["id"], "name": x["name"]} for x in lists]}
    if parents:
        return {**out, "pick": "parent:" + max(parents, key=fit)["taskId"]}
    active = {x["id"] for x in lists}
    mine = [t for t in tasks if str(t.get("assignee") or "") == str(person) and t.get("list") in active]
    if mine:
        return {**out, "pick": "list:" + max(mine, key=lambda t: t.get("created") or "")["list"]}
    if len(lists) == 1:
        return {**out, "pick": "list:" + lists[0]["id"]}
    return {**out, "pick": "" if lists else "none"}


GENERIC = {"ongoing", "support", "project", "projects", "internal", "foster", "interactive", "website", "proposals",
           "development", "processes", "client", "build", "phase", "maintenance", "drupal", "wordpress", "upgrade",
           "initiative", "sites", "school", "faculty"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def guess_project(projects: list, hint: str = "", repo: str = "") -> str:
    """The project a new task most likely belongs to, or "": the project named `hint` (the task picked in the log
    dialog: the work's own task, a rule's, or the person's choice), else the one the work's repository names, loosely
    ("acme" and "Acme-OS Ongoing Support", "initechweb" and "INI-OS", "globex-r" and "GX-OS Globex"), when only one
    does, or the one logged to most lately among them."""
    hint = (hint or "").strip().lower()
    if hint:
        for p in projects:
            if p["name"].strip().lower() == hint:
                return p["id"]
    r = _norm(repo.split(" / ")[0])
    if len(r) < 3:
        return ""
    found = []
    for p in projects:
        code = _norm(re.split(r"[-\s–]", p["name"].strip(), 1)[0])
        words = {_norm(w) for w in re.findall(r"[A-Za-z0-9]+", p["name"])} - GENERIC
        if r in _norm(p["name"]) or (len(code) >= 3 and code in r) or any(len(w) >= 5 and w in r for w in words):
            found.append(p)
    if not found:
        return ""
    return max(found, key=lambda p: p.get("lately") or 0)["id"] if len(found) > 1 else found[0]["id"]


# ---- the status, estimate and service offered for a new task (HOW-IT-WORKS 5.1, "Create a task") ----------------
TODO = re.compile(r"^\s*to\s*-?\s*do\s*$", re.I)
# the budget's contingency line, most specific first; never the overage or rollover lines next to it
CONTINGENCY = (re.compile(r"contingenc", re.I), re.compile(r"service requests?|support requests?", re.I))
NOT_CONTINGENCY = re.compile(r"overage|rollover", re.I)
SIZES_MIN = 2  # tasks of the same kind with an estimate, at least, before their median is offered
ISSUE_MAX = 8 * 60  # minutes: a median above it comes from projects or phases, not one issue's task; it is not offered


def dur(minutes) -> str:
    """90 -> "1 h 30 min", as the page writes a duration."""
    m = int(round(float(minutes or 0)))
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h" + (f" {m % 60} min" if m % 60 else "")


def _median(xs: list) -> float:
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _status(statuses: list, kind: str) -> dict:
    """A subtask starts in the workflow's first status (Backlog), a task in a list or the project in its "To Do":
    where tasks made by developers started (HOW-IT-WORKS 5.1)."""
    first = next((s for s in statuses if s["category"] == "1"), None) or (statuses[0] if statuses else None)
    if not first:
        return {"id": "", "why": "The project's statuses could not be read: Productive puts it in its first one."}
    if kind == "parent":
        return {"id": first["id"], "why": "Where new subtasks start."}
    todo = next((s for s in statuses if s["category"] == "1" and TODO.match(s["name"])), None)
    if todo:
        return {"id": todo["id"], "why": "Where new tasks usually start."}
    return {"id": first["id"], "why": "The workflow's first status."}


def _estimate(kind: str, parent: dict, sizes) -> dict:
    """{minutes, why, parent}: none when the parent carries one (`parent`, its minutes; a subtask may then have none);
    else the median of the project's recent tasks of the same kind that have one (subtasks for a subtask, top-level
    tasks other than containers otherwise), to the nearest 5 minutes; else none, said so."""
    parent = parent or {}
    own = parent.get("estimate")
    own = productive.minutes_of(own) if own not in (None, "") else 0
    if kind == "parent" and own > 0:
        return {"minutes": None, "parent": round(own),
                "why": f"Left empty, as “{parent.get('title') or 'its parent'}” carries {dur(own)}."}
    sub = kind == "parent"
    same = [m for m, is_sub, title in (sizes or []) if bool(is_sub) == sub and m > 0 and (sub or not CONTAINER.search(title))]
    name = f"“{parent['title']}”" if parent.get("title") else "Its parent"
    lead = "" if not sub else f"{name} could not be read. " if parent.get("unread") else f"{name} has none. "
    if sizes is None:
        return {"minutes": None, "parent": 0, "why": lead + "The project's estimates could not be read: add one if you know it."}
    if len(same) < SIZES_MIN:
        return {"minutes": None, "parent": 0, "why": lead + "Too few tasks like it in this project to go by."}
    median = _median(same)
    if median > ISSUE_MAX:  # a new task here is one issue: "60 h", the size of a project's phases, would mislead a PM
        return {"minutes": None, "parent": 0,
                "why": lead + f"Left empty, as this project's {'subtasks' if sub else 'tasks'} are much bigger "
                              f"({dur(min(same))} to {dur(max(same))}) than one issue."}
    return {"minutes": max(5, int(round(median / 5)) * 5), "parent": 0,
            "why": lead + f"The median of the {len(same)} recent {'subtasks' if sub else 'tasks'} that have one "
                          f"({dur(min(same))} to {dur(max(same))})."}


def _service(kind: str, parent: dict, services, date: str):
    """The service this time goes to once the task exists (the log dialog picks it), or None: the parent's own when it
    takes time on that date; under a period's task, the budget's contingency line; else the only service."""
    if services is None:
        return None, "The project's services could not be read: pick one when logging."
    parent = parent or {}
    by_id = {x["id"]: x for x in services}
    if kind == "parent" and parent.get("serviceId") in by_id:
        return by_id[parent["serviceId"]], "The parent task's service."
    if kind == "parent" and PERIOD_TASK.search(parent.get("title") or ""):
        period = (date or "")[:7]
        for rx in CONTINGENCY:
            hits = [x for x in services if rx.search(x.get("name") or "") and not NOT_CONTINGENCY.search(x.get("name") or "")]
            if hits:  # two budgets open at once: the one named for the month
                pick = next((x for x in hits if period and period in (x.get("budget") or "").replace("/", "-")), hits[0])
                return pick, "The budget's contingency line."
    if len(services) == 1:
        return services[0], "The only service that takes time on that date."
    if not services:
        return None, "No service of this project takes time on that date: ask its PM."
    return None, "Several services take time on that date: pick one."


def propose(statuses, kind: str, parent, services, sizes, date: str) -> dict:
    """What the new-task form offers, each with a sentence saying why: {"statuses": the open ones (not started, then
    started), "status": {id, why}, "estimate": {minutes or None, why, parent}, "services": those that take time on
    the date, "service": {id, name, budget, why}}. statuses, services and sizes are None when they could not be read (productive.workflow_statuses,
    project_services, project_estimates' "sizes"); kind is "parent" (a subtask of `parent`, productive.task_details),
    "list" or "none"; a parent that could not be read is {"title", "unread": True}. The service is never sent with
    the task: the log dialog picks it for this time."""
    open_ = [s for s in (statuses or []) if s["category"] in ("1", "2")]
    picked, why = _service(kind, parent, services, date)
    service = {"id": picked["id"], "name": (picked.get("name") or "").strip(), "budget": picked.get("budget") or ""} \
        if picked else {"id": "", "name": "", "budget": ""}
    return {"statuses": [{"id": s["id"], "name": s["name"], "category": s["category"]} for s in open_],
            "status": _status(open_, kind), "estimate": _estimate(kind, parent, sizes),
            "services": services or [], "service": {**service, "why": why}}
