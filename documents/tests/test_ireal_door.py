"""`documents ireal import`, `candidates` and `link` end to end: an invented
playlist, a scratch archive made by `archive init`, and the real Door.
Every title, composer and progression is invented."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCUMENTS = HERE.parent
sys.path.insert(0, str(DOCUMENTS))
sys.path.insert(0, str(DOCUMENTS / "ireal"))

import documents  # noqa: E402
from codec import Playlist, Song, encode_playlist, music_field  # noqa: E402

DOOR = os.environ.get("ARCHIVE_TEST_DOOR")
ARCHIVE = os.environ.get("ARCHIVE_TEST_ARCHIVE")
if os.environ.get("ARCHIVE_REQUIRE_INTEGRATION") and not (DOOR and ARCHIVE):
    raise RuntimeError("ARCHIVE_REQUIRE_INTEGRATION is set, but ARCHIVE_TEST_DOOR or ARCHIVE_TEST_ARCHIVE is not")
needs_door = unittest.skipUnless(DOOR and ARCHIVE, "set ARCHIVE_TEST_DOOR and ARCHIVE_TEST_ARCHIVE")

CHARTS = (
    ("Invented Tune One", "Doe Jane", "C", "[T44*AC^7XyQ|A-7 D7LZG-7 C7 |FXyQKcl LZ{*BC  |N1G7  }XyQ|N2C  Z"),
    ("Invented Tune Two", "Roe Max", "Eb-", "[T44*ASEb-7XyQ|Ab7 Q XyQ|Db^7XyQ|Gb^7<D.S. al Coda>XyQ]Q[*BC7XyQZ"),
    ("Invented Broken Tune", "Doe Jane", "F", "C7 |F |"),
)


def playlist(charts=CHARTS, name: str = "Invented List") -> bytes:
    songs = tuple(Song("irealb", (title, composer, "", "Medium Swing", key, "0", music_field(markup)), b"")
                  for title, composer, key, markup in charts)
    return encode_playlist(Playlist("irealb", songs, name))


@needs_door
class Import(unittest.TestCase):
    def setUp(self) -> None:
        # Short, under /tmp: git-annex keeps sockets in the repositories.
        self.tmp = Path(tempfile.mkdtemp(prefix="documents-", dir="/tmp"))
        self.addCleanup(self._remove, self.tmp)
        self.env = dict(os.environ, GIT_AUTHOR_NAME="Documents tests", GIT_AUTHOR_EMAIL="documents@example.invalid",
                        GIT_COMMITTER_NAME="Documents tests", GIT_COMMITTER_EMAIL="documents@example.invalid")
        self.root = self.tmp / "archive"
        subprocess.run([ARCHIVE, "init", "--operator", "documents-operator", str(self.root)], env=self.env,
                       check=True, capture_output=True)
        self.file = self.tmp / "invented.irealb"
        self.file.write_bytes(playlist())
        self.patch = unittest.mock.patch.dict(os.environ, {k: v for k, v in self.env.items() if k.startswith("GIT_")})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    @staticmethod
    def _remove(path: Path) -> None:
        for directory, _, _ in os.walk(path):
            os.chmod(directory, 0o700)
        shutil.rmtree(path, ignore_errors=True)

    def command(self, *arguments: str) -> tuple[int, dict]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = documents.main(list(arguments))
        text = out.getvalue() or err.getvalue()
        return code, json.loads(text)

    def importing(self, *extra: str) -> tuple[int, dict]:
        return self.command("ireal", "import", str(self.file), "--archive", str(self.root), "--staging",
                            str(self.tmp / "staging"), "--door", DOOR, "--archive-command", ARCHIVE, *extra)

    def export(self) -> dict:
        return json.loads(subprocess.run([ARCHIVE, "export"], cwd=self.root, env=self.env, check=True,
                                         capture_output=True, text=True).stdout)

    def claims(self, contract: str) -> list[dict]:
        return [r for r in self.export()["records"]
                if r["shape"] == "claim" and any(c["id"] == contract for c in r.get("contracts", []))]

    def test_a_dry_run_predicts_and_writes_nothing(self) -> None:
        before = len(self.export()["records"])
        proposals = self.tmp / "proposals"
        code, answer = self.importing("--dry-run", "--proposals", str(proposals))
        self.assertEqual((code, answer["status"]), (1, "predicted"))
        self.assertEqual(len(self.export()["records"]), before)
        self.assertFalse((self.tmp / "staging").exists())
        self.assertTrue((proposals / "0000-import" / "manifest.json").is_file())

    def test_import_admits_documents_and_claims_and_a_rerun_writes_nothing(self) -> None:
        code, answer = self.importing("--retrieved-at", "2001-02-03")
        self.assertEqual((code, answer["status"]), (1, "committed"))
        self.assertEqual(answer["counts"], {"read": 2, "failed": 1})
        self.assertEqual([f["number"] for f in answer["failed"]], [3])
        self.assertIn("no time signature", answer["failed"][0]["reason"])
        records = self.export()["records"]
        artifacts = [r for r in records if r["shape"] == "artifact"]
        self.assertEqual(len(artifacts), 5)   # the playlist, three charts, the parameters
        sources = self.claims("archive.claim/document_source")
        self.assertEqual(len(sources), 4)     # the playlist's and each chart's, the failed one's too
        playlist_source = [s for s in sources if "filename" in s["payload"]]
        self.assertEqual(playlist_source[0]["payload"], {"media_type": "text/plain", "kinds": ["chords"],
                                                         "filename": "invented.irealb", "retrieved_at": "2001-02-03"})
        self.assertTrue(all(any(ref["role"] == "derived_from" for ref in s["refs"]) and
                            any(ref["role"] == "run" for ref in s["refs"]) for s in sources if s not in playlist_source))
        for contract, count in (("archive.claim/derivation_run", 3), ("archive.claim/chord_sequence", 2),
                                ("archive.claim/form", 2), ("archive.claim/meter_layers", 2),
                                ("archive.claim/key_sequence", 2)):
            self.assertEqual(len(self.claims(contract)), count, contract)
        run = self.claims("archive.claim/derivation_run")[0]
        self.assertEqual(run["payload"]["method"], {"id": "ireal-chart", "version": "1"})
        self.assertEqual(sorted(ref["role"] for ref in run["refs"]), ["parameters", "used", "used"])

        before = len(self.export()["records"])
        code, answer = self.importing()
        self.assertEqual((code, answer["status"], answer["counts"]), (0, "unchanged", {"known": 3}))
        self.assertEqual(len(self.export()["records"]), before)

    def test_the_same_chart_in_another_playlist_yields_nothing(self) -> None:
        self.importing()
        self.file.write_bytes(playlist(CHARTS[:2], "Another Invented List"))
        code, answer = self.importing()
        self.assertEqual((code, answer["counts"]), (0, {"known": 2}))
        self.assertTrue(answer["playlist"]["new"])

    def test_candidates_then_the_operators_link(self) -> None:
        self.importing()
        out = self.tmp / "candidates"
        code, answer = self.command("ireal", "candidates", str(self.file), "--archive", str(self.root),
                                    "--out", str(out), "--archive-command", ARCHIVE)
        self.assertEqual((code, answer["admitted"], answer["with_work_candidates"]), (0, 3, 0))
        decisions = json.loads((out / "decisions.json").read_text())
        first = decisions["charts"][0]
        self.assertEqual((first["title"], first["work"]), ("Invented Tune One", None))
        first["work"] = "new"
        first["composers"][0]["person"] = "new"
        decisions["charts"][2]["work"] = "new"   # the failed chart has no reading to link
        path = self.tmp / "decisions.json"
        path.write_text(json.dumps(decisions))
        link = ("ireal", "link", str(path), "--archive", str(self.root), "--door", DOOR, "--archive-command", ARCHIVE)
        code, answer = self.command(*link)
        self.assertEqual(code, 1)
        self.assertIn("no complete ireal-chart reading", answer["detail"])
        decisions["charts"][2]["work"] = None
        path.write_text(json.dumps(decisions))

        code, answer = self.command(*link, "--dry-run")
        self.assertEqual((code, answer["status"]), (0, "predicted"))
        code, answer = self.command(*link)
        self.assertEqual((code, answer["status"], answer["charts"][0]["status"]), (0, "committed", "linked"))
        operator = [r for r in self.export()["records"] if r["shape"] == "entity"
                    and r["payload"].get("kind") == "person" and r["agent"] == r["object_id"]][0]["object_id"]
        sheet = self.claims("archive.claim/lead_sheet")
        self.assertEqual(len(sheet), 1)
        self.assertEqual(sheet[0]["agent"], operator)
        self.assertEqual(sorted(ref["role"] for ref in sheet[0]["refs"]),
                         ["chords", "credits", "form", "key", "meter", "work"])
        credit = self.claims("archive.claim/contribution")[0]
        self.assertEqual(credit["payload"], {"scope": "work", "role": {"type": "composer"}, "text": "Doe Jane",
                                             "credited_as": "Doe Jane"})
        self.assertTrue(credit["agent"].startswith("pkg:generic/archive-documents@"))
        links = [s for s in self.claims("archive.claim/document_source") if s["agent"] == operator]
        self.assertEqual(len(links), 1)

        before = len(self.export()["records"])
        code, answer = self.command(*link)
        self.assertEqual((code, answer["status"], answer["charts"][0]["status"]), (0, "unchanged", "already_linked"))
        self.assertEqual(len(self.export()["records"]), before)

        code, answer = self.command("ireal", "candidates", str(self.file), "--archive", str(self.root),
                                    "--out", str(out), "--archive-command", ARCHIVE)
        self.assertEqual((answer["linked"], answer["with_work_candidates"]), (1, 2))   # title, and composer


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
