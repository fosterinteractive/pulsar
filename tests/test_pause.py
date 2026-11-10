"""Pause (pause.py): the time from Pause to Resume is not work, written to each day's marks; the open pause counts as not
work in the Report, Log the day's plan and the Claude Code tools; and when Pulsar asks whether a long pause is still
on. On temporary files and fake trackers.

    python3 -m unittest discover -s tests
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH, refused  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import daymarks, dayplan, pause, report, server, trackers  # noqa: E402
from test_away import saved  # noqa: E402

KYIV = ZoneInfo("Europe/Kyiv")


def at(day: str, hm: str, tz=KYIV) -> float:
    return datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M").replace(tzinfo=tz).timestamp()


def item(s, e, cat="work"):
    return {"s": s, "e": e, "app": "Cursor", "title": "Task", "host": "", "cat": cat, "icon": None}


class Files(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp()
        for p in (mock.patch.object(pause, "PATH", os.path.join(folder, "pause.json")),
                  mock.patch.object(daymarks, "PATH", os.path.join(folder, "day-marks.json"))):
            p.start()
            self.addCleanup(p.stop)


class PauseTest(Files):
    def test_resume_marks_the_pause_not_work(self):
        pause.start(now=at("2026-11-10", "13:10"))
        self.assertTrue(pause.state()["paused"])
        pause.start(now=at("2026-11-10", "13:40"))  # already paused: it goes on from when it began
        self.assertEqual(pause.state()["since"], at("2026-11-10", "13:10"))
        got = pause.stop(KYIV, now=at("2026-11-10", "14:30"))
        self.assertEqual(got["marked"], {"2026-11-10": [790, 870]})
        self.assertFalse(got["paused"])
        self.assertEqual(daymarks.get("2026-11-10")["notWork"], [[790, 870]])
        self.assertFalse(os.path.exists(pause.PATH))

    def test_resume_from_when_you_came_back(self):
        pause.start(now=at("2026-11-10", "13:10"))
        got = pause.stop(KYIV, until=at("2026-11-10", "14:05"), now=at("2026-11-10", "16:00"))
        self.assertEqual(got["marked"], {"2026-11-10": [790, 845]})
        pause.start(now=at("2026-11-10", "17:00"))  # a time before the pause or after now is held to them
        self.assertEqual(pause.stop(KYIV, until=at("2026-11-10", "18:00"), now=at("2026-11-10", "17:30"))["marked"],
                         {"2026-11-10": [1020, 1050]})
        pause.start(now=at("2026-11-10", "19:00"))
        self.assertEqual(pause.stop(KYIV, until=at("2026-11-10", "18:00"), now=at("2026-11-10", "19:30"))["marked"], {})

    def test_a_pause_over_midnight_marks_both_days(self):
        pause.start(now=at("2026-11-09", "23:30"))
        self.assertEqual(pause.not_work("2026-11-09", KYIV, now=at("2026-11-10", "01:00")), [[1410, 1440]])
        self.assertEqual(pause.not_work("2026-11-10", KYIV, now=at("2026-11-10", "01:00")), [[0, 60]])
        self.assertEqual(pause.stop(KYIV, now=at("2026-11-10", "01:00"))["marked"],
                         {"2026-11-09": [1410, 1440], "2026-11-10": [0, 60]})

    def test_the_open_pause_joins_the_days_marks(self):
        daymarks.change("2026-11-10", add=[600, 620])
        pause.start(now=at("2026-11-10", "13:00"))
        self.assertEqual(pause.not_work("2026-11-10", KYIV, now=at("2026-11-10", "13:30")), [[600, 620], [780, 810]])
        self.assertEqual(pause.not_work("2026-11-11", KYIV, now=at("2026-11-10", "13:30")), [])

    def test_elapsed_minutes_on_the_day_the_clocks_go_back(self):
        t0 = at("2026-10-25", "00:00")
        self.assertEqual(pause.elapsed("2026-10-25", [[240, 300]], KYIV, t0), [(300.0, 360.0)])  # 04:00 is 5 h in
        self.assertEqual(pause.elapsed("2026-11-10", [[240, 1440]], KYIV, at("2026-11-10", "00:00")), [(240.0, 1440.0)])

    def test_a_broken_file_is_no_pause(self):
        with open(pause.PATH, "w") as fh:
            fh.write('{"since": "yesterday"}')
        self.assertFalse(pause.state()["paused"])


class AskTest(Files):
    """api_pause: asked when paused long enough with work seen, from when the work began again."""

    def ask(self, now, items, setting=60, how="1"):
        found = {"aw": items, "memtime": []}
        with saved({"timezone": "Europe/Kyiv", "away_after": 15, "advanced": {"pause_ask": setting}}), \
                mock.patch.object(server.time, "time", lambda: now), \
                mock.patch.object(trackers, "both", lambda *a, **k: found):
            return server.api_pause({"ask": how})

    def test_asked_with_the_time_you_came_back(self):
        pause.start(now=at("2026-11-10", "13:00"))
        # items in minutes from the pause: a short look at 13:20, then work from 14:05 (65 min in)
        work = [item(20, 22), item(65, 90), item(95, 120)]
        got = self.ask(at("2026-11-10", "15:00"), work)
        self.assertTrue(got["ask"])
        self.assertEqual(got["back"], at("2026-11-10", "14:05"))
        self.assertEqual((got["sinceText"], got["backText"], got["minutes"]), ("13:00", "14:05", 120))

    def test_not_asked_too_soon_without_work_or_when_off(self):
        pause.start(now=at("2026-11-10", "13:00"))
        work = [item(5, 40)]
        self.assertFalse(self.ask(at("2026-11-10", "13:45"), work)["ask"])               # under an hour
        self.assertTrue(self.ask(at("2026-11-10", "13:45"), work, setting=30)["ask"])    # Settings: 30 minutes
        self.assertFalse(self.ask(at("2026-11-10", "15:00"), [item(5, 10)])["ask"])      # 5 minutes of work
        self.assertFalse(self.ask(at("2026-11-10", "15:00"), [item(5, 60, cat="personal")])["ask"])
        self.assertFalse(self.ask(at("2026-11-10", "18:00"), work, setting=0)["ask"])    # Never
        self.assertTrue(self.ask(at("2026-11-10", "13:20"), [item(5, 19)], how="2")["ask"])  # Resume clicked: from when

    def test_stay_paused_asks_again_after_another_stretch(self):
        pause.start(now=at("2026-11-10", "13:00"))
        pause.stay(now=at("2026-11-10", "14:30"))
        work = [item(30, 200)]
        self.assertFalse(self.ask(at("2026-11-10", "15:00"), work)["ask"])
        self.assertTrue(self.ask(at("2026-11-10", "15:31"), work)["ask"])

    def test_not_paused_says_so(self):
        self.assertEqual(server.api_pause({"ask": "1"}), {"paused": False})

    def test_the_route_checks_what_it_is_given(self):
        for bad in ({}, {"on": "yes"}, {"on": False, "until": "14:05"}):
            with self.assertRaises(ValueError):
                server.api_pause_change(bad)


class CountedNowhereTest(Files):
    def test_the_report_does_not_call_not_work_not_logged(self):
        t0 = datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp()
        items = [item(600, 720)]
        plain = report.build(["2026-11-02"], [(t0, t0 + 86400)], {"aw": items}, [], "2026-11-02")
        marked = report.build(["2026-11-02"], [(t0, t0 + 86400)], {"aw": items}, [], "2026-11-02",
                              not_work={"2026-11-02": [(630, 690)]})
        self.assertEqual((plain["days"][0]["notLogged"], marked["days"][0]["notLogged"]), (120, 60))
        self.assertEqual(marked["days"][0]["active"], 120)  # still active: only not counted as not logged

    def test_the_plan_places_nothing_in_a_pause(self):
        daymarks.change("2026-11-04", add=[600, 660])
        seen = {}
        s = {"timezone": "UTC", "person_id": "1", "rules": [], "person_name": ""}
        meeting = {"s": 610, "e": 640, "title": "Call", "all_day": False}
        with mock.patch.object(server, "current_settings", lambda: s), \
                mock.patch.object(server.config, "tz", lambda s: timezone.utc), \
                mock.patch.object(server, "api_activity", lambda q: {"items": [item(540, 720)], "away": []}), \
                mock.patch.object(server, "productive_rows", lambda d0, d1, s: ([], None, "token", False)), \
                mock.patch.object(server, "history_rows", lambda d, s, days=28: ([], None)), \
                mock.patch.object(server, "api_repeats", lambda q: {"repeats": [{"s": 615, "e": 630}]}), \
                mock.patch.object(server, "api_suggest", lambda q: {"recent": [], "assigned": []}), \
                mock.patch.object(dayplan.config, "tz", lambda s: timezone.utc), \
                mock.patch.object(dayplan, "plan", lambda day, ctx, opts, **kw: seen.update(day) or {"entries": []}), \
                mock.patch("pulsar.likely.enabled", lambda s: False), \
                mock.patch("pulsar.calendar.day", lambda d, s, fresh=False: {"events": [meeting], "connected": True}), \
                mock.patch("pulsar.calendar.history", lambda f, n, s: []):
            dayplan.gather("2026-11-04")
        self.assertEqual([(x["s"], x["e"]) for x in seen["items"]], [(540, 600), (660, 720)])
        self.assertEqual((seen["meetings"], seen["repeats"]), ([], []))


if __name__ == "__main__":
    unittest.main()
