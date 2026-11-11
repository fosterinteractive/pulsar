"""What was on screen: ActivityWatch (input-based) or MemTime (window in front). Read only.

Every reader returns "items": {s, e, app, title, host, cat, icon}, with s and e in minutes from the start of
the range asked for, so a day and a month are handled the same way.
"""

from __future__ import annotations

import atexit
import bisect
import glob
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import urllib.request
from collections.abc import MutableMapping
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable

from . import config

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

_memtime_lock = threading.Lock()
_memtime_copy = {"dir": "", "source": "", "at": 0.0}
MEMTIME_COPY_FOR = 60  # seconds one copy serves every query (the database is tens of megabytes)
COPY_PREFIX = "pulsar-memtime-"
STALE_COPY = 600       # seconds after which a copy is nobody's: a running Pulsar replaces its own within a minute of use
_swept = [0.0]


def sweep_memtime_copies(older_than: float = STALE_COPY) -> int:
    """Remove the copies a Pulsar left when it ended without tidying up (killed, crashed, or restarted into an update,
    when nothing at exit runs): every copy older than ten minutes, whichever Pulsar made it. A copy serves queries for
    a minute only, so none that old is still read. Returns how many went."""
    gone = 0
    for old in glob.glob(os.path.join(tempfile.gettempdir(), COPY_PREFIX + "*")):
        try:
            if time.time() - os.path.getmtime(old) > older_than:
                shutil.rmtree(old)
                gone += 1
        except OSError:
            pass
    return gone


def _memtime_db(db_path: str) -> str:
    """A private copy of MemTime's database (MemTime keeps the original open and writing), made at most once a minute:
    copying it for every query cost more than the query. The copy lives in this user's temporary folder and goes when
    the next one is made or when Pulsar quits; copies older Pulsars left go too, every ten minutes at most."""
    with _memtime_lock:
        c = _memtime_copy
        if c["dir"] and c["source"] == db_path and time.time() - c["at"] < MEMTIME_COPY_FOR \
                and os.path.exists(os.path.join(c["dir"], "core.db")):
            return os.path.join(c["dir"], "core.db")
        if time.time() - _swept[0] > STALE_COPY:
            _swept[0] = time.time()
            sweep_memtime_copies()
        fresh = tempfile.mkdtemp(prefix=COPY_PREFIX)
        try:
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(db_path + suffix):
                    shutil.copy2(db_path + suffix, os.path.join(fresh, "core.db" + suffix))
        except OSError:
            shutil.rmtree(fresh, ignore_errors=True)
            raise
        old, c["dir"], c["source"], c["at"] = c["dir"], fresh, db_path, time.time()
        if old:
            shutil.rmtree(old, ignore_errors=True)  # a query still reading it keeps its open file until it is done
        return os.path.join(fresh, "core.db")


@atexit.register
def _forget_memtime_copy() -> None:
    if _memtime_copy["dir"]:
        shutil.rmtree(_memtime_copy["dir"], ignore_errors=True)


def memtime_query(sql: str, args=(), db_path: str = MEMTIME_DB) -> list:
    if not os.path.exists(db_path):
        return []  # not installed: nothing to say
    try:
        db = sqlite3.connect(_memtime_db(db_path))
        try:
            return db.execute(sql, args).fetchall()
        finally:
            db.close()
    except (sqlite3.Error, OSError) as exc:
        PROBLEMS["memtime"] = f"MemTime's database could not be read: {exc}"
        return []


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


class _Problems(MutableMapping):
    """tracker -> why it gave nothing the last time it was read; shown in the Activity header. A thread inside
    own_problems() keeps its own, so a background read of a past day never clears or sets today's."""

    def _now(self) -> dict:
        own = getattr(_own, "problems", None)
        return _shared if own is None else own

    def __getitem__(self, k):
        return self._now()[k]

    def __setitem__(self, k, v):
        self._now()[k] = v

    def __delitem__(self, k):
        del self._now()[k]

    def __iter__(self):
        return iter(list(self._now()))

    def __len__(self):
        return len(self._now())

    def copy(self) -> dict:
        return dict(self._now())


_shared: dict = {}
_own = threading.local()
_NONE = object()
PROBLEMS = _Problems()


@contextmanager
def own_problems():
    """The problems of the reads made inside, in this thread only ({tracker: why}), apart from PROBLEMS. Nests: an
    inner one keeps its own and gives the outer one back."""
    outer = getattr(_own, "problems", _NONE)
    _own.problems = {}
    try:
        yield _own.problems
    finally:
        if outer is _NONE:
            del _own.problems
        else:
            _own.problems = outer


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


def present_spans(afk_events: list, bridge: float = 0) -> list:
    """[[start, end]] in seconds while someone was at the keys: the AFK watcher's not-afk events, and every afk event
    shorter than `bridge` seconds between two not-afk ones. Settings' "Away after" gives `bridge`: a pause shorter than
    it is work, as in the fi-time-from-activity skill (activity_blocks.py). It only sees pauses ActivityWatch wrote, so
    one shorter than ActivityWatch's own AFK timeout is not there at all (awsetup.py)."""
    evs = sorted((ev for ev in afk_events if isinstance(ev, dict) and ev.get("timestamp")),
                 key=lambda ev: parse_ts(ev["timestamp"]))
    status = [(ev.get("data") or {}).get("status") for ev in evs]
    spans = []
    for i, ev in enumerate(evs):
        on = status[i] == "not-afk" or (status[i] == "afk" and float(ev.get("duration") or 0) < bridge
                                        and 0 < i < len(evs) - 1 and status[i - 1] == status[i + 1] == "not-afk")
        if on:
            s = parse_ts(ev["timestamp"])
            spans.append([s, s + float(ev.get("duration") or 0)])
    return merge(spans)


def activity_aw(base: str, t0: float, t1: float, classify: Classifier, away: float | None = None, raw=False) -> list:
    """The windows ActivityWatch saw while someone was at the keys; a pause shorter than `away` minutes (Settings'
    "Away after" when not given) between two stretches of input counts as being there. `raw`: the events, when the
    caller has read them already."""
    raw = activitywatch_raw(base, t0, t1) if raw is False else raw
    if not raw:
        return []
    minutes = config.away_after() if away is None else away
    present = present_spans(raw["afk"], minutes * 60)
    if not raw["afk"]:  # windows, but the AFK watcher recorded nothing: it stopped or lacks access, or a pause goes on
        if afk_silent(base, t0, raw["window"], minutes * 60 + AFK_GRACE):
            PROBLEMS["aw"] = ("ActivityWatch saw windows but its AFK watcher recorded nothing, so Pulsar cannot tell "
                              "when you were at the computer. Check that the AFK watcher runs and has Accessibility access.")
        return []
    return compact(clip_to_present(raw["window"], present, t0, t1, classify))


AFK_GRACE = 15 * 60  # seconds past ActivityWatch's own AFK timeout (Away after) before its silence means trouble


def afk_silent(base: str, t0: float, windows: list, grace: float) -> bool:
    """Windows went on for longer than `grace` after the AFK watcher's last event: it stopped, or lacks access. The
    watcher writes "away" only once its timeout has passed, dated back to the last input, so a day that starts inside
    a pause has windows and no AFK event yet (2026-11-11 00:01: away since 23:42, written at 00:12); its events just
    before the day count, so that is no alarm."""
    last_window = max((parse_ts(e["timestamp"]) + float(e.get("duration") or 0) for e in windows
                       if isinstance(e, dict) and e.get("timestamp")), default=t0)
    api = base.rstrip("/") + "/api/0"
    a = datetime.fromtimestamp(t0 - grace, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    b = datetime.fromtimestamp(t0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        before = [ev for x in http_json(f"{api}/buckets/", timeout=1.5) if x.startswith("aw-watcher-afk_")
                  for ev in http_json(f"{api}/buckets/{x}/events?start={a}&end={b}&limit=-1", timeout=10)]
    except Exception:
        before = []
    last_afk = max((parse_ts(e["timestamp"]) + float(e.get("duration") or 0) for e in before
                    if isinstance(e, dict) and e.get("timestamp")), default=float("-inf"))
    return last_window - last_afk > grace


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


def answering(aw_url: str) -> bool:
    """Whether a tracker can be read now: MemTime's database is there, or ActivityWatch answers."""
    if os.path.exists(MEMTIME_DB):
        return True
    try:
        http_json(aw_url.rstrip("/") + "/api/0/buckets/", timeout=1.5)
        return True
    except Exception:
        return False


def aw_away(raw: dict | None, t0: float, minutes: float) -> list:
    """[(s, e)] in minutes from t0: the stretches ActivityWatch's AFK watcher recorded as away for at least `minutes`.
    Only what it recorded: a time it was not running is not away."""
    out = []
    for ev in (raw or {}).get("afk") or []:
        if isinstance(ev, dict) and (ev.get("data") or {}).get("status") == "afk" and ev.get("timestamp") \
                and float(ev.get("duration") or 0) >= minutes * 60:
            s = parse_ts(ev["timestamp"])
            out.append(((s - t0) / 60, (s + float(ev["duration"]) - t0) / 60))
    return sorted(out)


def cut_out(items: list, gaps: list) -> list:
    """`items` without the minutes inside `gaps` ([(s, e)] in minutes): a window in front while nobody was there."""
    if not gaps:
        return items
    out = []
    for it in items:
        pieces = [(it["s"], it["e"])]
        for a, b in gaps:
            if b <= it["s"] or a >= it["e"]:
                continue
            pieces = [p for s, e in pieces for p in ((s, min(e, a)), (max(s, b), e)) if p[1] - p[0] >= 1 / 60]
        out.extend({**it, "s": round(s, 3), "e": round(e, 3)} for s, e in pieces)
    return out


def both(t0: float, t1: float, aw_url: str, classify: Classifier, away: float | None = None) -> dict:
    """Both trackers' items. `away`: Settings' "Away after" in minutes (read from the settings when not given).
    MemTime counts the window in front even while nobody is there (until its own idle time), so where ActivityWatch
    recorded the person away, MemTime's items are cut there too: one answer to "was I there?" whichever is shown."""
    PROBLEMS.pop("memtime", None)
    minutes = config.away_after() if away is None else away
    raw = activitywatch_raw(aw_url, t0, t1)
    mem = activity_memtime(t0, t1, classify)
    return {"aw": activity_aw(aw_url, t0, t1, classify, minutes, raw=raw), "memtime": cut_out(mem, aw_away(raw, t0, minutes))}


# ActivityWatch saw less than this share of MemTime's minutes: it was down most of the day. Not more: MemTime counts a
# window in front with nobody at the keys too, so a day away from the desk can show ActivityWatch at a third of it
AW_SHORT = 0.2


def pick(found: dict, source: str) -> str:
    """The tracker asked for, or (auto) ActivityWatch, the main tracker: it knows when someone is at the keys, even
    when MemTime saw more. MemTime for a day ActivityWatch saw nothing of, or a sliver of (AW_SHORT: it was down most
    of the day; 2026-11-03 it saw 2 minutes and MemTime 691, and the whole day showed no work), or when it is all
    there is. On an ordinary day the two are close (517 minutes against 503)."""
    if source in found:
        return source
    total = lambda k: sum(x["e"] - x["s"] for x in found.get(k) or [])  # noqa: E731
    if found.get("aw") and not (found.get("memtime") and total("aw") < AW_SHORT * total("memtime")):
        return "aw"
    return max(found, key=total)
