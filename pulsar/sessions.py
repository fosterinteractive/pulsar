"""The "Likely" column for anyone who works in Claude Code: which session, and so which project, each minute of work
most likely went to.

Read only and local only. Claude Code keeps every session as a JSON-lines file, ~/.claude/projects/<folder>/<id>.jsonl:
what the person typed, what the agent did (its tool calls name the files it touched) and a title.

  * A session's project is the git repository of the files a turn touched (the folder that holds .git, by its name).
    A turn that touched none belongs to its neighbours' project, then to the session's title, then to the folder the
    session ran in.
  * Each minute the activity tracker saw work goes to a session: the one whose title the editor window showed, else
    one whose agent was running, else the one last used in that sitting. Meetings and personal windows go to none.
  * Without a tracker, the time an agent ran on a request, and the answers typed within half an hour, count as work.

Nothing here knows any person, company or client: projects come from the folders on this computer.
"""

from __future__ import annotations

import glob
import json
import os
import re
import time
from datetime import datetime

from .config import Newest

HOME = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")
AWAY = 30  # minutes without a sign of work that end a sitting
CLAUDE_APPS = re.compile(r"^(Cursor|Code|Visual Studio Code|Windsurf|Terminal|iTerm2?|Warp|Ghostty|Claude)$", re.I)
MEETING = re.compile(r"zoom|meet\.google|google meet|microsoft teams|\bteams\b|huddle|webex|meeting", re.I)
NOT_TYPED = ("<task-notification>", "<local-command", "Caveat:", "<command-message>", "[Request interrupted",
             "This session is being continued")
# paths in the home folder, in temporary folders (a clone an agent made there) and on other disks
PATH_RE = re.compile(r"(?:" + re.escape(HOME) + r"|/private/tmp|/private/var/folders|/tmp|/var/folders|/Volumes)"
                     r"/[^\s'\"`<>|;,()\[\]{}]+")
# where the walk up stops: these are never a project themselves
ROOTS = {HOME, "/", "/private", "/private/tmp", "/tmp", "/private/var", "/private/var/folders", "/var", "/var/folders",
         "/Volumes"}
TOOLING = os.path.join(HOME, ".claude")  # Claude Code's own files (memory, skills, plans): tools, not a project
# Logging time: Pulsar itself, or Productive in a browser. It is work, but no client's: it goes to no session.
LOGGING = re.compile(r"^Pulsar$|app\.productive\.io|(127\.0\.0\.1|localhost):56\d\d", re.I)


def parse_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
    return ""


def human_prompt(r: dict) -> str | None:
    """What the person typed, or None for anything Claude Code wrote itself (tool results, notices, summaries)."""
    if r.get("type") == "attachment":
        att = r.get("attachment") or {}
        if att.get("type") == "queued_command" and (att.get("origin") or {}).get("kind") == "human":
            return text_of(att.get("prompt")) or "(message)"
        return None
    if r.get("type") != "user" or r.get("isMeta") or r.get("isCompactSummary") or r.get("toolUseResult") is not None:
        return None
    content = (r.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content):
        return None
    origin = r.get("origin")
    if origin is not None:  # recent Claude Code versions mark typed messages
        return (text_of(content) or "(attachment)") if origin.get("kind") == "human" else None
    text = text_of(content)
    return None if not text or text.lstrip().startswith(NOT_TYPED) else text


def notification(r: dict) -> bool:
    """A background task reporting back: the agent starts working again without anyone typing."""
    if r.get("type") == "attachment":
        att = r.get("attachment") or {}
        return att.get("type") == "queued_command" and text_of(att.get("prompt")).lstrip().startswith("<task-notification>")
    if r.get("type") != "user":
        return False
    if (r.get("origin") or {}).get("kind") == "task-notification":
        return True
    return text_of((r.get("message") or {}).get("content")).lstrip().startswith("<task-notification>")


def tool_paths(r: dict) -> list:
    """The paths in the home folder that an agent's tool calls named."""
    found: list = []
    for item in ((r.get("message") or {}).get("content") or []):
        if isinstance(item, dict) and item.get("type") == "tool_use":
            stack = [item.get("input")]
            while stack:
                v = stack.pop()
                if isinstance(v, str):
                    found += PATH_RE.findall(v[:4000])
                elif isinstance(v, dict):
                    stack.extend(v.values())
                elif isinstance(v, list):
                    stack.extend(v)
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
    s = (s or "").split(" — ")[0]
    return re.sub(r"(…|\.\.\.)\s*$", "", s).strip().lower()


def read_session(path: str, t0: float, t1: float) -> dict | None:
    """One session's messages, agent activity and titles around [t0, t1); None if nobody typed in it that day."""
    prompts, triggers, assistant, titles, cwd, title, custom = [], [], [], set(), None, "", False
    lo, hi = t0 - 6 * 3600, t1 + 3600
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"ai-title"' in line or '"custom-title"' in line:
                try:
                    r = json.loads(line)
                except ValueError:
                    r = {}
                name = r.get("customTitle") or r.get("aiTitle")
                if r.get("type") in ("ai-title", "custom-title") and name:
                    titles.add(norm_title(name))
                    if r.get("type") == "custom-title" or not custom:  # a title the person set wins
                        title, custom = name, custom or r.get("type") == "custom-title"
                    continue
            if '"timestamp"' not in line:
                continue
            try:
                r = json.loads(line)
                t = parse_ts(r["timestamp"])
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if not lo <= t < hi or r.get("isSidechain"):
                continue
            cwd = r.get("cwd") or cwd
            text = human_prompt(r)
            if text is not None:
                prompts.append((t, text.strip()))
                triggers.append(t)
            elif notification(r):
                triggers.append(t)
            elif r.get("type") == "assistant":
                assistant.append((t, tool_paths(r)))
    if not any(t0 <= t < t1 for t, _ in prompts):
        return None
    prompts.sort()
    dedup: list = []
    for t, text in prompts:  # a message sent while the agent works can be recorded twice
        if dedup and dedup[-1][1][:80] == text[:80] and t - dedup[-1][0] < 600:
            continue
        dedup.append((t, text))
    assistant.sort(key=lambda x: x[0])
    triggers.sort()
    return {"id": os.path.basename(path)[:8], "cwd": cwd, "title": title, "titles": {x for x in titles if len(x) >= 5},
            "prompts": dedup, "triggers": triggers, "assistant": assistant}


def analyse(s: dict) -> None:
    """The project of every turn, and the spans the agent was running."""
    prompts, assistant = s["prompts"], s["assistant"]
    folder = (project_of_path(s["cwd"]) or os.path.basename(s["cwd"].rstrip("/"))) if s["cwd"] else None
    turns = []
    for i, (p, text) in enumerate(prompts):
        nxt = prompts[i + 1][0] if i + 1 < len(prompts) else float("inf")
        counts: dict = {}
        for path in PATH_RE.findall(text) + [x for t, paths in assistant if p <= t < nxt for x in paths]:
            name = project_of_path(path)
            if name:
                counts[name] = counts.get(name, 0) + 1
        turns.append({"p": p, "project": max(counts, key=counts.get) if counts else None})
    named = (s.get("title") or "").strip()[:40] or None
    for i, t in enumerate(turns):
        if t["project"] is None:
            before = next((turns[j]["project"] for j in range(i - 1, -1, -1) if turns[j]["project"]), None)
            after = next((turns[j]["project"] for j in range(i + 1, len(turns)) if turns[j]["project"]), None)
            t["project"] = before or after or named or folder or "Claude Code"  # the last: nothing named it
    s["turns"] = turns
    running, trig = [], s["triggers"]
    for k, tk in enumerate(trig):
        nxt = trig[k + 1] if k + 1 < len(trig) else float("inf")
        ends = [t for t, _ in assistant if tk <= t < nxt]
        if ends and ends[-1] > tk:
            running.append((tk, ends[-1]))
    s["running"] = running


def project_at(s: dict, t: float) -> str:
    turn = s["turns"][0]
    for x in s["turns"]:
        if x["p"] > t:
            break
        turn = x
    return turn["project"]


def propose(t0: float, t1: float, items: list, tracker: str, projects_dir: str = PROJECTS_DIR) -> dict:
    """The Likely column for the day [t0, t1): blocks of minutes, each with its project, the sessions' titles, how many
    messages were typed in it and the first one. `items` are the day's activity (minutes from t0, as trackers.py makes
    them); without any, the agents' run time stands in for the tracker."""
    sessions = []
    for path in glob.glob(os.path.join(projects_dir, "*", "*.jsonl")):
        try:
            if os.path.getmtime(path) < t0 - 3600:
                continue
            s = read_session(path, t0, t1)
        except OSError:
            continue
        if s:
            analyse(s)
            sessions.append(s)
    n = max(1, int(round((t1 - t0) / 60)))
    m = lambda t: int((t - t0) // 60)  # noqa: E731 - a moment as the day's minute

    focus: list = [None] * n
    logging_s, work_s = [0.0] * n, [0.0] * n  # seconds of each minute in Pulsar or Productive, and in any work window
    for it in items:
        if it.get("cat") != "work":
            continue  # personal windows (Settings > Personal windows) and away time go to no session
        is_logging = any(LOGGING.search(str(it.get(k) or "")) for k in ("app", "host", "url"))
        for x in range(max(0, int(it["s"])), min(n, int(-(-it["e"] // 1)))):
            seconds = (min(it["e"], x + 1) - max(it["s"], x)) * 60
            work_s[x] += seconds
            if is_logging:
                logging_s[x] += seconds
            else:
                focus[x] = it
    # a minute that went mostly to logging is logging, even when another window shared it (a look at Pulsar between
    # two edits): it goes to no session
    logging_at = [logging_s[x] > 0 and 2 * logging_s[x] >= work_s[x] for x in range(n)]
    for x in range(n):
        if logging_at[x]:
            focus[x] = None
    present = [f is not None for f in focus]
    run_at: list = [[] for _ in range(n)]
    acts = []  # (minute, session) of every message and every end of an agent's run
    for i, s in enumerate(sessions):
        for a, b in s["running"]:
            for x in range(max(0, m(a)), min(n, m(b) + 1)):
                run_at[x].append(i)
            acts.append((m(b), i))
        acts += [(m(p), i) for p, _ in s["prompts"]]
    acts.sort()
    if not any(present):  # no tracker: an agent working on a request, and answers typed within half an hour
        tracker = "no tracker"
        for s in sessions:
            for a, b in s["running"]:
                for x in range(max(0, m(a)), min(n, m(b) + 1)):
                    present[x] = True
            for p, _ in s["prompts"]:
                end = max((b for a, b in s["running"] if b <= p), default=None)
                if end is not None and p - end <= AWAY * 60:
                    for x in range(max(0, m(end)), min(n, m(p) + 1)):
                        present[x] = True

    sitting: list = [None] * n  # (first, last) minute of the sitting a minute belongs to
    marks = [x for x in range(n) if present[x]]
    if marks:
        start = prev = marks[0]
        for x in marks[1:] + [None]:
            if x is None or x - prev > AWAY:
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
    for x in range(n):
        if not present[x]:
            continue
        f, cands = focus[x], []
        if f is None or CLAUDE_APPS.search(f.get("app") or ""):
            key = norm_title(f["title"]) if f else ""
            if key and key not in by_title:
                by_title[key] = [i for i, s in enumerate(sessions)
                                 if len(key) >= 5 and any(t.startswith(key) or key.startswith(t) for t in s["titles"])]
            cands = (by_title.get(key) if key else None) or run_at[x] or [i for i in [latest(x)] if i is not None]
        elif not MEETING.search(" ".join((f.get("app") or "", f.get("title") or "", f.get("host") or ""))):
            page = " ".join((f.get("title") or "", f.get("url") or "")).lower()
            cands = [i for i, ps in enumerate(projects_of) if any(len(p) >= 4 and p.lower() in page for p in ps)] \
                or [i for i in [nearest(x)] if i is not None]
        if len(cands) > 1:
            pick = latest(x, set(cands))
            cands = [pick if pick is not None else cands[0]]
        if cands:
            owner[x] = (cands[0], project_at(sessions[cands[0]], t0 + x * 60 + 59))

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
    rows = []
    for e in entries:
        typed = sorted((t, text) for i in e["_sessions"] for t, text in sessions[i]["prompts"]
                       if t0 + e["s"] * 60 <= t < t0 + e["e"] * 60 and project_at(sessions[i], t) == e["project"])
        rows.append({"s": e["s"], "e": e["e"], "minutes": e["_minutes"], "project": e["project"], "kind": "focused",
                     "titles": sorted({sessions[i]["title"] for i in e["_sessions"] if sessions[i]["title"]}),
                     "messages": len(typed), "first_message": (typed[0][1] if typed else "")[:200],
                     "sessions": sorted(sessions[i]["id"] for i in e["_sessions"])})
    return {"tracker": tracker, "working": sum(present), "proposed": sum(r["minutes"] for r in rows),
            "unplaced": [], "logging": sum(1 for x in range(n) if logging_at[x] and owner[x] is None), "entries": rows}


# ---- Productive tasks named in a session ------------------------------------------------------------------

TASK_LINK = re.compile(r"app\.productive\.io/[^\s/]+/(?:projects/\d+/)?tasks/(?:task/)?(\d+)")


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
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        name = r.get("customTitle") or r.get("aiTitle")
                        if r.get("type") in ("ai-title", "custom-title") and name:
                            titles.add(norm_title(name))
                            continue
                        text = human_prompt(r)
                        if text and r.get("timestamp"):
                            t = parse_ts(r["timestamp"])
                            links += [(t, x) for x in TASK_LINK.findall(text)]
        except (OSError, ValueError, TypeError):
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
    (on one person's real days, links from any time picked the logged task for 3 rows of 25; this window, for 6 of 8)."""
    for row in rows:
        keys = {k for k in (norm_title(t) for t in row.get("titles") or []) if len(k) >= 5}
        a, b = t0 + row["s"] * 60 - LINK_BEFORE, t0 + row["e"] * 60
        links = [x for f in found if any(t.startswith(k) or k.startswith(t) for k in keys for t in f["titles"])
                 for x in f["links"] if a <= x[0] <= b]
        if links:
            row["tasks"] = list(dict.fromkeys(tid for _, tid in sorted(links, reverse=True)))[:4]
