"""Your commits during a Likely row: what was done, in your own words. The log dialog and Log the day offer them as
the note's draft, and Ask Claude reads them with what you typed.

Which repositories: those the row's Claude sessions worked in during the row (the session's folder and the paths its
tools named) whose name matches the row's project. Which commits: yours (the repository's user.email), on any branch,
from five minutes before the row to ten minutes after it; no merges, version bumps or work in progress.
Read only: `git config` and `git log`, nothing else, both through `_run_git`, which keeps a repository's own settings from
running anything (a signature check's program, a hook, fsmonitor, a fetch). No git command runs when git is missing,
or when it is only Apple's stand-in that would ask to install the developer tools.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

from . import sessions
from .config import Newest

LEAD = 300  # seconds before a row a commit still counts
TAIL = 600  # and after it: the commit that ends the work
MOST = 10  # subjects per row
NOTE_MOST = 3  # subjects in a drafted note
NOTE_SHORT = 160  # characters a drafted note stops at, when a second or third subject would pass it
SKIP = re.compile(r"^(?:merge\b|revert \"merge|(?:fixup|squash|amend)!|wip\b|(?:[\w.-]+ )?v?\d+(?:\.\d+)+$"
                  r"|changelog\b|bump\b)", re.I)
_root_of = Newest(5000)  # folder -> the repository it is in (None outside one)
_read = Newest(400)  # (session file, its size and time, the day) -> what attach needs of it
_git: dict = {}


def git_binary():
    """The git to run, or None. /usr/bin/git without the developer tools opens Apple's install dialog: not run."""
    if "path" not in _git:
        g = shutil.which("git") or ("/usr/bin/git" if os.path.exists("/usr/bin/git") else None)
        if g and os.path.realpath(g) == "/usr/bin/git":
            try:
                ok = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, timeout=5).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            g = g if ok else None
        _git["path"] = g
    return _git["path"]


def repo_root(path: str):
    """The folder holding .git that a path is in, or None (Claude Code's own files are never a repository)."""
    d = (path or "").rstrip("/")
    if d == sessions.TOOLING or d.startswith(sessions.TOOLING + "/"):
        return None
    seen, root = [], None
    while d and d not in sessions.ROOTS:
        if d in _root_of:
            root = _root_of[d]
            break
        seen.append(d)
        if os.path.exists(os.path.join(d, ".git")):
            root = d
            break
        d = os.path.dirname(d)
    for x in seen:
        _root_of[x] = root
    return root


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def matches(project: str, root: str) -> bool:
    """A repository is the row's when their names contain one another ("Inovia" and inovia-web-ast,
    "claude-rules / skills" and claude-rules)."""
    p = _norm((project or "").split(" / ")[0])
    if len(p) < 3:
        return False
    names = {_norm(sessions.repo_name(root)), _norm(os.path.basename(root))}
    return any(len(n) >= 3 and (p in n or n in p) for n in names)


# Any folder with a .git in it can be read here, a client's unpacked archive too, and its .git/config is not the
# person's: settings in it that make git start a program are overridden on the command line, which wins over every
# file. log.showSignature with gpg.program (or gpg.ssh.program) would run that program for each signed commit;
# core.hooksPath and core.fsmonitor name programs too. In the environment: git's own variables from outside are
# dropped (a GIT_DIR would point it elsewhere), no transport is allowed (a partial clone would fetch a missing object
# through a remote, and core.sshCommand, the repository names), nothing asks for a password, and no lock is taken.
SAFE = ("--no-pager", "-c", "log.showSignature=false", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null")


def _run_git(git: str, root: str, args: list, run, timeout: float):
    """One read-only git command in a repository that may not be the person's own (above)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GIT_ALLOW_PROTOCOL="", GIT_NO_LAZY_FETCH="1")
    return run([git, "-C", root, *SAFE, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
               timeout=timeout, env=env)


def _log(git: str, root: str, since: float, run) -> list:
    """[(author time, subject)] of your commits in a repository since a time, on any branch."""
    try:
        email = _run_git(git, root, ["config", "user.email"], run, 5).stdout.strip().lower()
        if not email:
            return []
        when = datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")
        done = _run_git(git, root, ["log", "--no-show-signature", "--exclude=refs/stash", "--all", "--no-merges", "-n",
                                "500", f"--since={when}", "--format=%at%x09%ae%x09%s"], run, 10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in (done.stdout or "").splitlines():
        t, _, rest = line.partition("\t")
        who, _, subject = rest.partition("\t")
        if t.isdigit() and who.lower() == email and subject.strip():
            out.append((int(t), subject.strip()))
    return out


def clean(found: list) -> list:
    """The subjects worth showing, oldest first, each once: no merges or version bumps, and none a note could not
    carry (a path, a web address, code, a tool's name: askclaude.guard_note)."""
    from .askclaude import guard_note
    out, seen = [], set()
    for _, subject in sorted(found):
        text = subject.strip().rstrip(".")
        if not text or SKIP.match(text) or text.lower() in seen:
            continue
        ok, _bad = guard_note(text)
        if ok:
            seen.add(text.lower())
            out.append(ok.rstrip("."))
    return out[:MOST]


def note(subjects: list) -> str:
    """A note from commit subjects: the first few, joined, as short as they allow. Subjects that start the same way
    ("Reports: a; Reports: b") say it once ("Reports: a; b")."""
    out, last = "", None
    for s in subjects[:NOTE_MOST]:
        head, sep, rest = s.partition(": ")
        part = rest if sep and head == last and rest else s
        nxt = f"{out}; {part}" if out else s
        if out and len(nxt) > NOTE_SHORT:
            break
        out, last = nxt, head if sep else None
    return out[:1].upper() + out[1:] if re.match(r"[a-z]+:?(?:\s|$)", out) else out  # a word, not qa:full


def _session(path: str, t0: float):
    """(its names, folder, [(time, path)]) of one session file for the day, kept until the file changes."""
    st = os.stat(path)
    k = (path, st.st_size, st.st_mtime, t0)
    if k not in _read:
        s = sessions.read_session(path, t0, t0 + 86400)
        if s:
            paths = [(t, p) for t, ps in s["assistant"] for p in ps]
            paths += [(t, p) for t, text in s["prompts"] for p in sessions.PATH_RE.findall(text)]
            s = ({n for n in s["titles"] | {sessions.norm_title(s["title"])} if n}, s["cwd"], paths)
        _read[k] = s
    return _read[k]


def attach(rows: list, t0: float, projects_dir: str = sessions.PROJECTS_DIR, run=subprocess.run) -> None:
    """Add "commits" (subjects, oldest first) to each row of at least five minutes. A commit goes to one row: the one
    it was made in, else the nearest. Mutates the rows; a hint only."""
    rows = [r for r in rows if r.get("e", 0) - r.get("s", 0) >= 5 and r.get("titles")]
    git = git_binary()
    if not rows or not git:
        return
    found = []  # the day's sessions: (their names, folder, [(time, path)])
    for path in glob.glob(os.path.join(projects_dir, "*", "*.jsonl")):
        try:
            if os.path.getmtime(path) >= t0 - 3600:
                found.append(_session(path, t0))
        except OSError:
            continue
    found = [x for x in found if x]
    logs: dict = {}
    best: dict = {}  # (repository, time, subject) -> (distance from the row, row index)
    for i, r in enumerate(rows):
        keys = {k for k in (sessions.norm_title(t) for t in r["titles"]) if len(k) >= 5}
        a, b = t0 + r["s"] * 60, t0 + r["e"] * 60
        roots: set = set()
        for names, cwd, paths in found:
            if any(n.startswith(k) or k.startswith(n) for k in keys for n in names):
                roots |= {x for x in map(repo_root, ([cwd] if cwd else []) + [p for t, p in paths if a - LEAD <= t <= b])
                          if x}
        for root in sorted(x for x in roots if matches(r.get("project") or "", x)):
            if root not in logs:
                logs[root] = _log(git, root, t0 - 86400, run)
            for t, subject in logs[root]:
                if a - LEAD <= t <= b + TAIL:
                    d = max(a - t, t - b, 0)
                    if (root, t, subject) not in best or d < best[(root, t, subject)][0]:
                        best[(root, t, subject)] = (d, i)
    for i, r in enumerate(rows):
        r["commits"] = clean([(t, subject) for (_, t, subject), (_, j) in best.items() if j == i])
