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


if __name__ == "__main__":
    unittest.main()
