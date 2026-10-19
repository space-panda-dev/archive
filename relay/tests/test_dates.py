"""The relay reads access (rights terms, reach) on Door's day, the UTC date,
never the host's local date."""

from __future__ import annotations

import sys
import threading
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "relay"))

import relay  # noqa: E402,F401  (puts World and enrich on the path, as the relay runs)
import served  # noqa: E402

LATE = datetime(2030, 12, 31, 23, 30, tzinfo=timezone.utc)


class UtcClock(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is not timezone.utc:
            raise AssertionError(f"the clock was read in {tz!r}, not UTC")
        return LATE


class AccessDate(unittest.TestCase):
    def test_access_is_read_on_the_utc_date(self):
        relay = served.Served()
        relay.config = SimpleNamespace(world_store=Path("/nonexistent"))
        relay.operator, relay.circle = "urn:uuid:operator", None
        relay.known_access, relay.access_lock = None, threading.Lock()
        loaded = []

        def load(store, version, operator, circle, today):
            loaded.append(today)
            return SimpleNamespace(generation="g", today=today)

        with patch.object(served, "datetime", UtcClock), \
                patch.object(served.world, "selected_generation", return_value="g"), \
                patch.object(served.access, "load", side_effect=load):
            relay.access()
            relay.access()
        self.assertEqual(loaded, [date(2030, 12, 31)])


if __name__ == "__main__":
    unittest.main()
