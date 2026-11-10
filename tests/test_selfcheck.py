"""How well Pulsar picks for you (selfcheck.py): the journal's proposed fields, the measures on made-up days, what is
kept and when a day is worked out again, the background run, the blind plan and the two rules only the self-check
tries. No network, no trackers.

    python3 -m unittest discover -s tests
"""

import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import __version__, config, context, dayplan, learn, productive, selfcheck, server  # noqa: E402
from test_dayplan import day, history_for, row, work  # noqa: E402

DATE = "2026-11-04"
T0 = datetime(2026, 11, 4, tzinfo=timezone.utc).timestamp()
TODAY = "2026-11-10"


def plan_entry(s, e, task, project="Acme", billable=True, source="session"):
    return {"s": s, "e": e, "minutes": e - s, "taskId": task, "project": project, "billable": billable, "source": source}


def logged(s, minutes, task, project="Acme", billable=True):
    return {"s": s, "minutes": minutes, "taskId": task, "project": project, "billable": billable}


class ProposedFieldsTest(unittest.TestCase):
    """What Log the day proposed for a row, as the journal keeps it: checked, and never a name or a note."""

    def test_only_the_fields_the_self_check_reads(self):
        got = dayplan.proposed_fields({"taskId": "12", "serviceId": "34", "start": "09:30", "minutes": 45,
                                       "source": "session", "confidence": "high", "ticked": True,
                                       "title": "Acme checkout", "note": "secret", "project": "Acme"})
        self.assertEqual(got, {"taskId": "12", "serviceId": "34", "start": "09:30", "minutes": 45, "source": "session",
                               "confidence": "high", "ticked": True})

    def test_what_does_not_pass_is_dropped(self):
        got = dayplan.proposed_fields({"taskId": "x1", "serviceId": "²", "start": "25:00", "minutes": True,
                                       "source": "Session; drop", "confidence": "sure", "ticked": "yes"})
        self.assertEqual(got, {"taskId": None, "serviceId": None, "start": None, "minutes": None, "source": "",
                               "confidence": None, "ticked": False})
        self.assertIsNone(dayplan.proposed_fields(None))
        self.assertIsNone(dayplan.proposed_fields("12"))


class JournalProposedTest(unittest.TestCase):
    """POST /api/log with a row's proposed values: the journal keeps them beside what was sent, through every status."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.existing = []

        def create(e, s):
            i = str(500 + len(self.existing))
            self.existing.append({"id": i, "date": e["date"], "start": datetime.fromisoformat(e["started_at"]).timestamp(),
                                  "minutes": e["minutes"], "taskId": e["task_id"], "serviceId": e["service_id"],
                                  "project": "P", "task": "T", "personId": "1"})
            return {"id": i}
        self.patches = [mock.patch.object(config, "CACHE_DIR", self.dir),
                        mock.patch.object(server, "current_settings", lambda: {"timezone": "UTC", "person_id": "1"}),
                        mock.patch.object(server.config, "tz", lambda s: timezone.utc),
                        mock.patch.object(server.productive, "entries", lambda d0, d1, s: (self.existing, None, "token")),
                        mock.patch.object(server.productive, "create_entry", create),
                        mock.patch.dict(server._just_logged, clear=True),
                        mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "", "PULSAR_PREVIEW": ""})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_the_row_as_first_planned_is_kept_beside_what_was_sent(self):
        run = "ld-2026-11-04-0123abcd"
        body = {"date": DATE, "start": "10:15", "minutes": 30, "taskId": "5", "serviceId": "6", "run": run, "row": "r1",
                "proposed": {"taskId": "4", "serviceId": "6", "start": "10:00", "minutes": 45, "source": "lastday",
                             "confidence": "low", "ticked": False, "title": "never kept"}}
        self.assertIn("created", server.api_log(body))
        [x] = dayplan.journal()["runs"][0]["rows"]
        self.assertEqual((x["status"], x["taskId"], x["start"]), ("logged", "5", "10:15"))
        self.assertEqual(x["proposed"], {"taskId": "4", "serviceId": "6", "start": "10:00", "minutes": 45,
                                         "source": "lastday", "confidence": "low", "ticked": False})
        self.assertNotIn("never kept", json.dumps(dayplan.journal()))
        # a row sent without them (an older page, Pulsar in Claude Code): no field, counted as unknown
        server.api_log({**body, "row": "r2", "start": "11:00", "proposed": None})
        x2 = dayplan.journal()["runs"][0]["rows"][1]
        self.assertNotIn("proposed", x2)
        got = selfcheck.acceptance(dayplan.runs_for(DATE), self.existing)
        self.assertEqual((got["viaLogTheDay"], got["taskChanged"], got["unknown"], got["byHand"]), (2, 1, 1, 0))


class AcceptanceTest(unittest.TestCase):
    def test_each_kind_of_change_and_the_entries_added_another_way(self):
        p = {"taskId": "1", "serviceId": "9", "start": "09:00", "minutes": 60}
        rows = [{"row": "r0", "status": "logged", "id": "100", "taskId": "1", "serviceId": "9", "start": "09:00", "minutes": 60, "proposed": p},
                {"row": "r1", "status": "logged", "id": "101", "taskId": "2", "serviceId": "9", "start": "09:00", "minutes": 60, "proposed": p},
                {"row": "r2", "status": "changed", "id": "102", "taskId": "1", "serviceId": "9", "start": "09:15", "minutes": 60, "proposed": p},
                {"row": "r3", "status": "logged", "id": "103", "taskId": "1", "serviceId": "8", "start": "09:00", "minutes": 60, "proposed": p},
                {"row": "r4", "status": "logged", "id": "104", "taskId": "1", "serviceId": "9", "start": "09:00", "minutes": 45, "proposed": p},
                {"row": "r5", "status": "logged", "id": "105", "taskId": "1", "serviceId": "9", "start": "09:00", "minutes": 60},
                {"row": "r6", "status": "undone", "id": "106", "taskId": "1", "proposed": p},     # deleted by Undo
                {"row": "r7", "status": "logged", "id": "999", "taskId": "1", "proposed": p},     # gone from the day
                {"row": "r8", "status": "refused", "taskId": "1", "proposed": p}]
        entries = [{"id": str(i)} for i in range(100, 106)] + [{"id": "200"}, {"id": "201"}]
        got = selfcheck.acceptance([{"run": "x", "rows": rows}], entries)
        self.assertEqual(got, {"entries": 8, "byHand": 2, "viaLogTheDay": 6, "asProposed": 1, "taskChanged": 1,
                               "timeChanged": 2, "serviceChanged": 1, "unknown": 1})


class CompareTest(unittest.TestCase):
    """The measures, minute by minute, on a made-up day."""

    def test_right_task_client_and_no_task(self):
        plan = [plan_entry(540, 600, "1"),                                          # right
                plan_entry(600, 660, "2"),                                          # right client, wrong task
                plan_entry(660, 720, "7", project="FI-Admin", billable=False),      # both internal
                plan_entry(720, 750, None, project="", billable=None, source="")]   # no task
        got = selfcheck.compare(plan, [logged(540, 60, "1"), logged(600, 60, "1"),
                                       logged(660, 60, "8", project="FI-Ops", billable=False),
                                       logged(720, 30, "3"), logged(780, 30, "3"), {**logged(0, 20, "3"), "s": None}])
        self.assertEqual((got["logged"], got["rightTask"], got["rightClient"], got["noTask"], got["untimed"]),
                         (240, 60, 180, 60, 20))  # 780-810, where nothing was proposed, is "no task" too
        self.assertEqual(got["bySource"]["session"], [180, 60, 180])
        self.assertEqual(got["bySource"]["none"], [30, 0, 30])

    def test_billing_wrong_client_over_and_under(self):
        plan = [plan_entry(540, 600, "1", project="Acme"),         # you logged Globex then: wrong client, billed
                plan_entry(600, 690, "1", project="Acme"),         # nothing logged then: not a wrong client
                plan_entry(690, 720, "5", project="Globex", billable=False)]
        got = selfcheck.compare(plan, [logged(540, 60, "2", project="Globex"), logged(800, 30, "1", project="Acme")])
        self.assertEqual(got["wrongClient"], 60)
        # Acme: 150 proposed, 30 logged -> 120 over; Globex: 60 logged billable, none proposed billable -> 60 under
        self.assertEqual((got["over"], got["under"], got["billableProposed"], got["billableLogged"]), (120, 60, 150, 90))

    def test_an_empty_day_measures_nothing(self):
        self.assertEqual(selfcheck.compare([], [])["logged"], 0)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.p = mock.patch.object(config, "CACHE_DIR", self.dir)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_owner_only_atomic_and_kept_62_days(self):
        selfcheck.store("2026-09-01", {"format": 1, "date": "2026-09-01", "version": "1"}, "2026-09-02")
        doc = {"format": 1, "date": DATE, "version": __version__, "logged": 10}
        selfcheck.store(DATE, doc, TODAY)
        path = os.path.join(self.dir, "selfcheck", f"{DATE}.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
        self.assertEqual(selfcheck.stored(DATE), doc)
        self.assertEqual(sorted(os.listdir(os.path.dirname(path))), [f"{DATE}.json"])  # no temporary file, the old one gone
        with open(path, "w") as fh:
            fh.write('{"format": 1, "date": "2026-11-05", "version": "x"}')  # not this day's: not taken
        self.assertIsNone(selfcheck.stored(DATE))


def entry(d, i, start_h, minutes=60, task="1", project="Acme", billable=True):
    t0 = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    return {"id": f"{d}-{i}", "date": d, "start": t0 + start_h * 3600, "minutes": minutes, "taskId": task,
            "serviceId": "9", "project": project, "billable": billable, "task": "T", "service": "Dev"}


class RunTest(unittest.TestCase):
    """run_once with Productive, the tracker and the plan made up: what it reads, works out, keeps and skips."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.s = {"person_id": "1", "timezone": "UTC"}
        self.entries = [entry("2026-11-09", 1, 9), entry("2026-11-06", 1, 10), entry("2026-11-05", 1, 9),
                        entry("2026-11-03", 1, 9)]
        self.reads, self.planned, self.activity_reads = [], [], []
        self.problem = None
        self.answer = None
        self.fail_days = set()
        selfcheck._failed.clear()

        def rows(d0, d1, s, bulk=False):
            self.reads.append((d0, d1, bulk))
            if self.problem:
                return [], self.problem, "token", False
            return [r for r in self.entries if d0 <= r["date"] <= d1], None, "token", False

        def gather(date, blind=None, **kw):
            self.planned.append((date, blind))
            if self.answer:
                return self.answer
            if date in self.fail_days:
                return {"entries": [], "blindProblem": "the calendar did not answer"}
            return {"entries": [plan_entry(540, 600, "1")], "noActivity": False, "blindProblem": None,
                    "shadow": {"activity": None, "clients": [plan_entry(540, 600, "2")]}}

        def activity(d0, d1, s):
            self.activity_reads.append(d0)
            return []
        for d in [selfcheck._shift(TODAY, -i) for i in range(1, context.LEARN_DAYS + 1)]:  # the windows are kept
            os.makedirs(os.path.join(self.dir, "context-days"), exist_ok=True)
        self.patches = [mock.patch.object(config, "CACHE_DIR", self.dir),
                        mock.patch.object(context, "DIR", os.path.join(self.dir, "context-days")),
                        mock.patch.object(productive, "connection", lambda s: {"kind": "token", "token": "t"}),
                        mock.patch.object(productive, "assigned_tasks", lambda s: []),
                        mock.patch.object(productive, "my_activity", activity),
                        mock.patch.object(server, "productive_rows", rows),
                        mock.patch.object(server, "new_task_source", lambda d, s: []),
                        mock.patch.object(server, "api_activity", lambda q: {"items": [work(540, 600)], "problems": {}}),
                        mock.patch.object(dayplan, "past_items", lambda d, url: [work(540, 600)]),
                        mock.patch.object(dayplan, "gather", gather),
                        mock.patch.object(dayplan, "runs_for", lambda d: []),
                        mock.patch.object(config, "tz", lambda s: timezone.utc)]
        for p in self.patches:
            p.start()
        for i in range(1, context.LEARN_DAYS + 1):
            context.store(selfcheck._shift(TODAY, -i), [])

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_a_run_works_out_the_newest_days_first_and_keeps_numbers_only(self):
        self.assertEqual(selfcheck.run_once(self.s, TODAY), selfcheck.SOON)  # four days, three a run: one is left
        self.assertEqual([d for d, _ in self.planned], ["2026-11-09", "2026-11-06", "2026-11-05"])
        doc = selfcheck.stored("2026-11-09")
        self.assertEqual((doc["version"], doc["logged"], doc["rightTask"], doc["entries"], doc["byHand"]),
                         (__version__, 60, 60, 1, 1))
        self.assertEqual(doc["shadow"]["clients"]["rightTask"], 0)
        self.assertIsNone(doc["shadow"]["activity"])
        _, blind = self.planned[0]
        self.assertEqual(blind["day"], [self.entries[0]])  # the day's own entries go in only as "day"
        self.assertEqual(self.reads[0], ("2026-10-11", "2026-11-09", True))  # one read of the 30 days, in bulk
        self.assertEqual(self.activity_reads, [d for d, _ in self.planned])  # one activity read per day worked out
        self.assertFalse({"Acme", "Dev", "T", "2026-11-09-1"} & set(json.dumps(doc).replace(":", " ").replace(",", " ")
                                                                   .replace('"', " ").split()))  # no name, no id

    def test_nothing_changed_nothing_worked_out(self):
        while selfcheck.run_once(self.s, TODAY) == selfcheck.SOON:
            pass
        self.planned.clear()
        self.assertEqual(selfcheck.run_once(self.s, TODAY), selfcheck.IDLE)
        self.assertEqual(self.planned, [])

    def test_a_new_version_or_a_changed_day_is_worked_out_again(self):
        while selfcheck.run_once(self.s, TODAY) == selfcheck.SOON:
            pass
        old = selfcheck.stored("2026-11-06")
        selfcheck.store("2026-11-06", {**old, "version": "0.0.1"}, TODAY)
        self.entries.append(entry("2026-11-09", 2, 13))  # logged later: the day is worked out again
        self.planned.clear()
        selfcheck.run_once(self.s, TODAY)
        self.assertEqual([d for d, _ in self.planned], ["2026-11-09", "2026-11-06"])  # the changed day first
        self.assertEqual(selfcheck.stored("2026-11-06")["version"], __version__)
        self.assertEqual(selfcheck.stored("2026-11-09")["entries"], 2)

    def test_nothing_kept_when_productive_or_the_plan_did_not_answer(self):
        self.problem = "Productive timed out."
        self.assertEqual(selfcheck.run_once(self.s, TODAY), selfcheck.LATER)
        self.assertEqual(self.planned, [])
        self.problem = None
        self.answer = {"entries": [], "blindProblem": "no tracker answered"}
        self.assertEqual(selfcheck.run_once(self.s, TODAY), selfcheck.LATER)
        self.assertIsNone(selfcheck.stored("2026-11-09"))  # never kept as if it were real

    def test_a_day_whose_entries_are_gone_is_dropped(self):
        while selfcheck.run_once(self.s, TODAY) == selfcheck.SOON:
            pass
        self.entries = [r for r in self.entries if r["date"] != "2026-11-06"]
        selfcheck.run_once(self.s, TODAY)
        self.assertIsNone(selfcheck.stored("2026-11-06"))
        self.assertIsNotNone(selfcheck.stored("2026-11-05"))

    def test_a_day_that_failed_waits_behind_the_others(self):
        self.fail_days = {"2026-11-09"}
        self.assertEqual(selfcheck.run_once(self.s, TODAY), selfcheck.LATER)
        self.planned.clear()
        selfcheck.run_once(self.s, TODAY)
        self.assertEqual([d for d, _ in self.planned], ["2026-11-03", "2026-11-09"])

    def test_refresh_works_out_the_missing_days_now_within_its_caps(self):
        for f in os.listdir(os.path.join(self.dir, "context-days")):
            os.remove(os.path.join(self.dir, "context-days", f))
        with mock.patch.object(selfcheck, "REFRESH_SECONDS", -1), mock.patch.dict(selfcheck._state, {"running": False}):
            got = selfcheck.refresh(self.s, TODAY)  # out of time before the first day: none started, all said
        self.assertEqual((got["worked"], got["left"]), (0, 4))
        self.assertIn("4 more days to work out", got["note"])
        self.assertEqual(len(os.listdir(os.path.join(self.dir, "context-days"))), context.LEARN_DAYS)  # all, in one call
        with mock.patch.dict(selfcheck._state, {"running": False}):
            got = selfcheck.refresh(self.s, TODAY)
        self.assertEqual((got["worked"], got["left"], got["note"]), (4, 0, ""))
        self.assertFalse(selfcheck._state["running"])
        self.assertEqual(selfcheck.report(self.s, 30, TODAY)["days"]["workedOut"], 4)

    def test_refresh_waits_for_a_background_run_then_says_so(self):
        with mock.patch.object(selfcheck, "REFRESH_WAIT", 0.2), mock.patch.dict(selfcheck._state, {"running": True}):
            got = selfcheck.refresh(self.s, TODAY)
            self.assertTrue(selfcheck._state["running"])  # the background run's slot is left alone
        self.assertEqual(got["worked"], 0)
        self.assertIn("background run", got["note"])
        self.assertEqual(self.planned, [])

    def test_the_days_still_missing_come_from_the_kept_copies_of_the_days(self):
        for d in ("2026-11-09", "2026-11-06"):
            server.write_json(server._copy_path(d, self.s), {"fetched": "x", "rows": [r for r in self.entries if r["date"] == d]})
        self.assertEqual(selfcheck.pending(self.s, TODAY), ["2026-11-09", "2026-11-06"])
        selfcheck.run_once(self.s, TODAY)
        self.assertEqual(selfcheck.pending(self.s, TODAY), [])

    def test_never_without_a_connection_or_while_learning_runs(self):
        with mock.patch.object(productive, "connection", lambda s: {"kind": None}):
            self.assertEqual(selfcheck.run_once(self.s, TODAY), selfcheck.IDLE)
        self.assertEqual(selfcheck.run_once({"timezone": "UTC"}, TODAY), selfcheck.IDLE)
        with learn._warming:
            self.assertEqual(selfcheck.run_once(self.s, TODAY), selfcheck.SOON)
        self.assertEqual((self.reads, self.planned), ([], []))

    def test_the_windows_of_the_days_before_come_first_a_few_a_run(self):
        for f in os.listdir(os.path.join(self.dir, "context-days")):
            os.remove(os.path.join(self.dir, "context-days", f))
        read = []
        with mock.patch.object(dayplan, "past_items", lambda d, url: read.append(d) or [work(1, 2)]):
            self.assertEqual(selfcheck.run_once(self.s, TODAY), selfcheck.SOON)
        self.assertEqual(len(read), selfcheck.READS)  # never 60 days at once
        self.assertEqual(read[0], "2026-11-09")
        self.assertEqual(self.planned, [])

    def test_a_day_seen_by_no_tracker_is_kept_aside_from_the_measures(self):
        self.answer = {"entries": [], "noActivity": True, "blindProblem": None, "shadow": {}}
        selfcheck.run_once(self.s, TODAY)
        doc = selfcheck.stored("2026-11-09")
        self.assertTrue(doc["noActivity"])
        self.assertNotIn("logged", doc)
        sm = selfcheck.summary(self.s, TODAY)
        self.assertEqual((sm["days"], sm["noActivity"]), (0, len(self.planned)))


class OwnProblemsTest(unittest.TestCase):
    def test_reading_past_days_never_touches_todays_tracker_warning(self):
        """A past day's tracker read in the self-check has its own problems: the Activity header's warning for today
        is neither set nor cleared by it."""
        from pulsar import selfcheck, trackers
        trackers.PROBLEMS.clear()
        trackers.PROBLEMS["aw"] = "today's warning"

        def reads(*a, **k):  # what a failing past-day read would do
            trackers.PROBLEMS.pop("aw", None)
            trackers.PROBLEMS["memtime"] = "a past day's problem"
            return {"wait": 60, "worked": 0, "left": 0, "note": ""}
        try:
            with mock.patch.object(selfcheck, "_run_days", reads):
                selfcheck._run({})
            self.assertEqual(dict(trackers.PROBLEMS), {"aw": "today's warning"})
        finally:
            trackers.PROBLEMS.clear()


class NudgeTest(unittest.TestCase):
    def test_never_in_tests_nor_read_only(self):
        with mock.patch.object(selfcheck, "run_once", lambda s: self.fail("ran in a test")), \
                mock.patch.dict(selfcheck._state, {"running": False, "next": 0.0}):
            self.assertFalse(selfcheck.nudge({"person_id": "1"}))
            with mock.patch.object(config, "tests_offline", lambda: False), \
                    mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "1"}):
                self.assertFalse(selfcheck.nudge({"person_id": "1"}))

    def test_one_run_at_a_time_then_not_before_the_next(self):
        gate, ran = threading.Event(), []

        def run_once(s):
            ran.append(1)
            gate.wait(5)
            return selfcheck.SOON
        with mock.patch.object(config, "tests_offline", lambda: False), \
                mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": ""}), \
                mock.patch.object(selfcheck, "run_once", run_once), \
                mock.patch.dict(selfcheck._state, {"running": False, "next": 0.0}):
            self.assertTrue(selfcheck.nudge({}))
            self.assertFalse(selfcheck.nudge({}))  # one runs: the page never waits for it
            gate.set()
            for _ in range(50):
                if not selfcheck._state["running"]:
                    break
                time.sleep(0.02)
            self.assertFalse(selfcheck.nudge({}))  # the next only SOON seconds later
            self.assertGreater(selfcheck._state["next"], time.time() + selfcheck.SOON - 5)
        self.assertEqual(ran, [1])


class SummaryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.p = mock.patch.object(config, "CACHE_DIR", self.dir)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def put(self, d, **kw):
        doc = {"format": 1, "date": d, "version": __version__, "logged": 100, "rightTask": 50, "rightClient": 80,
               "noTask": 10, "over": 5, "entries": 2, "byHand": 1, "asProposed": 1, "viaLogTheDay": 1,
               "bySource": {"session": [100, 50, 100]},
               "shadow": {"activity": {"logged": 100, "rightTask": 55}, "clients": None}, **kw}
        selfcheck.store(d, doc, TODAY)

    def test_still_collecting_under_five_days_then_the_totals(self):
        for d in ("2026-11-09", "2026-11-06", "2026-11-05"):
            self.put(d)
        sm = selfcheck.summary({"person_id": "1", "timezone": "UTC"}, TODAY)
        self.assertTrue(sm["collecting"])
        self.assertEqual((sm["days"], sm["weekDays"], sm["week"]["days"]), (3, 3, 3))
        for d in ("2026-10-20", "2026-10-21", "2026-09-01"):  # the last is older than 30 days: not counted
            self.put(d, version="0.0.1")
        sm = selfcheck.summary({"person_id": "1", "timezone": "UTC"}, TODAY)
        self.assertFalse(sm["collecting"])
        m = sm["month"]
        self.assertEqual((m["days"], m["logged"], m["rightTask"], m["over"]), (5, 500, 250, 25))
        self.assertEqual(sm["older"], 2)
        self.assertEqual(sm["log"]["byHand"], 5)
        # a shadow over the days it has, beside the plan's own on those days
        self.assertEqual((m["shadow"]["activity"]["days"], m["shadow"]["activity"]["rightTask"],
                          m["shadow"]["activity"]["base"]["rightTask"]), (5, 275, 250))
        self.assertEqual(m["shadow"]["clients"]["days"], 0)

    def test_the_file_holds_numbers_only(self):
        self.put("2026-11-09")
        doc = selfcheck.download({"person_id": "1", "timezone": "UTC", "round_to": 10, "person_name": "Ann Example"},
                                 TODAY)
        self.assertEqual((doc["pulsar"], doc["from"], doc["to"]), (__version__, "2026-10-11", "2026-11-09"))
        self.assertEqual((doc["settings"]["roundTo"], doc["settings"]["awayAfter"]), (10, 15))
        [d] = doc["perDay"]
        self.assertEqual(d["bySource"], {"session": [100, 50, 100]})
        self.assertEqual((doc["days"]["workedOut"], doc["totals"]["rightTask"], doc["logTheDay"]["byHand"]), (1, 50, 1))
        self.assertNotIn("sig", d)
        self.assertNotIn("Ann", json.dumps(doc))


class BlindGatherTest(unittest.TestCase):
    """dayplan.gather(blind=...): the day's own entries never vote or block, nothing is read from Productive, and the
    two rules are planned apart."""

    def run_gather(self, blind, likely_error=None, items=None):
        seen = {}
        s = {"timezone": "UTC", "person_id": "1", "rules": [], "person_name": "", "ask_claude": True}
        history = [{"date": "2026-11-03", "start": T0 - 86400 + 9 * 3600, "minutes": 60, "taskId": "7", "serviceId": "1",
                    "service": "Dev", "task": "R&D", "project": "Acme", "billable": True, "id": "h1"}]

        def plan(d, ctx, opts=None, **kw):
            seen.setdefault("plans", []).append(ctx)
            seen["day"] = d
            return {"entries": [{"s": 540, "e": 600, "minutes": 60, "taskId": "7", "project": "", "serviceId": None,
                                 "source": "context"}], "unplaced": [], "skipped": [], "uncovered": [], "totals": {}}
        fail = lambda *a, **k: self.fail("read Productive")  # noqa: E731
        with mock.patch.object(server, "current_settings", lambda: s), \
                mock.patch.object(server.config, "tz", lambda s: timezone.utc), \
                mock.patch.object(server, "api_activity", lambda q: {"items": items if items is not None else [work(540, 600)],
                                                                     "away": [], "problems": {}}), \
                mock.patch.object(server, "productive_rows", fail), mock.patch.object(server, "history_rows", fail), \
                mock.patch.object(server, "api_suggest", fail), mock.patch.object(server, "api_repeats", fail), \
                mock.patch.object(server, "new_task_source", fail), \
                mock.patch.object(dayplan.config, "tz", lambda s: timezone.utc), \
                mock.patch.object(dayplan, "plan", plan), \
                mock.patch.object(dayplan.context_mod, "warm", lambda *a: self.fail("warmed")), \
                mock.patch("pulsar.askclaude.latest", lambda d: {"picks": {"540|x": {"tasks": [{"taskId": "9"}]}}}), \
                mock.patch("pulsar.likely.enabled", lambda s: bool(likely_error)), \
                mock.patch("pulsar.likely.proposal", lambda *a, **k: {"error": likely_error}), \
                mock.patch("pulsar.trackers.answering", lambda url: False), \
                mock.patch("pulsar.calendar.day", lambda d, s, fresh=False: {"events": [], "connected": False}), \
                mock.patch("pulsar.calendar.history", lambda f, n, s: []):
            got = dayplan.gather(DATE, blind={"history": history + [{**history[0], "date": DATE, "taskId": "8", "id": "d1"}],
                                              "day": [{**history[0], "date": DATE, "taskId": "8"}], **blind})
        return got, seen

    def test_the_days_own_entries_never_vote_or_block(self):
        got, seen = self.run_gather({"assigned": [{"taskId": "3", "title": "Sprint", "project": "Acme"}],
                                     "fresh": None, "activity": [{"at": T0 + 11 * 3600, "task": "7"}]})
        self.assertEqual(seen["day"]["logged"], [])
        self.assertEqual(seen["day"]["picks"], {})  # Ask Claude saw the day's entries: left out
        ctx = seen["plans"][0]
        self.assertNotIn("8", ctx["names"])  # the day's own task teaches nothing
        self.assertNotIn("touched", ctx)
        self.assertEqual([x["taskId"] for x in seen["day"]["assigned"]], ["3"])
        [e] = got["entries"]
        self.assertEqual((e["project"], e["serviceId"], e["billable"]), ("Acme", "1", True))  # as the history says
        self.assertIsNone(got["blindProblem"])

    def test_the_rules_are_planned_apart_only_when_they_can_change_something(self):
        got, seen = self.run_gather({"assigned": None, "fresh": None, "activity": None})
        self.assertIsNone(got["shadow"]["activity"])  # not read: left out, never taken as "no change"
        self.assertEqual(len(seen["plans"]), 1)  # no Likely rows, no learned windows: nothing to plan again
        self.assertEqual(got["shadow"]["clients"][0]["taskId"], "7")

    def test_a_day_no_tracker_answered_for_is_not_whole(self):
        got, _ = self.run_gather({"assigned": None, "fresh": None, "activity": None}, items=[])
        self.assertIn("no tracker", got["blindProblem"])
        got, _ = self.run_gather({"assigned": None, "fresh": None, "activity": None}, likely_error="sessions unreadable")
        self.assertIn("sessions unreadable", got["blindProblem"])


class ShadowRulesTest(unittest.TestCase):
    """The two rules only the self-check tries: off in every plan that does not ask for them."""

    def test_a_bulk_moment_says_nothing_and_time_is_never_activity(self):
        pts = [(600, "A"), (601, "B"), (603, "C"), (700, "D"), (702, "D")]
        self.assertEqual(dayplan.touches(pts), [(700.0, "D"), (702.0, "D")])
        self.assertIsNone(productive._activity_task({"item_type": "TimeEntry", "task_id": 7}))
        self.assertIsNone(productive._activity_task({"item_type": "Comment", "parent_type": "time_entry", "task_id": 7}))
        self.assertEqual(productive._activity_task({"item_type": "Comment", "parent_type": "Task", "parent_id": 8}), "8")

    def test_activity_read_only_your_own_on_tasks(self):
        def get(path, query, token, org):
            return 200, {"data": [
                {"attributes": {"created_at": "2026-11-04T10:00:00+00:00", "item_type": "Task", "item_id": 7},
                 "relationships": {"creator": {"data": {"type": "people", "id": "42"}}}},
                {"attributes": {"created_at": "2026-11-04T11:00:00+00:00", "item_type": "Task", "item_id": 9},
                 "relationships": {"creator": {"data": {"type": "people", "id": "99"}}}}]}
        with mock.patch.object(productive, "connection", lambda s: {"kind": "token", "token": "t"}), \
                mock.patch.object(productive, "rest_get", get):
            got = productive.my_activity(DATE, DATE, {"person_id": "42", "org_id": "1"})
        self.assertEqual([x["task"] for x in got], ["7"])

    def setUp(self):
        names = {"P": {"task": "Initech support", "project": "Initech"},
                 "Q": {"task": "Initech audit", "project": "Initech"}}
        self.ctx = {**history_for({"Fix the importer": "11"}), "sess": {}, "sr": {}, "names": names,
                    "repo": {"initech": {"Q": 300.0}}, "lastday": {"Initech": ("2026-11-03", "P", 60, 0.0)}}
        self.rows = [row(540, 600, project="initech", titles=["Brand new thing"])]

    def pick(self, ctx):
        [e] = dayplan.plan(day(items=[work(540, 600)], rows=self.rows), ctx)["entries"]
        return e

    def test_a_guess_gives_way_only_where_the_self_check_asks(self):
        e = self.pick(self.ctx)
        self.assertEqual((e["taskId"], e["source"]), ("P", "lastday"))
        e = self.pick({**self.ctx, "touched": [(700.0, "Q")]})
        self.assertEqual((e["taskId"], e["source"], e["checked"]), ("Q", "touched", False))
        self.assertEqual(self.pick({**self.ctx, "touched": [(700.0, "Q"), (650.0, "P")]})["taskId"], "P")

    def test_window_picks_name_a_clients_task_only_in_the_self_check(self):
        items = [work(540, 600, app="Safari", host="tracker.example.com", url="https://tracker.example.com/board")]
        votes = {k: {"C1": 50.0} for k in context.keys(items[0])}
        ctx = {**self.ctx, "context": votes, "names": {"C1": {"task": "Client work", "project": "Acme"}},
               "project_class": {"Acme": "client"}}
        d = day(items=items)
        plain = dayplan.plan(d, ctx)["entries"]
        self.assertFalse([e for e in plain if e["taskId"] == "C1"])
        tried = dayplan.plan(d, {**ctx, "context_clients": True})["entries"]
        self.assertEqual([e["taskId"] for e in tried], ["C1"])
        self.assertEqual(dayplan.plan(d, ctx)["entries"], plain)  # the same day planned again: nothing carried over


class PageTest(unittest.TestCase):
    """The page's part: each Log the day row goes out with what was proposed for it, and Settings has the panel."""
    WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

    def read(self, *path):
        with open(os.path.join(self.WEB, *path), encoding="utf-8") as fh:
            return fh.read()

    def test_each_row_is_sent_with_the_plan_as_first_proposed(self):
        js = self.read("js", "logday.js")
        self.assertIn("proposed: r.orig || null", js)
        self.assertIn("key: ldRowKey(d, e), orig: ldOrig(e),", js)  # the plan as it came
        self.assertIn("orig: firstAt(e) || ldOrig(e)", js)  # Claude's picks keep Pulsar's own first plan
        self.assertIn("'orig']", self.read("js", "ldkeep.js"))  # a reopened day keeps it

    def test_settings_has_the_panel_and_its_script(self):
        html = self.read("index.html")
        panel = html[html.index('id="p-day"'):html.index('id="p-claude"')]
        self.assertIn('id="sc-sec"', panel)
        self.assertIn("How well Pulsar picks for you", panel)
        self.assertIn('<script src="/static/js/selfcheck.js"></script>', html)
        self.assertIn("scLoad();", self.read("js", "settings.js"))


if __name__ == "__main__":
    unittest.main()
