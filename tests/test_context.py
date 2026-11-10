"""Tasks for work outside Claude Code from the window in front (context.py, dayplan._context), on made-up days.

    python3 -m unittest discover -s tests
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import context, dayplan  # noqa: E402

DATE = "2026-11-04"


def t0_of(d):
    return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def slack(s, e, conv="#team-ops"):
    return {"s": s, "e": e, "app": "Slack", "title": f"{conv} - Example Co - Slack", "host": "", "url": "", "cat": "work"}


def page(s, e, path="/board/42", host="tracker.example.com", title="Board"):
    return {"s": s, "e": e, "app": "Google Chrome", "title": title, "host": host, "url": f"https://{host}{path}",
            "cat": "work"}


def doc(s, e, name="Budget plan"):
    return {"s": s, "e": e, "app": "Numbers", "title": name, "host": "", "url": "", "cat": "work"}


def zoom(s, e, title="Zoom Meeting"):
    return {"s": s, "e": e, "app": "zoom.us", "title": title, "host": "", "url": "", "cat": "work"}


def history(windows: dict, days=6, minutes=60, billable=False):
    """Past days: each window in front 9:00-10:00 (then 10:30-11:30, ...) while its task was logged then."""
    hist, kept = [], {}
    for i in range(1, days + 1):
        d = f"2026-11-{4 - i:02d}" if i < 4 else f"2026-10-{31 - (i - 4):02d}"
        items = []
        for j, (task, make) in enumerate(windows.items()):
            s = 540 + j * 90
            items.append(make(s, s + minutes))
            hist.append({"date": d, "start": t0_of(d) + s * 60, "minutes": minutes, "taskId": task, "task": f"Task {task}",
                         "project": "Client X" if billable else "Internal", "billable": billable})
        kept[d] = context.day_rows(items)
    return hist, kept


def plan(items, hist, kept, rows=()):
    ctx = dayplan.learn(DATE, hist, {}, t0_of, context_days=kept)
    day = {"items": items, "away": [], "rows": list(rows), "logged": [], "meetings": [], "repeats": [], "calls": [],
           "rules": [], "assigned": [], "picks": {}}
    return dayplan.plan(day, ctx)


class KeysTest(unittest.TestCase):
    def test_keys_are_hashed_and_a_tickets_number_does_not_matter(self):
        a = context.keys(page(0, 1, "/projects/12/tasks/345"))
        b = context.keys(page(0, 1, "/projects/12/tasks/678"))
        self.assertEqual(a, b)
        self.assertTrue(all(len(k) == 16 and "tracker" not in k for k in a))
        self.assertEqual(len(context.keys(slack(0, 1))), 1)  # a Slack conversation is one key
        self.assertNotEqual(context.keys(slack(0, 1)), context.keys(slack(0, 1, "#other")))

    def test_a_day_keeps_hashes_only(self):
        kept = context.day_rows([doc(0, 30, "Secret plan"), slack(30, 40)])
        self.assertEqual([r[:2] for r in kept["runs"]], [[0, 30], [30, 40]])
        self.assertNotIn("secret", json.dumps(kept).lower())

    def test_votes_and_pick(self):
        hist, kept = history({"7": doc, "8": slack})
        learned = context.votes(kept, hist, t0_of, DATE)
        self.assertEqual(context.pick(context.keys(doc(0, 1)), learned)[0], "7")
        self.assertEqual(context.pick(context.keys(slack(0, 1)), learned)[0], "8")
        other = {**doc(0, 1, "Notes"), "app": "Preview"}
        self.assertIsNone(context.pick(context.keys(other), learned))  # never seen
        # an app used for one task only names it, whatever the document: its least precise key
        self.assertEqual(context.pick(context.keys(doc(0, 1, "Another file")), learned)[0], "7")
        # two documents of one app: each names its own task (the most precise key first)
        hist, kept = history({"7": doc, "9": lambda s, e: doc(s, e, "Hiring plan")})
        learned = context.votes(kept, hist, t0_of, DATE)
        self.assertEqual(context.pick(context.keys(doc(0, 1, "Hiring plan")), learned)[0], "9")
        self.assertEqual(context.pick(context.keys(doc(0, 1)), learned)[0], "7")
        # too little seen: nothing
        hist, kept = history({"7": doc}, days=1, minutes=3)
        self.assertIsNone(context.pick(context.keys(doc(0, 1)), context.votes(kept, hist, t0_of, DATE)))

    def test_a_day_the_clocks_change_votes_by_the_clock(self):
        # the activity is by the clock (server.clock_spans), an entry's start is a moment: the autumn day has 25 hours,
        # so 15:00 is 960 minutes after midnight, and the window in front at 15:00 is at minute 900 of the clock
        tz, day = ZoneInfo("Europe/Kyiv"), "2026-10-25"
        t0 = lambda d: datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=tz).timestamp()  # noqa: E731
        at = lambda hour: datetime(2026, 10, 25, hour, tzinfo=tz).timestamp()  # noqa: E731
        kept = {day: context.day_rows([doc(900, 960), slack(960, 1020)])}
        hist = [{"date": day, "start": at(15), "minutes": 60, "taskId": "7"},
                {"date": day, "start": at(16), "minutes": 60, "taskId": "8"}]
        learned = context.votes(kept, hist, t0, "2026-10-26", tz=tz)
        self.assertEqual(context.pick(context.keys(doc(0, 1)), learned)[0], "7")
        self.assertEqual(context.pick(context.keys(slack(0, 1)), learned)[0], "8")

    def test_a_call_window_names_nothing_and_teaches_nothing(self):
        # a call is the meetings' (dayplan._meetings): the daily meeting logged in Zoom never names the desk work after
        # Zoom's own window was left in front
        hist, kept = history({"5": zoom})
        learned = context.votes(kept, hist, t0_of, DATE)
        self.assertEqual(context.keys(zoom(0, 1)), [])
        self.assertIsNone(context.pick(context.keys(zoom(0, 1, "Zoom Workplace")), learned))
        p = plan([zoom(600, 610, "Zoom Workplace"), doc(610, 700, "Notes")], hist, kept)
        self.assertEqual({e["taskId"] for e in p["entries"]}, {None})

    def test_a_window_used_for_two_tasks_alike_names_neither(self):
        hist, kept = history({"7": doc})
        hist += [{**r, "taskId": "9"} for r in hist]  # the same minutes logged to another task as well
        self.assertIsNone(context.pick(context.keys(doc(0, 1)), context.votes(kept, hist, t0_of, DATE)))


class WithoutClaudeTest(unittest.TestCase):
    """No Claude Code: no Likely rows, only the tracker and the person's own history."""

    def test_work_outside_claude_takes_the_task_of_its_window_unticked(self):
        hist, kept = history({"7": doc, "8": slack})
        p = plan([doc(600, 640), slack(640, 680)], hist, kept)
        got = [(e["s"], e["e"], e["taskId"], e["source"], e["checked"]) for e in p["entries"]]
        self.assertEqual(got, [(600, 640, "7", "context", False), (640, 680, "8", "context", False)])
        # the share of your time there, never faded minutes read as minutes (6 days of 60 minutes read "258 min")
        self.assertEqual(p["entries"][0]["why"], "you logged it before while this window was in front (100% of the time)")

    def test_the_task_of_the_days_meeting_is_never_named_by_a_window(self):
        # the daily meeting's channel read before the meeting named the desk work around it the meeting's task, then
        # joined it to the meeting (replay, no sessions: 0 of 1,096 minutes right); the meeting keeps its own task
        hist, kept = history({"5": lambda s, e: slack(s, e, "#standup")})
        meeting = {"s": 990, "e": 1050, "title": "Standup", "uid": "m1", "declined": False, "all_day": False,
                   "tasks": [{"taskId": "5", "title": "Task 5", "project": "Internal", "n": 9, "of": 10}]}
        ctx = dayplan.learn(DATE, hist, {}, t0_of, context_days=kept)
        day = {"items": [slack(780, 790, "#standup"), doc(790, 990, "Notes"), zoom(990, 1050)], "away": [], "rows": [],
               "logged": [], "meetings": [meeting], "repeats": [], "calls": [{"s": 990, "e": 1050}], "rules": [],
               "assigned": [], "picks": {}}
        got = [(e["s"], e["e"], e["taskId"]) for e in dayplan.plan(day, ctx)["entries"]]
        self.assertEqual([t for s, e, t in got if s < 990], [None] * len([1 for s, e, t in got if s < 990]), got)
        self.assertIn((990, 1050, "5"), got)

    def test_with_no_history_nothing_is_named(self):
        p = plan([doc(600, 640)], [], {})
        self.assertEqual([e["taskId"] for e in p["entries"]], [None])

    def test_a_window_with_no_votes_takes_the_task_of_its_neighbour_in_the_same_sitting(self):
        hist, kept = history({"7": doc})
        p = plan([doc(600, 640), page(640, 660, "/somewhere/new")], hist, kept)
        self.assertEqual({e["taskId"] for e in p["entries"]}, {"7"})
        self.assertEqual(sum(e["minutes"] for e in p["entries"]), 60)

    def test_a_clients_task_is_never_named_this_way(self):
        hist, kept = history({"7": doc}, billable=True)
        p = plan([doc(600, 640)], hist, kept)
        self.assertEqual([e["taskId"] for e in p["entries"]], [None])


class WithClaudeTest(unittest.TestCase):
    def test_a_sitting_with_a_session_keeps_the_sessions_task(self):
        hist, kept = history({"7": doc})
        hist.append({"date": "2026-11-03", "start": t0_of("2026-11-03") + 900 * 60, "minutes": 60, "taskId": "11",
                     "task": "Task 11", "project": "Internal", "billable": False})
        stored = {"2026-11-03": [{"k": [dayplan.title_key("Fix the importer")], "p": "repo", "s": 900, "e": 960}]}
        ctx = dayplan.learn(DATE, hist, stored, t0_of, context_days=kept)
        row = {"s": 600, "e": 630, "minutes": 30, "project": "repo", "kind": "focused", "titles": ["Fix the importer"],
               "first_message": "", "tasks": []}
        code = {"s": 600, "e": 630, "app": "Cursor", "title": "x", "host": "", "url": "", "cat": "work"}
        day = {"items": [code, doc(630, 650)], "away": [], "rows": [row], "logged": [], "meetings": [], "repeats": [],
               "calls": [], "rules": [], "assigned": [], "picks": {}}
        p = dayplan.plan(day, ctx)
        self.assertEqual({e["taskId"] for e in p["entries"]}, {"11"})
        self.assertNotIn("context", {e["source"] for e in p["entries"]})


class KeptTest(unittest.TestCase):
    def test_store_and_read_back_and_old_days_fall_out(self):
        context.store("2026-08-01", [doc(0, 30)])
        context.store(DATE, [doc(0, 30)])
        self.assertEqual(context.stored(DATE)["runs"], [[0, 30, 0]])
        self.assertIsNone(context.stored("2026-08-01"))  # more than KEEP_DAYS before
        self.assertEqual(list(context.days_before("2026-11-05")), [DATE])

    def test_a_damaged_day_kept_is_left_out(self):
        hist, kept = history({"7": doc})
        folder = tempfile.mkdtemp(dir=os.path.dirname(context.DIR))
        self.addCleanup(mock.patch.object(context, "DIR", folder).stop)
        mock.patch.object(context, "DIR", folder).start()
        bad = {"2026-11-03": '{"format":1,"keys":[],"runs":[[1,2]]}', "2026-11-02": '{"format":1,"keys":[["k"]],"runs":[["a","b",0]]}',
               "2026-11-01": '{"format":1,"keys":{"0":["k"]},"runs":[[0,10,0]]}', "2026-10-31": '{"format":1,"ru'}
        for d, text in bad.items():
            with open(context._path(d), "w", encoding="utf-8") as fh:
                fh.write(text)
        days = context.days_before(DATE)
        self.assertEqual(set(days) & set(bad), set())
        dayplan.learn(DATE, hist, {}, t0_of, context_days={**days, **{d: json.loads(t) for d, t in bad.items() if d != "2026-10-31"}})

    def test_a_cache_folder_that_cannot_be_written_breaks_nothing(self):
        with tempfile.NamedTemporaryFile() as fh, mock.patch.object(context, "DIR", os.path.join(fh.name, "context-days")):
            context.store("2026-11-03", [doc(0, 30)])  # no error: that day is simply not kept
            self.assertIsNone(context.stored("2026-11-03"))

    def test_a_past_day_no_tracker_read_whole_is_never_kept_empty(self):
        cases = [({"items": [], "problems": {"aw": "ActivityWatch is not answering"}}, True, None),  # MemTime's file is there
                 ({"items": [], "problems": {"aw": "ActivityWatch did not return its events: timed out"}}, True, None),
                 ({"items": [], "problems": {}}, False, None),
                 ({"items": [], "problems": {}}, True, []),  # a day off: kept, empty
                 ({"items": [slack(600, 610)], "problems": {"aw": "x"}}, True, [slack(600, 610)])]
        from pulsar import server, trackers
        for got, answering, want in cases:
            with mock.patch.object(server, "api_activity", return_value=got), \
                    mock.patch.object(trackers, "answering", return_value=answering):
                self.assertEqual(dayplan.past_items("2026-11-03", "http://localhost:5600"), want, (got, answering))

    def test_warm_keeps_the_days_a_tracker_gave_and_reads_the_others_again_later(self):
        asked = []

        def read(d):
            asked.append(d)
            return None if d == "2026-11-02" else [slack(600, 610)]  # no tracker answered that day
        context.warm("2026-11-04", "2026-11-04", read)
        with context._warming:  # the run is over once its lock is free
            pass
        self.assertIn("2026-11-02", asked)
        self.assertIsNone(context.stored("2026-11-02"))
        self.assertEqual(context.stored("2026-11-01")["runs"], [[600, 610, 0]])
        self.assertNotIn(DATE, asked)  # the day itself is not over


if __name__ == "__main__":
    unittest.main()
