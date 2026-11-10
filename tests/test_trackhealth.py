"""Whether the trackers record (trackhealth.py), for the page's notice: on temporary folders and fake answers, never
the real ActivityWatch, MemTime, pgrep or open.

    python3 -m unittest discover -s tests
"""

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH, refused  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import awsetup, server, trackers, trackhealth  # noqa: E402

TZ = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc).timestamp()
URL = "http://localhost:5600"


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def aw_answers(window_at=NOW - 5):
    """ActivityWatch's buckets, its window watcher last writing at `window_at` (None: no window watcher)."""
    buckets = {"aw-watcher-afk_mac": {"last_updated": iso(NOW - 60)}}
    if window_at is not None:
        buckets["aw-watcher-window_mac"] = {"last_updated": iso(window_at)}
    return lambda url, timeout=5: buckets


def aw_down(url, timeout=5):
    raise ConnectionRefusedError("connection refused")


class Trackers(unittest.TestCase):
    """A Mac in a temporary folder: ActivityWatch's support folder and database, MemTime's app and database."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.aw_dir = os.path.join(self.home, "activitywatch")
        self.mem_db = os.path.join(self.home, "memtime", "core.db")
        self.mem_app = os.path.join(self.home, "memtime.app")
        for p in (mock.patch.object(awsetup, "SUPPORT", self.aw_dir),
                  mock.patch.object(awsetup, "PLACES", (os.path.join(self.home, "ActivityWatch.app"),)),
                  mock.patch.object(trackhealth, "AW_DATA", os.path.join(self.aw_dir, "aw-server*", "*.db*")),
                  mock.patch.object(trackers, "MEMTIME_DB", self.mem_db),
                  mock.patch.object(trackhealth, "MEMTIME_APPS", (self.mem_app,))):
            p.start()
            self.addCleanup(p.stop)

    def aw_installed(self, days_ago=0):
        os.makedirs(os.path.join(self.aw_dir, "aw-server"), exist_ok=True)
        db = os.path.join(self.aw_dir, "aw-server", "peewee-sqlite.v2.db")
        open(db, "w").close()
        os.utime(db, (NOW - days_ago * 86400,) * 2)

    def memtime_installed(self, days_ago=0):
        os.makedirs(self.mem_app, exist_ok=True)
        os.makedirs(os.path.dirname(self.mem_db), exist_ok=True)
        open(self.mem_db, "w").close()
        os.utime(self.mem_db, (NOW - days_ago * 86400,) * 2)

    def health(self, fetch, memtime_runs=None):
        return trackhealth.health(URL, TZ, now=NOW, fetch=fetch, running=lambda name: memtime_runs)

    def keys(self, h):
        return [x["key"] for x in h["issues"]]

    def test_both_recording_say_nothing(self):
        self.aw_installed()
        self.memtime_installed()
        self.assertEqual(self.keys(self.health(aw_answers(), memtime_runs=True)), [])

    def test_activitywatch_not_running_is_said_with_open(self):
        self.aw_installed(days_ago=1)
        h = self.health(aw_down)
        self.assertEqual(self.keys(h), ["aw-down"])
        self.assertEqual(h["issues"][0]["action"], "open")
        self.assertEqual(h["aw"], {"installed": True, "used": True, "running": False})

    def test_a_silent_window_watcher_offers_a_restart_and_says_since_when(self):
        self.aw_installed()
        h = self.health(aw_answers(window_at=NOW - 25 * 60))
        self.assertEqual(self.keys(h), ["aw-stale"])
        self.assertEqual(h["issues"][0]["action"], "restart")
        self.assertIn("since 13:35", h["issues"][0]["text"])  # 11:35 UTC in Kyiv
        self.assertEqual(self.keys(self.health(aw_answers(window_at=NOW - 5 * 60))), [])  # a few minutes: fine

    def test_no_window_watcher(self):
        self.aw_installed()
        self.assertEqual(self.keys(self.health(aw_answers(window_at=None))), ["aw-nowindow"])

    def test_memtime_stopped_while_activitywatch_records(self):
        self.aw_installed()
        self.memtime_installed()
        h = self.health(aw_answers(), memtime_runs=False)
        self.assertEqual(self.keys(h), ["memtime-down"])
        self.assertIn("Pulsar reads ActivityWatch", h["issues"][0]["text"])

    def test_a_tracker_left_long_ago_does_not_nag(self):
        self.aw_installed()
        self.memtime_installed(days_ago=90)  # installed once, left: MemTime not running says nothing
        self.assertEqual(self.keys(self.health(aw_answers(), memtime_runs=False)), [])
        self.aw_installed(days_ago=90)       # both left, neither running: nothing records, so the notice says that
        h = self.health(aw_down, memtime_runs=False)
        self.assertEqual(self.keys(h), ["none"])
        self.assertEqual(h["issues"][0]["action"], "open")
        self.assertIn("Open ActivityWatch or MemTime", h["issues"][0]["text"])

    def test_no_tracker_on_this_mac_offers_the_download(self):
        h = self.health(aw_down, memtime_runs=False)
        self.assertEqual(self.keys(h), ["none"])
        self.assertEqual(h["issues"][0]["action"], "get")
        self.assertEqual(h["getUrl"], trackhealth.GET_AW)

    def test_memtime_unknown_in_a_test_says_nothing(self):
        self.memtime_installed()
        self.aw_installed()
        self.assertIsNone(trackhealth._pgrep("memtime"))  # a test run never runs pgrep
        self.assertEqual(self.keys(trackhealth.health(URL, TZ, now=NOW, fetch=aw_answers())), [])

    def test_opening_is_refused_in_a_test(self):
        for tracker in ("aw", "memtime"):
            res = trackhealth.start(tracker, URL)
            self.assertFalse(res["ok"])
            self.assertIn("test", res["message"])
        self.assertFalse(trackhealth.start("aw", URL, restart=True)["ok"])
        with self.assertRaises(ValueError):
            server.api_trackers_open({"tracker": "slack"})
        self.assertFalse(server.api_trackers_open({"tracker": "memtime"})["result"]["ok"])

    def test_login_is_the_last_answer(self):
        self.aw_installed()
        with mock.patch.dict(awsetup.LOGIN, {"at": time.time(), "on": False}):
            self.assertIs(self.health(aw_down)["login"], False)


if __name__ == "__main__":
    unittest.main()
