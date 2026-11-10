"""Ticket flags (flags.py), "Round entries to" and catching up on earlier days, on made-up tasks and days. No network:
   Productive is faked.

    python3 -m unittest discover -s tests
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import config, dayplan, flags, productive, server  # noqa: E402
from test_dayplan import day, history_for, row, work  # noqa: E402


def names():
    return productive.Names(os.path.join(tempfile.mkdtemp(), "names.json"))


def task(tid="7", project="9", estimate=None, closed=False):
    return {"taskId": tid, "title": "Fix the menu", "projectId": project, "estimate": estimate, "closed": closed}


class FlagsTest(unittest.TestCase):
    def setUp(self):
        flags._failed.clear()
        self.names = names()
        self.reads = []
        self.share = {"n": 8, "with": ["1", "2", "3"]}

        def read(pid, settings):
            self.reads.append(pid)
            if isinstance(self.share, Exception):
                raise self.share
            return self.share
        p = mock.patch.object(flags.productive, "project_estimates", read)
        p.start()
        self.addCleanup(p.stop)

    def kinds(self, t, services=({"id": "1"},)):
        return [f["kind"] for f in flags.for_task(t, None if services is None else list(services), "2026-11-06", {}, self.names)]

    def test_a_closed_task_and_a_date_with_no_budget_are_flagged(self):
        got = flags.for_task(task(closed=True, estimate=60), [], "2026-11-06", {}, self.names)
        self.assertEqual([f["kind"] for f in got], ["closed", "nobudget"])
        self.assertEqual(got[1]["text"], "No service takes time on 6 Nov: no budget for it")
        self.assertEqual(self.kinds(task(estimate=60)), [])
        self.assertEqual(self.kinds(task(estimate=60), services=None), [])  # services not read: no guess
        self.assertEqual(self.reads, [])                                    # an estimate: the project is never asked
        self.assertEqual(flags.for_task(None, [], "2026-11-06", {}, self.names), [])

    def test_over_the_estimate_counts_the_time_worked_and_the_minutes_about_to_be_logged(self):
        t = {**task(estimate=240), "worked": 200}
        self.assertEqual(flags.for_task(t, [{"id": "1"}], "2026-11-06", {}, self.names, minutes=30),  # 230 of 240: left
                         [{"kind": "nearestimate", "text": "3 h 50 min of the 4 h estimate with this entry, 10 min left"}])
        self.assertEqual(flags.for_task(t, [{"id": "1"}], "2026-11-06", {}, self.names, minutes=-20), [])  # 180: under 80%
        [f] = flags.for_task(t, [{"id": "1"}], "2026-11-06", {}, self.names, minutes=130)
        self.assertEqual(f, {"kind": "overestimate", "text": "+1 h 30 min over the estimate (5 h 30 min of 4 h)"})
        self.assertEqual(flags.for_task(t, [{"id": "1"}], "2026-11-06", {}, self.names), [])  # no minutes: no check
        self.assertIsNone(flags.over_estimate({**t, "worked": None}, 600))  # time worked unknown: nothing said
        self.assertIsNone(flags.over_estimate({**task(), "worked": 900}, 600))  # no estimate: noestimate's business

    def test_near_the_estimate_from_80_percent_with_this_entry(self):
        # Nick, 2026-11-10 (as Timely shows it): see how much is left before going over, not only after
        t = {**task(estimate=600), "worked": 450}
        self.assertIsNone(flags.near_estimate(t, 20))                       # 470 of 600: 78%
        self.assertEqual(flags.near_estimate(t, 30)["text"], "8 h of the 10 h estimate with this entry, 2 h left")
        self.assertEqual(flags.near_estimate(t, 150)["text"], "10 h of the 10 h estimate with this entry, 0 min left")
        self.assertIsNone(flags.near_estimate(t, 151))                      # over: overestimate's business
        self.assertIsNone(flags.near_estimate({**t, "worked": None}, 100))  # time worked unknown
        self.assertIsNone(flags.near_estimate({**t, "estimate": None}, 100))

    def test_a_services_time_budget_only_where_it_can_be_read(self):
        svc = {"id": "1", "budgetTime": {"budgeted": 3000, "worked": 2400}}  # 50 h budget, 40 h used
        self.assertEqual(flags.service_budget(svc, 60)["kind"], "nearbudget")   # 41 h: 82%
        self.assertEqual(flags.service_budget(svc, 60)["text"], "41 h of the service's 50 h budget with this entry, 9 h left")
        self.assertEqual(flags.service_budget(svc, 700)["text"], "+1 h 40 min over the service's budget (51 h 40 min of 50 h)")
        self.assertIsNone(flags.service_budget({**svc, "budgetTime": {"budgeted": 3000, "worked": 1000}}, 60))
        self.assertIsNone(flags.service_budget({"id": "1"}, 6000))          # not readable (Staff): nothing said
        t = {**task(estimate=6000), "worked": 0}
        got = flags.for_task(t, [svc, {"id": "2"}], "2026-11-06", {}, self.names, minutes=60, service_id="1")
        self.assertEqual([f["kind"] for f in got], ["nearbudget"])
        self.assertEqual(flags.for_task(t, [svc, {"id": "2"}], "2026-11-06", {}, self.names, minutes=60, service_id="2"), [])
        self.assertEqual(productive.budget_time(None, None), {})              # what a Staff role gets
        self.assertEqual(productive.budget_time(0, 10), {})
        self.assertEqual(productive.budget_time("3000", 2400.0), {"budgetTime": {"budgeted": 3000.0, "worked": 2400.0}})

    def test_log_the_day_counts_the_plans_earlier_rows_on_the_same_service(self):
        svc = {"id": "1", "budgetTime": {"budgeted": 600, "worked": 400}}
        found = {"7": ({"task": {**task(), "worked": 0}, "services": [svc]}, None),
                 "8": ({"task": {**task(), "worked": 0}, "services": [svc]}, None)}
        rows = [{"s": 540, "minutes": 60, "taskId": "7", "serviceId": "1"}, {"s": 600, "minutes": 60, "taskId": "8", "serviceId": "1"}]
        dayplan.ticket_flags(rows, found, "2026-11-06", {"advanced": {"no_estimate": False}})
        self.assertEqual(rows[0]["ticket"], [])                                   # 460 of 600: 77%
        self.assertEqual([f["kind"] for f in rows[1]["ticket"]], ["nearbudget"])  # 520 of 600 with the first: 87%

    def test_log_the_day_counts_the_plans_earlier_rows_on_the_same_task(self):
        t = {**task(estimate=120), "worked": 60}
        found = {"7": ({"task": t, "services": [{"id": "1"}]}, None)}
        rows = [{"s": 600, "minutes": 40, "taskId": "7"}, {"s": 540, "minutes": 30, "taskId": "7"}]
        dayplan.ticket_flags(rows, found, "2026-11-06", {})
        self.assertEqual(rows[1]["ticket"], [])  # 9:00, first: 90 of 120
        self.assertEqual([f["text"] for f in rows[0]["ticket"]], ["+10 min over the estimate (2 h 10 min of 2 h)"])

    def test_no_estimate_is_flagged_only_where_the_projects_tasks_have_them(self):
        self.assertEqual(self.kinds(task()), ["noestimate"])  # 3 of 8 recent tasks have one
        [f] = flags.for_task(task(), [{"id": "1"}], "2026-11-06", {}, self.names)
        self.assertEqual(f["text"], "No estimate, while 3 of the project's 8 recent tasks have one")
        # Settings > Advanced can switch the flag off
        self.assertEqual(flags.for_task(task(), [{"id": "1"}], "2026-11-06", {"advanced": {"no_estimate": False}},
                                        self.names), [])
        for n, with_, flagged in ((100, 24, False), (100, 25, True), (5, 1, False), (5, 2, True), (0, 0, False)):
            self.names = names()
            self.share = {"n": n, "with": [str(i) for i in range(100, 100 + with_)]}
            self.assertEqual(self.kinds(task()) == ["noestimate"], flagged, (n, with_))
        # the task itself never counts as one of the others (its estimate was just removed)
        self.names, self.share = names(), {"n": 5, "with": ["7", "8"]}
        self.assertEqual(self.kinds(task(tid="7")), [])
        self.assertEqual(self.kinds(task(estimate=0)), [])  # an estimate of 0 is none: one other is not enough

    def test_the_share_is_read_once_a_day_and_kept_in_names_json(self):
        for _ in range(3):
            self.kinds(task())
        self.assertEqual(self.reads, ["9"])
        self.assertEqual(productive.Names(self.names.path).get("estimates:projects:9"), self.share)
        self.names.at["estimates:projects:9"] -= 86400 + 1  # a day later
        self.kinds(task())
        self.assertEqual(self.reads, ["9", "9"])

    def test_a_new_tasks_estimate_reads_again_an_answer_kept_without_the_estimates(self):
        self.kinds(task())  # kept as an older Pulsar kept it: no "sizes"
        self.assertEqual(flags.project_share("9", {}, self.names), self.share)
        self.assertEqual(self.reads, ["9"])
        self.share = {"n": 8, "with": ["1"], "sizes": [[60, 1, "Menu"]]}
        self.assertEqual(flags.project_share("9", {}, self.names, sizes=True)["sizes"], [[60, 1, "Menu"]])
        self.assertEqual(flags.project_share("9", {}, self.names, sizes=True)["sizes"], [[60, 1, "Menu"]])
        self.assertEqual(self.reads, ["9", "9"])  # once, then kept

    def test_a_failed_read_keeps_the_old_answer_and_waits_an_hour(self):
        self.kinds(task())
        self.names.at["estimates:projects:9"] -= 86400 + 1
        self.share = productive.ProductiveError("Productive answered HTTP 503.")
        with mock.patch.object(flags.logbook, "record") as record:
            self.assertEqual(self.kinds(task()), ["noestimate"])  # the old answer
            self.assertEqual(self.kinds(task()), ["noestimate"])  # not asked again within the hour
        self.assertEqual(len(self.reads), 2)
        self.assertTrue(record.called)
        # nothing kept at all: no flag, and not asked on every row
        self.names = names()
        self.assertEqual(self.kinds(task(project="10")), [])
        self.assertEqual(self.kinds(task(project="10")), [])
        self.assertEqual(self.reads.count("10"), 1)
        flags._failed["10"] -= flags.RETRY + 1
        self.share = {"n": 4, "with": ["1", "2"]}
        self.assertEqual(self.kinds(task(project="10")), ["noestimate"])

    def test_no_project_no_read(self):
        self.assertEqual(self.kinds(task(project="")), [])
        self.assertEqual(self.reads, [])


class ReadsTest(unittest.TestCase):
    """What Pulsar asks Productive for: read only, and what it makes of the answer."""

    def test_a_projects_estimates_through_claude_codes_connection(self):
        sent = []

        def mcp(tool, args, token):
            sent.append((tool, args))
            rows = ('[["tasks", "1", "2 h", "ACME - Core Site Updates", null], ["tasks", "2", null, "Menu", null], '
                    '["tasks", "3", "0 min", "Footer", "tasks:1"], ["tasks", "4", "30 min", "Hero image", "tasks:1"]]')
            return ('```json\n{"items": {"columns": ["type", "id", "initial_estimate", "title", "parent_task"], '
                    '"rows": %s}}\n```' % rows)
        with mock.patch.object(productive, "connection", lambda s: {"kind": "claude-code", "token": "t"}), \
                mock.patch.object(productive, "mcp_text", mcp):
            got = productive.project_estimates("9", {})
        # the estimates themselves too, with whether each is a subtask (a new task's estimate, newtask.propose)
        self.assertEqual(got, {"n": 4, "with": ["1", "4"], "sizes": [[120, 0, "ACME - Core Site Updates"], [30, 1, "Hero image"]]})
        self.assertEqual(sent[0][1]["include_fields"], ["initial_estimate", "title", "parent_task"])
        [(tool, args)] = sent
        self.assertEqual(tool, "query_resources")
        self.assertNotIn(tool, productive.WRITE_TOOLS)
        self.assertEqual((args["resource_type"], args["limit"], args["sort"]), ("tasks", 100, ["-last_activity_at"]))
        self.assertEqual(args["filters"]["conditions"], [{"field": "project", "operator": "eq", "value": "9"}])
        with self.assertRaises(productive.ProductiveError):
            productive.project_estimates("", {})

    def test_a_projects_estimates_through_the_token(self):
        asked = []

        def get(path, query, token, org):
            asked.append((path, query))
            return 200, {"data": [{"id": "1", "attributes": {"initial_estimate": 120, "title": "Fix"},
                                   "relationships": {"parent_task": {"data": {"type": "tasks", "id": "5"}}}},
                                  {"id": "2", "attributes": {"initial_estimate": None}}]}
        with mock.patch.object(productive, "connection", lambda s: {"kind": "token", "token": "t"}), \
                mock.patch.object(productive, "rest_get", get):
            self.assertEqual(productive.project_estimates("9", {"org_id": "1"}),
                             {"n": 2, "with": ["1"], "sizes": [[120, 1, "Fix"]]})
        self.assertEqual(asked, [("/tasks", {"filter[project_id]": "9", "page[size]": 100})])
        with mock.patch.object(productive, "connection", lambda s: {"kind": "token", "token": "t"}), \
                mock.patch.object(productive, "rest_get", lambda *a: (503, None)):
            with self.assertRaises(productive.ProductiveError):
                productive.project_estimates("9", {"org_id": "1"})

    def test_a_task_says_whether_it_is_closed(self):
        productive._task_cache.clear()

        def mcp(tool, args, token):
            self.assertIn("closed_at", args["include_fields"])
            closed = '"2026-11-02T21:09:43.000+01:00"' if args["id"] == "501" else "null"
            return ('```json\n{"item": {"id": "%s", "type": "tasks", "fields": {"title": "T", "closed_at": %s, '
                    '"project": {"type": "projects", "id": "9"}, "initial_estimate": null}}, "included": []}\n```'
                    % (args["id"], closed))
        with mock.patch.object(productive, "connection", lambda s: {"kind": "claude-code", "token": "t"}), \
                mock.patch.object(productive, "mcp_text", mcp):
            got = productive.task_details(["501", "502"], {})
        self.assertEqual((got["501"]["closed"], got["502"]["closed"]), (True, False))
        productive._task_cache.clear()

        def get(path, query, token, org):
            closed = "2026-11-02T20:09:43Z" if path.endswith("/503") else None
            return 200, {"data": {"id": path.split("/")[-1], "attributes": {"title": "T", "closed_at": closed},
                                  "relationships": {"project": {"data": {"type": "projects", "id": "9"}}}}}
        with mock.patch.object(productive, "connection", lambda s: {"kind": "token", "token": "t"}), \
                mock.patch.object(productive, "rest_get", get):
            got = productive.task_details(["503", "504"], {"org_id": "1"})
        self.assertEqual((got["503"]["closed"], got["504"]["closed"]), (True, False))
        productive._task_cache.clear()


class ServicesApiTest(unittest.TestCase):
    def test_the_page_gets_the_flags_with_the_services_and_claude_code_does_not_ask_for_them(self):
        flags._failed.clear()
        reads = []
        answer = {"task": task(closed=True), "services": []}
        with mock.patch.object(server, "current_settings", lambda: {"person_id": "1"}), \
                mock.patch.object(server.productive, "bookable_services", lambda t, d, s: dict(answer)), \
                mock.patch.object(flags.productive, "shared_names", names), \
                mock.patch.object(flags.productive, "project_estimates", lambda pid, s: reads.append(pid) or {"n": 4, "with": ["1", "2"]}):
            got = server.api_services({"task": "7", "date": "2026-11-06", "flags": "1"})
            plain = server.api_services({"task": "7", "date": "2026-11-06"})
        self.assertEqual([f["kind"] for f in got["flags"]], ["closed", "nobudget", "noestimate"])
        self.assertNotIn("flags", plain)  # pulsar_preview and the other Claude Code tools: no extra reads
        self.assertEqual(reads, ["9"])

    def test_log_the_day_reads_each_project_once_and_never_unticks_for_a_flag(self):
        flags._failed.clear()
        reads = []
        entries = [{"taskId": "1", "checked": True}, {"taskId": "2", "checked": True}, {"taskId": None, "checked": False},
                   {"taskId": "3", "checked": True}]
        found = {"1": ({"task": task("1", closed=True), "services": [{"id": "s"}]}, None),
                 "2": ({"task": task("2"), "services": []}, None),
                 "3": (None, "Productive could not be reached")}
        shared = names()
        with mock.patch.object(flags.productive, "shared_names", lambda: shared), \
                mock.patch.object(flags.productive, "project_estimates", lambda pid, s: reads.append(pid) or {"n": 4, "with": ["8", "9"]}):
            dayplan.ticket_flags(entries, found, "2026-11-06", {})
        self.assertEqual([[f["kind"] for f in e["ticket"]] for e in entries],
                         [["closed", "noestimate"], ["nobudget", "noestimate"], [], []])
        self.assertEqual(reads, ["9"])
        self.assertEqual([e["checked"] for e in entries], [True, True, False, True])


class RoundingTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.patches = [mock.patch.object(config, "CONFIG_DIR", self.dir),
                        mock.patch.object(config, "SETTINGS_FILE", os.path.join(self.dir, "settings.json")),
                        mock.patch.object(config, "_keychain", lambda: False)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_the_setting_is_5_10_or_15_and_5_by_default(self):
        self.assertEqual(config.load()["round_to"], 5)
        for good in (5, 10, 15):
            config.update({"round_to": good})
            self.assertEqual(config.load()["round_to"], good)
        for bad in (7, True, "15", 0, None):
            with open(config.SETTINGS_FILE, "w") as fh:
                json.dump({"round_to": bad, "person_id": "42"}, fh)
            s = config.load()
            self.assertEqual((s["round_to"], s["person_id"]), (5, "42"), bad)  # only the bad value falls back
        self.assertEqual(server.validate({"round_to": 15}), {"round_to": 15})
        self.assertEqual(server.validate({"round_to": "10"}), {"round_to": 10})
        for bad in (7, True, "five", None, 15.5):
            with self.assertRaises(ValueError):
                server.validate({"round_to": bad})
        config.update({"round_to": 10})
        with mock.patch.object(server.productive, "connection", lambda s: {"kind": None, "problem": "x"}):
            self.assertEqual(server.public_config(config.load())["roundTo"], 10)
            # a Preview that started without PULSAR_PREVIEW (PULSAR_INSTANCE=preview alone, as a test server may):
            # no branch to show, and Settings still load instead of answering 500
            with mock.patch.dict(os.environ, {"PULSAR_PREVIEW": "1"}), mock.patch.object(server, "PREVIEW", None):
                self.assertEqual(server.public_config(config.load())["preview"], {"writes": False})

    def test_it_sets_log_the_days_grid_and_shortest_entry(self):
        grid = lambda o: {k: o[k] for k in ("grid", "min_entry")}  # noqa: E731 - "sitting_gap" is Away after's
        self.assertEqual(grid(dayplan.opts_for({})), {"grid": 5, "min_entry": 10})       # today's, unchanged
        self.assertEqual(grid(dayplan.opts_for({"round_to": 10})), {"grid": 10, "min_entry": 10})
        self.assertEqual(grid(dayplan.opts_for({"round_to": 15})), {"grid": 15, "min_entry": 15})
        self.assertEqual(grid(dayplan.opts_for({"round_to": True})), {"grid": 5, "min_entry": 10})

    def test_a_quarter_hour_grid_on_a_day(self):
        ctx = history_for({"Fix the importer": "11", "Hero video": "22"})
        d = day(items=[work(543, 655)], rows=[row(543, 643), row(643, 655, titles=["Hero video"])])
        five = dayplan.plan(d, ctx, dayplan.opts_for({}))
        self.assertEqual([(e["s"], e["e"], e["taskId"]) for e in five["entries"]], [(545, 645, "11"), (645, 655, "22")])
        self.assertEqual(five["entries"], dayplan.plan(d, ctx)["entries"])  # the default is what plan() did before
        quarter = dayplan.plan(d, ctx, dayplan.opts_for({"round_to": 15}))
        # the 12-minute piece is shorter than a step: it joins the work next to it; every boundary on a quarter hour.
        # Task 11 was seen for 100 minutes and its billing is not known, so it may be billable: the cap cuts it to 90,
        # and the quarter hour after it, where both tasks of its project were in front (100 + 12 minutes seen), comes
        # back to it (_refill, 2026-11-10): no hole of a step, and never more than the project was seen
        self.assertEqual([(e["s"], e["e"], e["taskId"]) for e in quarter["entries"]], [(540, 645, "11")])
        self.assertEqual(quarter["uncovered"], [])
        self.assertTrue(all(e["s"] % 15 == 0 and e["e"] % 15 == 0 for e in quarter["entries"]))
        self.assertLessEqual(quarter["totals"]["proposed"], quarter["totals"]["cap"])


class CatchUpTest(unittest.TestCase):
    """Log the week (web/js/catchup.js) only moves between days: each day is written by Log the day's own confirm."""
    WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

    def test_it_never_writes_and_reads_only_the_report(self):
        with open(os.path.join(self.WEB, "js", "catchup.js"), encoding="utf-8") as fh:
            code = fh.read()
        for write in ("postJSON", "/api/log", "/api/entry", "writeRun", "fetch(", "method:"):
            self.assertNotIn(write, code)
        import re
        # reads only: the report, and the days marked done or "not work" (daymarks.py)
        self.assertEqual(set(re.findall(r"getJSON\(`(/api/[a-z/]+)", code)), {"/api/report", "/api/daymarks"})
        self.assertIn("openLogDay()", code)  # a day opens as Log the day, with its own confirm step and journal

    def test_the_page_loads_it_after_log_the_day_and_the_range_fits_the_report(self):
        with open(os.path.join(self.WEB, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        self.assertLess(html.index("/static/js/logday.js"), html.index("/static/js/catchup.js"))
        self.assertLess(html.index("/static/js/catchup.js"), html.index("/static/js/main.js"))
        # 14 workdays reach back at most 20 days (two weekends and a day), well inside /api/report's 93
        with mock.patch.object(server, "current_settings", lambda: {"timezone": "UTC", "personal_patterns": [],
                                                                     "activitywatch_url": "http://localhost:5600"}), \
                mock.patch.object(server, "productive_rows", lambda d0, d1, s: ([], None, "token", False)), \
                mock.patch.object(server.trackers, "both", lambda a, b, url, cls, away=None: {"aw": []}):
            got = server.api_report({"from": "2026-10-16", "to": "2026-11-05"})
        self.assertEqual(len(got["days"]), 21)
        self.assertTrue(all("notLogged" in d and "weekday" in d for d in got["days"]))


if __name__ == "__main__":
    unittest.main()
