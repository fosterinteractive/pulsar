"""The calendar source (pulsar/calendar.py) on made-up iCalendar text: no network, no keychain.

    python3 -m unittest discover -s tests
"""

import gzip
import os
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Never the real settings, caches or log: point them at a scratch folder before importing.
_SCRATCH = tempfile.mkdtemp(prefix="pulsar-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_SCRATCH, "config")
os.environ["XDG_CACHE_HOME"] = os.path.join(_SCRATCH, "cache")

from zoneinfo import ZoneInfo  # noqa: E402

from pulsar import calendar, config, server  # noqa: E402

TORONTO, KYIV = ZoneInfo("America/Toronto"), ZoneInfo("Europe/Kyiv")
ADDRESS = "https://calendar.example.com/ical/someone%40example.com/private-0123abcd/basic.ics"


def ics(*events, head=()):
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Made up//EN", *head, *events, "END:VCALENDAR"]) + "\r\n"


def vevent(*lines):
    return "\r\n".join(["BEGIN:VEVENT", *lines, "END:VEVENT"])


def on(text, day, tz=TORONTO):
    return calendar.events_on(calendar.build(calendar.parse(text), tz), day, tz)


def spans(text, day, tz=TORONTO):
    return [(x["s"], x["e"]) for x in on(text, day, tz)]


class ReadTest(unittest.TestCase):
    def test_folded_lines_escapes_and_quoted_parameters(self):
        text = ics(vevent("UID:a1", 'DTSTART;TZID="America/Toronto":20261105T100000',
                          "DTEND;TZID=America/Toronto:20261105T103000",
                          "SUMMARY:Design review\\, round two for the", "  next sprint",
                          "LOCATION:Room 4", "DESCRIPTION:Agenda\\nfirst item",
                          "BEGIN:VALARM", "SUMMARY:Reminder text", "TRIGGER:-PT10M", "END:VALARM"))
        [ev] = on(text, "2026-11-05")
        self.assertEqual(ev["title"], "Design review, round two for the next sprint")  # not the reminder's
        self.assertEqual((ev["s"], ev["e"], ev["all_day"], ev["recurring"]), (600, 630, False, False))
        self.assertEqual(ev["location"], "Room 4")
        self.assertNotIn("description", ev)  # descriptions carry passcodes and notes: never sent to the page

    def test_a_meeting_in_another_zone_lands_on_the_viewers_clock(self):
        text = ics(vevent("UID:a2", "DTSTART;TZID=America/Toronto:20261105T093000",
                          "DTEND;TZID=America/Toronto:20261105T100000", "SUMMARY:Standup"),
                   vevent("UID:a3", "DTSTART:20261105T120000Z", "DURATION:PT15M", "SUMMARY:Check-in"))
        self.assertEqual(spans(text, "2026-11-05", KYIV), [(840, 855), (990, 1020)])  # 12:00Z, 09:30 in Toronto

    def test_outlook_zone_names_floating_times_and_cancelled_events(self):
        text = ics(vevent("UID:b1", "DTSTART;TZID=Eastern Standard Time:20261105T140000",
                          "DTEND;TZID=Eastern Standard Time:20261105T143000", "SUMMARY:Outlook meeting"),
                   vevent("UID:b2", "DTSTART:20261105T080000", "DTEND:20261105T081500", "SUMMARY:Floating"),
                   vevent("UID:b3", "DTSTART;TZID=America/Toronto:20261105T150000", "DTEND;TZID=America/Toronto:20261105T160000",
                          "STATUS:CANCELLED", "SUMMARY:Called off"))
        self.assertEqual([(x["title"], x["s"]) for x in on(text, "2026-11-05")],
                         [("Floating", 480), ("Outlook meeting", 840)])

    def test_whole_day_events_cover_each_day_up_to_their_end(self):
        text = ics(vevent("UID:c1", "DTSTART;VALUE=DATE:20261104", "DTEND;VALUE=DATE:20261106", "SUMMARY:Conference"),
                   vevent("UID:c2", "DTSTART;VALUE=DATE:20261105", "SUMMARY:Holiday"))
        self.assertEqual(on(text, "2026-11-03"), [])
        self.assertEqual([x["title"] for x in on(text, "2026-11-04")], ["Conference"])
        both = on(text, "2026-11-05")
        self.assertEqual(sorted(x["title"] for x in both), ["Conference", "Holiday"])
        self.assertTrue(all(x["all_day"] and (x["s"], x["e"]) == (0, 1440) for x in both))
        self.assertEqual(on(text, "2026-11-06"), [])  # DTEND is the first day after

    def test_an_event_across_midnight_shows_on_both_days(self):
        text = ics(vevent("UID:d1", "DTSTART;TZID=America/Toronto:20261105T230000",
                          "DTEND;TZID=America/Toronto:20261106T010000", "SUMMARY:Release"))
        self.assertEqual(spans(text, "2026-11-05"), [(1380, 1440)])
        self.assertEqual(spans(text, "2026-11-06"), [(0, 60)])

    def test_declined_and_the_meeting_link(self):
        head = ("X-WR-CALNAME:someone@example.com", "X-WR-TIMEZONE:America/Toronto")
        text = ics(vevent("UID:e1", "DTSTART;TZID=America/Toronto:20261105T110000", "DURATION:PT30M", "SUMMARY:Sync",
                          "ATTENDEE;CN=Someone;PARTSTAT=DECLINED:mailto:someone@example.com",
                          "ATTENDEE;CN=Other;PARTSTAT=ACCEPTED:mailto:other@example.com",
                          "DESCRIPTION:Join: https://example.zoom.us/j/123456?pwd=abc <https://www.google.com/url?q=htt"
                          "ps://example.zoom.us/j/123456>"),
                   vevent("UID:e2", "DTSTART;TZID=America/Toronto:20261105T120000", "DURATION:PT30M", "SUMMARY:Pairing",
                          "ATTENDEE;PARTSTAT=ACCEPTED:mailto:someone@example.com",
                          "X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij"),
                   vevent("UID:e3", "DTSTART;TZID=America/Toronto:20261105T130000", "DURATION:PT30M", "SUMMARY:Notes",
                          "URL:https://example.com/agenda"), head=head)
        sync, pairing, notes = on(text, "2026-11-05")
        self.assertEqual((sync["declined"], sync["url"]), (True, "https://example.zoom.us/j/123456?pwd=abc"))
        self.assertEqual((pairing["declined"], pairing["url"]), (False, "https://meet.google.com/abc-defg-hij"))
        self.assertEqual(notes["url"], "https://example.com/agenda")
        self.assertNotIn("location", notes)


class RepeatTest(unittest.TestCase):
    def test_weekly_series_with_a_skipped_a_moved_and_a_cancelled_day(self):
        text = ics(vevent("UID:scrum-1", "DTSTART;TZID=America/Toronto:20260105T093000",
                          "DTEND;TZID=America/Toronto:20260105T094500", "SUMMARY:Scrum",
                          "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR", "EXDATE;TZID=America/Toronto:20261102T093000"),
                   vevent("UID:scrum-1", "RECURRENCE-ID;TZID=America/Toronto:20261104T093000",
                          "DTSTART;TZID=America/Toronto:20261104T110000", "DTEND;TZID=America/Toronto:20261104T111500",
                          "SUMMARY:Scrum (moved)"),
                   vevent("UID:scrum-1", "RECURRENCE-ID;TZID=America/Toronto:20261106T093000",
                          "DTSTART;TZID=America/Toronto:20261106T093000", "STATUS:CANCELLED", "SUMMARY:Scrum"))
        self.assertEqual(on(text, "2026-11-02"), [])                      # EXDATE
        self.assertEqual(on(text, "2026-11-03"), [])                      # a Tuesday
        [moved] = on(text, "2026-11-04")
        self.assertEqual((moved["title"], moved["s"], moved["e"], moved["recurring"], moved["uid"]),
                         ("Scrum (moved)", 660, 675, True, "scrum-1"))
        self.assertEqual(on(text, "2026-11-06"), [])                      # cancelled on its own
        self.assertEqual(spans(text, "2026-11-09"), [(570, 585)])        # the series goes on
        self.assertEqual(spans(text, "2026-01-05"), [(570, 585)])        # and began on its first day

    def test_a_daily_meeting_keeps_its_clock_time_across_the_clock_change(self):
        text = ics(vevent("UID:f1", "DTSTART;TZID=America/Toronto:20261001T093000", "DURATION:PT15M",
                          "SUMMARY:Daily", "RRULE:FREQ=DAILY"))
        self.assertEqual(spans(text, "2026-10-30"), [(570, 585)])
        self.assertEqual(spans(text, "2026-11-05"), [(570, 585)])         # Toronto left summer time on 1 November
        self.assertEqual(spans(text, "2026-10-30", KYIV), [(930, 945)])  # six hours apart before,
        self.assertEqual(spans(text, "2026-11-05", KYIV), [(990, 1005)])  # seven after

    def test_count_and_until_end_a_series(self):
        text = ics(vevent("UID:g1", "DTSTART;TZID=America/Toronto:20261102T080000", "DURATION:PT10M",
                          "SUMMARY:Three times", "RRULE:FREQ=DAILY;COUNT=3"),
                   vevent("UID:g2", "DTSTART;TZID=America/Toronto:20261102T093000", "DURATION:PT10M",
                          "SUMMARY:Until", "RRULE:FREQ=DAILY;UNTIL=20261103T140000Z"))
        self.assertEqual([x["title"] for x in on(text, "2026-11-02")], ["Three times", "Until"])
        self.assertEqual([x["title"] for x in on(text, "2026-11-03")], ["Three times"])  # 09:30 here is 14:30Z
        self.assertEqual([x["title"] for x in on(text, "2026-11-04")], ["Three times"])
        self.assertEqual(on(text, "2026-11-05"), [])

    def test_monthly_yearly_and_every_other_week(self):
        def days(rule, start, dates, value=""):
            text = ics(vevent("UID:h", f"DTSTART{value}:{start}", "SUMMARY:x", f"RRULE:{rule}"))
            return [d for d in dates if on(text, d)]

        nov = [f"2026-11-{d:02d}" for d in range(1, 31)]
        self.assertEqual(days("FREQ=MONTHLY;BYDAY=-1FR", "20260130T100000", nov), ["2026-11-27"])
        self.assertEqual(days("FREQ=MONTHLY;BYDAY=MO;BYSETPOS=1", "20260105T100000", nov), ["2026-11-02"])
        self.assertEqual(days("FREQ=MONTHLY;BYMONTHDAY=31", "20260131T100000", nov + ["2026-12-31"]), ["2026-12-31"])
        self.assertEqual(days("FREQ=MONTHLY;BYMONTHDAY=-1", "20260131T100000", nov), ["2026-11-30"])
        self.assertEqual(days("FREQ=WEEKLY;INTERVAL=2;BYDAY=TH", "20261022T100000", nov), ["2026-11-05", "2026-11-19"])
        self.assertEqual(days("FREQ=YEARLY", "20201105", nov, ";VALUE=DATE"), ["2026-11-05"])
        self.assertEqual(days("FREQ=YEARLY;BYMONTH=11;BYDAY=4TH", "20201126T100000", nov), ["2026-11-26"])
        self.assertEqual(days("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1", "20260102T100000", nov),
                         ["2026-11-06", "2026-11-13", "2026-11-20", "2026-11-27"])

    def test_a_rule_pulsar_does_not_expand_still_shows_its_first_day(self):
        text = ics(vevent("UID:i1", "DTSTART:20261105T100000", "SUMMARY:x", "RRULE:FREQ=YEARLY;BYWEEKNO=20"))
        self.assertEqual(len(on(text, "2026-11-05")), 1)
        self.assertEqual(on(text, "2027-11-05"), [])

    def test_years_of_daily_meetings_are_placed_quickly(self):
        events = [vevent(f"UID:j{i}", f"DTSTART;TZID=America/Toronto:20100104T{9 + i % 8:02d}0000", "DURATION:PT15M",
                         f"SUMMARY:Series {i}", "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR" if i % 2 else "RRULE:FREQ=DAILY")
                  for i in range(300)]
        built = calendar.build(calendar.parse(ics(*events)), TORONTO)
        t0 = time.time()
        got = calendar.events_on(built, "2026-11-05", TORONTO)
        self.assertEqual(len(got), 300)
        self.assertLess(time.time() - t0, 1.0)


class DownloadTest(unittest.TestCase):
    def test_webcal_is_https_and_nothing_else_is_accepted(self):
        self.assertEqual(calendar.https_address("webcal://example.com/a.ics"), "https://example.com/a.ics")
        for bad in ("http://example.com/a.ics", "file:///etc/passwd", "ftp://example.com/a.ics"):
            with self.assertRaises(calendar.CalendarError):
                calendar.https_address(bad)

    def test_an_answer_is_unzipped_and_must_be_a_calendar(self):
        text = ics(vevent("UID:k1", "DTSTART:20261105T100000Z", "SUMMARY:x"))
        self.assertEqual(calendar.unpack(gzip.compress(text.encode()), "gzip"), text)
        self.assertEqual(calendar.unpack(b"\xef\xbb\xbf" + text.encode()), text)  # a byte-order mark first
        with self.assertRaises(calendar.CalendarError):
            calendar.unpack(b"<!doctype html><title>Sign in</title>")
        with mock.patch.object(calendar, "MAX_BYTES", 1000):
            with self.assertRaises(calendar.CalendarError):
                calendar.unpack(gzip.compress(b"BEGIN:VCALENDAR\r\n" + b"X" * 5000), "gzip")  # unzips past the limit

    def test_a_refused_address_says_what_to_do_without_quoting_it(self):
        message = calendar.http_reason(404)
        self.assertIn("Copy the secret address again", message)
        self.assertNotIn("private-", message)


class Isolated(unittest.TestCase):
    """The address lives in a scratch file, never the keychain, and nothing is kept between tests."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.patches = [mock.patch.object(config, "_keychain", lambda: False),
                        mock.patch.object(config, "CALENDAR_FILE", os.path.join(self.dir, "calendar-url")),
                        mock.patch.object(config, "CONFIG_DIR", self.dir)]
        for p in self.patches:
            p.start()
        config._calendar_cache[2] = False
        calendar.forget()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        config._calendar_cache[2] = False
        calendar.forget()


class AddressTest(Isolated):
    def test_the_address_is_kept_privately_and_odd_ones_are_refused(self):
        config.set_calendar_url(ADDRESS)
        self.assertEqual(config.get_calendar_url(), ADDRESS)
        self.assertEqual(stat.S_IMODE(os.stat(config.CALENDAR_FILE).st_mode), 0o600)
        for bad in ("http://example.com/a.ics", 'https://example.com/a".ics', "https://example.com/a b.ics",
                    "https://example.com/a\\b.ics", "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                config.set_calendar_url(bad)
        config.delete_calendar_url()
        self.assertIsNone(config.get_calendar_url())


class DayTest(Isolated):
    SETTINGS = {**config.DEFAULTS, "timezone": "America/Toronto"}
    TEXT = ics(vevent("UID:l1", "DTSTART;TZID=America/Toronto:20261105T093000", "DURATION:PT15M", "SUMMARY:Standup"))

    def test_no_address_is_a_setup_step(self):
        got = calendar.day("2026-11-05", self.SETTINGS, get=lambda url: self.fail("nothing to download"))
        self.assertEqual((got["events"], got["source"], got["needsSetup"]), ([], None, True))

    def test_the_calendar_is_downloaded_once_for_several_days(self):
        config.set_calendar_url(ADDRESS)
        calls = []
        get = lambda url: calls.append(url) or self.TEXT  # noqa: E731
        first = calendar.day("2026-11-05", self.SETTINGS, get)
        calendar.day("2026-11-06", self.SETTINGS, get)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["events"][0]["s"], 570)
        self.assertEqual((first["source"], first["problem"], first["needsSetup"]), ("ics", None, False))

    def test_when_the_address_fails_the_last_copy_stands_in_and_the_address_is_never_quoted(self):
        config.set_calendar_url(ADDRESS)
        now = [1_800_000_000.0]
        clock = mock.Mock(time=lambda: now[0])

        def broken(url):
            raise calendar.CalendarError(calendar.http_reason(404))

        with mock.patch.object(calendar, "time", clock):
            calendar.day("2026-11-05", self.SETTINGS, lambda url: self.TEXT)
            now[0] += calendar.FRESH + 1
            got = calendar.day("2026-11-05", self.SETTINGS, broken)
            self.assertEqual(len(got["events"]), 1)
            self.assertIn("Showing the calendar as it was at", got["problem"])
            now[0] += calendar.KEEP
            got = calendar.day("2026-11-05", self.SETTINGS, broken)
            self.assertEqual(got["events"], [])
        for text in (got["problem"], str(got)):
            self.assertNotIn("private-0123abcd", text)

    def test_a_failure_is_not_retried_on_every_request(self):
        config.set_calendar_url(ADDRESS)
        calls = []

        def broken(url):
            calls.append(url)
            raise calendar.CalendarError("The calendar could not be reached (it took too long to answer).")

        for _ in range(3):
            got = calendar.day("2026-11-05", self.SETTINGS, broken)
        self.assertEqual(len(calls), 1)
        self.assertEqual((got["events"], got["source"]), ([], "ics"))


class ServerTest(Isolated):
    def settings(self, body):
        with mock.patch.object(server.config, "update", lambda ch: {**config.DEFAULTS, **ch}), \
             mock.patch.object(server, "current_settings", lambda: dict(config.DEFAULTS)), \
             mock.patch.object(server, "test_productive", lambda s: {"ok": True}), \
             mock.patch.object(server.productive, "connection", lambda s: {"kind": None}), \
             mock.patch.object(server.calendar, "check", lambda s: {"ok": True, "message": "checked"}):
            return server.api_settings(body)

    def test_a_bad_address_changes_nothing(self):
        with self.assertRaises(ValueError):
            self.settings({"calendar_url": "http://example.com/a.ics"})
        self.assertIsNone(config.get_calendar_url())

    def test_saving_an_address_checks_it_and_the_page_never_gets_it_back(self):
        out = self.settings({"calendar_url": ADDRESS})
        self.assertEqual(config.get_calendar_url(), ADDRESS)
        self.assertEqual(out["calendarTest"]["message"], "checked")
        self.assertEqual(out["config"]["calendar"], {"saved": True})
        self.assertNotIn("private-0123abcd", repr(out))
        out = self.settings({"remove_calendar_url": True})
        self.assertIsNone(config.get_calendar_url())
        self.assertEqual(out["config"]["calendar"], {"saved": False})
        self.assertNotIn("calendarTest", out)

    def test_the_route_answers_a_signed_in_page_only(self):
        import json
        import threading
        from http.server import ThreadingHTTPServer
        with mock.patch.object(config, "SECRET_FILE", os.path.join(self.dir, "secret")), \
             mock.patch.object(config, "SIGNIN_FILE", os.path.join(self.dir, "signin-code")), \
             mock.patch.object(config, "SESSIONS_FILE", os.path.join(self.dir, "sessions.json")):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                import http.client
                port = httpd.server_address[1]

                def ask(path, headers=None):
                    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
                    r = c.getresponse()
                    return r.status, json.loads(r.read() or b"null")

                self.assertEqual(ask("/api/calendar?date=2026-11-05")[0], 401)
                key = {"X-Pulsar-Key": config.session_secret()}
                self.assertEqual(ask("/api/calendar", key)[0], 400)
                status, body = ask("/api/calendar?date=2026-11-05", key)
                self.assertEqual((status, body["needsSetup"], body["events"]), (200, True, []))
            finally:
                httpd.shutdown()
                httpd.server_close()


if __name__ == "__main__":
    unittest.main()
