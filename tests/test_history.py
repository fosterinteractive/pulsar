"""When the history cannot be read (your entries of the days before, or a tracker for a past day): said where it shows,
and never kept as if it were the answer (the third review, 2026-11-06, B5).

    python3 -m unittest discover -s tests
"""

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import askclaude, config, dayplan, learn, likely, productive, server  # noqa: E402

DATE = "2026-11-04"
S = {"timezone": "UTC", "person_id": "1", "org_id": "2", "rules": [], "person_name": "", "ask_claude": True,
     "ask_claude_model": "haiku"}


def row(date, task="5", minutes=30):
    return {"id": f"{date}-{task}", "date": date, "start": None, "minutes": minutes, "taskId": task, "task": "Build",
            "project": "Acme", "serviceId": "6", "service": "Dev", "budget": "B", "billable": True, "note": ""}


class SuggestTest(unittest.TestCase):
    """The log dialog's "You logged to these lately": a failed read is said, and asked again in a minute."""

    def setUp(self):
        self.answer = ([], "Productive timed out.", "token")
        for p in (mock.patch.object(config, "CACHE_DIR", tempfile.mkdtemp()),
                  mock.patch.object(server, "current_settings", lambda: dict(S)),
                  mock.patch.object(server.productive, "entries", lambda d0, d1, s, bulk=False: self.answer),
                  mock.patch.object(server.productive, "assigned_tasks", lambda s: []),
                  mock.patch.object(server.logbook, "record", lambda *a, **k: None),
                  mock.patch.object(server, "_recent", config.Newest(8))):
            p.start()
            self.addCleanup(p.stop)

    def test_a_failed_read_is_said_and_kept_a_minute_only(self):
        got = server.api_suggest({"date": DATE})
        self.assertEqual(got["recent"], [])
        self.assertIn("Productive timed out.", got["historyError"])
        self.assertLessEqual(server._recent[(DATE, "1")][0], time.time() + 60)
        self.answer = ([row("2026-11-02")], None, "token")
        server._recent.clear()  # a minute later
        got = server.api_suggest({"date": DATE})
        self.assertNotIn("historyError", got)
        self.assertEqual([t["taskId"] for t in got["recent"]], ["5"])
        self.assertGreater(server._recent[(DATE, "1")][0], time.time() + 500)  # a good read is kept 10 minutes


class HistoryCopyTest(unittest.TestCase):
    def test_a_saved_copy_is_used_but_kept_a_minute_not_an_hour(self):
        copy = ([row("2026-11-02")], "Productive timed out. Showing the copy saved 03.11 09:00; 3 of 60 days have none.",
                "token", True)
        with mock.patch.object(server, "productive_rows", lambda d0, d1, s, bulk=False: copy), \
                mock.patch.object(server, "_rows_before", config.Newest(10)):
            rows, problem = server.history_rows(DATE, S, days=28)
            self.assertEqual(([r["id"] for r in rows], problem), (["2026-11-02-5"], None))
            when = server._rows_before[(DATE, "1")][0]
        self.assertLessEqual(time.time() - when, server.HISTORY_FOR)          # usable now
        self.assertGreater(time.time() + 61 - when, server.HISTORY_FOR)        # and read again a minute from now


class PlanTest(unittest.TestCase):
    def test_log_the_day_says_its_plan_was_made_without_the_days_before(self):
        with mock.patch.object(server, "current_settings", lambda: dict(S)), \
                mock.patch.object(server.config, "tz", lambda s: timezone.utc), \
                mock.patch.object(server, "api_activity", lambda q: {"items": [], "away": []}), \
                mock.patch.object(server, "productive_rows", lambda d0, d1, s: ([], None, "token", False)), \
                mock.patch.object(server, "history_rows", lambda d, s, days=28: ([], "Productive timed out.")), \
                mock.patch.object(server, "api_repeats", lambda q: {"repeats": []}), \
                mock.patch.object(server, "api_suggest", lambda q: {"recent": [], "assigned": []}), \
                mock.patch.object(dayplan.config, "tz", lambda s: timezone.utc), \
                mock.patch("pulsar.likely.enabled", lambda s: False), \
                mock.patch("pulsar.calendar.day", lambda d, s, fresh=False: {"events": [], "connected": False}), \
                mock.patch("pulsar.calendar.history", lambda f, n, s: []):
            got = dayplan.gather(DATE)
        self.assertNotIn("error", got)
        self.assertIn("could not be read", got["historyProblem"])
        self.assertIn("Productive timed out.", got["historyProblem"])
        self.assertIn(got["historyProblem"], got["warning"])  # pulsar --mcp passes "warning" on to Claude Code


class AskClaudeTest(unittest.TestCase):
    def test_claude_is_not_asked_and_nothing_kept_without_the_days_before(self):
        rows = [{"s": 540, "e": 600, "minutes": 60, "project": "acme-site", "titles": ["Fix search"]}]
        with mock.patch.object(server, "current_settings", lambda: dict(S)), \
                mock.patch.object(server.likely, "proposal", lambda *a, **k: {"entries": rows}), \
                mock.patch.object(server, "history_rows", lambda d, s, days=28: ([], "Productive timed out.")), \
                mock.patch.object(server.logbook, "record", lambda *a, **k: None), \
                mock.patch.object(askclaude, "ask", lambda *a, **k: self.fail("asked Claude")), \
                mock.patch.dict(askclaude._cache, clear=True):
            got = server.api_ask_claude({"date": DATE})
            self.assertEqual((got["picks"], got["cost"]), ({}, 0))
            self.assertIn("Productive timed out.", got["error"])
            self.assertIsNone(askclaude.latest(DATE))  # Log the day finds no answer to reuse


class LearnTrackerTest(unittest.TestCase):
    """A past day worked out while no tracker answered (ActivityWatch not up yet at login) is not kept for good."""

    def setUp(self):
        self.today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        self.answer, self.runs, self.up = {"tracker": "no tracker", "entries": []}, [], False

        def proposal(date, *a, **k):
            self.runs.append(date)
            return self.answer
        for p in (mock.patch.object(learn, "DIR", tempfile.mkdtemp()), mock.patch.object(learn, "LEARN_DAYS", 1),
                  mock.patch.object(likely, "proposal", proposal),
                  mock.patch("pulsar.trackers.answering", lambda url: self.up),
                  mock.patch.object(learn.threading, "Thread", lambda target, **k: mock.Mock(start=target))):  # here, now
            p.start()
            self.addCleanup(p.stop)

    def warm(self):
        learn.warm(self.today, {**config.DEFAULTS, "timezone": "UTC"})

    def test_a_day_seen_with_no_tracker_answering_is_worked_out_again_once_one_does(self):
        self.warm()
        self.assertEqual(learn._stored(self.yesterday), [])  # used meanwhile: the sessions alone
        self.warm()
        self.assertEqual(self.runs, [self.yesterday])  # still no tracker: not worked out again for nothing
        self.up, self.answer = True, {"tracker": "ActivityWatch", "entries": [{"s": 540, "e": 600, "project": "p"}]}
        self.warm()
        self.assertEqual(self.runs, [self.yesterday, self.yesterday])
        self.assertEqual(learn._stored(self.yesterday), [{"k": [], "p": "p", "s": 540, "e": 600}])
        self.warm()
        self.assertEqual(len(self.runs), 2)  # then kept

    def test_a_day_with_no_activity_while_a_tracker_answers_is_kept(self):
        self.up = True
        self.warm()
        self.warm()
        self.assertEqual(self.runs, [self.yesterday])
        self.assertEqual(learn._stored(self.yesterday), [])


class LikelyTrackerTest(unittest.TestCase):
    """The Likely column of a past day worked out while no tracker answered is not final either: it is never kept on
    disk, it is worked out again once a tracker answers, and learning never takes it from memory."""

    def setUp(self):
        import shutil
        self.up, self.runs = False, []

        def built_in(date, settings):
            self.runs.append(date)
            return ({"tracker": "ActivityWatch", "entries": [{"s": 540, "e": 720, "minutes": 180, "project": "p"}]}
                    if self.up else {"tracker": "no tracker", "entries": [{"s": 540, "e": 570, "minutes": 30, "project": "p"}]})
        for p in (mock.patch.object(likely, "own_script", lambda: False), mock.patch.object(likely, "_built_in", built_in),
                  mock.patch.object(likely, "_with_tasks", lambda *a, **k: None),
                  mock.patch("pulsar.trackers.answering", lambda url: self.up)):
            p.start()
            self.addCleanup(p.stop)
        for c in (likely._cache, likely._hints):
            c.clear()
            self.addCleanup(c.clear)
        shutil.rmtree(likely.DAYS_DIR, ignore_errors=True)
        self.addCleanup(shutil.rmtree, likely.DAYS_DIR, True)

    def ask(self, keep=True):
        return likely.proposal(DATE, "UTC", "2026-11-06", False, dict(S), keep=keep)

    def test_a_day_seen_while_no_tracker_answered_is_worked_out_again(self):
        self.assertEqual(self.ask()["tracker"], "no tracker")
        self.assertEqual(self.ask()["tracker"], "no tracker")  # reused for a while, as today is
        self.assertEqual(len(self.runs), 1)
        self.assertFalse(os.path.exists(os.path.join(likely.DAYS_DIR, f"{DATE}.json")))  # never kept on disk
        self.up = True
        self.assertEqual(self.ask(keep=False)["entries"][0]["minutes"], 180)  # learning: never the copy in memory
        likely._cache.clear()  # a restart, the tracker up
        self.assertEqual(self.ask()["tracker"], "ActivityWatch")
        self.assertEqual(len(self.runs), 3)
        now = time.time()
        self.up = False
        self.ask()  # a day seen by a tracker is final: kept, never worked out again
        with mock.patch.object(likely.time, "time", lambda: now + 3600):
            self.ask()
        self.assertEqual(len(self.runs), 3)
        for _ in range(100):
            if os.path.exists(os.path.join(likely.DAYS_DIR, f"{DATE}.json")):
                break
            time.sleep(0.02)
        self.assertTrue(os.path.exists(os.path.join(likely.DAYS_DIR, f"{DATE}.json")))

    def test_after_a_while_it_is_worked_out_again_in_memory_too(self):
        now = time.time()
        self.ask()
        self.up = True
        with mock.patch.object(likely.time, "time", lambda: now + likely.TODAY_FOR + 1):
            self.assertEqual(self.ask()["tracker"], "ActivityWatch")
        self.assertEqual(len(self.runs), 2)


if __name__ == "__main__":
    unittest.main()
