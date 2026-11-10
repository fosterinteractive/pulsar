"""ActivityWatch's own settings that decide what Pulsar can see: its AFK timeout, and whether it opens at login.

ActivityWatch's AFK watcher writes "afk" only after its own timeout, backdated to the last input
(aw-watcher-afk.toml, section [aw-watcher-afk], key `timeout`, in seconds; 180 without the file). A pause shorter than
that timeout is not in its data at all, so no threshold in Pulsar can see it. Settings compares the timeout with
"Away after" (config.away_after):

  * longer: Settings says so and offers Fix. Fix keeps a copy of the file next to it, changes that one line (or adds
    the key in that section) and restarts ActivityWatch, whose watchers read the file only when they start. The new
    timeout applies to new data only;
  * the same or shorter: the file is left alone, and trackers.present_spans counts a pause shorter than "Away after"
    between two stretches of input as work.

ActivityWatch that is not in Login Items records nothing after a restart of the Mac until someone opens it. Settings
asks macOS (System Events) whether it is there and offers to add it.

Settings only ever reads the file by itself. Changing it, restarting ActivityWatch and asking System Events happen on
a click in Settings, through POST routes that need the page's session (server.key_refusal), and never in a read-only
run (PULSAR_READ_ONLY) or a test (blocked; _run and _fetch refuse on their own too).
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime

from . import config

SUPPORT = os.path.expanduser("~/Library/Application Support/activitywatch")
TOML = os.path.join(SUPPORT, "aw-watcher-afk", "aw-watcher-afk.toml")
SECTION = "aw-watcher-afk"   # never [aw-watcher-afk-testing]
DEFAULT_TIMEOUT = 180        # seconds: aw-watcher-afk's own default, without the file or the key
MAX_FILE = 64 * 1024         # a config file this large is not ActivityWatch's: left alone
BUNDLE_ID = "net.activitywatch.ActivityWatch"
APP_NAME = "ActivityWatch"   # the name a login item of it carries
QT = "ActivityWatch.app/Contents/MacOS/aw-qt"              # the app itself: quitting it stops its watchers
ANY = "ActivityWatch.app/Contents/"                        # every process of the app
AFK = "ActivityWatch.app/Contents/MacOS/aw-watcher-afk"    # the watcher the new timeout is for
PLACES = ("/Applications/ActivityWatch.app", "~/Applications/ActivityWatch.app")  # where it usually is
QUIT_WAIT, START_WAIT = 15, 30  # seconds: the longest Pulsar waits for it to quit, then to answer and watch again

AUTOMATION = ("Open System Settings, Privacy & Security, Automation, and let Pulsar control System Events; or add "
              "ActivityWatch yourself: System Settings, General, Login Items, then + under Open at Login.")


class Refused(RuntimeError):
    """A test or a read-only run reached for ActivityWatch or macOS."""


def _refusal() -> str | None:
    """Why nothing may change ActivityWatch or ask macOS now, or None. Read from the environment each time, never from
    something a test can swap: _run and _fetch ask this, not blocked()."""
    if config.tests_offline():
        return "A test run never changes ActivityWatch or asks macOS about it."
    if config.read_only():
        return "Pulsar runs read-only (PULSAR_READ_ONLY): ActivityWatch was not changed."
    return None


def blocked() -> str | None:
    """Why the routes refuse (a test, a read-only run), or None."""
    return _refusal()


def _run(argv: list, timeout: float = 30) -> subprocess.CompletedProcess:
    why = _refusal()
    if why:
        raise Refused(why)
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _fetch(url: str, timeout: float = 2):
    why = _refusal()
    if why:
        raise Refused(why)
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---- the AFK timeout ------------------------------------------------------------------------------------------

HEADER = re.compile(r"^\s*\[(\[?)\s*([^\[\]#]*?)\s*\]\]?\s*(?:#.*)?$")
KEY = re.compile(r"""^(\s*)("timeout"|'timeout'|timeout)(\s*=\s*)([^#\r\n]*?)(\s*(?:#.*)?)$""")
OTHER_KEY = re.compile(r"^\s*[^\s=\[#][^=]*=")  # any key = value line, its key dotted or quoted
NUMBER = re.compile(r"^[+]?(?:\d+(?:_\d+)*)(?:\.\d+)?$")


def _name(raw: str) -> str:
    return raw.strip().strip("\"'").strip()


def scan(text: str) -> dict:
    """Where [aw-watcher-afk] and its `timeout` are in a TOML file, read line by line with the standard library only:
    {"seconds" (the value, or None when the key is not there), "line" (its index), "header" (the section's line, or
    None), "problem"}. Anything this cannot read for sure is a problem, and the file is then never changed: another
    form of the key (a dotted key, an inline table), the section or the key twice, multi-line strings, a value that
    is not a number of seconds."""
    out = {"seconds": None, "line": None, "header": None, "problem": None}
    if '"""' in text or "'''" in text:
        out["problem"] = "it has multi-line strings, which Pulsar does not read"
        return out
    section = ""
    for i, line in enumerate(text.splitlines()):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        h = HEADER.match(line)
        if h:
            section = ("[[" if h.group(1) else "") + _name(h.group(2))
            if section == SECTION:
                if out["header"] is not None:
                    out["problem"] = f"[{SECTION}] is in it twice"
                    return out
                out["header"] = i
            continue
        key = OTHER_KEY.match(line)
        bare = re.sub(r"[\"'\s]", "", line.split("=", 1)[0]) if key else ""
        if bare == SECTION or bare.startswith(SECTION + "."):  # aw-watcher-afk.timeout = …, or an inline table
            out["problem"] = f"it sets {SECTION} in a form Pulsar does not read"
            return out
        if section != SECTION:
            continue
        m = KEY.match(line)
        if not m:
            continue
        if out["line"] is not None:
            out["problem"] = "timeout is in it twice"
            return out
        value = m.group(4).strip()
        if not NUMBER.match(value):
            out["problem"] = f"its timeout is not a number of seconds ({value[:40] or 'empty'})"
            return out
        seconds = float(value.replace("_", ""))
        out["seconds"], out["line"] = (int(seconds) if seconds.is_integer() else seconds), i
    return out


def read_timeout(path: str | None = None) -> dict:
    """ActivityWatch's AFK timeout (in its own file unless `path`): {"seconds", "exists", "problem"}. Without the file,
    or the key in its section, ActivityWatch uses 180 seconds; a file that cannot be read for sure gives seconds None
    and the reason."""
    path = path or TOML
    try:
        if os.path.islink(path):
            return {"seconds": None, "exists": True, "problem": "it is a link, which Pulsar leaves alone"}
        if not os.path.exists(path):
            return {"seconds": DEFAULT_TIMEOUT, "exists": False, "problem": None}
        if os.path.getsize(path) > MAX_FILE:
            return {"seconds": None, "exists": True, "problem": "it is too large to be ActivityWatch's"}
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return {"seconds": None, "exists": True, "problem": f"it could not be read ({type(exc).__name__})"}
    found = scan(text)
    if found["problem"]:
        return {"seconds": None, "exists": True, "problem": found["problem"]}
    return {"seconds": DEFAULT_TIMEOUT if found["seconds"] is None else found["seconds"], "exists": True, "problem": None}


def longer(seconds, minutes: int) -> bool:
    """Whether ActivityWatch's timeout hides pauses "Away after" should see."""
    return isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > minutes * 60


def differs(seconds, minutes: int) -> bool:
    """Whether ActivityWatch's timeout is not "Away after": one away time everywhere (Nick, 2026-11-10), so Settings
    offers Fix and Save sets it. Unreadable: never touched."""
    return isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds != minutes * 60


def _backup_path(path: str, now: float) -> str:
    stamp = datetime.fromtimestamp(now).strftime("%Y-%m-%d-%H%M%S")
    base = f"{path}.pulsar-{stamp}.bak"
    out, n = base, 2
    while os.path.lexists(out):
        out, n = f"{path}.pulsar-{stamp}-{n}.bak", n + 1
    return out


def set_timeout(path: str, seconds: int, now: float | None = None) -> dict:
    """Changes `timeout` in [aw-watcher-afk] of `path` to `seconds`, and nothing else: that one line, or the key added
    under the section's header, or the section added at the end. A copy of the file as it was goes next to it first
    (<file>.pulsar-<date-time>.bak, never over another file). Written through a temporary file and a rename, with
    the file's own permissions; read back, and put back as it was if it does not say the new value.
    {"backup", "before", "after"}; ValueError when the file cannot be changed safely."""
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 60 <= seconds <= 7200:
        raise ValueError("The timeout is a whole number of seconds, from 60 to 7200.")
    if os.path.islink(path) or not os.path.isfile(path):
        raise ValueError(f"{path} is not a file Pulsar can change.")
    if os.path.getsize(path) > MAX_FILE:
        raise ValueError(f"{path} is too large to be ActivityWatch's.")
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{path} is not text Pulsar can read.")
    found = scan(text)
    if found["problem"]:
        raise ValueError(f"Pulsar did not change {os.path.basename(path)}: {found['problem']}.")
    lines = text.splitlines(keepends=True)
    eol = "\r\n" if "\r\n" in text else "\n"
    if found["line"] is not None:  # the one line, its indent and comment kept
        line = lines[found["line"]]
        body, end = line.rstrip("\r\n"), line[len(line.rstrip("\r\n")):]
        m = KEY.match(body)
        lines[found["line"]] = f"{m.group(1)}{m.group(2)}{m.group(3)}{seconds}{m.group(5)}{end}"
    elif found["header"] is not None:  # the key, right under the section's header
        h = found["header"]
        if not lines[h].endswith(("\n", "\r")):
            lines[h] += eol
        lines.insert(h + 1, f"timeout = {seconds}{eol}")
    else:  # the section, at the end
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += eol
        lines.append(f"{eol if text.strip() else ''}[{SECTION}]{eol}timeout = {seconds}{eol}")
    new = "".join(lines)
    before = DEFAULT_TIMEOUT if found["seconds"] is None else found["seconds"]
    mode = os.stat(path).st_mode & 0o777
    backup = _backup_path(path, time.time() if now is None else now)
    with open(backup, "xb") as fh:  # x: never over a file already there
        fh.write(raw)
    os.chmod(backup, mode)
    folder = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".pulsar-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    if read_timeout(path)["seconds"] != seconds:  # never leave a file that says something else
        with open(path, "wb") as fh:
            fh.write(raw)
        raise ValueError(f"{os.path.basename(path)} did not read back as {seconds} seconds, so it was put back as it was.")
    return {"backup": backup, "before": before, "after": seconds}


# ---- the app: where it is, restarting it ------------------------------------------------------------------

def _is_aw(app: str) -> bool:
    try:
        with open(os.path.join(app, "Contents", "Info.plist"), "rb") as fh:
            return plistlib.load(fh).get("CFBundleIdentifier") == BUNDLE_ID
    except (OSError, ValueError, plistlib.InvalidFileException, AttributeError):
        return False


def app_path(run=None) -> str | None:
    """Where ActivityWatch.app is: the copy that runs, else /Applications or ~/Applications, else where Spotlight
    finds its bundle id. Only a bundle whose Info.plist says ActivityWatch counts."""
    run = run or _run
    found = []
    try:
        ps = run(["ps", "-axo", "command="], timeout=10)
        found += [m.group(1) for m in re.finditer(r"^\s*(/.*?/ActivityWatch\.app)/Contents/MacOS/aw-", ps.stdout or "", re.M)]
    except (OSError, subprocess.SubprocessError):
        pass
    found += [os.path.expanduser(p) for p in PLACES]
    for app in found:
        if _is_aw(app):
            return app
    try:
        md = run(["mdfind", f"kMDItemCFBundleIdentifier == '{BUNDLE_ID}'"], timeout=10)
        for app in (md.stdout or "").splitlines():
            if app.endswith(".app") and _is_aw(app):
                return app
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def installed() -> bool:
    """ActivityWatch is on this Mac (its support folder, or the app in a usual place): no program is started."""
    return os.path.isdir(SUPPORT) or any(_is_aw(os.path.expanduser(p)) for p in PLACES)


def _running(run, pattern: str) -> bool:
    return run(["pgrep", "-f", pattern], timeout=10).returncode == 0


def _afk_fresh(fetch, base: str, since: float) -> bool:
    """An AFK bucket written to after `since`: the watcher runs again."""
    try:
        buckets = fetch(base.rstrip("/") + "/api/0/buckets/")
    except Exception:
        return False
    for name, b in (buckets or {}).items():
        if name.startswith("aw-watcher-afk_") and isinstance(b, dict) and b.get("last_updated"):
            try:
                if datetime.fromisoformat(str(b["last_updated"]).replace("Z", "+00:00")).timestamp() >= since:
                    return True
            except ValueError:
                continue
    return False


def restart(base: str, run=None, fetch=None, sleep=time.sleep, clock=time.monotonic, app: str | None = None) -> dict:
    """Quits ActivityWatch and opens it again, so its AFK watcher reads the new timeout. Quitting from AppleScript is
    ignored by aw-qt, so its process is ended (pkill), and Pulsar waits until every process of the app is gone, at
    most QUIT_WAIT seconds; it then opens the app and waits for its server (/api/0/info) and its AFK watcher (the
    process, or a fresh heartbeat in its bucket), START_WAIT seconds each. {"ok", "message"}."""
    run, fetch = run or _run, fetch or _fetch
    app = app or app_path(run)
    if not app:
        return {"ok": False, "message": "ActivityWatch.app was not found, so Pulsar could not restart it. Quit "
                                        "ActivityWatch from its menu-bar icon and open it again."}
    run(["pkill", "-f", QT], timeout=10)  # 1: it was not running, which is fine
    end = clock() + QUIT_WAIT
    while _running(run, ANY):
        if clock() > end:
            return {"ok": False, "message": f"ActivityWatch did not quit within {QUIT_WAIT} seconds. Quit it from its "
                                            "menu-bar icon and open it again."}
        sleep(0.5)
    since = time.time()
    opened = run(["open", "-a", app], timeout=30)
    if opened.returncode != 0:
        return {"ok": False, "message": f"ActivityWatch could not be opened again ({(opened.stderr or '').strip()[:200]}). "
                                        f"Open it from {os.path.dirname(app)}."}
    end = clock() + START_WAIT
    while True:
        try:
            fetch(base.rstrip("/") + "/api/0/info")
            break
        except Exception:
            if clock() > end:
                return {"ok": False, "message": f"ActivityWatch was opened again but did not answer at {base} within "
                                                f"{START_WAIT} seconds. Check its menu-bar icon."}
            sleep(1)
    end = clock() + START_WAIT
    while not (_running(run, AFK) or _afk_fresh(fetch, base, since)):
        if clock() > end:
            return {"ok": False, "message": "ActivityWatch runs again, but its AFK watcher did not start. Check its "
                                            "menu-bar icon: Modules, aw-watcher-afk."}
        sleep(1)
    return {"ok": True, "message": "ActivityWatch was restarted and its AFK watcher runs."}


def fix(minutes: int, base: str, path: str | None = None, run=None, fetch=None, sleep=time.sleep,
        clock=time.monotonic) -> dict:
    """Settings' Fix, and Save when the two differ: ActivityWatch counts you away after `minutes` ("Away after"),
    longer or shorter than it was, then restarts. {"ok", "changed", "message", "backup"}. Refused in a test or a
    read-only run."""
    path = path or TOML
    why = blocked()
    if why:
        return {"ok": False, "changed": False, "message": why}
    if minutes not in config.AWAY_AFTER or isinstance(minutes, bool):
        raise ValueError("Away after is 10, 15, 20 or 30 minutes.")
    now = read_timeout(path)
    if now["problem"]:
        return {"ok": False, "changed": False, "message": f"Pulsar did not change {path}: {now['problem']}."}
    if not differs(now["seconds"], minutes):
        return {"ok": True, "changed": False,
                "message": f"ActivityWatch already counts you as away after {_mins(now['seconds'])}: nothing to change."}
    try:
        edit = set_timeout(path, minutes * 60)
    except OSError as exc:  # no permission to write next to it, a full disk: nothing changed (set_timeout)
        return {"ok": False, "changed": False, "message": f"Pulsar could not change {path}: {exc.strerror or exc}."}
    except ValueError as exc:  # no file yet (ActivityWatch makes it on its first start): never created here
        return {"ok": False, "changed": False, "message": f"Pulsar did not change {path}: {exc}"}
    try:
        done = restart(base, run, fetch, sleep, clock)
    except (OSError, subprocess.SubprocessError) as exc:
        done = {"ok": False, "message": f"Pulsar could not restart ActivityWatch ({type(exc).__name__}): quit it from "
                                        "its menu-bar icon and open it again."}
    said = (f"ActivityWatch now counts you as away after {minutes} minutes (it was {_mins(edit['before'])}). Days "
            f"already recorded keep their breaks: this applies to new data only. Its file as it was is kept as "
            f"{os.path.basename(edit['backup'])}.")
    return {"ok": done["ok"], "changed": True, "backup": edit["backup"], "message": f"{said} {done['message']}"}


def _mins(seconds) -> str:
    m = seconds / 60
    return f"{int(m) if float(m).is_integer() else round(m, 1)} minute{'' if m == 1 else 's'}"


# ---- Login Items ---------------------------------------------------------------------------------------------

LOGIN = {"at": 0.0, "on": None}  # the last answer of System Events, kept while Pulsar runs: asking again may prompt


def _osascript(run, script: str) -> tuple:
    """(stdout, problem): one AppleScript line through System Events. The first time, macOS asks whether Pulsar may
    control System Events; a no is error -1743."""
    try:
        done = run(["osascript", "-e", script], timeout=120)  # the person may take a while to answer macOS
    except subprocess.TimeoutExpired:
        return "", "macOS did not answer in time. Try again, and answer macOS's question about System Events."
    except OSError as exc:
        return "", f"Pulsar could not ask System Events ({exc.strerror or exc}). " + AUTOMATION
    if done.returncode != 0:
        err = (done.stderr or "").strip()
        if "-1743" in err or "not authorized" in err.lower() or "not allowed" in err.lower():
            return "", "macOS did not let Pulsar ask System Events. " + AUTOMATION
        return "", f"System Events did not answer ({err[:200] or done.returncode}). " + AUTOMATION
    return (done.stdout or "").strip(), None


def login_items(run=None) -> tuple:
    """(names of the Login Items, problem)."""
    out, problem = _osascript(run or _run, 'tell application "System Events" to get the name of every login item')
    return ([n.strip() for n in out.split(",") if n.strip()] if not problem else None), problem


def check_login(run=None) -> dict:
    """Whether ActivityWatch opens at login: {"ok", "on", "message"}. Refused in a test or a read-only run."""
    why = blocked()
    if why:
        return {"ok": False, "on": None, "message": why}
    names, problem = login_items(run)
    if problem:
        return {"ok": False, "on": None, "message": problem}
    on = APP_NAME in names
    LOGIN.update(at=time.time(), on=on)
    return {"ok": True, "on": on, "message": "ActivityWatch opens when you log in." if on else
            "ActivityWatch is not in your Login Items: after a restart of the Mac it records nothing until you open it."}


def _applescript_text(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def add_login(run=None) -> dict:
    """Adds ActivityWatch to the Login Items (hidden: it opens to its menu-bar icon), unless it is there already, then
    lists them again to be sure: System Events answers "login item UNKNOWN" even when it worked.
    {"ok", "on", "message"}. Refused in a test or a read-only run."""
    why = blocked()
    if why:
        return {"ok": False, "on": None, "message": why}
    run = run or _run
    names, problem = login_items(run)
    if problem:
        return {"ok": False, "on": None, "message": problem}
    if APP_NAME in names:
        LOGIN.update(at=time.time(), on=True)
        return {"ok": True, "on": True, "message": "ActivityWatch was already in your Login Items."}
    app = app_path(run)
    if not app:
        return {"ok": False, "on": False, "message": "ActivityWatch.app was not found on this Mac."}
    _, problem = _osascript(run, 'tell application "System Events" to make login item at end with properties '
                                 f'{{path:{_applescript_text(app)}, hidden:true}}')
    if problem:
        return {"ok": False, "on": False, "message": problem}
    names, problem = login_items(run)
    on = bool(names) and APP_NAME in names
    LOGIN.update(at=time.time(), on=on)
    if not on:
        return {"ok": False, "on": False, "message": problem or "macOS did not add it. " + AUTOMATION}
    return {"ok": True, "on": True, "message": f"Added: ActivityWatch ({app}) opens when you log in."}


def status(minutes: int) -> dict:
    """What Settings shows, with no program started: the timeout read from the file, whether it is longer than
    "Away after" (`minutes`), and the last Login Items answer while Pulsar runs."""
    t = read_timeout()
    return {"installed": installed(), "file": TOML.replace(os.path.expanduser("~"), "~", 1),
            "seconds": t["seconds"], "fromFile": t["exists"], "problem": t["problem"],
            "longer": longer(t["seconds"], minutes), "differs": differs(t["seconds"], minutes), "login": LOGIN["on"]}
