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

from pulsar import askclaude, config, server  # noqa: E402

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

S = {"timezone": "UTC", "person_id": "1", "org_id": "2", "rules": [], "person_name": ""}


def task(tid, title, project="Internal"):
    return {"taskId": tid, "title": title, "project": project, "projectId": "9", "serviceId": "", "due": None,
            "status": "", "blocked": False}


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
