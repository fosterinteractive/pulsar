"""A new task from the log dialog (pulsar/newtask.py, productive.create_task, server.api_task_*): the request that
reaches Productive, whole, through the REST token and through Claude Code's MCP connection, faked at the lowest level
(productive.https); where a task may go; the drafts, made here and by Haiku (faked); and every way a create is refused
before anything is sent.

    python3 -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import askclaude, config, newtask, productive, server  # noqa: E402

SETTINGS = {"timezone": "America/Toronto", "person_id": "1", "org_id": "2", "ask_claude": True}
WRITES_ALLOWED = {"PULSAR_READ_ONLY": "", "PULSAR_PREVIEW": ""}  # so a write reaches the fake wire, and only it
TASK = {"project_id": "30", "parent_id": "40", "task_list_id": "", "title": "Charts: horizontal bar option",
        "description": "Figure 4 shows horizontal bars.\n- a checkbox on chart paragraphs"}


class Wire:
    """Productive at the lowest level (productive.https): every request kept with its decoded body, and answered
    with `answer` (MCP text) or `rest` ((status, body)). Nothing here reaches the network."""

    def __init__(self, answer="task created (ID: 777).", rest=(201, b'{"data": {"id": "777", "type": "tasks"}}'),
                 is_error=False):
        self.calls, self.answer, self.rest, self.is_error = [], answer, rest, is_error

    def __call__(self, method, url, body=None, headers=None, timeout=60, write=False, head=None):
        if write and config.writes_blocked():  # as the real https: a blocked write never leaves
            raise productive.ProductiveError(config.writes_blocked())
        doc = json.loads(body) if body else None
        self.calls.append({"method": method, "url": url, "body": doc, "headers": headers or {}, "write": write})
        if url != productive.MCP_URL:
            return self.rest
        answer = {"jsonrpc": "2.0", "id": 1,
                  "result": {"content": [{"type": "text", "text": self.answer}], "isError": self.is_error}}
        return 200, ("event: message\ndata: " + json.dumps(answer)).encode()

    def writes(self) -> list:
        return [c for c in self.calls if c["write"]]

    def tools(self) -> list:
        return [(c["body"]["params"]["name"], c["body"]["params"]["arguments"]) for c in self.calls
                if c["url"] == productive.MCP_URL]


def connected(kind: str):
    return mock.patch.object(productive, "connection", lambda s: {"kind": kind, "token": "t"})


class CreateTaskBodyTest(unittest.TestCase):
    """productive.create_task: the whole request, and only Productive's success sentence counts as created."""

    def setUp(self):
        env = mock.patch.dict(os.environ, WRITES_ALLOWED)
        env.start()
        self.addCleanup(env.stop)

    def test_mcp_subtask_under_the_period_task(self):
        wire = Wire()
        with connected("claude-code"), mock.patch.object(productive, "https", wire):
            self.assertEqual(productive.create_task(TASK, SETTINGS), {"taskId": "777"})
        # task_list null under a parent (Productive: "task_list is required (unless task_list=null)"); never a service,
        # status or estimate (a PM sets the service); assigned to the person in Settings
        self.assertEqual(wire.tools(), [("create_resource", {"resource_type": "tasks", "items": [{
            "title": "Charts: horizontal bar option", "project": "30", "task_list": None, "assignee": "1",
            "parent_task": "40", "description": "Figure 4 shows horizontal bars.\n- a checkbox on chart paragraphs"}]})])
        self.assertEqual([c["write"] for c in wire.calls], [True])

    def test_mcp_in_a_task_list_and_directly_in_the_project(self):
        for task_list, want in (("50", "50"), ("", None)):
            wire = Wire()
            with connected("claude-code"), mock.patch.object(productive, "https", wire):
                productive.create_task(dict(TASK, parent_id="", task_list_id=task_list, description=""), SETTINGS)
            self.assertEqual(wire.tools()[0][1]["items"], [{"title": "Charts: horizontal bar option", "project": "30",
                                                             "task_list": want, "assignee": "1"}])

    def test_rest_bodies(self):
        wire = Wire()
        with connected("token"), mock.patch.object(productive, "https", wire):
            self.assertEqual(productive.create_task(TASK, SETTINGS), {"taskId": "777"})
        [w] = wire.writes()
        self.assertEqual((w["method"], w["url"]), ("POST", productive.API + "/tasks"))
        self.assertEqual(w["body"], {"data": {
            "type": "tasks",
            "attributes": {"title": "Charts: horizontal bar option",
                           "description": "<p>Figure 4 shows horizontal bars.</p><ul><li>a checkbox on chart paragraphs</li></ul>"},
            "relationships": {"project": {"data": {"type": "projects", "id": "30"}},
                              "assignee": {"data": {"type": "people", "id": "1"}},
                              "parent_task": {"data": {"type": "tasks", "id": "40"}}}}})
        self.assertEqual((w["headers"]["X-Auth-Token"], w["headers"]["X-Organization-Id"]), ("t", "2"))
        wire = Wire()
        with connected("token"), mock.patch.object(productive, "https", wire):
            productive.create_task(dict(TASK, parent_id="", task_list_id="50", description="<b> & co"), SETTINGS)
        body = wire.writes()[0]["body"]["data"]
        self.assertEqual(body["relationships"]["task_list"], {"data": {"type": "task_lists", "id": "50"}})
        self.assertNotIn("parent_task", body["relationships"])
        self.assertEqual(body["attributes"]["description"], "<p>&lt;b&gt; &amp; co</p>")  # escaped, never markup

    def test_an_estimate_the_size_of_a_project_is_not_offered(self):
        """A project whose recent tasks are phases (10 h to 100 h) offers no estimate for one issue's task: 60 h for a
        small fix would mislead a PM. Issue-sized ones still give their median."""
        big = [(600, False, "Phase one"), (3600, False, "Phase two"), (6000, False, "Phase three"), (2400, False, "Phase four")]
        got = newtask._estimate("list", {}, big)
        self.assertIsNone(got["minutes"])
        self.assertIn("much bigger", got["why"])
        small = [(60, False, "Menu fix"), (120, False, "Footer"), (90, False, "Search")]
        self.assertEqual(newtask._estimate("list", {}, small)["minutes"], 90)

    def test_status_and_estimate_both_ways_and_never_a_service(self):
        wire = Wire()
        with connected("claude-code"), mock.patch.object(productive, "https", wire):
            productive.create_task(dict(TASK, status_id="301", estimate=90), SETTINGS)
        [(tool, args)] = wire.tools()
        self.assertEqual((args["items"][0]["workflow_status"], args["items"][0]["initial_estimate"]), ("301", "90 min"))
        wire = Wire()
        with connected("token"), mock.patch.object(productive, "https", wire):
            productive.create_task(dict(TASK, status_id="301", estimate=90), SETTINGS)
        body = wire.writes()[0]["body"]["data"]
        self.assertEqual(body["attributes"]["initial_estimate"], 90)  # minutes, as REST keeps it
        self.assertEqual(body["relationships"]["workflow_status"], {"data": {"type": "workflow_statuses", "id": "301"}})
        for kind in ("claude-code", "token"):  # none given: neither is sent, Productive puts it in the first status
            wire = Wire()
            with connected(kind), mock.patch.object(productive, "https", wire):
                productive.create_task(dict(TASK, status_id="", estimate=None, service_id="77"), SETTINGS)
            sent = json.dumps(wire.writes()[0]["body"])
            for word in ("workflow_status", "initial_estimate", "service", "77"):
                self.assertNotIn(word, sent, (kind, word))
        for bad in ({"status_id": "1x"}, {"status_id": "١٢"}, {"estimate": 0}, {"estimate": productive.ESTIMATE_MAX + 1},
                    {"estimate": "90"}, {"estimate": 1.5}, {"estimate": True}):
            wire = Wire()
            with connected("claude-code"), mock.patch.object(productive, "https", wire):
                with self.assertRaises(productive.ProductiveError) as caught:
                    productive.create_task(dict(TASK, **bad), SETTINGS)
            self.assertNotIsInstance(caught.exception, productive.ProductiveUnclear)
            self.assertEqual(wire.calls, [], bad)

    def test_both_success_sentences_are_read(self):
        for text in ("task created (ID: 21297146).", "Created 1 task (ID: 21297146)."):
            with connected("claude-code"), mock.patch.object(productive, "https", Wire(text)):
                self.assertEqual(productive.create_task(TASK, SETTINGS), {"taskId": "21297146"}, text)

    def test_refusals_in_plain_words_and_never_unclear(self):
        cases = [("Failed to create task. task_list is required (unless task_list=null)", False, "needs a task list"),
                 ('Failed to create task. API request failed because of error: {"status": 422, "detail": '
                  '"invalid level of subtasks"}', True, "cannot have subtasks"),
                 ("Item 1: Invalid parameters: project: Invalid input", False, "Invalid parameters"),
                 ("Failed to create task. 403 Forbidden", False, "your role may not add tasks")]
        for text, is_error, said in cases:
            with connected("claude-code"), mock.patch.object(productive, "https", Wire(text, is_error=is_error)):
                with self.assertRaises(productive.ProductiveError) as caught:
                    productive.create_task(TASK, SETTINGS)
            self.assertNotIsInstance(caught.exception, productive.ProductiveUnclear, text)
            self.assertIn(said, str(caught.exception))
            self.assertTrue(str(caught.exception).startswith("Productive did not create the task"), text)
        with connected("token"), mock.patch.object(productive, "https", Wire(rest=(422, b'{"errors": [{"detail": "x"}]}'))):
            with self.assertRaises(productive.ProductiveError) as caught:
                productive.create_task(TASK, SETTINGS)
        self.assertNotIsInstance(caught.exception, productive.ProductiveUnclear)

    def test_a_refusal_keeps_productives_own_words(self):
        """The plain sentence first, then what Productive said: a role refusal by its status only, never by any English
        phrase that merely says "not allowed"."""
        role = "your role may not add tasks"
        for text, plain in (("HTTP 403 This API token is read-only", True), ("Failed to create task. 403 Forbidden", True),
                            ("Failed to create task. Assignee is not allowed: person is not a member", False),
                            ("Failed to create task. subtasks are not allowed under a closed task", False),
                            ("Failed to create task. You do not have permission to set this field", False)):
            said = productive.task_refusal(text)
            self.assertEqual(role in said, plain, said)
            self.assertIn(text.replace("Failed to create task. ", ""), said)  # the original, on the page and in Logs

    def test_an_answer_that_is_not_the_success_sentence_may_have_created_it(self):
        for text, is_error in (("Something about ID: 55", False), ("", False),
                               ("Failed to create task. fetch failed", False),  # behind the MCP: it may have run
                               ('Failed to create task. API request failed because of error: {"status": 502}', True),
                               ("The request timed out", True)):
            with connected("claude-code"), mock.patch.object(productive, "https", Wire(text, is_error=is_error)):
                with self.assertRaises(productive.ProductiveUnclear) as said:
                    productive.create_task(TASK, SETTINGS)
            self.assertIn("Search for it", str(said.exception))
        for rest in ((502, b""), (201, b"{}")):
            with connected("token"), mock.patch.object(productive, "https", Wire(rest=rest)):
                with self.assertRaises(productive.ProductiveUnclear):
                    productive.create_task(TASK, SETTINGS)

    def test_nothing_is_sent_when_writes_are_blocked_or_ids_are_wrong(self):
        for env in ({"PULSAR_READ_ONLY": "1"}, {"PULSAR_READ_ONLY": "preview"}):
            for kind in ("claude-code", "token"):
                wire = Wire()
                with mock.patch.dict(os.environ, env), connected(kind), mock.patch.object(productive, "https", wire):
                    with self.assertRaises(productive.ProductiveError):
                        productive.create_task(TASK, SETTINGS)
                self.assertEqual(wire.calls, [], (env, kind))
        for bad in ({"project_id": ""}, {"project_id": "3x"}, {"parent_id": "4 0"}, {"task_list_id": "../5"}):
            wire = Wire()
            with connected("claude-code"), mock.patch.object(productive, "https", wire):
                with self.assertRaises(productive.ProductiveError):
                    productive.create_task(dict(TASK, **bad), SETTINGS)
            self.assertEqual(wire.calls, [], bad)
        wire = Wire()
        with connected("claude-code"), mock.patch.object(productive, "https", wire):
            with self.assertRaises(productive.ProductiveError):
                productive.create_task(TASK, dict(SETTINGS, person_id=""))
        self.assertEqual(wire.calls, [])

    def test_the_real_https_refuses_a_task_write_when_read_only(self):
        with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "1"}), mock.patch.object(config, "tests_offline", lambda: False):
            with self.assertRaises(productive.ProductiveError) as caught:
                productive.https("POST", productive.API + "/tasks", b"{}", {}, write=True)
        self.assertIn("read-only", str(caught.exception))


class ReadsTest(unittest.TestCase):
    """The projects and the places in one: read only, and what Pulsar asks for exactly."""

    def mcp(self, *answers):
        texts = iter(answers)

        def fake(tool, args, token, session=None):
            self.asked.append((tool, args))
            return next(texts)
        self.asked = []
        return mock.patch.object(productive, "mcp_text", fake)

    def test_places_through_the_mcp(self):
        tasks = ('Loaded 2.\n```json\n{"items": {"columns": ["type", "id", "title", "task_list", "assignee", "created_at"], '
                 '"rows": [["tasks", "41", "ACME - Contingency Hours Nov 2026", null, "people:9", "2026-11-02T10:00:00"],'
                 '["tasks", "42", "Hide affiliation", "task_lists:5", "people:1", "2026-10-30T10:00:00"]]}}\n```')
        lists = ('```json\n{"items": {"columns": ["type", "id", "name"], "rows": [["task_lists", "5", "To Do"]]}}\n```')
        with connected("claude-code"), self.mcp(tasks, lists):
            got = productive.project_places("30", SETTINGS)
        self.assertEqual(got, {"tasks": [
            {"taskId": "41", "title": "ACME - Contingency Hours Nov 2026", "list": "", "assignee": "9", "created": "2026-11-02T10:00:00"},
            {"taskId": "42", "title": "Hide affiliation", "list": "5", "assignee": "1", "created": "2026-10-30T10:00:00"}],
            "lists": [{"id": "5", "name": "To Do"}]})
        self.assertEqual([t for t, _ in self.asked], ["query_resources", "query_resources"])
        conds = self.asked[0][1]["filters"]["conditions"]
        self.assertIn({"field": "task_type", "operator": "eq", "value": "1"}, conds)  # top-level only: one level of subtasks
        self.assertIn({"field": "workflow_status_category", "operator": "not_eq", "value": "3"}, conds)
        with connected("claude-code"), self.assertRaises(productive.ProductiveError):
            productive.project_places("3 0", SETTINGS)

    def test_statuses_through_the_mcp_in_the_workflows_order(self):
        project = '```json\n{"item": {"id": "30", "type": "projects", "fields": {"workflow": {"type": "workflows", "id": "63"}}}}\n```'
        statuses = ('```json\n{"items": {"columns": ["type", "id", "name", "position", "workflow_status_category"], "rows": ['
                    '["workflow_statuses", "3", "Closed", 1, "Closed"], ["workflow_statuses", "2", "To Do", 2, "Not started"],'
                    '["workflow_statuses", "4", "In Progress", 4, "Started"], ["workflow_statuses", "1", "Backlog", 1, "Not started"]]}}\n```')
        with connected("claude-code"), self.mcp(project, statuses):
            got = productive.workflow_statuses("30", SETTINGS)
        self.assertEqual([(x["name"], x["category"]) for x in got],
                         [("Backlog", "1"), ("To Do", "1"), ("In Progress", "2"), ("Closed", "3")])
        self.assertEqual([t for t, _ in self.asked], ["load_resource", "query_resources"])
        self.assertFalse({t for t, _ in self.asked} & productive.WRITE_TOOLS)
        self.assertEqual(self.asked[1][1]["filters"]["conditions"], [{"field": "workflow", "operator": "eq", "value": "63"}])
        with connected("claude-code"), self.mcp('```json\n{"item": {"id": "30", "fields": {}}}\n```'):
            with self.assertRaises(productive.ProductiveError):
                productive.workflow_statuses("30", SETTINGS)

    def test_statuses_and_services_through_the_token(self):
        asked = []

        def get(path, query, token, org):
            asked.append((path, query))
            if path == "/projects/30":
                return 200, {"data": {"id": "30", "relationships": {"workflow": {"data": {"type": "workflows", "id": "63"}}}}}
            if path == "/workflow_statuses":
                return 200, {"data": [{"id": "2", "attributes": {"name": "To Do", "category_id": 1, "position": 2}},
                                      {"id": "1", "attributes": {"name": "Backlog", "category_id": 1, "position": 1}}]}
            return 200, {"data": [{"id": "8", "attributes": {"name": "Service Request / Contingency", "billing_type": 2},
                                   "relationships": {"deal": {"data": {"type": "deals", "id": "5"}}}}],
                         "included": [{"type": "deals", "id": "5", "attributes": {"name": "ACME · 2026-11"}}]}
        with connected("token"), mock.patch.object(productive, "rest_get", get):
            self.assertEqual([x["name"] for x in productive.workflow_statuses("30", SETTINGS)], ["Backlog", "To Do"])
            services = productive.project_services("30", "2026-11-07", SETTINGS, productive.Names(os.path.join(tempfile.mkdtemp(), "n.json")))
        self.assertEqual([p for p, _ in asked], ["/projects/30", "/workflow_statuses", "/services"])
        self.assertEqual(asked[1][1]["filter[workflow_id]"], "63")
        self.assertEqual((asked[2][1]["filter[project_id]"], asked[2][1]["filter[bookable_date]"]), ("30", "2026-11-07"))
        self.assertEqual((services[0]["id"], services[0]["budget"]), ("8", "ACME · 2026-11"))

    def test_active_projects_through_the_mcp(self):
        page = ('```json\n{"items": {"columns": ["type", "id", "name"], "rows": [["projects", "2", "beta"], '
                '["projects", "1", "Acme-OS Ongoing Support"], ["projects", "x", "bad"]]}}\n```')
        with connected("claude-code"), self.mcp(page):
            self.assertEqual(productive.active_projects(SETTINGS),
                             [{"id": "1", "name": "Acme-OS Ongoing Support"}, {"id": "2", "name": "beta"}])
        self.assertEqual(self.asked[0][1]["filters"]["conditions"], [{"field": "status", "operator": "eq", "value": "1"}])


class PlacesTest(unittest.TestCase):
    """Where a new task goes first: the period's contingency task (FI: a subtask per issue), else the list of your
    newest task there, else the only list, else directly in the project; with several lists, the page asks."""

    T = [{"taskId": "40", "title": "ACME - Contingency Hours Oct 2026", "list": "", "assignee": "", "created": "2026-10-01"},
         {"taskId": "41", "title": "ACME - Service Request & Contingency - Nov/Dec 2026", "list": "", "assignee": "",
          "created": "2026-11-02"},
         {"taskId": "42", "title": "ACME - Core Site Updates - Nov 2026", "list": "", "assignee": "1", "created": "2026-11-02"}]

    def test_the_period_task_named_for_the_month(self):
        got = newtask.places({"tasks": self.T, "lists": []}, "2026-10-20", "1")
        self.assertEqual(got["pick"], "parent:40")
        self.assertEqual([p["taskId"] for p in got["parents"]], ["41", "40"])  # newest first; core updates is not one
        self.assertEqual(newtask.places({"tasks": self.T, "lists": []}, "2026-11-07", "1")["pick"], "parent:41")
        self.assertEqual(newtask.places({"tasks": self.T, "lists": []}, "2027-01-07", "1")["pick"], "parent:41")  # newest

    def test_lists(self):
        lists = [{"id": "5", "name": "Backlog"}, {"id": "6", "name": "To Do"}]
        mine = [{"taskId": "50", "title": "Skills", "list": "6", "assignee": "1", "created": "2026-10-21"},
                {"taskId": "51", "title": "Other", "list": "5", "assignee": "9", "created": "2026-11-01"}]
        self.assertEqual(newtask.places({"tasks": mine, "lists": lists}, "2026-11-07", "1")["pick"], "list:6")
        self.assertEqual(newtask.places({"tasks": mine[1:], "lists": lists}, "2026-11-07", "1")["pick"], "")  # ask
        self.assertEqual(newtask.places({"tasks": [], "lists": lists[:1]}, "2026-11-07", "1")["pick"], "list:5")
        self.assertEqual(newtask.places({"tasks": [], "lists": []}, "2026-11-07", "1")["pick"], "none")
        archived = [dict(mine[0], list="99")]  # your newest task's list is no longer active
        self.assertEqual(newtask.places({"tasks": archived, "lists": lists}, "2026-11-07", "1")["pick"], "")


class ProposeTest(unittest.TestCase):
    """The status, estimate and service the form offers for a new task (newtask.propose), each with why."""

    STATUSES = [{"id": "1", "name": "Backlog", "category": "1", "position": 1},
                {"id": "2", "name": "To Do", "category": "1", "position": 2},
                {"id": "4", "name": "In Progress", "category": "2", "position": 4},
                {"id": "9", "name": "Closed", "category": "3", "position": 1}]
    SERVICES = [{"id": "70", "name": "Maintenance Updates", "budget": "ACME · 2026-11 · Maintenance"},
                {"id": "71", "name": "Service Request Overage", "budget": "ACME · 2026-11 · Contingency"},
                {"id": "72", "name": "Service Request / Contingency ", "budget": "ACME · 2026-10 · Contingency"},
                {"id": "73", "name": "Service Request / Contingency", "budget": "ACME · 2026-11 · Contingency"}]
    PERIOD = {"taskId": "40", "title": "ACME - Contingency Hours Nov 2026", "estimate": None, "serviceId": ""}
    SIZES = [[60, 1, "Hero image"], [180, 1, "Search"], [70, 1, "Menu"], [540, 0, "ACME - Core Site Updates - Nov 2026"],
             [240, 0, "Phase 2: deployment"], [120, 0, "Site search"]]

    def test_a_subtask_under_the_period_task(self):
        got = newtask.propose(self.STATUSES, "parent", self.PERIOD, self.SERVICES, self.SIZES, "2026-11-07")
        self.assertEqual([x["name"] for x in got["statuses"]], ["Backlog", "To Do", "In Progress"])  # never closed
        self.assertEqual(got["status"]["id"], "1")
        # the parent has no estimate: the median of the project's recent subtasks that have one
        self.assertEqual(got["estimate"]["minutes"], 70)
        self.assertIn("“ACME - Contingency Hours Nov 2026” has none", got["estimate"]["why"])
        self.assertIn("median of the 3 recent subtasks", got["estimate"]["why"])
        # the contingency line of the month's budget, never the overage line
        self.assertEqual(got["service"]["id"], "73")
        self.assertIn("contingency line", got["service"]["why"])
        self.assertEqual(newtask.propose(self.STATUSES, "parent", self.PERIOD, self.SERVICES[:2], self.SIZES,
                                         "2026-11-07")["service"]["id"], "")  # only the overage: nothing proposed

    def test_the_parents_own_service_and_estimate(self):
        parent = dict(self.PERIOD, estimate=240, serviceId="72")
        got = newtask.propose(self.STATUSES, "parent", parent, self.SERVICES, self.SIZES, "2026-11-07")
        self.assertEqual((got["service"]["id"], got["service"]["why"]), ("72", "The parent task's service."))
        self.assertEqual((got["estimate"]["minutes"], got["estimate"]["parent"]), (None, 240))  # may stay empty
        self.assertIn("carries 4 h", got["estimate"]["why"])
        gone = dict(parent, serviceId="99")  # its service takes no time on that date: the contingency line
        self.assertEqual(newtask.propose(self.STATUSES, "parent", gone, self.SERVICES, [], "2026-11-07")["service"]["id"], "73")
        other = dict(parent, title="ACME - Drupal 11 Upgrade", serviceId="")  # not a period's task: no guess
        self.assertEqual(newtask.propose(self.STATUSES, "parent", other, self.SERVICES, [], "2026-11-07")["service"]["id"], "")

    def test_a_task_in_a_list_or_the_project(self):
        for kind in ("list", "none"):
            got = newtask.propose(self.STATUSES, kind, None, self.SERVICES[:1], self.SIZES, "2026-11-07")
            self.assertEqual(got["status"]["id"], "2")  # To Do
            self.assertEqual(got["estimate"]["minutes"], 180)  # 240 and 120: the core-updates container is left out
            self.assertIn("median of the 2 recent tasks", got["estimate"]["why"])
            self.assertEqual(got["service"]["id"], "70")  # the only service
        no_todo = [x for x in self.STATUSES if x["name"] != "To Do"]
        self.assertEqual(newtask.propose(no_todo, "list", None, [], [], "2026-11-07")["status"]["id"], "1")

    def test_too_little_to_go_by_or_nothing_read(self):
        got = newtask.propose(self.STATUSES, "parent", self.PERIOD, self.SERVICES, self.SIZES[:1], "2026-11-07")
        self.assertIsNone(got["estimate"]["minutes"])  # one subtask with an estimate is not enough
        self.assertIn("Too few", got["estimate"]["why"])
        got = newtask.propose(None, "parent", {"title": "ACME - Contingency Hours Nov 2026", "unread": True}, None, None,
                              "2026-11-07")
        self.assertEqual((got["statuses"], got["status"]["id"], got["service"]["id"], got["estimate"]["minutes"]),
                         ([], "", "", None))
        self.assertIn("could not be read", got["status"]["why"])
        self.assertIn("could not be read", got["estimate"]["why"])
        self.assertIn("could not be read", got["service"]["why"])
        self.assertEqual(newtask.dur(90), "1 h 30 min")


class GuessProjectTest(unittest.TestCase):
    P = [{"id": "1", "name": "Acme-OS Ongoing Support", "lately": 300},
         {"id": "2", "name": "INI-OS Initech Ongoing Support", "lately": 50},
         {"id": "3", "name": "GX-OS Globex Ongoing Support", "lately": 0},
         {"id": "4", "name": "FI-DAI – Drupal AI Initiative", "lately": 0},
         {"id": "5", "name": "UMB-OS Umbrella Ongoing Support", "lately": 0}]

    def test_hint_then_repository(self):
        self.assertEqual(newtask.guess_project(self.P, "ini-os initech ongoing support", "acme"), "2")
        for repo, want in (("acme-web", "1"), ("initechweb", "2"), ("globex-r", "3"), ("umb-site", "5"),
                           ("fi-drupal-qa", ""), ("pulsar", ""), ("", ""), ("ab", "")):
            self.assertEqual(newtask.guess_project(self.P, "", repo), want, repo)


class DraftTest(unittest.TestCase):
    ROWS = [{"s": 600, "e": 660, "project": "acme", "titles": ["IMPORTANT! acme session"],
             "commits": ["Fix ordered list numbering in Firefox", "Lists: restart numbering per list"]}]

    def test_local_from_commits_never_the_session_title(self):
        self.assertEqual(newtask.local_draft(self.ROWS), {
            "title": "Fix ordered list numbering in Firefox",
            "description": "- Fix ordered list numbering in Firefox\n- Lists: restart numbering per list", "from": "commits"})
        self.assertEqual(newtask.local_draft([dict(self.ROWS[0], commits=[])]),
                         {"title": "", "description": "", "from": "none"})
        self.assertEqual(newtask.local_draft([], "Weekly sync with the PM")["title"], "Weekly sync with the PM")
        self.assertEqual(newtask.local_draft(self.ROWS, "Weekly sync")["from"], "meeting")  # the page says it was one

    def test_guards(self):
        self.assertEqual(newtask.guard_title("  important! fix the menu.  ")[0], "Fix the menu")
        for bad in ("Edit web/themes/x/menu.twig", "See https://example.com", "Claude Code plugin", "Fix it. Then more."):
            self.assertEqual(newtask.guard_title(bad)[0], "", bad)
        self.assertEqual(newtask.guard_description("One.\n- two\n\n- three")[0], "One.\n- two\n- three")
        self.assertEqual(newtask.guard_description("Fixed it.\n- in settings.php")[0], "")
        long = "\n".join(f"- line {i} " + "x" * 80 for i in range(20))
        kept, bad = newtask.guard_description(long)
        self.assertIsNone(bad)
        self.assertLessEqual(len(kept), newtask.DESC_MAX)
        self.assertTrue(all(x.startswith("- line ") and x.endswith("x") for x in kept.split("\n")))  # whole lines only
        one = "Checked the reported problem on the live site. " * 20 + "See web/x/y.php."  # one long line: sentences
        kept, bad = newtask.guard_description(one)
        self.assertIsNone(bad)
        self.assertTrue(kept.endswith("live site.") and len(kept) <= newtask.DESC_MAX)
        # a remark on the time is left out, the rest stays; a title with one is refused
        self.assertEqual(newtask.guard_description("Reviewed the document. The review time is logged as billable work."
                                                   "\n- Confirmed the findings and logged the time for the work.")[0],
                         "Reviewed the document.")
        self.assertEqual(newtask.guard_description("Fixed it\u200b.")[1], "a character nobody sees")
        self.assertEqual(newtask.guard_title("Audit hours billed to Acme"), ("", "a remark on the time"))

    def test_examples_skip_containers_and_old_keys(self):
        history = [{"task": "ACME - Contingency Hours Nov 2026", "project": "P", "date": "2026-11-03"},
                   {"task": "[ACME-12] Search fixes", "project": "P", "date": "2026-11-02"},
                   {"task": "Hero images", "project": "Q", "date": "2026-11-04"},
                   {"task": "Scrum - Nick", "project": "Q", "date": "2026-11-01"}]
        self.assertEqual(newtask.examples(history, "P"), ["Search fixes", "Hero images"])

    def test_claude_runs_isolated_and_its_answer_is_checked(self):
        runs = []

        def run(argv, **kw):
            runs.append((argv, kw))
            out = {"structured_output": {"title": "Lists: numbering in Firefox", "description": "See web/x.twig"},
                   "total_cost_usd": 0.002}
            return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")
        steps = [{"typed": "fix the lists", "reply": "Numbering restarts per list now."}]
        newtask._cache.clear()
        session = {"CLAUDECODE": "1", "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/x.sock", "CLAUDE_CODE_MESSAGING_TOKEN": "t",
                   "ANTHROPIC_BASE_URL": "http://127.0.0.1:9", "NODE_OPTIONS": "--require /tmp/x.js", "LANG": "en_CA.UTF-8"}
        with mock.patch.object(productive, "_claude_binary", lambda: "/bin/claude-fake"), \
                mock.patch.object(askclaude, "steps", lambda *a, **k: steps), mock.patch.dict(os.environ, session):
            got = newtask.claude_draft("2026-10-15", self.ROWS, 0, [
                {"task": "Hero images", "project": "P", "date": "2026-10-10"},
                {"task": "Menu fixes from August", "project": "P", "date": "2026-08-20"}], "P", run=run)
        self.assertEqual((got["title"], got["description"]), ("Lists: numbering in Firefox", ""))
        self.assertIn("description had a file path", got["why"])
        [(argv, kw)] = runs
        self.assertEqual(argv[:3], ["/bin/claude-fake", "-p", "--model"])
        for flag in (["--model", "haiku"], ["--tools", ""], ["--permission-mode", "dontAsk"], ["--setting-sources", ""],
                     ["--max-budget-usd", "0.1"]):
            i = argv.index(flag[0])
            self.assertEqual(argv[i:i + 2], flag)
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--no-session-persistence", argv)
        self.assertNotIn("--mcp-config", argv)  # no MCP server at all
        self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1]), newtask.SCHEMA)
        self.assertEqual(kw["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
        self.assertEqual(kw["env"]["LANG"], "en_CA.UTF-8")
        for k in session.keys() - {"LANG"}:  # the calling session's variables never reach this run
            self.assertFalse(k in kw["env"], k)  # the name only: a failure never prints the environment
        self.assertTrue(set(kw["env"]) <= set(askclaude.ENV_KEEP) | {"ENABLE_CLAUDEAI_MCP_SERVERS",
                                                                     "CLAUDE_CODE_DISABLE_AUTO_MEMORY"})
        self.assertEqual(kw["cwd"], config.CACHE_DIR)
        for said in ("Numbering restarts per list now.", "Lists: restart numbering per list", "Hero images", "from 10:00 to 11:00"):
            self.assertIn(said, kw["input"])
        self.assertNotIn("IMPORTANT!", kw["input"])
        self.assertNotIn("Menu fixes from August", kw["input"])  # only the person's tasks of the last 30 days

    def test_a_draft_naming_another_client_or_the_person_is_left_out(self):
        """As for a note (askclaude.foreign_name): a client may read the task (review 4, A5)."""
        def run_with(title, description):
            def run(argv, **kw):
                out = {"structured_output": {"title": title, "description": description}}
                return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")
            return run
        history = [{"task": "Hero images", "project": "Acme Support", "date": "2026-10-10"},
                   {"task": "Login fixes", "project": "Globex Build", "date": "2026-10-11"}]
        steps = [{"typed": "fix the lists", "reply": "Numbering restarts per list now."}]
        with mock.patch.object(productive, "_claude_binary", lambda: "/bin/claude-fake"), \
                mock.patch.object(askclaude, "steps", lambda *a, **k: steps), \
                mock.patch.object(askclaude, "_person", lambda: "Nick Example"):
            for title, desc, want in (("Lists: numbering for Globex", "Numbering fixed.", ("", "Numbering fixed.")),
                                      ("Lists: numbering in Firefox", "Same fix as on Globex.", ("Lists: numbering in Firefox", "")),
                                      ("Lists: numbering in Firefox", "Reviewed with Nick.", ("Lists: numbering in Firefox", "")),
                                      ("Acme lists: numbering", "Numbering fixed.", ("Acme lists: numbering", "Numbering fixed."))):
                newtask._cache.clear()
                got = newtask.claude_draft("2026-10-15", self.ROWS, 0, history, "Acme Support", run=run_with(title, desc))
                self.assertEqual((got["title"], got["description"]), want, (title, desc))
                if want != (title, desc):
                    self.assertIn("a name from another project", got["why"])

    def test_nothing_to_go_on_runs_nothing(self):
        def run(*a, **k):
            raise AssertionError("Claude was run")
        newtask._cache.clear()
        with mock.patch.object(askclaude, "steps", lambda *a, **k: []):
            got = newtask.claude_draft("2026-10-15", [dict(self.ROWS[0], commits=[])], 0, [], run=run)
        self.assertEqual((got["title"], got["description"]), ("", ""))


class ServerTest(unittest.TestCase):
    """POST /api/task/create and /api/task/draft, GET /api/task/places and /api/task/projects."""

    def setUp(self):
        for p in (mock.patch.dict(os.environ, WRITES_ALLOWED), mock.patch.object(config, "CACHE_DIR", tempfile.mkdtemp()),
                  mock.patch.object(server, "current_settings", lambda: dict(SETTINGS)),
                  mock.patch.dict(server._just_created, clear=True), mock.patch.dict(server._projects, clear=True),
                  mock.patch.dict(server._statuses, clear=True), mock.patch.dict(server._task_services, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    BODY = {"projectId": "30", "parentId": "40", "taskListId": "", "title": "  Charts:  horizontal bar option ",
            "description": "Figure 4."}

    def fields(self, **change):
        """api_task_fields with every read faked (counted), `change` replacing one."""
        self.read = []

        def note(name, value):
            def fn(*a, **k):
                self.read.append(name)
                if isinstance(value, Exception):
                    raise value
                return value
            return fn
        reads = {"workflow_statuses": ProposeTest.STATUSES, "project_services": ProposeTest.SERVICES,
                 "task_details": {"40": ProposeTest.PERIOD}, **change}
        patches = [mock.patch.object(server.productive, k, note(k, v)) for k, v in reads.items()]
        patches.append(mock.patch.object(server.flags, "project_share", note("sizes", change.get("sizes", {"sizes": ProposeTest.SIZES}))))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_fields_proposed_for_a_place(self):
        self.fields()
        got = server.api_task_fields({"project": "30", "date": "2026-11-07", "place": "parent:40"})
        self.assertEqual((got["status"]["id"], got["estimate"]["minutes"], got["service"]["id"]), ("1", 70, "73"))
        self.assertEqual(sorted(self.read), ["project_services", "sizes", "task_details", "workflow_statuses"])
        got = server.api_task_fields({"project": "30", "date": "2026-11-07", "place": "list:5"})
        self.assertEqual(got["status"]["id"], "2")
        self.assertEqual(self.read.count("workflow_statuses"), 1)  # kept 10 minutes
        self.assertEqual(self.read.count("project_services"), 1)
        self.assertEqual(self.read.count("task_details"), 1)  # no parent for a task in a list
        for bad in ({"project": "x", "place": "none"}, {"project": "30", "place": "parent:x"}, {"project": "30", "place": "list"},
                    {"project": "30", "place": "none:5"}, {"project": "30", "place": "elsewhere:5"}):
            with self.assertRaises(ValueError):
                server.api_task_fields({"date": "2026-11-07", **bad})

    def test_a_failed_read_leaves_its_proposal_empty(self):
        self.fields(workflow_statuses=productive.ProductiveError("HTTP 503"), task_details={})
        with mock.patch.object(server.logbook, "record"):
            got = server.api_task_fields({"project": "30", "date": "2026-11-07", "place": "parent:40",
                                          "title": "ACME - Contingency Hours Nov 2026"})
        self.assertEqual((got["statuses"], got["status"]["id"]), ([], ""))
        self.assertIn("could not be read", got["estimate"]["why"])  # the parent: read, but not returned
        self.assertEqual(got["service"]["id"], "73")  # its title, as the form shows it, still says it is a period's task

    def test_create_sends_the_status_and_estimate_checked(self):
        server._statuses["30"] = (time.time() + 600, ProposeTest.STATUSES)
        wire = Wire()
        with connected("claude-code"), mock.patch.object(productive, "https", wire):
            server.api_task_create(dict(self.BODY, statusId="2", estimate=90, serviceId="73"))
        item = wire.tools()[0][1]["items"][0]
        self.assertEqual((item["workflow_status"], item["initial_estimate"]), ("2", "90 min"))
        self.assertNotIn("service", item)  # never sent: a PM sets a task's service
        for change in ({"statusId": "9"}, {"statusId": "77"}, {"statusId": "x"}, {"estimate": 0}, {"estimate": "90"},
                       {"estimate": True}, {"estimate": productive.ESTIMATE_MAX + 1}, {"estimate": 2.5}):
            wire = Wire()
            with connected("claude-code"), mock.patch.object(productive, "https", wire):
                with self.assertRaises(ValueError):
                    server.api_task_create(dict(self.BODY, title="Another", **change))
            self.assertEqual(wire.calls, [], change)  # a closed or unknown status, a bad estimate: nothing sent
        server._statuses.clear()  # statuses not known here: any id may go, Productive checks it
        wire = Wire()
        with connected("claude-code"), mock.patch.object(productive, "https", wire):
            server.api_task_create(dict(self.BODY, title="Third", statusId="77", estimate=""))
        self.assertEqual(wire.tools()[0][1]["items"][0]["workflow_status"], "77")
        self.assertNotIn("initial_estimate", wire.tools()[0][1]["items"][0])

    def test_create_sends_what_the_page_confirmed(self):
        wire = Wire()
        server._projects["1"] = (time.time() + 600, [{"id": "30", "name": "Globex-OS"}])
        with connected("claude-code"), mock.patch.object(productive, "https", wire):
            got = server.api_task_create(dict(self.BODY))
        self.assertEqual(got, {"task": {"taskId": "777", "title": "Charts: horizontal bar option", "project": "Globex-OS",
                                        "projectId": "30"}})
        self.assertEqual(wire.tools()[0][1]["items"][0], {"title": "Charts: horizontal bar option", "project": "30",
                                                          "task_list": None, "assignee": "1", "parent_task": "40",
                                                          "description": "Figure 4."})
        with connected("claude-code"), mock.patch.object(productive, "https", wire):  # a second click: refused
            with self.assertRaises(ValueError) as caught:
                server.api_task_create(dict(self.BODY))
        self.assertIn("a moment ago", str(caught.exception))
        self.assertEqual(len(wire.writes()), 1)

    def test_an_unclear_answer_holds_a_second_create_for_ten_minutes(self):
        """A timeout, a 5xx or a reply that is not the success sentence: the task may exist, so the same title in the
        same project is refused for ten minutes and nothing more is sent. A clear refusal holds nothing."""
        for kind, wire in (("claude-code", Wire("Task created successfully (ID 777)")), ("token", Wire(rest=(502, b""))),
                           ("claude-code", Wire('Failed to create task. API request failed because of error: {"status": 504, '
                                               '"title": "Gateway Timeout"}', is_error=True))):
            server._just_created.clear()
            with connected(kind), mock.patch.object(productive, "https", wire):
                with self.assertRaises(productive.ProductiveUnclear):
                    server.api_task_create(dict(self.BODY))
                with self.assertRaises(ValueError) as caught:  # the second click
                    server.api_task_create(dict(self.BODY))
                self.assertIn("may exist", str(caught.exception))
                self.assertIn("Search for it", str(caught.exception))
                now = time.time()
                with mock.patch.object(server.time, "time", lambda: now + 9 * 60):
                    with self.assertRaises(ValueError):
                        server.api_task_create(dict(self.BODY))
                with mock.patch.object(server.time, "time", lambda: now + 11 * 60), \
                        mock.patch.object(productive, "create_task", lambda t, s: {"taskId": "778"}):
                    self.assertEqual(server.api_task_create(dict(self.BODY))["task"]["taskId"], "778")
            self.assertEqual(len(wire.writes()), 1, kind)
        server._just_created.clear()
        wire = Wire("Failed to create task. task_list is required (unless task_list=null)")
        with connected("claude-code"), mock.patch.object(productive, "https", wire):
            with self.assertRaises(productive.ProductiveError):
                server.api_task_create(dict(self.BODY))
        self.assertEqual(server._just_created, {})  # refused: nothing exists, the person fixes it and tries again

    def test_refused_before_anything_is_sent(self):
        bad = [{"projectId": ""}, {"projectId": "3/0"}, {"parentId": "x"}, {"taskListId": "5"}, {"title": " "},
               {"title": "x" * 141}, {"description": "x" * 5001}, {"title": "a\x1bb"}]
        for change in bad:
            wire = Wire()
            with connected("claude-code"), mock.patch.object(productive, "https", wire):
                with self.assertRaises(ValueError):
                    server.api_task_create(dict(self.BODY, **change))
            self.assertEqual(wire.calls, [], change)
        for env in ({"PULSAR_READ_ONLY": "1"}, {"PULSAR_READ_ONLY": "preview"}):
            wire = Wire()
            with mock.patch.dict(os.environ, env), connected("claude-code"), mock.patch.object(productive, "https", wire):
                with self.assertRaises(ValueError) as caught:
                    server.api_task_create(dict(self.BODY))
            self.assertEqual(wire.calls, [], env)
            self.assertIn("Pulsar", str(caught.exception))

    def test_the_key_alone_cannot_create_or_draft(self):
        for path in ("/api/task/create", "/api/task/draft"):
            self.assertIsNotNone(server.key_refusal(path, dict(self.BODY)))

    def test_places_and_projects(self):
        found = {"tasks": PlacesTest.T, "lists": []}
        with mock.patch.object(server.productive, "project_places", lambda p, s: found):
            self.assertEqual(server.api_task_places({"project": "30", "date": "2026-11-07"})["pick"], "parent:41")
        with self.assertRaises(ValueError):
            server.api_task_places({"project": "x", "date": "2026-11-07"})
        projects = [{"id": "1", "name": "Globex-OS Ongoing Support"}, {"id": "2", "name": "Acme-OS Ongoing Support"},
                    {"id": "3", "name": "Beta"}]
        with mock.patch.object(server.productive, "active_projects", lambda s: projects), \
                mock.patch.object(server, "api_suggest", lambda q: {"recent": [
                    {"project": "Acme-OS Ongoing Support", "minutes": 90}, {"project": "Beta", "minutes": 10},
                    {"project": "Acme-OS Ongoing Support", "minutes": 30}]}):
            got = server.api_task_projects({"date": "2026-11-07", "repo": "globex-web", "hint": ""})
        self.assertEqual([(p["id"], p["lately"]) for p in got["projects"]], [("2", 120), ("3", 10), ("1", 0)])
        self.assertEqual(got["pick"], "1")

        def failing(s):
            raise productive.ProductiveError("HTTP 503")
        server._projects.clear()
        with mock.patch.object(server.productive, "active_projects", failing):
            got = server.api_task_projects({"date": "2026-11-07"})
        self.assertEqual(got["projects"], [])
        self.assertIn("HTTP 503", got["error"])

    def test_draft_from_the_rows_in_the_time(self):
        tz = config.tz(SETTINGS)
        rows = [{"s": 600, "e": 660, "project": "acme", "titles": ["acme session"], "commits": ["Fix the menu"]},
                {"s": 650, "e": 700, "project": "other", "titles": ["other"], "commits": ["Other thing"]}]
        with mock.patch.object(server.likely, "enabled", lambda s: True), \
                mock.patch.object(server.likely, "proposal", lambda *a, **k: {"entries": rows}):
            got = server.api_task_draft({"date": "2026-11-04", "s": 600, "e": 690})
            self.assertEqual((got["title"], got["repo"]), ("Fix the menu", "acme"))
            self.assertEqual(server.api_task_draft({"date": "2026-11-04", "s": 600, "e": 690, "meeting": "Sync"})["title"],
                             "Sync")
            with mock.patch.object(server, "current_settings", lambda: dict(SETTINGS, ask_claude=False)):
                with self.assertRaises(ValueError):
                    server.api_task_draft({"date": "2026-11-04", "s": 600, "e": 690, "claude": True})
            asked = []
            with mock.patch.object(server, "history_rows", lambda d, s, days=0: ([], None)), \
                    mock.patch.object(server.newtask, "claude_draft",
                                      lambda date, rows, t0, history, project, tz=None, model=None:
                                      asked.append(rows) or {"title": "T"}):
                self.assertEqual(server.api_task_draft({"date": "2026-11-04", "s": 600, "e": 690, "claude": True}),
                                 {"title": "T"})
            self.assertEqual([r["project"] for r in asked[0]], ["acme"])  # only the project with most minutes there
        del tz


if __name__ == "__main__":
    unittest.main()
