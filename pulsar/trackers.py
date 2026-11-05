"""What was on screen: ActivityWatch (input-based) or MemTime (window in front). Read only.

Every reader returns "items": {s, e, app, title, host, cat, icon}, with s and e in minutes from the start of
the range asked for, so a day and a month are handled the same way.
"""

from __future__ import annotations

import bisect
import json
import os
import re
import shutil
import sqlite3
import tempfile
import urllib.request
from datetime import datetime, timezone
from typing import Callable

MEMTIME_DIR = os.path.expanduser("~/Library/Application Support/memtime/user")
MEMTIME_DB = os.path.join(MEMTIME_DIR, "core.db")
ICONS_DIR = os.path.join(MEMTIME_DIR, "icons")
EDITORS = re.compile(r"^(Cursor|Code|Visual Studio Code)$")
APP_NAMES = {  # bundle ids MemTime records, as people know the apps
    "com.todesktop.230313mzl4w4u92": "Cursor", "com.tdesktop.Telegram": "Telegram", "com.google.Chrome": "Google Chrome",
    "com.tinyspeck.slackmacgap": "Slack", "com.memtime.app": "MemTime", "com.apple.Terminal": "Terminal",
    "us.zoom.xos": "zoom.us", "com.anthropic.claudefordesktop": "Claude", "com.microsoft.VSCode": "Code",
}

Classifier = Callable[[str, str, str], str]


def classifier(patterns: list) -> Classifier:
    """'personal' when the app, title or site contains one of the words (any case), else 'work'."""
    words = [re.escape(p.strip()) for p in patterns or [] if p and p.strip()]
    personal = re.compile("|".join(words), re.I) if words else None
    return lambda app, title, host: "personal" if personal and personal.search(f"{app} {title} {host}") else "work"


def parse_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def merge(spans: list) -> list:
    out: list = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def clean_title(app: str, title: str) -> str:
    title = (title or "").strip().lstrip("● ").strip()
    if EDITORS.match(app or ""):
        title = title.split(" — ")[0]  # "Session tab — repo (Workspace)" -> the tab
    return title


def compact(items: list) -> list:
    """Join touching pieces of the same window, so a day is hundreds of rows, not thousands."""
    items.sort(key=lambda x: x["s"])
    out: list = []
    for it in items:
        last = out[-1] if out else None
        if last and it["s"] - last["e"] < 0.05 and (last["app"], last["title"], last["host"], last.get("url")) == \
                (it["app"], it["title"], it["host"], it.get("url")):
            last["e"] = max(last["e"], it["e"])
        else:
            out.append(it)
    return out


class Icons:
    """App and site icons MemTime has already saved; nothing is fetched from the network."""

    def __init__(self, folder: str = ICONS_DIR):
        self.folder, self._seen = folder, {}

    def path(self, key: str) -> str | None:
        if not key or len(key) > 200 or not re.fullmatch(r"[A-Za-z0-9._-]+", key):
            return None
        if key in self._seen:
            return self._seen[key]
        path = os.path.join(self.folder, key + ".png")
        if not os.path.exists(path):
            return None  # misses are not remembered: random names must not fill memory
        self._seen[key] = path
        return path

    def key(self, *keys) -> str | None:
        return next((k for k in keys if self.path(k)), None)


ICONS = Icons()


def workspace(app: str, title: str) -> str:
    """The folder an editor has open ("Session tab — my-repo (Workspace)" -> "my-repo"), for rules."""
    if not EDITORS.match(app or "") or " — " not in (title or ""):
        return ""
    return re.sub(r"\s*\(Workspace\)$", "", title.split(" — ")[-1]).strip()


def bare_url(url: str) -> str:
    """Scheme, host and path only: queries carry tokens and signatures that rules never need."""
    return re.sub(r"[?#].*$", "", url or "")[:300]


def make_item(t0: float, t1: float, s: float, e: float, app: str, title: str, host: str, bundle: str | None,
              classify: Classifier, names: dict, url: str = "") -> dict | None:
    s, e = max(s, t0), min(e, t1)
    if e - s < 1:
        return None
    app = APP_NAMES.get(app, app or "")
    return {"s": round((s - t0) / 60, 3), "e": round((e - t0) / 60, 3), "app": app,
            "title": clean_title(app, title), "host": host or "", "cat": classify(app, title or "", host or ""),
            "icon": ICONS.key(host, bundle or names.get("name:" + app)),
            "workspace": workspace(app, title or ""), "url": bare_url(url)}


# ---- MemTime (a copy: the app keeps its database open) ------------------------------------

def memtime_query(sql: str, args=(), db_path: str = MEMTIME_DB) -> list:
    if not os.path.exists(db_path):
        return []  # not installed: nothing to say
    tmp = tempfile.mkdtemp()
    try:
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(db_path + suffix):
                shutil.copy2(db_path + suffix, os.path.join(tmp, "core.db" + suffix))
        db = sqlite3.connect(os.path.join(tmp, "core.db"))
        try:
            return db.execute(sql, args).fetchall()
        finally:
            db.close()
    except (sqlite3.Error, OSError) as exc:
        PROBLEMS["memtime"] = f"MemTime's database could not be read: {exc}"
        return []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_program_names: dict = {}


def program_names() -> dict:
    """MemTime's bundle id -> app name, and back ("name:Slack" -> bundle id), for icons of ActivityWatch apps."""
    if not _program_names:
        for program, name in memtime_query("select program, name from TProgramName"):
            if program and name:
                _program_names[program] = name
                _program_names.setdefault("name:" + name, program)
    return _program_names


def activity_memtime(t0: float, t1: float, classify: Classifier) -> list:
    rows = memtime_query("select start, coalesce(end, start), type, program, title, host, path from TTracking "
                         "where coalesce(end, start) >= ? and start < ? and type != 'offline'", (int(t0), int(t1)))
    names = program_names()
    out = [make_item(t0, t1, s, e, names.get(program, program or ""), title, host or "", program, classify, names,
                     url=path if kind == "browser" else "")
           for s, e, kind, program, title, host, path in rows]
    return compact([x for x in out if x])


# ---- ActivityWatch -------------------------------------------------------------------------

def http_json(url: str, timeout: float = 5):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


PROBLEMS: dict = {}  # tracker -> why it gave nothing the last time it was read; shown in the Activity header


def activitywatch_raw(base: str, t0: float, t1: float) -> dict | None:
    """Window and AFK events of every host's buckets (a renamed computer gets a new pair)."""
    api = base.rstrip("/") + "/api/0"
    PROBLEMS.pop("aw", None)
    try:
        buckets = http_json(f"{api}/buckets/", timeout=1.5)
    except Exception:
        PROBLEMS["aw"] = f"ActivityWatch is not answering at {base}. Is it running?"
        return None
    a = datetime.fromtimestamp(t0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    b = datetime.fromtimestamp(t1, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # ActivityWatch can run with a watcher missing; then say which, instead of an empty day without a reason
    if not any(x.startswith("aw-watcher-window_") for x in buckets):
        PROBLEMS["aw"] = ("ActivityWatch runs but records no windows: turn on its window watcher (aw-watcher-window) "
                          "and give ActivityWatch Accessibility access in System Settings > Privacy & Security.")
        return None
    if not any(x.startswith("aw-watcher-afk_") for x in buckets):
        PROBLEMS["aw"] = ("ActivityWatch records windows but not whether you are at the computer: turn on its "
                          "AFK watcher (aw-watcher-afk), without which Pulsar cannot tell work time.")
        return None
    out: dict = {"window": [], "afk": []}
    for key, prefix in (("window", "aw-watcher-window_"), ("afk", "aw-watcher-afk_")):
        for bucket in (x for x in buckets if x.startswith(prefix)):
            try:
                out[key] += http_json(f"{api}/buckets/{bucket}/events?start={a}&end={b}&limit=-1", timeout=30)
            except Exception as exc:
                PROBLEMS["aw"] = f"ActivityWatch did not return its events: {exc}"
                return None
    return out if out["window"] else None


def activity_aw(base: str, t0: float, t1: float, classify: Classifier) -> list:
    raw = activitywatch_raw(base, t0, t1)
    if not raw:
        return []
    present = merge([[parse_ts(ev["timestamp"]), parse_ts(ev["timestamp"]) + ev["duration"]]
                     for ev in raw["afk"] if (ev.get("data") or {}).get("status") == "not-afk"])
    if not raw["afk"]:  # windows, but the AFK watcher recorded nothing at all: it stopped, or lacks access
        PROBLEMS["aw"] = ("ActivityWatch saw windows but its AFK watcher recorded nothing, so Pulsar cannot tell when "
                          "you were at the computer. Check that the AFK watcher runs and has Accessibility access.")
        return []
    return compact(clip_to_present(raw["window"], present, t0, t1, classify))


def clip_to_present(window_events: list, present: list, t0: float, t1: float, classify: Classifier) -> list:
    """The window in front, only while someone was at the keys."""
    starts = [a for a, _ in present]
    names = program_names()
    out = []
    for ev in window_events:
        s = parse_ts(ev["timestamp"])
        e = s + ev["duration"]
        d = ev.get("data") or {}
        host = re.sub(r"^https?://([^/:?#]+).*", r"\1", d.get("url") or "") if d.get("url") else ""
        i = max(bisect.bisect_right(starts, s) - 1, 0)
        while i < len(present) and present[i][0] < e:
            lo, hi = max(present[i][0], s), min(present[i][1], e)
            if hi > lo:
                it = make_item(t0, t1, lo, hi, d.get("app", ""), d.get("title", ""), host, None, classify, names,
                               url=d.get("url") or "")
                if it:
                    out.append(it)
            i += 1
    return out


def both(t0: float, t1: float, aw_url: str, classify: Classifier) -> dict:
    PROBLEMS.pop("memtime", None)
    return {"aw": activity_aw(aw_url, t0, t1, classify), "memtime": activity_memtime(t0, t1, classify)}


def pick(found: dict, source: str) -> str:
    """The tracker asked for, or (auto) the one that saw more of the time."""
    if source in found:
        return source
    return max(found, key=lambda k: sum(x["e"] - x["s"] for x in found[k]))
