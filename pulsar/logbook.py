"""The error log: what went wrong, when and where, kept for 31 days so it can be read in the page or downloaded
and sent to whoever maintains Pulsar.

One JSON line per event in ~/.cache/pulsar/logs/errors-YYYY-MM-DD.jsonl (owner-only, like every file Pulsar
writes). Tokens, keys and signed URLs are cut out before anything is written.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import threading
import time
from datetime import datetime, timedelta

from . import __version__, config

LOG_DIR = os.path.join(config.CACHE_DIR, "logs")
KEEP_DAYS = 31
_lock = threading.Lock()
_last: dict = {}  # (where, message) -> time, so one problem repeated by every request is logged once a minute
SECRET = re.compile(
    r"(?i)(bearer\s+|x-auth-token[\"']?\s*[:=]\s*[\"']?|[\"']?(?:access_?|refresh_?)?token[\"']?\s*[:=]\s*[\"']?"
    r"|[?&](?:sig|signature|key|token)=)[^\s\"'&,}]+"
    r"|\b(gh[pousr]_|sk-)[A-Za-z0-9_]{10,}")


def scrub(text: str) -> str:
    """Remove anything that looks like a credential: tokens, keys, signatures, very long opaque strings."""
    return SECRET.sub(lambda m: (m.group(1) or "") + "[removed]", str(text or ""))


def record(where: str, message: str, kind: str = "error", detail: str = "", once_per: int = 60) -> None:
    """Write one event, at most once per `once_per` seconds for the same message. Never raises: logging must not
    become a second failure."""
    try:
        message, detail = scrub(message)[:1000], scrub(detail)[-4000:]
        key = (where, message)
        now = time.time()
        with _lock:
            if now - _last.get(key, 0) < once_per:
                return
            _last[key] = now
            os.makedirs(LOG_DIR, exist_ok=True)
            line = json.dumps({"time": datetime.now().astimezone().isoformat(timespec="seconds"), "kind": kind,
                               "where": where, "message": message, "detail": detail, "version": __version__},
                              ensure_ascii=False)
            with open(os.path.join(LOG_DIR, f"errors-{datetime.now().strftime('%Y-%m-%d')}.jsonl"), "a",
                      encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        pass


def read(days: int) -> list:
    """Events of the last `days` days, newest first."""
    since = datetime.now().astimezone() - timedelta(days=days)
    out = []
    for back in range(days + 1):
        name = f"errors-{(datetime.now() - timedelta(days=back)).strftime('%Y-%m-%d')}.jsonl"
        try:
            with open(os.path.join(LOG_DIR, name), encoding="utf-8") as fh:
                for line in fh:
                    try:
                        ev = json.loads(line)
                        if datetime.fromisoformat(ev["time"]) >= since:
                            out.append(ev)
                    except (ValueError, KeyError):
                        continue
        except OSError:
            continue
    return sorted(out, key=lambda e: e["time"], reverse=True)[:5000]


def as_text(days: int) -> str:
    """The log as a plain text file to attach to a message: who runs what, then every event."""
    events = read(days)
    head = [f"Pulsar {__version__} error log, last {days} day{'s' if days != 1 else ''}",
            f"Written {datetime.now().astimezone().isoformat(timespec='seconds')}",
            f"macOS {platform.mac_ver()[0] or platform.platform()}, {platform.machine()}, Python {sys.version.split()[0]}",
            f"Time zone: {config.timezone_name(config.load()) or 'UTC offset'}",
            f"Events: {len(events)}", ""]
    body = []
    for ev in reversed(events):
        body.append(f"{ev['time']}  {ev['kind'].upper():7}  {ev['where']}  (v{ev.get('version', '?')})")
        body.append(f"    {ev['message']}")
        if ev.get("detail"):
            body.extend("    " + line for line in ev["detail"].splitlines())
        body.append("")
    launcher = os.path.expanduser("~/Library/Logs/Pulsar.log")  # what the app's launcher wrote, if anything
    if os.path.exists(launcher):
        try:
            with open(launcher, encoding="utf-8", errors="replace") as fh:
                tail = fh.read()[-20000:]
            body += ["", "---- last lines of ~/Library/Logs/Pulsar.log ----", scrub(tail)]
        except OSError:
            pass
    return "\n".join(head + (body or ["No errors in this period."])) + "\n"


def prune() -> None:
    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    try:
        for name in os.listdir(LOG_DIR):
            m = re.fullmatch(r"errors-(\d{4}-\d{2}-\d{2})\.jsonl", name)
            if m and m.group(1) < cutoff:
                os.remove(os.path.join(LOG_DIR, name))
    except OSError:
        pass
