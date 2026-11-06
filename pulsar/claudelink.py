"""One switch for Claude Code: Settings > Claude Code > "Use Claude Code with Pulsar".

On, it does three things, so nothing else needs setting up:
1. Ask Claude: the button in the Likely column, and a note drafted from the session for the log dialog.
2. Pulsar in Claude Code: `claude mcp add --scope user pulsar -- python3 pulsar.py --mcp` (mcp.py), so any session
   can read the day from Pulsar and log time through it. Claude Code asks the person before every entry.
3. Claude Code asks before a session changes Productive itself: "ask" rules for the Productive connection's
   create, update, delete and action tools, in ~/.claude/settings.json.
Off, it takes away what it added, and only that: rules the person had already are never removed.

Claude Code's own command adds and removes the connection (its settings file is Claude Code's to write); the rules are
added to ~/.claude/settings.json by a read, a merge and an atomic rename, after a one-time backup beside it. A
settings file that is not valid JSON is left alone, and the switch says why. Pulsar Preview adds itself as
"pulsar-preview", on its own port and with its own folders (--instance preview), so the two never share a connection.
The ask rules are one set for both: switching one off leaves them while the other is still in Claude Code.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

from . import config

CLAUDE_DIR = os.path.join(os.path.expanduser("~"), ".claude")
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
CLAUDE_JSON = os.path.join(os.path.expanduser("~"), ".claude.json")  # where Claude Code keeps user-scope servers
INSTALL = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Pulsar")
WRITE_TOOLS = ("create_resource", "update_resource", "delete_resource", "perform_action")
TIMEOUT = 30


def name() -> str:
    return "pulsar-preview" if config.is_preview() else "pulsar"


def command(port: int) -> list:
    """How Claude Code starts Pulsar's MCP server. An installed Pulsar is started through `current`, so the
    connection keeps working across updates; a copy run from source (Preview, development) from where it is."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.realpath(here).startswith(os.path.realpath(os.path.join(INSTALL, "versions")) + os.sep):
        here = os.path.join(INSTALL, "current")
    python = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
    own = ["--instance", config.INSTANCE] if config.INSTANCE else []  # Preview's MCP server reads Preview's key
    return [python, "-E", "-s", "-B", os.path.join(here, "pulsar.py"), "--mcp", "--port", str(int(port)), *own]


def rules(server: str = "productive") -> list:
    return [f"mcp__{server}__{t}" for t in WRITE_TOOLS]


def _claude():
    from .productive import _claude_binary
    return _claude_binary()


def _run(args: list) -> tuple:
    """(ok, output) of a `claude` command."""
    binary = _claude()
    if not binary:
        return False, "Claude Code was not found on this Mac."
    try:
        done = subprocess.run([binary, *args], capture_output=True, text=True, timeout=TIMEOUT, cwd=os.path.expanduser("~"),
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, "Claude Code did not answer in time."
    except OSError as exc:
        return False, f"Claude Code could not be started ({exc})."
    return done.returncode == 0, (done.stdout or done.stderr or "").strip()[-300:]


def registered(server: str = "") -> dict | None:
    """Pulsar's entry (or `server`'s) among Claude Code's user-scope servers, read from its file (no command runs),
    or None."""
    try:
        with open(CLAUDE_JSON, encoding="utf-8") as fh:
            servers = (json.load(fh) or {}).get("mcpServers") or {}
    except (OSError, ValueError, AttributeError):
        return None
    got = servers.get(server or name()) if isinstance(servers, dict) else None
    return got if isinstance(got, dict) else None


def other() -> str:
    """The other Pulsar's name in Claude Code: Pulsar for Preview, Preview for Pulsar."""
    return "pulsar" if name() == "pulsar-preview" else "pulsar-preview"


def _read_settings() -> tuple:
    """(settings, None) or (None, why it cannot be changed). A missing file is empty settings."""
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        return None, f"Claude Code's settings could not be read ({exc})."
    try:
        doc = json.loads(text) if text.strip() else {}
    except ValueError:
        return None, "Claude Code's settings file (~/.claude/settings.json) is not valid JSON, so Pulsar left it alone."
    if not isinstance(doc, dict) or not isinstance(doc.get("permissions", {}), dict) \
            or not isinstance((doc.get("permissions") or {}).get("ask", []), list):
        return None, "Claude Code's settings file has an unexpected shape, so Pulsar left it alone."
    return doc, None


def _write_settings(doc: dict) -> None:
    path = os.path.realpath(SETTINGS)  # a settings file linked from elsewhere (a dotfiles folder) stays linked
    backup = path + ".pulsar-backup"
    if os.path.exists(path) and not os.path.exists(backup):
        shutil.copy2(path, backup)
    config.write_atomic(path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def add_rules() -> tuple:
    """(the rules Pulsar added, None) or ([], why not). Rules already there are left as they are and not counted."""
    doc, why = _read_settings()
    if doc is None:
        return [], why
    ask = doc.setdefault("permissions", {}).setdefault("ask", [])
    added = [r for r in rules() if r not in ask]
    if added:
        ask.extend(added)
        try:
            _write_settings(doc)
        except OSError as exc:
            return [], f"Claude Code's settings could not be saved ({exc})."
    return added, None


def remove_rules(added: list) -> str | None:
    """Take out the rules Pulsar added (the ones in `added`), nothing else. None, or why it could not."""
    if not added:
        return None
    doc, why = _read_settings()
    if doc is None:
        return why
    perms = doc.get("permissions") or {}
    ask = perms.get("ask") or []
    left = [r for r in ask if r not in added]
    if left == ask:
        return None
    perms["ask"] = left
    if not left:
        perms.pop("ask")
    if not perms:
        doc.pop("permissions", None)
    try:
        _write_settings(doc)
    except OSError as exc:
        return f"Claude Code's settings could not be saved ({exc})."
    return None


def _test_run() -> bool:
    """A read-only run (tests, a test server) never changes Claude Code's settings."""
    return config.read_only()


def connect(port: int, settings: dict) -> dict:
    """Add Pulsar to Claude Code and the ask rules. {"ok", "message", "rulesAdded"}; the caller saves rulesAdded."""
    if _test_run():
        return {"ok": True, "message": "A read-only run: Claude Code was not changed.",
                "rulesAdded": settings.get("claude_rules_added") or []}
    if not _claude():
        return {"ok": False, "message": "Claude Code was not found on this Mac.", "rulesAdded": []}
    _run(["mcp", "remove", name(), "-s", "user"])  # an older entry (another path or port) goes first
    ok, out = _run(["mcp", "add", "--scope", "user", name(), "--", *command(port)])
    added, why = add_rules()
    kept = sorted(set(settings.get("claude_rules_added") or []) | set(added))
    if not ok:
        return {"ok": False, "message": f"Pulsar could not add itself to Claude Code: {out or 'no answer'}",
                "rulesAdded": kept}
    msg = "Pulsar is in Claude Code: a new session can read your day and log time, and asks you before each entry."
    if why:
        msg += f" {why}"
    return {"ok": True, "message": msg, "rulesAdded": kept}


def disconnect(settings: dict) -> dict:
    """Remove Pulsar from Claude Code and the rules Pulsar added. {"ok", "message", "rulesAdded": []}."""
    if _test_run():
        return {"ok": True, "message": "A read-only run: Claude Code was not changed.",
                "rulesAdded": settings.get("claude_rules_added") or []}
    problems = []
    if registered() is not None:
        ok, out = _run(["mcp", "remove", name(), "-s", "user"])
        if not ok:
            problems.append(f"Pulsar could not remove itself from Claude Code: {out or 'no answer'}")
    added = settings.get("claude_rules_added") or []
    if added and registered(other()) is not None:
        # The other Pulsar is still in Claude Code and counts on the same rules: they stay, and this copy keeps
        # them on its list, so it takes them back when it is switched off again once the other has gone.
        name_ = "Pulsar Preview" if other() == "pulsar-preview" else "Pulsar"
        return {"ok": not problems, "rulesAdded": added,
                "message": " ".join(problems) or f"Pulsar is no longer in Claude Code. Its ask rules stay: {name_} "
                                                 "still uses them."}
    why = remove_rules(added)
    if why:
        problems.append(why)
    return {"ok": not problems, "message": " ".join(problems) or "Pulsar is no longer in Claude Code.",
            "rulesAdded": added if why else []}


def status(port: int) -> dict:
    """For Settings: is Pulsar in Claude Code, started the way this copy would start it, and are the rules there."""
    got = registered()
    want = command(port)
    current = bool(got) and [got.get("command"), *(got.get("args") or [])] == want
    doc, _ = _read_settings()
    ask = ((doc or {}).get("permissions") or {}).get("ask") or []
    return {"claude": bool(_claude()), "name": name(), "registered": bool(got), "current": current,
            "rules": all(r in ask for r in rules())}
