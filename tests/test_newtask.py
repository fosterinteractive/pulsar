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

    def test_an_answer_that_is_not_the_success_sentence_may_have_created_it(self):
        for text in ("Something about ID: 55", ""):
            with connected("claude-code"), mock.patch.object(productive, "https", Wire(text)):
                with self.assertRaises(productive.ProductiveUnclear):
                    productive.create_task(TASK, SETTINGS)
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
        with mock.patch.object(productive, "_claude_binary", lambda: "/bin/claude-fake"), \
                mock.patch.object(askclaude, "steps", lambda *a, **k: steps):
            got = newtask.claude_draft("2026-10-15", self.ROWS, 0, [{"task": "Hero images", "project": "P"}], "P", run=run)
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
        self.assertEqual(kw["cwd"], config.CACHE_DIR)
        for said in ("Numbering restarts per list now.", "Lists: restart numbering per list", "Hero images", "from 10:00 to 11:00"):
            self.assertIn(said, kw["input"])
        self.assertNotIn("IMPORTANT!", kw["input"])

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
                  mock.patch.dict(server._just_created, clear=True), mock.patch.dict(server._projects, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    BODY = {"projectId": "30", "parentId": "40", "taskListId": "", "title": "  Charts:  horizontal bar option ",
            "description": "Figure 4."}

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
                                      lambda date, rows, t0, history, project, tz=None: asked.append(rows) or {"title": "T"}):
                self.assertEqual(server.api_task_draft({"date": "2026-11-04", "s": 600, "e": 690, "claude": True}),
                                 {"title": "T"})
            self.assertEqual([r["project"] for r in asked[0]], ["acme"])  # only the project with most minutes there
        del tz


if __name__ == "__main__":
    unittest.main()
