"""The private folder every replay tool reads from and writes to: PULSAR_REPLAY_DIR, required.

What the tools keep there is the person's own: Productive entries with their notes, window titles, calendar events
and what was typed in Claude Code sessions (data/worktext.json). So there is no default: a folder inside a git
checkout could end up in a commit of this public repository. Imported first by each tool, before anything is read.
"""
import os
import sys


def folder() -> str:
    """The folder named by PULSAR_REPLAY_DIR, made owner-only; exits with a sentence when it is unset or in a git
    checkout (this one or any other)."""
    raw = os.environ.get("PULSAR_REPLAY_DIR") or ""
    if not raw:
        sys.exit("Set PULSAR_REPLAY_DIR to a private folder outside any git checkout: the replay keeps real entries, "
                 "window titles, meetings and typed text there.")
    path = os.path.realpath(os.path.expanduser(raw))
    probe = path
    while True:
        if os.path.exists(os.path.join(probe, ".git")):
            sys.exit(f"PULSAR_REPLAY_DIR ({path}) is inside the git checkout {probe}: the replay's private data could be "
                     "committed. Use a folder outside any checkout.")
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    os.makedirs(os.path.join(path, "data"), mode=0o700, exist_ok=True)
    return path
