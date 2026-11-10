"""Save sets ActivityWatch's away time to Away after (server.sync_activitywatch, awsetup.fix both ways) beyond
tests/test_awsetup.py: with the real file change on a temporary copy and the restart faked, never the real
ActivityWatch.

    python3 -m unittest discover -s tests
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH, refused  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import awsetup, config, server  # noqa: E402


class SyncTest(unittest.TestCase):
    """ActivityWatch's support folder in a temporary folder; the refusal lifted and the restart faked."""

    def setUp(self):
        self.support = tempfile.mkdtemp()
        self.toml = os.path.join(self.support, "aw-watcher-afk", "aw-watcher-afk.toml")
        self.restarts, self.records = [], []
        self.restart_ok = True
        for p in (mock.patch.object(awsetup, "SUPPORT", self.support), mock.patch.object(awsetup, "TOML", self.toml),
                  mock.patch.object(awsetup, "PLACES", ()), mock.patch.object(awsetup, "blocked", lambda: None),
                  mock.patch.object(awsetup, "restart", self.fake_restart),
                  mock.patch.object(server.logbook, "record", lambda *a, **k: self.records.append(a))):
            p.start()
            self.addCleanup(p.stop)

    def fake_restart(self, base, *a, **k):
        self.restarts.append(base)
        return {"ok": self.restart_ok, "message": "Restarted." if self.restart_ok else "It did not come back."}

    def timeout(self, seconds):
        os.makedirs(os.path.dirname(self.toml), exist_ok=True)
        with open(self.toml, "w") as fh:
            fh.write(f"[aw-watcher-afk]\ntimeout = {seconds}\n")

    def save(self, minutes):
        return server.sync_activitywatch({**config.DEFAULTS, "away_after": minutes})

    def test_both_ways_and_then_nothing_more_to_change(self):
        for before, minutes in ((180, 15), (1800, 10)):  # shorter than Away after, then longer
            self.timeout(before)
            self.assertTrue(awsetup.status(minutes)["differs"])
            got = self.save(minutes)
            self.assertEqual((got["ok"], got["changed"]), (True, True), got)
            self.assertEqual(awsetup.read_timeout()["seconds"], minutes * 60)
            self.assertTrue(os.path.exists(got["backup"]))
            self.assertFalse(awsetup.status(minutes)["differs"])
            self.assertIsNone(self.save(minutes))  # the next Save: the same already, no restart
        self.assertEqual(len(self.restarts), 2)
        self.assertEqual(self.records, [])

    def test_not_installed_or_a_file_it_cannot_read_is_never_touched(self):
        os.rmdir(self.support)  # no ActivityWatch on this Mac
        self.assertIsNone(self.save(15))
        self.timeout('"twenty"')
        self.assertIsNone(self.save(15))
        with open(self.toml) as fh:
            self.assertIn('"twenty"', fh.read())
        self.assertEqual(self.restarts, [])

    def test_a_restart_that_failed_after_the_change_is_logged(self):
        self.timeout(1200)
        self.restart_ok = False
        got = self.save(15)
        self.assertEqual((got["ok"], got["changed"]), (False, True))
        self.assertEqual([r[0] for r in self.records], ["activitywatch"])
        self.assertIn("It did not come back.", self.records[0][1])

    # fixed 2026-11-10: Save raises ValueError when ActivityWatch has no aw-watcher-afk.toml yet
    def test_save_with_activitywatch_but_no_timeout_file_never_fails(self):
        # installed (its support folder is there) but its AFK watcher's file is not written yet: read_timeout says 180
        # seconds, differs() says fix it, and set_timeout refuses a file that does not exist with a ValueError that fix()
        # does not catch: api_settings answers 400 after the settings were saved, on every Save (and Settings offers a
        # Fix that fails the same way). Before differs(), 180 seconds was never longer than Away after: never touched
        self.assertFalse(os.path.exists(self.toml))
        got = self.save(15)
        self.assertTrue(got is None or got["ok"] is False, got)


class SaveTest(unittest.TestCase):
    def test_save_answers_with_what_activitywatch_did(self):
        folder = tempfile.mkdtemp()
        seen = []
        answer = {"ok": True, "changed": True, "message": "ActivityWatch follows."}
        with mock.patch.object(config, "CONFIG_DIR", folder), \
                mock.patch.object(config, "CACHE_DIR", os.path.join(folder, "cache")), \
                mock.patch.object(config, "SETTINGS_FILE", os.path.join(folder, "settings.json")), \
                mock.patch.object(config, "TOKEN_FILE", os.path.join(folder, "token")), \
                mock.patch.object(config, "_keychain", lambda: False), \
                mock.patch.object(server, "sync_activitywatch", lambda s: seen.append(s["away_after"]) or answer):
            got = server.api_settings({"away_after": 20})
        self.assertEqual(got["activitywatch"], answer)
        self.assertEqual(seen, [20])  # with the settings just saved


if __name__ == "__main__":
    unittest.main()
