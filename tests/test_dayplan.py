"""Log the day on made-up days: the planner's rules, the journal, Undo and the API. No network, no trackers.

    python3 -m unittest discover -s tests
"""

import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import config, dayplan, productive, server  # noqa: E402

DATE = "2026-11-04"
T0 = datetime(2026, 11, 4, tzinfo=timezone.utc).timestamp()


def work(s, e, app="Cursor", title="x", host="", url="", cat="work"):
    return {"s": s, "e": e, "app": app, "title": title, "host": host, "url": url, "cat": cat}


def row(s, e, project="repo", titles=("Fix the importer",), kind="focused", tasks=None):
    return {"s": s, "e": e, "minutes": e - s, "project": project, "kind": kind, "titles": list(titles),
            "first_message": "", "tasks": tasks or []}


def history_for(title_key_task: dict, project="repo", days=8, minutes=60):
    """Past days where a session of each title ran 9:00-10:00 and its task was logged then: strong votes."""
    hist, stored = [], {}
    for i in range(1, days + 1):
        d = f"2026-10-{28 - i:02d}"
        t0 = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        for j, (title, task) in enumerate(title_key_task.items()):
            s = 540 + j * 90
            stored.setdefault(d, []).append({"k": [dayplan.title_key(title)], "p": project, "s": s, "e": s + minutes})
            hist.append({"date": d, "start": t0 + s * 60, "minutes": minutes, "taskId": task, "serviceId": "s" + task,
                         "service": "Dev", "task": f"Task {task}", "project": "P"})
    t0_of = lambda d: datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()  # noqa: E731
    return dayplan.learn(DATE, hist, stored, t0_of, person="Nick Example")


def day(**kw):
    base = {"items": [], "away": [], "rows": [], "logged": [], "meetings": [], "repeats": [], "calls": [], "rules": [],
            "assigned": [], "picks": {}}
    return {**base, **kw}


class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.ctx = history_for({"Fix the importer": "11", "Hero video": "22"})

    def test_a_known_session_gets_its_task_with_high_confidence_and_starts_ticked(self):
        p = dayplan.plan(day(items=[work(540, 600)], rows=[row(540, 600)]), self.ctx)
        [e] = p["entries"]
        self.assertEqual((e["taskId"], e["confidence"], e["checked"], e["s"], e["minutes"]), ("11", "high", True, 540, 60))
        self.assertEqual(e["note"], "")  # never written from the session's title

    def test_low_confidence_and_unknown_rows_start_unticked(self):
        p = dayplan.plan(day(items=[work(600, 640)], rows=[row(600, 640, project="elsewhere", titles=["Something new"])]),
                         self.ctx)
        [e] = p["entries"]
        self.assertEqual(e["confidence"], "low")
        self.assertFalse(e["checked"])
        self.assertIn("notask", e["flags"])

    def test_nothing_overlaps_what_is_logged_or_each_other(self):
        logged = [{"s": 560, "minutes": 20, "taskId": "99"}]
        p = dayplan.plan(day(items=[work(530, 640)], rows=[row(530, 600), row(600, 640, titles=["Hero video"])],
                             logged=logged), self.ctx)
        spans = sorted([(e["s"], e["e"]) for e in p["entries"]] + [(560, 580)])
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            self.assertLessEqual(a1, b0, spans)

    def test_proposed_plus_logged_never_exceeds_active_time_and_meetings(self):
        # 60 active minutes, 50 already logged elsewhere in the day: at most 10 more, the rest shown as not placed
        logged = [{"s": 900, "minutes": 50, "taskId": "99"}]
        p = dayplan.plan(day(items=[work(540, 600)], rows=[row(540, 600)], logged=logged), self.ctx)
        self.assertLessEqual(p["totals"]["proposed"] + p["totals"]["logged"], p["totals"]["cap"])
        self.assertEqual(p["totals"]["cap"], 60)
        self.assertEqual(sum(u["minutes"] for u in p["unplaced"]), 50)
        self.assertTrue(all(u["why"].startswith("more than") for u in p["unplaced"]))
        # a day logged in full gets nothing more
        logged = [{"s": None, "minutes": 70, "taskId": "99"}]  # even without a start time
        p = dayplan.plan(day(items=[work(540, 600)], rows=[row(540, 600)], logged=logged), self.ctx)
        self.assertEqual(p["entries"], [])

    def test_a_repeat_a_calendar_meeting_and_a_session_on_the_same_call_are_one_entry(self):
        rep = {"s": 600, "e": 630, "minutes": 30, "at": 600, "taskId": "77", "title": "Scrum", "project": "PM",
               "serviceId": "s77", "service": "Admin", "note": "Daily meeting", "days": 12}
        meet = {"s": 600, "e": 630, "title": "Daily scrum", "uid": "u1", "declined": False, "all_day": False,
                "tasks": [{"taskId": "77", "title": "Scrum", "project": "PM", "n": 5, "of": 6}]}
        p = dayplan.plan(day(items=[work(600, 630, app="zoom.us", title="Zoom Meeting")], calls=[{"s": 601, "e": 629}],
                             repeats=[rep], meetings=[meet], rows=[row(605, 625)]), self.ctx)
        self.assertEqual(len(p["entries"]), 1, p["entries"])
        e = p["entries"][0]
        self.assertEqual((e["taskId"], e["s"], e["minutes"], e["note"], e["noteFrom"]), ("77", 600, 30, "Daily meeting", "repeat"))
        self.assertIn("Daily scrum", e["why"])
        self.assertTrue(e["checked"])

    def test_a_calendar_meeting_without_a_call_is_unticked_and_its_idle_part_skipped(self):
        meet = {"s": 600, "e": 660, "title": "Client review", "uid": "u2", "declined": False, "all_day": False,
                "tasks": [{"taskId": "33", "title": "Reviews", "project": "C", "n": 3, "of": 3}]}
        p = dayplan.plan(day(items=[work(600, 620, app="Safari")], meetings=[meet]), self.ctx)
        [e] = p["entries"]
        self.assertEqual((e["taskId"], e["minutes"], e["note"]), ("33", 20, "Client review"))
        self.assertFalse(e["checked"])
        self.assertIn("unattended", e["flags"])
        self.assertEqual(p["skipped"][0]["minutes"], 40)
        self.assertLessEqual(p["totals"]["proposed"], p["totals"]["cap"])

    def test_a_call_with_no_calendar_entry_has_no_task_and_no_note(self):
        p = dayplan.plan(day(items=[work(700, 730, app="zoom.us", title="Zoom Meeting")], calls=[{"s": 700, "e": 730}]),
                         self.ctx)
        [e] = p["entries"]
        self.assertEqual((e["taskId"], e["note"], e["checked"]), (None, "", False))

    def test_the_pulsar_window_is_work_only_in_pulsars_own_repository(self):
        items = [work(600, 640, app="Pulsar")]
        p = dayplan.plan(day(items=items, rows=[row(600, 640, project="pulsar")]), self.ctx)
        self.assertEqual(sum(e["minutes"] for e in p["entries"]), 40)  # testing Pulsar while building it
        p = dayplan.plan(day(items=items, rows=[row(600, 640, project="tua-trent")]), self.ctx)
        self.assertEqual(sum(e["minutes"] for e in p["entries"]), 0)  # anywhere else it is time spent logging

    def test_away_time_agent_only_time_and_time_spent_logging_are_never_ticked(self):
        items = [work(540, 570), work(585, 600), work(600, 620, app="Pulsar"), work(620, 640, app="Safari", host="app.productive.io")]
        p = dayplan.plan(day(items=items, away=[{"s": 570, "e": 585}],
                             rows=[row(540, 600), row(650, 700, kind="parallel", titles=["Hero video"])]), self.ctx)
        for e in p["entries"]:
            self.assertFalse(e["s"] < 585 and e["e"] > 570 and e["minutes"] > 45, e)  # the break is not logged
            self.assertFalse(600 <= e["s"] < 640, e)  # logging time
        self.assertEqual(sum(e["minutes"] for e in p["entries"] if e["checked"]), 45)
        # an agent-only row with no activity under it proposes nothing
        self.assertFalse(any(e["s"] >= 650 for e in p["entries"]))
        items.append(work(650, 700, app="Safari"))
        p = dayplan.plan(day(items=items, away=[{"s": 570, "e": 585}],
                             rows=[row(540, 600), row(650, 700, kind="parallel", titles=["Hero video"])]), self.ctx)
        agent = [e for e in p["entries"] if e["s"] >= 650]
        self.assertTrue(agent and all(not e["checked"] and "agent" in e["flags"] for e in agent), agent)

    def test_claude_replaces_an_unsure_answer_never_a_sure_one(self):
        pick = {"tasks": [{"taskId": "55", "title": "From Claude", "project": "Q"}], "why": "the messages say so",
                "note": "Fixed the importer."}
        rows = [row(540, 600), row(700, 740, project="other", titles=["New thing"])]
        p = dayplan.plan(day(items=[work(540, 600), work(700, 740)], rows=rows,
                             picks={"540|repo": pick, "700|other": pick}), self.ctx)
        sure, unsure = p["entries"]
        self.assertEqual((sure["taskId"], sure["note"]), ("11", ""))   # Claude's note is for its own task only
        self.assertEqual((unsure["taskId"], unsure["source"], unsure["note"], unsure["noteFrom"]),
                         ("55", "claude", "Fixed the importer.", "claude"))

    def test_when_claude_finds_no_task_the_guess_stays_flagged(self):
        rows = [row(700, 740, project="other", titles=["New thing"])]
        ctx = {**self.ctx, "repo": {"other": {"44": 30.0}}, "names": {**self.ctx["names"], "44": {"task": "Guess", "project": "G"}}}
        p = dayplan.plan(day(items=[work(700, 740)], rows=rows, picks={"700|other": {"tasks": [], "why": ""}}), ctx)
        [e] = p["entries"]
        self.assertEqual((e["taskId"], e["claudeNone"]), ("44", True))

    def test_claudes_pick_keeps_the_planners_guess_as_the_alternative(self):
        rows = [row(700, 740, project="other", titles=["New thing"])]
        ctx = {**self.ctx, "repo": {"other": {"44": 30.0}}, "names": {**self.ctx["names"], "44": {"task": "Guess", "project": "G"}}}
        pick = {"tasks": [{"taskId": "55", "title": "From Claude", "project": "Q"}], "why": "the messages"}
        p = dayplan.plan(day(items=[work(700, 740)], rows=rows, picks={"700|other": pick}), ctx)
        [e] = p["entries"]
        self.assertEqual((e["taskId"], e["source"], e["alts"][0]["taskId"]), ("55", "claude", "44"))
        self.assertTrue(e["alts"][0]["why"].startswith("Pulsar's guess"))

    def test_a_repository_never_logged_is_skipped_not_proposed(self):
        ctx = {**self.ctx, "never_logged": {"bot"}}
        p = dayplan.plan(day(items=[work(540, 600)], rows=[row(540, 600, project="bot", titles=["Coins"])]), ctx)
        self.assertEqual(p["entries"], [])
        self.assertEqual(p["skipped"][0]["minutes"], 60)

    def test_a_short_piece_far_away_does_not_pull_an_entry_to_its_time(self):
        # replay, 6 Oct: a 3-minute row at 00:39 joined a 204-minute afternoon row, laid out at 00:40-04:00
        items = [work(39, 42), work(785, 989)]
        rows = [row(39, 42, project="elsewhere", titles=["Something new"]), row(785, 989)]
        p = dayplan.plan(day(items=items, rows=rows, away=[{"s": 42, "e": 785, "off": True}]), self.ctx)
        [main] = [e for e in p["entries"] if e["taskId"] == "11"]
        self.assertEqual((main["s"], main["e"]), (785, 990))
        for e in p["entries"]:
            self.assertFalse(e["s"] < 785 and e["e"] > 60, e)  # nothing proposed in the night

    def test_a_repeat_stretched_over_the_next_meeting_does_not_swallow_it(self):
        # replay, 4 Nov: the 16:25 repeat ran to 17:45 on one long call and was taken for the 17:00 meeting too,
        # whose call was then left with no task
        rep = {"s": 985, "e": 1065, "minutes": 80, "at": 985, "taskId": "77", "title": "Scrum", "project": "PM",
               "serviceId": "s77", "service": "Admin", "note": "", "days": 13}
        meetings = [{"s": 990, "e": 1020, "title": "Scrum", "uid": "u1", "declined": False, "all_day": False,
                     "tasks": [{"taskId": "77", "title": "Scrum", "project": "PM", "n": 2, "of": 2}]},
                    {"s": 1020, "e": 1140, "title": "AI jam", "uid": "u2", "declined": False, "all_day": False,
                     "tasks": [{"taskId": "88", "title": "Jams", "project": "AI", "n": 2, "of": 4}]}]
        p = dayplan.plan(day(items=[work(991, 1156, app="zoom.us", title="Zoom Meeting")], calls=[{"s": 991, "e": 1156}],
                             repeats=[rep], meetings=meetings), self.ctx)
        jam = [e for e in p["entries"] if e["taskId"] == "88"]
        self.assertTrue(jam and sum(e["minutes"] for e in jam) >= 60, p["entries"])
        self.assertFalse([e for e in p["entries"] if e["source"] == "call"], p["entries"])
        self.assertEqual([e["taskId"] for e in p["entries"] if e["s"] < 1065], ["77"])

    def test_days_with_entries_logged_by_length_only_never_make_a_repository_never_logged(self):
        # replay, Sep: entries with no start time cannot be matched to the work; the person's main repository was
        # set aside as "you never log this repository"
        t0_of = lambda d: datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()  # noqa: E731
        stored = {f"2026-10-{n:02d}": [{"k": [], "p": "qa", "s": 540, "e": 720}] for n in (20, 21, 22)}
        untimed = [{"date": d, "start": None, "minutes": 180, "taskId": "5", "task": "QA", "project": "P"} for d in stored]
        self.assertNotIn("qa", dayplan.learn(DATE, untimed, stored, t0_of)["never_logged"])
        # logged at other times of those days, with a start: the repository really is never logged
        timed = [{**r, "start": t0_of(r["date"]) + 1200 * 60, "minutes": 60} for r in untimed]
        self.assertIn("qa", dayplan.learn(DATE, timed, stored, t0_of)["never_logged"])

    def test_an_editor_with_no_session_row_is_unticked_work_with_the_neighbouring_task(self):
        items = [work(540, 600), work(640, 670, app="Terminal")]
        p = dayplan.plan(day(items=items, rows=[row(540, 600)]), self.ctx)
        session, editor = p["entries"]
        self.assertEqual((session["taskId"], session["checked"]), ("11", True))
        self.assertEqual((editor["taskId"], editor["kind"], editor["s"], editor["minutes"], editor["checked"]),
                         ("11", "outside", 640, 30, False))

    def test_a_session_vote_of_40_to_60_percent_is_low_and_unticked(self):
        key = dayplan.title_key("Fix the importer")
        ctx = {**self.ctx, "sess": {key: {"11": 55.0, "22": 45.0}}}
        [e] = dayplan.plan(day(items=[work(540, 600)], rows=[row(540, 600)]), ctx)["entries"]
        self.assertEqual((e["taskId"], e["confidence"], e["checked"]), ("11", "low", False))

    def test_a_sessions_task_does_not_carry_over_to_another_clients_repository(self):
        # replay, 3 Nov: a session that did one client's database upgrade the day before gave that task to the next
        # day's work for other clients
        key = dayplan.title_key("Fix the importer")
        names = {"M": {"task": "Database upgrade", "project": "Munk"}, "P": {"task": "Pharmacy support", "project": "Pharmacy"}}
        ctx = {**self.ctx, "sess": {key: {"M": 120.0}}, "repo": {"pharmacy": {"P": 300.0}}, "names": names}
        [e] = dayplan.plan(day(items=[work(540, 600)], rows=[row(540, 600, project="pharmacy")]), ctx)["entries"]
        self.assertEqual((e["taskId"], e["source"]), ("P", "repo"))
        # a repository with too little history says nothing: the session's task stays
        ctx = {**ctx, "repo": {"pharmacy": {"P": 30.0}}}
        [e] = dayplan.plan(day(items=[work(540, 600)], rows=[row(540, 600, project="pharmacy")]), ctx)["entries"]
        self.assertEqual((e["taskId"], e["source"]), ("M", "session"))

    def test_common_words_never_match_a_task_name(self):
        # a pick once matched a task name on "not"
        ctx = {**self.ctx, "recent": ["90"], "names": {"90": {"task": "Not urgent: hero banner", "project": "X"}},
               "sess": {}, "repo": {}}
        p = dayplan.plan(day(items=[work(540, 600)], rows=[row(540, 600, project="z", titles=["Why is this not working"])]), ctx)
        self.assertIsNone(p["entries"][0]["taskId"])
        self.assertEqual(dayplan.words("the hero banner is not here and not done"), {"hero", "banner", "done"})

    def test_your_own_name_in_titles_is_no_evidence(self):
        ctx = {**self.ctx, "recent": ["90"], "names": {"90": {"task": "Estimations - Nick", "project": "X"}},
               "sess": {}, "repo": {}}
        p = dayplan.plan(day(items=[work(540, 600)], rows=[row(540, 600, project="z", titles=["Nick's notes"])]), ctx)
        self.assertIsNone(p["entries"][0]["taskId"])

    def test_the_service_is_the_last_one_used_if_it_still_takes_time(self):
        svcs = [{"id": "1", "name": "Dev"}, {"id": "2", "name": "Design"}]
        self.assertEqual(dayplan.pick_service(svcs, "2", "Design")["id"], "2")
        self.assertEqual(dayplan.pick_service([{"id": "3", "name": "Dev"}, {"id": "2", "name": "Design"}], "1", "Dev")["id"], "3")
        self.assertIsNone(dayplan.pick_service(svcs, "9", "Gone"))
        self.assertEqual(dayplan.pick_service(svcs[:1], None, "")["id"], "1")

    def test_time_no_entry_covers_is_reported(self):
        # five minutes in a browser with nothing near them: too short for an entry of their own, so not covered
        p = dayplan.plan(day(items=[work(540, 600), work(800, 805, app="Safari")], rows=[row(540, 600)]), self.ctx)
        self.assertEqual([(u["s"], u["e"]) for u in p["uncovered"]], [(800, 805)])
        self.assertEqual(p["totals"]["notCovered"], 5)
        # the same minutes logged already: covered
        p = dayplan.plan(day(items=[work(540, 600), work(800, 805, app="Safari")], rows=[row(540, 600)],
                             logged=[{"s": 800, "minutes": 5, "taskId": "1"}]), self.ctx)
        self.assertEqual(p["uncovered"], [])


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.p = mock.patch.object(config, "CACHE_DIR", self.dir)
        self.p.start()
        self.run = "ld-2026-11-04-0123abcd"
        self.entry = {"start": "10:00", "minutes": 30, "taskId": "5", "serviceId": "6"}

    def tearDown(self):
        self.p.stop()

    def test_a_row_sent_or_logged_is_never_sent_again_by_any_run(self):
        dayplan.check(self.run, "r1", DATE, "10:00", 30, "5", "6")
        dayplan.mark(self.run, DATE, "r1", self.entry, "sending")
        for run, rowname in ((self.run, "r1"), ("ld-2026-11-04-ffffffff", "r9")):  # the same run, or a new one after a restart
            with self.assertRaises(ValueError):
                dayplan.check(run, rowname, DATE, "10:00", 30, "5", "6")
        dayplan.mark(self.run, DATE, "r1", self.entry, "logged", id="123")
        with self.assertRaises(ValueError):
            dayplan.check(self.run, "r1", DATE, "10:00", 30, "5", "6")
        dayplan.check(self.run, "r2", DATE, "11:00", 30, "5", "6")  # another entry is fine

    def test_a_refused_row_may_be_tried_again(self):
        dayplan.mark(self.run, DATE, "r1", self.entry, "refused", reason="overlap")
        dayplan.check(self.run, "r1", DATE, "10:00", 30, "5", "6")

    def test_the_journal_is_owner_only_and_names_are_checked(self):
        dayplan.mark(self.run, DATE, "r1", self.entry, "sending")
        self.assertEqual(stat.S_IMODE(os.stat(dayplan.journal_path()).st_mode), 0o600)
        for run, rowname in (("ld-2026-11-05-0123abcd", "r1"), ("x", "r1"), (self.run, "a b"), (self.run, "")):
            with self.assertRaises(ValueError):
                dayplan.check(run, rowname, DATE, "10:00", 30, "5", "6")

    def test_undo_chooses_only_its_own_logged_entries(self):
        other = "ld-2026-11-04-99999999"
        dayplan.mark(self.run, DATE, "r1", self.entry, "logged", id="101")
        dayplan.mark(self.run, DATE, "r2", {**self.entry, "start": "11:00"}, "unknown", reason="timeout")
        dayplan.mark(self.run, DATE, "r3", {**self.entry, "start": "12:00"}, "refused", reason="overlap")
        dayplan.mark(other, DATE, "r1", {**self.entry, "start": "13:00"}, "logged", id="202")
        self.assertEqual([x["id"] for x in dayplan.undo_ids(dayplan.journal(), self.run)], ["101"])
        self.assertEqual([x["id"] for x in dayplan.undo_ids(dayplan.journal(), other)], ["202"])
        dayplan.mark_undone(self.run, "101")
        self.assertEqual(dayplan.undo_ids(dayplan.journal(), self.run), [])


class WriteTest(unittest.TestCase):
    """POST /api/log and /api/entry/delete with a run: the journal comes first, and Undo touches only its own."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.existing, self.created, self.deleted, self.seen = [], [], [], []

        def create(e, s):
            self.seen.append([x["status"] for r in dayplan.journal()["runs"] for x in r["rows"]])  # what the disk said
            self.created.append(e)
            return {"id": str(500 + len(self.created))}

        self.patches = [mock.patch.object(config, "CACHE_DIR", self.dir),
                        mock.patch.object(server, "current_settings", lambda: {"timezone": "UTC", "person_id": "1"}),
                        mock.patch.object(server.config, "tz", lambda s: timezone.utc),
                        mock.patch.object(server.productive, "entries", lambda d0, d1, s: (self.existing, None, "token")),
                        mock.patch.object(server.productive, "create_entry", create),
                        mock.patch.object(server.productive, "delete_entry", lambda i, s: self.deleted.append(i)),
                        mock.patch.dict(server._just_logged, clear=True),
                        mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "", "PULSAR_PREVIEW": ""})]  # the fakes only
        for p in self.patches:
            p.start()
        self.run = "ld-2026-11-04-0123abcd"

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def body(self, **kw):
        return {"date": DATE, "start": "10:00", "minutes": 30, "taskId": "5", "serviceId": "6", "run": self.run,
                "row": "r1", **kw}

    def test_the_journal_says_sending_before_the_write_and_logged_after(self):
        got = server.api_log(self.body())
        self.assertEqual(got["created"], {"id": "501"})
        self.assertEqual(self.seen, [["sending"]])
        self.assertEqual(dayplan.undo_ids(dayplan.journal(), self.run)[0]["id"], "501")
        server._just_logged.clear()
        with self.assertRaises(ValueError):  # a restart that re-sends the row: refused, not written twice
            server.api_log(self.body(run="ld-2026-11-04-feedbeef", row="r7"))
        self.assertEqual(len(self.created), 1)

    def test_an_unclear_answer_is_recorded_as_unknown_and_never_resent(self):
        def unclear(e, s):
            raise productive.ProductiveUnclear("No clear answer")
        with mock.patch.object(server.productive, "create_entry", unclear):
            with self.assertRaises(productive.ProductiveError):
                server.api_log(self.body())
        self.assertEqual(dayplan.journal()["runs"][0]["rows"][0]["status"], "unknown")
        with self.assertRaises(ValueError):
            server.api_log(self.body())
        self.assertEqual(self.created, [])

    def test_an_overlap_is_refused_with_the_reason_and_can_be_tried_again(self):
        self.existing = [{"id": "1", "start": T0 + 600 * 60, "minutes": 15, "project": "P", "task": "T"}]
        self.assertIn("overlap", server.api_log(self.body()))
        x = dayplan.journal()["runs"][0]["rows"][0]
        self.assertEqual(x["status"], "refused")
        self.assertIn("overlap", x["reason"])
        self.existing = []
        self.assertIn("created", server.api_log(self.body()))

    def test_a_dry_run_writes_nothing_and_journals_nothing(self):
        got = server.api_log(self.body(dry=True))
        self.assertEqual(got["wouldWrite"]["started_at"], "2026-11-04T10:00:00+00:00")
        self.assertEqual((self.created, dayplan.journal()["runs"]), ([], []))

    def test_read_only_refuses_the_write_before_the_journal(self):
        self.patches[4].stop()
        try:
            with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "1"}):
                with self.assertRaisesRegex(ValueError, "read-only"):
                    server.api_log(self.body())
                self.assertIn("wouldWrite", server.api_log(self.body(dry=True)))  # a dry run still works
                with self.assertRaises(productive.ProductiveError):  # and the lowest level refuses on its own
                    productive.create_entry({}, {})
        finally:
            self.patches[4].start()
        self.assertEqual(dayplan.journal()["runs"], [])

    def test_undo_deletes_only_this_runs_entries_and_only_as_written(self):
        server.api_log(self.body())                                         # 501, 10:00
        server.api_log(self.body(row="r2", start="11:00"))                  # 502, 11:00
        self.existing = [{"id": "501", "date": DATE, "start": T0 + 600 * 60, "minutes": 30, "taskId": "5", "project": "P", "personId": "1"},
                         {"id": "502", "date": DATE, "start": T0 + 660 * 60, "minutes": 45, "taskId": "5", "project": "P", "personId": "1"},
                         {"id": "777", "date": DATE, "start": T0 + 720 * 60, "minutes": 30, "taskId": "5", "project": "P", "personId": "1"}]
        with self.assertRaises(ValueError):  # not created by this run
            server.api_entry_delete({"id": "777", "date": DATE, "run": self.run})
        with self.assertRaises(ValueError):  # changed since (45 minutes now): left alone
            server.api_entry_delete({"id": "502", "date": DATE, "run": self.run})
        self.assertEqual(server.api_entry_delete({"id": "501", "date": DATE, "run": self.run}), {"deleted": "501"})
        self.assertEqual(self.deleted, ["501"])
        self.assertEqual([x["id"] for x in dayplan.undo_ids(dayplan.journal(), self.run)], ["502"])


class AskAgainTest(unittest.TestCase):
    def test_an_empty_answer_is_asked_once_more_and_no_more(self):
        from pulsar import askclaude
        answers = [{"picks": []}, {"picks": [{"id": 0, "tasks": ["7"], "why": "w"}]}]
        calls = []

        def fake(cmd, **kw):
            calls.append(1)
            got = answers[min(len(calls), len(answers)) - 1]
            return mock.Mock(stdout=json.dumps({"result": "", "structured_output": got}), stderr="", returncode=0)

        rows = [{"s": 540, "e": 600, "project": "acme", "titles": [], "minutes": 60}]
        entries = [{"taskId": "7", "task": "Estimations", "project": "P", "minutes": 30, "date": "2026-11-02"}]
        with mock.patch.object(askclaude.productive, "_claude_binary", lambda: "/bin/claude"), \
                mock.patch.dict(askclaude._cache, clear=True):
            got = askclaude.ask("2026-11-05", rows, 0, entries, run=fake)
            self.assertEqual((len(calls), list(got["picks"])), (2, ["540|acme"]))
            self.assertEqual(askclaude.latest("2026-11-05")["picks"]["540|acme"]["tasks"][0]["taskId"], "7")
            answers[1] = {"picks": []}
            calls.clear()
            got = askclaude.ask("2026-11-06", rows, 0, entries, run=fake)
        self.assertEqual((len(calls), got["picks"]), (2, {}))


class DayplanAPITest(unittest.TestCase):
    def test_ignore_logged_is_for_read_only_test_servers_only(self):
        with mock.patch.object(dayplan, "gather", lambda d, src, ign: {"ignored": ign, "entries": []}):
            env = {k: v for k, v in os.environ.items() if k != "PULSAR_READ_ONLY"}
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError):
                    server.api_dayplan({"date": DATE, "ignore_logged": "1"})
                self.assertFalse(server.api_dayplan({"date": DATE})["ignored"])
            with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "1"}):
                self.assertTrue(server.api_dayplan({"date": DATE, "ignore_logged": "1"})["ignored"])
            with self.assertRaises(ValueError):
                server.api_dayplan({"date": DATE, "source": "x"})

    def test_no_proposal_when_the_days_entries_cannot_be_read(self):
        s = {"timezone": "UTC", "person_id": "1", "rules": [], "person_name": ""}
        with mock.patch.object(server, "current_settings", lambda: s), \
                mock.patch.object(server.config, "tz", lambda s: timezone.utc), \
                mock.patch.object(server, "api_activity", lambda q: {"items": [], "away": []}), \
                mock.patch.object(server, "productive_rows", lambda d0, d1, s: ([], "Productive is down.", "token", False)), \
                mock.patch.object(server, "history_rows", lambda d, s, days=28: ([], None)), \
                mock.patch.object(server, "api_repeats", lambda q: {"repeats": []}), \
                mock.patch.object(server, "api_suggest", lambda q: {"recent": [], "assigned": []}), \
                mock.patch.object(dayplan.config, "tz", lambda s: timezone.utc), \
                mock.patch("pulsar.likely.enabled", lambda s: False), \
                mock.patch("pulsar.calendar.day", lambda d, s, fresh=False: {"events": [], "connected": False}), \
                mock.patch("pulsar.calendar.history", lambda f, n, s: []):
            got = dayplan.gather(DATE)
        self.assertIn("nothing is proposed", got["error"])
        self.assertEqual(got["entries"], [])


if __name__ == "__main__":
    unittest.main()
