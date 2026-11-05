"""Settings, the time zone, the Productive token and Google Calendar's refresh token.

Settings live in ~/.config/pulsar/settings.json (or $XDG_CONFIG_HOME/pulsar). The tokens never go there: on macOS
they are kept in the login keychain, elsewhere in files only their owner can read.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone, tzinfo

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "pulsar")
CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "pulsar")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
SECRET_FILE = os.path.join(CONFIG_DIR, "secret")        # this user's session key: the API answers only its holder
SIGNIN_FILE = os.path.join(CONFIG_DIR, "signin-code")   # a one-time code the page trades for a session token
TOKEN_FILE = os.path.join(CONFIG_DIR, "token")
KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT = "pulsar", "productive"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{8,200}$")

DEFAULTS = {
    "timezone": "",              # "" = the computer's own
    "target_hours": None,        # None = the organization's working day in Productive, else 8
    "personal_patterns": ["Telegram", "WhatsApp", "YouTube", "Netflix", "Instagram", "Facebook", "TikTok", "Reddit"],
    "activitywatch_url": "http://localhost:5600",
    "likely": "auto",            # auto | on | off: the column built from Claude Code sessions
    "productive_source": "auto",  # auto | token | claude-code
    "org_id": "", "org_name": "", "person_id": "", "person_name": "", "person_day_hours": None,
    "update_skip": "",           # a release the person chose to skip in the reminder
    "update_asked": 0,           # when the reminder last showed, in seconds
    "rules": [],                 # [{match, taskId, title, project}]: activity containing `match` belongs to that task
    "calendar_account": "",      # the Google account whose calendar is connected (its email), shown in Settings
}
_lock = threading.Lock()


def write_atomic(path: str, text: str) -> None:
    """Write via a temporary file of its own in the same folder, then rename: readers never see half a file, and
    two threads writing at once each use their own temporary file."""
    import tempfile
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def ensure_dirs() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)


_settings_lock = threading.RLock()  # held across read-change-write, so two saves at once never lose one


def _hours(v) -> bool:
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 24)


def _rule_ok(r) -> bool:
    return isinstance(r, dict) and isinstance(r.get("match"), str) and bool(r["match"].strip()) \
        and isinstance(r.get("taskId"), str) and r["taskId"].isdigit() \
        and all(isinstance(r.get(k, ""), str) for k in ("title", "project"))


def _fits(key: str, value) -> bool:
    """Whether a saved value has the kind of value Pulsar expects for that setting."""
    if key in ("target_hours", "person_day_hours"):
        return _hours(value)
    if key == "timezone":
        return isinstance(value, str) and (not value or _valid_zone(value))
    if key == "likely":
        return value in ("auto", "on", "off")
    if key == "productive_source":
        return value in ("auto", "token", "claude-code")
    if key == "personal_patterns":
        return isinstance(value, list) and all(isinstance(p, str) for p in value)
    return type(value) is type(DEFAULTS[key])


def _keep_copy(text: str, why: str) -> None:
    """Keep the file as it was before anything overwrites it, once per content, and say so in the error log."""
    import hashlib
    from . import logbook  # imported here: logbook itself imports this module
    name = f"settings.broken-{hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()[:12]}.json"
    path = os.path.join(CONFIG_DIR, name)
    if os.path.exists(path):
        return
    try:
        write_atomic(path, text)
    except OSError:
        name = "nowhere (the copy could not be written)"
    logbook.record("settings", f"settings.json {why}. Those settings are back to their defaults; the file as it was "
                               f"is kept as {name} in {CONFIG_DIR}.", kind="warning")


def _load() -> dict:
    out = json.loads(json.dumps(DEFAULTS))  # a deep copy: callers may change the lists
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return out
    try:
        saved = json.loads(text)
    except ValueError:
        saved = None
    if not isinstance(saved, dict):
        _keep_copy(text, "could not be read as settings")
        return out
    bad = []
    for key in DEFAULTS:
        if key not in saved:
            continue
        value = saved[key]
        if key == "rules" and isinstance(value, list):  # a broken rule goes; the others stay
            out[key] = [r for r in value if _rule_ok(r)]
            if len(out[key]) != len(value):
                bad.append("rules")
        elif _fits(key, value):
            out[key] = value
        else:
            bad.append(key)
    if bad:
        _keep_copy(text, "had values of the wrong kind (" + ", ".join(bad) + ")")
    return out


def load() -> dict:
    with _settings_lock:
        return _load()


def save(settings: dict) -> dict:
    ensure_dirs()
    clean = {k: settings.get(k, v) for k, v in DEFAULTS.items()}
    with _settings_lock:
        write_atomic(SETTINGS_FILE, json.dumps(clean, ensure_ascii=False, indent=2))
    return clean


def update(changes: dict) -> dict:
    with _settings_lock:
        return save({**_load(), **changes})


# ---- time zone -----------------------------------------------------------------------

def _valid_zone(name: str) -> bool:
    if not name or ZoneInfo is None or not re.fullmatch(r"UTC|[A-Z][A-Za-z_]+(/[A-Za-z0-9_+\-]+){1,2}", name):
        return False
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


def system_timezone() -> str:
    """The computer's IANA time zone name, or "" when it cannot be told (then the UTC offset is used)."""
    env = (os.environ.get("TZ") or "").lstrip(":")
    if _valid_zone(env):
        return env
    try:  # macOS and most Linux: /etc/localtime -> .../zoneinfo/Europe/Paris
        match = re.search(r"zoneinfo/(.+)$", os.path.realpath("/etc/localtime"))
        if match and _valid_zone(match.group(1)):
            return match.group(1)
    except OSError:
        pass
    try:  # Debian and friends
        with open("/etc/timezone", encoding="utf-8") as fh:
            name = fh.read().strip()
        if _valid_zone(name):
            return name
    except OSError:
        pass
    return ""


def timezone_name(settings: dict) -> str:
    chosen = (settings.get("timezone") or "").strip()
    return chosen if _valid_zone(chosen) else system_timezone()


def tz(settings: dict) -> tzinfo:
    name = timezone_name(settings)
    if name:
        return ZoneInfo(name)
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)  # no zone name (e.g. Windows without tzdata)
    return timezone(offset)


def target_minutes(settings: dict) -> int:
    hours = settings.get("target_hours") or settings.get("person_day_hours") or 8
    try:
        return max(1, int(round(float(hours) * 60)))
    except (TypeError, ValueError):
        return 480


# ---- the Productive token --------------------------------------------------------------

def _keychain() -> bool:
    return sys.platform == "darwin"


_token_cache: list = [0.0, None, False]  # time, token, filled


def get_token() -> str | None:
    """The saved Productive token, read from the keychain at most once a minute."""
    if _token_cache[2] and time.time() - _token_cache[0] < 60:
        return _token_cache[1]
    token = _read_token()
    _token_cache[:] = [time.time(), token, True]
    return token


def _read_token() -> str | None:
    return _read_secret(KEYCHAIN_ACCOUNT, TOKEN_FILE, "Productive token")


def set_token(token: str) -> None:
    _token_cache[2] = False
    token = token.strip()
    if not TOKEN_RE.match(token):
        raise ValueError("That does not look like a Productive token (letters, digits, - and _ only).")
    _write_secret(KEYCHAIN_ACCOUNT, TOKEN_FILE, token, "token")


def delete_token() -> None:
    _token_cache[2] = False
    _delete_secret(KEYCHAIN_ACCOUNT, TOKEN_FILE)


# ---- Google Calendar's refresh token ------------------------------------------------------
# What lets Pulsar read the person's calendar events (pulsar/calendar.py), so it is kept like the Productive token:
# in the keychain on macOS, elsewhere in a file only its owner can read. It never goes into settings.json, the page or
# the log. The access token made from it lives in memory only.

GOOGLE_TOKEN_FILE = os.path.join(CONFIG_DIR, "google-token")
KEYCHAIN_GOOGLE = "google-calendar"
# Google's refresh tokens ("1//0g…") use these characters; nothing that could break out of `security -i`'s quotes.
GOOGLE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=\-]{10,2048}$")
_google_cache: list = [0.0, None, False]  # time, token, filled


def get_google_token() -> str | None:
    if _google_cache[2] and time.time() - _google_cache[0] < 60:
        return _google_cache[1]
    token = _read_secret(KEYCHAIN_GOOGLE, GOOGLE_TOKEN_FILE, "Google Calendar access")
    _google_cache[:] = [time.time(), token, True]
    return token


def set_google_token(token: str) -> None:
    _google_cache[2] = False
    token = (token or "").strip()
    if not GOOGLE_TOKEN_RE.match(token):
        raise ValueError("Google answered with a token Pulsar does not recognise; nothing was saved.")
    _write_secret(KEYCHAIN_GOOGLE, GOOGLE_TOKEN_FILE, token, "Google Calendar access")


def delete_google_token() -> None:
    _google_cache[2] = False
    _delete_secret(KEYCHAIN_GOOGLE, GOOGLE_TOKEN_FILE)


# ---- the keychain, or a private file -----------------------------------------------------

def _read_secret(account: str, path: str, what: str) -> str | None:
    if _keychain():
        run = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account,
                              "-w"], capture_output=True, text=True, timeout=15)
        if run.returncode not in (0, 44):  # 44: no such item; anything else is a keychain that said no (locked, denied)
            from . import logbook
            logbook.record("keychain", f"The keychain did not give Pulsar its {what} (security exit "
                                       f"{run.returncode}: {run.stderr.strip()[:200]}). Unlock the login keychain, or "
                                       "allow Pulsar when macOS asks.", kind="warning", once_per=3600)
        return run.stdout.strip() or None
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _write_secret(account: str, path: str, value: str, what: str) -> None:
    """`value` must already be checked: it goes inside double quotes on `security -i`'s command line."""
    if _keychain():
        # Through `security -i` on stdin, so the value never shows in the process list.
        cmd = f'add-generic-password -U -s {KEYCHAIN_SERVICE} -a {account} -w "{value}"\n'
        run = subprocess.run(["/usr/bin/security", "-i"], input=cmd, capture_output=True, text=True, timeout=15)
        if run.returncode != 0 or _read_secret(account, path, what) != value:
            raise RuntimeError(f"Could not save the {what} in the keychain: " + (run.stderr or run.stdout).strip()[:200])
        return
    ensure_dirs()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value)
    os.chmod(path, 0o600)


def _delete_secret(account: str, path: str) -> None:
    if _keychain():
        subprocess.run(["/usr/bin/security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account],
                       capture_output=True, text=True, timeout=15)
        return
    try:
        os.remove(path)
    except OSError:
        pass


# ---- who may use the API --------------------------------------------------------------------------------
# Two secrets, never the same:
# * the key in SECRET_FILE (owner-only) never leaves this user's files. The launcher proves it holds it (whoami, quit).
# * a browser gets a session token of its own: the launcher or pulsar.py writes a one-time sign-in code, the page
#   trades it for a token (POST /api/session) and keeps it in localStorage, which belongs to Pulsar's own address and
#   port only. Every API call carries it in a header. Unlike a cookie, no other service on 127.0.0.1 ever receives it.
#   The server keeps only hashes of tokens; one unused for 90 days expires.

SESSIONS_FILE = os.path.join(CONFIG_DIR, "sessions.json")
SESSION_DAYS = 90
_sessions: dict = {}
_sessions_loaded = [False]


def session_secret() -> str:
    import secrets
    try:
        with open(SECRET_FILE, encoding="utf-8") as fh:
            value = fh.read().strip()
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    except OSError:
        pass
    ensure_dirs()
    value = secrets.token_hex(32)
    write_atomic(SECRET_FILE, value)
    os.chmod(SECRET_FILE, 0o600)
    return value


def _digest(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


def _load_sessions() -> None:
    if _sessions_loaded[0]:
        return
    try:
        with open(SESSIONS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            _sessions.update({k: float(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, (int, float))})
    except (OSError, ValueError):
        pass
    _sessions_loaded[0] = True


def _save_sessions() -> None:
    cutoff = time.time() - SESSION_DAYS * 86400
    for k in [k for k, v in _sessions.items() if v < cutoff]:
        del _sessions[k]
    for k in sorted(_sessions, key=_sessions.get)[:-20]:  # at most 20 browsers signed in
        del _sessions[k]
    write_atomic(SESSIONS_FILE, json.dumps(_sessions))


def new_session() -> str:
    import secrets
    token = secrets.token_hex(32)
    with _lock:
        _load_sessions()
        _sessions[_digest(token)] = time.time()
        _save_sessions()
    return token


def valid_session(token: str) -> bool:
    if not token or not re.fullmatch(r"[0-9a-f]{64}", token):
        return False
    with _lock:
        _load_sessions()
        key = _digest(token)
        last = _sessions.get(key)
        if last is None or time.time() - last > SESSION_DAYS * 86400:
            return False
        if time.time() - last > 3600:  # sliding expiry, written at most hourly
            _sessions[key] = time.time()
            _save_sessions()
    return True


def end_session(token: str) -> None:
    with _lock:
        _load_sessions()
        _sessions.pop(_digest(token or ""), None)
        _save_sessions()


def is_key(value: str) -> bool:
    """The launcher's proof of being this user: the key from SECRET_FILE itself."""
    import hmac
    return bool(value) and hmac.compare_digest(value, session_secret())


def new_signin_code() -> str:
    import secrets
    ensure_dirs()
    code = secrets.token_hex(16)
    write_atomic(SIGNIN_FILE, f"{code} {int(time.time())}")
    return code


def take_signin_code(code: str) -> bool:
    """True once for the code just written (valid two minutes). A wrong code leaves the file alone, so a guess cannot
    spoil a real sign-in."""
    import hmac
    try:
        with open(SIGNIN_FILE, encoding="utf-8") as fh:
            saved, stamp = (fh.read().split() + ["", "0"])[:2]
        fresh = time.time() - int(stamp) < 120
    except (OSError, ValueError):
        return False
    if not fresh:
        try:
            os.remove(SIGNIN_FILE)
        except OSError:
            pass
        return False
    if not (saved and hmac.compare_digest(saved, str(code or ""))):
        return False
    try:
        os.remove(SIGNIN_FILE)
    except OSError:
        return False
    return True
