"""A stretch picked in the Day view (pulsar/ranges.py, GET /api/range): what it holds by Log the day's minute ownership,
the task and note that lead the log dialog, when it is mixed, and the entries a split would make. Made-up days only.

    python3 -m unittest discover -s tests
"""

import os
import re
import shutil
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import dayplan, ranges, server  # noqa: E402

DATE = "2026-11-04"
WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
OPTS = {"grid": 5, "min_entry": 10}


def work(s, e, app="Cursor", title="x", host=""):
    return {"s": s, "e": e, "app": app, "title": title, "host": host, "url": "", "cat": "work"}


def row(s, e, project="repo", titles=("Fix the importer",), commits=()):
    return {"s": s, "e": e, "minutes": e - s, "project": project, "kind": "focused", "titles": list(titles),
            "first_message": "", "tasks": [], "commits": list(commits)}


def day(**kw):
    return {"items": [], "away": [], "rows": [], "logged": [], "meetings": [], "repeats": [], "calls": [], "rules": [],
            "assigned": [], "picks": {}, **kw}


def ctx_for(tasks: dict):
    """Past days where each session title ran an hour and its task was logged then: strong votes."""
    hist, stored = [], {}
    for i in range(1, 9):
        d = f"2026-10-{28 - i:02d}"
        t0 = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        for j, (title, task) in enumerate(tasks.items()):
            s = 540 + j * 90
            stored.setdefault(d, []).append({"k": [dayplan.title_key(title)], "p": "repo", "s": s, "e": s + 60})
            hist.append({"date": d, "start": t0 + s * 60, "minutes": 60, "taskId": task, "serviceId": "s" + task,
                         "service": "Dev", "task": f"Task {task}", "project": "P"})
    t0_of = lambda d: datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()  # noqa: E731
    return dayplan.learn(DATE, hist, stored, t0_of, person="Nick Example")


def analyse(d, ctx, s, e):
    keep = {}
    dayplan.plan(d, ctx, OPTS, keep)
    return ranges.analyse(keep, s, e, OPTS)


SCRUM = {"s": 600, "e": 630, "minutes": 30, "at": 600, "taskId": "77", "title": "Scrum", "project": "PM",
         "serviceId": "s77", "service": "Admin", "note": "Daily meeting", "days": 12}


class AnalyseTest(unittest.TestCase):
    def setUp(self):
        self.ctx = ctx_for({"Fix the importer": "11", "Hero video": "22"})

    def test_keep_changes_nothing_in_the_plan(self):
        d = day(items=[work(540, 640)], rows=[row(540, 600), row(600, 640, project="site", titles=["Hero video"])])
        keep = {}
        self.assertEqual(dayplan.plan(d, self.ctx, OPTS, keep), dayplan.plan(d, self.ctx, OPTS))
        self.assertEqual(len(keep["owner"]), 1440)
        self.assertTrue({"claims", "pieces", "work", "away", "names", "ctx"} <= set(keep))

    def test_one_project_leads_with_log_the_days_task_and_its_commits_note(self):
        res = analyse(day(items=[work(540, 600)], rows=[row(540, 600, commits=["Importer: skip empty rows",
                                                                               "Importer: log the count"])]),
                      self.ctx, 540, 600)
        d = res["dominant"]
        self.assertEqual((d["taskId"], d["kind"], d["label"], d["confidence"], d["share"]), ("11", "claude", "repo", "high", 1.0))
        self.assertIn("session", d["why"])
        self.assertEqual((d["note"], d["noteFrom"]), ("Importer: skip empty rows; log the count", "commits"))
        self.assertEqual((res["mixed"], res["split"], res["work"], res["minutes"]), (False, [], 60, 60))

    def test_two_kinds_of_work_of_20_minutes_each_are_mixed_and_split_on_the_grid(self):
        d = day(items=[work(540, 620)], rows=[row(540, 583), row(583, 620, project="site", titles=["Hero video"])])
        res = analyse(d, self.ctx, 540, 620)
        self.assertTrue(res["mixed"])
        self.assertEqual([(x["start"], x["minutes"], x["taskId"], x["label"]) for x in res["split"]],
                         [("09:00", 45, "11", "repo"), ("09:45", 35, "22", "site")])  # 09:43 goes on the grid
        self.assertEqual(sum(x["minutes"] for x in res["split"]), 80)  # the whole range, nothing more
        self.assertEqual(res["dominant"]["taskId"], "11")

    def test_a_task_holding_most_of_the_range_with_nothing_else_of_20_minutes_is_not_mixed(self):
        d = day(items=[work(540, 612)], rows=[row(540, 600), row(600, 612, project="site", titles=["Hero video"])])
        res = analyse(d, self.ctx, 540, 612)
        self.assertEqual([g["taskId"] for g in res["groups"]], ["11", "22"])  # the parts are there...
        self.assertFalse(res["mixed"])  # ...but 60 of 72 minutes is most of it
        self.assertEqual(res["split"], [])

    def test_no_part_holding_three_quarters_is_mixed_even_when_short(self):
        d = day(items=[work(540, 572)], rows=[row(540, 558), row(558, 572, project="site", titles=["Hero video"])])
        res = analyse(d, self.ctx, 540, 572)  # 18 and 14 minutes: neither reaches 20, neither holds three quarters
        self.assertTrue(res["mixed"])
        self.assertEqual([x["taskId"] for x in res["split"]], ["11", "22"])

    def test_a_meeting_and_work_split_with_the_meetings_note(self):
        d = day(items=[work(540, 600), work(600, 630, app="zoom.us", title="Zoom Meeting")],
                calls=[{"s": 601, "e": 629}], repeats=[SCRUM], rows=[row(540, 600)])
        res = analyse(d, self.ctx, 540, 630)
        self.assertTrue(res["mixed"])
        meet = next(g for g in res["groups"] if g["taskId"] == "77")
        self.assertEqual((meet["kind"], meet["label"], meet["note"], meet["noteFrom"]),
                         ("meeting", "a meeting", "Daily meeting", "repeat"))
        self.assertEqual([(x["start"], x["minutes"], x["taskId"]) for x in res["split"]],
                         [("09:00", 60, "11"), ("10:00", 30, "77")])

    def test_time_already_logged_and_away_time_stay_out_of_the_share_and_the_split(self):
        d = day(items=[work(540, 600), work(610, 640)], away=[{"s": 600, "e": 610}],
                logged=[{"s": 570, "minutes": 10, "taskId": "99"}],
                rows=[row(540, 600), row(610, 640, project="site", titles=["Hero video"])])
        res = analyse(d, self.ctx, 540, 640)
        kinds = {p["kind"]: p["minutes"] for p in res["parts"]}
        self.assertEqual((kinds["logged"], kinds["away"], res["work"]), (10, 10, 80))
        self.assertTrue(res["mixed"])
        spans = [(x["s"], x["s"] + x["minutes"], x["taskId"]) for x in res["split"]]
        self.assertEqual(spans, [(540, 570, "11"), (580, 610, "11"), (610, 640, "22")])  # away goes with the work before
        self.assertFalse(any(a < 580 and b > 570 for a, b, _ in spans))  # never over the entry already logged

    def test_work_outside_claude_takes_its_neighbours_task_and_says_so(self):
        d = day(items=[work(540, 560), work(560, 600, app="Safari", title="Docs")], rows=[row(540, 560)])
        res = analyse(d, self.ctx, 540, 600)
        self.assertEqual([(g["taskId"], g["minutes"]) for g in res["groups"]], [("11", 60)])
        # the page does not lead with it (range.js): the neighbour's task did worse than the dialog's own first task
        self.assertEqual(res["dominant"]["kind"], "outside")
        self.assertFalse(res["mixed"])

    def test_a_call_with_no_task_is_its_own_part(self):
        d = day(items=[work(540, 580), work(580, 610, app="zoom.us", title="Zoom Meeting")],
                calls=[{"s": 580, "e": 610}], rows=[row(540, 580)])
        res = analyse(d, self.ctx, 540, 610)
        call = next(g for g in res["groups"] if g["kind"] == "call")
        self.assertEqual((call["taskId"], call["label"], call["minutes"]), (None, "a call", 30))
        self.assertTrue(res["mixed"])
        self.assertEqual([x["taskId"] for x in res["split"]], ["11", None])  # listed, never logged without a task

    def test_two_tasks_in_one_repository_are_told_apart_by_task(self):
        groups = [{"label": "repo", "title": "A task with a rather long name that goes on and on"},
                  {"label": "repo", "title": "Short"}, {"label": "site", "title": "X"}]
        ranges._unique_labels(groups)
        self.assertEqual([g["label"] for g in groups],
                         ["repo (A task with a rather long name that…)", "repo (Short)", "site"])


class SplitTest(unittest.TestCase):
    def test_a_short_piece_joins_its_neighbour(self):
        got = ranges._split(["A"] * 30 + ["B"] * 6 + ["A"] * 30, 540, OPTS)
        self.assertEqual(got, [(540, 66, "A", 66)])

    def test_a_short_piece_alone_after_an_entry_already_logged_is_left_out(self):
        got = ranges._split(["A"] * 30 + ["logged"] * 10 + ["B"] * 4, 540, OPTS)
        self.assertEqual(got, [(540, 30, "A", 30)])

    def test_minutes_with_no_work_go_with_the_work_before_and_count_as_no_work(self):
        got = ranges._split(["A"] * 23 + [None] * 5 + ["B"] * 22, 540, OPTS)
        self.assertEqual(got, [(540, 30, "A", 23), (570, 20, "B", 22)])  # 09:28 on the grid: 09:30
        got = ranges._split([None] * 5 + ["A"] * 20, 540, OPTS)
        self.assertEqual(got, [(540, 25, "A", 20)])  # none before: with the work after, from the range's start

    def test_idle_minutes_never_make_a_short_piece_long_enough_to_stand_alone(self):
        got = ranges._split(["A"] * 30 + ["B"] * 4 + [None] * 20 + ["A"] * 30, 540, OPTS)
        self.assertEqual(got, [(540, 84, "A", 64)])


class ServicesTest(unittest.TestCase):
    def test_each_split_entry_gets_the_service_last_used_and_a_name(self):
        res = {"split": [{"taskId": "11", "title": ""}, {"taskId": "22", "title": "Hero"}, {"taskId": None},
                         {"taskId": "33", "title": "Gone"}],
               "groups": [{"taskId": "44", "title": ""}]}
        found = {"11": {"task": {"title": "Importer", "project": "P", "serviceId": "s2"},
                        "services": [{"id": "s1", "name": "Dev ", "budget": "B", "billable": True},
                                     {"id": "s2", "name": "QA", "budget": "B", "billable": False}]},
                 "22": {"task": {"title": "Hero", "project": "P"}, "services": []},
                 "44": {"task": {"title": "Named later", "project": "Q"}, "services": []}}
        asked = []

        def bookable(tid):
            asked.append(tid)
            return found.get(tid)
        ranges.with_services(res, {"service": {"11": ("s1", "Dev")}}, bookable)
        a, b, c, d = res["split"]
        self.assertEqual((a["serviceId"], a["service"], a["title"], a["billable"]), ("s1", "Dev", "Importer", True))
        self.assertIn("No service", b["serviceProblem"])
        self.assertNotIn("serviceId", c)
        self.assertIn("did not list", d["serviceProblem"])
        self.assertEqual(res["groups"][0]["title"], "Named later")
        self.assertEqual(sorted(asked), ["11", "22", "33", "44"])  # once each


class RangeAPITest(unittest.TestCase):
    S = {"timezone": "UTC", "person_id": "1", "rules": [], "person_name": ""}

    def gather(self, d):
        def fake(date, source="auto", ignore=False, keep=None):
            self.asked = (date, source, ignore)
            dayplan.plan(d, ctx_for({"Fix the importer": "11", "Hero video": "22"}), OPTS, keep)
            return {"entries": [], "roundTo": 5}
        return fake

    def test_the_range_with_services_for_its_split(self):
        d = day(items=[work(540, 620)], rows=[row(540, 583), row(583, 620, project="site", titles=["Hero video"])])
        bookable = lambda tid, date, s: {"task": {"title": f"Task {tid}", "project": "P"},  # noqa: E731
                                         "services": [{"id": "s" + tid, "name": "Dev"}]}
        with mock.patch.object(server, "current_settings", lambda: self.S), \
                mock.patch.object(dayplan, "gather", self.gather(d)), \
                mock.patch.object(dayplan, "_bookable", bookable):
            got = server.api_range({"date": DATE, "s": "540", "e": "620", "source": "aw"})
        self.assertEqual(self.asked, (DATE, "aw", False))
        self.assertTrue(got["mixed"])
        self.assertEqual([(x["start"], x["taskId"], x["serviceId"]) for x in got["split"]],
                         [("09:00", "11", "s11"), ("09:45", "22", "s22")])
        self.assertEqual((got["date"], got["roundTo"], got["ignoreLogged"]), (DATE, 5, False))

    def test_a_day_that_cannot_be_worked_out_says_why(self):
        with mock.patch.object(server, "current_settings", lambda: self.S), \
                mock.patch.object(dayplan, "gather", lambda *a, **k: {"error": "Connect Productive in Settings first.",
                                                                      "entries": []}):
            got = server.api_range({"date": DATE, "s": "540", "e": "620"})
        self.assertEqual((got["error"], got["split"]), ("Connect Productive in Settings first.", []))

    def test_bad_requests_are_refused(self):
        with mock.patch.object(dayplan, "gather", lambda *a, **k: self.fail("never worked out")):
            for q in ({"date": "4 Nov", "s": "1", "e": "2"}, {"date": DATE, "s": "600", "e": "600"},
                      {"date": DATE, "s": "-5", "e": "60"}, {"date": DATE, "s": "0", "e": "1441"},
                      {"date": DATE, "s": "x", "e": "60"}, {"date": DATE, "e": "60"},
                      {"date": DATE, "s": "0", "e": "60", "source": "x"}):
                with self.assertRaises(ValueError, msg=q):
                    server.api_range(q)

    def test_ignore_logged_is_for_read_only_test_servers_only(self):
        d = day(items=[work(540, 600)], rows=[row(540, 600)])
        env = {k: v for k, v in os.environ.items() if k != "PULSAR_READ_ONLY"}
        with mock.patch.object(server, "current_settings", lambda: self.S), \
                mock.patch.object(dayplan, "gather", self.gather(d)), \
                mock.patch.object(dayplan, "_bookable", lambda *a: None):
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError):
                    server.api_range({"date": DATE, "s": "540", "e": "600", "ignore_logged": "1"})
            with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "1"}):
                self.assertTrue(server.api_range({"date": DATE, "s": "540", "e": "600", "ignore_logged": "1"})["ignoreLogged"])

    def test_gather_hands_the_minutes_over(self):
        from pulsar import askclaude, calendar, learn, likely
        rows = [row(540, 600)]
        keep = {}
        with mock.patch.object(server, "current_settings", lambda: self.S), \
                mock.patch.object(dayplan.config, "tz", lambda s: timezone.utc), \
                mock.patch.object(server, "api_activity", lambda q: {"items": [work(540, 600)], "away": []}), \
                mock.patch.object(server, "productive_rows", lambda d0, d1, s: ([], None, "token", False)), \
                mock.patch.object(server, "history_rows", lambda d, s, days=28: ([], None)), \
                mock.patch.object(server, "api_repeats", lambda q: {"repeats": []}), \
                mock.patch.object(server, "api_suggest", lambda q: {"recent": [], "assigned": []}), \
                mock.patch.object(server, "new_task_source", lambda d, s: None), \
                mock.patch.object(likely, "enabled", lambda s: True), \
                mock.patch.object(likely, "proposal", lambda *a, **k: {"entries": [dict(r) for r in rows]}), \
                mock.patch.object(calendar, "day", lambda d, s, fresh=False: {"events": [], "connected": False}), \
                mock.patch.object(calendar, "history", lambda f, n, s: []), \
                mock.patch.object(learn, "_stored", lambda d: None), \
                mock.patch.object(askclaude, "latest", lambda d: None):
            dayplan.gather(DATE, keep=keep)
        res = ranges.analyse(keep, 540, 600, OPTS)
        self.assertEqual([(p["kind"], p["label"], p["minutes"]) for p in res["parts"]], [("claude", "repo", 60)])


class ClockChangeTest(unittest.TestCase):
    """The night the clocks go forward, the hour they skip is on the page's clock but never happened: a split entry
    across it is that much shorter, so it never runs into the next one (the fourth review, C10)."""

    def test_a_split_entry_across_the_skipped_hour_counts_real_minutes(self):
        def day(gone):
            return {"items": [work(60, 300, app="Code")], "logged": [{"s": 140, "e": 160, "minutes": 20, "taskId": "33"}],
                    "rows": [dict(row(60, 150, "acme"), tasks=["11"]), dict(row(150, 300, "globex"), tasks=["22"])],
                    "gone": gone}
        ctx = dayplan.learn("2026-03-08", [], {}, lambda d: 0)
        got = {}
        for name, gone in (("normal", []), ("spring", [{"s": 120, "e": 180}])):
            keep: dict = {}
            dayplan.plan(day(gone), ctx, OPTS, keep=keep)
            got[name] = [(x["s"], x["minutes"], x["taskId"]) for x in ranges.analyse(keep, 60, 300, OPTS)["split"]]
        self.assertEqual(got["normal"], [(60, 80, "11"), (160, 140, "22")])
        self.assertEqual(got["spring"], [(60, 60, "11"), (180, 120, "22")])  # 01:00-03:00 on the clock is one hour

    def test_the_page_counts_real_minutes_from_the_clock(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        with open(os.path.join(WEB, "js", "core.js"), encoding="utf-8") as fh:
            src = fh.read()

        def body(name):  # one function of core.js, whole
            i = src.index(f"function {name}(")
            j, depth = src.index("{", i), 0
            while True:
                depth += {"{": 1, "}": -1}.get(src[j], 0)
                if not depth:
                    return src[i:j + 1]
                j += 1
        code = "\n".join(body(n) for n in ("validDate", "zoneOffset", "clockMoment", "realMinutes", "clockAfter"))
        script = code + """
const out = [];
for (const [tz, d] of [['America/Toronto', '2026-03-08'], ['America/Toronto', '2026-11-01'], ['Europe/Kyiv', '2026-03-29'],
                       ['America/Toronto', '2026-06-01']]) {
  TZ = tz;
  out.push([realMinutes(d, 90, 210), realMinutes(d, 0, 1440), clockAfter(d, 90, 60)].join(' '));
}
console.log(out.join(','));"""
        got = subprocess.run([node, "-e", "let TZ;\n" + script], capture_output=True, text=True, timeout=30)
        # Toronto spring: 01:30-03:30 is 60 min, the day 23 h; autumn: 180 min, 25 h; Kyiv spring (03:00 -> 04:00): 120
        self.assertEqual(got.stdout.strip(), "60 1380 210,180 1500 150,120 1380 150,120 1440 150", got.stderr)
        flow = self.read_js("flow.js")
        self.assertIn("setMinutes(realMinutes(date, a, b))", flow)  # End sets real minutes
        self.assertIn("realMinutes(state.date, a, b)", self.read_js("day.js") + self.read_js("log.js"))

    def read_js(self, name):
        with open(os.path.join(WEB, "js", name), encoding="utf-8") as fh:
            return fh.read()


class PageTest(unittest.TestCase):
    """The page side (web/js/range.js): small hooks in log.js, loaded after log.js and flow.js, never forcing a write."""

    def read(self, name):
        with open(os.path.join(WEB, name), encoding="utf-8") as fh:
            return fh.read()

    def test_it_is_loaded_after_the_dialog_it_works_in(self):
        html = self.read("index.html")
        self.assertLess(html.index("/static/js/log.js"), html.index("/static/js/range.js"))
        self.assertLess(html.index("/static/js/flow.js"), html.index("/static/js/range.js"))
        self.assertIn('href="/static/range.css"', html)
        self.assertIn('id="log-range"', html)

    def test_the_dialog_asks_only_for_stretches_and_puts_the_range_first(self):
        log = self.read("js/log.js")
        self.assertIn("rangeOpen(range && !edit ? {date, start, minutes} : null)", log)  # never for an entry being edited
        self.assertIn("rangeLead(date)", log)
        self.assertIn("range: true", log)  # + Log after the last entry
        self.assertEqual(len(re.findall(r"logStretch\(", log)), 2)  # a drag, + Log on a rail: as a stretch
        self.assertIn("logStretch(s.a, s.b)", self.read("js/flow.js"))  # Log and next
        day = self.read("js/day.js")
        self.assertRegex(day, r"function logStretch[\s\S]{0,600}range: true")  # every stretch goes through range.js
        self.assertIn("logBtn.dataset.likely == null) { logStretch(a, b)", day)  # rails, Activity blocks

    def test_a_drag_to_the_minute_is_not_rounded_again(self):
        """The Logged column drags in 1-minute steps: the dialog keeps those minutes, never "Round entries to"."""
        log = self.read("js/log.js")
        self.assertIn("logStretch(a, b, true)", log)
        self.assertIn("if (!edit && !exact) minutes = roundMinutes(", log)
        self.assertRegex(self.read("js/day.js"), r"function logStretch\(a, b, exact = false\)[\s\S]{0,900}range: true, exact\}")

    def test_the_split_logs_one_entry_at_a_time_and_never_forces(self):
        code = self.read("js/range.js")
        self.assertEqual(re.findall(r"postJSON\('(/api/[a-z/]+)'", code), ["/api/log"])
        self.assertNotRegex(code, r"force\s*:")  # never "Log anyway"
        self.assertNotRegex(code, r"dry\s*:")
        self.assertIn("/api/range?", code)
        self.assertIn("rangeNow.typed", code)  # what the person wrote is never replaced

    def test_a_dragged_edge_moves_by_the_pointer_not_to_it(self):
        """day.js: a logged card's edge moves by as many minutes as the pointer moved since it was grabbed. Taken from
        the pointer's place, it jumped on the first move: the 6 px handle is up to 4 minutes wide at 30-minute rows,
        and a short card is drawn 18 px tall whatever its minutes, so a 10-minute entry grabbed by its bottom edge and
        let go where it was asked to become 13 minutes (27 at 1-hour rows)."""
        day = self.read("js/day.js")
        resize = day[day.index("(function resizeEntries()"):day.index("function askResize(")]
        self.assertIn("y0: ev.clientY - lane.getBoundingClientRect().top", resize)
        self.assertIn("Math.round((ev.clientY - lane.getBoundingClientRect().top - drag.y0) / ppm)", resize)
        self.assertIn("Math.round(drag.x.s) + d", resize)
        self.assertIn("Math.round(drag.x.e) + d", resize)

    def test_a_drag_keeps_its_minutes_and_old_windows_sign_in_again(self):
        """log.js: a stretch dragged in the Logged column opens the dialog with the minutes dragged, not rounded to
        "Round entries to". main.js: after an update a window of 1.4.0 or older, which ignores 'reload', is asked
        'signin' as well, so it does not stay signed out."""
        log, day, main = self.read("js/log.js"), self.read("js/day.js"), self.read("js/main.js")
        self.assertIn("logStretch(a, b, true)", log)
        self.assertIn("if (!edit && !exact) minutes = roundMinutes(", log)
        self.assertIn("range: true, exact});", day)
        reopen = main[main.index("async function reopenSignedIn("):]
        self.assertLess(reopen.index("toApp('reload')"), reopen.index("toApp('signin')"))

    def test_settings_say_ask_claudes_answers_are_kept(self):
        """askclaude.keep: a day's picks stay on disk for KEEP_DAYS, across restarts; Settings said they last until
        Pulsar restarts."""
        from pulsar import askclaude, claudeuses  # Settings > Claude shows each use's words (claudeuses.USES)
        said = claudeuses.USES["ask"]["does"] + " " + claudeuses.USES["ask"]["sends"]
        self.assertNotIn("until Pulsar restarts", said)
        self.assertIn(f"kept on this Mac for {askclaude.KEEP_DAYS} days", said)

    def test_a_late_range_answer_never_replaces_a_task_picked_or_created(self):
        """log.js's own picker, run in node on a stub page: a task just created and picked (newtask.js) stays picked
        when the stretch's answer arrives later (rangeOpen -> lead); one the dialog chose by itself gives way."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        src = self.read("js/log.js")
        picker = src[src.index("function dedupe("):src.index("/* ---------- the log dialog ---------- */")]
        script = """
const el = () => ({innerHTML: '', classList: {add() {}, remove() {}}, contains: () => false, querySelector: () => null,
                   querySelectorAll: () => [], addEventListener() {}, value: '', focus() {}});
const esc = s => String(s), dur = m => m + ' min', CLAUDE_MARK = '', document = {activeElement: null},
      todayLocal = () => '2026-11-07', dayLabel = d => d;
""" + picker + """
const out = [], p = makePicker(el(), el(), t => out.push(t.taskId));
const lead = {label: 'For this time', tasks: [{taskId: '555', title: 'Guess', project: 'Acme'}]};
p.loading(); p.set([{label: 'Lately', tasks: [{taskId: '1', title: 'Old', project: 'Acme'}]}], null);
p.pick({taskId: '999', title: 'Just created', project: 'Acme'}); p.lead(lead); out.push('|' + p.selected.taskId);
p.loading(); p.set([{label: 'Lately', tasks: [{taskId: '1', title: 'Old', project: 'Acme'}]}], {taskId: '1', title: 'Old'});
p.lead(lead); out.push('|' + p.selected.taskId);
console.log(out.join(','));"""
        got = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(got.stdout.strip(), "999,|999,1,555,|555", got.stderr)
        code = self.read("js/range.js")  # an answer for a time the person has since changed leads nothing
        self.assertLess(code.index("if (rangeNow.stale) return rangeDraw();"), code.index("logPicker.lead(g)"))


if __name__ == "__main__":
    unittest.main()
