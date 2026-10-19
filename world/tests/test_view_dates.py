"""A view read on a date defaults to Door's day, the UTC date, never the
host's local date: Door runs the check beyond the circle on today's UTC date,
and World must agree with it at any hour."""

import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from world import record_views

# 23:30 UTC on the last day of a year: east of UTC it is already the next year.
LATE = datetime(2030, 12, 31, 23, 30, tzinfo=timezone.utc)


class NoLocalDate(date):
    @classmethod
    def today(cls):
        raise AssertionError("the host's local date was read")


class UtcClock(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is not timezone.utc:
            raise AssertionError(f"the clock was read in {tz!r}, not UTC")
        return LATE


class DefaultDate(unittest.TestCase):
    def test_the_default_on_is_the_utc_date(self):
        with patch.object(record_views, "date", NoLocalDate), patch.object(record_views, "datetime", UtcClock):
            self.assertEqual(record_views._on({}), "2030-12-31")

    def test_a_given_on_is_kept_and_checked(self):
        with patch.object(record_views, "date", NoLocalDate), patch.object(record_views, "datetime", UtcClock):
            self.assertEqual(record_views._on({"on": "2031-01-01"}), "2031-01-01")
            with self.assertRaises(record_views.ViewError):
                record_views._on({"on": "2031-02-30"})


if __name__ == "__main__":
    unittest.main()
