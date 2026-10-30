"""Export-only tests share bytes, never mutable records or writer archives."""
import copy
import unittest
from unittest.mock import patch

from world.tests.fixture_exports import fixture_export


class FixtureExportSnapshot(unittest.TestCase):
    def test_the_real_writer_seed_is_reused_without_reopening_an_archive(self):
        expected = fixture_export()  # The first call always uses the real builder.
        with patch("world.tests.test_conformance.build_fixture_archive",
                   side_effect=AssertionError("a warm JSON snapshot must not rebuild an archive")):
            actual = fixture_export()
        self.assertEqual(actual, expected)
        self.assertNotIn("archive_root", actual[0])

    def test_nested_mutations_never_change_another_callers_export(self):
        manifest, export = fixture_export()
        expected = copy.deepcopy((manifest, export))
        other = fixture_export()
        self.assertEqual(other, expected)  # Anchor before deliberately changing it.
        self.assertTrue(manifest["objects"])
        self.assertTrue(export["records"])
        manifest["objects"].clear()
        export["records"][0]["payload"]["snapshot-test"] = {"nested": [1, 2]}
        export["records"].pop()
        self.assertNotEqual((manifest, export), expected)
        self.assertEqual(other, expected)
        self.assertEqual(fixture_export(), expected)
