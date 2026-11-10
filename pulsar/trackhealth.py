"""Whether the trackers Pulsar reads are recording now, for the page's notice when one has stopped, and opening them.

A tracker counts when the person uses it: its data changed within IN_USE_DAYS (a tracker installed once and left
does not nag). ActivityWatch records when its server answers and its window watcher wrote within STALE_WINDOW;
MemTime when its program runs. Reading this starts nothing; opening or restarting a tracker happens only from the
notice's button, and never in a test or a read-only run (awsetup's rule).
"""

from __future__ import annotations

import glob
import os
import subprocess
import time
from datetime import datetime, timezone

from . import awsetup, config, trackers

IN_USE_DAYS = 30          # data changed within this many days: a tracker the person uses
STALE_WINDOW = 10 * 60    # seconds: ActivityWatch's window watcher writes every few seconds while the Mac is awake
AW_DATA = os.path.join(awsetup.SUPPORT, "aw-server*", "*.db*")  # aw-server's and aw-server-rust's databases
MEMTIME_APPS = ("/Applications/memtime.app", "~/Applications/memtime.app")
MEMTIME_BUNDLE = "com.memtime.app"
MEMTIME_PROCESS = "memtime"  # the app's own process (pgrep -x), not its helpers
GET_AW = "https://activitywatch.net/downloads/"


def _newest(patterns) -> float:
    """The newest change time of the files matching `patterns`, 0 when there are none."""
    times = []
    for pat in patterns:
        for p in glob.glob(pat):
            try:
                times.append(os.path.getmtime(p))
            except OSError:
                continue
    return max(times, default=0.0)


def _pgrep(name: str) -> bool | None:
    """Whether a process named exactly `name` runs; None in a test run or when that cannot be told."""
    if config.tests_offline():
        return None
    try:
        return subprocess.run(["pgrep", "-x", name], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None


def _aw_window(aw_url: str, fetch) -> tuple:
    """(running, newest window write as a timestamp or None when there is no window watcher)."""
    try:
        buckets = fetch(aw_url.rstrip("/") + "/api/0/buckets/", timeout=1.5)
    except Exception:
        return False, None
    newest = None
    for name, b in (buckets or {}).items():
        if name.startswith("aw-watcher-window_"):
            try:
                at = datetime.fromisoformat(str((b or {}).get("last_updated")).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                at = 0.0
            newest = max(newest or 0.0, at)
    return True, newest


def health(aw_url: str, tz, now: float | None = None, fetch=None, running=None) -> dict:
    """Each tracker's state and what the notice says: {"aw": {...}, "memtime": {...}, "issues": [{"key", "tracker",
    "text", "action"}], "login"}. `action`: "open" (start it), "restart" (ActivityWatch's watcher is silent), "get"
    (nothing installed: the download page) or None. `login`: whether ActivityWatch opens at login, as last asked."""
    now = time.time() if now is None else now
    fetch = fetch or trackers.http_json
    running = running or _pgrep
    fresh = now - IN_USE_DAYS * 86400
    aw_up, window = _aw_window(aw_url, fetch)
    aw = {"installed": awsetup.installed(), "used": aw_up or _newest([AW_DATA]) >= fresh, "running": aw_up}
    mem_up = running(MEMTIME_PROCESS)
    memtime = {"installed": any(os.path.isdir(os.path.expanduser(p)) for p in MEMTIME_APPS),
               "used": bool(mem_up) or _newest([trackers.MEMTIME_DB, trackers.MEMTIME_DB + "-wal"]) >= fresh,
               "running": mem_up}
    issues = []
    if aw["used"] and not aw_up:
        issues.append({"key": "aw-down", "tracker": "aw", "action": "open",
                       "text": "ActivityWatch is not running, so Pulsar sees none of your work until it is open again."})
    elif aw_up and window is None:
        issues.append({"key": "aw-nowindow", "tracker": "aw", "action": "restart",
                       "text": "ActivityWatch runs but records no windows: its window watcher is off. Turn it on in its "
                               "menu-bar icon (Modules), and give it Accessibility access in System Settings."})
    elif aw_up and window < now - STALE_WINDOW:
        since = f" since {datetime.fromtimestamp(window, timezone.utc).astimezone(tz).strftime('%H:%M')}" if window else ""
        issues.append({"key": "aw-stale", "tracker": "aw", "action": "restart",
                       "text": f"ActivityWatch runs, but has recorded no window{since}: its window watcher has "
                               "stopped. Restarting ActivityWatch usually brings it back."})
    if memtime["used"] and mem_up is False:
        side = " Pulsar reads ActivityWatch, so your day is still recorded." if aw_up else ""
        issues.append({"key": "memtime-down", "tracker": "memtime", "action": "open",
                       "text": f"MemTime is not running, so it records nothing until it is open again.{side}"})
    if not issues and not aw_up and not mem_up:  # nothing records, and nothing the person uses has stopped
        if aw["installed"] or memtime["installed"]:
            names = " or ".join(n for n, t in (("ActivityWatch", aw), ("MemTime", memtime)) if t["installed"])
            issues.append({"key": "none", "tracker": "aw" if aw["installed"] else "memtime", "action": "open",
                           "text": f"No activity tracker is running, so the day shows no activity. Open {names}."})
        else:
            issues.append({"key": "none", "tracker": None, "action": "get",
                           "text": "Pulsar finds no activity tracker on this Mac, so the day shows no activity. "
                                   "ActivityWatch is free and is the one Pulsar reads first; MemTime works too."})
    return {"aw": aw, "memtime": memtime, "issues": issues, "login": awsetup.LOGIN["on"], "getUrl": GET_AW}


def start(tracker: str, aw_url: str, restart: bool = False) -> dict:
    """Opens a tracker (or, `restart`, quits ActivityWatch and opens it again): {"ok", "message"}. Refused in a test
    or a read-only run."""
    why = awsetup.blocked()
    if why:
        return {"ok": False, "message": why}
    if tracker == "aw" and restart:
        return awsetup.restart(aw_url)
    try:
        if tracker == "aw":
            app = awsetup.app_path()
            if not app:
                return {"ok": False, "message": "ActivityWatch.app was not found on this Mac."}
            done = awsetup._run(["open", "-a", app], timeout=30)
        elif tracker == "memtime":
            done = awsetup._run(["open", "-b", MEMTIME_BUNDLE], timeout=30)
        else:
            return {"ok": False, "message": "Unknown tracker."}
    except (OSError, subprocess.SubprocessError, awsetup.Refused) as exc:
        return {"ok": False, "message": str(exc)}
    name = "ActivityWatch" if tracker == "aw" else "MemTime"
    if done.returncode != 0:
        return {"ok": False, "message": f"{name} could not be opened ({(done.stderr or '').strip()[:200]}). "
                                        "Open it from Applications."}
    return {"ok": True, "message": f"{name} is opening."}
