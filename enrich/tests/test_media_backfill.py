"""The derived-media backfill (ADR 0069, proposed ADR 0129).

Synthetic exports and synthetic tones only. Making media runs the real
ffmpeg and ffprobe; without them those tests are skipped and say so, unless
ARCHIVE_REQUIRE_INTEGRATION is set, when they fail.
"""

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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "enrich"))

import derived  # noqa: E402
import media  # noqa: E402
import media_backfill  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
if os.environ.get("ARCHIVE_REQUIRE_INTEGRATION") and not (FFMPEG and FFPROBE):
    raise RuntimeError("ARCHIVE_REQUIRE_INTEGRATION is set but ffmpeg or ffprobe is not on PATH")
needs_ffmpeg = unittest.skipUnless(FFMPEG and FFPROBE, "put ffmpeg and ffprobe on PATH to make derived media")

STREAM = {"id": "archive.claim/audio_stream", "version": "1", "digest": "sha256:" + "0" * 64}
DESTROY = {"id": "archive.transition/destroy", "version": "1", "digest": "sha256:" + "1" * 64}


def artifact(object_id: str, digest: str, size: int, revision: str | None = None,
             supersedes: list[str] | None = None) -> dict:
    return {"shape": "artifact", "object_id": object_id, "revision_id": revision or f"rev-{object_id}",
            "supersedes": [{"revision_id": r} for r in supersedes or []],
            "payload": {"content": {"digest": digest, "size": size}}}


def stream(object_id: str, about: str, rate: int = 8000, count: int = 16000, channels: int = 1) -> dict:
    return {"shape": "claim", "object_id": object_id, "revision_id": f"rev-{object_id}", "contracts": [STREAM],
            "refs": [{"role": "artifact", "id": about, "revision_id": f"rev-{about}"}],
            "payload": {"sample_rate": rate, "sample_count": count, "channel_count": channels}}


def export(*records: dict) -> dict:
    return {"status": "ok", "records": list(records)}


class Scratch(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="media-backfill-"))
        self.addCleanup(shutil.rmtree, self.dir)
        self.archive = self.dir / "archive"
        self.root = self.dir / "derived-media"
        self.archive.mkdir()
        self.root.mkdir()

    def place(self, data: bytes | Path) -> tuple[str, int]:
        """Put bytes in the archive's content worktree, as layout archive/2 does."""
        if isinstance(data, Path):
            data = data.read_bytes()
        source = self.dir / "source.bin"
        source.write_bytes(data)
        digest = media.content_digest(source)
        target = media_backfill.content_path(self.archive, digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return digest, len(data)

    def tone(self, seconds: float = 2.0) -> Path:
        path = self.dir / f"tone-{seconds}.wav"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency=440:sample_rate=8000:duration={seconds}", "-c:a", "pcm_s16le",
                        str(path)], check=True)
        return path

    def cli(self, *args: str) -> tuple[int, dict, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = media_backfill.main(["--archive", str(self.archive), "--root", str(self.root), *args])
        return code, (json.loads(out.getvalue()) if out.getvalue() else {}), err.getvalue()

    def write_export(self, data: dict) -> Path:
        path = self.dir / "export.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path


class Finding(Scratch):
    def test_a_recording_is_a_current_artifact_a_stream_claim_is_about(self) -> None:
        heard = "sha256:" + "a" * 64
        found = media_backfill.recordings(export(
            artifact("art-1", heard, 100), stream("claim-1", "art-1", rate=8000, count=24000, channels=2),
            artifact("doc-1", "sha256:" + "b" * 64, 10)))
        self.assertEqual([(r.digest, r.objects, r.seconds, r.channels) for r in found],
                         [(heard, ["art-1"], 3.0, 2)])

    def test_superseded_revisions_and_destroyed_content_are_left_out(self) -> None:
        old, new, gone = ("sha256:" + c * 64 for c in "cde")
        destroy = {"shape": "transition", "object_id": "t-1", "revision_id": "rev-t-1", "contracts": [DESTROY],
                   "payload": {"content": {"digest": gone}}}
        found = media_backfill.recordings(export(
            artifact("art-1", old, 1, revision="r1"), artifact("art-1", new, 1, revision="r2", supersedes=["r1"]),
            stream("s-1", "art-1"), artifact("art-2", gone, 1), stream("s-2", "art-2"), destroy))
        self.assertEqual([r.digest for r in found], [new])

    def test_artifacts_sharing_content_share_one_recording(self) -> None:
        same = "sha256:" + "f" * 64
        found = media_backfill.recordings(export(artifact("a", same, 1), stream("s-a", "a"),
                                                 artifact("b", same, 1), stream("s-b", "b")))
        self.assertEqual([(r.digest, r.objects) for r in found], [(same, ["a", "b"])])

    def test_an_export_that_failed_is_refused(self) -> None:
        with self.assertRaises(media_backfill.BackfillError):
            media_backfill.recordings({"status": "failed"})

    def test_the_estimate_follows_the_bit_rate_and_the_pixel_rate(self) -> None:
        recording = media_backfill.Recording("sha256:" + "a" * 64, 1, seconds=3600.0, channels=2)
        self.assertAlmostEqual(recording.estimate("listen") / 3600, 24_000 * 1.03, delta=1)
        self.assertEqual(recording.estimate("peaks"), 3600 * 100 * 4)
        self.assertIsNone(media_backfill.Recording("sha256:" + "a" * 64, 1).estimate("listen"))


class DryRun(Scratch):
    def test_a_dry_run_reports_what_is_missing_and_writes_nothing(self) -> None:
        here, size = self.place(b"not audio, but a dry run never decodes")
        absent = "sha256:" + "9" * 64
        path = self.write_export(export(artifact("a", here, size), stream("s-a", "a", count=8000 * 60),
                                        artifact("b", absent, 5), stream("s-b", "b")))
        code, answer, _ = self.cli("--export", str(path), "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(answer["status"], "dry-run")
        self.assertEqual((answer["recordings"], answer["complete"], answer["content_not_here"]), (2, 0, 1))
        self.assertEqual(answer["missing"], {"listen": 2, "peaks": 2})
        self.assertEqual(answer["estimate"]["seconds_of_audio"], 60.0)
        self.assertEqual(answer["estimate"]["bytes_to_write"]["listen"], round(60 * 24_000 * 1.03))
        self.assertEqual({item["digest"]: item["content"] for item in answer["items"]},
                         {here: "here", absent: "not here"})
        self.assertEqual(list(self.root.iterdir()), [])

    def test_kind_limits_what_counts_as_missing(self) -> None:
        here, size = self.place(b"x")
        listen = derived.path(self.root, here, "listen")
        listen.parent.mkdir(parents=True)
        path = self.write_export(export(artifact("a", here, size), stream("s-a", "a")))
        code, answer, _ = self.cli("--export", str(path), "--dry-run", "--kind", "listen")
        self.assertEqual((code, answer["missing"]), (0, {"listen": 1}))

    def test_a_missing_root_is_refused(self) -> None:
        shutil.rmtree(self.root)
        code, _, err = self.cli("--export", str(self.write_export(export())), "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("derived-media root", err)


@needs_ffmpeg
class Making(Scratch):
    def test_it_makes_what_is_missing_and_a_rerun_writes_nothing(self) -> None:
        digest, size = self.place(self.tone())
        path = self.write_export(export(artifact("a", digest, size), stream("s-a", "a")))
        code, answer, _ = self.cli("--export", str(path), "--ffmpeg", FFMPEG, "--ffprobe", FFPROBE)
        self.assertEqual((code, answer["status"]), (0, "done"))
        self.assertEqual(answer["items"][0]["status"], {"listen": "written", "peaks": "written"})
        self.assertTrue(media.mp4_complete(derived.path(self.root, digest, "listen")))
        written = derived.path(self.root, digest, "listen").stat().st_mtime_ns
        code, again, _ = self.cli("--export", str(path), "--ffmpeg", FFMPEG, "--ffprobe", FFPROBE)
        self.assertEqual((code, again["complete"], again["items"]), (0, 1, []))
        self.assertEqual(derived.path(self.root, digest, "listen").stat().st_mtime_ns, written)

    def test_only_the_missing_kind_is_made(self) -> None:
        digest, size = self.place(self.tone())
        media.derive(media_backfill.content_path(self.archive, digest), digest, self.root,
                     ffmpeg=FFMPEG, ffprobe=FFPROBE, kinds=("peaks",))
        path = self.write_export(export(artifact("a", digest, size), stream("s-a", "a")))
        code, answer, _ = self.cli("--export", str(path), "--ffmpeg", FFMPEG, "--ffprobe", FFPROBE)
        self.assertEqual((code, answer["items"][0]["status"]), (0, {"listen": "written"}))

    def test_one_failure_does_not_stop_the_others_and_fails_the_command(self) -> None:
        good, good_size = self.place(self.tone())
        bad, bad_size = self.place(b"these bytes are not audio")
        path = self.write_export(export(artifact("a", good, good_size), stream("s-a", "a"),
                                        artifact("b", bad, bad_size), stream("s-b", "b")))
        code, answer, err = self.cli("--export", str(path), "--jobs", "2", "--ffmpeg", FFMPEG, "--ffprobe", FFPROBE)
        self.assertEqual((code, answer["status"]), (1, "failed"))
        status = {item["digest"]: item["status"] for item in answer["items"]}
        self.assertEqual(status[good], {"listen": "written", "peaks": "written"})
        self.assertTrue(status[bad].startswith("failed"))
        self.assertIn(good, err)

    def test_content_that_is_not_here_is_skipped(self) -> None:
        absent = "sha256:" + "9" * 64
        path = self.write_export(export(artifact("a", absent, 5), stream("s-a", "a")))
        code, answer, _ = self.cli("--export", str(path), "--ffmpeg", FFMPEG, "--ffprobe", FFPROBE)
        self.assertEqual((code, answer["items"][0]["status"]), (0, "skipped: content not here"))
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
