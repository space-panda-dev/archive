import hashlib
import json
import os
import tempfile
import unittest
import io
import contextlib
from pathlib import Path
from unittest.mock import patch

from migration.source_manifest import Limits, _Scanner, source_manifest, main

_original_tempdir = tempfile.tempdir


def setUpModule():
    tempfile.tempdir = os.path.realpath(tempfile.gettempdir())


def tearDownModule():
    tempfile.tempdir = _original_tempdir


class ContentManifestTest(unittest.TestCase):
    def test_manifest_is_sorted_deterministic_and_does_not_include_record_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "z.json").write_text('{"label":"SYNTHETIC_PRIVATE_VALUE"}', encoding="utf-8")
            nested = root / "nested"
            nested.mkdir()
            (nested / "a.json").write_text('{"schema":"artifact"}', encoding="utf-8")

            first = source_manifest(root)
            second = source_manifest(root)

        self.assertEqual(first, second)
        self.assertEqual(first["status"], "complete")
        self.assertFalse(first["atomic_snapshot"])
        self.assertTrue(first["requires_frozen_source"])
        self.assertEqual([item["path"] for item in first["files"]], ["nested/a.json", "z.json"])
        self.assertEqual(first["files"][1]["sha256"], hashlib.sha256(b'{"label":"SYNTHETIC_PRIVATE_VALUE"}').hexdigest())
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(first))

    def test_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "tree"
            root.mkdir()
            target = root.parent / "target.json"
            target.write_text("synthetic", encoding="utf-8")
            (root / "linked.json").symlink_to(target)

            report = source_manifest(root)

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["issues"], [{"kind": "symlink_unsupported", "path": "linked.json"}])
        self.assertNotIn("files", report)

    def test_symlink_in_root_parent_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            actual = base / "actual"
            root = actual / "records"
            root.mkdir(parents=True)
            (root / "record.json").write_bytes(b'{"schema":"artifact"}')
            (base / "alias").symlink_to(actual, target_is_directory=True)

            report = source_manifest(base / "alias" / "records")

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["issues"], [{"kind": "symlink_root_unsupported"}])
        self.assertNotIn("files", report)

    def test_empty_tree_is_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            report = source_manifest(Path(directory))

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["issues"], [{"kind": "empty_source"}])
        self.assertNotIn("files", report)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO creation is unavailable")
    def test_special_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.mkfifo(root / "pipe")
            report = source_manifest(root)

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["issues"], [{"kind": "special_file_unsupported", "path": "pipe"}])

    def test_file_mutated_during_read_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "record.json"
            target.write_text("before", encoding="utf-8")
            real_read = os.read
            changed = False

            def read_and_change(fd, count):
                nonlocal changed
                result = real_read(fd, count)
                if not changed:
                    changed = True
                    target.write_text("after!", encoding="utf-8")
                return result

            with patch("migration.source_manifest.os.read", side_effect=read_and_change):
                report = source_manifest(root)

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["issues"], [{"kind": "changed_during_scan", "path": "record.json"}])
        self.assertNotIn("files", report)

    def test_limits_fail_closed_without_returning_a_partial_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "record.json").write_bytes(b"1234")
            report = source_manifest(root, Limits(max_files=2, max_bytes=3, max_file_bytes=8, max_depth=2))

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["issues"], [{"kind": "total_size_limit_exceeded", "path": "record.json"}])
        self.assertNotIn("files", report)

    def test_duplicate_normalized_paths_are_refused(self):
        scanner = _Scanner(Limits())
        scanner._register_path(("Ä.json",))
        with self.assertRaisesRegex(Exception, "duplicate_path"):
            scanner._register_path(("ä.json",))

    def test_cli_escapes_unicode_and_sorts_paths_by_filesystem_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "é.json").write_text("synthetic", encoding="utf-8")
            (root / "z.json").write_text("synthetic", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main([str(root)])

        self.assertEqual(status, 0)
        self.assertIn("\\u00e9", output.getvalue())
        report = json.loads(output.getvalue())
        paths = [item["path"] for item in report["files"]]
        self.assertEqual(paths, sorted(paths, key=os.fsencode))

    def test_missing_safe_open_flags_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("migration.source_manifest._safe_open_flags_available", return_value=False):
                report = source_manifest(Path(directory))

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["issues"], [{"kind": "required_safe_open_flags_unavailable"}])


if __name__ == "__main__":
    unittest.main()
