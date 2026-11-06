"""pulsar --mcp against a real Pulsar server on a free port, with Productive and the day's sources made up. No network.

    python3 -m unittest discover -s tests
"""

import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import __version__, config, dayplan, learn, likely, logbook, mcp, productive, server  # noqa: E402

REAL_API_PROPOSAL = server.api_proposal  # before FakeDay replaces it

DATE = "2026-11-04"
SECRET_WORDS = ["Inbox – client@example.com", "Re: salary review", "fix the SECRET-TOKEN-123 thing",
                "Doctor appointment", "Session title about Acme", "commit subject private", "a logged note about Bob",
                "rule-word-zz"]


T0 = 1793750400  # 2026-11-04 00:00 UTC


def entry(s, minutes, task="11", project="Acme", note=""):
    return {"id": f"e{s}", "date": DATE, "s": s, "e": s + minutes, "start": T0 + s * 60, "minutes": minutes, "note": note,
            "task": "Build", "taskId": task, "service": "Dev", "serviceId": "21", "project": project, "budget": "B",
            "billable": True}


def plan_row(s, e, task="11", repo="acme-site", **kw):
    return {"s": s, "e": e, "minutes": e - s, "taskId": task, "title": "Build", "project": "Acme", "repo": repo,
            "meeting": "", "serviceId": "21", "service": "Dev", "budget": "B", "billable": True, "note": "",
            "noteFrom": "", "confidence": "high", "why": "a task link in the session", "checked": True, "flags": [],
            "alts": [], **kw}


class FakeDay:
    """Every source of one made-up day, as Pulsar's API answers it."""

    def __init__(self):
        self.entries = [entry(600, 30, note=SECRET_WORDS[6])]
        self.items = [{"s": 540, "e": 720, "app": "Cursor", "title": SECRET_WORDS[0] + " " + SECRET_WORDS[7], "host": "",
                       "url": "", "cat": "work"},
                      {"s": 720, "e": 730, "app": "Safari", "title": SECRET_WORDS[1], "host": "", "url": "", "cat": "personal"}]
        self.rows = [{"s": 540, "e": 600, "minutes": 60, "project": "acme-site", "kind": "focused",
                      "titles": [SECRET_WORDS[4]], "messages": 3, "first_message": SECRET_WORDS[2],
                      "commits": [SECRET_WORDS[5]], "sessions": ["abcdef12"],
                      "learned": [{"taskId": "11", "title": "Build", "project": "Acme", "why": "logged in this session"}]},
                     {"s": 630, "e": 720, "minutes": 90, "project": "other-repo", "kind": "focused", "titles": [],
                      "messages": 1, "first_message": "x", "sessions": ["99999999"]}]
        self.events = [{"s": 660, "e": 690, "title": SECRET_WORDS[3], "uid": "u1", "recurring": False}]
        self.plan = {"entries": [plan_row(540, 600), plan_row(660, 690, task="12", repo="", meeting=SECRET_WORDS[3],
                                                             note=SECRET_WORDS[3], noteFrom="meeting", checked=False,
                                                             why=f"you logged it during 2 of the last 3 meetings like this; "
                                                                 f"on your calendar as “{SECRET_WORDS[3]}”")],
                     "unplaced": [], "uncovered": [], "totals": {"proposed": 90, "logged": 30}}
        self.created = []
        self.preview = None  # Pulsar Preview's block in /api/config: {"writes": bool}

    def patches(self):
        sug = {"recent": [{"taskId": "11", "title": "Build", "project": "Acme", "serviceId": "21", "service": "Dev",
                           "count": 3, "last": DATE, "minutes": 90}], "assigned": []}
        settings = {**config.load(), "timezone": "UTC", "person_id": "1", "org_id": "2"}
        return [
            mock.patch.object(server, "current_settings", lambda: settings),
            mock.patch.object(server, "public_config", lambda s: {
                "version": __version__, "preview": self.preview, "timezone": "UTC", "targetMinutes": 480,
                "writesBlocked": ("Pulsar runs read-only (PULSAR_READ_ONLY)." if os.environ.get("PULSAR_READ_ONLY") else None) or (
                    "Pulsar Preview does not write to Productive." if self.preview and not self.preview.get("writes") else None),
                "rules": [{"match": SECRET_WORDS[7], "taskId": "11", "title": "Build", "project": "Acme"}]}),
            mock.patch.object(mcp, "PROJECTS", os.path.join(_SCRATCH, "no-sessions")),
            mock.patch.object(server, "api_entries", lambda q: {"entries": self.entries, "source": "token"}),
            mock.patch.object(server, "api_activity", lambda q: {"items": self.items, "away": [], "source": "aw"}),
            mock.patch.object(server, "api_proposal", lambda q: {"entries": self.rows, "tracker": "aw"}),
            mock.patch.object(server, "api_calendar", lambda q: {"events": self.events, "problem": ""}),
            mock.patch.object(server, "api_repeats", lambda q: {"repeats": []}),
            mock.patch.object(server, "api_suggest", lambda q: sug),
            # pulsar_status's read-only probe (a bad source) reaches the real one: it relies on its order of checks
            mock.patch.object(server, "api_dayplan", lambda q, real=server.api_dayplan:
                              real(q) if q.get("source") == "none" else self.plan),
            mock.patch.object(productive, "entries", lambda d0, d1, s: (self.entries, "", None)),
            mock.patch.object(productive, "bookable_services", lambda t, d, s: {
                "task": {"title": "Build", "project": "Acme", "projectId": "5", "serviceId": "21"},
                "services": [{"id": "21", "name": "Dev", "budget": "B", "billable": True},
                             {"id": "22", "name": "QA", "budget": "B", "billable": True}]}),
            mock.patch.object(productive, "search_tasks", lambda q, s: []),
        ]


class McpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_dirs()
        config.session_secret()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        mcp._state.update({"port": cls.httpd.server_address[1], "verified": False})

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        self.day = FakeDay()
        for p in self.day.patches():
            p.start()
            self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ, {"PULSAR_MCP_SESSION_ID": "abcdef12", "PULSAR_READ_ONLY": "",
                                           "CLAUDE_PROJECT_DIR": "", "PULSAR_PREVIEW": "", "PULSAR_MCP_IGNORE_LOGGED": ""})
        env.start()
        self.addCleanup(env.stop)
        mcp._pending.clear()
        server._just_logged.clear()  # the server's one-minute guard against the same entry twice
        if os.path.exists(dayplan.journal_path()):  # each test starts with no Log the day runs
            os.remove(dayplan.journal_path())

    def call(self, name, args, client="claude-code"):
        """A tool call from a client that said its name in initialize (Claude Code's is "claude-code")."""
        reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}},
                           {"client": client})
        return json.loads(reply["result"]["content"][0]["text"]), reply["result"]["isError"]

    # ---- the protocol --------------------------------------------------------------------------------------

    def test_protocol(self):
        out = io.BytesIO()
        lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "claude-code", "version": "2"}}},
                 {"jsonrpc": "2.0", "method": "notifications/initialized"},
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                 {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope"}},
                 {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "pulsar_status"}}]
        mcp.main(self.httpd.server_address[1],
                 io.BytesIO(b"\n".join(json.dumps(x).encode() for x in lines) + b"\nnot json\n"), out)
        got = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(got[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertNotIn("resultType", got[0]["result"])
        self.assertEqual({t["name"] for t in got[1]["result"]["tools"]}, set(mcp.HANDLERS))
        self.assertEqual(got[2]["error"]["code"], -32602)
        self.assertTrue(got[3]["result"]["structuredContent"]["logFromHere"])  # the client initialize named
        self.assertEqual(got[4]["error"]["code"], -32700)
        self.assertEqual(len(got), 5)  # nothing for the notification

    def test_protocol_2026_07_28(self):
        """No handshake: server/discover, then every request with its version, capabilities and client in _meta."""
        def req(i, method, params=None, client="claude-code", version="2026-07-28", caps=True):
            meta = {"io.modelcontextprotocol/protocolVersion": version,
                    "io.modelcontextprotocol/clientInfo": {"name": client, "version": "2"}}
            if caps:
                meta["io.modelcontextprotocol/clientCapabilities"] = {}
            return {"jsonrpc": "2.0", "id": i, "method": method, "params": {**(params or {}), "_meta": meta}}
        lines = [req(1, "server/discover"), req(2, "tools/list"),
                 req(3, "tools/call", {"name": "pulsar_status", "arguments": {}}),
                 req(4, "tools/call", {"name": "pulsar_log", "arguments": {"code": "x", "summary": "y"}}, client="other"),
                 req(5, "tools/list", version="2099-01-01"), req(6, "tools/list", caps=False)]
        out = io.BytesIO()
        mcp.main(self.httpd.server_address[1], io.BytesIO(b"\n".join(json.dumps(x).encode() for x in lines)), out)
        got = {x["id"]: x for x in map(json.loads, out.getvalue().splitlines())}
        found = got[1]["result"]
        self.assertEqual((found["resultType"], found["supportedVersions"][0]), ("complete", "2026-07-28"))
        self.assertIn("2025-11-25", found["supportedVersions"])  # and the handshake, for older clients
        self.assertEqual(found["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "pulsar")
        self.assertEqual((found["capabilities"], found["instructions"]), ({"tools": {}}, mcp.INSTRUCTIONS))
        self.assertGreaterEqual(found["ttlMs"], 0)
        self.assertEqual(got[2]["result"]["resultType"], "complete")
        self.assertGreaterEqual(got[2]["result"]["ttlMs"], 0)
        self.assertEqual(len(got[2]["result"]["tools"]), len(mcp.TOOLS))
        self.assertEqual(got[3]["result"]["resultType"], "complete")
        self.assertTrue(got[3]["result"]["structuredContent"]["logFromHere"])  # the client from this request's _meta
        self.assertTrue(got[4]["result"]["isError"])
        self.assertIn("only in Claude Code", got[4]["result"]["content"][0]["text"])
        self.assertEqual(got[5]["error"]["code"], -32022)
        self.assertIn("2026-07-28", got[5]["error"]["data"]["supported"])
        self.assertEqual(got[6]["error"]["code"], -32602)

    def test_the_write_tool_is_marked_for_the_person(self):
        tools = {t["name"]: t for t in mcp.TOOLS}
        self.assertEqual(tools["pulsar_log"]["_meta"], {"anthropic/requiresUserInteraction": True})
        self.assertFalse(tools["pulsar_log"]["annotations"]["readOnlyHint"])
        self.assertEqual(set(tools["pulsar_log"]["inputSchema"]["required"]), {"code", "summary"})
        for name in set(tools) - {"pulsar_log"}:
            self.assertTrue(tools[name]["annotations"]["readOnlyHint"], name)
            self.assertNotIn("_meta", tools[name])
        for text in [mcp.INSTRUCTIONS] + [t["description"] for t in mcp.TOOLS]:
            self.assertNotIn("\u2014", text)  # no em dashes in what the model reads

    def test_impostor_never_gets_the_key(self):
        mcp._state["verified"] = False
        with mock.patch.object(mcp, "_request", side_effect=lambda *a, **k: {"proof": "0" * 64}) as req:
            with self.assertRaises(mcp.ToolError):
                mcp.fetch({"c": ("/api/config", None)})
        self.assertTrue(all(c.args[3:4] in ((), (None,)) and not c.kwargs.get("key") for c in req.call_args_list))

    # ---- reading -------------------------------------------------------------------------------------------

    def test_nothing_private_leaves(self):
        for name, args in (("pulsar_day", {"date": DATE}), ("pulsar_propose", {"date": DATE}),
                           ("pulsar_session", {"date": DATE}), ("pulsar_suggest", {"date": DATE, "start": "11:00", "minutes": 30}),
                           ("pulsar_tasks", {"query": "build"})):
            data, err = self.call(name, args)
            self.assertFalse(err, data)
            text = json.dumps(data, ensure_ascii=False)
            for w in SECRET_WORDS:
                self.assertNotIn(w, text, f"{name} leaks {w!r}")

    def test_day(self):
        data, _ = self.call("pulsar_day", {"date": DATE})
        self.assertEqual(data["totals"]["loggedMinutes"], 30)
        self.assertEqual([u["start"] for u in data["unlogged"]], ["09:00", "10:30"])
        self.assertTrue(data["likely"][0]["thisSession"])
        self.assertNotIn("thisSession", data["likely"][1])

    def test_propose_keeps_tasks_drops_meeting_words(self):
        data, _ = self.call("pulsar_propose", {"date": DATE})
        first, meeting = data["proposed"]
        self.assertEqual((first["start"], first["minutes"], first["taskId"], first["serviceId"]), ("09:00", 60, "11", "21"))
        self.assertTrue(meeting["meeting"])
        self.assertNotIn("noteDraft", meeting)  # a meeting's note is its title
        self.assertIn("on your calendar", meeting["why"])

    def test_session_finds_its_own_rows(self):
        data, _ = self.call("pulsar_session", {"date": DATE})
        self.assertEqual(data["session"], "abcdef12")
        self.assertEqual([(r["start"], r["minutes"]) for r in data["rows"]], [("09:00", 60)])
        self.assertEqual(data["notLoggedYet"], [{"start": "09:00", "minutes": 60, "project": "acme-site", "taskId": "11",
                                                 "task": "Build", "serviceId": "21"}])
        self.day.rows.append({"s": 900, "e": 930, "minutes": 30, "project": "acme-site", "kind": "focused",
                              "sessions": ["abcdef12", "99999999"]})  # another session had these minutes too
        data, _ = self.call("pulsar_session", {"date": DATE})
        self.assertEqual([x["start"] for x in data["notLoggedYet"]], ["09:00"])
        self.assertEqual([x["start"] for x in data["sharedNotLogged"]], ["15:00"])
        with mock.patch.dict(os.environ, {"PULSAR_MCP_SESSION_ID": "", "CLAUDE_CODE_SESSION_ID": ""}):
            data, err = self.call("pulsar_session", {"date": DATE})
        self.assertTrue(err)

    # ---- writing -------------------------------------------------------------------------------------------

    def _create(self):
        created = self.day.created

        def fake(e, s):
            created.append(e)
            return {"id": str(900 + len(created))}
        return mock.patch.object(productive, "create_entry", fake)

    ARGS = {"date": DATE, "entries": [{"start": "09:00", "minutes": 60, "taskId": "11"},
                                      {"start": "10:30", "minutes": 20, "taskId": "11", "note": "Search fixed."}]}

    def test_preview_writes_nothing_and_says_it_in_pulsars_words(self):
        with self._create():
            dry, err = self.call("pulsar_preview", self.ARGS)
        self.assertFalse(err, dry)
        self.assertEqual(self.day.created, [])
        self.assertEqual(dayplan.runs_for(DATE), [])  # a preview is not journalled
        self.assertEqual([e["serviceId"] for e in dry["entries"]], ["21", "21"])  # the one used last
        self.assertTrue(dry["code"])
        self.assertEqual(dry["summary"], 'Wed 4 Nov 09:00-10:00 (60 min): Build (Acme), Dev, billable. No note.\n'
                                         'Wed 4 Nov 10:30-10:50 (20 min): Build (Acme), Dev, billable. Note: "Search fixed."')
        self.assertNotIn("\u2014", dry["summary"])

    def test_log_writes_the_previewed_entries_once(self):
        with self._create():
            dry, _ = self.call("pulsar_preview", self.ARGS)
            for summary in (dry["summary"] + " ", dry["summary"].replace("Search", "Bug"), "", None):
                got, err = self.call("pulsar_log", {"code": dry["code"], "summary": summary})
                self.assertTrue(err, summary)
                self.assertIn("word for word", got["error"])
            self.assertEqual(self.day.created, [])
            done, err = self.call("pulsar_log", {"code": dry["code"], "summary": dry["summary"]})  # the code still works
            self.assertFalse(err, done)
            self.assertEqual(done["written"], 2)
            self.assertEqual([(e["started_at"][11:16], e["minutes"], e["service_id"], e["note"]) for e in self.day.created],
                             [("09:00", 60, "21", ""), ("10:30", 20, "21", "Search fixed.")])
            again, err = self.call("pulsar_log", {"code": dry["code"], "summary": dry["summary"]})  # a code works once
            self.assertTrue(err)
            self.assertIn("unknown or has expired", again["error"])
            self.assertEqual(len(self.day.created), 2)
        runs = dayplan.runs_for(DATE)  # journalled like Log the day, so the page can undo it
        self.assertEqual(sorted(x["row"] for r in runs for x in r["rows"] if x["status"] == "logged"), ["mcp-1", "mcp-2"])
        with self._create():  # and never twice: the journal refuses the same entry from a new preview
            dry, _ = self.call("pulsar_preview", self.ARGS)
        self.assertIsNone(dry["code"])
        self.assertIn("not sent twice", dry["entries"][0]["problem"])

    def test_a_code_expires(self):
        dry, _ = self.call("pulsar_preview", self.ARGS)
        mcp._pending[dry["code"]]["until"] -= mcp.CONFIRM_FOR + 1
        got, err = self.call("pulsar_log", {"code": dry["code"], "summary": dry["summary"]})
        self.assertTrue(err)
        self.assertEqual(self.day.created, [])

    def test_log_only_in_claude_code(self):
        dry, _ = self.call("pulsar_preview", self.ARGS)
        for client in ("", "claude-ai", "Claude Code", "cursor"):  # they ignore requiresUserInteraction
            with self._create():
                got, err = self.call("pulsar_log", {"code": dry["code"], "summary": dry["summary"]}, client=client)
            self.assertTrue(err)
            self.assertIn("only in Claude Code", got["error"])
        self.assertEqual(self.day.created, [])
        self.assertIn(dry["code"], mcp._pending)  # refused before the code is used

    def test_problems_block_the_code(self):
        with self._create():
            dry, _ = self.call("pulsar_preview", {"date": DATE, "entries": [
                {"start": "10:10", "minutes": 10, "taskId": "11", "note": "Updated web/themes/acme/style.css"}]})
        self.assertIsNone(dry["code"])
        self.assertIsNone(dry["summary"])
        self.assertIn("file path", dry["entries"][0]["problem"])
        self.assertIn("overlap", dry["entries"][0]["problem"])  # logged 10:00-10:30
        _, err = self.call("pulsar_preview", {"date": DATE, "entries": [{"start": "09:00", "minutes": 60, "taskId": "11"},
                                                                        {"start": "09:30", "minutes": 10, "taskId": "11"}]})
        self.assertTrue(err)
        self.assertEqual(self.day.created, [])

    def test_read_only_is_refused_at_both_ends_and_never_journalled(self):
        dry, _ = self.call("pulsar_preview", self.ARGS)
        with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "1"}):  # this process: nothing is sent
            with mock.patch.object(mcp, "post") as post:
                done, err = self.call("pulsar_log", {"code": dry["code"], "summary": dry["summary"]})
            post.assert_not_called()
        self.assertTrue(err)
        self.assertIn("read-only", done["error"])
        before = len(logbook.read(1))
        real_blocked, asked = config.writes_blocked, []

        def blocked():  # this process lets it through once; then the server's own check, the real one, decides
            asked.append(1)
            return None if len(asked) == 1 else real_blocked()
        with mock.patch.object(config, "writes_blocked", blocked), \
                mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "1"}):  # the server refuses: the real create_entry
            done, _ = self.call("pulsar_log", {"code": dry["code"], "summary": dry["summary"]})
        self.assertEqual(done["written"], 0)
        self.assertIn("read-only", done["results"][0]["why"])
        self.assertEqual(dayplan.runs_for(DATE), [])  # refused before the journal heard of it
        self.assertEqual(len(logbook.read(1)), before)  # and not a line in Logs

    # ---- note drafts from commits ------------------------------------------------------------------------

    def _repo(self, name):
        folder = os.path.join(_SCRATCH, "repos", name)
        os.makedirs(os.path.join(folder, ".git"), exist_ok=True)
        return folder

    def test_commit_notes_only_from_the_callers_repository(self):
        self.day.plan["entries"] = [plan_row(540, 600, note="Search keeps every letter", noteFrom="commits"),
                                    plan_row(800, 830, repo="other-repo", note="Weekly call", noteFrom="repeat")]
        self.day.rows[0]["s"] = 540  # the session's own row, for pulsar_session

        def notes(folder):
            with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": folder}):
                data, _ = self.call("pulsar_propose", {"date": DATE})
                mine, _ = self.call("pulsar_session", {"date": DATE})
            return ([p.get("noteDraft") for p in data["proposed"]],
                    [r.get("suggested", {}).get("noteDraft") for r in mine["rows"]])
        kept = ["Search keeps every letter", "Weekly call"]
        self.assertEqual(notes(self._repo("acme-site")), (kept, ["Search keeps every letter"]))
        self.assertEqual(notes(os.path.join(self._repo("acme-site"), "web", "themes")), (kept, ["Search keeps every letter"]))
        self.assertEqual(notes(self._repo("ACME_Site-2")), (kept, ["Search keeps every letter"]))  # loosely, as commits.matches
        for folder in (self._repo("other-repo"), "", "relative/acme-site", os.path.join(_SCRATCH, "not-a-repo")):
            self.assertEqual(notes(folder), ([None, "Weekly call"], [None]), folder)  # a repeat's note stays

    # ---- Pulsar closed, and pulsar_status ----------------------------------------------------------------

    def test_every_tool_says_open_pulsar_when_it_is_closed(self):
        dry, _ = self.call("pulsar_preview", self.ARGS)
        with socket.socket() as sock:  # a port nothing listens on
            sock.bind(("127.0.0.1", 0))
            dead = sock.getsockname()[1]
        port = mcp._state["port"]
        self.addCleanup(mcp._state.update, {"port": port})
        for verified in (True, False):
            mcp._state.update({"port": dead, "verified": verified})
            for name, args in (("pulsar_day", {}), ("pulsar_propose", {}), ("pulsar_session", {}),
                               ("pulsar_suggest", {"start": "09:00", "minutes": 30}), ("pulsar_tasks", {"query": "x"}),
                               ("pulsar_preview", self.ARGS), ("pulsar_log", {"code": dry["code"], "summary": dry["summary"]})):
                got, err = self.call(name, args)
                self.assertTrue(err, name)
                self.assertIn("Open Pulsar", got["error"], name)
            status, err = self.call("pulsar_status", {})
            self.assertFalse(err)
            self.assertEqual(status["pulsar"], "not answering")
            self.assertIn("open Pulsar", status["next"])
            self.assertRegex(status["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn(dry["code"], mcp._pending)  # the code waits for Pulsar
        mcp._state["port"] = port
        with self._create():  # a fake Productive: a test never writes for real (this one once reached Productive)
            done, err = self.call("pulsar_log", {"code": dry["code"], "summary": dry["summary"]})
        self.assertFalse(err, done)
        self.assertEqual(done["written"], 2)

    def test_status(self):
        status, err = self.call("pulsar_status", {})
        self.assertFalse(err)
        self.assertEqual((status["pulsar"], status["version"], status["mcpVersion"]), ("open", __version__, __version__))
        self.assertEqual((status["writesBlocked"], status["why"], status["serverBlocked"]), (False, [], False))
        self.assertEqual((status["timezone"], status["logFromHere"]), ("UTC", True))
        self.assertRegex(status["time"], r"^\d{2}:\d{2}$")
        with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "1"}):  # this process and the server: both say so
            status, _ = self.call("pulsar_status", {})
        self.assertTrue(status["writesBlocked"])
        self.assertTrue(status["serverBlocked"])
        self.assertEqual(len(status["why"]), 2)
        self.day.preview = {"writes": False}
        status, _ = self.call("pulsar_status", {}, client="cursor")
        self.assertEqual((status["writesBlocked"], status["logFromHere"]), (True, False))
        self.assertIn("Preview", status["why"][0])
        self.assertIn("only in Claude Code", status["logFromHereWhy"])
        self.day.preview = {"writes": True}
        status, _ = self.call("pulsar_status", {})
        self.assertFalse(status["writesBlocked"])

    # ---- the test switch: a day already logged, planned blind -------------------------------------------

    def test_test_switch_hides_the_day_everywhere(self):
        asked, on_logged = [], {"date": DATE, "entries": [{"start": "10:00", "minutes": 30, "taskId": "11"}]}
        self.day.rows[0]["learned"] = [{"taskId": "11", "title": "Build", "project": "Acme", "why": "logged in this session"}]
        with mock.patch.object(server, "api_proposal", lambda q: asked.append(dict(q)) or {"entries": self.day.rows}):
            with mock.patch.dict(os.environ, {"PULSAR_MCP_IGNORE_LOGGED": "1"}):  # not read-only: every tool refuses
                for name, args in (("pulsar_day", {"date": DATE}), ("pulsar_propose", {"date": DATE}),
                                   ("pulsar_preview", on_logged)):
                    got, err = self.call(name, args)
                    self.assertTrue(err, name)
                    self.assertIn("read-only", got["error"])
            with mock.patch.dict(os.environ, {"PULSAR_MCP_IGNORE_LOGGED": "1", "PULSAR_READ_ONLY": "1"}):
                for name, args in (("pulsar_day", {"date": DATE}), ("pulsar_propose", {"date": DATE}),
                                   ("pulsar_session", {"date": DATE}),
                                   ("pulsar_suggest", {"date": DATE, "start": "09:00", "minutes": 30})):
                    asked.clear()
                    got, err = self.call(name, args)
                    self.assertFalse(err, got)
                    self.assertTrue(asked and all(q.get("ignore_logged") == "1" for q in asked), (name, asked))
                    self.assertNotIn(SECRET_WORDS[6], json.dumps(got))
                day, _ = self.call("pulsar_day", {"date": DATE})
                self.assertEqual((day["totals"]["loggedMinutes"], day["logged"]), (0, []))
                # 10:00-10:30 is logged for real: the blind dry run does not show it
                dry, err = self.call("pulsar_preview", on_logged)
                self.assertFalse(err, dry)
                self.assertTrue(dry["code"])
                self.assertNotIn("overlap", json.dumps(dry))
        dry, _ = self.call("pulsar_preview", on_logged)
        self.assertIn("overlap", dry["entries"][0]["problem"])  # without the switch it still does
        self.assertIsNone(dry["code"])

    def test_likely_learns_blind_only_on_a_read_only_server(self):
        seen = []
        row = {"s": 540, "e": 600, "minutes": 60, "project": "acme-site", "kind": "focused", "titles": [], "sessions": []}
        with mock.patch.object(likely, "enabled", lambda s: True), \
                mock.patch.object(likely, "proposal", lambda *a, **k: {"entries": [dict(row)], "tracker": "aw"}), \
                mock.patch.object(server, "history_ready", lambda d, s: True), \
                mock.patch.object(server, "history_rows", lambda d, s, days=0: ([], "")), \
                mock.patch.object(server, "productive_rows", lambda d0, d1, s: (self.day.entries, "", None, None)), \
                mock.patch.object(learn, "annotate", lambda p, d, s, before, today, tz: seen.append(list(today))):
            REAL_API_PROPOSAL({"date": DATE})
            self.assertEqual(len(seen[-1]), 1)  # normally the day's own entries teach the rows after them
            with self.assertRaises(ValueError):
                REAL_API_PROPOSAL({"date": DATE, "ignore_logged": "1"})
            with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "1"}):
                REAL_API_PROPOSAL({"date": DATE, "ignore_logged": "1"})
            self.assertEqual(seen[-1], [])

    # ---- a session title standing in for a repository never leaves --------------------------------------

    def test_session_titles_never_leave_as_repositories(self):
        title1, title2 = "Munk home page carousel", "IMPORTANT! Pricing talk with Bob about the Acme renewal plan"
        cut2 = "Pricing talk with Bob about the Acme ren"  # the skill script's fallback: no IMPORTANT!, 40 characters
        past = "Secret title of a session last week"
        self.day.rows += [
            {"s": 800, "e": 860, "minutes": 60, "project": title1, "kind": "focused", "titles": [title1],
             "sessions": ["abcdef12"], "learned": [{"taskId": "11", "title": "Build", "project": "Acme",
                                                    "why": f"logged while in {past}: 2 h 5 min"}]},
            {"s": 870, "e": 900, "minutes": 30, "project": cut2, "kind": "focused", "titles": [title2],
             "sessions": ["abcdef12"]},
            {"s": 905, "e": 925, "minutes": 20, "project": "Claude Code", "kind": "focused", "titles": [],
             "sessions": ["abcdef12"]}]
        self.day.plan["entries"] += [plan_row(800, 860, repo=title1, why=f"you logged it while in {title1} (58%)"),
                                     plan_row(870, 900, repo=cut2, why=f"logged while in {past}: 2 h 5 min"),
                                     plan_row(905, 925, repo="Claude Code", why="you logged it while in acme-site (40%)")]
        self.day.plan["unplaced"] = [{"minutes": 10, "task": None, "project": title1,
                                      "why": f"{title1}: you never log this repository"}]
        outs = {}
        for name, args in (("pulsar_day", {"date": DATE}), ("pulsar_propose", {"date": DATE}),
                           ("pulsar_session", {"date": DATE}),
                           ("pulsar_suggest", {"date": DATE, "start": "13:20", "minutes": 60})):
            data, err = self.call(name, args)
            self.assertFalse(err, data)
            outs[name] = data
            text = json.dumps(data, ensure_ascii=False)
            for w in (title1, "Pricing talk", past) + tuple(SECRET_WORDS):
                self.assertNotIn(w, text, f"{name} leaks {w!r}")
        repos = [e["repository"] for e in outs["pulsar_propose"]["proposed"]]
        self.assertEqual(repos[0], "acme-site")  # a real repository still says its name
        self.assertEqual(set(repos[2:]), {""})
        self.assertIn("acme-site", outs["pulsar_propose"]["proposed"][4]["why"])
        self.assertIn("other work", outs["pulsar_propose"]["proposed"][3]["why"])
        self.assertEqual({r["project"] for r in outs["pulsar_session"]["rows"]}, {"acme-site", ""})
        self.assertNotIn("Claude Code", {r["project"] for r in outs["pulsar_day"]["likely"]})

    # ---- logging waits for the person's yes ----------------------------------------------------------------

    def test_log_waits_for_a_yes_in_a_new_message(self):
        dry, _ = self.call("pulsar_preview", self.ARGS)
        log = next(t for t in mcp.TOOLS if t["name"] == "pulsar_log")
        for text in (mcp.INSTRUCTIONS, log["description"], dry["next"]):
            self.assertIn("new message", text)
            self.assertIn("same turn", text)
        self.assertTrue(log["_meta"]["anthropic/requiresUserInteraction"])


if __name__ == "__main__":
    unittest.main()
