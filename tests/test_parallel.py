"""The Report's parallel work (pulsar/parallel.py): agents in the background while the work in front was another
repository's, by project, and what the entries leave not logged."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import parallel  # noqa: E402

T0 = 1_800_000_000.0


def row(s, e, project, kind="focused"):
    return {"s": s, "e": e, "minutes": e - s, "project": project, "kind": kind}


class ParallelTest(unittest.TestCase):
    def day(self):
        # 09:00-11:00 the tool in front; the client's agent ran 09:30-10:30 in the background (60 minutes in parallel)
        return {"entries": [row(540, 660, "house-tool"), row(660, 720, "client-web")],
                "background": [row(570, 630, "client-web", "background"), row(570, 600, "house-tool", "background")]}

    def summary(self, rows, kept=True):
        return parallel.summary(["2026-11-10"], rows, lambda d: self.day() if kept else None, lambda d: T0)

    def test_an_agent_beside_other_work_is_parallel_and_named_by_the_project_logged_in_front(self):
        rows = [{"date": "2026-11-10", "start": T0 + 660 * 60, "minutes": 60, "project": "Clientco"},
                {"date": "2026-11-10", "start": T0 + 540 * 60, "minutes": 120, "project": "FI-Tools"}]
        got = self.summary(rows)
        self.assertEqual((got["days"], got["of"]), (1, 1))
        [c] = [p for p in got["projects"] if p["name"] == "Clientco"]
        self.assertEqual((c["mapped"], c["parallel"], c["front"], c["logged"], c["notLogged"]), (True, 60, 60, 60, 60))
        # its own repository's agent beside its own work in front is not parallel
        self.assertFalse([p for p in got["projects"] if p["name"] == "FI-Tools"])

    def test_what_was_logged_beyond_the_work_in_front_covers_the_parallel_minutes(self):
        rows = [{"date": "2026-11-10", "start": T0 + 660 * 60, "minutes": 60, "project": "Clientco"},
                {"date": "2026-11-10", "start": T0 + 900 * 60, "minutes": 45, "project": "Clientco"}]  # parallel time moved
        [c] = [p for p in self.summary(rows)["projects"] if p["name"] == "Clientco"]
        self.assertEqual((c["logged"], c["notLogged"]), (105, 15))

    def test_a_repository_with_no_project_logged_in_front_keeps_its_own_name_and_a_day_not_kept_has_none(self):
        got = self.summary([])
        self.assertEqual([(p["name"], p["mapped"], p["notLogged"]) for p in got["projects"]], [("client-web", False, 60)])
        self.assertEqual(self.summary([], kept=False), {"days": 0, "of": 1, "parallel": 0, "notLogged": 0, "projects": []})


class KeepDaysTest(unittest.TestCase):
    def test_past_days_are_kept_as_the_setting_says_and_only_its_choices_are_taken(self):
        from pulsar import likely, server
        self.assertEqual(likely.keep_days({}), 31)
        self.assertEqual(likely.keep_days({"likely_keep_days": 92}), 92)
        self.assertEqual(likely.keep_days({"likely_keep_days": 40}), 31)
        self.assertEqual(server.validate({"likely_keep_days": "183"}), {"likely_keep_days": 183})
        with self.assertRaises(ValueError):
            server.validate({"likely_keep_days": 40})


class TaskStatusTest(unittest.TestCase):
    """productive.set_task_status, with the network stood in for: never Productive itself."""

    def test_the_token_sends_one_patch_of_the_tasks_status_and_blocked_writes_send_nothing(self):
        import json
        from unittest import mock
        from pulsar import config, productive
        sent = []

        def https(method, url, body=None, headers=None, **kw):
            sent.append((method, url, json.loads(body)))
            return 200, b"{}"
        with mock.patch.object(productive, "_write_conn", lambda s: {"kind": "token", "token": "t"}), \
                mock.patch.object(productive, "https", https):
            productive.set_task_status("55", "30", {"org_id": "1"})
        [(method, url, body)] = sent
        self.assertEqual((method, url.endswith("/tasks/55")), ("PATCH", True))
        self.assertEqual(body["data"]["relationships"]["workflow_status"]["data"], {"type": "workflow_statuses", "id": "30"})
        with mock.patch.object(config, "writes_blocked", lambda: "Writes are off here."):
            with self.assertRaisesRegex(productive.ProductiveError, "Writes are off"):
                productive.set_task_status("55", "30", {})

    def test_claude_codes_connection_loads_the_status_before_changing_it_and_only_a_clear_answer_counts(self):
        from unittest import mock
        from pulsar import productive
        calls = []

        def mcp(tool, args, token, session=None):
            calls.append(tool)
            return "Task updated (ID: 55)." if tool == "update_resource" else "{}"
        with mock.patch.object(productive, "_write_conn", lambda s: {"kind": "mcp", "token": "t"}), \
                mock.patch.object(productive, "mcp_session", lambda t: "s1"), mock.patch.object(productive, "mcp_text", mcp):
            productive.set_task_status("55", "30", {})
            self.assertEqual(calls, ["load_resource", "update_resource"])
        with mock.patch.object(productive, "_write_conn", lambda s: {"kind": "mcp", "token": "t"}), \
                mock.patch.object(productive, "mcp_session", lambda t: "s1"), \
                mock.patch.object(productive, "mcp_text", lambda *a, **k: "Invalid parameters"):
            with self.assertRaises(productive.ProductiveUnclear):
                productive.set_task_status("55", "30", {})


class TaskStatusesRouteTest(unittest.TestCase):
    def test_the_page_asks_with_the_date_every_such_route_needs_and_a_refusal_comes_back_in_words(self):
        """2026-11-13: the page asked /api/task/statuses without a date, and the route table refuses that."""
        from unittest import mock
        from pulsar import productive, server
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "js", "logday.js"),
                  encoding="utf-8") as fh:
            self.assertRegex(fh.read(), r"/api/task/statuses\?task=\$\{tid\}&date=\$\{LD\.date\}")

        def refuse(tid, s):
            raise productive.ProductiveError("Productive did not return the task (HTTP 404).")
        with mock.patch.object(server, "current_settings", lambda: {}), mock.patch.object(server.productive, "task_status", refuse):
            self.assertEqual(server.api_task_statuses({"task": "5", "date": "2026-11-13"}),
                             {"error": "Productive did not return the task (HTTP 404)."})


class TrackerFillTest(unittest.TestCase):
    """trackers.aw_gaps and both(): where ActivityWatch recorded nothing at all, MemTime's time (the owner, 2026-11-13)."""
    T0 = 1_800_000_000.0

    def afk(self, a, b, status="not-afk"):
        from datetime import datetime, timezone
        return {"timestamp": datetime.fromtimestamp(self.T0 + a * 60, timezone.utc).isoformat(), "duration": (b - a) * 60,
                "data": {"status": status}}

    def test_only_a_stretch_with_no_event_at_all_is_a_gap_and_the_one_up_to_now_only_past_the_timeout(self):
        from pulsar import trackers
        raw = {"window": [{}], "afk": [self.afk(0, 540, "afk"), self.afk(540, 600), self.afk(604, 660),
                                       self.afk(660, 700, "afk"), self.afk(760, 800)]}
        # 600-604 is under FILL_MIN; away (afk) is never a gap; 700-760 nothing recorded: a gap
        self.assertEqual(trackers.aw_gaps(raw, self.T0, self.T0 + 86400, 30, now=self.T0 + 820 * 60), [(700.0, 760.0)])
        # up to now: from the AFK timeout and TRAIL_GRACE only (a pause still going on is written later)
        self.assertEqual(trackers.aw_gaps(raw, self.T0, self.T0 + 86400, 30, now=self.T0 + 830 * 60), [(700.0, 760.0)])
        self.assertEqual(trackers.aw_gaps(raw, self.T0, self.T0 + 86400, 30, now=self.T0 + 840 * 60),
                         [(700.0, 760.0), (800.0, 840.0)])
        self.assertEqual(trackers.aw_gaps(None, self.T0, self.T0 + 86400, 30), [])

    def test_memtimes_items_fill_the_gaps_marked_and_the_setting_switches_it_off(self):
        from unittest import mock
        from pulsar import trackers
        raw = {"window": [{}], "afk": [self.afk(540, 600), self.afk(700, 800)]}
        aw = [{"s": 540.0, "e": 600.0, "app": "Cursor"}, {"s": 700.0, "e": 800.0, "app": "Cursor"}]
        mem = [{"s": 530.0, "e": 720.0, "app": "Safari"}]
        with mock.patch.object(trackers, "activitywatch_raw", lambda *a: raw), \
                mock.patch.object(trackers, "activity_aw", lambda *a, **k: list(aw)), \
                mock.patch.object(trackers, "activity_memtime", lambda *a: list(mem)), \
                mock.patch.object(trackers.time, "time", lambda: self.T0 + 900 * 60):
            got = trackers.both(self.T0, self.T0 + 86400, "http://aw", None, 30, fill=True)
            off = trackers.both(self.T0, self.T0 + 86400, "http://aw", None, 30, fill=False)
        self.assertEqual([(x["s"], x["e"], x.get("fill")) for x in got["aw"]],
                         [(530.0, 540.0, "memtime"), (540.0, 600.0, None), (600.0, 700.0, "memtime"), (700.0, 800.0, None)])
        self.assertEqual(off["aw"], aw)
        self.assertEqual(got["memtime"], mem)

    def test_on_by_default_and_only_true_or_false(self):
        from pulsar import config, server
        self.assertIs(config.DEFAULTS["tracker_fill"], True)
        self.assertEqual(server.validate({"tracker_fill": False}), {"tracker_fill": False})
        with self.assertRaises(ValueError):
            server.validate({"tracker_fill": 1})


class BookingsTest(unittest.TestCase):
    """productive.bookings: the day's Resource Planner bookings, read with the network stood in for."""

    def test_each_way_of_booking_comes_to_its_minutes_that_day(self):
        import json
        from unittest import mock
        from pulsar import productive
        cols = ["type", "id", "started_on", "ended_on", "time", "booking_method", "percentage", "total_time", "is_draft",
                "note", "task", "service", "event"]
        rows = [["bookings", "1", "2026-11-11", "2026-11-12", None, "Total hours", None, "1 h", False, None, "tasks:7", "services:8", None],
                ["bookings", "2", "2026-11-10", "2026-11-14", "2 h", "Per day", None, None, True, "pairing", "tasks:9", "services:8", None],
                ["bookings", "3", "2026-11-11", "2026-11-11", None, "Percentage", 25, None, False, None, None, "services:8", None],
                ["bookings", "4", "2026-11-11", "2026-11-11", None, "Per day", None, None, False, None, None, None, "events:5"]]
        inc = {"columns": ["type", "id", "title", "name"],
               "rows": [["tasks", "7", "SEO audit", None], ["tasks", "9", "Pairing", None], ["services", "8", None, "Support"],
                        ["events", "5", None, "Vacation"]]}
        doc = json.dumps({"items": {"columns": cols, "rows": rows}, "included": inc})
        with mock.patch.object(productive, "_need", lambda s: {"kind": "mcp", "token": "t"}), \
                mock.patch.object(productive, "mcp_text", lambda *a, **k: doc), \
                mock.patch.object(productive, "parse_mcp", lambda text: json.loads(text)):
            got = productive.bookings("2026-11-11", {"person_id": "1", "target_hours": 8})
        by = {x["id"]: x for x in got}
        self.assertEqual((by["1"]["minutes"], by["1"]["title"], by["1"]["taskId"]), (30, "SEO audit", "7"))  # 1 h over 2 days
        self.assertEqual((by["2"]["minutes"], by["2"]["draft"], by["2"]["note"]), (120, True, "pairing"))
        self.assertEqual((by["3"]["minutes"], by["3"]["title"]), (120, "Support"))  # 25% of 8 h
        self.assertEqual((by["4"]["kind"], by["4"]["title"], by["4"]["minutes"]), ("absence", "Vacation", 480))
        self.assertEqual(got[0]["kind"], "absence")  # absences first

    def test_the_column_is_on_by_default_and_switched_off_it_never_asks_productive(self):
        from unittest import mock
        from pulsar import config, productive, server
        self.assertIs(config.DEFAULTS["planned"], True)
        self.assertEqual(server.validate({"planned": False}), {"planned": False})
        with self.assertRaises(ValueError):
            server.validate({"planned": "no"})

        def asked(*a, **k):
            raise AssertionError("Productive was asked")
        with mock.patch.object(server, "current_settings", lambda: {"planned": False}), \
                mock.patch.object(productive, "bookings", asked):
            self.assertEqual(server.api_bookings({"date": "2026-11-13"}), {"bookings": [], "off": True})


if __name__ == "__main__":
    unittest.main()
