"""The usability fixes of the 2026-11-07 review: search ranking, the day's done state and time marked as not work,
short stretches, and what the page gets for them. Made-up data only.

    python3 -m unittest discover -s tests
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import config, server  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
