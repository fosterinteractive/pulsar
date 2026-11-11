"""ActivityWatch's own AFK timeout and Login Items (awsetup.py), against Settings' "Away after", on temporary files and
fake programs: never the real ActivityWatch, its file, osascript or pkill.

    python3 -m unittest discover -s tests
"""

import json
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH, refused  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import awsetup, config, server  # noqa: E402
from test_away import saved  # noqa: E402

# the shape of ActivityWatch's own file: the testing section must never be read or changed
REAL_SHAPE = """[aw-watcher-afk]
timeout = 1200
poll_time = 30

[aw-watcher-afk-testing]
#timeout = 20
#poll_time = 1"""


def toml(text: str, folder: str = None) -> str:
    folder = folder or tempfile.mkdtemp()
    path = os.path.join(folder, "aw-watcher-afk.toml")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


class Done:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


class ReadTimeoutTest(unittest.TestCase):
    def test_the_file_as_activitywatch_writes_it(self):
        self.assertEqual(awsetup.read_timeout(toml(REAL_SHAPE)), {"seconds": 1200, "exists": True, "problem": None})

    def test_no_file_is_activitywatchs_default_of_three_minutes(self):
        got = awsetup.read_timeout(os.path.join(tempfile.mkdtemp(), "aw-watcher-afk.toml"))
        self.assertEqual(got, {"seconds": 180, "exists": False, "problem": None})

    def test_only_its_own_section_counts(self):
        # the testing section's timeout is not the watcher's; a commented line is not a value
        self.assertEqual(awsetup.read_timeout(toml("[aw-watcher-afk-testing]\ntimeout = 20\n"))["seconds"], 180)
        self.assertEqual(awsetup.read_timeout(toml("[aw-watcher-afk]\n#timeout = 20\npoll_time = 5\n"))["seconds"], 180)
        self.assertEqual(awsetup.read_timeout(toml("timeout = 99\n[aw-watcher-afk]\npoll_time = 5\n"))["seconds"], 180)
        self.assertEqual(awsetup.read_timeout(toml('[ "aw-watcher-afk" ]\n  "timeout" = 1_800  # half an hour\n'))["seconds"],
                         1800)
        self.assertEqual(awsetup.read_timeout(toml("[aw-watcher-afk]\r\ntimeout = 900.0\r\n"))["seconds"], 900)

    def test_a_file_it_cannot_read_for_sure_gives_no_value_and_a_reason(self):
        for text in ('[aw-watcher-afk]\ntimeout = "1800"\n', "[aw-watcher-afk]\ntimeout = 1800\ntimeout = 600\n",
                     "[aw-watcher-afk]\ntimeout = 1800\n[aw-watcher-afk]\n", "aw-watcher-afk.timeout = 1800\n",
                     '"aw-watcher-afk".timeout = 1800\n', "aw-watcher-afk = { timeout = 1800 }\n",
                     '[aw-watcher-afk]\nnote = """\n[x]\n"""\ntimeout = 60\n', "[aw-watcher-afk]\ntimeout = -5\n",
                     "[aw-watcher-afk]\ntimeout =\n"):
            got = awsetup.read_timeout(toml(text))
            self.assertIsNone(got["seconds"], text)
            self.assertTrue(got["problem"], text)
        path = os.path.join(tempfile.mkdtemp(), "aw-watcher-afk.toml")
        with open(path, "wb") as fh:
            fh.write(b"[aw-watcher-afk]\ntimeout = 1800 # \xff\n")
        self.assertIn("could not be read", awsetup.read_timeout(path)["problem"])
        link = os.path.join(tempfile.mkdtemp(), "aw-watcher-afk.toml")
        os.symlink(toml(REAL_SHAPE), link)
        self.assertIn("link", awsetup.read_timeout(link)["problem"])


class DecisionTest(unittest.TestCase):
    def test_a_timeout_other_than_away_after_needs_a_fix(self):
        self.assertTrue(awsetup.differs(1200, 15))     # 20 minutes hides the pauses of 15 to 20
        self.assertTrue(awsetup.differs(180, 15))      # shorter: one away time everywhere (2026-11-10)
        self.assertFalse(awsetup.differs(900, 15))     # the same: nothing to fix
        self.assertFalse(awsetup.differs(None, 15))    # unreadable: never touched
        self.assertFalse(awsetup.differs(True, 0))
        self.assertTrue(awsetup.longer(1200, 15))
        self.assertFalse(awsetup.longer(180, 15))

    def test_settings_shows_it_without_starting_a_program(self):
        path = toml(REAL_SHAPE)
        with mock.patch.object(awsetup, "TOML", path), mock.patch.object(awsetup, "_run", mock.Mock(side_effect=AssertionError)):
            got = awsetup.status(15)
            self.assertEqual((got["seconds"], got["longer"], got["fromFile"], got["problem"]), (1200, True, True, None))
            self.assertFalse(awsetup.status(30)["longer"])
        with saved({"away_after": 20}), mock.patch.object(awsetup, "TOML", path):
            c = server.public_config(config.load())
        self.assertEqual((c["awayAfter"], c["activitywatch"]["seconds"], c["activitywatch"]["longer"]), (20, 1200, False))


class SetTimeoutTest(unittest.TestCase):
    def test_only_that_line_changes_and_a_copy_is_kept_first(self):
        path = toml(REAL_SHAPE)
        os.chmod(path, 0o640)
        got = awsetup.set_timeout(path, 900, now=datetime(2026, 11, 7, 14, 5, 9).timestamp())
        with open(path) as fh:
            self.assertEqual(fh.read(), REAL_SHAPE.replace("timeout = 1200", "timeout = 900"))
        self.assertEqual(os.path.basename(got["backup"]), "aw-watcher-afk.toml.pulsar-2026-11-07-140509.bak")
        self.assertEqual(os.path.dirname(got["backup"]), os.path.dirname(path))  # next to the file
        with open(got["backup"]) as fh:
            self.assertEqual(fh.read(), REAL_SHAPE)
        self.assertEqual((got["before"], got["after"]), (1200, 900))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)
        self.assertEqual(sorted(os.listdir(os.path.dirname(path))),
                         ["aw-watcher-afk.toml", "aw-watcher-afk.toml.pulsar-2026-11-07-140509.bak"])  # no temporary left

    def test_its_indent_comment_and_line_endings_stay(self):
        path = toml("# mine\r\n[aw-watcher-afk]\r\n  timeout   =   1800   # was 30 min\r\npoll_time = 5\r\n")
        awsetup.set_timeout(path, 600)
        with open(path, newline="") as fh:
            self.assertEqual(fh.read(), "# mine\r\n[aw-watcher-afk]\r\n  timeout   =   600   # was 30 min\r\npoll_time = 5\r\n")

    def test_a_missing_key_is_added_in_that_section_and_a_missing_section_at_the_end(self):
        path = toml("[aw-watcher-afk]\npoll_time = 5\n\n[aw-watcher-afk-testing]\ntimeout = 20\n")
        awsetup.set_timeout(path, 900)
        with open(path) as fh:
            self.assertEqual(fh.read(), "[aw-watcher-afk]\ntimeout = 900\npoll_time = 5\n\n[aw-watcher-afk-testing]\ntimeout = 20\n")
        path = toml("[aw-watcher-afk-testing]\ntimeout = 20")
        awsetup.set_timeout(path, 1200)
        with open(path) as fh:
            self.assertEqual(fh.read(), "[aw-watcher-afk-testing]\ntimeout = 20\n\n[aw-watcher-afk]\ntimeout = 1200\n")
        self.assertEqual(awsetup.read_timeout(path)["seconds"], 1200)

    def test_a_file_it_cannot_read_for_sure_is_left_as_it_was_with_no_copy(self):
        for text in ('[aw-watcher-afk]\ntimeout = "x"\n', "[aw-watcher-afk]\ntimeout = 1\ntimeout = 2\n"):
            path = toml(text)
            with self.assertRaises(ValueError):
                awsetup.set_timeout(path, 900)
            with open(path) as fh:
                self.assertEqual(fh.read(), text)
            self.assertEqual(os.listdir(os.path.dirname(path)), ["aw-watcher-afk.toml"])
        with self.assertRaises(ValueError):
            awsetup.set_timeout(os.path.join(tempfile.mkdtemp(), "none.toml"), 900)  # no file: nothing to change
        for bad in (0, 30, 9000, 900.5, True, "900"):
            with self.assertRaises(ValueError):
                awsetup.set_timeout(toml(REAL_SHAPE), bad)

    def test_a_copy_never_replaces_another(self):
        path = toml(REAL_SHAPE)
        when = datetime(2026, 11, 7, 9, 0, 0).timestamp()
        first = awsetup.set_timeout(path, 900, now=when)["backup"]
        second = awsetup.set_timeout(path, 600, now=when)["backup"]
        self.assertNotEqual(first, second)
        with open(first) as a, open(second) as b:
            self.assertEqual((a.read(), b.read()), (REAL_SHAPE, REAL_SHAPE.replace("1200", "900")))

    def test_a_value_that_does_not_read_back_puts_the_file_back(self):
        path = toml(REAL_SHAPE)
        with mock.patch.object(awsetup, "read_timeout", lambda p: {"seconds": 1, "exists": True, "problem": None}):
            with self.assertRaises(ValueError):
                awsetup.set_timeout(path, 900)
        with open(path) as fh:
            self.assertEqual(fh.read(), REAL_SHAPE)


class RestartTest(unittest.TestCase):
    """The restart, on fake programs: what runs, in which order, and how long it waits at most."""

    def setUp(self):
        self.calls, self.now = [], [0.0]
        self.sleep = lambda s: self.now.__setitem__(0, self.now[0] + s)
        self.clock = lambda: self.now[0]

    def fake(self, quits_after=2, afk=True, opens=0):
        state = {"pgrep": 0}

        def run(argv, timeout=30):
            self.calls.append(argv[:2] if argv[0] in ("pgrep", "pkill") else argv)
            if argv[0] == "pgrep" and argv[2] == awsetup.ANY:
                state["pgrep"] += 1
                return Done(0 if state["pgrep"] <= quits_after else 1)
            if argv[0] == "pgrep":
                return Done(0 if afk else 1)
            if argv[0] == "open":
                return Done(opens, err="no app" if opens else "")
            return Done(0)
        return run

    def test_it_ends_aw_qt_waits_for_every_process_then_opens_and_checks_the_afk_watcher(self):
        answers = iter([OSError("not yet"), {"hostname": "mac"}])

        def fetch(url, timeout=2):
            self.calls.append(url)
            a = next(answers)
            if isinstance(a, Exception):
                raise a
            return a
        got = awsetup.restart("http://localhost:5600", self.fake(), fetch, self.sleep, self.clock, app="/Apps/ActivityWatch.app")
        self.assertTrue(got["ok"], got)
        self.assertEqual(self.calls, [["pkill", "-f"], ["pgrep", "-f"], ["pgrep", "-f"], ["pgrep", "-f"],
                                      ["open", "-a", "/Apps/ActivityWatch.app"], "http://localhost:5600/api/0/info",
                                      "http://localhost:5600/api/0/info", ["pgrep", "-f"]])

    def test_an_app_that_does_not_quit_is_not_opened_twice(self):
        got = awsetup.restart("http://localhost:5600", self.fake(quits_after=10 ** 6), lambda *a, **k: {}, self.sleep,
                              self.clock, app="/Apps/ActivityWatch.app")
        self.assertFalse(got["ok"])
        self.assertIn("did not quit", got["message"])
        self.assertNotIn("open", [c[0] for c in self.calls])
        self.assertLessEqual(self.now[0], awsetup.QUIT_WAIT + 1)  # a bounded wait

    def test_a_server_that_does_not_answer_or_a_missing_afk_watcher_is_said(self):
        def down(url, timeout=2):
            raise OSError("refused")
        got = awsetup.restart("http://localhost:5600", self.fake(), down, self.sleep, self.clock, app="/A/ActivityWatch.app")
        self.assertIn("did not answer", got["message"])
        self.now[0] = 0.0
        got = awsetup.restart("http://localhost:5600", self.fake(afk=False), lambda url, timeout=2: {}, self.sleep,
                              self.clock, app="/A/ActivityWatch.app")
        self.assertIn("AFK watcher did not start", got["message"])

    def test_a_fresh_heartbeat_in_the_afk_bucket_also_says_it_runs(self):
        def fetch(url, timeout=2):  # a heartbeat written after the app was opened again
            stamp = datetime.fromtimestamp(time.time() + 1, timezone.utc).isoformat()
            return {"aw-watcher-afk_mac": {"last_updated": stamp}} if url.endswith("/buckets/") else {}
        got = awsetup.restart("http://localhost:5600", self.fake(afk=False), fetch, self.sleep, self.clock,
                              app="/A/ActivityWatch.app")
        self.assertTrue(got["ok"], got)

    def test_the_app_is_found_where_it_runs_and_only_if_it_is_activitywatch(self):
        root = tempfile.mkdtemp()

        def bundle(name, bundle_id):
            app = os.path.join(root, name, "ActivityWatch.app")
            os.makedirs(os.path.join(app, "Contents"))
            with open(os.path.join(app, "Contents", "Info.plist"), "wb") as fh:
                plistlib.dump({"CFBundleIdentifier": bundle_id}, fh)
            return app
        real, fake = bundle("Tools", awsetup.BUNDLE_ID), bundle("Elsewhere", "com.example.other")
        ps = f"/usr/sbin/cfprefsd agent\n{fake}/Contents/MacOS/aw-qt\n{real}/Contents/MacOS/aw-qt\n{real}/Contents/MacOS/aw-server\n"
        with mock.patch.object(awsetup, "PLACES", ()):  # not this Mac's own /Applications
            self.assertEqual(awsetup.app_path(lambda argv, timeout=10: Done(0, ps if argv[0] == "ps" else "")), real)
            self.assertEqual(awsetup.app_path(lambda argv, timeout=10: Done(0, "" if argv[0] == "ps" else f"{fake}\n{real}\n")), real)
            self.assertIsNone(awsetup.app_path(lambda argv, timeout=10: Done(0, "")))


class FixTest(unittest.TestCase):
    def test_a_longer_timeout_is_changed_then_activitywatch_restarted(self):
        path, restarted = toml(REAL_SHAPE), []
        with mock.patch.object(awsetup, "blocked", lambda: None), \
                mock.patch.object(awsetup, "restart", lambda base, *a: restarted.append(base) or {"ok": True, "message": "Restarted."}):
            got = awsetup.fix(15, "http://localhost:5600", path)
        self.assertTrue(got["ok"] and got["changed"], got)
        self.assertEqual(awsetup.read_timeout(path)["seconds"], 900)
        self.assertEqual(restarted, ["http://localhost:5600"])
        self.assertIn("new data only", got["message"])

    def test_a_restart_that_cannot_run_is_said_and_the_change_kept(self):
        path = toml(REAL_SHAPE)
        with mock.patch.object(awsetup, "blocked", lambda: None), \
                mock.patch.object(awsetup, "restart", mock.Mock(side_effect=subprocess.TimeoutExpired("pkill", 10))):
            got = awsetup.fix(15, "http://localhost:5600", path)
        self.assertEqual((got["ok"], got["changed"]), (False, True))
        self.assertIn("menu-bar icon", got["message"])
        self.assertEqual(awsetup.read_timeout(path)["seconds"], 900)  # changed, with its copy: a restart by hand does it
        got_path = toml(REAL_SHAPE)
        with mock.patch.object(awsetup, "blocked", lambda: None), \
                mock.patch.object(awsetup, "set_timeout", mock.Mock(side_effect=PermissionError(13, "Permission denied"))):
            got = awsetup.fix(15, "http://localhost:5600", got_path)
        self.assertEqual((got["ok"], got["changed"]), (False, False))
        self.assertIn("Permission denied", got["message"])

    def test_a_shorter_timeout_is_set_too(self):
        path, restarted = toml("[aw-watcher-afk]\ntimeout = 180\n"), []
        with mock.patch.object(awsetup, "blocked", lambda: None), \
                mock.patch.object(awsetup, "restart", lambda base, *a: restarted.append(base) or {"ok": True, "message": "Restarted."}):
            got = awsetup.fix(20, "http://localhost:5600", path)
        self.assertTrue(got["ok"] and got["changed"], got)
        self.assertEqual((awsetup.read_timeout(path)["seconds"], len(restarted)), (1200, 1))

    def test_save_sets_activitywatch_to_away_after_but_not_from_preview(self):
        path, calls = toml(REAL_SHAPE), []  # ActivityWatch at 20 minutes
        fake = lambda minutes, base: calls.append(minutes) or {"ok": True, "changed": True, "message": "set"}  # noqa: E731
        s = {**config.DEFAULTS, "away_after": 15}
        with mock.patch.object(awsetup, "TOML", path), mock.patch.object(awsetup, "SUPPORT", os.path.dirname(path)), \
                mock.patch.object(awsetup, "fix", fake):
            self.assertIsNone(server.sync_activitywatch(s))  # a test run: refused before anything
            with mock.patch.object(awsetup, "blocked", lambda: None):
                self.assertEqual(server.sync_activitywatch(s)["message"], "set")
                self.assertIsNone(server.sync_activitywatch({**s, "away_after": 20}))  # the same already
                with mock.patch.object(config, "is_preview", lambda: True):
                    self.assertIsNone(server.sync_activitywatch(s))  # Preview: ActivityWatch is the real Pulsar's too
        self.assertEqual(calls, [15])

    def test_the_same_or_unreadable_is_never_touched(self):
        for text in ("[aw-watcher-afk]\ntimeout = 900\n", '[aw-watcher-afk]\ntimeout = "x"\n'):
            path = toml(text)
            with mock.patch.object(awsetup, "blocked", lambda: None), \
                    mock.patch.object(awsetup, "restart", mock.Mock(side_effect=AssertionError("no restart"))):
                got = awsetup.fix(15, "http://localhost:5600", path)
            self.assertFalse(got["changed"], text)
            with open(path) as fh:
                self.assertEqual(fh.read(), text)
            self.assertEqual(os.listdir(os.path.dirname(path)), ["aw-watcher-afk.toml"])

    def test_a_test_run_or_a_read_only_run_changes_nothing(self):
        path = toml(REAL_SHAPE)
        got = awsetup.fix(15, "http://localhost:5600", path)  # this run is a test run (PULSAR_TESTS_OFFLINE)
        self.assertEqual((got["ok"], got["changed"]), (False, False))
        self.assertIn("test run", got["message"])
        with mock.patch.dict(os.environ, {"PULSAR_TESTS_OFFLINE": "", "PULSAR_READ_ONLY": "1"}):
            self.assertIn("read-only", awsetup.fix(15, "http://localhost:5600", path)["message"])
            self.assertIn("read-only", awsetup.check_login()["message"])
            self.assertIn("read-only", awsetup.add_login()["message"])
        with open(path) as fh:
            self.assertEqual(fh.read(), REAL_SHAPE)

    def test_the_real_programs_and_activitywatchs_server_are_refused_even_with_the_routes_open(self):
        # a test that opens the routes and forgets to pass fakes still reaches nothing (_run and _fetch ask on their own)
        with mock.patch.object(awsetup, "blocked", lambda: None):
            with self.assertRaises(awsetup.Refused):
                awsetup._run(["pkill", "-f", awsetup.QT])
            with self.assertRaises(awsetup.Refused):
                awsetup._fetch("http://localhost:5600/api/0/info")
            with self.assertRaises(awsetup.Refused):  # through the route's own code: refused, loudly, nothing started
                awsetup.check_login()
        with refused() as tried:  # and behind that, the run's own guard (isolation.py)
            for program in ("pkill", "osascript", "open"):
                with self.assertRaises(BaseException):
                    subprocess.run([program, "-h"], capture_output=True)
        self.assertEqual(len(tried), 3)


class LoginItemsTest(unittest.TestCase):
    def setUp(self):
        awsetup.LOGIN.update(at=0.0, on=None)
        self.scripts = []

    def osascript(self, items, add_ok=True, denied=False):
        def run(argv, timeout=30):
            if argv[0] == "osascript":
                self.scripts.append(argv[2])
                if denied:
                    return Done(1, err="execution error: Not authorized to send Apple events to System Events. (-1743)")
                if "make login item" in argv[2]:
                    if add_ok:
                        items.append("ActivityWatch")
                    return Done(0, "login item UNKNOWN\n")
                return Done(0, ", ".join(items) + "\n")
            return Done(0, "")
        return run

    def test_checking_says_whether_it_opens_at_login_and_remembers_it(self):
        with mock.patch.object(awsetup, "blocked", lambda: None):
            self.assertEqual(awsetup.check_login(self.osascript(["Pulsar", "ActivityWatch"]))["on"], True)
            self.assertEqual(awsetup.LOGIN["on"], True)
            got = awsetup.check_login(self.osascript(["MemTime"]))
        self.assertEqual((got["ok"], got["on"]), (True, False))
        self.assertIn("records nothing", got["message"])
        self.assertEqual(awsetup.status(15)["login"], False)

    def test_adding_uses_the_apps_real_path_and_checks_again(self):
        items = ["Pulsar"]
        with mock.patch.object(awsetup, "blocked", lambda: None), \
                mock.patch.object(awsetup, "app_path", lambda run=None: '/Volumes/My "Apps"/ActivityWatch.app'):
            got = awsetup.add_login(self.osascript(items))
        self.assertEqual((got["ok"], got["on"]), (True, True), got)
        self.assertEqual(len(self.scripts), 3)  # list, add, list again
        self.assertIn('{path:"/Volumes/My \\"Apps\\"/ActivityWatch.app", hidden:true}', self.scripts[1])

    def test_already_there_is_not_added_twice_and_an_add_that_did_not_take_is_said(self):
        with mock.patch.object(awsetup, "blocked", lambda: None), \
                mock.patch.object(awsetup, "app_path", lambda run=None: "/Applications/ActivityWatch.app"):
            self.assertIn("already", awsetup.add_login(self.osascript(["ActivityWatch"]))["message"])
            self.assertEqual(len(self.scripts), 1)
            got = awsetup.add_login(self.osascript(["Pulsar"], add_ok=False))
        self.assertEqual((got["ok"], got["on"]), (False, False))

    def test_a_no_from_macos_says_where_to_allow_it(self):
        with mock.patch.object(awsetup, "blocked", lambda: None):
            got = awsetup.check_login(self.osascript([], denied=True))
        self.assertFalse(got["ok"])
        self.assertIn("Automation", got["message"])
        self.assertIsNone(awsetup.LOGIN["on"])


class RoutesTest(unittest.TestCase):
    """The two POST routes: the page's session only, never the key alone; refused in a test run and a read-only run,
    with nothing saved or started."""

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.dir = tempfile.mkdtemp()
        cls.patches = [mock.patch.object(config, "CONFIG_DIR", cls.dir),
                       mock.patch.object(config, "SECRET_FILE", os.path.join(cls.dir, "secret")),
                       mock.patch.object(config, "SIGNIN_FILE", os.path.join(cls.dir, "signin-code")),
                       mock.patch.object(config, "SETTINGS_FILE", os.path.join(cls.dir, "settings.json"))]
        for p in cls.patches:
            p.start()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for p in cls.patches:
            p.stop()

    def post(self, path, body, headers):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", path, body=json.dumps(body).encode(),
                  headers={"Host": f"127.0.0.1:{self.port}", "X-Pulsar": "1", "Content-Type": "application/json", **headers})
        r = c.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        c.close()
        return out

    def page(self):
        status, body = self.post("/api/session", {"code": config.new_signin_code()}, {})
        self.assertEqual(status, 200)
        return {"X-Pulsar-Session": body["token"]}

    def test_the_key_alone_is_refused(self):
        key = {"X-Pulsar-Key": config.session_secret()}
        for path, body in (("/api/activitywatch/fix", {"minutes": 15}), ("/api/activitywatch/login", {"add": True})):
            self.assertEqual(server.key_refusal(path, body), server.KEY_ONLY)
            status, answer = self.post(path, body, key)
            self.assertEqual(status, 403, path)
            self.assertIn("Pulsar's window", answer["error"])
            self.assertEqual(self.post(path, body, {})[0], 401)  # no session at all

    def test_the_page_is_refused_in_a_test_run_and_a_read_only_run_with_nothing_saved(self):
        page = self.page()
        path = toml(REAL_SHAPE)
        never = mock.Mock(side_effect=AssertionError("nothing may run"))
        with mock.patch.object(awsetup, "TOML", path), mock.patch.object(awsetup, "set_timeout", never), \
                mock.patch.object(awsetup, "restart", never), mock.patch.object(awsetup, "_osascript", never):
            for env in ({}, {"PULSAR_TESTS_OFFLINE": "", "PULSAR_READ_ONLY": "1"}):
                with mock.patch.dict(os.environ, env):
                    status, got = self.post("/api/activitywatch/fix", {"minutes": 15}, page)
                    self.assertEqual(status, 200)
                    self.assertEqual((got["result"]["ok"], got["result"]["changed"]), (False, False))
                    self.assertTrue(got["activitywatch"]["longer"])
                    for add in (False, True):
                        status, got = self.post("/api/activitywatch/login", {"add": add}, page)
                        self.assertFalse(got["result"]["ok"])
        self.assertFalse(os.path.exists(config.SETTINGS_FILE))  # Away after was not saved for a refused fix
        with open(path) as fh:
            self.assertEqual(fh.read(), REAL_SHAPE)
        status, got = self.post("/api/activitywatch/fix", {"minutes": 25}, page)
        self.assertEqual(status, 400)

    def test_a_fix_the_page_asked_for_saves_away_after_too(self):
        page = self.page()
        with mock.patch.object(awsetup, "fix", lambda minutes, base: {"ok": True, "changed": True, "message": "Done."}):
            status, got = self.post("/api/activitywatch/fix", {"minutes": 20}, page)
        self.assertEqual((status, got["awayAfter"]), (200, 20))
        self.assertEqual(config.load()["away_after"], 20)
        os.remove(config.SETTINGS_FILE)


class PageTest(unittest.TestCase):
    """Settings, Time and activity: Away after's four choices, saved with the rest, and the two buttons' routes."""

    WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

    def test_the_choice_is_in_settings_and_saved(self):
        import re
        with open(os.path.join(self.WEB, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        panel = html[html.index('id="p-day"'):html.index('id="p-claude"')]  # Time and activity
        select = re.search(r'<select name="away_after">(.*?)</select>', panel)
        self.assertEqual(re.findall(r'value="(\d+)"', select.group(1)), [str(v) for v in config.AWAY_AFTER])
        with open(os.path.join(self.WEB, "js", "settings.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn("away_after: +f.away_after.value", js)
        self.assertIn("'/api/activitywatch/fix'", js)
        self.assertIn("'/api/activitywatch/login'", js)
        self.assertIn("new data only", panel)  # said before the click


if __name__ == "__main__":
    unittest.main()
