"""Signing in to GitHub, so updates keep arriving from a private repository (pulsar/github.py): which links go through
GitHub's API, that the token never reaches the storage a download is redirected to, the device flow, the access check,
and what the update check says when the repository is private. GitHub is faked; nothing leaves the computer."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

import io  # noqa: E402
import json  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from unittest import mock  # noqa: E402

from pulsar import config, github, updater  # noqa: E402

TOKEN = "ghu_" + "a" * 36
REL = "https://github.com/fosterinteractive/pulsar/releases/"
ASSET = github.ASSETS + "77"


def http(code):
    return urllib.error.HTTPError("https://api.github.com/x", code, "no", {}, None)  # type: ignore[arg-type]


class Base(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch.object(github, "CLIENT_ID", "Iv23test"), mock.patch.object(github, "_sleep", lambda s: None)]
        for p in self.patches:
            p.start()
        config._github_cache[2] = False
        for path in (config.GITHUB_TOKEN_FILE, github._state_path()):
            if os.path.exists(path):
                os.remove(path)
        github._flow.clear()
        github._flow["gen"] = 0

    def tearDown(self):
        for p in self.patches:
            p.stop()
        config._github_cache[2] = False

    def sign_in(self):
        config.set_github_token(TOKEN)


class LinksTest(Base):
    def test_only_pulsars_own_release_links_go_through_the_api(self):
        for url in (updater.DEFAULT_MANIFEST, updater.DEFAULT_MANIFEST + ".sig", REL + "download/v1.6.15/pulsar-1.6.15.zip",
                    REL + "download/v1.6.15/Pulsar.zip", REL + "download/v1.6.15/Pulsar.zip.sig"):
            self.assertTrue(github.handles(url), url)
        for url in ("https://github.com/someone/pulsar/releases/latest/download/latest.json",
                    REL + "download/v1.6.15/../../x", REL + "download/main/Pulsar.zip", REL + "latest/download/a?b=c",
                    "http://github.com/fosterinteractive/pulsar/releases/latest/download/latest.json",
                    "https://example.invalid/latest.json"):
            self.assertFalse(github.handles(url), url)

    def test_a_token_is_offered_only_when_signed_in_and_set_up(self):
        self.assertIsNone(github.token_for(updater.DEFAULT_MANIFEST))
        self.sign_in()
        self.assertEqual(github.token_for(updater.DEFAULT_MANIFEST), TOKEN)
        self.assertIsNone(github.token_for("https://example.invalid/latest.json"))
        with mock.patch.object(github, "CLIENT_ID", ""):
            self.assertIsNone(github.token_for(updater.DEFAULT_MANIFEST))


class FetchTest(Base):
    def calls(self, release=None, error=None):
        seen = []

        def call(url, data=None, token=None, accept="", limit=0, timeout=0, raw=False):
            seen.append((url, token, accept, raw))
            if error:
                raise error
            if raw:
                return b"zip bytes"
            return release if release is not None else {"assets": [{"name": "Pulsar.zip", "url": ASSET}]}
        return seen, call

    def test_the_latest_release_then_its_file_with_the_token(self):
        seen, call = self.calls({"assets": [{"name": "latest.json", "url": ASSET}]})
        with mock.patch.object(github, "_call", call):
            self.assertEqual(github.fetch(updater.DEFAULT_MANIFEST, 1000, 10, TOKEN), b"zip bytes")
        self.assertEqual(seen[0][:2], (github.API + "/repos/fosterinteractive/pulsar/releases/latest", TOKEN))
        self.assertEqual(seen[1], (ASSET, TOKEN, "application/octet-stream", True))
        self.assertTrue(github._state().get("worked"))

    def test_a_tagged_release_is_read_by_its_tag(self):
        seen, call = self.calls()
        with mock.patch.object(github, "_call", call):
            github.fetch(REL + "download/v1.6.15/Pulsar.zip", 1000, 10, TOKEN)
        self.assertTrue(seen[0][0].endswith("/releases/tags/v1.6.15"))

    def test_a_missing_file_or_a_foreign_asset_link_is_not_found(self):
        for release in ({"assets": []}, {"assets": [{"name": "Pulsar.zip", "url": "https://evil.example/77"}]}):
            _, call = self.calls(release)
            with mock.patch.object(github, "_call", call), self.assertRaises(urllib.error.HTTPError) as got:
                github.fetch(REL + "download/v1.6.15/Pulsar.zip", 1000, 10, TOKEN)
            self.assertEqual(got.exception.code, 404)

    def test_a_refused_token_is_a_sign_in_problem_and_remembered(self):
        _, call = self.calls(error=http(401))
        with mock.patch.object(github, "_call", call), self.assertRaises(github.SignInProblem):
            github.fetch(updater.DEFAULT_MANIFEST, 1000, 10, TOKEN)
        self.assertEqual(github._state().get("problem"), "refused")


class TransportTest(Base):
    def test_the_token_goes_to_the_api_only_and_never_on_to_a_redirect(self):
        sent = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=0):
            sent.append(req)
            return Resp(b'{"ok": 1}')
        with mock.patch.object(config, "tests_offline", lambda: False), mock.patch.object(urllib.request, "urlopen", urlopen):
            github._call(github.API + "/repos/fosterinteractive/pulsar/releases/latest", token=TOKEN)
            with self.assertRaises(ValueError):
                github._call("https://github.com/login/oauth/access_token", token=TOKEN)
        req = sent[0]
        self.assertEqual(req.unredirected_hdrs.get("Authorization"), f"Bearer {TOKEN}")
        self.assertNotIn("Authorization", req.headers)
        # what urllib does on GitHub's 302 to its file storage: the new request carries no token
        moved = urllib.request.HTTPRedirectHandler().redirect_request(
            req, io.BytesIO(), 302, "Found", {}, "https://objects.githubusercontent.com/file?sig=x")
        self.assertIsNotNone(moved)
        self.assertNotIn("Authorization", {**moved.headers, **moved.unredirected_hdrs})

    def test_tests_never_reach_github(self):
        with self.assertRaises(OSError):
            github._call(github.API + "/user", token=TOKEN)


class SignInTest(Base):
    def flow(self, answers):
        """start() with GitHub's device code, then _wait over `answers` (one per poll), run here, not in a thread."""
        polls = list(answers)

        def call(url, data=None, token=None, accept="", limit=0, timeout=0, raw=False):
            if url == github.DEVICE_URL:
                return {"user_code": "ABCD-1234", "device_code": "dev", "verification_uri": "https://github.com/login/device",
                        "expires_in": 900, "interval": 5}
            if url == github.TOKEN_URL:
                a = polls.pop(0)
                if isinstance(a, Exception):
                    raise a
                return a
            raise AssertionError(url)
        with mock.patch.object(github, "_call", call), mock.patch.object(github.threading, "Thread") as thread, \
                mock.patch.object(github, "check", lambda token=None: github._note(login="dana", access=True, checked=1)):
            got = github.start()
            self.assertEqual(got, {"code": "ABCD-1234", "url": "https://github.com/login/device"})
            self.assertEqual(github.status()["pending"]["code"], "ABCD-1234")
            args = thread.call_args.kwargs["args"]
            github._wait(*args)
        return polls

    def test_waiting_slowing_down_and_a_network_hiccup_then_the_token_is_kept(self):
        left = self.flow([{"error": "authorization_pending"}, {"error": "slow_down", "interval": 10}, OSError("offline"),
                          {"access_token": TOKEN, "token_type": "bearer"}])
        self.assertEqual(left, [])
        self.assertEqual(config.get_github_token(), TOKEN)
        s = github.status()
        self.assertTrue(s["signedIn"])
        self.assertIsNone(s["pending"])
        self.assertEqual((s["login"], s["access"], s["warn"]), ("dana", True, False))
        self.assertNotIn(TOKEN, json.dumps(s))  # never the token

    def test_an_expiring_token_says_the_app_is_set_up_wrong(self):
        self.flow([{"access_token": TOKEN, "expires_in": 28800, "refresh_token": "ghr_" + "b" * 60}])
        s = github.status()
        self.assertTrue(s["signedIn"])
        self.assertIn("Expire user authorization tokens", s["message"])
        self.assertTrue(s["warn"])

    def test_a_cancelled_sign_in_says_so_and_keeps_nothing(self):
        self.flow([{"error": "access_denied"}])
        s = github.status()
        self.assertFalse(s["signedIn"])
        self.assertIn("cancelled", s["message"])
        self.assertTrue(s["warn"])

    def test_a_newer_click_ends_the_older_wait(self):
        github._flow.update(gen=5)
        with mock.patch.object(github, "_call", side_effect=AssertionError("must not poll")):
            github._wait("dev", 5, 9e12, 4)  # gen 4 is stale

    def test_not_set_up_offers_nothing(self):
        with mock.patch.object(github, "CLIENT_ID", ""):
            self.assertFalse(github.status()["available"])
            with self.assertRaises(ValueError):
                github.start()

    def test_sign_out_forgets_the_token_and_the_account(self):
        self.sign_in()
        github._note(login="dana", access=True)
        github.sign_out()
        self.assertIsNone(config.get_github_token())
        self.assertEqual(github.status()["login"], "")


class AccessTest(Base):
    def answer(self, installs, repos=(), error=None):
        def call(url, data=None, token=None, accept="", limit=0, timeout=0, raw=False):
            if error:
                raise error
            if url.endswith("/user"):
                return {"login": "dana"}
            if "/user/installations?" in url:
                return {"installations": installs}
            if "/repositories" in url:
                return {"repositories": [{"full_name": r} for r in repos]}
            raise AssertionError(url)
        return call

    def test_the_repository_in_the_apps_list_means_access(self):
        self.sign_in()
        with mock.patch.object(github, "_call", self.answer([{"id": 9, "account": {"login": "fosterinteractive"}}],
                                                            ["fosterinteractive/pulsar"])):
            github.check()
        s = github.status()
        self.assertEqual((s["access"], s["warn"]), (True, False))
        self.assertIn("Signed in as dana", s["message"])

    def test_no_installation_or_no_repository_means_no_access_and_says_whom_to_ask(self):
        self.sign_in()
        for installs, repos in (([], ()), ([{"id": 9, "account": {"login": "fosterinteractive"}}], ["fosterinteractive/other"])):
            with mock.patch.object(github, "_call", self.answer(installs, repos)):
                github.check()
            s = github.status()
            self.assertEqual((s["access"], s["warn"]), (False, True))
            self.assertIn("fosterinteractive/pulsar", s["message"])
            self.assertIn("access", github.update_hint())

    def test_a_refused_token_asks_to_sign_in_again(self):
        self.sign_in()
        with mock.patch.object(github, "_call", self.answer([], error=http(401))):
            github.check()
        self.assertIn("again", github.status()["message"])
        self.assertIn("again", github.update_hint())

    def test_once_a_day_only(self):
        self.sign_in()
        github._note(checked=__import__("time").time())
        with mock.patch.object(github, "check", side_effect=AssertionError("too soon")):
            github.maybe_recheck()
        github._note(checked=0)
        with mock.patch.object(github, "check") as check:
            github.maybe_recheck()
        check.assert_called_once()


class UpdaterTest(Base):
    def test_signed_in_the_api_first_and_the_public_link_when_it_fails(self):
        self.sign_in()
        for error in (github.SignInProblem("no"), http(404), OSError("api down")):
            with mock.patch.object(github, "fetch", side_effect=error), \
                    mock.patch.object(updater, "_anonymous", return_value=b"public") as pub:
                self.assertEqual(updater._fetch(updater.DEFAULT_MANIFEST, 10, 5), b"public")
            pub.assert_called_once()
        with mock.patch.object(github, "fetch", return_value=b"api"), \
                mock.patch.object(updater, "_anonymous", side_effect=AssertionError("not needed")):
            self.assertEqual(updater._fetch(updater.DEFAULT_MANIFEST, 10, 5), b"api")
        with mock.patch.object(github, "fetch", side_effect=http(500)), self.assertRaises(urllib.error.HTTPError):
            updater._fetch(updater.DEFAULT_MANIFEST, 10, 5)

    def test_not_signed_in_only_the_public_link(self):
        with mock.patch.object(github, "fetch", side_effect=AssertionError("no token, no API")), \
                mock.patch.object(updater, "_anonymous", return_value=b"public"):
            self.assertEqual(updater._fetch(updater.DEFAULT_MANIFEST, 10, 5), b"public")

    def test_a_private_repository_asks_an_installed_copy_to_sign_in(self):
        def gone(*a, **k):
            raise http(404)
        with mock.patch.object(updater, "fetch", gone), mock.patch.object(updater, "installed", lambda root=None: True):
            u = updater.Updater(root=tempfile.mkdtemp())
            got = u.check()
        self.assertEqual(got["status"], "signin")
        self.assertIn("sign in to GitHub", got["message"])
        self.assertFalse(got["github"]["signedIn"])


if __name__ == "__main__":
    unittest.main()
