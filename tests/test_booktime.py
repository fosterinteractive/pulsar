"""A booking's time of day from its note (pulsar/booktime.py): what is a time, what is not, and the person's zone."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from zoneinfo import ZoneInfo  # noqa: E402

from pulsar import booktime  # noqa: E402

KYIV = ZoneInfo("Europe/Kyiv")


def span(note, minutes=120, date="2026-11-18", tz=KYIV):
    got = booktime.on_day(note, date, tz, minutes)
    return (got["s"], got["e"]) if got else None


class NoteTimeTest(unittest.TestCase):
    def test_the_ways_people_write_a_range(self):
        for note in ("14:00-16:00", "14:00 – 16", "з 14 до 16", "з 14:00 по 16:00", "from 14 to 16", "2pm-4pm", "2-4 pm",
                     "Plan: 14.00–16.00, the migration", "<p>14:00&ndash;16:00</p>", "о 14:00, до 16:00"):
            self.assertEqual(span(note), (840, 960), note)
        self.assertEqual(span("2:30–4pm"), (870, 960))
        self.assertEqual(span("11-1pm"), (660, 780))
        self.assertEqual(span("з 11 до 1"), (660, 780))  # one in the afternoon
        self.assertEqual(span("9.30 to 11:00"), (570, 660))

    def test_a_start_alone_runs_as_long_as_the_booking(self):
        self.assertEqual(span("о 14:00", 90), (840, 930))
        self.assertEqual(span("Call at 2pm with the client", 60), (840, 900))
        self.assertEqual(span("at 23:30", 120), (1410, 1440))  # never past the day

    def test_numbers_that_are_not_a_time_of_day(self):
        for note in ("pages 2-3", "v1.2-1.4", "3 tasks", "Plan for 18.11", "18.11-20.11", "2026-11-18", "at 3-5 pages",
                     "deadline 17:00", "до 17:00", "due by 5pm", "25:00", "14:75", "", None):
            self.assertIsNone(span(note), note)

    def test_a_zone_in_the_note_is_turned_into_the_persons(self):
        # 14:00 in Toronto (UTC-5 in November) is 21:00 in Kyiv (UTC+2)
        self.assertEqual(span("14:00-16:00 ET"), (1260, 1380))
        self.assertEqual(span("2pm-4pm (Toronto)"), (1260, 1380))
        self.assertEqual(span("10:00 за Києвом", 60, tz=ZoneInfo("America/Toronto")), (180, 240))
        self.assertIsNone(span("8pm ET"))  # 03:00 the next day in Kyiv: not on this day
        self.assertEqual(span("14:00-16:00", tz=ZoneInfo("America/Toronto")), (840, 960))  # no zone: the person's


if __name__ == "__main__":
    unittest.main()
