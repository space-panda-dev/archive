import hashlib
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import migration.v2_bundle as v2_bundle
from migration.v2_bundle import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_FILES,
    MAX_BUNDLE_BYTES,
    MAX_MANIFEST_BYTES,
    BundleError,
    BundleLimits,
    build_candidate,
    build_candidate_file,
    verify_candidate,
    verify_candidate_file,
)


_original_tempdir = tempfile.tempdir


def setUpModule():
    tempfile.tempdir = os.path.realpath(tempfile.gettempdir())


def tearDownModule():
    tempfile.tempdir = _original_tempdir


class V2BundleCandidateTest(unittest.TestCase):
    def test_deterministic_bundle_has_exact_member_bytes_and_locators(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / ("long-" + "x" * 170)
            nested.mkdir()
            raw = b'{ "schema" : "artifact", "id": "synthetic-a", "revision_id": "r1" }\n'
            (nested / "record.json").write_bytes(raw)

            first = build_candidate(root)
            second = build_candidate(root)

        self.assertEqual(first, second)
        manifest = verify_candidate(first)
        self.assertEqual(manifest["record_inventory"], "complete")
        self.assertEqual(manifest["source_file_count"], 1)
        self.assertEqual(manifest["total_source_bytes"], len(raw))
        locator = manifest["records"][0]
        self.assertEqual(locator["member"], "source/" + nested.name + "/record.json")
        self.assertEqual(locator["offset"], 0)
        exact = raw[locator["offset"]:locator["offset"] + locator["length"]]
        self.assertEqual(locator["sha256"], "sha256:" + hashlib.sha256(exact).hexdigest())
        self.assertEqual(locator["accounting"], {"status": "unresolved", "reason": "no_accepted_archive_route"})
        with tarfile.open(fileobj=io.BytesIO(first), mode="r:") as archive:
            self.assertEqual(archive.getnames(), ["manifest.json", locator["member"]])
            self.assertEqual(archive.extractfile(locator["member"]).read(), raw)

    def test_many_json_array_locators_keep_utf8_byte_offsets(self):
        rows = [
            ('{ "schema":"artifact", "id":"item-%04d", "label":"café" }' % index).encode("utf-8")
            for index in range(3000)
        ]
        prefix, separator, suffix = b"[\n  ", b",\n  ", b"\n]\n"
        chunks = [prefix]
        expected = []
        offset = len(prefix)
        for index, row in enumerate(rows):
            chunks.append(row)
            expected.append((offset, len(row)))
            offset += len(row)
            if index + 1 < len(rows):
                chunks.append(separator)
                offset += len(separator)
        chunks.append(suffix)
        raw = b"".join(chunks)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "many.json").write_bytes(raw)
            manifest = verify_candidate(build_candidate(root))

        locators = manifest["records"]
        self.assertEqual(len(locators), len(rows))
        self.assertEqual([(item["offset"], item["length"]) for item in locators], expected)

    def test_unknown_schema_is_located_but_inventory_stays_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = b'{"schema":"new_schema","id":"source-x"}'
            (root / "new.json").write_bytes(raw)

            manifest = verify_candidate(build_candidate(root))

        self.assertEqual(manifest["record_inventory"], "incomplete")
        self.assertEqual(manifest["files"][0]["record_inventory"], {
            "status": "incomplete", "reason": "unsupported_source_schema",
        })
        self.assertEqual(manifest["records"][0]["accounting"], {
            "status": "unresolved", "reason": "unsupported_source_schema",
        })

    def test_unrecognized_file_bytes_are_preserved_and_counted_as_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = b"synthetic operational summary\x00\xff"
            (root / "summary.bin").write_bytes(original)

            bundle = build_candidate(root)
            manifest = verify_candidate(bundle)

        self.assertEqual(manifest["record_locator_count"], 0)
        self.assertEqual(manifest["record_inventory"], "incomplete")
        self.assertEqual(manifest["files"][0]["record_inventory"]["status"], "incomplete")
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:") as archive:
            self.assertEqual(archive.extractfile("source/summary.bin").read(), original)

    def test_duplicate_json_keys_keep_raw_file_but_mark_inventory_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = b'{"schema":"artifact","payload":{"x":1,"x":2}}'
            (root / "bad.json").write_bytes(raw)

            manifest = verify_candidate(build_candidate(root))

        self.assertEqual(manifest["record_locator_count"], 0)
        self.assertEqual(manifest["files"][0]["sha256"], "sha256:" + hashlib.sha256(raw).hexdigest())
        self.assertEqual(manifest["files"][0]["record_inventory"], {
            "status": "incomplete", "reason": "malformed_json",
        })

    def test_jsonl_locator_excludes_line_terminator_and_preserves_it_in_member(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = b'{"schema":"job","id":"job-1"}\r\n'
            second = b'{"schema":"attempt","id":"attempt-1"}'
            raw = first + second
            (root / "jobs.jsonl").write_bytes(raw)

            manifest = verify_candidate(build_candidate(root))

        locators = manifest["records"]
        self.assertEqual([(item["offset"], item["length"]) for item in locators], [
            (0, len(first.rstrip(b"\r\n"))), (len(first), len(second)),
        ])

    def test_candidate_refuses_symlinks_and_scan_limit_excess(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text('{"schema":"artifact"}', encoding="utf-8")
            (root / "alias.json").symlink_to(target)
            with self.assertRaises(BundleError):
                build_candidate(root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "big.json").write_bytes(b"x" * 20)
            with self.assertRaises(BundleError):
                build_candidate(root, BundleLimits(max_file_bytes=8))

    def test_source_change_after_a_member_read_refuses_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "record.json"
            source.write_text('{"schema":"artifact"}', encoding="utf-8")
            read_bound = v2_bundle._read_manifest_bound

            def read_then_change(*args, **kwargs):
                data = read_bound(*args, **kwargs)
                source.write_text('{"schema":"entity"}', encoding="utf-8")
                return data

            with patch("migration.v2_bundle._read_manifest_bound", side_effect=read_then_change):
                with self.assertRaises(BundleError):
                    build_candidate(root)

    def test_streaming_file_candidate_round_trips_without_bytes_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            (root / "one.json").write_bytes(b'{"schema":"artifact","id":"synthetic"}')
            destination = Path(directory) / "candidate.tar"

            produced = build_candidate_file(root, destination)
            checked = verify_candidate_file(destination)
            self.assertGreater(destination.stat().st_size, 0)

        self.assertEqual(produced["bytes"], checked["bytes"])
        self.assertEqual(produced["sha256"], checked["sha256"])
        self.assertEqual(produced["manifest"], checked["manifest"])

    def test_streaming_builder_refuses_in_tree_and_existing_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            (root / "one.bin").write_bytes(b"x")
            with self.assertRaises(BundleError):
                build_candidate_file(root, root / "candidate.tar")
            existing = Path(directory) / "existing.tar"
            existing.write_bytes(b"keep")
            with self.assertRaises(BundleError):
                build_candidate_file(root, existing)
            self.assertEqual(existing.read_bytes(), b"keep")

    def test_streaming_verifier_refuses_a_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            (root / "one.bin").write_bytes(b"x")
            candidate = Path(directory) / "candidate.tar"
            build_candidate_file(root, candidate)
            alias = Path(directory) / "alias.tar"
            alias.symlink_to(candidate)
            with self.assertRaises(BundleError):
                verify_candidate_file(alias)

    def test_streaming_verifier_requires_exact_header_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            (root / "one.bin").write_bytes(b"x")
            candidate = Path(directory) / "candidate.tar"
            build_candidate_file(root, candidate)
            original = candidate.read_bytes()

            mutations = []
            for offset, value in ((20, ord("x")), (500, 1)):
                changed = bytearray(original)
                changed[offset] = value
                changed[148:156] = b" " * 8
                checksum = sum(changed[:512])
                changed[148:156] = f"{checksum:06o}".encode("ascii") + b"\x00 "
                mutations.append(bytes(changed))
            changed_checksum = bytearray(original)
            changed_checksum[148:156] = b" " * 8
            checksum = sum(changed_checksum[:512])
            changed_checksum[148:156] = b"0" + f"{checksum:06o}".encode("ascii") + b"\x00"
            mutations.append(bytes(changed_checksum))

            for changed in mutations:
                candidate.write_bytes(changed)
                with self.assertRaises(BundleError):
                    verify_candidate_file(candidate)

    def test_streaming_builder_rejects_symlink_root_and_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            actual = base / "actual"
            root = actual / "records"
            root.mkdir(parents=True)
            (root / "one.json").write_text('{"schema":"artifact"}', encoding="utf-8")
            root_alias = base / "root-alias"
            root_alias.symlink_to(root, target_is_directory=True)
            parent_alias = base / "parent-alias"
            parent_alias.symlink_to(actual, target_is_directory=True)

            with self.assertRaises(BundleError):
                build_candidate_file(root_alias, base / "root-alias.tar")
            with self.assertRaises(BundleError):
                build_candidate_file(parent_alias / "records", base / "parent-alias.tar")

    def test_default_bounds_cover_the_measured_records_copy_without_allocating_it(self):
        measured_files = 48_809
        measured_source_bytes = 113_311_256
        self.assertGreaterEqual(DEFAULT_MAX_FILES, measured_files)
        self.assertGreaterEqual(DEFAULT_MAX_BYTES, measured_source_bytes)
        self.assertGreaterEqual(MAX_MANIFEST_BYTES, measured_files * 1024)
        self.assertGreaterEqual(MAX_BUNDLE_BYTES, measured_source_bytes + measured_files * 4096)

    def test_verifier_rejects_changed_tar_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.json").write_text('{"schema":"artifact"}', encoding="utf-8")
            bundle = build_candidate(root)

        corrupt = bytearray(bundle)
        corrupt[BLOCK_OFFSET_FOR_FIRST_MEMBER] ^= 1
        with self.assertRaises(BundleError):
            verify_candidate(bytes(corrupt))


BLOCK_OFFSET_FOR_FIRST_MEMBER = 1024


if __name__ == "__main__":
    unittest.main()
