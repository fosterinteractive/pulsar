"""Pulsar.app itself: "Reinstall Pulsar" puts a newer app in place only as a release signed it, keeps the old one until
the new one has started and puts it back if it does not (pulsar/appupdate.py); and the launcher's dialogs and progress
reach the window of 1.6.0 on, which shows them as its own sheets (macos/launcher.sh).

Every key is a throwaway one made here; the apps are small stand-ins (an Info.plist, a copy of /usr/bin/true for the
program, a few files of code) in temporary folders, signed ad hoc as build_app.sh signs; downloads are faked.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

import base64  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import plistlib  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from pulsar import appupdate, config, release_sig, server, updater  # noqa: E402

OPENSSL, CODESIGN, DITTO = "/usr/bin/openssl", "/usr/bin/codesign", "/usr/bin/ditto"
RELEASE_URL = "https://github.com/fosterinteractive/pulsar/releases/download/v1.6.0/Pulsar.zip"
MAC = all(os.path.exists(p) for p in (OPENSSL, CODESIGN, DITTO, "/usr/bin/true"))


def make_key(folder, name):
    key, pub = os.path.join(folder, name + ".pem"), os.path.join(folder, name + ".pub.pem")
    subprocess.run([OPENSSL, "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", key], check=True, capture_output=True)
    subprocess.run([OPENSSL, "ec", "-in", key, "-pubout", "-out", pub], check=True, capture_output=True)
    with open(pub, encoding="utf-8") as fh:
        return key, fh.read()


def sign(key, data):
    path = os.path.join(tempfile.mkdtemp(), "data")
    with open(path, "wb") as fh:
        fh.write(data)
    return subprocess.run([OPENSSL, "dgst", "-sha256", "-sign", key, path], check=True, capture_output=True).stdout


def write_code(folder, version, main="import sys\nsys.exit(1)\n"):
    files = {"pulsar.py": main, "pulsar/__init__.py": f'__version__ = "{version}"\n', "web/index.html": "<!doctype html>\n"}
    for rel, text in files.items():
        path = os.path.join(folder, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


def make_app(where, version, key, bundle_id=appupdate.APP_ID, code_version=None, launcher=False, pub=None,
             main="import sys\nsys.exit(1)\n"):
    """A stand-in Pulsar.app: its code described and signed with `key` (None: not signed), signed ad hoc."""
    app = os.path.join(where, "Pulsar.app")
    res = os.path.join(app, "Contents", "Resources")
    os.makedirs(os.path.join(app, "Contents", "MacOS"))
    os.makedirs(res)
    with open(os.path.join(app, "Contents", "Info.plist"), "wb") as fh:
        plistlib.dump({"CFBundleIdentifier": bundle_id, "CFBundleShortVersionString": version, "CFBundleVersion": version,
                       "CFBundleExecutable": "Pulsar", "CFBundlePackageType": "APPL", "CFBundleName": "Pulsar"}, fh)
    shutil.copy("/usr/bin/true", os.path.join(app, "Contents", "MacOS", "Pulsar"))
    code = os.path.join(res, "app")
    write_code(code, version, main)
    manifest = release_sig.bundle_manifest(code, version)
    with open(os.path.join(res, "app.manifest.json"), "wb") as fh:
        fh.write(manifest)
    if key:
        with open(os.path.join(res, "app.manifest.json.sig"), "wb") as fh:
            fh.write(sign(key, manifest))
    if code_version:  # the code changed after it was signed
        write_code(code, code_version, main)
    if launcher:  # its own launcher, trusting only this test's key, as the app's launcher trusts the release keys
        shutil.copy(os.path.join(REPO, "macos", "launcher.sh"), os.path.join(res, "launcher.sh"))
        with open(release_sig.__file__, encoding="utf-8") as fh:
            source = fh.read()
        source = re.sub(r"^PUBLIC_KEYS = \(\n.*?\n\)\n", lambda m: "PUBLIC_KEYS = (" + repr(pub) + ",)\n", source,
                        count=1, flags=re.S | re.M)
        with open(os.path.join(res, "release_sig.py"), "w", encoding="utf-8") as fh:
            fh.write(source)
    subprocess.run([CODESIGN, "--force", "--deep", "-s", "-", app], check=True, capture_output=True)
    return app


def zip_app(app):
    out = os.path.join(tempfile.mkdtemp(), "Pulsar.zip")
    subprocess.run([DITTO, "-c", "-k", "--keepParent", app, out], check=True, capture_output=True)
    with open(out, "rb") as fh:
        return fh.read()


def version_at(app):
    return appupdate.bundle_info(app).get("version")


@unittest.skipUnless(MAC, "needs a Mac: openssl, codesign and ditto")
class ReinstallTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.key, self.pub = make_key(self.dir, "release")
        self.root = os.path.join(self.dir, "support")  # what ~/Library/Application Support/Pulsar is to a real copy
        os.makedirs(self.root)
        self.apps = os.path.join(self.dir, "Applications")
        os.makedirs(self.apps)
        old = make_app(tempfile.mkdtemp(), "1.5.4", self.key)
        self.app = os.path.join(self.apps, "Pulsar.app")
        os.replace(old, self.app)
        self.fetched = []
        self.patches = [mock.patch.object(updater, "installed", lambda root=None: True),
                        mock.patch.object(updater, "fetch", self.fetch)]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.addCleanup(lambda: os.chmod(self.apps, 0o755))
        self.serve(make_app(tempfile.mkdtemp(), "1.6.0", self.key))

    def serve(self, new_app, data=None, signature=None):
        self.zip = data if data is not None else zip_app(new_app)
        self.sig = signature if signature is not None else sign(self.key, self.zip)
        self.release = {"version": "1.6.0", "min": "1.6.0", "url": RELEASE_URL,
                        "sha256": hashlib.sha256(self.zip).hexdigest()}

    def fetch(self, url, limit, timeout=30):
        self.fetched.append(url)
        if url == RELEASE_URL:
            return self.zip
        if url == RELEASE_URL + ".sig":
            return self.sig
        raise OSError(f"no such download in this test: {url}")

    def reinstall(self, **kw):
        return appupdate.reinstall(kw.pop("release", self.release), root=self.root, path=self.app, keys=(self.pub,),
                                   spawn=False, **kw)

    def litter(self):
        return sorted(n for n in os.listdir(self.apps) if n != "Pulsar.app")

    def assertUnchanged(self):
        self.assertEqual(version_at(self.app), "1.5.4")
        self.assertEqual(self.litter(), [])
        self.assertEqual(appupdate.read_state(self.root), {})

    def test_the_new_app_takes_the_old_ones_place_and_the_old_one_is_kept(self):
        got = self.reinstall()
        self.assertEqual((got["ok"], got["version"]), (True, "1.6.0"))
        self.assertEqual(version_at(self.app), "1.6.0")
        state = appupdate.read_state(self.root)
        self.assertEqual((state["status"], state["version"], state["previous"]), ("waiting", "1.6.0", "1.5.4"))
        self.assertEqual(version_at(state["old"]), "1.5.4")  # kept beside it, until the new one has started
        self.assertEqual(self.litter(), [appupdate.PREVIOUS])
        self.assertEqual(appupdate.status(self.root), {"pending": "waiting", "pendingVersion": "1.6.0", "failed": ""})
        run = subprocess.run([CODESIGN, "--verify", "--deep", "--strict", self.app], capture_output=True)
        self.assertEqual(run.returncode, 0)  # unpacked as signed

    def test_once_the_new_app_says_it_started_the_old_one_goes(self):
        self.reinstall()
        opened = []

        def opener(path):  # the new app opens, and its page says so
            opened.append(path)
            appupdate.started("1.6.0", self.root)
        self.assertEqual(appupdate.finish(os.path.join(self.root, appupdate.STATE), opener=opener, poll=0.01,
                                          wait_start=5), "confirmed")
        self.assertEqual(opened, [self.app])
        self.assertEqual(self.litter(), [])
        self.assertEqual(appupdate.status(self.root), {"pending": "", "pendingVersion": "", "failed": ""})

    def test_a_new_app_that_does_not_start_is_swapped_back_and_not_offered_again(self):
        self.reinstall()
        opened = []
        self.assertEqual(appupdate.finish(os.path.join(self.root, appupdate.STATE), opener=opened.append, poll=0.01,
                                          wait_start=0.2), "rolledback")
        self.assertEqual(opened, [self.app, self.app])  # the new one, then the old one again
        self.assertEqual(version_at(self.app), "1.5.4")
        self.assertEqual(self.litter(), [])
        self.assertEqual(appupdate.status(self.root)["failed"], "1.6.0")
        with self.assertRaisesRegex(ValueError, "did not start"):
            self.reinstall()

    def test_the_helper_waits_for_the_running_app_to_quit(self):
        running = subprocess.Popen(["/bin/sleep", "1.5"])
        threading.Thread(target=running.wait, daemon=True).start()  # reaped as soon as it ends, as launchd would
        with mock.patch.object(appupdate, "app_pids", lambda path: [running.pid]):
            self.reinstall()
        opened, t0 = [], time.time()
        out = appupdate.finish(os.path.join(self.root, appupdate.STATE), opener=lambda p: (opened.append(time.time() - t0),
                               appupdate.started("1.6.0", self.root)), poll=0.05, wait_start=5)
        self.assertEqual(out, "confirmed")
        self.assertGreater(opened[0], 1.0)  # not before the app that was running had quit

    def test_a_wrong_checksum_changes_nothing(self):
        self.release["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.reinstall()
        self.assertUnchanged()

    def test_a_zip_not_signed_with_the_release_key_changes_nothing(self):
        other, _ = make_key(self.dir, "other")
        self.serve(make_app(tempfile.mkdtemp(), "1.6.0", self.key), data=self.zip, signature=sign(other, self.zip))
        with self.assertRaisesRegex(ValueError, "not signed with the release key"):
            self.reinstall()
        self.assertUnchanged()

    def test_another_app_or_another_version_in_the_zip_changes_nothing(self):
        for app in (make_app(tempfile.mkdtemp(), "1.6.0", self.key, bundle_id="com.example.other"),
                    make_app(tempfile.mkdtemp(), "1.6.1", self.key)):
            self.serve(app)
            with self.assertRaisesRegex(ValueError, "another app"):
                self.reinstall()
            self.assertUnchanged()

    def test_code_no_release_signed_changes_nothing(self):
        for app in (make_app(tempfile.mkdtemp(), "1.6.0", None),                       # not signed
                    make_app(tempfile.mkdtemp(), "1.6.0", make_key(self.dir, "x")[0]),  # signed by another key
                    make_app(tempfile.mkdtemp(), "1.6.0", self.key, code_version="9.9.9")):  # changed after
            self.serve(app)
            with self.assertRaisesRegex(ValueError, "not a signed release"):
                self.reinstall()
            self.assertUnchanged()

    def test_an_app_changed_after_its_signature_changes_nothing(self):
        app = make_app(tempfile.mkdtemp(), "1.6.0", self.key)
        with open(os.path.join(app, "Contents", "MacOS", "Pulsar"), "ab") as fh:
            fh.write(b"\0")
        self.serve(app)
        with self.assertRaisesRegex(ValueError, "signature check"):
            self.reinstall()
        self.assertUnchanged()

    def test_a_download_outside_the_release_is_not_fetched(self):
        with self.assertRaisesRegex(ValueError, "outside its GitHub release"):
            self.reinstall(release={**self.release, "url": "https://example.com/Pulsar.zip"})
        self.assertEqual(self.fetched, [])
        self.assertUnchanged()

    def test_a_folder_this_account_cannot_change_gets_the_install_line_and_no_download(self):
        os.chmod(self.apps, stat.S_IRUSR | stat.S_IXUSR)
        with self.assertRaises(appupdate.NotWritable):
            self.reinstall()
        self.assertEqual(self.fetched, [])
        os.chmod(self.apps, 0o755)
        self.assertUnchanged()
        line = appupdate.install_line()
        self.assertTrue(line.startswith("d=$(/usr/bin/mktemp -d)") and "Pulsar.zip.sig" in line, line)

    def test_preview_and_an_app_that_is_not_pulsar_are_refused(self):
        with mock.patch.object(config, "is_preview", lambda: True), self.assertRaisesRegex(ValueError, "Preview"):
            self.reinstall()
        with open(os.path.join(self.app, "Contents", "Info.plist"), "wb") as fh:
            plistlib.dump({"CFBundleIdentifier": "com.fosterinteractive.pulsar.preview"}, fh)
        with self.assertRaisesRegex(ValueError, "is not Pulsar"):
            self.reinstall()
        self.assertEqual(self.fetched, [])

    def test_a_second_click_while_one_waits_changes_nothing_more(self):
        self.reinstall()
        self.fetched.clear()
        self.assertTrue(self.reinstall()["pending"])
        self.assertEqual(self.fetched, [])

    def test_started_from_another_version_is_not_the_end_of_a_reinstall(self):
        self.reinstall()
        self.assertFalse(appupdate.started("1.5.4", self.root)["done"])
        self.assertEqual(appupdate.status(self.root)["pending"], "waiting")
        self.assertEqual(self.litter(), [appupdate.PREVIOUS])


class ManifestTest(unittest.TestCase):
    def test_only_a_whole_app_description_is_believed(self):
        good = {"version": "1.6.0", "min": "1.6.0", "url": RELEASE_URL, "sha256": "a" * 64}
        self.assertEqual(appupdate.manifest_app({"app": good}), good)
        for bad in ({**good, "sha256": "xyz"}, {**good, "min": "1.7.0"}, {**good, "version": "1.6"},
                    {**good, "url": "file:///tmp/x"}, "1.6.0", None):
            self.assertEqual(appupdate.manifest_app({"app": bad}), {}, bad)
        self.assertEqual(appupdate.manifest_app({}), {})

    def test_the_updater_keeps_what_a_signed_latest_json_says_of_the_app(self):
        root = tempfile.mkdtemp()
        key, pub = make_key(root, "release")
        app = {"version": "1.6.0", "min": "1.6.0", "url": RELEASE_URL, "sha256": "b" * 64}
        raw = json.dumps({"version": "1.0.0", "url": "x", "sha256": "c" * 64, "expires": "2999-01-01T00:00:00Z",
                          "app": app}).encode()
        sig = sign(key, raw)
        u = updater.Updater(manifest_url="https://example.invalid/latest.json", root=root)
        with mock.patch.object(updater, "fetch", lambda url, limit, timeout=30: sig if url.endswith(".sig") else raw), \
             mock.patch.object(updater, "public_keys", lambda: (pub,)), mock.patch.object(updater, "__version__", "1.0.0"):
            state = u.check()
        self.assertEqual(state["status"], "latest")
        self.assertEqual(state["app"], {**app, "pending": "", "pendingVersion": "", "failed": ""})

    def test_the_server_answers_with_the_install_line_when_the_folder_is_not_ours(self):
        with mock.patch.object(appupdate, "reinstall", side_effect=appupdate.NotWritable("no permission")):
            got = server.api_app_reinstall()
        self.assertEqual(got["error"], "no permission")
        self.assertIn("Pulsar.zip.sig", got["installLine"])
        with mock.patch.object(appupdate, "reinstall", side_effect=OSError("offline")):
            self.assertEqual(server.api_app_reinstall(), {"error": "offline"})

    def test_reinstalling_needs_the_page(self):
        self.assertEqual(server.key_refusal("/api/update/app"), server.KEY_ONLY)
        self.assertEqual(server.key_refusal("/api/app/started"), server.KEY_ONLY)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@unittest.skipUnless(MAC, "needs a Mac: openssl, codesign and ditto")
class LauncherWindowTest(unittest.TestCase):
    """launcher.sh run by the window of 1.6.0 on (--window, PULSAR_WINDOW_DIALOGS=1): its dialogs and progress come out
    as lines for the window, and the answers go back on its input."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.key, self.pub = make_key(self.dir, "release")
        self.home = os.path.join(self.dir, "home")
        self.port = free_port()

    def env(self, **extra):
        return {**os.environ, "HOME": self.home, "XDG_CONFIG_HOME": os.path.join(self.home, ".config"),
                "XDG_CACHE_HOME": os.path.join(self.home, ".cache"), "PULSAR_PORT": str(self.port),
                "PULSAR_READ_ONLY": "1", **extra}

    def converse(self, app, answers, **extra):
        """Runs the app's launcher as the window does; answers each dialog in turn. Returns (exit, dialogs, statuses)."""
        dialogs, statuses, answers = [], [], list(answers)
        with subprocess.Popen(["/bin/bash", os.path.join(app, "Contents", "Resources", "launcher.sh"), "--window"],
                              env=self.env(PULSAR_WINDOW_DIALOGS="1", **extra), stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as run:
            for line in run.stdout:
                kind, _, rest = line.rstrip("\n").partition(" ")
                if kind == "PULSAR_DIALOG":
                    text, buttons = rest.split(" ")
                    dialogs.append((base64.b64decode(text).decode(), base64.b64decode(buttons).decode().split("\n")))
                    run.stdin.write((answers.pop(0) if answers else "") + "\n")
                    run.stdin.flush()
                elif kind == "PULSAR_STATUS":
                    statuses.append(base64.b64decode(rest).decode())
                else:
                    self.fail(f"the launcher said something the window does not read: {line!r}")
            return run.wait(timeout=60), dialogs, statuses

    def test_a_dialog_is_asked_of_the_window_and_its_answer_read_back(self):
        app = make_app(self.dir, "1.6.0", None, launcher=True, pub=self.pub)  # not signed: nothing to install
        code, dialogs, _ = self.converse(app, ["OK"])
        self.assertEqual(code, 1)
        self.assertEqual(len(dialogs), 1)
        self.assertIn("not a signed release", dialogs[0][0])
        self.assertEqual(dialogs[0][1], ["Show log", "OK"])

    def test_after_installing_the_developer_tools_it_goes_on_by_itself(self):
        app = make_app(self.dir, "1.6.0", None, launcher=True, pub=self.pub)
        fake, count, asked = (os.path.join(self.dir, n) for n in ("xcode-select", "count", "asked"))
        with open(fake, "w", encoding="utf-8") as fh:  # not there for two looks, then there
            fh.write(f'#!/bin/bash\nif [ "$1" = --install ]; then touch "{asked}"; exit 0; fi\n'
                     f'n=$(cat "{count}" 2>/dev/null || echo 0); echo $((n+1)) > "{count}"; [ "$n" -ge 3 ]\n')
        os.chmod(fake, 0o755)
        code, dialogs, statuses = self.converse(app, ["Install", "OK"], PULSAR_TEST_DIALOGS=os.path.join(self.dir, "d.txt"),
                                                PULSAR_TEST_XCODE_SELECT=fake, PULSAR_TEST_POLL="1")
        self.assertTrue(os.path.exists(asked))
        self.assertIn("developer tools", dialogs[0][0])
        self.assertEqual(dialogs[0][1], ["Not now", "Install"])
        self.assertGreaterEqual(len([s for s in statuses if "developer tools" in s]), 1)
        self.assertEqual(statuses[-1], "Starting Pulsar…")
        self.assertIn("not a signed release", dialogs[1][0])  # it went on: to the next step, and its own dialog
        self.assertEqual(code, 1)

    def test_not_now_stops_there(self):
        app = make_app(self.dir, "1.6.0", None, launcher=True, pub=self.pub)
        fake = os.path.join(self.dir, "xcode-select")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/bash\nexit 1\n")
        os.chmod(fake, 0o755)
        code, dialogs, statuses = self.converse(app, ["Not now"], PULSAR_TEST_DIALOGS=os.path.join(self.dir, "d.txt"),
                                                PULSAR_TEST_XCODE_SELECT=fake, PULSAR_TEST_POLL="1")
        self.assertEqual((code, len(dialogs), statuses), (0, 1, []))

    def test_the_server_is_told_which_app_started_it(self):
        told = os.path.join(self.dir, "told")
        main = f"import os, sys\nopen({told!r}, 'w').write(os.environ.get('PULSAR_APP_BUNDLE', ''))\nsys.exit(1)\n"
        app = make_app(self.dir, "1.6.0", self.key, launcher=True, pub=self.pub, main=main)
        code, dialogs, _ = self.converse(app, ["OK", "OK"])
        with open(told, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), os.path.realpath(app))
        self.assertTrue(dialogs and "could not start" in dialogs[-1][0])

    def test_the_server_holds_none_of_the_windows_pipe(self):
        """The window reads the launcher's output to its end: a server (or anything it starts) that kept the window's
        pipe open would leave the window waiting for ever."""
        pidfile = os.path.join(self.dir, "pid")
        main = ("import subprocess, sys\n"
                f"p = subprocess.Popen(['/bin/sleep', '30'], close_fds=False)\nopen({pidfile!r}, 'w').write(str(p.pid))\n"
                "sys.exit(1)\n")
        app = make_app(self.dir, "1.6.0", self.key, launcher=True, pub=self.pub, main=main)
        self.addCleanup(lambda: os.path.exists(pidfile) and os.kill(int(open(pidfile).read()), 9))
        t0 = time.time()
        self.converse(app, ["OK", "OK"])
        self.assertLess(time.time() - t0, 25)  # not the sleeper's 30 seconds


if __name__ == "__main__":
    unittest.main()
