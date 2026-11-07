"""Settings' "Away after": the one setting behind every away rule (the shortest away shown, the end of a sitting in
Likely and Log the day, and the pauses that count as work), on made-up days and made-up ActivityWatch events.

    python3 -m unittest discover -s tests
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import config, dayplan, server, sessions, trackers  # noqa: E402
from test_dayplan import day, history_for, row, work  # noqa: E402


def saved(settings: dict):
    """config.load() answering these settings, so nothing is written to the run's settings file."""
    return mock.patch.object(config, "load", lambda: {**config.DEFAULTS, **settings})


class AwayAfterTest(unittest.TestCase):

    def test_the_setting_is_10_15_20_or_30_and_15_by_default(self):
        self.assertEqual(config.away_after({}), 15)
        for v in (10, 15, 20, 30):
            self.assertEqual(config.away_after({"away_after": v}), v)
        for bad in (5, 25, "15", True, None, 15.5):
            self.assertEqual(config.away_after({"away_after": bad}), 15, bad)
        self.assertEqual(server.validate({"away_after": "20"}), {"away_after": 20})
        for bad in (5, "x", True, None, 45):
            with self.assertRaises(ValueError):
                server.validate({"away_after": bad})
        self.assertTrue(config._fits("away_after", 30))
        self.assertFalse(config._fits("away_after", 25))

    def test_it_is_the_shortest_away_shown(self):
        items = [work(540, 600), work(612, 640), work(660, 700)]  # pauses of 12 and 20 minutes
        with saved({"away_after": 10}):
            self.assertEqual([(g["s"], g["e"]) for g in server.away(items)], [(600, 612), (640, 660)])
        with saved({"away_after": 15}):
            self.assertEqual([(g["s"], g["e"]) for g in server.away(items)], [(640, 660)])
        with saved({"away_after": 30}):
            self.assertEqual(server.away(items), [])

    def test_it_ends_a_sitting_in_log_the_day(self):
        ctx = history_for({"Fix the importer": "11"})
        d = day(items=[work(540, 560), work(582, 600)], rows=[row(540, 560), row(582, 600)])  # a 22-minute pause
        apart = dayplan.plan(d, ctx, dayplan.opts_for({"away_after": 15}))
        together = dayplan.plan(d, ctx, dayplan.opts_for({"away_after": 30}))
        self.assertEqual([(e["s"], e["e"]) for e in apart["entries"]], [(540, 560), (580, 595)])  # two sittings
        self.assertEqual([(e["s"], e["e"]) for e in together["entries"]], [(540, 575)])          # one: one entry
        self.assertEqual(dayplan.opts_for({"away_after": 20})["sitting_gap"], 20)
        self.assertEqual(dayplan.opts_for({})["sitting_gap"], dayplan.SITTING_GAP)

    def test_it_ends_a_sitting_in_likely(self):
        root = tempfile.mkdtemp()
        repo = os.path.join(root, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        with open(os.path.join(repo, ".git", "config"), "w") as fh:
            fh.write('[remote "origin"]\n\turl = git@github.com:acme/acme-site.git\n')
        projects = os.path.join(root, "projects", "-acme")
        os.makedirs(projects)
        t0 = datetime(2026, 11, 4, tzinfo=timezone.utc).timestamp()
        at = lambda m: datetime.fromtimestamp(t0 + m * 60, timezone.utc).isoformat().replace("+00:00", "Z")  # noqa: E731
        lines = [{"type": "custom-title", "customTitle": "Fix the checkout form"},
                 {"type": "user", "timestamp": at(10), "cwd": repo, "origin": {"kind": "human"}, "message": {"content": "Go"}},
                 {"type": "assistant", "timestamp": at(11), "message": {"content": [
                     {"type": "tool_use", "name": "Edit", "input": {"file_path": os.path.join(repo, "a.js")}}]}},
                 {"type": "assistant", "timestamp": at(15), "message": {"content": [{"type": "text", "text": "Done."}]}}]
        with open(os.path.join(projects, "abcd1234.jsonl"), "w") as fh:
            fh.write("\n".join(json.dumps(x) for x in lines) + "\n")
        os.utime(os.path.join(projects, "abcd1234.jsonl"), (t0 + 3600, t0 + 3600))
        # the session's editor tab, then, after a 21-minute pause, a page that names no project
        items = [{"s": 9, "e": 20, "app": "Cursor", "title": "Fix the checkout form", "host": "", "url": "", "cat": "work"},
                 {"s": 41, "e": 50, "app": "Google Chrome", "title": "Some docs", "host": "", "url": "", "cat": "work"}]
        mins = lambda got: sum(e["minutes"] for e in got["entries"])  # noqa: E731
        folder = os.path.dirname(projects)
        sessions._repo_of.clear()
        self.assertEqual(mins(sessions.propose(t0, t0 + 86400, items, "ActivityWatch", folder, away=30)), 20)  # one sitting
        self.assertEqual(mins(sessions.propose(t0, t0 + 86400, items, "ActivityWatch", folder, away=15)), 11)  # the page: none
        with saved({"away_after": 30}):
            self.assertEqual(mins(sessions.propose(t0, t0 + 86400, items, "ActivityWatch", folder)), 20)
        with saved({"away_after": 15}):
            self.assertEqual(mins(sessions.propose(t0, t0 + 86400, items, "ActivityWatch", folder)), 11)


class ShortPauseIsWorkTest(unittest.TestCase):
    """A pause ActivityWatch recorded that is shorter than Away after, between two stretches of input, is work, as
    in the fi-time-from-activity skill: the window in front then counts."""

    T0 = datetime(2026, 11, 4, tzinfo=timezone.utc).timestamp()

    def ev(self, minute, minutes, status):
        stamp = datetime.fromtimestamp(self.T0 + minute * 60, timezone.utc).isoformat()
        return {"timestamp": stamp, "duration": minutes * 60, "data": {"status": status}}

    def spans(self, events, away):
        return [((a - self.T0) / 60, (b - self.T0) / 60) for a, b in trackers.present_spans(events, away * 60)]

    def test_a_short_pause_between_two_stretches_of_input_is_work(self):
        events = [self.ev(540, 30, "not-afk"), self.ev(570, 12, "afk"), self.ev(582, 18, "not-afk"),
                  self.ev(600, 25, "afk"), self.ev(625, 5, "not-afk")]
        self.assertEqual(self.spans(events, 15), [(540, 600), (625, 630)])   # the 12-minute pause is work
        self.assertEqual(self.spans(events, 10), [(540, 570), (582, 600), (625, 630)])
        self.assertEqual(self.spans(events, 30), [(540, 630)])
        self.assertEqual(self.spans(list(reversed(events)), 15), [(540, 600), (625, 630)])  # ActivityWatch's order: newest first

    def test_a_pause_at_either_end_or_next_to_another_pause_stays_away(self):
        events = [self.ev(500, 5, "afk"), self.ev(505, 30, "not-afk"), self.ev(535, 5, "afk"), self.ev(540, 5, "afk"),
                  self.ev(545, 10, "not-afk"), self.ev(555, 5, "afk")]
        self.assertEqual(self.spans(events, 15), [(505, 535), (545, 555)])

    def test_the_window_in_front_during_the_pause_is_counted_and_away_follows_the_setting(self):
        afk = [self.ev(540, 30, "not-afk"), self.ev(570, 12, "afk"), self.ev(582, 18, "not-afk")]
        window = [{"timestamp": datetime.fromtimestamp(self.T0 + 540 * 60, timezone.utc).isoformat(), "duration": 3600,
                   "data": {"app": "Google Chrome", "title": "Reading the spec", "url": "https://example.com/spec"}}]

        def http_json(url, timeout=0):
            if url.endswith("/buckets/"):
                return {"aw-watcher-window_mac": {}, "aw-watcher-afk_mac": {}}
            return window if "window" in url else afk
        with mock.patch.object(trackers, "http_json", http_json), mock.patch.object(trackers, "program_names", lambda: {}):
            read = lambda away: trackers.activity_aw("http://localhost:5600", self.T0, self.T0 + 86400,  # noqa: E731
                                                     trackers.classifier([]), away)
            self.assertEqual([(x["s"], x["e"]) for x in read(15)], [(540, 600)])  # the reading counts
            self.assertEqual([(x["s"], x["e"]) for x in read(10)], [(540, 570), (582, 600)])
            self.assertEqual(server.away(read(10), 10), [{"s": 570, "e": 582, "off": False, "kind": "pause"}])
            self.assertEqual(server.away(read(15), 15), [])
            with saved({"away_after": 10}):  # without `away`, the saved setting
                self.assertEqual(len(trackers.both(self.T0, self.T0 + 86400, "http://localhost:5600",
                                                   trackers.classifier([]))["aw"]), 2)



class OpenAwayTest(unittest.TestCase):
    """server.away with `until` (today, now): a pause still going on shows once it is "Away after" long."""

    def test_a_pause_until_now_shows(self):
        from pulsar import server
        items = [{"s": 600, "e": 660}]
        got = server.away(items, 15, until=690)
        self.assertEqual(got, [{"s": 660, "e": 690, "off": False, "open": True, "kind": "pause"}])
        self.assertEqual(server.away(items, 15, until=760)[0]["kind"], "away")  # an hour or more
        self.assertEqual(server.away(items, 15, until=670), [])  # not long enough yet
        self.assertEqual(server.away(items, 15), [])             # another day: only between activities

    def test_memtime_is_cut_where_activitywatch_saw_nobody(self):
        from pulsar import trackers
        raw = {"afk": [{"timestamp": "2026-11-07T10:00:00+00:00", "duration": 1800, "data": {"status": "afk"}},
                       {"timestamp": "2026-11-07T11:00:00+00:00", "duration": 300, "data": {"status": "afk"}}]}
        t0 = trackers.parse_ts("2026-11-07T00:00:00+00:00")
        gaps = trackers.aw_away(raw, t0, 15)
        self.assertEqual(gaps, [(600.0, 630.0)])  # the 5-minute one is a pause, not cut
        items = [{"s": 590.0, "e": 640.0, "app": "Cursor"}]
        self.assertEqual([(x["s"], x["e"]) for x in trackers.cut_out(items, gaps)], [(590.0, 600.0), (630.0, 640.0)])

if __name__ == "__main__":
    unittest.main()
