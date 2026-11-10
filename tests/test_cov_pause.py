"""Pause (pause.py) beyond tests/test_pause.py: the page's routes from Pause to Undo, what the file and the routes refuse,
pauses over several days, the question's wording, and the pause counted as not work in Log the day's sources and the
Claude Code tools (mcptools.pulsar_day, pulsar_status, mcp.pulsar_status). On temporary files and fake trackers.

    python3 -m unittest discover -s tests
"""

import json
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH, refused  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import daymarks, dayplan, mcp, mcptools, pause, server, trackers  # noqa: E402
from test_away import saved  # noqa: E402
from test_pause import KYIV, Files, at, item  # noqa: E402

DAY = "2026-11-10"


def clock(t):
    return mock.patch.object(server.time, "time", lambda: t)  # time.time, for pause.py and server.py alike


class RoutesTest(Files):
    """server.api_pause_change, as the page calls it: pause, keep it on, resume from a time, and Undo."""

    def change(self, body, now):
        with saved({"timezone": "Europe/Kyiv"}), clock(now):
            return server.api_pause_change(body)

    def test_pause_stay_resume_from_a_time_and_undo(self):
        daymarks.change(DAY, add=[600, 620])  # marked by hand earlier in the day
        got = self.change({"on": True}, at(DAY, "13:00"))
        self.assertEqual((got["paused"], got["since"], got["sinceText"], got["marked"]), (True, at(DAY, "13:00"), "13:00", {}))
        self.change({"stay": True}, at(DAY, "14:30"))
        self.assertEqual(pause.state()["asked"], at(DAY, "14:30"))
        got = self.change({"on": False, "until": at(DAY, "14:05")}, at(DAY, "15:00"))
        self.assertEqual((got["paused"], got["marked"], got["was"], got["resumedSince"]),
                         (False, {DAY: [780, 845]}, {DAY: [[600, 620]]}, at(DAY, "13:00")))
        self.assertEqual(daymarks.get(DAY)["notWork"], [[600, 620], [780, 845]])
        got = self.change({"undo": {"since": got["resumedSince"], "marked": got["marked"], "was": got["was"]}},
                          at(DAY, "15:01"))
        self.assertEqual((got["paused"], got["since"]), (True, at(DAY, "13:00")))
        self.assertEqual(daymarks.get(DAY)["notWork"], [[600, 620]])  # the hand's mark stays, the pause's is gone
        self.assertIsNone(pause.state()["asked"])  # paused again: asked again after a whole stretch

    # fixed 2026-11-10: server.api_pause_change {on: false} when not paused raises KeyError 'was' (HTTP 500)
    def test_resume_when_not_paused_says_so(self):
        # a second click on Resume, or Resume from a window opened before the pause ended elsewhere: pause.stop answers
        # {"paused": False, "marked": {}} with no "was" nor "resumedSince", and the route reads both
        got = self.change({"on": False}, at(DAY, "15:00"))
        self.assertEqual((got["paused"], got["marked"]), (False, {}))

    def test_what_the_routes_refuse(self):
        pause.start(now=at(DAY, "13:00"))
        for bad in ({"on": False, "until": True}, {"on": False, "until": [1]}, {"undo": {"since": True}},
                    {"undo": {"since": "13:00"}}, {"on": 1}, {"stay": "yes"}):
            with self.assertRaises(ValueError, msg=bad):
                self.change(bad, at(DAY, "15:00"))
        self.assertEqual(pause.state()["since"], at(DAY, "13:00"))  # still paused, nothing marked
        self.assertEqual(daymarks.get(DAY).get("notWork") or [], [])

    def test_the_new_routes_need_the_page_not_the_key(self):
        for path in ("/api/pause", "/api/trackers/open"):
            self.assertEqual(server.key_refusal(path, {}), server.KEY_ONLY, path)


class FileTest(Files):
    def write(self, doc):
        with open(pause.PATH, "w") as fh:
            json.dump(doc, fh)

    def test_what_is_not_a_pause_in_the_file(self):
        for doc in ({"since": True}, [1, 2], {"asked": 5}, {"since": None}):
            self.write(doc)
            self.assertFalse(pause.state()["paused"], doc)
        self.write({"since": 100.0, "asked": "soon"})  # a broken "asked" is dropped, the pause stays
        self.assertEqual(pause.state(), {"paused": True, "since": 100.0, "asked": None})

    def test_stay_when_not_paused_writes_nothing(self):
        self.assertFalse(pause.stay(now=at(DAY, "13:00"))["paused"])
        self.assertFalse(os.path.exists(pause.PATH))

    def test_a_pause_over_three_days_marks_the_middle_day_whole(self):
        pause.start(now=at("2026-11-08", "22:00"))
        got = pause.stop(KYIV, now=at(DAY, "01:00"))
        self.assertEqual(got["marked"], {"2026-11-08": [1320, 1440], "2026-11-09": [0, 1440], DAY: [0, 60]})
        self.assertEqual(daymarks.get("2026-11-09")["notWork"], [[0, 1440]])

    def test_under_a_minute_marks_nothing_and_still_resumes(self):
        pause.start(now=at(DAY, "13:00"))
        got = pause.stop(KYIV, now=at(DAY, "13:00") + 40)
        self.assertEqual((got["paused"], got["marked"], got["was"]), (False, {}, {}))
        self.assertFalse(os.path.exists(pause.PATH))

    def test_part_minutes_are_marked_whole(self):
        pause.start(now=at(DAY, "13:10") + 30)
        self.assertEqual(pause.stop(KYIV, now=at(DAY, "13:20") + 10)["marked"], {DAY: [790, 801]})

    def test_undo_refuses_a_start_too_old_or_to_come_and_skips_what_it_cannot_read(self):
        now = at(DAY, "15:00")
        for since in (now - 9 * 86400, now + 60, True, None):
            with self.assertRaises(ValueError, msg=since):
                pause.restore(since, {}, {}, now=now)
        self.assertFalse(pause.state()["paused"])
        daymarks.change(DAY, add=[600, 620])
        got = pause.restore(at(DAY, "13:00"), {"not-a-date": [1, 2], DAY: [50, 10], "2026-11-09": "x"}, "x", now=now)
        self.assertEqual((got["paused"], got["since"]), (True, at(DAY, "13:00")))
        self.assertEqual(daymarks.get(DAY)["notWork"], [[600, 620]])  # nothing taken off for a span that is not one


class AskWordingTest(Files):
    def ask(self, now, items, how="1"):
        with saved({"timezone": "Europe/Kyiv", "away_after": 15, "advanced": {"pause_ask": 60}}), clock(now), \
                mock.patch.object(trackers, "both", lambda *a, **k: {"aw": items, "memtime": []}):
            return server.api_pause({"ask": how} if how else {})

    def test_a_pause_from_another_day_names_the_day(self):
        pause.start(now=at("2026-11-09", "17:40"))
        got = self.ask(at(DAY, "09:00"), [], how=None)
        self.assertEqual((got["sinceText"], got["minutes"]), ("Mon 17:40", 920))
        self.assertNotIn("ask", got)  # not asked for: the page only shows the pause

    def test_work_from_the_moment_of_the_pause_asks_without_a_time_you_came_back(self):
        pause.start(now=at(DAY, "13:00"))
        got = self.ask(at(DAY, "14:30"), [item(0, 40), item(45, 80)])
        self.assertTrue(got["ask"])
        self.assertEqual(got["workMinutes"], 75)
        self.assertNotIn("back", got)

    def test_personal_windows_are_not_work_seen(self):
        pause.start(now=at(DAY, "13:00"))
        got = self.ask(at(DAY, "14:30"), [item(5, 8), item(10, 80, cat="personal")])
        self.assertEqual((got["ask"], got["workMinutes"]), (False, 3))


class CountedAsNotWorkTest(Files):
    def test_an_open_pause_is_left_out_of_the_plan_but_what_began_before_stays(self):
        pause.start(now=datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc).timestamp())  # still on: 10:00 to the end of the day
        seen = {}
        s = {"timezone": "UTC", "person_id": "1", "rules": [], "person_name": ""}
        zoom = lambda a, b: {**item(a, b), "app": "zoom.us", "title": "Zoom Meeting"}  # noqa: E731
        meeting = {"s": 590, "e": 640, "title": "Call", "all_day": False}  # began before the pause: stays
        repeats = [{"s": 590, "e": 610}, {"s": 615, "e": 630}]           # the second wholly inside it: left out
        with mock.patch.object(server, "current_settings", lambda: s), \
                mock.patch.object(server.config, "tz", lambda s: timezone.utc), \
                mock.patch.object(server, "api_activity", lambda q: {"items": [item(540, 720), zoom(560, 580), zoom(620, 650)],
                                                                     "away": []}), \
                mock.patch.object(server, "productive_rows", lambda d0, d1, s: ([], None, "token", False)), \
                mock.patch.object(server, "history_rows", lambda d, s, days=28: ([], None)), \
                mock.patch.object(server, "api_repeats", lambda q: {"repeats": repeats}), \
                mock.patch.object(server, "api_suggest", lambda q: {"recent": [], "assigned": []}), \
                mock.patch.object(dayplan.config, "tz", lambda s: timezone.utc), \
                mock.patch.object(dayplan, "plan", lambda day, ctx, opts, **kw: seen.update(day) or {"entries": []}), \
                mock.patch("pulsar.likely.enabled", lambda s: False), \
                mock.patch("pulsar.calendar.day", lambda d, s, fresh=False: {"events": [meeting], "connected": True}), \
                mock.patch("pulsar.calendar.history", lambda f, n, s: []):
            dayplan.gather("2026-11-04")
        self.assertEqual(max(x["e"] for x in seen["items"]), 600)
        self.assertEqual([(m["s"], m["e"]) for m in seen["meetings"]], [(590, 640)])
        self.assertEqual(seen["repeats"], [{"s": 590, "e": 610}])
        self.assertEqual(seen["calls"], [{"s": 560, "e": 580}])  # a call inside the pause is no call

    def test_claude_codes_day_counts_not_work_as_neither_logged_nor_not_logged(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daymarks.change(today, add=[600, 660])
        got = {"entries": {"entries": [{"s": 540, "e": 570, "minutes": 30, "taskId": "11", "billable": True}]},
               "activity": {"items": [item(540, 720)], "away": [], "source": "aw"}, "likely": {"entries": []},
               "calendar": {"events": []}, "repeats": {"repeats": []}}
        with saved({"timezone": "UTC"}), mock.patch.object(mcptools, "_config", lambda: {"timezone": "UTC"}), \
                mock.patch.object(mcptools, "fetch", lambda calls: {k: got[k] for k in calls}):
            day = mcptools.pulsar_day({"date": "today"})
        self.assertEqual(day["notWork"], [{"start": "10:00", "end": "11:00"}])
        self.assertEqual((day["totals"]["notWorkMinutes"], day["totals"]["activeNotLoggedMinutes"]), (60, 90))
        self.assertEqual([(u["start"], u["end"]) for u in day["unlogged"]], [("09:30", "10:00"), ("11:00", "12:00")])

    def test_the_status_tools_say_since_when_pulsar_is_paused(self):
        cfg = {"timezone": "Europe/Kyiv", "version": "9.9.9"}
        with mock.patch.object(mcptools, "_config", lambda: cfg):
            self.assertIsNone(mcptools.pulsar_status({})["pausedSince"])
            pause.start(now=at(DAY, "13:10"))
            self.assertEqual(mcptools.pulsar_status({})["pausedSince"], f"{DAY} 13:10")
        answer = {"version": "9.9.9", "date": DAY, "day": "Tue", "time": "14:00", "timezone": "Europe/Kyiv"}
        with mock.patch.object(mcp, "ask", lambda name, args, client: {**answer, "pausedSince": f"{DAY} 13:10:59"}):
            self.assertEqual(mcp.pulsar_status({}, "claude-code")["pausedSince"], f"{DAY} 13:10")
        with mock.patch.object(mcp, "ask", lambda name, args, client: {**answer, "pausedSince": None}):
            self.assertNotIn("pausedSince", mcp.pulsar_status({}, "claude-code"))


if __name__ == "__main__":
    unittest.main()
