"""Capture clock evidence and its real Door-to-World boundary (ADR 0107)."""
import copy
import tempfile
import unittest
from pathlib import Path

from world.capture_start import check
from world.world import build_projection, query, ProjectionError
from world.tests.test_conformance import build_fixture_archive, remove_tree

ARCH = "urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#"


class CaptureClock(unittest.TestCase):
    def test_unknown_and_local_and_zoned_clocks(self):
        for payload in ({"basis": "header"},
                        {"basis": "grammar", "started": "2000-02-29T12:34:56", "clock": "local"},
                        {"basis": "client", "started": "2001-01-01T12:34:56.125+01:00", "clock": "zoned"},
                        {"basis": "person", "started": "2001-01-01T12:34:56Z", "clock": "zoned"}):
            self.assertEqual(check(payload), payload)

    def test_bad_calendar_clock_pairing_and_unread_values_refuse(self):
        for payload in ({"basis": "grammar", "started": "2001-02-29T12:34:56", "clock": "local"},
                        {"basis": "grammar", "started": "2001-01-01T12:34:56Z", "clock": "local"},
                        {"basis": "person", "started": "2001-01-01T12:34:56", "clock": "zoned"},
                        {"basis": "person", "clock": "local"},
                        {"basis": "person", "started": "2001-01-01T12:34:56"},
                        {"basis": "label"}, {"basis": "header", "at": "invented"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                check(payload)


class CaptureProjection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.manifest, cls.export = build_fixture_archive(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        remove_tree(cls.tmp)

    def test_real_admitted_local_time_is_not_a_zoned_instant(self):
        projection = build_projection(self.export)
        answer = query(projection, f"SELECT ?start WHERE {{ GRAPH ?g {{ ?s <{ARCH}capture_started> ?start }} }}")
        [row] = answer.data["results"]["bindings"]
        self.assertEqual(row["start"]["value"], "2001-01-01T12:00:00")
        self.assertEqual(row["start"].get("datatype", "http://www.w3.org/2001/XMLSchema#string"),
                         "http://www.w3.org/2001/XMLSchema#string")

    def test_zoned_capture_projects_as_datetime_and_unknown_has_no_start(self):
        for payload in ({"basis": "person", "started": "2001-01-01T12:00:00+01:00", "clock": "zoned"},
                        {"basis": "person"}):
            changed = copy.deepcopy(self.export)
            [record] = [r for r in changed["records"] if any(c["id"] == "archive.claim/capture_start" for c in r.get("contracts", []))]
            record["payload"] = payload
            projection = build_projection(changed)
            answer = query(projection, f"SELECT ?start WHERE {{ GRAPH ?g {{ ?s <{ARCH}capture_started> ?start }} }}")
            rows = answer.data["results"]["bindings"]
            if "started" not in payload:
                self.assertEqual(rows, [])
            else:
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["start"]["datatype"], "http://www.w3.org/2001/XMLSchema#dateTime")

    def test_software_cannot_assert_a_persons_clock_statement(self):
        changed = copy.deepcopy(self.export)
        [record] = [r for r in changed["records"] if any(c["id"] == "archive.claim/capture_start" for c in r.get("contracts", []))]
        record["agent"] = "pkg:generic/clock-reader@1"
        with self.assertRaises(ProjectionError):
            build_projection(changed)

    def test_world_refuses_a_calendar_error_even_in_an_export(self):
        changed = copy.deepcopy(self.export)
        [record] = [r for r in changed["records"] if any(c["id"] == "archive.claim/capture_start" for c in r.get("contracts", []))]
        record["payload"]["started"] = "2001-02-29T12:00:00"
        with self.assertRaises(ProjectionError):
            build_projection(changed)
