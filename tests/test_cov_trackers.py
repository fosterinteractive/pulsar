"""Whether the trackers record (trackhealth.py) beyond tests/test_trackhealth.py: the answers ActivityWatch may give,
both trackers stopped, MemTime alone, and opening a tracker with every program faked (trackhealth.start, the
/api/trackers/open route). Never the real ActivityWatch, MemTime, pgrep or open.

    python3 -m unittest discover -s tests
"""

import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH, refused  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import awsetup, server, trackers, trackhealth  # noqa: E402
from test_trackhealth import NOW, TZ, URL, aw_answers, aw_down, iso  # noqa: E402


class Mac(unittest.TestCase):
    """A Mac in a temporary folder, as test_trackhealth.Trackers sets it up."""

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
        os.makedirs(os.path.join(self.aw_dir, "aw-server-rust"), exist_ok=True)
        db = os.path.join(self.aw_dir, "aw-server-rust", "sqlite.db")
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


class HealthTest(Mac):
    def test_a_window_watcher_date_it_cannot_read_is_stale_with_no_time(self):
        self.aw_installed()
        h = self.health(lambda url, timeout=5: {"aw-watcher-window_mac": {"last_updated": "not a date"}})
        self.assertEqual(self.keys(h), ["aw-stale"])
        self.assertIn("has recorded no window: its window watcher has stopped", h["issues"][0]["text"])  # no time to name

    def test_no_buckets_at_all_is_no_window_watcher(self):
        self.aw_installed()
        for answer in (None, {}, {"aw-watcher-afk_mac": {"last_updated": iso(NOW)}}):
            self.assertEqual(self.keys(self.health(lambda url, timeout=5: answer)), ["aw-nowindow"], answer)

    def test_the_newest_of_several_window_watchers_counts(self):
        self.aw_installed()
        for old_last in (False, True):  # whichever comes first in ActivityWatch's answer
            names = ["aw-watcher-window_mac", "aw-watcher-window_old-mac"][::-1 if old_last else 1]
            buckets = {n: {"last_updated": iso(NOW - (86400 if "old" in n else 30))} for n in names}
            self.assertEqual(self.keys(self.health(lambda url, timeout=5: buckets)), [], names)

    def test_both_stopped_are_both_said_and_memtime_does_not_say_activitywatch_records(self):
        self.aw_installed()
        self.memtime_installed()
        h = self.health(aw_down, memtime_runs=False)
        self.assertEqual(self.keys(h), ["aw-down", "memtime-down"])
        self.assertNotIn("Pulsar reads ActivityWatch", h["issues"][1]["text"])
        self.assertEqual([x["action"] for x in h["issues"]], ["open", "open"])

    def test_memtime_alone_left_long_ago_offers_memtime(self):
        self.memtime_installed(days_ago=90)
        h = self.health(aw_down, memtime_runs=False)
        self.assertEqual(self.keys(h), ["none"])
        self.assertEqual((h["issues"][0]["tracker"], h["issues"][0]["action"]), ("memtime", "open"))
        self.assertIn("Open MemTime.", h["issues"][0]["text"])
        self.assertEqual(h["memtime"], {"installed": True, "used": False, "running": False})

    def test_memtime_recording_while_an_unused_activitywatch_is_off_says_nothing(self):
        self.aw_installed(days_ago=90)
        self.memtime_installed()
        h = self.health(aw_down, memtime_runs=True)
        self.assertEqual(self.keys(h), [])
        self.assertFalse(h["aw"]["used"])

    def test_activitywatch_answering_counts_as_used_whatever_its_files_say(self):
        self.aw_installed(days_ago=90)  # old files, but its server answers
        h = self.health(aw_answers())
        self.assertEqual((h["aw"]["used"], h["aw"]["running"], self.keys(h)), (True, True, []))


class StartTest(unittest.TestCase):
    """trackhealth.start with the refusal lifted and every program faked: what it would run, and what it says."""

    def setUp(self):
        self.ran = []
        for p in (mock.patch.object(awsetup, "blocked", lambda: None),
                  mock.patch.object(awsetup, "app_path", lambda run=None: "/Applications/ActivityWatch.app")):
            p.start()
            self.addCleanup(p.stop)

    def fake_run(self, code=0, stderr=""):
        def run(argv, timeout=30):
            self.ran.append(argv)
            return SimpleNamespace(returncode=code, stdout="", stderr=stderr)
        return mock.patch.object(awsetup, "_run", run)

    def test_it_opens_activitywatch_by_its_path_and_memtime_by_its_bundle(self):
        with self.fake_run():
            self.assertEqual(trackhealth.start("aw", URL), {"ok": True, "message": "ActivityWatch is opening."})
            self.assertEqual(trackhealth.start("memtime", URL), {"ok": True, "message": "MemTime is opening."})
        self.assertEqual(self.ran, [["open", "-a", "/Applications/ActivityWatch.app"], ["open", "-b", "com.memtime.app"]])

    def test_restart_is_awsetups_restart(self):
        with mock.patch.object(awsetup, "restart", lambda base: {"ok": True, "message": f"Restarted {base}."}), self.fake_run():
            self.assertEqual(trackhealth.start("aw", URL, restart=True)["message"], f"Restarted {URL}.")
            trackhealth.start("memtime", URL, restart=True)  # restart is for ActivityWatch only: MemTime is opened
        self.assertEqual(self.ran, [["open", "-b", "com.memtime.app"]])

    def test_what_goes_wrong_is_said_never_raised(self):
        with self.fake_run(code=1, stderr="LSOpenURLsWithRole() failed\n"):
            got = trackhealth.start("memtime", URL)
        self.assertFalse(got["ok"])
        self.assertIn("MemTime could not be opened (LSOpenURLsWithRole() failed)", got["message"])
        for exc in (OSError("no such file"), subprocess.TimeoutExpired("open", 30), awsetup.Refused("read-only run")):
            def boom(argv, timeout=30, exc=exc):
                raise exc
            with mock.patch.object(awsetup, "_run", boom):
                self.assertFalse(trackhealth.start("aw", URL)["ok"], exc)
        with mock.patch.object(awsetup, "app_path", lambda run=None: None), self.fake_run():
            self.assertEqual(trackhealth.start("aw", URL), {"ok": False, "message": "ActivityWatch.app was not found on this Mac."})
        with self.fake_run():
            self.assertEqual(trackhealth.start("slack", URL), {"ok": False, "message": "Unknown tracker."})
        self.assertEqual(self.ran, [["open", "-b", "com.memtime.app"]])  # nothing ran for the last two

    def test_the_route_restarts_only_on_true_and_logs_a_failure(self):
        calls, records = [], []
        fake = lambda tracker, url, restart=False: calls.append((tracker, restart)) or {"ok": tracker == "aw", "message": "m"}  # noqa: E731
        with mock.patch.object(trackhealth, "start", fake), \
                mock.patch.object(server.logbook, "record", lambda *a, **k: records.append(a)):
            server.api_trackers_open({"tracker": "aw", "restart": True})
            server.api_trackers_open({"tracker": "aw", "restart": "yes"})
            self.assertEqual(server.api_trackers_open({"tracker": "memtime"}), {"result": {"ok": False, "message": "m"}})
            for bad in ({}, {"tracker": None}, {"tracker": "AW"}):
                with self.assertRaises(ValueError):
                    server.api_trackers_open(bad)
        self.assertEqual(calls, [("aw", True), ("aw", False), ("memtime", False)])
        self.assertEqual(records, [("tracker memtime", "m")])  # only the failure, once


if __name__ == "__main__":
    unittest.main()
