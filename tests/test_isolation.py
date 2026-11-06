"""The guard every test runs under (tests/isolation.py): nothing real is reachable, whatever the code under test does.

    python3 -m unittest discover -s tests
"""

import os
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import isolation  # noqa: E402  first: nothing real is reachable (isolation.py)

from pulsar import config, productive, trackers, updater  # noqa: E402


class IsolationTest(unittest.TestCase):
    def test_every_path_pulsar_reads_or_writes_is_inside_the_runs_scratch_folder(self):
        scratch = os.path.realpath(isolation.SCRATCH)
        for path in (os.path.expanduser("~"), tempfile.gettempdir(), tempfile.mkdtemp(), config.CONFIG_DIR, config.CACHE_DIR,
                     trackers.MEMTIME_DB, updater.ROOT, productive.os.path.expanduser("~/.claude.json")):
            self.assertTrue(os.path.realpath(path).startswith(scratch + os.sep), path)
        self.assertEqual(os.environ["TMPDIR"], tempfile.gettempdir())  # so programs a test starts write there too

    def test_the_keychain_cannot_be_asked_even_where_the_code_catches_everything(self):
        with isolation.refused() as seen:
            with self.assertRaises(isolation.RealWorld):
                subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "pulsar", "-w"], capture_output=True)
            with mock.patch.object(config, "_keychain", lambda: True), mock.patch.object(config, "_token_cache", [0.0, None, False]):
                with self.assertRaises(isolation.RealWorld):
                    config.get_token()
            with mock.patch.object(productive, "_cc_cache", [0.0, None]), self.assertRaises(isolation.RealWorld):
                productive.claude_code_token()
            try:
                subprocess.run("claude -p hello", shell=True)
            except Exception:  # noqa: BLE001 - the point: code that catches Exception does not swallow it
                self.fail("RealWorld must not be an Exception")
            except isolation.RealWorld:
                pass
        self.assertEqual(len(seen), 4)
        self.assertTrue(all("security" in x or "claude" in x for x in seen), seen)

    def test_no_connection_leaves_and_none_reaches_a_server_the_test_did_not_start(self):
        with isolation.refused() as seen:
            with self.assertRaises(isolation.RealWorld):
                urllib.request.urlopen("https://api.productive.io/api/v2/time_entries", timeout=1)
            with self.assertRaises(isolation.RealWorld):
                socket.create_connection(("1.1.1.1", 443), timeout=1)
            for port in (5600, 5610, 5620):  # ActivityWatch, Pulsar, Pulsar Preview
                with self.assertRaises(isolation.RealWorld), socket.socket() as s:
                    s.connect(("127.0.0.1", port))
        self.assertEqual(len(seen), 5)
        with socket.socket() as server:  # a server of its own is fine
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            with socket.create_connection(server.getsockname(), timeout=2):
                pass

    def test_a_restart_into_another_program_is_refused(self):
        with isolation.refused() as seen, self.assertRaises(isolation.RealWorld):
            os.execv(sys.executable, [sys.executable, "-c", "pass"])
        self.assertEqual(seen, ["os.execv"])

    def test_nothing_was_refused_outside_these_tests(self):
        """What a test reached for is listed here as it happens; the run also exits 1 at the end if any."""
        self.assertEqual(isolation.TRIED, [])


if __name__ == "__main__":
    unittest.main()
