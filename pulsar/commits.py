"""Your commits during a Likely row: what was done, in your own words. The log dialog and Log the day offer them as
the note's draft, and Ask Claude reads them with what you typed.

Which repositories: those the row's Claude sessions worked in during the row (the session's folder and the paths its
tools named) whose name matches the row's project. Which commits: yours, on any branch, from five minutes before the
row to ten minutes after it; no merges, version bumps or work in progress. Yours means author and committer both carry
an email of your own git settings (global or system, `git config --global user.email`), or one that two or more of the
repositories of the day's sessions set as their own user.email: never one that a single repository's .git/config names, which a
repository from elsewhere could set to its own author's.
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
    """A repository is the row's when their names contain one another ("Acme" and acme-web,
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


def _identity(git: str, root: str, run) -> tuple:
    """({emails from your own git settings: global or system scope}, {emails the repository sets itself: local or
    worktree scope, which its own .git/config, or a file it includes, decides})."""
    try:
        done = _run_git(git, root, ["config", "--show-scope", "--get-all", "user.email"], run, 5)
    except (OSError, subprocess.TimeoutExpired):
        return set(), set()
    own, local = set(), set()
    for line in (done.stdout or "").splitlines():
        scope, _, email = line.partition("\t")
        email = email.strip().lower()
        if email and scope in ("global", "system"):
            own.add(email)
        elif email and scope in ("local", "worktree"):
            local.add(email)
    return own, local


def _log(git: str, root: str, since: float, run, trusted=None) -> list:
    """[(author time, subject)] of your commits in a repository since a time, on any branch: author and committer
    both one of the `trusted` emails (by default, those of your own git settings: _identity)."""
    try:
        if trusted is None:
            trusted = _identity(git, root, run)[0]
        if not trusted:
            return []
        when = datetime.fromtimestamp(since, timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")
        done = _run_git(git, root, ["log", "--no-show-signature", "--exclude=refs/stash", "--all", "--no-merges", "-n",
                                "500", f"--since={when}", "--format=%at%x09%ae%x09%ce%x09%s"], run, 10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in (done.stdout or "").splitlines():
        t, author, committer, subject = (line.split("\t", 3) + ["", "", ""])[:4]
        if t.isdigit() and author.lower() in trusted and committer.lower() in trusted and subject.strip():
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
            paths += [(t, p) for t, text in s["prompts"] for p in sessions.find_paths(text)]
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
    each: list = []  # per row: (its time, the repositories it worked in that are its project's)
    for r in rows:
        keys = {k for k in (sessions.norm_title(t) for t in r["titles"]) if len(k) >= 5}
        a, b = t0 + r["s"] * 60, t0 + r["e"] * 60
        roots: set = set()
        for names, cwd, paths in found:
            if any(n.startswith(k) or k.startswith(n) for k in keys for n in names):
                roots |= {x for x in map(repo_root, ([cwd] if cwd else []) + [p for t, p in paths if a - LEAD <= t <= b])
                          if x}
        each.append((a, b, sorted(x for x in roots if matches(r.get("project") or "", x))))
    # whose commits count: your own git settings' emails; and, when a repository sets an email of its own (a person
    # who sets user.email per repository), that email only if another repository of the day's sessions sets it too:
    # one repository alone never decides whose commits are yours
    ids = {root: _identity(git, root, run) for root in sorted({x for _, _, rs in each for x in rs})}
    trusted = {e for own, _ in ids.values() for e in own}
    if any(local - trusted for _, local in ids.values()):
        day = {repo_root(p) for _, cwd, paths in found for p in ([cwd] if cwd else []) + [p for _, p in paths]}
        for root in sorted(x for x in day if x and x not in ids)[:40]:
            ids[root] = _identity(git, root, run)
        counts: dict = {}
        for _, local in ids.values():
            for e in local:
                counts[e] = counts.get(e, 0) + 1
        trusted |= {e for e, n in counts.items() if n >= 2}
    for i, (a, b, roots) in enumerate(each):
        for root in roots:
            if root not in logs:
                logs[root] = _log(git, root, t0 - 86400, run, trusted)
            for t, subject in logs[root]:
                if a - LEAD <= t <= b + TAIL:
                    d = max(a - t, t - b, 0)
                    if (root, t, subject) not in best or d < best[(root, t, subject)][0]:
                        best[(root, t, subject)] = (d, i)
    for i, r in enumerate(rows):
        r["commits"] = clean([(t, subject) for (_, t, subject), (_, j) in best.items() if j == i])
