"""One switch for Claude Code: Settings > Claude > "Use Claude Code with Pulsar".

On, it does three things, so nothing else needs setting up:
1. Ask Claude: the button in the Likely column, and a note drafted from the session for the log dialog.
2. Pulsar in Claude Code: `claude mcp add --scope user pulsar -- python3 pulsar.py --mcp` (mcp.py), so any session
   can read the day from Pulsar and log time through it. Claude Code asks the person before each write (one write,
   one pulsar_log call, holds up to 40 entries).
3. "ask" rules in ~/.claude/settings.json: Claude Code asks before a session changes Productive itself (the create,
   update, delete and action tools of the Productive server named "productive"; a server under another name, such
   as a claude.ai connector, is not covered), and before pulsar_log, as a fallback to the mark mcp.py puts on it.
Off, it takes away what it added, and only that: rules the person had already are never removed. A server named
"pulsar" that Pulsar did not add (another command, or its own env) is left as it is.

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
LOG_RULES = ("mcp__pulsar__pulsar_log", "mcp__pulsar-preview__pulsar_log")  # both Pulsars: one set, as below
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
    return [f"mcp__{server}__{t}" for t in WRITE_TOOLS] + list(LOG_RULES)


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


def _servers() -> dict | None:
    """Claude Code's user-scope servers, read from its file (no command runs): {} when there are none, None when the
    file cannot be read, so "not there" is never said when it is not known."""
    try:
        with open(CLAUDE_JSON, encoding="utf-8") as fh:
            servers = (json.load(fh) or {}).get("mcpServers") or {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, AttributeError):
        return None
    return servers if isinstance(servers, dict) else None


def registered(server: str = "") -> dict | None:
    """Pulsar's entry (or `server`'s) among Claude Code's user-scope servers, or None."""
    got = (_servers() or {}).get(server or name())
    return got if isinstance(got, dict) else None


def _ours(entry: dict) -> bool:
    """An entry Pulsar added (this copy or an older one, any path or port): pulsar.py --mcp, and no env of its own.
    Anything else under Pulsar's name the person made, and Pulsar leaves it alone."""
    args = [str(a) for a in entry.get("args") or []]
    return "--mcp" in args and any(a.endswith("pulsar.py") for a in args) and not entry.get("env")


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
    mode = os.stat(path).st_mode & 0o777 if os.path.exists(path) else None
    if mode is not None and not os.path.exists(backup):
        shutil.copy2(path, backup)
    config.write_atomic(path, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    if mode is not None:  # the file keeps its own permissions (write_atomic's temporary file is owner-only)
        os.chmod(path, mode)


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
    mine = registered()
    if mine is not None and not _ours(mine):  # the person's own entry under Pulsar's name: not Pulsar's to replace
        added, why = add_rules()
        return {"ok": False, "rulesAdded": sorted(set(settings.get("claude_rules_added") or []) | set(added)),
                "message": f"Claude Code already has a server named “{name()}” that Pulsar did not add, so Pulsar left "
                           f"it as it is. To use Pulsar's own, remove that one (claude mcp remove {name()} -s user) "
                           "and switch this on again." + (f" {why}" if why else "")}
    if mine is not None:
        _run(["mcp", "remove", name(), "-s", "user"])  # Pulsar's older entry (another path or port) goes first
    ok, out = _run(["mcp", "add", "--scope", "user", name(), "--", *command(port)])
    added, why = add_rules()
    kept = sorted(set(settings.get("claude_rules_added") or []) | set(added))
    if not ok:
        return {"ok": False, "message": f"Pulsar could not add itself to Claude Code: {out or 'no answer'}",
                "rulesAdded": kept}
    msg = "Pulsar is in Claude Code: a new session can read your day and log time, and asks you before each write."
    if why:
        msg += f" {why}"
    return {"ok": True, "message": msg, "rulesAdded": kept}


def disconnect(settings: dict) -> dict:
    """Remove Pulsar from Claude Code and the rules Pulsar added. {"ok", "message", "rulesAdded": []}."""
    if _test_run():
        return {"ok": True, "message": "A read-only run: Claude Code was not changed.",
                "rulesAdded": settings.get("claude_rules_added") or []}
    problems = []
    servers = _servers()
    mine = (servers or {}).get(name())
    if servers is None or (isinstance(mine, dict) and _ours(mine)):  # not known: Claude Code's command knows
        ok, out = _run(["mcp", "remove", name(), "-s", "user"])
        if not ok:
            problems.append(f"Pulsar could not remove itself from Claude Code: {out or 'no answer'}")
    added = settings.get("claude_rules_added") or []
    if added and servers is None:  # whether the other Pulsar still uses the rules is not known: they stay
        return {"ok": False, "rulesAdded": added,
                "message": " ".join(problems) or "Claude Code's ~/.claude.json could not be read, so Pulsar asked Claude "
                                                 "Code to remove it and kept its ask rules (Pulsar Preview may use "
                                                 "them). Switch this on and off again once Claude Code opens normally."}
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
