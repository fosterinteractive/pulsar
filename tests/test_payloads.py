"""What exactly reaches Productive: every write's full request, through the REST token and through Claude Code's MCP
connection, from the lowest level (productive.https) up through POST /api/log, Undo and pulsar_log. Counting calls
is not enough: a day off, a swapped id or a wrong unit would still be one call (the third review, 2026-11-06, A12).

    python3 -m unittest discover -s tests
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import config, dayplan, mcp, productive, server  # noqa: E402
import test_mcp  # noqa: E402  its made-up day (FakeDay); a module, so its tests are not collected twice here

SETTINGS = {"timezone": "America/Toronto", "person_id": "1", "org_id": "2"}  # every id differs, so a swap shows
DATE = "2026-11-04"
ENTRY = {"date": DATE, "minutes": 20, "started_at": "2026-11-04T14:10:00-05:00", "note": "Search keeps every letter",
         "task_id": "5", "service_id": "6"}


class Wire:
    """Productive at the lowest level (productive.https), REST and MCP: every request is kept with its decoded body,
    and answered with Productive's own success. Nothing here reaches the network."""

    def __init__(self, person="1"):
        self.calls, self.person = [], person

    def __call__(self, method, url, body=None, headers=None, timeout=60, write=False, head=None):
        doc = json.loads(body) if body else None
        self.calls.append({"method": method, "url": url, "body": doc, "headers": headers or {}, "write": write})
        if url != productive.MCP_URL:  # REST
            return {"POST": (201, b'{"data": {"id": "901", "type": "time_entries"}}'), "PATCH": (200, b"{}"),
                    "DELETE": (204, b"")}[method]
        if doc["method"] == "initialize":
            if head is not None:
                head["mcp-session-id"] = "s-1"
            return 200, b'data: {"jsonrpc":"2.0","id":0,"result":{}}'
        if doc["method"] == "notifications/initialized":
            return 202, b""
        name, args = doc["params"]["name"], doc["params"]["arguments"]
        if name == "load_resource":
            text = ('Found time entry:\n```{"item":{"id":"%s","type":"time_entries","fields":{"person":{"type":"people",'
                    '"id":"%s"}}},"included":[]}```' % (args["id"], self.person))
        elif name == "create_resource":
            text = "time entry created (ID: 901)."
        else:
            text = "time entry %s (ID: %s)." % ({"update_resource": "updated", "delete_resource": "deleted"}[name],
                                               args["items"][0]["id"])
        answer = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}}
        return 200, ("event: message\ndata: " + json.dumps(answer)).encode()

    def writes(self) -> list:
        return [c for c in self.calls if c["write"]]

    def tools(self) -> list:
        """(tool, arguments) of every MCP tool call, in order."""
        return [(c["body"]["params"]["name"], c["body"]["params"]["arguments"]) for c in self.calls
                if c["url"] == productive.MCP_URL and c["body"]["method"] == "tools/call"]


def connected(kind: str):
    return mock.patch.object(productive, "connection", lambda s: {"kind": kind, "token": "t"})


WRITES_ALLOWED = {"PULSAR_READ_ONLY": "", "PULSAR_PREVIEW": ""}  # so a write reaches the fake wire, and only it


class ProductiveBodiesTest(unittest.TestCase):
    """create_entry, update_entry and delete_entry: the request each one sends, whole."""

    def setUp(self):
        env = mock.patch.dict(os.environ, WRITES_ALLOWED)
        env.start()
        self.addCleanup(env.stop)

    def test_rest_create(self):
        wire = Wire()
        with connected("token"), mock.patch.object(productive, "https", wire):
            self.assertEqual(productive.create_entry(ENTRY, SETTINGS), {"id": "901"})
        [w] = wire.writes()
        self.assertEqual((w["method"], w["url"]), ("POST", productive.API + "/time_entries"))
        self.assertEqual(w["body"], {"data": {
            "type": "time_entries",
            "attributes": {"date": DATE, "time": 20, "started_at": "2026-11-04T14:10:00-05:00",
                           "note": "Search keeps every letter"},
            "relationships": {"person": {"data": {"type": "people", "id": "1"}},
                              "service": {"data": {"type": "services", "id": "6"}},
                              "task": {"data": {"type": "tasks", "id": "5"}}}}})
        self.assertEqual((w["headers"]["X-Auth-Token"], w["headers"]["X-Organization-Id"]), ("t", "2"))
        self.assertEqual(len(wire.calls), 1)

    def test_mcp_create(self):
        wire = Wire()
        with connected("claude-code"), mock.patch.object(productive, "https", wire):
            self.assertEqual(productive.create_entry(ENTRY, SETTINGS), {"id": "901"})
        self.assertEqual(wire.tools(), [("create_resource", {
            "resource_type": "time_entries",
            "items": [{"date": DATE, "time": "20 min", "started_at": "2026-11-04T14:10:00-05:00",
                       "note": "Search keeps every letter", "person": "1", "service": "6", "task": "5"}],
            "include_fields": ["date", "time", "started_at"]})])
        self.assertEqual([c["write"] for c in wire.calls], [True])

    CHANGE = {"minutes": 30, "started_at": "2026-11-04T09:55:00-05:00", "note": "New", "task_id": "5", "service_id": "6"}

    def test_rest_update(self):
        wire = Wire()
        with connected("token"), mock.patch.object(productive, "https", wire):
            productive.update_entry("7", self.CHANGE, SETTINGS)
        [w] = wire.writes()
        self.assertEqual((w["method"], w["url"]), ("PATCH", productive.API + "/time_entries/7"))
        self.assertEqual(w["body"], {"data": {
            "type": "time_entries", "id": "7",
            "attributes": {"time": 30, "started_at": "2026-11-04T09:55:00-05:00", "note": "New"},
            "relationships": {"task": {"data": {"type": "tasks", "id": "5"}},
                              "service": {"data": {"type": "services", "id": "6"}}}}})
        wire = Wire()
        with connected("token"), mock.patch.object(productive, "https", wire):  # only what changed
            productive.update_entry("7", {"minutes": 45}, SETTINGS)
        self.assertEqual(wire.writes()[0]["body"], {"data": {"type": "time_entries", "id": "7",
                                                             "attributes": {"time": 45}, "relationships": {}}})

    def test_mcp_update(self):
        wire = Wire()
        with connected("claude-code"), mock.patch.object(productive, "https", wire):
            productive.update_entry("7", self.CHANGE, SETTINGS)
        self.assertEqual(wire.tools(), [
            ("load_resource", {"resource_type": "time_entries", "id": "7",
                               "include_fields": ["time", "started_at", "note", "task", "service", "person"]}),
            ("update_resource", {"resource_type": "time_entries", "items": [
                {"id": "7", "time": "30 min", "started_at": "2026-11-04T09:55:00-05:00", "note": "New", "task": "5",
                 "service": "6"}]})])
        self.assertEqual([c["write"] for c in wire.calls], [False, False, False, True])  # only the update writes
        wire = Wire()
        with connected("claude-code"), mock.patch.object(productive, "https", wire):
            productive.update_entry("7", {"minutes": 45}, SETTINGS)
        self.assertEqual(wire.tools()[-1], ("update_resource", {"resource_type": "time_entries",
                                                                "items": [{"id": "7", "time": "45 min"}]}))

    def test_rest_delete(self):
        wire = Wire()
        with connected("token"), mock.patch.object(productive, "https", wire):
            productive.delete_entry("7", SETTINGS)
        self.assertEqual([(w["method"], w["url"], w["body"]) for w in wire.writes()],
                         [("DELETE", productive.API + "/time_entries/7", None)])

    def test_mcp_delete(self):
        wire = Wire()
        with connected("claude-code"), mock.patch.object(productive, "https", wire):
            productive.delete_entry("7", SETTINGS)
        self.assertEqual(wire.tools(), [("delete_resource", {"resource_type": "time_entries", "items": [{"id": "7"}]})])
        self.assertEqual([c["write"] for c in wire.calls], [True])


class LogBodyTest(unittest.TestCase):
    """POST /api/log down to the wire: the entry the person confirmed is the one sent, on its day, in its zone."""

    def setUp(self):
        self.existing = []
        for p in (mock.patch.dict(os.environ, WRITES_ALLOWED), mock.patch.object(config, "CACHE_DIR", tempfile.mkdtemp()),
                  mock.patch.object(server, "current_settings", lambda: dict(SETTINGS)),
                  mock.patch.object(server.productive, "entries", lambda d0, d1, s: (self.existing, None, "token")),
                  mock.patch.dict(server._just_logged, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    BODY = {"date": DATE, "start": "14:10", "minutes": 20, "taskId": "5", "serviceId": "6",
            "note": "Search keeps every letter"}

    def test_rest(self):
        wire = Wire()
        with connected("token"), mock.patch.object(productive, "https", wire):
            self.assertEqual(server.api_log(dict(self.BODY)), {"created": {"id": "901"}})
        attrs = wire.writes()[0]["body"]["data"]["attributes"]
        rels = wire.writes()[0]["body"]["data"]["relationships"]
        self.assertEqual(attrs, {"date": DATE, "time": 20, "started_at": "2026-11-04T14:10:00-05:00",
                                 "note": "Search keeps every letter"})
        self.assertEqual({k: v["data"]["id"] for k, v in rels.items()}, {"person": "1", "service": "6", "task": "5"})

    def test_mcp(self):
        wire = Wire()
        with connected("claude-code"), mock.patch.object(productive, "https", wire):
            self.assertEqual(server.api_log(dict(self.BODY, start="23:30", minutes=30)), {"created": {"id": "901"}})
        self.assertEqual(wire.tools()[0][1]["items"], [
            {"date": DATE, "time": "30 min", "started_at": "2026-11-04T23:30:00-05:00",  # late evening: still that day
             "note": "Search keeps every letter", "person": "1", "service": "6", "task": "5"}])

    def test_pieces_around_logged_time_send_one_entry_each(self):
        start = server.day_bounds(DATE, config.tz(SETTINGS))[0]
        self.existing = [{"id": "7", "date": DATE, "start": start + 60 * 60, "minutes": 30, "project": "Globex",
                          "task": "Support"}]
        body = dict(self.BODY, start="00:00", minutes=160)
        wire = Wire()
        with connected("claude-code"), mock.patch.object(productive, "https", wire):
            pieces = server.api_log(dict(body))["pieces"]  # the overlap answer: nothing sent
            self.assertEqual(wire.writes(), [])
            self.assertEqual(server.api_log(dict(body, pieces=pieces))["created"], [{"id": "901"}] * 2)
        item = {"date": DATE, "note": "Search keeps every letter", "person": "1", "service": "6", "task": "5"}
        self.assertEqual([t[1]["items"] for t in wire.tools()], [
            [{**item, "time": "60 min", "started_at": "2026-11-04T00:00:00-05:00"}],
            [{**item, "time": "70 min", "started_at": "2026-11-04T01:30:00-05:00"}]])

    def test_a_journalled_row_sends_the_same_entry(self):
        wire = Wire()
        with connected("claude-code"), mock.patch.object(productive, "https", wire):
            server.api_log(dict(self.BODY, run="ld-2026-11-04-0123abcd", row="r1"))
        item = wire.tools()[0][1]["items"][0]
        self.assertEqual((item["date"], item["started_at"], item["time"], item["task"], item["service"]),
                         (DATE, "2026-11-04T14:10:00-05:00", "20 min", "5", "6"))
        [row] = dayplan.journal()["runs"][0]["rows"]
        self.assertEqual((row["status"], row["id"]), ("logged", "901"))


class UndoTest(unittest.TestCase):
    """Undo deletes only what its run wrote, as written, and a delete Productive did not confirm stays undoable."""

    RUN = "ld-2026-11-04-0123abcd"

    def setUp(self):
        self.existing, self.deleted = [], []
        for p in (mock.patch.dict(os.environ, WRITES_ALLOWED), mock.patch.object(config, "CACHE_DIR", tempfile.mkdtemp()),
                  mock.patch.object(server, "current_settings", lambda: dict(SETTINGS)),
                  mock.patch.object(server.productive, "entries", lambda d0, d1, s: (self.existing, None, "token")),
                  mock.patch.object(server.productive, "create_entry", lambda e, s: {"id": "501"}),
                  mock.patch.dict(server._just_logged, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        server.api_log({"date": DATE, "start": "10:00", "minutes": 30, "taskId": "5", "serviceId": "6", "run": self.RUN,
                        "row": "r1"})
        start = server.day_bounds(DATE, config.tz(SETTINGS))[0] + 600 * 60
        self.existing = [{"id": "501", "date": DATE, "start": start, "minutes": 30, "taskId": "5", "serviceId": "6",
                          "project": "P", "personId": "1"}]

    def undoable(self) -> list:
        return [x["id"] for x in dayplan.undo_ids(dayplan.journal(), self.RUN)]

    def test_a_delete_productive_did_not_confirm_stays_undoable(self):
        for failure in (productive.ProductiveUnclear("No clear answer"), productive.ProductiveError("Refused")):
            def delete(i, s, failure=failure):
                raise failure
            with mock.patch.object(server.productive, "delete_entry", delete):
                with self.assertRaises(productive.ProductiveError):
                    server.api_entry_delete({"id": "501", "date": DATE, "run": self.RUN})
            self.assertEqual(self.undoable(), ["501"], failure)
        with mock.patch.object(server.productive, "delete_entry", lambda i, s: self.deleted.append(i)):
            self.assertEqual(server.api_entry_delete({"id": "501", "date": DATE, "run": self.RUN}), {"deleted": "501"})
        self.assertEqual((self.deleted, self.undoable()), (["501"], []))

    def test_an_entry_moved_to_another_task_is_left_alone(self):
        self.existing[0]["taskId"] = "7"  # same start and length, another task: changed by hand since
        with mock.patch.object(server.productive, "delete_entry", lambda i, s: self.deleted.append(i)):
            got = server.api_entry_delete({"id": "501", "date": DATE, "run": self.RUN})
        self.assertEqual(got["left"], "501")  # left alone, said, and no longer offered: Undo goes on with the rest
        self.assertIn("changed after this run", got["why"])
        self.assertEqual((self.deleted, self.undoable()), ([], []))


class PulsarLogBodyTest(unittest.TestCase):
    """pulsar_log through a real Pulsar server, Productive's wire faked: each entry as previewed, in order, and the
    run stops at the first entry that is not logged."""

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
        cls.httpd.server_close()

    def setUp(self):
        self.day = test_mcp.FakeDay()
        for p in self.day.patches() + [mock.patch.dict(os.environ, {
                "PULSAR_MCP_SESSION_ID": "abcdef12", "PULSAR_READ_ONLY": "", "CLAUDE_PROJECT_DIR": "",
                "PULSAR_PREVIEW": "", "PULSAR_MCP_IGNORE_LOGGED": ""}), connected("claude-code")]:
            p.start()
            self.addCleanup(p.stop)
        mcp._pending.clear()
        server._just_logged.clear()
        if os.path.exists(dayplan.journal_path()):
            os.remove(dayplan.journal_path())
        self.wire = Wire()
        wire = mock.patch.object(productive, "https", self.wire)
        wire.start()
        self.addCleanup(wire.stop)

    def call(self, name, args):
        reply = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}},
                           {"client": "claude-code"})
        return json.loads(reply["result"]["content"][0]["text"]), reply["result"]["isError"]

    def item(self, start, minutes, task, note=""):
        return {"date": DATE, "time": f"{minutes} min", "started_at": f"{DATE}T{start}:00+00:00", "note": note,
                "person": "1", "service": "21", "task": task}

    def test_each_entry_is_sent_as_previewed(self):
        dry, err = self.call("pulsar_preview", {"date": DATE, "entries": [
            {"start": "11:15", "minutes": 20, "taskId": "12", "note": "Search fixed."},
            {"start": "09:00", "minutes": 60, "taskId": "11"}]})
        self.assertFalse(err, dry)
        self.assertEqual(self.wire.calls, [])  # a preview sends nothing
        done, err = self.call("pulsar_log", {"code": dry["code"], "summary": dry["summary"]})
        self.assertFalse(err, done)
        self.assertEqual(done["written"], 2)
        self.assertEqual([args["items"] for tool, args in self.wire.tools()],
                         [[self.item("09:00", 60, "11")], [self.item("11:15", 20, "12", "Search fixed.")]])

    def test_the_run_stops_at_the_first_entry_not_logged(self):
        dry, _ = self.call("pulsar_preview", {"date": DATE, "entries": [
            {"start": "08:00", "minutes": 30, "taskId": "11"}, {"start": "11:00", "minutes": 30, "taskId": "11"},
            {"start": "12:00", "minutes": 30, "taskId": "12"}]})
        self.day.entries.append(test_mcp.entry(670, 15))  # someone logs 11:10-11:25 between the preview and the yes
        done, err = self.call("pulsar_log", {"code": dry["code"], "summary": dry["summary"]})
        self.assertFalse(err, done)
        self.assertEqual((done["written"], done["of"], done["stopped"]), (1, 3, True))
        self.assertEqual([r["status"] for r in done["results"]], ["logged", "not logged", "not sent"])
        self.assertIn("overlap", done["results"][1]["why"])
        self.assertEqual([args["items"] for tool, args in self.wire.tools()], [[self.item("08:00", 30, "11")]])


if __name__ == "__main__":
    unittest.main()
