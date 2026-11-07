"""One switch for Claude Code: Settings > Claude > "Use Claude Code with Pulsar".

On, it does three things, so nothing else needs setting up:
1. Ask Claude: the button in the Likely column, and a note drafted from the session for the log dialog.
2. Pulsar in Claude Code: `claude mcp add --scope user pulsar -- python3 pulsar.py --mcp` (mcp.py), so any session
   can read the day from Pulsar and log time through it. Claude Code asks the person before each write (one write,
   one pulsar_log call, holds up to 40 entries).
3. Rules in Claude Code's user settings (~/.claude/settings.json, or $CLAUDE_CONFIG_DIR/settings.json): an "ask" rule
   for pulsar_log, a fallback to the mark mcp.py puts on it; and "deny" rules so Claude Code's file tools cannot read
   or change Pulsar's folders (its key, sign-in code, settings and tokens; its installed code, the window's WebKit
   data and its cache; the release key's folder too, where there is one). A Read or Edit deny rule binds Claude Code's
   own file tools and the file commands it recognizes in a shell command (cat, head, sed, a redirection). It does not
   bind a script that opens the file itself (python3 -c, node -e, a script on disk), a command that reaches Pulsar
   without naming its files (pulsar.py --mcp or --signin-url, curl to its port), or any program that is not Claude
   Code; those are bound only by Claude Code's sandbox, which Pulsar leaves to the person. A session in bypass mode
   can also remove the rules from the settings file. They are a speed bump for Claude's own tools, not a wall.
A second setting, off by default, "Ask before any Claude Code session changes Productive", adds "ask" rules for the
create, update, delete and action tools of the Productive server named "productive": every Claude Code session then
asks before each such change, not only Pulsar's, so it is the person's choice. Pulsar 1.2 added them with the switch;
tidy() takes back the ones Pulsar added unless that setting is on.
Off, the switch takes away what it added, and only that: rules the person had already are never removed. A server
named "pulsar" that Pulsar did not add (another command, or its own env) is left as it is.

Claude Code's own command adds and removes the connection (its settings file is Claude Code's to write); the rules are
added to the settings file by a read, a merge and an atomic rename, after a one-time backup beside it. A settings file
that is not valid JSON is left alone, and the switch says why. Pulsar Preview adds itself as "pulsar-preview", on its
own port and with its own folders (--instance preview), so the two never share a connection. The pulsar_log and deny
rules are one set for both: switching one off leaves them while the other is still in Claude Code.

status() also says, read only, when Pulsar's rules or server would not apply: managed settings that allow only their
own permission rules or MCP servers, and which Claude account Claude Code is signed in with (its plan: a consumer plan
may let Anthropic train on the chats, depending on the account's own setting).
"""

from __future__ import annotations

import glob
import json
import os
import plistlib
import shutil
import subprocess
import sys

from . import config

# Claude Code's own folder: CLAUDE_CONFIG_DIR moves its settings and its .claude.json there (and so its sign-in). Set
# only in a shell, Pulsar started from the Dock does not see it: then Pulsar's server and rules land in ~/.claude,
# which that Claude Code does not read (said in docs section 13).
CONFIG_DIR_ENV = os.environ.get("CLAUDE_CONFIG_DIR") or ""
CLAUDE_DIR = CONFIG_DIR_ENV or os.path.join(os.path.expanduser("~"), ".claude")
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
# where Claude Code keeps user-scope servers and the signed-in account
CLAUDE_JSON = os.path.join(CONFIG_DIR_ENV, ".claude.json") if CONFIG_DIR_ENV else \
    os.path.join(os.path.expanduser("~"), ".claude.json")
INSTALL = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Pulsar")
WEBKIT = os.path.join(os.path.expanduser("~"), "Library", "WebKit", "com.fosterinteractive.pulsar")  # the window's data
WRITE_TOOLS = ("create_resource", "update_resource", "delete_resource", "perform_action")
LOG_RULES = ("mcp__pulsar__pulsar_log", "mcp__pulsar-preview__pulsar_log")  # both Pulsars: one set, as below
RELEASE_DIR = os.path.join(os.path.expanduser("~"), ".config", "pulsar-release")  # macos/release.sh: the release key
# Managed settings (an organization's), read only: the file, its drop-in folder, MDM profiles, the server's copy
MANAGED_DIR = "/Library/Application Support/ClaudeCode"
MANAGED_PLISTS = ("/Library/Managed Preferences/com.anthropic.claudecode.plist",
                  "/Library/Managed Preferences/{user}/com.anthropic.claudecode.plist")
DATA_USAGE = "https://code.claude.com/docs/en/data-usage"
SANDBOX_DOCS = "https://code.claude.com/docs/en/sandboxing"
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


def productive_rules(server: str = "productive") -> list:
    """The ask rules of "Ask before any Claude Code session changes Productive" (off by default)."""
    return [f"mcp__{server}__{t}" for t in WRITE_TOOLS]


def rules(settings: dict | None = None) -> list:
    """The ask rules the switch adds: pulsar_log of both Pulsars, and Productive's write tools only when the person
    chose to be asked (claude_ask_productive)."""
    return list(LOG_RULES) + (productive_rules() if (settings or {}).get("claude_ask_productive") else [])


def _rule_path(folder: str) -> str:
    """A folder as a Read or Edit rule writes it: ~/… under the home folder, //… elsewhere (a rule starting with one
    / would be read from the settings file's own folder)."""
    home = os.path.expanduser("~").rstrip("/")
    folder = os.path.abspath(folder)
    return "~" + folder[len(home):] if folder.startswith(home + "/") else "/" + folder


def deny_rules() -> list:
    """Read and Edit deny rules for Pulsar's folders and Pulsar Preview's: settings (the key, the sign-in code,
    tokens), the installed code (an edit there would take out Pulsar's own checks at the next restart), the window's
    WebKit data (its sign-in token) and the cache (copies of days with their notes, Ask Claude's answers, Logs); and
    the release key's folder when this Mac has one (only checked to exist, never read). Edit covers every file tool
    that writes (Write too): a session cannot write a sign-in code there and trade it. They bind Claude Code's own
    tools only, not every program (module docstring)."""
    base = os.path.dirname(config.MAIN_CONFIG_DIR)
    folders = [_rule_path(os.path.join(base, "pulsar")), _rule_path(os.path.join(base, "pulsar-preview")),
               _rule_path(INSTALL) + "*", _rule_path(WEBKIT) + "*",
               _rule_path(os.path.join(os.path.dirname(config.CACHE_DIR), "pulsar")) + "*"]
    if os.path.isdir(RELEASE_DIR):
        folders.append(_rule_path(RELEASE_DIR))
    return [f"{tool}({f}/**)" for f in folders for tool in ("Read", "Edit")]


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
    perms = doc.get("permissions", {}) if isinstance(doc, dict) else None
    if not isinstance(perms, dict) or not all(isinstance((perms or {}).get(k, []), list) for k in ("ask", "deny")):
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


def add_rules(ask: list, deny: list = ()) -> tuple:
    """(the ask rules Pulsar added, the deny rules it added, None) or ([], [], why not). Rules already there are left
    as they are and not counted, so they are never Pulsar's to take back."""
    doc, why = _read_settings()
    if doc is None:
        return [], [], why
    perms = doc.setdefault("permissions", {})
    got = {kind: [r for r in want if r not in (perms.get(kind) or [])] for kind, want in (("ask", ask), ("deny", deny))}
    if got["ask"] or got["deny"]:
        for kind, new in got.items():
            if new:
                perms.setdefault(kind, []).extend(new)
        try:
            _write_settings(doc)
        except OSError as exc:
            return [], [], f"Claude Code's settings could not be saved ({exc})."
    return got["ask"], got["deny"], None


def remove_rules(added: list, denied: list = ()) -> str | None:
    """Take out the rules Pulsar added (the ask rules in `added`, the deny rules in `denied`), nothing else. None, or
    why it could not."""
    if not added and not denied:
        return None
    doc, why = _read_settings()
    if doc is None:
        return why
    perms = doc.get("permissions") or {}
    changed = False
    for kind, mine in (("ask", added), ("deny", denied)):
        have = perms.get(kind) or []
        left = [r for r in have if r not in mine]
        if left != have:
            changed = True
            perms[kind] = left
            if not left:
                perms.pop(kind)
    if not changed:
        return None
    if not perms:
        doc.pop("permissions", None)
    try:
        _write_settings(doc)
    except OSError as exc:
        return f"Claude Code's settings could not be saved ({exc})."
    return None


def tidy(settings: dict) -> dict | None:
    """Take back the Productive ask rules this copy added (Pulsar 1.2 added them with the switch), unless the person
    chose "Ask before any Claude Code session changes Productive". Only the ones its own list says it added: a rule
    the person had is never touched. Run when Pulsar starts and when Settings are saved. None when nothing changed,
    else {"rulesAdded": the list to keep, "message"}."""
    added = [r for r in settings.get("claude_rules_added") or [] if isinstance(r, str)]
    if settings.get("claude_ask_productive") and settings.get("ask_claude"):
        return None
    drop = [r for r in added if r in productive_rules()]
    if not drop or _test_run():
        return None
    why = remove_rules(drop)
    if why:
        return {"rulesAdded": added, "message": why}
    return {"rulesAdded": [r for r in added if r not in drop],
            "message": "Pulsar took back its rules that made every Claude Code session ask before changing Productive."}


def _test_run() -> bool:
    """A read-only run (tests, a test server) never changes Claude Code's settings."""
    return config.read_only()


def _merged(settings: dict, key: str, new: list) -> list:
    return sorted(set(settings.get(key) or []) | set(new))


def connect(port: int, settings: dict) -> dict:
    """Add Pulsar to Claude Code and its rules. {"ok", "message", "rulesAdded", "deniesAdded"}; the caller saves both
    lists (claude_rules_added, claude_denies_added)."""
    if _test_run():
        return {"ok": True, "message": "A read-only run: Claude Code was not changed.",
                "rulesAdded": settings.get("claude_rules_added") or [],
                "deniesAdded": settings.get("claude_denies_added") or []}
    if not _claude():
        return {"ok": False, "message": "Claude Code was not found on this Mac.", "rulesAdded": [], "deniesAdded": []}
    mine = registered()
    if mine is not None and not _ours(mine):  # the person's own entry under Pulsar's name: not Pulsar's to replace
        added, denied, why = add_rules(rules(settings), deny_rules())
        return {"ok": False, "rulesAdded": _merged(settings, "claude_rules_added", added),
                "deniesAdded": _merged(settings, "claude_denies_added", denied),
                "message": f"Claude Code already has a server named “{name()}” that Pulsar did not add, so Pulsar left "
                           f"it as it is. To use Pulsar's own, remove that one (claude mcp remove {name()} -s user) "
                           "and switch this on again." + (f" {why}" if why else "")}
    if mine is not None:
        _run(["mcp", "remove", name(), "-s", "user"])  # Pulsar's older entry (another path or port) goes first
    ok, out = _run(["mcp", "add", "--scope", "user", name(), "--", *command(port)])
    added, denied, why = add_rules(rules(settings), deny_rules())
    kept = _merged(settings, "claude_rules_added", added)
    kept_denies = _merged(settings, "claude_denies_added", denied)
    if not ok:
        return {"ok": False, "message": f"Pulsar could not add itself to Claude Code: {out or 'no answer'}",
                "rulesAdded": kept, "deniesAdded": kept_denies}
    msg = "Pulsar is in Claude Code: a new session can read your day and log time, and asks you before each write."
    if why:
        msg += f" {why}"
    return {"ok": True, "message": msg, "rulesAdded": kept, "deniesAdded": kept_denies}


def disconnect(settings: dict) -> dict:
    """Remove Pulsar from Claude Code and the rules Pulsar added. {"ok", "message", "rulesAdded", "deniesAdded"}."""
    added = settings.get("claude_rules_added") or []
    denied = settings.get("claude_denies_added") or []
    if _test_run():
        return {"ok": True, "message": "A read-only run: Claude Code was not changed.", "rulesAdded": added,
                "deniesAdded": denied}
    problems = []
    servers = _servers()
    mine = (servers or {}).get(name())
    if servers is None or (isinstance(mine, dict) and _ours(mine)):  # not known: Claude Code's command knows
        ok, out = _run(["mcp", "remove", name(), "-s", "user"])
        if not ok:
            problems.append(f"Pulsar could not remove itself from Claude Code: {out or 'no answer'}")
    if (added or denied) and servers is None:  # whether the other Pulsar still uses the rules is not known: they stay
        return {"ok": False, "rulesAdded": added, "deniesAdded": denied,
                "message": " ".join(problems) or "Claude Code's ~/.claude.json could not be read, so Pulsar asked Claude "
                                                 "Code to remove it and kept its rules (Pulsar Preview may use "
                                                 "them). Switch this on and off again once Claude Code opens normally."}
    if (added or denied) and registered(other()) is not None:
        # The other Pulsar is still in Claude Code and counts on the same rules: they stay, and this copy keeps
        # them on its list, so it takes them back when it is switched off again once the other has gone. Productive's
        # are each copy's own choice: they go (tidy).
        name_ = "Pulsar Preview" if other() == "pulsar-preview" else "Pulsar"
        mine_productive = [r for r in added if r in productive_rules()]
        why = remove_rules(mine_productive)
        if why:
            problems.append(why)
        return {"ok": not problems, "rulesAdded": added if why else [r for r in added if r not in mine_productive],
                "deniesAdded": denied,
                "message": " ".join(problems) or f"Pulsar is no longer in Claude Code. Its rules stay: {name_} "
                                                 "still uses them."}
    why = remove_rules(added, denied)
    if why:
        problems.append(why)
    return {"ok": not problems, "message": " ".join(problems) or "Pulsar is no longer in Claude Code.",
            "rulesAdded": added if why else [], "deniesAdded": denied if why else []}


def tidy_saved() -> None:
    """tidy() on the saved settings, when Pulsar starts: a copy updated from 1.2 takes back the Productive rules it
    added. Never stops Pulsar from starting."""
    from . import logbook
    try:
        got = tidy(config.load())
        if got:
            config.update({"claude_rules_added": got["rulesAdded"]})
            logbook.record("claude code", got["message"], kind="warning", once_per=3600)
    except Exception as exc:  # a fault here is said in Logs; Pulsar starts anyway
        logbook.record("claude code", f"Tidying Claude Code's rules failed: {type(exc).__name__}: {exc}", kind="warning")


def _managed() -> dict:
    """What an organization's managed settings say that Pulsar's rules and server depend on, read only:
    {"permissionRulesOnly": the only permission rules are managed ones, so Pulsar's are ignored, "mcpServersOnly":
    only managed MCP servers start, so Pulsar's does not, "where": the file that said so}."""
    docs = []
    for path in [os.path.join(MANAGED_DIR, "managed-settings.json")] + sorted(
            glob.glob(os.path.join(MANAGED_DIR, "managed-settings.d", "*.json"))) + [os.path.join(CLAUDE_DIR,
                                                                                                "remote-settings.json")]:
        try:
            with open(path, encoding="utf-8") as fh:
                docs.append((path, json.load(fh)))
        except (OSError, ValueError):
            continue
    user = os.environ.get("USER") or ""
    for path in MANAGED_PLISTS:
        try:
            with open(path.format(user=user), "rb") as fh:
                docs.append((path.format(user=user), plistlib.load(fh)))
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
    out = {"permissionRulesOnly": False, "mcpServersOnly": False, "where": ""}
    for path, doc in docs:
        if not isinstance(doc, dict):
            continue
        for key, flag in (("permissionRulesOnly", "allowManagedPermissionRulesOnly"),
                          ("mcpServersOnly", "allowManagedMcpServersOnly")):
            if doc.get(flag) is True:
                out[key], out["where"] = True, out["where"] or path
    return out


# Claude Code's names for its plans (organization_type), and which are consumer plans: under the consumer terms the
# chats may be used for training when the account's own setting allows it; Team, Enterprise and API keys are commercial
PLANS = {"claude_pro": ("Pro", True), "claude_max": ("Max", True), "claude_team": ("Team", False),
         "claude_enterprise": ("Enterprise", False)}


def account() -> dict | None:
    """Which Claude account Claude Code is signed in with, as Claude Code keeps it in its .claude.json (read only,
    no keychain): {"email", "plan", "consumer"}; consumer is None when the plan is not known. None when no account
    is there (signed in with a key, or not at all)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return {"email": "", "plan": "an API key", "consumer": False}
    try:
        with open(CLAUDE_JSON, encoding="utf-8") as fh:
            acct = (json.load(fh) or {}).get("oauthAccount")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(acct, dict):
        return None
    kind = str(acct.get("organizationType") or "")
    plan, consumer = PLANS.get(kind, (str(acct.get("planDisplayName") or "") or "a plan Pulsar does not know", None))
    return {"email": str(acct.get("emailAddress") or "")[:120], "plan": plan[:40], "consumer": consumer}


def status(port: int, settings: dict | None = None) -> dict:
    """For Settings: is Pulsar in Claude Code, started the way this copy would start it, are its rules there, and
    whatever keeps them from applying (CLAUDE_CONFIG_DIR, managed settings), with the account Claude Code uses."""
    got = registered()
    want = command(port)
    current = bool(got) and [got.get("command"), *(got.get("args") or [])] == want
    doc, _ = _read_settings()
    perms = (doc or {}).get("permissions") or {}
    ask, deny = perms.get("ask") or [], perms.get("deny") or []
    return {"claude": bool(_claude()), "name": name(), "registered": bool(got), "current": current,
            "rules": all(r in ask for r in rules(settings)) and all(r in deny for r in deny_rules()),
            "askProductive": all(r in ask for r in productive_rules()),
            "configDir": CONFIG_DIR_ENV, "settingsFile": SETTINGS, "managed": _managed(), "account": account(),
            "dataUsage": DATA_USAGE, "sandboxDocs": SANDBOX_DOCS}
