"""The usability fixes of the 2026-11-07 review: search ranking, the day's done state and time marked as not work,
short stretches, and what the page gets for them. Made-up data only.

    python3 -m unittest discover -s tests
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import askclaude, daymarks, dayplan, server  # noqa: E402

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

S = {"timezone": "UTC", "person_id": "1", "org_id": "2", "rules": [], "person_name": ""}


def task(tid, title, project="Internal"):
    return {"taskId": tid, "title": title, "project": project, "projectId": "9", "serviceId": "", "due": None,
            "status": "", "blocked": False}


class PageScriptsTest(unittest.TestCase):
    """The page's scripts share one global scope, so a name declared in two of them is one name: the later file's
    replaces the earlier's everywhere. 1.6.12 shipped askPause in ask.js (the Ask Claude button) and in pause.js (the
    pause dialog), and Ask Claude stuck on "Asking…" from the first click."""

    def test_no_two_scripts_declare_the_same_global(self):
        import glob
        import re
        top = re.compile(r"(?:async\s+)?function\s+(\w+)\s*\(|(?:const|let|var|class)\s+(\w+)")
        seen = {}
        for path in sorted(glob.glob(os.path.join(WEB, "js", "*.js"))):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            if re.search(r"^\((?:\(\) =>|function)", text, re.M):
                continue  # wrapped in a function of its own: nothing it declares is global
            for line in text.splitlines():
                m = top.match(line)  # a declaration at column 0 is the script's own
                if m:
                    name, here = m.group(1) or m.group(2), os.path.basename(path)
                    if name in seen:
                        self.fail(f"{name} is declared in {seen[name]} and {here}")
                    seen[name] = here
        self.assertIn("askPause", seen)
        self.assertIn("pauseQuestion", seen)


class SearchRankTest(unittest.TestCase):
    """A search for "Scrum" in a company where everyone has a Scrum task: yours first, closed ones last."""

    FOUND = [task("68", "[FIP-68] Scrum - Ann"), task("69", "[FIP-69] Scrum - Bo (closed)"),
             task("70", "Scrum - Cy"), task("72", "[FIP-72] Scrum - Me"), task("73", "Daily scrum notes")]

    def test_yours_first_then_assigned_then_others_closed_last(self):
        got = server.rank_search(self.FOUND, "scrum", [task("72", "[FIP-72] Scrum - Me")], [task("73", "Daily scrum notes")])
        self.assertEqual([t["taskId"] for t in got], ["72", "73", "68", "70", "69"])
        self.assertEqual(got[0]["yours"], "logged")
        self.assertEqual(got[1]["yours"], "assigned")
        self.assertNotIn("yours", got[2])

    def test_logged_ones_keep_the_order_of_the_last_logged(self):
        recent = [task("70", "Scrum - Cy"), task("72", "[FIP-72] Scrum - Me")]  # newest first, as api_suggest gives
        got = server.rank_search(self.FOUND, "scrum", recent, [])
        self.assertEqual([t["taskId"] for t in got][:2], ["70", "72"])

    def test_one_of_yours_left_off_productives_page_is_added(self):
        got = server.rank_search(self.FOUND[:2], "scrum", [task("72", "[FIP-72] Scrum - Me")], [task("80", "Other")])
        self.assertEqual([t["taskId"] for t in got], ["72", "68", "69"])  # "Other" does not hold the words

    def test_no_history_keeps_productives_order(self):
        got = server.rank_search(self.FOUND, "scrum", [], [])
        self.assertEqual([t["taskId"] for t in got], ["68", "70", "72", "73", "69"])

    def test_the_endpoint_ranks_and_survives_a_failed_history(self):
        with mock.patch.object(server, "current_settings", lambda: dict(S)), \
                mock.patch.object(server.productive, "search_tasks", lambda q, s: list(self.FOUND)), \
                mock.patch.object(server.logbook, "record", lambda *a, **k: None):
            with mock.patch.object(server, "api_suggest", lambda q: {"recent": [task("72", "x scrum")], "assigned": []}):
                got = server.api_search({"q": "scrum", "date": "2026-11-05"})
            self.assertEqual(got["tasks"][0]["taskId"], "72")

            def broken(q):
                raise RuntimeError("Productive timed out")
            with mock.patch.object(server, "api_suggest", broken):
                got = server.api_search({"q": "scrum", "date": "bad"})
            self.assertEqual(len(got["tasks"]), 5)
            self.assertEqual(server.api_search({"q": "s"}), {"tasks": []})


class KeptAnswersTest(unittest.TestCase):
    """What Claude wrote for a day is kept on disk beside Ask Claude's picks, so the same question costs nothing twice."""

    def setUp(self):
        self.dir = os.path.join(tempfile.mkdtemp(), "claude-picks")
        for p in (mock.patch.object(askclaude, "PICKS_DIR", self.dir),
                  mock.patch.object(askclaude.productive, "_claude_binary", lambda: "/bin/claude"),
                  mock.patch.object(askclaude.config, "load", lambda: {})):
            p.start()
            self.addCleanup(p.stop)
        askclaude._notes.clear()
        self.addCleanup(askclaude._notes.clear)

    def test_a_note_drafted_in_likely_is_found_for_the_same_work(self):
        key = lambda s, e, p: json.dumps([s, e, p, 3, 1, "Done", "haiku"])  # noqa: E731  as askclaude.note keeps it
        askclaude.stash("2026-11-05", "notes", key(600, 660, "site"), {"note": "Hero video fixed."})
        askclaude.stash("2026-11-05", "notes", key(700, 720, "site"), {"note": ""})            # nothing was drafted
        askclaude.stash("2026-11-05", "notes", key(600, 660, "tool"), {"note": "Tool note."})   # another repository
        self.assertEqual(askclaude.kept_note("2026-11-05", "site", 610, 670), "Hero video fixed.")
        self.assertEqual(askclaude.kept_note("2026-11-05", "site", 640, 760), "")   # under half of the stretch
        self.assertEqual(askclaude.kept_note("2026-11-05", "site", 700, 720), "")
        self.assertEqual(askclaude.kept_note("2026-11-06", "site", 610, 670), "")

    def test_kept_by_day_and_key_owner_only_and_old_days_go(self):
        askclaude.stash("2026-11-05", "notes", "a", {"note": "Search keeps every letter."})
        self.assertEqual(askclaude.stashed("2026-11-05", "notes", "a"), {"note": "Search keeps every letter."})
        self.assertIsNone(askclaude.stashed("2026-11-05", "notes", "b"))
        self.assertIsNone(askclaude.stashed("2026-11-06", "notes", "a"))
        self.assertIsNone(askclaude.stashed("not a day", "notes", "a"))
        self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dir, "2026-11-05.notes.json")).st_mode), 0o600)
        old = os.path.join(self.dir, "2020-01-01.review.json")
        with open(old, "w") as fh:
            fh.write("{}")
        askclaude.stash("2026-11-05", "notes", "c", {"note": ""})
        self.assertFalse(os.path.exists(old))  # older than KEEP_DAYS

    def test_a_note_is_drafted_once_for_the_same_work_even_after_a_restart(self):
        runs = []

        def run(cmd, **kw):
            runs.append(cmd)
            return mock.Mock(stdout=json.dumps({"structured_output": {"note": "Search keeps every letter."},
                                                "total_cost_usd": 0.001}), stderr="", returncode=0)
        row = {"s": 540, "e": 600, "project": "acme-site", "titles": [], "commits": ["Search: keep every letter"]}
        with mock.patch.object(askclaude, "steps", lambda t0, titles, s, e: [{"typed": "fix it", "reply": "Fixed."}]):
            first = askclaude.note("2026-11-05", row, 0, [], run=run)
            askclaude._notes.clear()  # Pulsar restarted
            again = askclaude.note("2026-11-05", row, 0, [], run=run)
            other = askclaude.note("2026-11-05", {**row, "e": 610}, 0, [], run=run)  # another stretch: asked
        self.assertEqual(first["note"], "Search keeps every letter.")
        self.assertEqual((again["note"], again.get("kept"), again["cost"]), (first["note"], True, 0))
        self.assertEqual(len(runs), 2)
        self.assertEqual(other["note"], first["note"])

    def test_the_days_review_is_reused_while_the_plan_is_the_same(self):
        entries = [{"id": "r0", "s": 540, "e": 600, "taskId": "7", "kind": "focused", "repo": "acme-site"}]
        plan = {"entries": entries}
        runs = []

        def gather(date, source, ignore, inputs=None):
            inputs.update(rows=[], t0=0)
            return plan

        def review(date, doc, entries, model=None, settings=None):
            runs.append(model)
            return {"suggestions": [{"kind": "task", "row": "r0", "taskId": "8"}], "dropped": [], "cost": 0.02}
        with mock.patch.object(server, "current_settings", lambda: {**S, "ask_claude": True}), \
                mock.patch.object(server.dayplan, "gather", gather), \
                mock.patch.object(server.dayreview, "digest", lambda d, i: ("digest", 0, 100)), \
                mock.patch.object(server.dayreview, "review", review), \
                mock.patch.object(server.claudeuses, "model", lambda use, s: "haiku"):
            self.assertEqual(server.api_dayplan_review({"date": "2026-11-05", "kept_only": True})["suggestions"], [])
            self.assertEqual(runs, [])  # opening the panel never runs Claude
            first = server.api_dayplan_review({"date": "2026-11-05"})
            again = server.api_dayplan_review({"date": "2026-11-05"})
            opened = server.api_dayplan_review({"date": "2026-11-05", "kept_only": True})
            self.assertEqual(len(runs), 1)
            self.assertEqual((again["suggestions"], again["kept"], again["cost"]), (first["suggestions"], True, 0))
            self.assertTrue(opened["kept"])
            server.api_dayplan_review({"date": "2026-11-05", "again": True})  # "Review again": a new run
            self.assertEqual(len(runs), 2)
            entries[0]["taskId"] = "9"  # the plan changed: asked again
            self.assertTrue(server.api_dayplan_review({"date": "2026-11-05", "kept_only": True}).get("none"))
            server.api_dayplan_review({"date": "2026-11-05"})
            self.assertEqual(len(runs), 3)

    def test_a_changed_plan_keeps_the_suggestions_of_the_rows_that_stayed_the_same(self):
        """2026-11-13: after a restart the review was gone as soon as anything in the day had changed."""
        plan = {"entries": [{"id": "r0", "s": 540, "e": 600, "taskId": "7", "kind": "focused", "repo": "acme-site"},
                            {"id": "r1", "s": 600, "e": 660, "taskId": "5", "kind": "focused", "repo": "house-tool"}]}
        runs = []

        def gather(date, source, ignore, inputs=None):
            inputs.update(rows=[], t0=0)
            return plan

        def review(date, doc, entries, model=None, settings=None):
            runs.append(model)
            return {"suggestions": [{"kind": "task", "row": "r0", "taskId": "8"}, {"kind": "untick", "row": "r1"},
                                    {"kind": "question", "row": "", "question": "Was the 4 PM call billable?"}],
                    "dropped": [], "cost": 0.02}
        with mock.patch.object(server, "current_settings", lambda: {**S, "ask_claude": True}), \
                mock.patch.object(server.dayplan, "gather", gather), \
                mock.patch.object(server.dayreview, "digest", lambda d, i: ("digest", 0, 100)), \
                mock.patch.object(server.dayreview, "review", review), \
                mock.patch.object(server.claudeuses, "model", lambda use, s: "haiku"):
            server.api_dayplan_review({"date": "2026-11-06"})
            # time went on: a row before the others (ids shift), and the second row grew
            plan["entries"] = [{"id": "r0", "s": 480, "e": 520, "taskId": "3", "kind": "outside"},
                               {"id": "r1", "s": 540, "e": 600, "taskId": "7", "kind": "focused", "repo": "acme-site"},
                               {"id": "r2", "s": 600, "e": 690, "taskId": "5", "kind": "focused", "repo": "house-tool"}]
            got = server.api_dayplan_review({"date": "2026-11-06", "kept_only": True})
            self.assertEqual(len(runs), 1)  # never a run on opening
            self.assertEqual([(s["kind"], s["row"]) for s in got["suggestions"]], [("task", "r1"), ("question", "")])
            self.assertEqual((got["kept"], got["changed"], got["cost"]), (True, 1, 0))
            self.assertNotIn("rowSigs", got)
            # every row with a suggestion gone, and none about the day: nothing kept
            plan["entries"] = [{"id": "r0", "s": 540, "e": 610, "taskId": "7", "kind": "focused", "repo": "acme-site"}]
            with mock.patch.object(server.dayreview, "review", lambda *a, **k: {"suggestions": [{"kind": "untick", "row": "r0"}],
                                                                                  "dropped": [], "cost": 0}):
                server.api_dayplan_review({"date": "2026-11-07"})
            plan["entries"] = [{"id": "r0", "s": 540, "e": 620, "taskId": "7", "kind": "focused", "repo": "acme-site"}]
            self.assertTrue(server.api_dayplan_review({"date": "2026-11-07", "kept_only": True}).get("none"))


class DayMarksTest(unittest.TestCase):
    """The day done and time marked "not work": on this Mac only, per day, merged spans, KEEP_DAYS days."""

    def setUp(self):
        p = mock.patch.object(daymarks, "PATH", os.path.join(tempfile.mkdtemp(), "day-marks.json"))
        p.start()
        self.addCleanup(p.stop)

    def test_spans_merge_and_cut(self):
        self.assertEqual(daymarks.merge([[60, 90], [85, 100], [100, 110], [200, 190], "x", [True, 3], [-5, 10]]),
                         [[0, 10], [60, 110]])
        self.assertEqual(daymarks.cut([[60, 110]], 70, 80), [[60, 70], [80, 110]])
        self.assertEqual(daymarks.cut([[60, 110]], 0, 2000), [])

    def test_an_entry_marked_not_an_example_teaches_nothing_until_taken_back(self):
        from pulsar import learn
        self.assertEqual(daymarks.change("2026-11-05", nolearn="900000001"), {"noLearn": ["900000001"]})
        daymarks.change("2026-11-05", nolearn="900000001")  # twice is once
        self.assertEqual(daymarks.no_learn(), {"900000001"})
        with self.assertRaises(ValueError):
            daymarks.change("2026-11-05", nolearn="1; rm")
        # the history every learner reads leaves it out
        rows = [{"id": "900000001", "date": "2026-11-05", "taskId": "1"}, {"id": "2", "date": "2026-11-05", "taskId": "1"}]
        with mock.patch.object(server, "productive_rows", lambda *a, **k: (rows, None, None, False)):
            server._rows_before.clear()
            got, _ = server.history_rows("2026-11-06", {"person_id": "p"}, days=30)
            self.assertEqual([r["id"] for r in got], ["2"])
            # Likely's rows: annotate gets the entries, and drops the marked one before it votes
            seen = []
            with mock.patch.object(learn, "votes", lambda days, entries, *a: (seen.append(entries), ({}, {}, {}))[1]), \
                    mock.patch.object(learn, "warm", lambda *a: None):
                learn.annotate({"entries": []}, "2026-11-06", {}, rows, [], None)
            self.assertEqual([r["id"] for r in seen[0]], ["2"])
            self.assertEqual(daymarks.change("2026-11-05", learn="900000001"), {})
            got, _ = server.history_rows("2026-11-06", {"person_id": "p"}, days=30)
            self.assertEqual(len(got), 2)
            server._rows_before.clear()

    def test_a_day_is_marked_done_and_time_not_work_and_both_can_be_taken_back(self):
        self.assertEqual(daymarks.get("2026-11-05"), {})
        self.assertEqual(daymarks.change("2026-11-05", done=True), {"done": True})
        self.assertEqual(daymarks.change("2026-11-05", add=[757, 764]), {"done": True, "notWork": [[757, 764]]})
        daymarks.change("2026-11-05", add=[764, 770])
        self.assertEqual(daymarks.get("2026-11-05")["notWork"], [[757, 770]])
        daymarks.change("2026-11-05", remove=[757, 764])
        self.assertEqual(daymarks.get("2026-11-05")["notWork"], [[764, 770]])
        self.assertEqual(daymarks.change("2026-11-05", done=False)["done"], False)  # past the target, "not done" after all
        self.assertNotIn("done", daymarks.change("2026-11-05", undone=True))
        daymarks.change("2026-11-04", done=True)
        self.assertEqual(sorted(daymarks.between("2026-11-01", "2026-11-04")), ["2026-11-04"])
        self.assertEqual(stat.S_IMODE(os.stat(daymarks.PATH).st_mode), 0o600)
        for bad in ({"date": "x"}, {"date": "2026-11-05", "done": "yes"}, {"date": "2026-11-05", "add": [5, 5]}):
            with self.assertRaises(ValueError):
                daymarks.change(bad["date"], done=bad.get("done"), add=bad.get("add"))

    def test_old_days_go(self):
        with open(daymarks.PATH, "w") as fh:
            json.dump({"2020-01-01": {"done": True}}, fh)
        daymarks.change("2026-11-05", done=True)
        self.assertEqual(daymarks.between("2000-01-01", "2100-01-01"), {"2026-11-05": {"done": True}})


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class LogTheDayKeepsWorkTest(unittest.TestCase):
    """web/js/ldkeep.js under node: what the person did in Log the day goes back on a fresh plan by the row's key."""

    def js(self, code):
        with open(os.path.join(WEB, "js", "ldkeep.js"), encoding="utf-8") as fh:
            src = fh.read()
        done = subprocess.run(["node", "-e", src + "\nconsole.log(JSON.stringify((() => {" + code + "})()));"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    PLAN = """
      const D = '2026-11-05';
      const plan = (extra) => [...extra, {s: 0, e: 150, minutes: 150, repo: 'qa', taskId: '1', title: 'QA', checked: false},
        {s: 185, e: 210, minutes: 25, repo: 'qa', taskId: '1', title: 'QA', checked: false},
        {s: 1035, e: 1075, minutes: 40, kind: 'meeting', meeting: 'Daily', taskId: '', title: '', checked: false},
        {s: 1090, e: 1200, minutes: 110, repo: 'pulsar', taskId: '5', title: 'Pulsar', checked: false}]
        .map((e, i) => ({...e, id: 'r' + i, key: ldRowKey(D, e), opened: false, edited: false, status: e.status || null}));
    """

    def test_edits_go_back_on_the_same_rows_when_the_list_shifts(self):
        got = self.js(self.PLAN + """
          const before = plan([]);
          Object.assign(before[0], {checked: true, opened: true, edited: true, note: 'Report rebuilt.', noteEdited: true});
          Object.assign(before[2], {taskId: '72', title: 'Scrum', serviceId: '3', checked: true, edited: true, opened: true});
          const kept = ldSnapshot(before, 'r2', [before[1].key]);
          const fresh = plan([{s: 0, e: 0, minutes: 0, repo: 'new', taskId: '9', title: 'New'}]);  // r0 is another row now
          const out = ldReapply(fresh, kept, []);
          return {rows: out.rows.map(r => [r.id, r.title, r.checked, r.note || '', r.taskId]), open: out.open, dropped: out.dropped,
                  kept: Object.keys(kept.rows).length};
        """)
        self.assertEqual(got["kept"], 2)  # only rows touched or opened
        self.assertEqual(got["rows"], [["r0", "New", None, "", "9"],
                                       ["r1", "QA", True, "Report rebuilt.", "1"],      # was r0
                                       ["r3", "Scrum", True, "", "72"],                  # was r2, the call
                                       ["r4", "Pulsar", False, "", "5"]])                # r2 (deleted) stays out
        self.assertEqual(got["open"], "r3")
        self.assertEqual(got["dropped"], [])

    def test_edits_go_when_the_row_is_gone_or_its_time_is_logged_now(self):
        got = self.js(self.PLAN + """
          const before = plan([]);
          for (const r of before) Object.assign(r, {checked: true, opened: true, edited: true});
          const kept = ldSnapshot(before, null, []);
          const fresh = plan([]).filter(r => r.repo !== 'pulsar');                 // the Pulsar row is gone
          fresh[1].status = 'logged';                                               // a run wrote this one
          const out = ldReapply(fresh, kept, [{s: 0, e: 150}]);                     // 00:00 is logged now
          return {rows: out.rows.map(r => [r.id, r.checked, r.status]), dropped: out.dropped.sort()};
        """)
        self.assertEqual(got["rows"], [["r0", False, None], ["r1", False, "logged"], ["r2", True, None]])
        self.assertEqual(len(got["dropped"]), 2)  # 00:00 (logged since) and the Pulsar row (gone)

    def test_a_row_claude_only_drafted_a_note_for_keeps_its_note_not_the_plans_fields(self):
        got = self.js(self.PLAN + """
          const before = plan([]);
          Object.assign(before[3], {noteTried: true, note: 'Settings section added.', noteFrom: 'session', checked: true,
                                    why: 'old why'});
          const kept = ldSnapshot(before, null, []);
          const fresh = plan([]); fresh[3].why = 'new why';
          const r = ldReapply(fresh, kept, []).rows[3];
          return [r.note, r.noteFrom, r.noteTried, r.checked, r.why];
        """)
        self.assertEqual(got, ["Settings section added.", "session", True, False, "new why"])

    def test_a_shared_row_goes_to_the_client_half_each_or_the_internal_work_at_its_real_time(self):
        got = self.js("""
          const r = {id: 'r1', key: 'k', s: 620, e: 660, minutes: 40, shared: true, choice: 'client', taskId: 'C1',
                     serviceId: 'sC', title: 'Client support', whole: [620, 660],
                     client: {taskId: 'C1', serviceId: 'sC', title: 'Client support'},
                     sharedWith: {taskId: 'T1', serviceId: 'sT', title: 'Build the tool', repo: 'house-tool'}};
          const half = ldChoice(r, 'half', 5), inner = ldChoice(r, 'internal', 5);
          const back = ldChoice(inner[0], 'client', 5), kept = ldWithHalves([half[0]], 5);
          const short = ldChoice({...r, whole: [620, 628], e: 628, minutes: 8}, 'half', 5);
          const fifteen = ldChoice({...r, whole: [620, 635], e: 635, minutes: 15}, 'half', 5);
          const odd = ldChoice({...r, whole: [620, 645], e: 645, minutes: 25}, 'half', 5);
          const show = x => [x.s, x.e, x.minutes, x.taskId, x.serviceId, x.choice, x.checked, x.halfOf || ''];
          return {half: half.map(show), inner: inner.map(show), back: back.map(show), kept: kept.map(show), short: short.map(show),
                  fifteen: fifteen.map(x => x.minutes), odd: odd.map(x => x.minutes)};
        """)
        self.assertEqual(got["half"], [[620, 640, 20, "C1", "sC", "half", True, ""], [640, 660, 20, "T1", "sT", "half", True, "r1"]])
        self.assertEqual(got["inner"], [[620, 660, 40, "T1", "sT", "internal", True, ""]])
        self.assertEqual(got["back"], [[620, 660, 40, "C1", "sC", "client", True, ""]])   # the client's fields come back
        self.assertEqual(got["kept"], got["half"])                                         # a kept Half each gets its half
        self.assertEqual(got["short"], [[620, 628, 8, "C1", "sC", "client", True, ""]])    # too short to halve on the grid
        # both halves an entry at least, and halved as dayplan.apply_choice halves (2026-11-12 audit)
        self.assertEqual(got["fifteen"], [15])
        self.assertEqual(got["odd"], [15, 10])
        shared = {"id": "r1", "s": 620, "e": 645, "minutes": 25, "shared": True, "taskId": "C1",
                  "sharedWith": {"taskId": "T1", "serviceId": "sT"}}
        self.assertEqual([x["minutes"] for x in dayplan.apply_choice(shared, "half")], got["odd"])
        self.assertEqual([x["minutes"] for x in dayplan.apply_choice({**shared, "e": 635, "minutes": 15}, "half")], [15])

    def test_the_key_is_the_work_not_the_place(self):
        got = self.js("""
          return [ldRowKey('2026-11-05', {s: 60, repo: 'qa', taskId: '1'}), ldRowKey('2026-11-05', {s: 60, meeting: 'Daily', kind: 'meeting'}),
                  ldRowKey('2026-11-05', {s: 60, kind: 'outside', taskId: '2'}),
                  sgItemKey({key: 'c1', state: 'accepted', source: 'claude', kind: 'task', at: {s: 60, taskId: '1'}, taskId: '8'}) ===
                  sgItemKey({key: 'k0', state: 'open', source: 'claude', kind: 'task', at: {s: 60, taskId: '1'}, taskId: '8'})];
        """)
        self.assertEqual(got, ["2026-11-05|60|qa|1", "2026-11-05|60|meeting:Daily|", "2026-11-05|60|outside|2", True])


if __name__ == "__main__":
    unittest.main()
