import os
import tempfile
import unittest
from pathlib import Path

from migration.v2_bundle import BundleError, build_candidate_file
from migration.v2_lookup import open_verified_bundle


_ORIGINAL_TEMPDIR = tempfile.tempdir


def setUpModule():
    tempfile.tempdir = os.path.realpath(tempfile.gettempdir())


def tearDownModule():
    tempfile.tempdir = _ORIGINAL_TEMPDIR


class V2LookupTest(unittest.TestCase):
    def _candidate(self, directory: str) -> tuple[Path, dict]:
        root = Path(directory) / "source"
        root.mkdir()
        first = b'{"schema":"artifact","artifact_id":"a1","title":"\xc3\xa9"}'
        second = b'{"schema":"claim","claim_id":"c1"}'
        (root / "records.json").write_bytes(b"[" + first + b"," + second + b"]")
        bundle = Path(directory) / "candidate.tar"
        result = build_candidate_file(root, bundle)
        return bundle, result["manifest"]

    def test_real_builder_locator_returns_exact_bytes_and_manifest_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, manifest = self._candidate(directory)
            locator = manifest["records"][0]

            with open_verified_bundle(bundle) as source:
                found = source.lookup(locator)

            expected = b'{"schema":"artifact","artifact_id":"a1","title":"\xc3\xa9"}'
            self.assertEqual(found.data, expected)
            self.assertEqual(found.metadata, locator)
            self.assertEqual(found.metadata["member"], "source/records.json")
            self.assertEqual(found.metadata["offset"], locator["offset"])
            self.assertEqual(found.metadata["length"], len(expected))
            self.assertIn("accounting", found.metadata)

    def test_locator_mismatch_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, manifest = self._candidate(directory)
            locator = dict(manifest["records"][0])
            locator["offset"] += 1

            with open_verified_bundle(bundle) as source:
                with self.assertRaisesRegex(BundleError, "does not match verified manifest"):
                    source.lookup(locator)

    def test_forged_locator_metadata_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, manifest = self._candidate(directory)
            original = manifest["records"][0]
            forged_locators = []
            forged_member = dict(original)
            forged_member["member_sha256"] = "sha256:" + "0" * 64
            forged_locators.append(forged_member)
            forged_accounting = dict(original)
            forged_accounting["accounting"] = {"status": "complete"}
            forged_locators.append(forged_accounting)

            with open_verified_bundle(bundle) as source:
                for locator in forged_locators:
                    with self.subTest(locator=locator):
                        with self.assertRaisesRegex(BundleError, "does not match verified manifest"):
                            source.lookup(locator)

    def test_bundle_tamper_before_open_fails_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, manifest = self._candidate(directory)
            locator = manifest["records"][0]
            with bundle.open("r+b") as handle:
                handle.seek(0)
                handle.write(b"X")

            with self.assertRaises(BundleError):
                open_verified_bundle(bundle)

    def test_bundle_change_after_verification_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle, manifest = self._candidate(directory)
            source = open_verified_bundle(bundle)
            try:
                with bundle.open("r+b") as handle:
                    handle.seek(0)
                    handle.write(b"X")
                with self.assertRaisesRegex(BundleError, "changed after verification"):
                    source.lookup(manifest["records"][0])
            finally:
                source.close()


if __name__ == "__main__":
    unittest.main()
