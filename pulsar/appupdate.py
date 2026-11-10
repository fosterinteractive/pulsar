"""Pulsar.app itself: a newer app (its window and launcher) for a Mac whose app is older than a release needs.

The updater (updater.py) replaces only the code under versions/; the app around it, PulsarWindow.swift and
launcher.sh, stays as it was installed. When a release needs a newer app, its signed latest.json says so:

    "app": {"version": "1.6.0", "min": "1.6.0", "url": ".../v1.6.0/Pulsar.zip", "sha256": "<SHA-256 of Pulsar.zip>"}

and the page offers "Reinstall Pulsar" to a window older than `min` (a window of 1.5 or older says no version at
all). Only on that click (POST /api/update/app), never by itself, and never in Pulsar Preview:

1. Pulsar.zip comes from the release, and must match the SHA-256 the signed latest.json gives and carry the release
   key's own signature (Pulsar.zip.sig, the one README.md's install line checks).
2. It is unpacked with ditto into a hidden folder beside the app, on the same disk. The app in it must be Pulsar
   (its bundle id) of the version the release names, pass `codesign --verify --deep --strict`, and carry code a
   release signed (release_sig.check_bundle, with this copy's keys).
3. The two apps swap places in one step (renamex_np with RENAME_SWAP): the new one is at the app's path, the old one
   kept in the hidden folder `.Pulsar-previous` beside it.
4. A helper started for it (finish(), run on its own) waits for the running app to quit (the new window quits by
   itself after the click; an older one asks the person to), opens the new app, and waits for its page to say it
   started (POST /api/app/started, from the new window's page). Then the old copy goes. If the new app does not
   start within two minutes, it is quit, the old one swapped back and opened, and that version is not offered again.

When the app's folder cannot be changed by this user (an app another account put in /Applications), or the app runs
from a place macOS made read-only (App Translocation), nothing is downloaded and the page shows README.md's install
line instead.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Callable, Optional

from . import config, updater
from .release_sig import check_bundle

APP_ID = "com.fosterinteractive.pulsar"
DEFAULT_APP = "/Applications/Pulsar.app"
# The oldest app (window and launcher) this code wants. release.sh writes it into latest.json as app.min; raise it in
# a release whose window or launcher changed in a way people should have, and the page offers "Reinstall Pulsar" to
# every window older than it.
MIN_APP = "1.6.12"
STATE = "app-reinstall.json"   # in updater.ROOT: the swap in progress, or the last one
PREVIOUS = ".Pulsar-previous"  # beside the app: the old app, kept until the new one has started
WORK = ".pulsar-reinstall-"    # beside the app: a download being checked
MAX_APP_ZIP = 30 * 1024 * 1024
WAIT_QUIT = 24 * 3600          # a window of 1.5 or older quits only when its person quits it
WAIT_START = 120
RENAME_SWAP = 0x2              # <stdio.h>: renamex_np swaps the two names atomically
HELPER = "import sys; sys.path.insert(0, sys.argv[1]); from pulsar import appupdate; appupdate.finish(sys.argv[2])"


class NotWritable(ValueError):
    """The app's folder cannot be changed by this user: the install line it is, done by the person."""


def manifest_app(manifest: dict) -> dict:
    """The app a signed latest.json describes, checked for shape, or {} (no app, or one not understood)."""
    a = manifest.get("app") if isinstance(manifest, dict) else None
    if not isinstance(a, dict):
        return {}
    version, minimum = str(a.get("version") or ""), str(a.get("min") or "")
    sha, url = str(a.get("sha256") or "").lower(), str(a.get("url") or "")
    if not (updater.VERSION_RE.fullmatch(version) and updater.VERSION_RE.fullmatch(minimum)
            and re.fullmatch(r"[0-9a-f]{64}", sha) and url.startswith(("https://", "http://"))):
        return {}
    if updater.vtuple(minimum) > updater.vtuple(version):
        return {}
    return {"version": version, "min": minimum, "sha256": sha, "url": url}


def app_path() -> str:
    """The app this server belongs to: the launcher of 1.6.0 on says where it is (PULSAR_APP_BUNDLE); an older one did
    not, and its app is where the install line puts it."""
    return os.environ.get("PULSAR_APP_BUNDLE") or DEFAULT_APP


def bundle_info(path: str) -> dict:
    """{"id", "version", "exe"} of an app, from its Info.plist ({} when it has none)."""
    try:
        with open(os.path.join(path, "Contents", "Info.plist"), "rb") as fh:
            info = plistlib.load(fh)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {}
    return {"id": str(info.get("CFBundleIdentifier") or ""), "version": str(info.get("CFBundleShortVersionString") or ""),
            "exe": str(info.get("CFBundleExecutable") or "")}


def read_state(root: str = updater.ROOT) -> dict:
    try:
        with open(os.path.join(root, STATE), encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(state: dict, root: str = updater.ROOT) -> None:
    os.makedirs(root, exist_ok=True)
    tmp = os.path.join(root, f".{STATE}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, os.path.join(root, STATE))


def status(root: str = updater.ROOT) -> dict:
    """What the page shows about a reinstall: {"pending": "waiting" | "opened" | "", "pendingVersion": the app being
    put in place, "failed": a version that did not start on this Mac}."""
    s = read_state(root)
    pending = s.get("status") if s.get("status") in ("waiting", "opened") else ""
    return {"pending": pending, "pendingVersion": str(s.get("version") or "") if pending else "",
            "failed": str(s.get("failed") or "")}


def install_line() -> str:
    """README.md's install line, for whoever must reinstall by hand."""
    try:
        with open(os.path.join(updater.app_dir(), "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
    except OSError:
        return ""
    return next((x.strip() for x in readme.splitlines() if "Pulsar.zip.sig" in x and "openssl" in x and x.strip().startswith("d=")), "")


def swap(a: str, b: str) -> None:
    """Exchange the two paths in one step (both on one disk)."""
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    fn = libc.renamex_np
    fn.argtypes, fn.restype = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint], ctypes.c_int
    if fn(os.fsencode(a), os.fsencode(b), RENAME_SWAP) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), a)


def app_pids(path: str) -> list:
    """The processes running this app's window (its executable at this path)."""
    exe = os.path.join(path, "Contents", "MacOS", bundle_info(path).get("exe") or "Pulsar")
    try:
        out = subprocess.run(["/bin/ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    pids = []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[1] == exe and parts[0].isdigit() and int(parts[0]) != os.getpid():
            pids.append(int(parts[0]))
    return pids


def _writable(path: str) -> bool:
    parent = os.path.dirname(path)
    return os.access(parent, os.W_OK | os.X_OK) and os.access(path, os.W_OK)


def reinstall(app: dict, root: str = updater.ROOT, path: Optional[str] = None, keys: Optional[tuple] = None,
              spawn: bool = True) -> dict:
    """The "Reinstall Pulsar" click: the new app in place of this one, checked as the module docstring says, and the
    helper started that opens it once this one has quit. Raises ValueError (NotWritable with the install line) when
    it cannot; nothing is changed then."""
    if config.is_preview():
        raise ValueError("Pulsar Preview never installs anything.")
    if not (updater.installed(root) or updater.overrides_allowed() and os.environ.get("PULSAR_UPDATE_URL")):
        raise ValueError("This copy runs from source, so it does not reinstall the app.")
    app = manifest_app({"app": app}) if app else {}
    if not app:
        raise ValueError("The latest release does not describe a new app.")
    if status(root)["pending"]:
        return {"ok": True, "pending": True, "version": status(root)["pendingVersion"]}
    if status(root)["failed"] == app["version"]:
        raise ValueError(f"Pulsar.app {app['version']} did not start on this Mac before, so it is not installed again.")
    path = os.path.abspath(path or app_path())
    if "/AppTranslocation/" in path or not path.endswith(".app"):
        raise NotWritable("macOS runs Pulsar from a read-only copy here. Move Pulsar into Applications, or reinstall it "
                          "with the install line.")
    if bundle_info(path).get("id") != APP_ID:
        raise ValueError(f"{path} is not Pulsar.")
    if not _writable(path):
        raise NotWritable(f"Pulsar cannot replace itself in {os.path.dirname(path)}: this account may not change it. "
                          "Reinstall it with the install line, or ask whoever installed it.")
    version = app["version"]
    url = app["url"]
    if not (updater.overrides_allowed() and os.environ.get("PULSAR_UPDATE_URL")) and \
            url != f"{updater.RELEASES}v{version}/Pulsar.zip":
        raise ValueError(f"The app of Pulsar {version} points to a download outside its GitHub release; ignored.")
    data = updater.fetch(url, MAX_APP_ZIP, timeout=180)
    if hashlib.sha256(data).hexdigest() != app["sha256"]:
        raise ValueError(f"The download of Pulsar {version} did not match its checksum; nothing was changed.")
    signature = updater.fetch(url + ".sig", 4096, timeout=30)
    if not any(updater.verify(data, signature, key) for key in (keys or updater.public_keys())):
        raise ValueError(f"Pulsar.zip of {version} is not signed with the release key; nothing was changed.")
    parent = os.path.dirname(path)
    work = tempfile.mkdtemp(prefix=WORK, dir=parent)
    try:
        new = _unpack_checked(data, work, version, keys)
        old_version = bundle_info(path).get("version") or ""
        pids = app_pids(path)
        swap(new, path)  # the new app at the path; the old one where the new one was, in `work`
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    holder = os.path.join(parent, PREVIOUS)  # from here `work` holds the old app: it is kept, whatever happens
    try:
        shutil.rmtree(holder, ignore_errors=True)  # an older one left behind
        os.replace(work, holder)
    except OSError:
        holder = work
    state = {"app": path, "old": os.path.join(holder, "unpacked", "Pulsar.app"), "version": version,
             "previous": old_version, "pids": pids, "status": "waiting", "at": time.time()}
    write_state(state, root)
    from . import logbook
    logbook.record("updater", f"Pulsar.app {version} is in place of {old_version or 'the old app'}; it opens once "
                   "the running Pulsar quits.", kind="warning")
    if spawn:
        subprocess.Popen([sys.executable, "-E", "-s", "-B", "-c", HELPER, updater.app_dir(), os.path.join(root, STATE)],
                         start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
    return {"ok": True, "version": version, "pending": False}


def _unpack_checked(data: bytes, work: str, version: str, keys: Optional[tuple]) -> str:
    """Pulsar.zip unpacked in `work`, and the app in it checked. Returns the app's path."""
    zip_path = os.path.join(work, "Pulsar.zip")
    with open(zip_path, "wb") as fh:
        fh.write(data)
    out = os.path.join(work, "unpacked")
    run = subprocess.run(["/usr/bin/ditto", "-x", "-k", zip_path, out], capture_output=True, text=True, timeout=120)
    os.remove(zip_path)
    if run.returncode != 0:
        raise ValueError(f"Pulsar.zip of {version} could not be unpacked: {run.stderr.strip()[:200]}")
    if sorted(n for n in os.listdir(out) if n != "__MACOSX") != ["Pulsar.app"]:
        raise ValueError(f"Pulsar.zip of {version} does not hold exactly one Pulsar.app.")
    new = os.path.join(out, "Pulsar.app")
    info = bundle_info(new)
    if info.get("id") != APP_ID or info.get("version") != version:
        raise ValueError(f"Pulsar.zip of {version} holds another app ({info.get('id') or '?'} {info.get('version') or '?'}).")
    exe = os.path.join(new, "Contents", "MacOS", info.get("exe") or "")
    if not info.get("exe") or not os.path.isfile(exe) or os.path.islink(exe):
        raise ValueError(f"The app of {version} has no program in it.")
    run = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", new], capture_output=True, text=True,
                         timeout=120)
    if run.returncode != 0:
        raise ValueError(f"The app of {version} does not pass macOS's signature check: {run.stderr.strip()[:200]}")
    res = os.path.join(new, "Contents", "Resources")
    manifest = os.path.join(res, "app.manifest.json")
    problem = check_bundle(os.path.join(res, "app"), _read(manifest), _read(manifest + ".sig"), version,
                           keys or updater.public_keys())
    if problem:
        raise ValueError(f"The app of {version} is not a signed release: {problem}.")
    return new


def _read(path: str) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(1 << 22)
    except OSError:
        return b""


def started(version: str, root: str = updater.ROOT) -> dict:
    """The new app's page says it started (POST /api/app/started): the reinstall is done, and the old app goes."""
    s = read_state(root)
    if s.get("status") not in ("waiting", "opened") or not version or version != s.get("version"):
        return {"ok": True, "done": False}
    s["status"], s["confirmed"] = "confirmed", time.time()
    write_state(s, root)
    _remove_old(s)
    return {"ok": True, "done": True}


def _remove_old(s: dict) -> None:
    old = str(s.get("old") or "")
    holder = os.path.dirname(os.path.dirname(old))  # .Pulsar-previous (or the download folder), beside the app
    if os.path.basename(holder) == PREVIOUS or os.path.basename(holder).startswith(WORK):
        shutil.rmtree(holder, ignore_errors=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _open(path: str) -> None:
    subprocess.run(["/usr/bin/open", path], capture_output=True, timeout=60)


def finish(state_path: str, opener: Callable[[str], None] = _open, poll: float = 1.0, wait_quit: float = WAIT_QUIT,
           wait_start: float = WAIT_START) -> str:
    """The helper: once the app that was running has quit, open the new one; if its page does not say it started in
    time, put the old one back and open it. Returns the state it ends in."""
    root = os.path.dirname(state_path)
    s = read_state(root)
    if s.get("status") != "waiting":
        return str(s.get("status") or "")
    deadline = time.time() + wait_quit
    while any(_alive(p) for p in s.get("pids") or []) and time.time() < deadline:
        time.sleep(poll)
    if any(_alive(p) for p in s.get("pids") or []):
        return "waiting"  # never quit: the new app is in place, and starts the next time Pulsar is opened
    s["status"] = "opened"
    write_state(s, root)
    time.sleep(min(poll, 1.0))  # the old window's own quitting (its server stopping) is done by then
    opener(s["app"])
    deadline = time.time() + wait_start
    while time.time() < deadline:
        if read_state(root).get("status") == "confirmed":
            return "confirmed"
        time.sleep(poll)
    s = read_state(root)
    if s.get("status") == "confirmed":
        return "confirmed"
    return rollback(s, root, opener, poll)


def rollback(s: dict, root: str, opener: Callable[[str], None] = _open, poll: float = 1.0) -> str:
    """The new app did not start: quit it, swap the old one back, open it, and do not offer that version again."""
    from . import logbook
    path, old = s["app"], s["old"]
    for pid in app_pids(path):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + 15
    while app_pids(path) and time.time() < deadline:
        time.sleep(poll)
    try:
        swap(old, path)
    except OSError as exc:
        logbook.record("updater", f"Pulsar.app {s.get('version')} did not start, and the old app could not be put "
                       f"back: {exc}. Reinstall Pulsar with the install line in the README.")
        s.update(status="failed", failed=s.get("version"))
        write_state(s, root)
        return "failed"
    _remove_old(s)  # now the app that did not start
    s.update(status="rolledback", failed=s.get("version"))
    write_state(s, root)
    logbook.record("updater", f"Pulsar.app {s.get('version')} did not start, so the previous app is back; that "
                   "version is not offered again.")
    opener(path)
    return "rolledback"
