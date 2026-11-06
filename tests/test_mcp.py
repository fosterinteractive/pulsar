"""pulsar --mcp against a real Pulsar server on a free port, with Productive and the day's sources made up. No network.

    python3 -m unittest discover -s tests
"""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRATCH = tempfile.mkdtemp(prefix="pulsar-tests-")
os.environ.setdefault("XDG_CONFIG_HOME", os.path.join(_SCRATCH, "config"))
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(_SCRATCH, "cache"))

from pulsar import config, dayplan, mcp, productive, server  # noqa: E402

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

    def patches(self):
        sug = {"recent": [{"taskId": "11", "title": "Build", "project": "Acme", "serviceId": "21", "service": "Dev",
                           "count": 3, "last": DATE, "minutes": 90}], "assigned": []}
        settings = {**config.load(), "timezone": "UTC", "person_id": "1", "org_id": "2"}
        return [
            mock.patch.object(server, "current_settings", lambda: settings),
            mock.patch.object(server, "public_config", lambda s: {
                "timezone": "UTC", "targetMinutes": 480,
                "rules": [{"match": SECRET_WORDS[7], "taskId": "11", "title": "Build", "project": "Acme"}]}),
            mock.patch.object(mcp, "PROJECTS", os.path.join(_SCRATCH, "no-sessions")),
            mock.patch.object(server, "api_entries", lambda q: {"entries": self.entries, "source": "token"}),
            mock.patch.object(server, "api_activity", lambda q: {"items": self.items, "away": [], "source": "aw"}),
            mock.patch.object(server, "api_proposal", lambda q: {"entries": self.rows, "tracker": "aw"}),
            mock.patch.object(server, "api_calendar", lambda q: {"events": self.events, "problem": ""}),
            mock.patch.object(server, "api_repeats", lambda q: {"repeats": []}),
            mock.patch.object(server, "api_suggest", lambda q: sug),
            mock.patch.object(server, "api_dayplan", lambda q: self.plan),
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
        env = mock.patch.dict(os.environ, {"PULSAR_MCP_SESSION_ID": "abcdef12", "PULSAR_READ_ONLY": ""})
        env.start()
        self.addCleanup(env.stop)
        mcp._pending.clear()
        server._just_logged.clear()  # the server's one-minute guard against the same entry twice
        if os.path.exists(dayplan.journal_path()):  # each test starts with no Log the day runs
            os.remove(dayplan.journal_path())

    def call(self, name, args):
        reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, {})
        return json.loads(reply["result"]["content"][0]["text"]), reply["result"]["isError"]

    # ---- the protocol --------------------------------------------------------------------------------------

    def test_protocol(self):
        out = io.BytesIO()
        lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                 {"jsonrpc": "2.0", "method": "notifications/initialized"},
                 {"jsonrpc": "2.0", "id": "d", "method": "server/discover"},
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                 {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope"}}]
        mcp.main(0, io.BytesIO(b"\n".join(json.dumps(x).encode() for x in lines) + b"\nnot json\n"), out)
        got = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(got[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(got[1]["error"]["code"], -32601)  # a method it does not know: Claude Code falls back
        self.assertEqual({t["name"] for t in got[2]["result"]["tools"]}, set(mcp.HANDLERS))
        self.assertEqual(got[3]["error"]["code"], -32602)
        self.assertEqual(got[4]["error"]["code"], -32700)
        self.assertEqual(len(got), 5)  # nothing for the notification
        mcp._state["port"] = self.httpd.server_address[1]

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

    def test_dry_run_writes_nothing_and_confirm_writes_once(self):
        args = {"date": DATE, "entries": [{"start": "09:00", "minutes": 60, "taskId": "11"},
                                          {"start": "10:30", "minutes": 20, "taskId": "11", "note": "Search fixed."}]}
        with self._create():
            dry, err = self.call("pulsar_log", args)
            self.assertFalse(err, dry)
            self.assertEqual(self.day.created, [])
            self.assertEqual([e["serviceId"] for e in dry["entries"]], ["21", "21"])  # the one used last
            self.assertTrue(dry["confirm"])
            changed, err = self.call("pulsar_log", {**args, "entries": args["entries"][:1], "confirm": dry["confirm"]})
            self.assertTrue(err)
            self.assertEqual(self.day.created, [])
            done, err = self.call("pulsar_log", {**args, "confirm": dry["confirm"]})
            self.assertFalse(err, done)
            self.assertEqual(done["written"], 2)
            self.assertEqual([(e["started_at"][11:16], e["minutes"], e["service_id"]) for e in self.day.created],
                             [("09:00", 60, "21"), ("10:30", 20, "21")])
            again, err = self.call("pulsar_log", {**args, "confirm": dry["confirm"]})  # a code works once
            self.assertTrue(err)
            self.assertEqual(len(self.day.created), 2)
        runs = dayplan.runs_for(DATE)  # journalled like Log the day, so the page can undo it
        self.assertEqual(sorted(x["row"] for r in runs for x in r["rows"] if x["status"] == "logged"), ["mcp-1", "mcp-2"])
        with self._create():  # and never twice: the journal refuses the same entry from a new run
            dry, _ = self.call("pulsar_log", args)
        self.assertIsNone(dry["confirm"])
        self.assertIn("not sent twice", dry["entries"][0]["problem"])

    def test_problems_block_the_code(self):
        with self._create():
            dry, _ = self.call("pulsar_log", {"date": DATE, "entries": [
                {"start": "10:10", "minutes": 10, "taskId": "11", "note": "Updated web/themes/acme/style.css"}]})
        self.assertIsNone(dry["confirm"])
        self.assertIn("file path", dry["entries"][0]["problem"])
        self.assertIn("overlap", dry["entries"][0]["problem"])  # logged 10:00-10:30
        _, err = self.call("pulsar_log", {"date": DATE, "entries": [{"start": "09:00", "minutes": 60, "taskId": "11"},
                                                                    {"start": "09:30", "minutes": 10, "taskId": "11"}]})
        self.assertTrue(err)
        self.assertEqual(self.day.created, [])

    def test_read_only_is_refused_at_both_ends(self):
        args = {"date": DATE, "entries": [{"start": "09:00", "minutes": 60, "taskId": "11"}]}
        dry, _ = self.call("pulsar_log", args)
        with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "1"}):  # this process: nothing is sent
            with mock.patch.object(mcp, "post") as post:
                done, _ = self.call("pulsar_log", {**args, "confirm": dry["confirm"]})
            post.assert_not_called()
        self.assertEqual(done["written"], 0)
        self.assertIn("read-only", done["refused"])
        dry, _ = self.call("pulsar_log", args)
        real_blocked, asked = config.writes_blocked, []

        def blocked():  # this process lets it through once; then the server's own check, the real one, decides
            asked.append(1)
            return None if len(asked) == 1 else real_blocked()
        with mock.patch.object(config, "writes_blocked", blocked), \
                mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "1"}):  # the server: the real create_entry refuses
            done, _ = self.call("pulsar_log", {**args, "confirm": dry["confirm"]})
        self.assertEqual(done["written"], 0)
        self.assertIn("read-only", done["results"][0]["why"])


if __name__ == "__main__":
    unittest.main()
