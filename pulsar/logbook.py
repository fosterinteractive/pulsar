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
_last = config.Newest(200)  # (where, message) -> time, so one problem repeated by every request is logged once a minute
_VALUE = r"[^\s\"'&,;}<>]+"
_B64 = r"[A-Za-z0-9_\-]"
SECRET = re.compile(
    # a label, then its value: the label stays, the value goes
    r"(?i:(bearer\s+|x-auth-token[\"']?\s*[:=]\s*[\"']?"
    r"|[\"']?\b(?:access_?|refresh_?|id_?)?token[\"']?\s*[:=]\s*[\"']?"
    r"|[\"']?\b(?:client_?secret|code_?verifier|api_?key|password)[\"']?\s*[:=]\s*[\"']?"
    r"|[\"']?\bx-pulsar-(?:session|key)[\"']?\s*[:=]\s*[\"']?"  # Pulsar's own session token and key, as headers
    r"|[?&](?:x-amz-)?(?:sig|signature|key|token|credential|security-token)="
    r"|\bx-amz-(?:signature|credential|security-token)=)" + _VALUE + ")" +
    # an OAuth code: in an address or a form (code=…&…), never "exit code=1"
    r"|(?i:((?:^|[?&\s\"'])code=)(?=[^\s\"'&,;}<>]*\d)[^\s\"'&,;}<>]{8,})"
    # in an address, a code is always a secret (Pulsar's sign-in code may have no digit at all)
    r"|(?i:([?&]code=)[^\s\"'&,;}<>]{16,})"
    # keys that say what they are, and JSON web tokens
    r"|\b(?:gh[pousr]_|sk-ant-|sk-)[A-Za-z0-9_\-]{10,}"
    # Google's access tokens (ya29.…) and refresh tokens (1//…), which the long-string rule below misses
    r"|\bya29\.[A-Za-z0-9_\-.]{10,}|(?<![\w/])1//[A-Za-z0-9_\-]{10,}"
    r"|\beyJ" + _B64 + r"{10,}\." + _B64 + r"{10,}\." + _B64 + r"*"
    # a JSON field named as a key or a secret ("key", "private_key", "apiKey", "client-secret", "passphrase"): the
    # name stays, the whole quoted value goes, spaces and all ("monkey", "keys" and "key_id" are not such names)
    r"|([\"'](?:[A-Za-z0-9]+[_\-])*(?:[a-z0-9]+(?=[KS]))?(?:[Kk]ey|KEY|[Ss]ecret|SECRET|[Pp]assphrase|PASSPHRASE)"
    r"[\"']\s*:\s*[\"'])[^\"'\n]+"
    # bare lowercase hex of 32 or more, with a letter in it: Pulsar's own key, session tokens, sign-in codes and
    # proofs are such. Not exactly 40, which is a git commit id, and not inside a path or a file name.
    r"|(?<![\w\-/\\.])(?=[0-9a-f]*[a-f])(?![0-9a-f]{40}(?![0-9a-f]))[0-9a-f]{32,}(?![\w\-/\\]|\.\w)"
    # a UUID standing alone (Productive's API tokens look like one); one inside a path, a session's file, stays
    r"|(?<![\w\-/\\.])[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}(?![\w\-/\\]|\.\w)"
    # anything else long and opaque: 40 or more base64url characters, upper and lower case and digits, standing alone
    # (not part of a word, a path, a file or a dotted name)
    r"|(?<![\w\-/\\.])(?=" + _B64 + r"*[A-Z])(?=" + _B64 + r"*[a-z])(?=" + _B64 + r"*\d)" + _B64 + r"{40,}={0,2}"
    r"(?![\w\-/\\]|\.\w)")


def scrub(text: str) -> str:
    """Remove anything that looks like a credential: tokens, keys, signatures, OAuth codes, fields named as keys or
    secrets, long bare hex and UUIDs, very long opaque strings. Normal words, paths and identifiers stay."""
    return SECRET.sub(lambda m: (m.group(1) or m.group(2) or m.group(3) or m.group(4) or "") + "[removed]",
                      str(text or ""))


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
            if config.read_only():  # a script or test server reading the real data: its errors are not the app's
                print("Pulsar (read only): " + line, file=sys.stderr)  # (2026-11-11: three in the log, from a script)
                return
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


def unseen(seen: float = 0) -> int:
    """Errors of the last 24 hours newer than `seen` (seconds; "Mark as seen" in the error log): the header's ! counts
    these. The log itself keeps them all."""
    n = 0
    for e in read(1):
        try:
            if e["kind"] == "error" and datetime.fromisoformat(e["time"]).timestamp() > seen:
                n += 1
        except (ValueError, KeyError, TypeError):
            continue
    return n


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
