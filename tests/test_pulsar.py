"""Pulsar's logic on made-up data: no network, no trackers, no keychain.

    python3 -m unittest discover -s tests
"""

import os
import stat
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Tests never touch the real settings, caches or error log: point them at a scratch folder before importing.
_SCRATCH = tempfile.mkdtemp(prefix="pulsar-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_SCRATCH, "config")
os.environ["XDG_CACHE_HOME"] = os.path.join(_SCRATCH, "cache")

from pulsar import config, productive, report, server, trackers, updater  # noqa: E402


def item(s, e, app="Cursor", title="Task", host="", cat="work"):
    return {"s": s, "e": e, "app": app, "title": title, "host": host, "cat": cat, "icon": None}


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.patches = [mock.patch.object(config, "CONFIG_DIR", self.dir),
                        mock.patch.object(config, "CACHE_DIR", os.path.join(self.dir, "cache")),
                        mock.patch.object(config, "SETTINGS_FILE", os.path.join(self.dir, "settings.json")),
                        mock.patch.object(config, "TOKEN_FILE", os.path.join(self.dir, "token")),
                        mock.patch.object(config, "_keychain", lambda: False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_settings_round_trip_keeps_only_known_keys(self):
        config.save({**config.DEFAULTS, "target_hours": 7.5, "stray": 1})
        s = config.load()
        self.assertEqual(s["target_hours"], 7.5)
        self.assertNotIn("stray", s)

    def write(self, text):
        with open(config.SETTINGS_FILE, "w", encoding="utf-8") as fh:
            fh.write(text)

    def copies(self):
        return [n for n in os.listdir(self.dir) if n.startswith("settings.broken-")]

    def test_a_file_that_is_not_settings_gives_defaults_and_is_kept(self):
        for text in ("[]", "null", "{not json", '"text"'):
            self.write(text)
            self.assertEqual(config.load(), config.DEFAULTS, text)
        self.assertEqual(len(self.copies()), 4)
        self.write("[]"); config.load()
        self.assertEqual(len(self.copies()), 4)  # one copy per content, not one per request

    def test_values_of_the_wrong_kind_fall_back_one_by_one(self):
        import json
        good = {"match": "kb-dev", "taskId": "123", "title": "QA", "project": "P"}
        self.write(json.dumps({"timezone": 5, "personal_patterns": "YouTube", "target_hours": True, "likely": "maybe",
                               "update_asked": "yesterday", "person_id": "42", "rules": [good, {"match": "x"}, "y"]}))
        s = config.load()
        self.assertEqual(s["timezone"], "")
        self.assertEqual(s["personal_patterns"], config.DEFAULTS["personal_patterns"])
        self.assertIsNone(s["target_hours"])
        self.assertEqual(s["likely"], "auto")
        self.assertEqual(s["update_asked"], 0)
        self.assertEqual(s["person_id"], "42")  # the good values stay
        self.assertEqual(s["rules"], [good])     # and so do the good rules
        self.assertEqual(len(self.copies()), 1)

    def test_two_saves_at_once_both_land(self):
        import threading
        config.save(config.DEFAULTS)
        real = config.write_atomic
        def slow(path, text):
            time.sleep(0.05); real(path, text)
        with mock.patch.object(config, "write_atomic", slow):
            a = threading.Thread(target=config.update, args=({"org_id": "1"},))
            b = threading.Thread(target=config.update, args=({"person_id": "2"},))
            a.start(); b.start(); a.join(); b.join()
        s = config.load()
        self.assertEqual((s["org_id"], s["person_id"]), ("1", "2"))

    def test_target_comes_from_setting_then_organization_then_eight(self):
        self.assertEqual(config.target_minutes({"target_hours": 6}), 360)
        self.assertEqual(config.target_minutes({"target_hours": None, "person_day_hours": 7.5}), 450)
        self.assertEqual(config.target_minutes({}), 480)

    def test_timezone_from_tz_variable_and_a_bad_choice_falls_back_to_the_system(self):
        with mock.patch.dict(os.environ, {"TZ": "America/Toronto"}):
            self.assertEqual(config.system_timezone(), "America/Toronto")
            self.assertEqual(config.timezone_name({"timezone": "Mars/Base"}), "America/Toronto")
            self.assertEqual(config.timezone_name({"timezone": "Europe/Paris"}), "Europe/Paris")

    def test_token_file_is_private_and_bad_tokens_are_refused(self):
        config.set_token("abc123-DEF_456")
        self.assertEqual(config.get_token(), "abc123-DEF_456")
        self.assertEqual(stat.S_IMODE(os.stat(config.TOKEN_FILE).st_mode), 0o600)
        with self.assertRaises(ValueError):
            config.set_token('bad" token; rm -rf')
        config.delete_token()
        self.assertIsNone(config.get_token())


class TrackersTest(unittest.TestCase):
    def test_activitywatch_with_a_watcher_missing_says_which(self):
        cases = ((["aw-watcher-afk_mac"], "window watcher"), (["aw-watcher-window_mac"], "AFK watcher"))
        for buckets, words in cases:
            trackers.PROBLEMS.clear()
            with mock.patch.object(trackers, "http_json", lambda url, timeout=0: {b: {} for b in buckets}):
                self.assertEqual(trackers.activity_aw("http://localhost:5600", 0, 3600, trackers.classifier([])), [])
            self.assertIn(words, trackers.PROBLEMS.get("aw", ""))

    def test_an_afk_watcher_that_recorded_nothing_is_named_but_a_quiet_day_is_not(self):
        def answer(afk):
            def http_json(url, timeout=0):
                if url.endswith("/buckets/"):
                    return {"aw-watcher-window_mac": {}, "aw-watcher-afk_mac": {}}
                if "window" in url:
                    return [{"timestamp": "1970-01-01T00:10:00Z", "duration": 600, "data": {"app": "Cursor", "title": "x"}}]
                return afk
            return http_json
        for afk, warned in (([], True), ([{"timestamp": "1970-01-01T00:00:00Z", "duration": 3600, "data": {"status": "afk"}}], False)):
            trackers.PROBLEMS.clear()
            with mock.patch.object(trackers, "http_json", answer(afk)):
                self.assertEqual(trackers.activity_aw("http://localhost:5600", 0, 3600, trackers.classifier([])), [])
            self.assertEqual("AFK watcher recorded nothing" in trackers.PROBLEMS.get("aw", ""), warned, afk)

    def test_personal_words_match_app_title_or_site_in_any_case(self):
        classify = trackers.classifier(["telegram", "YouTube"])
        self.assertEqual(classify("Telegram", "Chat", ""), "personal")
        self.assertEqual(classify("Google Chrome", "x", "www.youtube.com"), "personal")
        self.assertEqual(classify("Cursor", "Fix tests", ""), "work")
        self.assertEqual(trackers.classifier([])("Telegram", "", ""), "work")

    def test_editor_title_keeps_the_session_tab(self):
        self.assertEqual(trackers.clean_title("Cursor", "● Fix the build — repo (Workspace)"), "Fix the build")
        self.assertEqual(trackers.clean_title("Slack", "a — b"), "a — b")

    def test_touching_pieces_of_one_window_are_joined(self):
        out = trackers.compact([item(0, 1), item(1.01, 2), item(2, 3, title="Other")])
        self.assertEqual([(x["s"], x["e"], x["title"]) for x in out], [(0, 2, "Task"), (2, 3, "Other")])

    def test_window_time_counts_only_while_at_the_keys(self):
        t0 = datetime(2026, 11, 4, tzinfo=timezone.utc).timestamp()
        events = [{"timestamp": "2026-11-04T10:00:00+00:00", "duration": 3600, "data": {"app": "Cursor", "title": "A"}}]
        present = [[t0 + 10 * 3600 + 600, t0 + 10 * 3600 + 1800]]  # 10:10-10:30 at the keys
        with mock.patch.object(trackers, "program_names", lambda: {}):
            out = trackers.clip_to_present(events, present, t0, t0 + 86400, trackers.classifier([]))
        self.assertEqual([(x["s"], x["e"]) for x in out], [(610.0, 630.0)])

    def test_auto_picks_the_tracker_that_saw_more(self):
        self.assertEqual(trackers.pick({"aw": [item(0, 10)], "memtime": [item(0, 30)]}, "auto"), "memtime")
        self.assertEqual(trackers.pick({"aw": [item(0, 10)], "memtime": [item(0, 30)]}, "aw"), "aw")


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp()  # a Monday
        self.dates = ["2026-11-02", "2026-11-03"]
        self.bounds = [(self.t0, self.t0 + 86400), (self.t0 + 86400, self.t0 + 172800)]

    def test_days_split_at_midnight_and_unlogged_minutes(self):
        items = [item(600, 660), item(1430, 1450, app="Slack", title="", host=""), item(700, 720, app="Telegram", cat="personal")]
        entries = [{"date": "2026-11-02", "start": self.t0 + 600 * 60, "minutes": 30, "approved": True, "project": "P"}]
        r = report.build(self.dates, self.bounds, {"aw": items}, entries, "2026-11-03")
        d1, d2 = r["days"]
        self.assertEqual((d1["active"], d1["personal"], d1["logged"], d1["approved"]), (70, 20, 30, 30))
        self.assertEqual(d1["notLogged"], 40)  # 10:30-11:00 and 23:50-24:00
        self.assertEqual((d2["active"], d2["notLogged"]), (10, 10))
        self.assertEqual(r["hours"][0][10], 60)  # Monday 10:00-11:00
        self.assertEqual(r["hours"][1][0], 10)   # Tuesday 00:00-00:10
        self.assertEqual([a["name"] for a in r["apps"]], ["Cursor", "Slack"])

    def test_each_day_uses_the_tracker_that_saw_more_of_it(self):
        r = report.build(self.dates, self.bounds, {"aw": [item(600, 610)], "memtime": [item(600, 660), item(1500, 1505)]},
                         [], "2026-11-03")
        self.assertEqual([d["source"] for d in r["days"]], ["memtime", "memtime"])
        r = report.build(self.dates, self.bounds, {"aw": [item(600, 610), item(1500, 1600)], "memtime": [item(600, 660)]},
                         [], "2026-11-03")
        self.assertEqual([d["source"] for d in r["days"]], ["memtime", "aw"])


def jsonapi(rows, included=(), pages=1):
    return {"data": rows, "included": list(included), "meta": {"total_pages": pages}}


def entry(i, date, minutes, task="1", service="2", approved_at="2026-11-02T10:00:00Z"):
    return {"id": i, "type": "time_entries",
            "attributes": {"date": date, "time": minutes, "note": "<p>Did &amp; done</p>",
                           "started_at": f"{date}T09:00:00.000+00:00", "approved_at": approved_at},
            "relationships": {"task": {"data": {"type": "tasks", "id": task}},
                              "service": {"data": {"type": "services", "id": service}}}}


INCLUDED = [
    {"type": "tasks", "id": "1", "attributes": {"title": "Build it"}, "relationships": {"project": {"data": {"type": "projects", "id": "9"}}}},
    {"type": "projects", "id": "9", "attributes": {"name": "Client site"}},
    {"type": "services", "id": "2", "attributes": {"name": "Development"}, "relationships": {"deal": {"data": {"type": "deals", "id": "7"}}}},
    {"type": "deals", "id": "7", "attributes": {"name": "2026 retainer"}},
]


class ProductiveTest(unittest.TestCase):
    def names(self):
        return productive.Names(os.path.join(tempfile.mkdtemp(), "names.json"))

    def test_rest_rows_get_names_from_the_included_records_and_stay_in_range(self):
        calls = []

        def get(path, query, token, org):
            calls.append((path, dict(query)))
            return 200, jsonapi([entry("1", "2026-11-01", 15), entry("2", "2026-11-02", 45)], INCLUDED)

        rows = productive.rest_entries("2026-11-02", "2026-11-02", "t", "12345", "42", self.names(), get)
        self.assertEqual(len(rows), 1)  # the day before, asked for in case `after` is exclusive, is dropped
        r = rows[0]
        self.assertEqual((r["minutes"], r["project"], r["budget"], r["task"], r["service"], r["approved"]),
                         (45, "Client site", "2026 retainer", "Build it", "Development", True))
        self.assertEqual(r["note"], "Did & done")
        self.assertEqual(calls[0][1]["filter[after]"], "2026-11-01")
        self.assertEqual(calls[0][1]["filter[before]"], "2026-11-03")

    def test_rest_falls_back_to_a_plain_include_and_looks_names_up_once(self):
        seen = []

        def get(path, query, token, org):
            seen.append((path, query.get("include")))
            if path == "/time_entries" and query["include"] != "task,service":
                return 400, None
            if path == "/time_entries":
                return 200, jsonapi([entry("2", "2026-11-02", 45)], [INCLUDED[0], INCLUDED[2]])
            if path == "/tasks/1":
                return 200, {"data": INCLUDED[0], "included": [INCLUDED[1]]}
            if path == "/services/2":
                return 200, {"data": INCLUDED[2], "included": [INCLUDED[3]]}
            return 404, None

        names = self.names()
        rows = productive.rest_entries("2026-11-02", "2026-11-02", "t", "1", "42", names, get)
        self.assertEqual((rows[0]["project"], rows[0]["budget"]), ("Client site", "2026 retainer"))
        self.assertEqual(names.get("tasks:1"), "Client site")
        seen.clear()
        productive.rest_entries("2026-11-02", "2026-11-02", "t", "1", "42", names, get)
        self.assertNotIn(("/tasks/1", "project"), seen)  # remembered

    def test_rest_needs_organization_and_person(self):
        with self.assertRaises(productive.ProductiveError):
            productive.rest_entries("2026-11-02", "2026-11-02", "t", "", "", self.names(), lambda *a: (200, {}))

    def test_rest_detect_reads_the_membership(self):
        doc = {"data": [{"type": "organization_memberships", "id": "5",
                         "relationships": {"organization": {"data": {"type": "organizations", "id": "12345"}},
                                           "person": {"data": {"type": "people", "id": "42"}}}}],
               "included": [{"type": "organizations", "id": "12345", "attributes": {"name": "Acme"}},
                            {"type": "people", "id": "42", "attributes": {"first_name": "Ada", "last_name": "Byron"}}]}
        found = productive.rest_detect("t", get=lambda *a: (200, doc))
        self.assertEqual(found, {"org_id": "12345", "org_name": "Acme", "person_id": "42", "person_name": "Ada Byron"})

    def test_mcp_answers(self):
        self.assertEqual(productive.parse_mcp("Loaded 0 time entries.")["items"]["rows"], [])
        self.assertEqual(productive.parse_mcp('Loaded 1\n```{"items": {"columns": ["id"], "rows": [["1"]]}}```')
                         ["items"]["rows"], [["1"]])
        with self.assertRaises(productive.ProductiveError):
            productive.parse_mcp("Invalid filter")
        one = productive.parse_mcp('Loaded 1 of 1\n```{"items":[{"type":"time_entries","id":"7","time":"5 min",'
                                   '"service":{"type":"services","id":"2"}}],"pagination":{"total":1}}```')
        row = dict(zip(one["items"]["columns"], one["items"]["rows"][0]))
        self.assertEqual((one["items"]["rows"][0][:2], row["time"], row["service"]), (["time_entries", "7"], "5 min", "services:2"))
        self.assertEqual(productive.minutes_of("-90 min"), -90)
        self.assertEqual(productive.minutes_of("1.5 h"), 90)
        self.assertEqual(productive.minutes_of("3 h 30 min"), 210)


class ReadRetryTest(unittest.TestCase):
    def conn_class(self, outcomes):
        calls = []
        class Conn:
            def __init__(self, *a, **k):
                self.sock = None
            def request(self, *a, **k):
                calls.append(1)
                result = outcomes[len(calls) - 1]
                if isinstance(result, Exception):
                    raise result
            def getresponse(self):
                r = mock.Mock(status=200, will_close=True)
                r.read.return_value = b"ok"
                return r
            def close(self):
                pass
        return Conn, calls

    def test_a_read_that_the_server_hangs_up_on_is_tried_again_twice_at_most(self):
        import http.client
        hang = http.client.RemoteDisconnected("closed")
        with mock.patch.object(productive.time, "sleep", lambda s: None):
            Conn, calls = self.conn_class([hang, hang, None])
            with mock.patch.object(productive.http.client, "HTTPSConnection", Conn):
                self.assertEqual(productive.https("GET", "https://example.invalid/x"), (200, b"ok"))
            self.assertEqual(len(calls), 3)
            Conn, calls = self.conn_class([hang, hang, hang, None])
            with mock.patch.object(productive.http.client, "HTTPSConnection", Conn):
                with self.assertRaises(http.client.RemoteDisconnected):
                    productive.https("GET", "https://example.invalid/x")
            self.assertEqual(len(calls), 3)

    def test_three_stale_kept_alive_connections_do_not_exhaust_the_tries(self):
        import http.client
        class Dead:
            sock = None
            def request(self, *a, **k):
                raise http.client.RemoteDisconnected("stale")
            def close(self):
                pass
        Conn, calls = self.conn_class([None])
        with mock.patch.dict(productive._pool, {"example.invalid": [Dead(), Dead(), Dead()]}, clear=True), \
             mock.patch.object(productive.http.client, "HTTPSConnection", Conn):
            self.assertEqual(productive.https("GET", "https://example.invalid/x"), (200, b"ok"))
            self.assertEqual(productive._pool.get("example.invalid", []), [])  # the stale ones were dropped
        self.assertEqual(len(calls), 1)

    def test_a_write_and_a_timeout_are_never_tried_twice(self):
        import http.client, socket
        for outcome, write in ((http.client.RemoteDisconnected("closed"), True), (socket.timeout("slow"), False)):
            Conn, calls = self.conn_class([outcome, None])
            with mock.patch.object(productive.http.client, "HTTPSConnection", Conn):
                with self.assertRaises(Exception):
                    productive.https("POST", "https://example.invalid/x", b"{}", write=write)
            self.assertEqual(len(calls), 1)

    def test_estimates_without_a_connection_are_a_message_not_an_error(self):
        from pulsar import logbook
        seen = []
        def expired(*a):
            raise productive.ProductiveError("Claude Code's Productive token expired at 19:50.")
        with mock.patch.object(productive, "task_details", expired), \
             mock.patch.object(logbook, "record", lambda *a, **k: seen.append(a)):
            got = server.api_estimates({"ids": "123"})
        self.assertEqual(got["tasks"], {})
        self.assertIn("expired", got["error"])
        self.assertEqual(seen, [])


class NamesTest(unittest.TestCase):
    def test_each_minor_version_has_its_pulsar_and_nothing_breaks_past_the_list(self):
        from pulsar import names
        self.assertEqual(names.codename("1.0.4")[0], "CP 1919")
        self.assertEqual(names.codename("1.1.0")[0], "Vela")
        self.assertEqual(names.codename("9.9.9"), ("", ""))
        self.assertEqual(names.codename("x"), ("", ""))


class ReasonTest(unittest.TestCase):
    def test_a_request_without_an_answer_says_why(self):
        import socket
        for exc, words in ((socket.gaierror(8, "nodename"), "looked up"), (socket.timeout("slow"), "too long"),
                           (ConnectionResetError(), "closed the connection")):
            with mock.patch.object(productive, "https", mock.Mock(side_effect=exc)):
                status, why = productive.rest_get("/time_entries", {}, "t", "1")
            self.assertEqual(status, 0)
            self.assertIn(words, productive.why(status, why))
        self.assertIn("limiting", productive.why(429, None))

    def test_a_refused_token_at_first_setup_says_so_not_org_id(self):
        with self.assertRaises(productive.ProductiveError) as caught:
            productive.rest_detect("t", get=lambda *a: (401, None))
        self.assertIn("refused the token", str(caught.exception))

    def test_a_working_day_with_minutes_keeps_them(self):
        for day, hours in (("7 h 30 min", 7.5), ("8 h", 8.0), ("", None)):
            answer = ('```json\n{"id": "1", "firstName": "A"}\n```\n```json\n{"id": "2", "person_day": "%s"}\n```' % day)
            with mock.patch.object(productive, "mcp_text", lambda *a: answer):
                self.assertEqual(productive.mcp_detect("t")["person_day_hours"], hours, day)


class ClaudeCodeTokenTest(unittest.TestCase):
    """An expired Claude Code token is renewed by Claude Code itself (claude mcp get), then read again."""

    def keychain(self, expires_in_min):
        import json
        item = {"mcpOAuth": {"productive|abc": {"serverName": "productive", "serverUrl": "https://mcp.productive.io/mcp",
                                                "accessToken": "tok%d" % expires_in_min, "refreshToken": "never-used",
                                                "expiresAt": int((time.time() + expires_in_min * 60) * 1000)}}}
        return mock.Mock(stdout=json.dumps(item), returncode=0)

    def token_with(self, keychains, claude_status="✔ Connected"):
        calls = []
        def fake_run(args, **kw):
            calls.append(args)
            if args[0] == "/usr/bin/security":
                return keychains.pop(0) if len(keychains) > 1 else keychains[0]
            return mock.Mock(stdout=f"productive:\n  Status: {claude_status}\n", returncode=0)
        productive._cc_cache[:] = [0.0, None]
        productive._renewed_at[0] = 0.0
        with mock.patch.object(productive.subprocess, "run", fake_run), mock.patch.object(productive.sys, "platform", "darwin"), \
             mock.patch.object(productive, "_claude_binary", lambda: "/bin/claude"):
            got = productive.claude_code_token()
        return got, calls

    def test_an_expired_token_is_renewed_by_claude_code_and_read_again(self):
        got, calls = self.token_with([self.keychain(-5), self.keychain(-5), self.keychain(60)])
        self.assertEqual(got[0], "tok60")
        self.assertIn(["/bin/claude", "mcp", "get", "productive"], calls)
        self.assertFalse(any("never-used" in " ".join(map(str, c)) for c in calls))  # the refresh token is not Pulsar's

    def test_when_claude_code_cannot_renew_it_says_what_to_do(self):
        got, calls = self.token_with([self.keychain(-5)], claude_status="✘ Needs authentication")
        self.assertIsNone(got[0])
        self.assertIn("/mcp", got[1])

    def test_a_valid_token_never_runs_claude(self):
        got, calls = self.token_with([self.keychain(30)])
        self.assertEqual(got[0], "tok30")
        self.assertEqual([c[0] for c in calls], ["/usr/bin/security"])

    def test_renewal_is_tried_at_most_once_in_five_minutes(self):
        self.token_with([self.keychain(-5)], claude_status="✘ Failed")
        productive._cc_cache[:] = [0.0, None]
        with mock.patch.object(productive.subprocess, "run", lambda args, **kw: self.keychain(-5)), \
             mock.patch.object(productive.sys, "platform", "darwin"), \
             mock.patch.object(productive, "_claude_binary", lambda: self.fail("ran claude again")):
            self.assertIsNone(productive.claude_code_token()[0])


class CreateEntryTest(unittest.TestCase):
    def test_the_created_id_is_read_from_the_text_answer(self):
        with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": ""}), \
             mock.patch.object(productive, "connection", lambda s: {"kind": "claude-code", "token": "t"}), \
             mock.patch.object(productive, "mcp_text", lambda *a: "time entry created (ID: 12345678)."):
            got = productive.create_entry({"date": "2026-11-04", "minutes": 10, "started_at": "2026-11-04T14:00:00+00:00",
                                           "task_id": "1", "service_id": "2"}, {"person_id": "3"})
        self.assertEqual(got, {"id": "12345678"})

    def test_read_only_stops_every_write_not_only_logging(self):
        with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": "1"}), \
             mock.patch.object(productive.http.client, "HTTPSConnection", lambda *a, **k: self.fail("connected")):
            with self.assertRaises(productive.ProductiveError):
                productive.mcp_text("delete_resource", {}, "t")

    def test_no_person_id_means_nothing_is_written(self):
        with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": ""}), \
             mock.patch.object(productive, "connection", lambda s: {"kind": "claude-code", "token": "t"}), \
             mock.patch.object(productive, "mcp_text", lambda *a: self.fail("wrote")):
            with self.assertRaises(productive.ProductiveError):
                productive.create_entry({"date": "2026-11-04", "minutes": 10, "started_at": "2026-11-04T14:00:00+00:00",
                                         "task_id": "1", "service_id": "2"}, {"person_id": ""})

    def test_an_error_that_mentions_an_id_is_not_a_new_entry(self):
        with mock.patch.dict(os.environ, {"PULSAR_READ_ONLY": ""}), \
             mock.patch.object(productive, "connection", lambda s: {"kind": "claude-code", "token": "t"}), \
             mock.patch.object(productive, "mcp_text", lambda *a: "Error: service ID: 987 is not bookable"):
            with self.assertRaises(productive.ProductiveError):
                productive.create_entry({"date": "2026-11-04", "minutes": 10, "started_at": "2026-11-04T14:00:00+00:00",
                                         "task_id": "1", "service_id": "2"}, {"person_id": "3"})

    def test_an_mcp_answer_marked_as_error_raises(self):
        import io
        answer = 'data: {"jsonrpc":"2.0","id":1,"result":{"isError":true,"content":[{"type":"text","text":"Error: service ID: 987"}]}}'
        with mock.patch.object(productive, "https", lambda *a, **k: (200, answer.encode())):
            with self.assertRaises(productive.ProductiveError):
                productive.mcp_text("create_resource", {}, "t")


class LogTest(unittest.TestCase):
    """Writing time: the overlap guard and the dry run, with Productive replaced by fakes."""

    def setUp(self):
        start = datetime(2026, 11, 4, 14, 0, tzinfo=timezone.utc).timestamp()
        self.existing = [{"start": start, "minutes": 30, "project": "P", "task": "T"}]
        self.created = []
        self.patches = [
            mock.patch.object(server, "current_settings", lambda: {"timezone": "UTC", "person_id": "1"}),
            mock.patch.object(server.config, "tz", lambda s: timezone.utc),
            mock.patch.object(server.productive, "entries", lambda d0, d1, s: (self.existing, None, "token")),
            mock.patch.object(server.productive, "create_entry", lambda e, s: self.created.append(e) or {"id": "9"}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def body(self, **kw):
        return {"date": "2026-11-04", "start": "14:10", "minutes": 20, "taskId": "5", "serviceId": "6", **kw}

    def test_an_overlap_is_shown_and_nothing_is_written(self):
        r = server.api_log(self.body())
        self.assertEqual([o["start"] for o in r["overlap"]], ["14:00"])
        self.assertEqual(self.created, [])

    def test_a_dry_run_never_writes_even_when_forced(self):
        r = server.api_log(self.body(dry=True, force=True))
        self.assertTrue(r["dry"])
        self.assertEqual(r["wouldWrite"]["started_at"], "2026-11-04T14:10:00+00:00")
        self.assertEqual(self.created, [])

    def test_a_free_slot_or_a_confirmed_overlap_is_written_once(self):
        self.assertEqual(server.api_log(self.body(start="15:00"))["created"], {"id": "9"})
        server.api_log(self.body(force=True))
        self.assertEqual(len(self.created), 2)

    def test_incomplete_requests_are_refused(self):
        for bad in (self.body(taskId=""), self.body(minutes=0), self.body(start="25:99x"), self.body(start="23:60"),
                    self.body(minutes=1.5), self.body(minutes=True), self.body(minutes="30"), self.body(taskId="²"),
                    self.body(date="٢٠٢٦-11-04"), self.body(start="23:50", minutes=20)):
            with self.assertRaises(ValueError):
                server.api_log(bad)


def release_zip(version, files=None):
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in (files or {"pulsar.py": "print('hi')", "pulsar/__init__.py": f'__version__ = "{version}"'}).items():
            zf.writestr(f"pulsar-{version}/{name}", text)
    return buf.getvalue()


def openssl_key(folder, name):
    import subprocess
    key, pub = os.path.join(folder, name + ".pem"), os.path.join(folder, name + ".pub.pem")
    subprocess.run(["/usr/bin/openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", key], check=True, capture_output=True)
    subprocess.run(["/usr/bin/openssl", "ec", "-in", key, "-pubout", "-out", pub], check=True, capture_output=True)
    return key, pub


def sign(key, data):
    import subprocess
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "m")
    with open(path, "wb") as fh:
        fh.write(data)
    return subprocess.run(["/usr/bin/openssl", "dgst", "-sha256", "-sign", key, path], check=True, capture_output=True).stdout


URL = "https://github.com/fosterinteractive/pulsar/releases/download/v1.1.0/pulsar-1.1.0.zip"


@unittest.skipUnless(os.path.exists("/usr/bin/openssl"), "needs openssl")
class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "versions", "1.0.0", "pulsar"))
        updater.link("current", "versions/1.0.0", self.root)
        self.key, self.pub = openssl_key(self.root, "release")
        with open(self.pub, encoding="utf-8") as fh:
            self.pub_pem = fh.read()

    def make(self, manifest, zip_bytes=b"", passes=True, signed_by=None, signature=None):
        u = updater.Updater(root=self.root)
        raw = json_bytes(manifest)
        sig = signature if signature is not None else sign(signed_by or self.key, raw)

        def fetch(url, limit, timeout=30):
            return raw if url == u.manifest_url else sig if url == u.manifest_url + ".sig" else zip_bytes
        self.patches = [mock.patch.object(updater, "fetch", fetch), mock.patch.object(updater, "installed", lambda root=None: True),
                        mock.patch.object(updater, "public_keys", lambda: (self.pub_pem,)),
                        mock.patch.object(updater, "self_test", lambda copy: passes),
                        mock.patch.object(updater, "__version__", "1.0.0")]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        return u

    def release(self, **kw):
        import hashlib
        data = release_zip("1.1.0")
        return {"version": "1.1.0", "url": URL, "sha256": hashlib.sha256(data).hexdigest(), **kw}, data

    def test_a_missing_release_on_an_installed_copy_is_logged_once(self):
        import urllib.error
        from pulsar import logbook
        def gone(*a, **k):
            raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        seen = []
        with mock.patch.object(updater, "fetch", gone), mock.patch.object(updater, "installed", lambda root=None: True), \
             mock.patch.object(logbook, "record", lambda *a, **k: seen.append(k.get("kind"))):
            u = updater.Updater(manifest_url="https://example.invalid/latest.json")
            self.assertEqual(u.check(install=True)["status"], "latest")
        self.assertEqual(seen, ["warning"])

    def test_every_listed_release_key_loads_in_the_macs_openssl(self):
        import subprocess
        for key in updater.PUBLIC_KEYS:
            run = subprocess.run(["/usr/bin/openssl", "pkey", "-pubin", "-noout"], input=key.encode(), capture_output=True)
            self.assertEqual(run.returncode, 0, key)

    def test_a_check_never_downloads_and_counts_the_releases_behind(self):
        manifest, data = self.release(versions=["1.0.1", "1.0.2", "1.0.3", "1.0.4", "1.1.0", "0.9.0", "../x"])
        u = self.make(manifest, data)
        with mock.patch.object(updater, "unpack", lambda *a: self.fail("downloaded without a click")):
            state = u.check()
        self.assertEqual((state["status"], state["behind"]), ("available", 5))
        self.assertIn("5 releases behind", state["message"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "versions", "1.1.0")))

    def test_without_a_list_of_versions_a_newer_release_counts_as_one(self):
        manifest, data = self.release()
        self.assertEqual(self.make(manifest, data).check()["behind"], 1)

    def test_the_update_button_downloads_tests_and_restarts(self):
        manifest, data = self.release()
        u = self.make(manifest, data)
        with mock.patch.object(updater.threading, "Timer") as timer:
            self.assertEqual(u.install_now()["status"], "ready")
        self.assertEqual(timer.call_args[0][1], u.apply)  # the restart into it is scheduled

    def test_versions_compare_as_numbers(self):
        self.assertGreater(updater.vtuple("1.10.0"), updater.vtuple("1.9.3"))
        self.assertEqual(updater.vtuple("2"), (2, 0, 0))

    def test_a_signed_newer_release_is_checked_unpacked_and_made_ready(self):
        manifest, data = self.release()
        u = self.make(manifest, data)
        self.assertEqual(u.check(install=True)["status"], "ready")
        self.assertTrue(os.path.exists(os.path.join(self.root, "versions", "1.1.0", "pulsar.py")))
        with mock.patch.object(updater.os, "execv") as execv:
            u.apply()
        self.assertEqual(os.readlink(os.path.join(self.root, "current")), "versions/1.1.0")
        self.assertEqual(os.readlink(os.path.join(self.root, "previous")), "versions/1.0.0")
        self.assertEqual(execv.call_args[0][1][1:4], ["-E", "-s", "-B"])  # no PYTHON* variables, no user packages
        self.assertTrue(execv.call_args[0][1][4].endswith("versions/1.1.0/pulsar.py"))

    def test_a_release_signed_with_the_reserve_key_is_accepted(self):
        reserve_key, reserve_pub = openssl_key(self.root, "reserve")
        manifest, data = self.release()
        u = self.make(manifest, data, signed_by=reserve_key)
        with open(reserve_pub, encoding="utf-8") as fh:
            keys = (self.pub_pem, fh.read())
        with mock.patch.object(updater, "public_keys", lambda: keys):
            self.assertEqual(u.check(install=True)["status"], "ready")

    def test_a_release_without_a_valid_signature_is_never_installed(self):
        other_key, _ = openssl_key(self.root, "someone-else")
        for kw in ({"signature": b""}, {"signature": b"garbage"}, {"signed_by": other_key}):
            manifest, data = self.release()
            u = self.make(manifest, data, **kw)
            state = u.check(install=True)
            self.assertEqual(state["status"], "failed", kw)
            self.assertIn("not signed", state["message"])
            self.assertFalse(os.path.exists(os.path.join(self.root, "versions", "1.1.0")))
            for p in self.patches:
                p.stop()

    def test_a_download_outside_the_github_release_is_refused(self):
        manifest, data = self.release(url="https://evil.example/pulsar-1.1.0.zip")
        self.assertEqual(self.make(manifest, data).check(install=True)["status"], "failed")

    def test_odd_versions_are_refused_before_anything_is_touched(self):
        for version in ("../../x", "/tmp/x", "1.1", "1.1.0-beta"):
            u = self.make({"version": version, "url": URL, "sha256": "00"})
            self.assertEqual(u.check(install=True)["status"], "failed", version)
            for p in self.patches:
                p.stop()
        with self.assertRaises(ValueError):
            updater.unpack(release_zip("1.1.0"), "../escape", self.root)

    def test_a_wrong_checksum_changes_nothing(self):
        manifest, data = self.release(sha256="00")
        self.assertEqual(self.make(manifest, data).check(install=True)["status"], "failed")
        self.assertFalse(os.path.exists(os.path.join(self.root, "versions", "1.1.0")))

    def test_a_release_that_fails_its_self_test_is_marked_bad_and_skipped(self):
        manifest, data = self.release()
        u = self.make(manifest, data, passes=False)
        self.assertEqual(u.check(install=True)["status"], "failed")
        self.assertIn("1.1.0", updater.bad_versions(self.root))
        self.assertEqual(u.check(install=True)["status"], "skipped")

    def test_files_outside_the_release_folder_are_refused(self):
        with self.assertRaises(ValueError):
            updater.unpack(release_zip("1.1.0", {"../evil.py": "x"}), "1.1.0", self.root)

    def test_test_switches_are_ignored_by_an_installed_copy(self):
        with mock.patch.object(updater, "installed", lambda root=None: True), \
             mock.patch.dict(os.environ, {"PULSAR_UPDATE_URL": "http://evil.example/latest.json", "PULSAR_UPDATE_PUBKEY": self.pub}):
            self.assertEqual(updater.Updater(root=self.root).manifest_url, updater.DEFAULT_MANIFEST)
            self.assertEqual(updater.public_keys(), updater.PUBLIC_KEYS)

    def test_a_ready_update_stays_ready_on_the_next_check_and_odd_manifests_fail_cleanly(self):
        manifest, data = self.release()
        u = self.make(manifest, data)
        self.assertEqual(u.check(install=True)["status"], "ready")
        self.assertEqual(u.check(install=False)["status"], "ready")
        for p in self.patches:
            p.stop()
        for bad in ([], None, "1.2.0"):
            raw = json_bytes(bad)
            sig = sign(self.key, raw)
            with mock.patch.object(updater, "fetch", lambda url, limit, timeout=30, raw=raw, sig=sig: sig if url.endswith(".sig") else raw), \
                 mock.patch.object(updater, "public_keys", lambda: (self.pub_pem,)):
                self.assertEqual(updater.Updater(root=self.root).check()["status"], "failed", bad)

    def test_a_self_test_that_hangs_counts_as_failed(self):
        import subprocess
        with mock.patch.object(updater.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 60)):
            self.assertFalse(updater.self_test(self.root))

    def test_the_same_version_is_not_an_update(self):
        self.assertEqual(self.make({"version": "1.0.0", "url": URL, "sha256": "x"}).check()["status"], "latest")


def json_bytes(obj):
    import json
    return json.dumps(obj).encode()


class SettingsTokenTest(unittest.TestCase):
    """A new token may be someone else's: the person found for the old one must not survive it."""

    def run_settings(self, body, before):
        saved = {}
        with mock.patch.object(server.config, "load", lambda: dict(before)), \
             mock.patch.object(server.config, "update", lambda ch: saved.update(ch) or {**before, **ch}), \
             mock.patch.object(server.config, "set_token", lambda t: None), \
             mock.patch.object(server, "current_settings", lambda: {**before, **saved}), \
             mock.patch.object(server, "public_config", lambda s: {}), \
             mock.patch.object(server, "test_productive", lambda s: {"ok": True}):
            server.api_settings(body)
        return saved

    BEFORE = {**config.DEFAULTS, "person_id": "42", "org_id": "12345", "person_name": "Ada Byron"}

    def test_a_new_token_forgets_the_person_the_page_sent_back(self):
        saved = self.run_settings({"token": "new-token-123", "person_id": "42", "org_id": "12345"}, self.BEFORE)
        self.assertEqual((saved["person_id"], saved["org_id"], saved["person_name"]), ("", "", ""))

    def test_an_id_typed_with_the_new_token_is_kept(self):
        saved = self.run_settings({"token": "new-token-123", "person_id": "77", "org_id": "12345"}, self.BEFORE)
        self.assertEqual((saved["person_id"], saved["org_id"]), ("77", ""))

    def test_other_settings_keep_the_person(self):
        saved = self.run_settings({"auto_update": False, "person_id": "42"}, self.BEFORE)
        self.assertNotIn("person_name", saved)


class ClockChangeTest(unittest.TestCase):
    """On the days the clocks change, times are placed by the clock, not by elapsed minutes."""

    def test_wall_clock_minutes_on_the_autumn_and_spring_changes(self):
        from zoneinfo import ZoneInfo
        kyiv = ZoneInfo("Europe/Kyiv")
        for day, expect in (("2026-10-25", 600), ("2026-03-29", 600), ("2026-11-04", 600)):
            t0, _ = server.day_bounds(day, kyiv)
            ten = datetime.strptime(day + " 10:00", "%Y-%m-%d %H:%M").replace(tzinfo=kyiv).timestamp()
            self.assertEqual(server.clock(ten, t0, kyiv), expect, day)

    def test_the_week_heatmap_uses_the_clock_hour(self):
        from zoneinfo import ZoneInfo
        kyiv = ZoneInfo("Europe/Kyiv")
        a, b = server.day_bounds("2026-10-25", kyiv)
        ten = datetime.strptime("2026-10-25 10:00", "%Y-%m-%d %H:%M").replace(tzinfo=kyiv).timestamp()
        start = (ten - a) / 60  # elapsed minutes: 660 on this 25-hour day
        r = report.build(["2026-10-25"], [(a, b)], {"aw": [item(start, start + 30)]}, [], "2026-10-25", tz=kyiv)
        self.assertEqual(r["hours"][6][10], 30)  # Sunday, 10:00 on the clock


class GuardTest(unittest.TestCase):
    """The HTTP boundary: who may read data and change things, tested against a real server on a free port."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.dir = tempfile.mkdtemp()
        cls.patches = [mock.patch.object(config, "CONFIG_DIR", cls.dir),
                       mock.patch.object(config, "SECRET_FILE", os.path.join(cls.dir, "secret")),
                       mock.patch.object(config, "SIGNIN_FILE", os.path.join(cls.dir, "signin-code"))]
        for p in cls.patches:
            p.start()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        for p in cls.patches:
            p.stop()

    def ask(self, path, method="GET", headers=None, body=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def token(self):
        code = config.new_signin_code()
        status, _, body = self.ask("/api/session", "POST", {"X-Pulsar": "1", "Content-Type": "application/json"},
                                   ('{"code": "%s"}' % code).encode())
        self.assertEqual(status, 200)
        return {"X-Pulsar-Session": json_load(body)["token"]}

    def test_without_a_session_no_data_and_no_changes(self):
        self.assertEqual(self.ask("/api/config")[0], 401)
        self.assertEqual(self.ask("/icon/com.google.Chrome")[0], 401)
        self.assertEqual(self.ask("/api/quit", "POST", {"X-Pulsar": "1", "Content-Type": "application/json"}, b"{}")[0], 401)
        self.assertEqual(self.ask("/api/version")[0], 200)
        self.assertEqual(self.ask("/")[0], 200)

    def test_a_sign_in_code_gives_one_token_once_and_a_guess_does_not_spoil_it(self):
        code = config.new_signin_code()
        post = lambda c: self.ask("/api/session", "POST", {"X-Pulsar": "1", "Content-Type": "application/json"},
                                  ('{"code": "%s"}' % c).encode())
        self.assertEqual(post("0" * 32)[0], 401)            # a wrong guess...
        status, _, body = post(code)                         # ...leaves the real code working
        token = json_load(body)["token"]
        self.assertEqual(status, 200)
        self.assertNotEqual(token, config.session_secret())  # the browser never holds the key itself
        self.assertEqual(post(code)[0], 401)                 # once only
        self.assertEqual(self.ask("/api/logs?days=1", headers={"X-Pulsar-Session": token})[0], 200)
        self.assertEqual(self.ask("/api/logs?days=1", headers={"X-Pulsar-Session": "f" * 64})[0], 401)
        self.assertEqual(self.ask("/api/logs?days=1", headers={"Cookie": f"pulsar_session={token}"})[0], 401)  # no cookies

    def test_the_launcher_proves_itself_with_the_key(self):
        self.assertEqual(self.ask("/api/logs?days=1", headers={"X-Pulsar-Key": config.session_secret()})[0], 200)
        self.assertEqual(self.ask("/api/logs?days=1", headers={"X-Pulsar-Key": "0" * 64})[0], 401)

    def test_other_sites_names_and_websocket_handshakes_are_refused(self):
        tok = self.token()
        self.assertEqual(self.ask("/api/logs?days=1", headers=tok)[0], 200)
        for extra in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "http://evil.test"}, {"Upgrade": "websocket"},
                      {"Host": "evil.test"}, {"Host": "localhost.evil.test"}):
            self.assertEqual(self.ask("/api/logs?days=1", headers={**tok, **extra})[0], 403, extra)
        self.assertEqual(self.ask("/api/quit", "POST", {**tok, "Content-Type": "application/json"}, b"{}")[0], 403)

    def test_whoami_proves_the_key_without_revealing_it(self):
        import hashlib
        import hmac
        status, _, body = self.ask("/api/whoami?nonce=0123456789abcdef")
        expected = hmac.new(config.session_secret().encode(), b"0123456789abcdef", hashlib.sha256).hexdigest()
        self.assertEqual(json_load(body)["proof"], expected)
        self.assertNotIn(config.session_secret().encode(), body)


def json_load(b):
    import json
    return json.loads(b)


class ValidateTest(unittest.TestCase):
    def test_the_update_reminder_keeps_a_version_and_a_time_only(self):
        self.assertEqual(server.validate({"update_skip": "1.2.0", "update_asked": 1762370000}),
                         {"update_skip": "1.2.0", "update_asked": 1762370000})
        for bad in ({"update_skip": "../x"}, {"update_asked": "now"}, {"update_asked": True}, {"update_asked": -1}):
            with self.assertRaises(ValueError):
                server.validate(bad)

    def test_settings_from_the_page_are_checked(self):
        self.assertEqual(server.validate({"timezone": "", "target_hours": "", "personal_patterns": "a\n\n b "}),
                         {"timezone": "", "target_hours": None, "personal_patterns": ["a", "b"]})
        for bad in ({"timezone": "Mars/Base"}, {"target_hours": "30"}, {"org_id": "12a"}, {"likely": "maybe"},
                    {"activitywatch_url": "file:///etc/passwd"}):
            with self.assertRaises(ValueError):
                server.validate(bad)


if __name__ == "__main__":
    unittest.main()
