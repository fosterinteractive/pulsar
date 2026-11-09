"""The "Likely" column for anyone who works in Claude Code: which session, and so which project, each minute of work
most likely went to, and what the agents did meanwhile in the background.

Read only and local only. Claude Code keeps every session as a JSON-lines file, ~/.claude/projects/<folder>/<id>.jsonl:
what the person typed, what the agent did (its tool calls name the files it touched) and a title. A subagent's own
calls are in <folder>/<id>/subagents/*.jsonl, and count for the session that started it.

  * A turn's project is the git repository its tool calls worked in, weighted by what each call did there: a file
    changed (Edit, Write) counts 3, a command run in a folder of it (Bash cd, git -C, a path that exists) 2, a file
    read or searched 1, a path the person typed 1; a word that only names a project counts nothing. One session can
    so work in several repositories, one turn at a time. A turn that touched none belongs to its neighbours'
    project, then to the session's title, then to the folder the session ran in.
  * Each minute the activity tracker saw work goes to a session: the one whose title the editor window showed, else
    one whose agent was running, else the one last used in that sitting. Meetings and personal windows go to none.
  * The other sessions' agents that ran meanwhile, in another repository, are the background, at their real time.
    An agent's stretch counts as attended work while it runs on a chain started by a message the person typed, and
    ends at a silence of more than 10 minutes (a long command, a wait): a new message starts the next one.
  * Without a tracker, the time an agent ran on a request, and the answers typed within Settings'
    "Away after", count as work.

Nothing here knows any person, company or client: projects come from the folders on this computer (or from a naming
function given by the caller, likely.py).
"""

from __future__ import annotations

import bisect
import glob
import json
import math
import os
import re
import time
from datetime import datetime

from . import config
from .config import Newest

HOME = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")
CLAUDE_APPS = re.compile(r"^(Cursor|Code|Visual Studio Code|Windsurf|Terminal|iTerm2?|Warp|Ghostty|Claude)$", re.I)
MEETING = re.compile(r"zoom|meet\.google|google meet|microsoft teams|\bteams\b|huddle|webex|meeting", re.I)
NOT_TYPED = ("<task-notification>", "<local-command", "Caveat:", "<command-message>", "[Request interrupted",
             "This session is being continued")
# paths in the home folder (also written ~/ or $HOME/, as commands often do), in temporary folders (a clone an agent
# made there) and on other disks
PATH = (r"(?:" + re.escape(HOME) + r"|(?<![\w~$/.-])(?:~|\$HOME|\$\{HOME\})|/private/tmp|/private/var/folders|/tmp"
        r"|/var/folders|/Volumes)/[^\s'\"`<>|;,()\[\]{}]+")
PATH_RE = re.compile(PATH)
_TILDE = re.compile(r"^(?:~|\$HOME|\$\{HOME\})(?=/)")


def find_paths(text: str) -> list:
    """The paths named in a text, each as a full path: ~/Sites/x and $HOME/Sites/x become the home folder's."""
    return [_TILDE.sub(lambda _: HOME, p) for p in PATH_RE.findall(text)]
# What a tool call says about the repository a turn worked in: changing a file most, running a command in a folder
# next, reading or searching least. A path the person typed counts as a read; a word that only names a project (a
# search for it, a commit message, an instruction to a subagent) counts nothing.
EDITS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
READS = frozenset({"Read", "Grep", "Glob", "LS", "NotebookRead"})
W_EDIT, W_RUN, W_READ, W_TYPED = 3, 2, 1, 1
HARD = W_RUN  # from this weight up a call did work in the repository (Edit, Write, a command in it)
SILENCE = 600  # seconds an agent may stay silent while its stretch still counts as attended work
CD = re.compile(r"(?:^|[\s;&|(])(?:cd|pushd)\s+['\"]?(" + PATH + ")")
GIT_C = re.compile(r"\bgit\s+-C\s+['\"]?(" + PATH + ")")
# where the walk up stops: these are never a project themselves
ROOTS = {HOME, "/", "/private", "/private/tmp", "/tmp", "/private/var", "/private/var/folders", "/var", "/var/folders",
         "/Volumes"}
TOOLING = os.path.join(HOME, ".claude")  # Claude Code's own files (memory, skills, plans): tools, not a project
# Logging time: Pulsar itself, or Productive in a browser. It is work, but no client's: it goes to no session.
LOGGING = re.compile(r"^Pulsar$|app\.productive\.io|(127\.0\.0\.1|localhost):56\d\d", re.I)
# Time in Pulsar or Productive counts as the work around it (1.6.9): set aside as "time spent logging", its glances in
# the middle of other work showed as gaps there (2026-11-09: 17 minutes in turns of 1 to 3 while deploying a site)
LOGGING_IS_WORK = True
# ...except Pulsar's own window while a session in Pulsar's own repository is at work (a message typed, or its agent
# running, within the away time): that is testing what was just built, the session's work (dayplan.OWN_REPO too)
PULSAR_WINDOW = re.compile(r"^Pulsar$|(127\.0\.0\.1|localhost):56\d\d", re.I)
OWN_REPO = re.compile(r"^pulsar(-preview)?$", re.I)


def parse_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def record(line: str) -> dict:
    """One line of a session file as a dict, {} when it is not one (cut short, or of a shape this code does not know):
    an odd line is skipped, never the session or the day."""
    try:
        r = json.loads(line)
    except (ValueError, RecursionError):
        return {}
    return r if isinstance(r, dict) else {}


def _obj(v) -> dict:
    return v if isinstance(v, dict) else {}


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
    return ""


def human_prompt(r: dict) -> str | None:
    """What the person typed, or None for anything Claude Code wrote itself (tool results, notices, summaries)."""
    if r.get("type") == "attachment":
        att = _obj(r.get("attachment"))
        if att.get("type") == "queued_command" and _obj(att.get("origin")).get("kind") == "human":
            return text_of(att.get("prompt")) or "(message)"
        return None
    if r.get("type") != "user" or r.get("isMeta") or r.get("isCompactSummary") or r.get("toolUseResult") is not None:
        return None
    content = _obj(r.get("message")).get("content")
    if isinstance(content, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content):
        return None
    origin = r.get("origin")
    if origin is not None:  # recent Claude Code versions mark typed messages
        return (text_of(content) or "(attachment)") if _obj(origin).get("kind") == "human" else None
    text = text_of(content)
    return None if not text or text.lstrip().startswith(NOT_TYPED) else text


def notification(r: dict) -> bool:
    """A background task reporting back: the agent starts working again without anyone typing."""
    if r.get("type") == "attachment":
        att = _obj(r.get("attachment"))
        return att.get("type") == "queued_command" and text_of(att.get("prompt")).lstrip().startswith("<task-notification>")
    if r.get("type") != "user":
        return False
    if _obj(r.get("origin")).get("kind") == "task-notification":
        return True
    return text_of(_obj(r.get("message")).get("content")).lstrip().startswith("<task-notification>")


def tool_paths(r: dict) -> list:
    """The paths in the home folder that an agent's tool calls named."""
    found: list = []
    content = _obj(r.get("message")).get("content")
    for item in content if isinstance(content, list) else []:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            stack = [item.get("input")]
            while stack:
                v = stack.pop()
                if isinstance(v, str):
                    found += find_paths(v[:4000])
                elif isinstance(v, dict):
                    stack.extend(v.values())
                elif isinstance(v, list):
                    stack.extend(v)
    return found


_exists = Newest(5000)  # path -> whether it, or the folder it is in, exists


def _real(path: str) -> bool:
    """The path, or the folder it is in, exists: a command used it, not a word that only looks like one."""
    got = _exists.get(path)
    if got is None:
        got = os.path.exists(path) or os.path.isdir(os.path.dirname(path.rstrip("/")))
        _exists[path] = got
    return got


def tasks_seen(r: dict) -> list:
    """The Productive tasks an agent message opened or linked: a task loaded by its id (Productive's MCP), or a task
    link in Claude's own words. The session's own work names them; no one asked Claude about it."""
    out: list = []
    content = _obj(r.get("message")).get("content")
    for item in content if isinstance(content, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            out += TASK_LINK.findall(str(item.get("text") or "")[:20000])
        elif item.get("type") == "tool_use" and str(item.get("name") or "").startswith("mcp__productive__"):
            inp = _obj(item.get("input"))
            if inp.get("resource_type") == "tasks" and str(inp.get("id") or "").isdigit():
                out.append(str(inp["id"]))
    return out


def tool_calls(r: dict) -> list:
    """[(path, weight)]: the paths an agent's tool calls named, weighted by what each call did with them (EDITS,
    Bash, READS above). Other tools count nothing (what a subagent did is in its own file), except a Productive call on
    time entries, as the marker "tool:mcp__productive__time_entries"."""
    found: list = []
    content = _obj(r.get("message")).get("content")
    for item in content if isinstance(content, list) else []:
        if not (isinstance(item, dict) and item.get("type") == "tool_use"):
            continue
        name, inp = str(item.get("name") or ""), _obj(item.get("input"))
        if name in EDITS:
            found += [(p, W_EDIT) for k in ("file_path", "notebook_path") for p in find_paths(str(inp.get(k) or "")[:1000])]
        elif name == "Bash":
            cmd = str(inp.get("command") or "")[:4000]
            named = {_TILDE.sub(lambda _: HOME, p) for p in CD.findall(cmd) + GIT_C.findall(cmd)}
            found += [(p, W_RUN) for p in find_paths(cmd) if p in named or _real(p)]
        elif name in READS:
            found += [(p, W_READ) for k in ("file_path", "notebook_path", "path", "pattern")
                      for p in find_paths(str(inp.get(k) or "")[:1000])]
        elif name.startswith("mcp__productive__") and "time_entries" in json.dumps(inp)[:4000]:
            # a call on time entries: logging time. Not a path: only a naming function that knows it names it (the
            # fi-time-from-activity script's "time logging"), so such a turn is never another project's work
            found.append(("tool:mcp__productive__time_entries", W_RUN))
    return found


_repo_of = Newest(5000)  # folder -> its repository (None outside one)


def project_of_path(path: str) -> str | None:
    """The name of the git repository a path is in (the folder holding .git), or None outside one."""
    d = path.rstrip("/")
    if d == TOOLING or d.startswith(TOOLING + "/"):
        return None
    seen, name = [], None
    while d and d not in ROOTS:
        if d in _repo_of:
            name = _repo_of[d]
            break
        seen.append(d)
        if os.path.exists(os.path.join(d, ".git")):
            name = repo_name(d)
            break
        d = os.path.dirname(d)
    for x in seen:
        _repo_of[x] = name
    return name


def repo_name(folder: str) -> str:
    """A repository's own name, from its origin's address (a clone in a folder called "repo" is still "fi-drupal-qa"),
    else the folder's name. Reads .git/config only; no git command runs."""
    git = os.path.join(folder, ".git")
    try:
        if os.path.isfile(git):  # a worktree: .git says where the real one is
            with open(git, encoding="utf-8") as fh:
                linked = fh.read().split("gitdir:", 1)[-1].strip()
            common = os.path.join(linked, "commondir")
            git = os.path.normpath(os.path.join(linked, open(common, encoding="utf-8").read().strip())) \
                if os.path.exists(common) else linked
        with open(os.path.join(git, "config"), encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', text)
        if m:
            name = re.sub(r"\.git$", "", m.group(1).rstrip("/")).rsplit("/", 1)[-1].rsplit(":", 1)[-1]
            if name and name.lower() not in GENERIC:  # every Pantheon site's address ends in repository.git
                return name
    except OSError:
        pass
    name = os.path.basename(folder)
    return os.path.basename(os.path.dirname(folder)) if name.lower() in GENERIC else name


GENERIC = {"repo", "repository", "src", "app", "code", "project", "site", "web", "main", "clone", "checkout", "workspace",
           "work"}  # names that say nothing about which project it is


def norm_title(s: str) -> str:
    s = (s if isinstance(s, str) else "").split(" — ")[0]
    return re.sub(r"(…|\.\.\.)\s*$", "", s).strip().lower()


def tool_result(r: dict) -> bool:
    """A tool's answer coming back to the agent: the agent is still at work."""
    if r.get("type") != "user":
        return False
    if r.get("toolUseResult") is not None:
        return True
    content = _obj(r.get("message")).get("content")
    return isinstance(content, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content)


# A record's UTC time as Claude Code writes it, "2026-11-09T11:21:05.123Z": compared as text with the day's bounds, a
# line whose every such time is outside them is skipped before it is parsed (most of a long session's lines; parsing
# them was most of a day's cost). Any other line is parsed and checked as before.
_TS = re.compile(r'"timestamp":"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?Z"')


def _bounds(lo: float, hi: float) -> tuple:
    """lo and hi as _TS's text, rounded outwards: a time skipped by them is outside [lo, hi) for certain."""
    return (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(math.floor(lo))),
            time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(math.ceil(hi))))


def _outside(line: str, lo_s: str, hi_s: str) -> bool:
    """Every record time in the line is before lo_s or at hi_s or later (_bounds); False when it has none."""
    found = _TS.findall(line)
    return bool(found) and all(x < lo_s or x >= hi_s for x in found)


def read_session(path: str, t0: float, t1: float, subagents: bool = False, strings_of=None) -> dict | None:
    """One session's messages, agent activity and titles around [t0, t1); None if nobody typed in it that day.
    subagents: also the calls of the subagents it started (<folder>/<id>/subagents/*.jsonl). strings_of(record): the
    strings a naming of turns counts (likely.skill_turns), kept per agent message as "strings"."""
    prompts, triggers, notified, assistant, titles, cwd, title, custom = [], [], [], [], set(), None, "", False
    calls, events, strings, seen_tasks, branches = [], [], [], [], []
    lo, hi = t0 - 6 * 3600, t1 + 3600
    lo_s, hi_s = _bounds(lo, hi)
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"ai-title"' in line or '"custom-title"' in line:
                r = record(line)
                name = r.get("customTitle") or r.get("aiTitle")
                if r.get("type") in ("ai-title", "custom-title") and name and isinstance(name, str):
                    titles.add(norm_title(name))
                    if r.get("type") == "custom-title" or not custom:  # a title the person set wins
                        title, custom = name, custom or r.get("type") == "custom-title"
                    continue
            if '"timestamp"' not in line or _outside(line, lo_s, hi_s):
                continue
            try:
                r = record(line)
                t = parse_ts(r["timestamp"])
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if not lo <= t < hi or r.get("isSidechain"):
                continue
            cwd = r["cwd"] if isinstance(r.get("cwd"), str) and r["cwd"] else cwd
            text = human_prompt(r)
            if text is not None:
                prompts.append((t, text.strip()))
                triggers.append(t)
                if isinstance(r.get("gitBranch"), str) and r["gitBranch"] and isinstance(r.get("cwd"), str):
                    branches.append((t, r["gitBranch"], r["cwd"]))  # the branch of that folder's repository
            elif notification(r):
                triggers.append(t)
                notified.append(t)
            elif r.get("type") == "assistant":
                assistant.append((t, tool_paths(r)))
                events.append(t)
                seen_tasks += [(t, tid) for tid in tasks_seen(r)]
                found = tool_calls(r)
                if found:
                    calls.append((t, found))
                if strings_of:
                    try:
                        strings.append((t, list(strings_of(r))))
                    except Exception:  # noqa: BLE001 - a record of a shape the naming does not know: that record only
                        pass
            elif tool_result(r):
                events.append(t)
    if not any(t0 <= t < t1 for t, _ in prompts):
        return None
    if subagents:
        _read_subagents(path, lo, hi, calls, events)
    prompts.sort()
    dedup: list = []
    for t, text in prompts:  # a message sent while the agent works can be recorded twice
        if dedup and dedup[-1][1][:80] == text[:80] and t - dedup[-1][0] < 600:
            continue
        dedup.append((t, text))
    assistant.sort(key=lambda x: x[0])
    calls.sort(key=lambda x: x[0])
    triggers.sort()
    events.sort()
    return {"id": os.path.basename(path)[:8], "cwd": cwd, "title": title, "titles": {x for x in titles if len(x) >= 5},
            "prompts": dedup, "triggers": triggers, "notified": sorted(notified), "assistant": assistant,
            "calls": calls, "events": events, "seen_tasks": sorted(seen_tasks), "branches": sorted(branches),
            **({"strings": sorted(strings, key=lambda x: x[0])} if strings_of else {})}


def _read_subagents(path: str, lo: float, hi: float, calls: list, events: list) -> None:
    """The calls and activity of the subagents a session started, added to the session's own: Claude Code keeps each
    in <folder>/<id>/subagents/<agent>.jsonl, and the session waits on them (or they report back)."""
    lo_s, hi_s = _bounds(lo, hi)
    for sub in glob.glob(os.path.join(path[:-len(".jsonl")] if path.endswith(".jsonl") else path, "subagents", "*.jsonl")):
        try:
            if os.path.getmtime(sub) < lo:
                continue
            with open(sub, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"timestamp"' not in line or _outside(line, lo_s, hi_s):
                        continue
                    r = record(line)
                    try:
                        t = parse_ts(r["timestamp"])
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue
                    if not lo <= t < hi:
                        continue
                    if r.get("type") == "assistant":
                        events.append(t)
                        found = tool_calls(r)
                        if found:
                            calls.append((t, found))
                    elif r.get("type") == "user":
                        events.append(t)
        except OSError:
            continue  # that subagent only


def analyse(s: dict, namer=None, turns=None) -> None:
    """The project of every turn (weighted by its tool calls), the spans the agent was running, and the stretches of
    attended agent work. namer(path) -> a project name or None (default: the git repository, project_of_path).
    turns: another naming of turns (likely.skill_turns: the fi-time-from-activity script's own), used in place of
    the weights when the session was read with its strings (read_session strings_of); a turn's hard calls then count
    for its project, at their highest weight."""
    prompts = s["prompts"]
    name_of = namer or project_of_path
    names: dict = {}

    def named(path: str):
        if path not in names:
            names[path] = name_of(path)
        return names[path]

    calls = s.get("calls")
    if calls is None:  # a session read without weights: every path the agent named counts once
        calls = [(t, [(p, W_READ) for p in paths]) for t, paths in s.get("assistant") or [] if paths]
    at = [t for t, _ in calls]
    folder = (project_of_path(s["cwd"]) or os.path.basename(s["cwd"].rstrip("/"))) if s["cwd"] else None
    out = []
    for i, (p, text) in enumerate(prompts):
        nxt = prompts[i + 1][0] if i + 1 < len(prompts) else float("inf")
        counts: dict = {}
        hard: dict = {}
        found = [(x, W_TYPED) for x in find_paths(text)]
        for _, pairs in calls[bisect.bisect_left(at, p):bisect.bisect_left(at, nxt)]:
            found += pairs
        for path, w in found:
            name = named(path)
            if name and w > 0:
                counts[name] = counts.get(name, 0) + w
                if w >= HARD:
                    hard[name] = hard.get(name, 0) + w
        out.append({"p": p, "project": max(counts, key=counts.get) if counts else None, "hard": hard})
    title = (s.get("title") or "").strip()[:40] or None
    last = "Claude Code"  # nothing named it
    named_by = _named_turns(s, turns) if turns and "strings" in s else None
    if named_by is not None:  # the other naming, with its own last resorts
        projects, title, folder, last = named_by
        for t, project in zip(out, projects):
            t["project"] = project
    for i, t in enumerate(out):
        if t["project"] is None:
            before = next((out[j]["project"] for j in range(i - 1, -1, -1) if out[j]["project"]), None)
            after = next((out[j]["project"] for j in range(i + 1, len(out)) if out[j]["project"]), None)
            t["project"] = before or after or title or folder or last
        if named_by is not None and t["hard"]:  # its hard calls count for the project the other naming gave it
            t["hard"] = {t["project"]: max(t["hard"].values())}
    s["turns"] = out
    events = s.get("events") or [t for t, _ in s.get("assistant") or []]
    running, trig = [], s["triggers"]
    for k, tk in enumerate(trig):
        nxt = trig[k + 1] if k + 1 < len(trig) else float("inf")
        ends = [t for t in events[bisect.bisect_left(events, tk):bisect.bisect_left(events, nxt)]]
        if ends and ends[-1] > tk:
            running.append((tk, ends[-1]))
    s["running"] = running
    s["chains"] = chains(trig, set(s.get("notified") or ()), events)


def _named_turns(s: dict, turns: dict):
    """([project or None per turn], title, folder, last resort) as the other naming names them: its count(strings,
    counts) over what was typed and each agent message's strings in the turn; its fallbacks are the session's title
    without a leading "IMPORTANT!", the folder as it names it, then "unknown". None when it fails on this session."""
    count = turns["count"]
    try:
        at = [t for t, _ in s["strings"]]
        projects = []
        for i, (p, text) in enumerate(s["prompts"]):
            nxt = s["prompts"][i + 1][0] if i + 1 < len(s["prompts"]) else float("inf")
            counts: dict = {}
            count([text], counts)
            for _, strings in s["strings"][bisect.bisect_left(at, p):bisect.bisect_left(at, nxt)]:
                count(strings, counts)
            projects.append(max(counts, key=counts.get) if counts else None)
        folder = None
        if s.get("cwd"):
            c: dict = {}
            count([s["cwd"]], c)
            folder = max(c, key=c.get) if c else os.path.basename(s["cwd"].rstrip("/"))
    except Exception:  # noqa: BLE001 - the naming changed shape: the weights name this session
        return None
    title = re.sub(r"^important!\s*", "", s.get("title") or "", flags=re.I).strip()[:40] or None
    return projects, title, folder, "unknown"


def chains(triggers: list, notified: set, events: list) -> list:
    """[(start, end)]: the stretches an agent worked on a chain started by a message the person typed. A background
    task reporting back (a notification) carries the chain on; a silence of more than SILENCE seconds ends it, and
    only the next typed message starts another. Silences up to SILENCE inside a stretch are part of it."""
    out, cur, last, live = [], None, None, False
    for k, tk in enumerate(triggers):
        nxt = triggers[k + 1] if k + 1 < len(triggers) else float("inf")
        if tk not in notified:  # typed
            if cur:
                out.append(tuple(cur))
            cur, last, live = [tk, tk], tk, True
        elif live and tk - last > SILENCE:
            out.append(tuple(cur))
            cur, live = None, False
        for t in events[bisect.bisect_left(events, tk):bisect.bisect_left(events, nxt)]:
            if not live:
                break
            if t - last > SILENCE:
                out.append(tuple(cur))
                cur, live = None, False
                break
            cur[1], last = t, t
    if cur:
        out.append(tuple(cur))
    return [(a, b) for a, b in out if b > a]


def turn_at(s: dict, t: float) -> dict:
    turn = s["turns"][0]
    for x in s["turns"]:
        if x["p"] > t:
            break
        turn = x
    return turn


def project_at(s: dict, t: float) -> str:
    return turn_at(s, t)["project"]


_day_reads = Newest(400)  # (session file, its size and time, t0, t1) -> its read for that day (read_day)


def read_day(path: str, t0: float, t1: float) -> dict | None:
    """read_session(path, t0, t1) for readers of the session's own messages and calls only (commits.py, Ask Claude's
    steps): kept while the file is unchanged, and the read load() made of that day when it made one (a fuller read:
    its own records are the same). Raises OSError as open() does."""
    st = os.stat(path)
    k = (path, st.st_size, st.st_mtime, t0, t1)
    if k not in _day_reads:
        _day_reads[k] = read_session(path, t0, t1)
    return _day_reads[k]


def load(t0: float, t1: float, projects_dir: str = PROJECTS_DIR, namer=None, turns=None) -> list:
    """The sessions someone typed in during [t0, t1), each read with its subagents and analysed (turns: analyse)."""
    sessions = []
    for path in glob.glob(os.path.join(projects_dir, "*", "*.jsonl")):
        try:
            if os.path.getmtime(path) < t0 - 3600:
                continue
            st = os.stat(path)
            s = read_session(path, t0, t1, subagents=True, strings_of=(turns or {}).get("strings"))
            _day_reads[(path, st.st_size, st.st_mtime, t0, t1)] = s
            if s:
                analyse(s, namer, turns)
                sessions.append(s)
        except (OSError, ValueError, TypeError, AttributeError):  # a file that cannot be read: that session only
            continue
    return sessions


def propose(t0: float, t1: float, items: list, tracker: str, projects_dir: str = PROJECTS_DIR, namer=None,
            logging_name: str | None = None, turns=None, away: int | None = None) -> dict:
    """The Likely column for the day [t0, t1): blocks of minutes, each with its project, the sessions' titles, how many
    messages were typed in it and the first one; and the agents' background work at its real time. `items` are the
    day's activity (minutes from t0, as trackers.py makes them); without any, the agents' run time stands in for the
    tracker. namer, turns: see analyse. logging_name: a project name that means time spent logging time (the
    fi-time-from-activity script's): its minutes go to no session, as Pulsar's own window does. away: the minutes
    without a sign of work that end a sitting, Settings' "Away after" (config.away_after) when not given."""
    return propose_from(load(t0, t1, projects_dir, namer, turns), t0, t1, items, tracker, logging_name, away)


def propose_from(sessions: list, t0: float, t1: float, items: list, tracker: str, logging_name: str | None = None,
                 away: int | None = None) -> dict:
    """propose() on sessions already read (load): the same rows."""
    away = config.away_after() if away is None else away
    n = max(1, int(round((t1 - t0) / 60)))
    m = lambda t: int((t - t0) // 60)  # noqa: E731 - a moment as the day's minute

    focus: list = [None] * n
    logging_s, work_s = [0.0] * n, [0.0] * n  # seconds of each minute in Pulsar or Productive, and in any work window
    pulsar_s, pulsar_it = [0.0] * n, [None] * n  # of those, in Pulsar's own window
    logging_it: list = [None] * n  # the Pulsar or Productive window of each such minute
    for it in items:
        if it.get("cat") != "work":
            continue  # personal windows (Settings > Personal windows) and away time go to no session
        is_logging = any(LOGGING.search(str(it.get(k) or "")) for k in ("app", "host", "url"))
        is_pulsar = is_logging and any(PULSAR_WINDOW.search(str(it.get(k) or "")) for k in ("app", "host", "url"))
        for x in range(max(0, int(it["s"])), min(n, int(-(-it["e"] // 1)))):
            seconds = (min(it["e"], x + 1) - max(it["s"], x)) * 60
            work_s[x] += seconds
            if is_logging:
                logging_s[x] += seconds
                logging_it[x] = it
                if is_pulsar:
                    pulsar_s[x] += seconds
                    pulsar_it[x] = it
            else:
                focus[x] = it
    # a minute that went mostly to logging is logging, even when another window shared it (a look at Pulsar between
    # two edits): it goes to no session
    logging_at = [logging_s[x] > 0 and 2 * logging_s[x] >= work_s[x] for x in range(n)]
    run_at: list = [[] for _ in range(n)]
    acts = []  # (minute, session) of every message and every end of an agent's run
    for i, s in enumerate(sessions):
        for a, b in s["running"]:
            for x in range(max(0, m(a)), min(n, m(b) + 1)):
                run_at[x].append(i)
            acts.append((m(b), i))
        acts += [(m(p), i) for p, _ in s["prompts"]]
    acts.sort()
    # Pulsar's own window, while a session in Pulsar's repository is at work: that session's minute, not logging
    own_at: list = [None] * n
    for x in range(n):
        if not (logging_at[x] and pulsar_s[x] and 2 * pulsar_s[x] >= logging_s[x]):
            continue
        near = [(t, i) for t, i in acts if x - away <= t <= x] + [(x, i) for i in run_at[x]]
        if not near:
            continue
        i = max(near)[1]
        project = project_at(sessions[i], t0 + x * 60 + 59)
        if OWN_REPO.match(project or "") and project != logging_name:
            own_at[x], logging_at[x], focus[x] = (i, project), False, pulsar_it[x]
    for x in range(n):
        if logging_at[x]:
            if LOGGING_IS_WORK:  # a window like any other: the work it sits in (the session whose tab came before)
                logging_at[x], focus[x] = False, logging_it[x]
            else:
                focus[x] = None
    present = [f is not None for f in focus]
    if not any(present):  # no tracker: an agent working on a request, and answers typed within the away time
        tracker = "no tracker"
        for s in sessions:
            for a, b in s["running"]:
                for x in range(max(0, m(a)), min(n, m(b) + 1)):
                    present[x] = True
            for p, _ in s["prompts"]:
                end = max((b for a, b in s["running"] if b <= p), default=None)
                if end is not None and p - end <= away * 60:
                    for x in range(max(0, m(end)), min(n, m(p) + 1)):
                        present[x] = True

    sitting: list = [None] * n  # (first, last) minute of the sitting a minute belongs to
    marks = [x for x in range(n) if present[x]]
    if marks:
        start = prev = marks[0]
        for x in marks[1:] + [None]:
            if x is None or x - prev > away:
                for y in range(start, prev + 1):
                    sitting[y] = (start, prev)
                start = x
            if x is not None:
                prev = x

    def latest(x: int, among=None):
        best = None
        for t, i in acts:
            if t > x:
                break
            if among is None or i in among:
                best = i
        return best

    def nearest(x: int):
        """The session last used in this sitting, else the next one used in it."""
        if sitting[x] is None:
            return None
        a, b = sitting[x]
        before = [i for t, i in acts if a <= t <= x]
        after = [i for t, i in acts if x < t <= b]
        return before[-1] if before else after[0] if after else None

    by_title: dict = {}
    projects_of = [{t["project"] for t in s["turns"]} for s in sessions]
    owner: list = [None] * n
    logged_turn = 0  # minutes of a turn that was time logging (logging_name)
    tab = None  # (sitting, session) of the last session tab in front: the work a browser or Slack minute comes from
    for x in range(n):
        if not present[x]:
            continue
        if own_at[x]:  # testing Pulsar in its own window (above)
            owner[x] = own_at[x]
            continue
        f, cands = focus[x], []
        if f is None or CLAUDE_APPS.search(f.get("app") or ""):
            key = norm_title(f["title"]) if f else ""
            if key and key not in by_title:
                by_title[key] = [i for i, s in enumerate(sessions)
                                 if len(key) >= 5 and any(t.startswith(key) or key.startswith(t) for t in s["titles"])]
            named = by_title.get(key) if key else None
            cands = named or run_at[x] or [i for i in [latest(x)] if i is not None]
            if named and len(named) == 1:
                tab = (sitting[x], named[0])
        elif not MEETING.search(" ".join((f.get("app") or "", f.get("title") or "", f.get("host") or ""))):
            # a page that names a session's repository; else the session whose tab was in front last in this sitting
            # (2026-11-09: the client's pages between turns in its session went to the session whose agent ran last,
            # another one working in the background); else the session used last
            page = " ".join((f.get("title") or "", f.get("url") or "")).lower()
            cands = [i for i, ps in enumerate(projects_of)
                     if any(len(p) >= 4 and p != logging_name and p.lower() in page for p in ps)] \
                or ([tab[1]] if tab and sitting[x] is not None and tab[0] == sitting[x] else []) \
                or [i for i in [nearest(x)] if i is not None]
        if len(cands) > 1:
            pick = latest(x, set(cands))
            cands = [pick if pick is not None else cands[0]]
        if cands:
            project = project_at(sessions[cands[0]], t0 + x * 60 + 59)
            if logging_name and project == logging_name:
                logged_turn += 1  # time spent logging time, in a Claude session: no session's work
                continue
            owner[x] = (cands[0], project)

    def single(i: int, project: str, until: float) -> bool:
        """Every turn of the session today, up to `until`, was in this project."""
        return all(t["project"] == project for t in sessions[i]["turns"] if t0 - 6 * 3600 <= t["p"] <= until)

    def links(ids: set, project: str, a: float, b: float) -> list:
        """Task links typed in these sessions, in a turn of this project, from LINK_BEFORE before a to b: newest first."""
        found = sorted(((t, tid) for i in ids for t, text in sessions[i]["prompts"] if a - LINK_BEFORE <= t <= b
                        and project_at(sessions[i], t) == project
                        for tid in TASK_LINK.findall(text) + TASK_NUMBER.findall(text)), reverse=True)
        return list(dict.fromkeys(tid for _, tid in found))[:4]

    def seen(ids: set, project: str, a: float, b: float) -> list:
        """Tasks the sessions' Claude opened or linked, in a turn of this project, from LINK_BEFORE before a to b."""
        found = sorted(((t, tid) for i in ids for t, tid in sessions[i].get("seen_tasks") or []
                        if a - LINK_BEFORE <= t <= b and project_at(sessions[i], t) == project), reverse=True)
        return list(dict.fromkeys(tid for _, tid in found))[:4]

    def branches_of(ids: set, project: str, a: float, b: float) -> list:
        """The git branches the sessions were on when you typed, in a turn of this project, that name work."""
        found = [br for i in ids for t, br, cwd in sessions[i].get("branches") or []
                 if a - LINK_BEFORE <= t <= b and project_at(sessions[i], t) == project and not PLAIN_BRANCH.match(br)
                 and project_of_path(cwd) == project]  # the session's folder is this repository, not another's
        return list(dict.fromkeys(found))[:3]

    def pages_of(x0: int, x1: int, ids: set, project: str) -> list:
        """The Productive task pages in front in the row's own minutes: [task, project]. The page's own "#123 " number
        goes (Productive puts it before the task's name)."""
        out = []
        for x in range(max(0, x0), min(n, x1)):
            if not owner[x] or owner[x][0] not in ids or owner[x][1] != project:
                continue
            f = focus[x]
            m = PRODUCTIVE_TAB.match(str((f or {}).get("title") or "").strip()) if f else None
            task = re.sub(r"^#\d+\s+", "", m.group(1).strip()) if m else ""
            if m and [task, m.group(2).strip()] not in out:
                out.append([task, m.group(2).strip()])
        return out[:3]

    def github_of(x0: int, x1: int, ids: set, project: str) -> list:
        """Titles of the pull requests and issues of this repository (or its "-kb-dev" tracker) in front in the row's
        own minutes: words of the work."""
        out = []
        for x in range(max(0, x0), min(n, x1)):
            if not owner[x] or owner[x][0] not in ids or owner[x][1] != project or not focus[x]:
                continue
            m = GITHUB_TAB.match(str(focus[x].get("title") or "").strip())
            repo = m.group(2).lower().removesuffix("-kb-dev") if m else ""
            if m and repo == (project or "").lower() and m.group(1).strip() not in out:
                out.append(m.group(1).strip())
        return out[:3]

    def row_of(e: dict, kind: str) -> dict:
        a, b = t0 + e["s"] * 60, t0 + e["e"] * 60
        typed = sorted((t, text) for i in e["_sessions"] for t, text in sessions[i]["prompts"]
                       if a <= t < b and project_at(sessions[i], t) == e["project"])
        return {"s": e["s"], "e": e["e"], "minutes": e["_minutes"], "project": e["project"], "kind": kind,
                "titles": sorted({sessions[i]["title"] for i in e["_sessions"] if sessions[i]["title"]}),
                "messages": len(typed), "first_message": (typed[0][1] if typed else "")[:200],
                "sessions": sorted(sessions[i]["id"] for i in e["_sessions"]),
                "single": all(single(i, e["project"], b) for i in e["_sessions"]),
                "repoLinks": list(dict.fromkeys(links(e["_sessions"], e["project"], a, b)
                                                 + [tid for i in e["_sessions"] for tid in TASK_NUMBER.findall(sessions[i]["title"] or "")]))[:4],
                "seenTasks": seen(e["_sessions"], e["project"], a, b),
                "branches": branches_of(e["_sessions"], e["project"], a, b),
                "taskPages": pages_of(int(e["s"]), int(e["e"]), e["_sessions"], e["project"]),
                "github": github_of(int(e["s"]), int(e["e"]), e["_sessions"], e["project"])}

    entries: list = []
    for x in range(n):
        if owner[x] is None:
            continue
        i, project = owner[x]
        e = entries[-1] if entries else None
        if e and e["project"] == project and x - e["_last"] <= 2:
            e["_last"], e["e"] = x, x + 1
            e["_minutes"] += 1
            e["_sessions"].add(i)
        else:
            entries.append({"s": x, "e": x + 1, "_last": x, "_minutes": 1, "project": project, "_sessions": {i}})
    rows = [row_of(e, "focused") for e in entries]

    # the background: agents that ran while another session (or none) had the minute, in another repository. A
    # minute counts as attended (qualifies) on a chain started by a typed message with no silence of over SILENCE, in a
    # turn whose calls did work in that repository (HARD); the rest is shown, never more
    chain_at = [set() for _ in range(n)]
    for i, s in enumerate(sessions):
        for a, b in s.get("chains") or []:
            for x in range(max(0, m(a)), min(n, m(b) + 1)):
                chain_at[x].add(i)
    open_bg: dict = {}  # (session, project, qualifies) -> the row being built
    background: list = []
    for x in range(n):
        front = owner[x]
        for i in sorted(set(run_at[x]) | chain_at[x]):
            if front and front[0] == i:
                continue
            t = t0 + x * 60 + 59
            turn = turn_at(sessions[i], t)
            project = turn["project"]
            if project == logging_name or (front and front[1] == project):
                continue  # time logging, or the repository in front already has the minute
            ok = i in chain_at[x] and turn.get("hard", {}).get(project, 0) >= HARD
            k = (i, project, ok)
            e = open_bg.get(k)
            if e and x - e["_last"] <= 2:
                e["_last"], e["e"] = x, x + 1
                e["_minutes"] += 1
            else:
                e = {"s": x, "e": x + 1, "_last": x, "_minutes": 1, "project": project, "_sessions": {i}, "_ok": ok}
                open_bg[k] = e
                background.append(e)
    bg_rows = []
    for e in sorted(background, key=lambda e: (e["s"], e["project"])):
        r = row_of(e, "background")
        r["qualifies"] = e["_ok"]
        bg_rows.append(r)
    return {"tracker": tracker, "working": sum(present), "proposed": sum(r["minutes"] for r in rows),
            "unplaced": [], "logging": sum(1 for x in range(n) if logging_at[x] and owner[x] is None) + logged_turn,
            "entries": rows, "background": bg_rows}


# ---- Productive tasks named in a session ------------------------------------------------------------------

TASK_LINK = re.compile(r"app\.productive\.io/[^\s/]+/(?:projects/\d+/)?tasks/(?:task/)?(\d+)")
# a task named by its number, as tools that start a session for a task title it: "Productive task 21429921" (2026-11-09:
# such a session's own task was not found, and another client's task logged once in it came first)
# a git branch that names no work: the trunk, an environment, a person's own branch
PLAIN_BRANCH = re.compile(r"^(main|master|qa|dev|develop|development|stag(e|ing)|live|prod(uction)?|head|.+-dev)$", re.I)
# a GitHub pull request or issue, as the browser titles it: "<title> by <author> · Pull Request #12 · owner/repo"
GITHUB_TAB = re.compile(r"^(.+?)(?: by [\w\[\].-]+)? · (?:Pull Request|Issue) #\d+ · [\w.-]+/([\w.-]+)")
# a task's page in Productive, as the browser titles it: "<task> - Tasks - <project> - Productive"
PRODUCTIVE_TAB = re.compile(r"^(.+?) - Tasks - (.+?) - Productive\b")
TASK_NUMBER = re.compile(r"\bproductive\s+task\s*#?\s*(\d{5,10})\b", re.I)


def task_links(t0: float, projects_dir: str = PROJECTS_DIR) -> list:
    """Every session used since the day began that has a Productive task link the person typed or pasted:
    [{titles, links: [(time, task id), ...]}]. Only typed messages count, never what a tool returned."""
    found = []
    for path in glob.glob(os.path.join(projects_dir, "*", "*.jsonl")):
        try:
            if os.path.getmtime(path) < t0 - 3600:
                continue
            titles, links = set(), []
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"ai-title"' in line or '"custom-title"' in line or "productive.io" in line:
                        r = record(line)
                        name = r.get("customTitle") or r.get("aiTitle")
                        if r.get("type") in ("ai-title", "custom-title") and name and isinstance(name, str):
                            titles.add(norm_title(name))
                            continue
                        text = human_prompt(r)
                        if text and r.get("timestamp"):
                            try:
                                t = parse_ts(r["timestamp"])
                            except (ValueError, TypeError, AttributeError):
                                continue  # this line only
                            links += [(t, x) for x in TASK_LINK.findall(text)]
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if links and titles:
            found.append({"titles": {x for x in titles if len(x) >= 5}, "links": sorted(links)})
    return found


ORG_ADDRESS = re.compile(rb"app\.productive\.io/(\d+)-([a-z0-9]+(?:-[a-z0-9]+)*)/")


def productive_address(org_id: str, projects_dir: str = PROJECTS_DIR, days: int = 60) -> str:
    """The organization's address in Productive's web app, "https://app.productive.io/<org id>-<name>", as links in
    the person's own Claude sessions show it (each of them opens a task as <address>/tasks/<id>). Productive's API
    gives the id but not the name part, so it is never made up: '' when no link to this organization has been seen.
    The newest sessions first, read as bytes, stopping at the first file with one: well under a second."""
    if not re.fullmatch(r"\d+", str(org_id or "")):
        return ""
    want, since = str(org_id).encode(), time.time() - days * 86400
    paths = []
    for path in glob.glob(os.path.join(projects_dir, "*", "*.jsonl")):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime >= since:
            paths.append((mtime, path))
    for _, path in sorted(paths, reverse=True):
        names, tail = [], b""
        try:
            with open(path, "rb") as fh:
                while True:  # in pieces: a session file can be hundreds of MB
                    chunk = fh.read(8 << 20)
                    if not chunk:
                        break
                    data, tail = tail + chunk, chunk[-200:]  # a link cut in two is whole in the next piece
                    names += [name for oid, name in ORG_ADDRESS.findall(data) if oid == want]
        except OSError:
            continue
        if names:
            name = max(set(names), key=names.count).decode()
            return f"https://app.productive.io/{org_id}-{name}"
    return ""


LINK_BEFORE = 3 * 3600  # a link typed this long before a row still names its task; older ones were about other work


def attach_tasks(rows: list, t0: float, found: list) -> None:
    """Give each Likely row the Productive tasks linked in its sessions while it ran or in the three hours before,
    the latest first. Older links are left out: a long session moves through many tasks
    (on one person's real days, links from any time picked the logged task for 3 rows of 25; this window, for 6 of 8).
    A row propose() made carries its own (repoLinks): only the links typed in a turn of its repository, so a link to
    one client's task in a session that also worked elsewhere names the task of that work alone."""
    for row in rows:
        if "repoLinks" in row:
            both = list(dict.fromkeys(list(row["repoLinks"]) + list(row.get("seenTasks") or [])))[:4]
            if both:
                row["tasks"] = both  # the dialog's "linked in this session"; Log the day tells the two apart
            continue
        keys = {k for k in (norm_title(t) for t in row.get("titles") or []) if len(k) >= 5}
        a, b = t0 + row["s"] * 60 - LINK_BEFORE, t0 + row["e"] * 60
        links = [x for f in found if any(t.startswith(k) or k.startswith(t) for k in keys for t in f["titles"])
                 for x in f["links"] if a <= x[0] <= b]
        if links:
            row["tasks"] = list(dict.fromkeys(tid for _, tid in sorted(links, reverse=True)))[:4]
