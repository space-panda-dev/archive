"""`capture import` end to end: synthetic recorder cards, a scratch archive
made by `archive init`, the real Door, and the real enrichment chain.

Every name and sound is synthetic: the audio is numpy chords and noise, the
recorder names carry invented dates in 2001.
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
from typing import Any
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "enrich"))

import adapter  # noqa: E402
import capture  # noqa: E402
import derived  # noqa: E402
import support  # noqa: E402
from grammar import ZoomH4E  # noqa: E402
from support import needs_door  # noqa: E402

RATE = 8000
STREAM = "archive.claim/audio_stream"
SPANS = "archive.claim/span_suggestions"
PARTS = "archive.claim/session_parts"


def music(seconds: float) -> list:
    """Enough structure for the detector: chords, talk, and chords."""
    third = seconds / 3
    return [support.chord(third), support.talk(third), support.chord(seconds - 2 * third, (196.0, 246.94, 293.66))]


def mic(seconds: float, seed: int = 5) -> list:
    return [support.talk(seconds, amplitude=0.1, seed=seed)]


def card_file(directory: Path, name: str, pieces: list) -> Path:
    path, _ = support.synthetic_wav(directory / name, RATE, pieces)
    return path


def fake_program(directory: Path, name: str, body: str) -> str:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


def snapshot(directory: Path) -> dict[str, tuple[int, int, int, bytes]]:
    """Every file under the directory with its modification time, mode, link
    count, and bytes: a staged copy that shared the input's inode, or locked
    it read-only, would change the link count or the mode."""
    return {str(path.relative_to(directory)): (path.stat().st_mtime_ns, path.stat().st_mode, path.stat().st_nlink,
                                               path.read_bytes())
            for path in sorted(directory.rglob("*")) if path.is_file()}


@needs_door
class CaptureImport(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = support.scratch(self, "capture-")
        self.env = support.environment()
        self.root = self.tmp / "archive"
        completed = subprocess.run([support.ARCHIVE, "init", "--operator", "capture-operator", str(self.root)],
                                   cwd=self.tmp, capture_output=True, text=True, env=self.env)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.staging = self.tmp / "staging"
        self.media = self.tmp / "media"
        self.media.mkdir()
        self.card = self.tmp / "card"
        self.card.mkdir()

    # -- helpers ---------------------------------------------------------

    def run_import(self, directory: Path | None = None, *extra: str, grammars: Any = None,
                   door: str | None = None, ffmpeg: str | None = None) -> tuple[int, dict]:
        argv = ["import", str(directory or self.card), "--archive", str(self.root), "--staging", str(self.staging),
                "--derived-media", str(self.media), "--door", door or support.DOOR,
                "--archive-command", support.ARCHIVE, "--ffmpeg", ffmpeg or support.FFMPEG,
                "--ffprobe", support.FFPROBE, *extra]
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.env), redirect_stdout(out), redirect_stderr(err):
            code = capture.main(argv, grammars or (ZoomH4E(),))
        return code, json.loads(out.getvalue() or err.getvalue())

    def records(self) -> list[dict]:
        return support.export(self.root)["records"]

    def cites(self, record: dict, contract: str) -> bool:
        return any(c["id"] == contract for c in record.get("contracts", []))

    def claims(self, contract: str) -> list[dict]:
        return [r for r in self.records() if r["shape"] == "claim" and self.cites(r, contract)]

    def about(self, claims: list[dict], role: str, object_id: str) -> list[dict]:
        return [c for c in claims if any(ref["role"] == role and ref["id"] == object_id for ref in c["refs"])]

    def artifact_of(self, path: Path) -> dict:
        digest = adapter.digest_of(path.read_bytes())
        [record] = [r for r in self.records() if r["shape"] == "artifact"
                    and r["payload"]["content"]["digest"] == digest]
        return record

    def session_id(self, first: Path) -> str:
        archive_id = support.export(self.root)["archive_id"]
        name = "session-" + adapter.digest_of(first.read_bytes()).removeprefix("sha256:")
        return adapter.object_id(archive_id, capture.SESSION_NAMESPACE, name)

    def parts_of(self, session_id: str, answered: dict) -> dict[str, list[str]]:
        """Each source's parts claim, found by the object the import answered,
        as its Artifact objects in reference order. Every parts claim about
        the session is one of them."""
        claims = {claim["object_id"]: claim for claim in self.about(self.claims(PARTS), "session", session_id)}
        self.assertEqual(sorted(claims), sorted(part["object_id"] for part in answered["parts"]))
        return {part["source"]: [ref["id"] for ref in claims[part["object_id"]]["refs"] if ref["role"] == "part"]
                for part in answered["parts"]}

    def assert_media(self, path: Path) -> None:
        digest = adapter.digest_of(path.read_bytes())
        for kind in derived.KINDS:
            self.assertTrue(derived.path(self.media, digest, kind).is_file(), (path.name, kind))

    # -- sessions --------------------------------------------------------

    def test_a_two_source_take_is_one_session_with_a_parts_claim_per_source(self) -> None:
        lr = card_file(self.card, "010101_120000_TrLR.WAV", music(24))
        mic_file = card_file(self.card, "010101_120000_TrMic.WAV", mic(24))
        (self.card / ".hidden").write_text("not recorder output")
        self.assertNotEqual(lr.read_bytes(), mic_file.read_bytes())
        before = snapshot(self.card)

        code, answer = self.run_import()
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        self.assertEqual(answer["ignored"], [".hidden"])
        [session] = answer["sessions"]
        self.assertEqual((session["status"], session["label"]), ("committed", "Zoom H4essential 2001-01-01 12:00:00"))
        self.assertEqual(session["sources"], [{"source": "TrLR", "files": [lr.name]},
                                              {"source": "TrMic", "files": [mic_file.name]}])

        lr_artifact, mic_artifact = self.artifact_of(lr), self.artifact_of(mic_file)
        session_id = self.session_id(lr)
        [entity] = [r for r in self.records() if r["object_id"] == session_id]
        self.assertEqual(entity["payload"], {"kind": "recording_session", "label": session["label"]})
        self.assertEqual(entity["agent"], capture.AGENT)
        self.assertEqual(self.parts_of(session_id, session),
                         {"TrLR": [lr_artifact["object_id"]], "TrMic": [mic_artifact["object_id"]]})
        # Each parts claim pins the exact revisions.
        for claim in self.about(self.claims(PARTS), "session", session_id):
            self.assertEqual([ref.get("revision_id") for ref in claim["refs"] if ref["role"] == "session"],
                             [entity["revision_id"]])

        # The chain: facts for both, spans only for the primary, media for both.
        streams = self.claims(STREAM)
        self.assertEqual(len(self.about(streams, "artifact", lr_artifact["object_id"])), 1)
        self.assertEqual(len(self.about(streams, "artifact", mic_artifact["object_id"])), 1)
        spans = self.claims(SPANS)
        self.assertEqual(len(self.about(spans, "source", lr_artifact["object_id"])), 1)
        self.assertEqual(self.about(spans, "source", mic_artifact["object_id"]), [])
        self.assert_media(lr)
        self.assert_media(mic_file)
        self.assertEqual([(entry["file"], entry["probe"], entry.get("detect")) for entry in answer["chain"]],
                         [(lr.name, "committed", "committed"), (mic_file.name, "committed", None)])

        # The input is exactly as it was.
        self.assertEqual(snapshot(self.card), before)

    def test_a_studio_import_detects_with_the_studio_preset(self) -> None:
        import detector
        card_file(self.card, "010101_120000_TrLR.WAV", music(24))
        code, answer = self.run_import(None, "--detector-preset", "studio")
        self.assertEqual(code, 0, answer)
        [claim] = self.claims(SPANS)
        [run_ref] = [ref for ref in claim["refs"] if ref["role"] == "run"]
        by_revision = {r["revision_id"]: r for r in self.records()}
        [parameters_ref] = [ref for ref in by_revision[run_ref["revision_id"]]["refs"] if ref["role"] == "parameters"]
        data = adapter.canonical(detector.parameters("studio"))
        # Anchor: the presets' parameters differ, so the digest names one.
        self.assertNotEqual(data, adapter.canonical(detector.parameters("jam")))
        self.assertEqual(by_revision[parameters_ref["revision_id"]]["payload"]["content"],
                         {"digest": adapter.digest_of(data), "size": len(data)})
        self.assertNotIn("talk", {span["class"] for span in claim["payload"]["spans"]})

    def test_a_second_import_changes_nothing_and_does_no_work(self) -> None:
        card_file(self.card, "010101_120000_TrLR.WAV", music(24))
        card_file(self.card, "010101_120000_TrMic.WAV", mic(24))
        self.assertEqual(self.run_import()[0], 0)
        head, staged = support.head(self.root), support.head(self.staging)
        media = snapshot(self.media)
        before = snapshot(self.card)
        bomb = fake_program(self.tmp, "bomb", "echo called >> \"$0.log\"; exit 9\n")

        code, answer = self.run_import(door=bomb, ffmpeg=bomb)
        self.assertEqual((code, answer["status"]), (0, "unchanged"), answer)
        self.assertEqual([s["status"] for s in answer["sessions"]], ["already_imported"])
        self.assertEqual(answer["chain"], [])
        self.assertFalse(Path(bomb + ".log").exists(), "the second import ran Door or ffmpeg")
        self.assertEqual(support.head(self.root), head)
        self.assertEqual(support.head(self.staging), staged)
        self.assertEqual(snapshot(self.media), media)
        self.assertEqual(snapshot(self.card), before)

    # -- the chain's cost ----------------------------------------------------

    def two_source_take(self) -> tuple[Path, Path]:
        return (card_file(self.card, "010101_120000_TrLR.WAV", music(24)),
                card_file(self.card, "010101_120000_TrMic.WAV", mic(24)))

    def logging(self, name: str, real: str, before: str = "") -> str:
        """A wrapper that logs each call's arguments, one line each, and then
        runs the real program."""
        return fake_program(self.tmp, name, f'echo "$*" >> "$0.log"\n{before}exec "{real}" "$@"\n')

    def test_the_chain_exports_the_archive_in_full_once(self) -> None:
        self.two_source_take()
        archive = self.logging("archive-logged", support.ARCHIVE)
        argv = ["import", str(self.card), "--archive", str(self.root), "--staging", str(self.staging),
                "--derived-media", str(self.media), "--door", support.DOOR, "--archive-command", archive,
                "--ffmpeg", support.FFMPEG, "--ffprobe", support.FFPROBE]
        out = io.StringIO()
        with patch.dict(os.environ, self.env), redirect_stdout(out):
            self.assertEqual(capture.main(argv, (ZoomH4E(),)), 0, out.getvalue())
        calls = Path(archive + ".log").read_text().splitlines()
        # One full export to classify the card, one for the whole chain; three
        # runs (two probes, one detect) each brought it up to date instead.
        # The match committed nothing (the archive has no Works), so nothing
        # was brought up to date after it.
        self.assertEqual(calls.count("export"), 2, calls)
        self.assertEqual(len([c for c in calls if c.startswith("export --since ")]), 3, calls)
        # Anchor: the chain's three runs were committed, and the match ran.
        chain = json.loads(out.getvalue())["chain"]
        self.assertEqual([(e["probe"], e.get("detect")) for e in chain], [("committed", "committed"), ("committed", None)])
        self.assertEqual([e.get("match") for e in chain], [{"status": "nothing_proposed", "proposed": 0}, None])

    def overlapping_ffmpeg(self, wait: int) -> str:
        """An ffmpeg that logs each call, and whose encodes each wait up to
        ``wait`` seconds for another encode to be running, logging whether one
        was. An encode's mark is removed when it ends."""
        marks = self.tmp / "encoding"
        marks.mkdir()
        body = (f'echo "$*" >> "$0.log"\n'
                f'case "$*" in *" aac "*) ;; *) exec "{support.FFMPEG}" "$@";; esac\n'
                f'touch "{marks}/$$"; i=0\n'
                f'while [ "$(ls "{marks}" | wc -l)" -lt 2 ] && [ $i -lt {wait * 10} ]; do sleep 0.1; i=$((i+1)); done\n'
                f'if [ "$(ls "{marks}" | wc -l)" -ge 2 ]; then echo overlap >> "$0.log"; fi\n'
                f'"{support.FFMPEG}" "$@"; status=$?\n'
                f'rm -f "{marks}/$$"; exit $status\n')
        return fake_program(self.tmp, "ffmpeg-overlap", body)

    def test_listening_copies_follow_every_analysis_and_run_side_by_side(self) -> None:
        self.two_source_take()
        ffmpeg = self.overlapping_ffmpeg(wait=20)
        code, answer = self.run_import(None, "--jobs", "2", ffmpeg=ffmpeg)
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        calls = Path(ffmpeg + ".log").read_text().splitlines()
        encodes = [index for index, call in enumerate(calls) if " aac " in f" {call} "]
        self.assertEqual(len(encodes), 2, calls)
        others = [index for index, call in enumerate(calls) if call != "overlap" and index not in encodes]
        # Probes, the detector, and peaks all decoded before the first encode.
        self.assertGreater(min(encodes), max(others), calls)
        self.assertIn("overlap", calls)
        self.assertEqual([e["media"] for e in answer["chain"]],
                         [{"peaks": "written", "listen": "written"}] * 2)

    def test_one_job_encodes_one_at_a_time(self) -> None:
        # Anchor for the test above: the wrapper reports overlap only when
        # two encodes really ran at once.
        self.two_source_take()
        ffmpeg = self.overlapping_ffmpeg(wait=2)
        code, answer = self.run_import(None, "--jobs", "1", ffmpeg=ffmpeg)
        self.assertEqual(code, 0, answer)
        self.assertNotIn("overlap", Path(ffmpeg + ".log").read_text().splitlines())

    def test_deferred_listening_copies_are_not_encoded(self) -> None:
        lr, mic_file = self.two_source_take()
        ffmpeg = self.logging("ffmpeg", support.FFMPEG)
        code, answer = self.run_import(None, "--defer-listening-copies", ffmpeg=ffmpeg)
        self.assertEqual(code, 0, answer)
        self.assertFalse([c for c in Path(ffmpeg + ".log").read_text().splitlines() if " aac " in f" {c} "])
        for path in (lr, mic_file):
            digest = adapter.digest_of(path.read_bytes())
            self.assertTrue(derived.path(self.media, digest, "peaks").is_file())
            self.assertFalse(derived.path(self.media, digest, "listen").exists())
        self.assertEqual([e["media"] for e in answer["chain"]], [{"peaks": "written", "listen": "deferred"}] * 2)

    def test_a_failed_peaks_file_is_reported_and_stops_before_listening_copies(self) -> None:
        lr, mic_file = self.two_source_take()
        # Peaks of the second file cannot be written: its directory is a file.
        digest = adapter.digest_of(mic_file.read_bytes())
        blocker = derived.directory(self.media, digest)
        blocker.parent.mkdir(parents=True)
        blocker.write_text("in the way")
        ffmpeg = self.logging("ffmpeg", support.FFMPEG)
        code, answer = self.run_import(None, "--jobs", "2", ffmpeg=ffmpeg)
        self.assertEqual((code, answer["status"]), (1, "failed"), answer)
        self.assertIn(f"derived media (peaks) for {mic_file.name}", answer["detail"])
        self.assertEqual([e["media"] for e in answer["chain"]], [{"peaks": "written"}, {"peaks": "failed"}])
        self.assertFalse([c for c in Path(ffmpeg + ".log").read_text().splitlines() if " aac " in f" {c} "])

    def test_takes_are_sessions_and_a_rollover_joins_two_takes(self) -> None:
        first_lr = card_file(self.card, "010101_120000_TrLR.WAV", music(24))
        first_mic = card_file(self.card, "010101_120000_TrMic.WAV", mic(24))
        second_lr = card_file(self.card, "010101_120024_TrLR.WAV", music(22))
        second_mic = card_file(self.card, "010101_120024_TrMic.WAV", mic(22, seed=6))
        later_lr = card_file(self.card, "010101_130000_TrLR.WAV", music(21))
        later_mic = card_file(self.card, "010101_130000_TrMic.WAV", mic(21, seed=7))
        cap = first_lr.stat().st_size
        # Anchor: the first take's files are at the cap, the others are not.
        self.assertEqual(first_mic.stat().st_size, cap)
        self.assertTrue(all(p.stat().st_size != cap for p in (second_lr, second_mic, later_lr, later_mic)))
        small_cap = (ZoomH4E(split_size=cap),)

        # Anchor: at the recorder's real cap nothing rolls over, so three sessions.
        code, predicted = self.run_import(None, "--dry-run")
        self.assertEqual((code, len(predicted["sessions"])), (0, 3), predicted)

        code, answer = self.run_import(grammars=small_cap)
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        self.assertEqual([s["sources"] for s in answer["sessions"]], [
            [{"source": "TrLR", "files": [first_lr.name, second_lr.name]},
             {"source": "TrMic", "files": [first_mic.name, second_mic.name]}],
            [{"source": "TrLR", "files": [later_lr.name]}, {"source": "TrMic", "files": [later_mic.name]}]])
        ids = {path: self.artifact_of(path)["object_id"]
               for path in (first_lr, first_mic, second_lr, second_mic, later_lr, later_mic)}
        first, later = answer["sessions"]
        self.assertEqual(self.parts_of(self.session_id(first_lr), first),
                         {"TrLR": [ids[first_lr], ids[second_lr]], "TrMic": [ids[first_mic], ids[second_mic]]})
        self.assertEqual(self.parts_of(self.session_id(later_lr), later),
                         {"TrLR": [ids[later_lr]], "TrMic": [ids[later_mic]]})
        # Spans for every part of each primary source, and no other.
        spans = self.claims(SPANS)
        for path in (first_lr, second_lr, later_lr):
            self.assertEqual(len(self.about(spans, "source", ids[path])), 1, path.name)
        self.assertEqual(len(spans), 3)
        self.assertEqual(len(self.claims(STREAM)), 6)
        for path in ids:
            self.assert_media(path)

        head = support.head(self.root)
        code, again = self.run_import(grammars=small_cap)
        self.assertEqual((code, again["status"]), (0, "unchanged"), again)
        self.assertEqual(support.head(self.root), head)

    def test_a_dry_run_predicts_what_the_import_commits_and_writes_nothing(self) -> None:
        card_file(self.card, "010101_120000_TrLR.WAV", music(24))
        head = support.head(self.root)
        code, predicted = self.run_import(None, "--dry-run")
        self.assertEqual((code, predicted["status"]), (0, "predicted"), predicted)
        self.assertEqual([s["status"] for s in predicted["sessions"]], ["predicted"])
        self.assertEqual(predicted["chain"], [])
        self.assertEqual(support.head(self.root), head)
        self.assertFalse(self.staging.exists())
        self.assertEqual(list(self.media.iterdir()), [])
        remotes = subprocess.run(["git", "remote"], cwd=self.root, capture_output=True, text=True).stdout.split()
        self.assertNotIn(capture.STAGING_REMOTE, remotes)

        code, committed = self.run_import()
        self.assertEqual(code, 0, committed)
        planned = predicted["sessions"][0]
        self.assertEqual(committed["sessions"][0]["session"], planned["session"])
        self.assertEqual(committed["sessions"][0]["parts"], planned["parts"])
        self.assertNotEqual(support.head(self.root), head)

    # -- files no grammar recognises -------------------------------------

    def test_a_file_no_grammar_recognises_is_refused_without_a_session_name(self) -> None:
        card_file(self.card, "010101_120000_TrLR.WAV", music(24))
        card_file(self.card, "memo.wav", music(21))
        head = support.head(self.root)
        code, answer = self.run_import()
        self.assertEqual((code, answer["status"]), (1, "failed"), answer)
        self.assertIn("memo.wav", answer["detail"])
        self.assertIn("--session-name", answer["detail"])
        self.assertEqual(support.head(self.root), head)
        self.assertFalse(self.staging.exists())

    def test_a_named_session_holds_unrecognised_files_in_path_order(self) -> None:
        first = card_file(self.card, "a-memo.wav", music(21))
        second = card_file(self.card, "b-memo.wav", music(24))
        notes = self.card / "c-picture.mkv"  # a picture with no audio stream
        support.ffmpeg("-f", "lavfi", "-i", "testsrc=d=0.2:s=32x32", "-c:v", "ffv1", str(notes))
        code, answer = self.run_import(None, "--session-name", "Synthetic named session")
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        self.assertTrue(answer["session_name_used"])
        [session] = answer["sessions"]
        self.assertEqual((session["label"], session["grammar"]), ("Synthetic named session", None))
        self.assertEqual(session["sources"], [{"source": "files", "files": [first.name, second.name, notes.name]}])
        ids = [self.artifact_of(path)["object_id"] for path in (first, second, notes)]
        self.assertEqual(self.parts_of(self.session_id(first), session), {"files": ids})
        # The picture is probed, has no countable audio stream, and
        # nothing else runs on it.
        chain = {entry["file"]: entry for entry in answer["chain"]}
        self.assertEqual(chain[notes.name]["probe"], "nothing_proposed")
        self.assertTrue(chain[notes.name]["detect"].startswith("skipped"))
        self.assertTrue(chain[notes.name]["media"].startswith("skipped"))
        self.assertEqual(self.about(self.claims(STREAM), "artifact", ids[2]), [])
        self.assertEqual(len(self.claims(SPANS)), 2)
        self.assert_media(first)
        self.assertFalse(derived.directory(self.media, adapter.digest_of(notes.read_bytes())).exists())

    # -- what is already in the archive ----------------------------------

    def test_content_already_an_artifact_is_skipped_and_a_partly_known_session_refused(self) -> None:
        lr = card_file(self.tmp, "010101_120000_TrLR.WAV", music(24))
        mic_file = card_file(self.tmp, "010101_120000_TrMic.WAV", mic(24))
        # The archive already holds the LR content, admitted by the fixture builder.
        self.root, _ = support.build_archive(self, {"lr": lr})
        known = self.tmp / "known"
        known.mkdir()
        shutil.copyfile(lr, known / lr.name)
        head = support.head(self.root)
        code, answer = self.run_import(known)
        self.assertEqual((code, answer["status"]), (0, "unchanged"), answer)
        self.assertEqual([s["status"] for s in answer["sessions"]], ["already_imported"])
        self.assertEqual(support.head(self.root), head)

        shutil.copyfile(mic_file, known / mic_file.name)
        code, answer = self.run_import(known)
        self.assertEqual(code, 1, answer)
        self.assertIn("partly imported", answer["detail"])
        self.assertEqual(support.head(self.root), head)

    # -- refusals --------------------------------------------------------

    def test_the_same_content_in_two_files_is_refused(self) -> None:
        memo = card_file(self.card, "a.wav", music(21))
        shutil.copyfile(memo, self.card / "b.wav")
        code, answer = self.run_import(None, "--session-name", "Twice")
        self.assertEqual(code, 1, answer)
        self.assertIn("more than one file", answer["detail"])

    def test_one_take_and_source_in_two_places_is_refused(self) -> None:
        for folder, seconds in (("one", 21), ("two", 22)):
            (self.card / folder).mkdir()
            card_file(self.card / folder, "010101_120000_TrLR.WAV", music(seconds))
        code, answer = self.run_import()
        self.assertEqual(code, 1, answer)
        self.assertIn("same take and source", answer["detail"])

    def test_sources_that_disagree_about_a_rollover_are_refused(self) -> None:
        first_lr = card_file(self.card, "010101_120000_TrLR.WAV", music(24))
        card_file(self.card, "010101_120000_TrMic.WAV", mic(20))  # stopped early: not at the cap
        card_file(self.card, "010101_120024_TrLR.WAV", music(21))
        card_file(self.card, "010101_120024_TrMic.WAV", mic(21, seed=6))
        code, answer = self.run_import(grammars=(ZoomH4E(split_size=first_lr.stat().st_size),))
        self.assertEqual(code, 1, answer)
        self.assertIn("disagree about a rollover", answer["detail"])

    def test_a_rollover_take_missing_a_source_is_refused(self) -> None:
        first_lr = card_file(self.card, "010101_120000_TrLR.WAV", music(24))
        card_file(self.card, "010101_120000_TrMic.WAV", mic(24))
        card_file(self.card, "010101_120024_TrLR.WAV", music(21))
        code, answer = self.run_import(grammars=(ZoomH4E(split_size=first_lr.stat().st_size),))
        self.assertEqual(code, 1, answer)
        self.assertIn("disagree about a rollover", answer["detail"])

    def test_an_empty_directory_is_a_failure(self) -> None:
        code, answer = self.run_import()
        self.assertEqual(code, 1, answer)
        self.assertIn("holds no file", answer["detail"])
        (self.card / ".Trashes").mkdir()
        (self.card / "._010101_120000_TrLR.WAV").write_bytes(b"\0")
        code, answer = self.run_import()
        self.assertEqual(code, 1, answer)
        self.assertIn("passed over: .Trashes/, ._010101_120000_TrLR.WAV", answer["detail"])

    def test_a_directory_that_cannot_be_listed_is_refused_not_skipped(self) -> None:
        card_file(self.card, "010101_120000_TrLR.WAV", music(21))
        hidden_from_us = self.card / "more"
        hidden_from_us.mkdir()
        card_file(hidden_from_us, "010101_130000_TrLR.WAV", music(22))
        hidden_from_us.chmod(0)
        self.addCleanup(hidden_from_us.chmod, 0o700)
        head = support.head(self.root)
        code, answer = self.run_import()
        self.assertEqual((code, answer["status"]), (1, "failed"), answer)
        self.assertIn("more could not be read", answer["detail"])
        self.assertEqual(support.head(self.root), head)
        self.assertFalse(self.staging.exists())

    def test_a_file_that_cannot_be_read_is_refused(self) -> None:
        card_file(self.card, "010101_120000_TrLR.WAV", music(21))
        locked = card_file(self.card, "010101_130000_TrLR.WAV", music(22))
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o600)
        code, answer = self.run_import()
        self.assertEqual((code, answer["status"]), (1, "failed"), answer)
        self.assertIn("010101_130000_TrLR.WAV could not be read", answer["detail"])
        self.assertFalse(self.staging.exists())

    def test_a_symbolic_link_is_refused(self) -> None:
        target = card_file(self.tmp, "010101_120000_TrLR.WAV", music(21))
        (self.card / target.name).symlink_to(target)
        code, answer = self.run_import()
        self.assertEqual(code, 1, answer)
        self.assertIn("not a regular file", answer["detail"])
        (self.card / target.name).unlink()
        (self.card / "linked").symlink_to(self.tmp)
        code, answer = self.run_import()
        self.assertEqual(code, 1, answer)
        self.assertIn("symbolic link", answer["detail"])

    def test_places_that_overlap_the_input_are_refused(self) -> None:
        card_file(self.card, "010101_120000_TrLR.WAV", music(21))
        inside = self.card / "staging"
        argv_staging = ["import", str(self.card), "--archive", str(self.root), "--staging", str(inside),
                        "--derived-media", str(self.media), "--door", support.DOOR,
                        "--archive-command", support.ARCHIVE]
        err = io.StringIO()
        with patch.dict(os.environ, self.env), redirect_stderr(err):
            code = capture.main(argv_staging)
        self.assertEqual(code, 1)
        self.assertIn("overlap", json.loads(err.getvalue())["detail"])
        self.assertFalse(inside.exists())
        code, answer = self.run_import(self.tmp)  # the input holds the archive
        self.assertEqual(code, 1, answer)
        self.assertIn("overlap", answer["detail"])

    def test_a_missing_derived_media_root_is_refused_before_anything_is_written(self) -> None:
        card_file(self.card, "010101_120000_TrLR.WAV", music(21))
        self.media.rmdir()
        head = support.head(self.root)
        code, answer = self.run_import()
        self.assertEqual(code, 1, answer)
        self.assertIn("derived-media root", answer["detail"])
        self.assertEqual(support.head(self.root), head)

    def test_door_refusing_a_session_leaves_the_archive_unchanged(self) -> None:
        card_file(self.card, "010101_120000_TrLR.WAV", music(21))
        head = support.head(self.root)
        refusing = fake_program(self.tmp, "door", "echo refused >&2; exit 1\n")
        code, answer = self.run_import(door=refusing)
        self.assertEqual(code, 1, answer)
        self.assertIn("door admit", answer["detail"])
        self.assertEqual(answer["sessions"][0]["status"], "new")
        self.assertEqual(support.head(self.root), head)

    def test_a_door_answer_other_than_the_plan_is_refused(self) -> None:
        card_file(self.card, "010101_120000_TrLR.WAV", music(21))
        wrong = fake_program(self.tmp, "door", "echo '{\"status\": \"predicted\", \"revision_ids\": []}'\n")
        code, answer = self.run_import(None, "--dry-run", door=wrong)
        self.assertEqual(code, 1, answer)
        self.assertIn("planned", answer["detail"])
        garbled = fake_program(self.tmp, "garbled", "echo not json\n")
        code, answer = self.run_import(None, "--dry-run", door=garbled)
        self.assertEqual(code, 1, answer)
        self.assertIn("None", answer["detail"])

    def test_a_failing_chain_reports_the_committed_session(self) -> None:
        lr = card_file(self.card, "010101_120000_TrLR.WAV", music(21))
        broken = fake_program(self.tmp, "ffmpeg", "exit 1\n")
        head = support.head(self.root)
        code, answer = self.run_import(ffmpeg=broken)
        self.assertEqual((code, answer["status"]), (1, "failed"), answer)
        self.assertIn("enrich probe", answer["detail"])
        self.assertEqual([s["status"] for s in answer["sessions"]], ["committed"])
        self.assertNotEqual(support.head(self.root), head)
        self.artifact_of(lr)  # admitted
        self.assertEqual(self.claims(STREAM), [])

    def test_a_capped_file_whose_header_cannot_be_read_is_refused(self) -> None:
        broken = self.card / "010101_120000_TrLR.WAV"
        broken.write_bytes(b"not a wave file" * 4)
        card_file(self.card, "010101_120001_TrLR.WAV", music(21))
        code, answer = self.run_import(grammars=(ZoomH4E(split_size=broken.stat().st_size),))
        self.assertEqual(code, 1, answer)
        self.assertIn("cannot decide whether 010101_120001_TrLR.WAV continues", answer["detail"])

    def test_an_existing_session_whose_files_are_new_is_refused(self) -> None:
        lr = card_file(self.card, "010101_120000_TrLR.WAV", music(21))
        other = card_file(self.tmp, "other.wav", music(22))
        name = "session-" + adapter.digest_of(lr.read_bytes()).removeprefix("sha256:")
        entity = {"key": "session", "namespace": capture.SESSION_NAMESPACE, "name": name, "shape": "entity",
                  "payload": {"kind": "recording_session", "label": "Synthetic"}}
        self.root, _ = support.build_archive(self, {"other": other},
                                             ({"name": "session", "command": "admit", "records": [entity]},))
        head = support.head(self.root)
        code, answer = self.run_import()
        self.assertEqual(code, 1, answer)
        self.assertIn(f"session {name} already exists", answer["detail"])
        self.assertEqual(support.head(self.root), head)

    def test_a_door_that_cannot_run_is_refused(self) -> None:
        card_file(self.card, "010101_120000_TrLR.WAV", music(21))
        code, answer = self.run_import(None, "--dry-run", door=str(self.tmp / "absent-door"))
        self.assertEqual(code, 1, answer)
        self.assertIn("could not run", answer["detail"])

    def test_garbled_derived_media_answers_are_refused(self) -> None:
        # A copy of the enrichment tools whose media tool answers nonsense.
        tools = self.tmp / "tools"
        shutil.copytree(HERE.parent / "enrich", tools, ignore=shutil.ignore_patterns("__pycache__", "tests"))
        (tools / "media.py").write_text("print('not json')\n")
        card_file(self.card, "010101_120000_TrLR.WAV", music(24))
        with patch.object(capture, "ENRICH", tools):
            code, answer = self.run_import()
        self.assertEqual(code, 1, answer)
        self.assertIn("derived media (peaks) for 010101_120000_TrLR.WAV answered", answer["detail"])
        self.assertEqual(answer["chain"][0]["detect"], "committed")

    def test_an_archive_without_the_parts_contract_is_refused(self) -> None:
        # A reader whose archive does not install session_parts.
        card_file(self.card, "010101_120000_TrLR.WAV", music(21))
        real = adapter.ArchiveReader.load

        def without_parts(reader: adapter.ArchiveReader, export: dict) -> None:
            export = {**export, "contracts": [c for c in export["contracts"] if c["id"] != PARTS]}
            real(reader, export)

        with patch.object(adapter.ArchiveReader, "load", without_parts):
            code, answer = self.run_import()
        self.assertEqual(code, 1, answer)
        self.assertIn("does not install archive.claim/session_parts", answer["detail"])


class Steps(unittest.TestCase):
    """The importer's own failure branches that need no archive."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="capture-steps-"))
        self.addCleanup(support.remove_tree, self.tmp)
        self.args = capture.argparse.Namespace(archive=self.tmp, staging=self.tmp / "s", door="door",
                                               archive_command="archive", ffmpeg="ffmpeg", ffprobe="ffprobe",
                                               detector_preset="jam")

    def fake_enrich(self, body: str) -> Path:
        tools = self.tmp / "tools"
        tools.mkdir(exist_ok=True)
        (tools / "enrich.py").write_text(body)
        return tools

    def test_an_enrich_answer_that_is_not_json_is_a_failure(self) -> None:
        with patch.object(capture, "ENRICH", self.fake_enrich("print('not json')\n")):
            with self.assertRaisesRegex(capture.CaptureError, "enrich probe of x failed \\(0\\): not json"):
                capture.enrich("probe", "x", self.args, dict(os.environ))

    def test_an_enrich_answer_without_a_status_is_a_failure(self) -> None:
        with patch.object(capture, "ENRICH", self.fake_enrich("print('{}')\n")):
            with self.assertRaisesRegex(capture.CaptureError, "failed"):
                capture.enrich("detect", "x", self.args, dict(os.environ))

    def test_nothing_proposed_is_an_answer(self) -> None:
        body = "import sys\nprint('{\"status\": \"nothing_proposed\"}')\nsys.exit(3)\n"
        with patch.object(capture, "ENRICH", self.fake_enrich(body)):
            self.assertEqual(capture.enrich("probe", "x", self.args, dict(os.environ)),
                             (3, {"status": "nothing_proposed"}))

    def test_match_windows_is_given_the_profile_cache_only_when_one_is_named(self) -> None:
        body = "import json, sys\nprint(json.dumps({'status': 'unchanged', 'argv': sys.argv[1:]}))\n"
        seen = []
        with patch.object(capture, "ENRICH", self.fake_enrich(body)):
            for cache in (None, self.tmp / "profiles"):
                args = capture.argparse.Namespace(**vars(self.args), jobs=2, profile_cache=cache)
                seen.append(capture.enrich("match-windows", "x", args, dict(os.environ))[1]["argv"])
        self.assertNotIn("--profile-cache", seen[0])
        self.assertIn("--jobs", seen[0])  # anchor: the arguments were read
        at = seen[1].index("--profile-cache")
        self.assertEqual(seen[1][at + 1], str(self.tmp / "profiles"))

    def test_detect_is_given_the_detector_preset(self) -> None:
        body = "import json, sys\nprint(json.dumps({'status': 'unchanged', 'argv': sys.argv[1:]}))\n"
        seen = {}
        with patch.object(capture, "ENRICH", self.fake_enrich(body)):
            for preset in ("jam", "studio"):
                args = capture.argparse.Namespace(**{**vars(self.args), "detector_preset": preset})
                seen[preset] = capture.enrich("detect", "x", args, dict(os.environ))[1]["argv"]
            probe = capture.enrich("probe", "x", self.args, dict(os.environ))[1]["argv"]
        for preset, argv in seen.items():
            self.assertEqual(argv[argv.index("--preset") + 1], preset)
        self.assertIn("--artifact", probe)  # anchor: the arguments were read
        self.assertNotIn("--preset", probe)

    def test_the_detector_presets_are_the_detectors(self) -> None:
        import detector
        self.assertEqual(sorted(capture.DETECTOR_PRESETS), sorted(detector.PRESETS))

    def test_an_enrich_that_cannot_start_is_a_failure(self) -> None:
        with patch.object(capture.sys, "executable", str(self.tmp / "no-python")):
            with self.assertRaisesRegex(capture.CaptureError, "enrich probe could not run"):
                capture.enrich("probe", "x", self.args, dict(os.environ))

    def test_door_needs_git_and_git_annex(self) -> None:
        bare = {"PATH": str(self.tmp)}
        with patch.dict(os.environ, bare, clear=True):
            with self.assertRaisesRegex(capture.CaptureError, "git is not on PATH and ARCHIVE_GIT_EXECUTABLE is unset"):
                capture.environment()
        found = {"PATH": str(self.tmp), "ARCHIVE_GIT_EXECUTABLE": "/bin/git-of-choice",
                 "ARCHIVE_GIT_ANNEX_EXECUTABLE": "/bin/annex-of-choice"}
        with patch.dict(os.environ, found, clear=True):
            env = capture.environment()
        self.assertEqual(env["ARCHIVE_GIT_EXECUTABLE"], "/bin/git-of-choice")


class SuggestionsMadeBy(unittest.TestCase):
    """The chain matches the suggestions the detector's run made, whether
    that run was committed now or found unchanged."""

    def reader(self, *runs: str) -> adapter.ArchiveReader:
        def suggestions(index: int, run: str) -> dict:
            return {"shape": "claim", "object_id": f"urn:uuid:{index:08d}-0000-4000-8000-000000000000",
                    "revision_id": "sha256:" + str(index) * 64,
                    "contracts": [{"id": "archive.claim/span_suggestions", "version": "1"}],
                    "refs": [{"role": "run", "id": "urn:uuid:run", "revision_id": run}]}
        return adapter.ArchiveReader.from_export(Path("/nonexistent"), {
            "archive_id": "urn:uuid:00000000-0000-4000-8000-000000000000", "spec": "archive/0.0.8", "contracts": [],
            "records": [suggestions(index + 1, run) for index, run in enumerate(runs)]})

    def test_exactly_one_claim_of_the_run_is_found_or_refused(self) -> None:
        reader = self.reader("sha256:" + "a" * 64, "sha256:" + "b" * 64)
        self.assertEqual(capture.suggestions_made_by(reader, {"run": {"revision_id": "sha256:" + "b" * 64}}),
                         "urn:uuid:00000002-0000-4000-8000-000000000000")
        for answer in ({"run": {"revision_id": "sha256:" + "c" * 64}}, {"status": "failed"}):
            with self.assertRaisesRegex(capture.CaptureError, "span suggestions claims, not one"):
                capture.suggestions_made_by(reader, answer)
        twice = self.reader("sha256:" + "a" * 64, "sha256:" + "a" * 64)
        with self.assertRaisesRegex(capture.CaptureError, "made 2 span suggestions claims"):
            capture.suggestions_made_by(twice, {"run": {"revision_id": "sha256:" + "a" * 64}})


class CommandLine(unittest.TestCase):
    def test_a_directory_must_hold_exactly_what_its_manifest_lists(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="capture-manifest-"))
        self.addCleanup(support.remove_tree, tmp)

        def item(path: str, data: bytes) -> capture.InputFile:
            return capture.InputFile(path, tmp / path, len(data), adapter.digest_of(data))

        files = [item("a.WAV", b"aaaa"), item("b.WAV", b"bbbb")]
        manifest = tmp / "manifest.json"

        def listing(*entries: capture.InputFile) -> Path:
            manifest.write_text(json.dumps({"files": [{"path": e.relative, "digest": e.digest, "size": e.size}
                                                      for e in entries]}))
            return manifest

        capture.check_expected(files, listing(*files))  # anchor: an exact listing passes
        with self.assertRaisesRegex(capture.CaptureError, "missing c.WAV"):
            capture.check_expected(files, listing(*files, item("c.WAV", b"cccc")))
        with self.assertRaisesRegex(capture.CaptureError, "not listed b.WAV"):
            capture.check_expected(files, listing(files[0]))
        with self.assertRaisesRegex(capture.CaptureError, "changed a.WAV"):
            capture.check_expected(files, listing(item("a.WAV", b"aaab"), files[1]))
        manifest.write_text("{}")
        with self.assertRaisesRegex(capture.CaptureError, "could not be read"):
            capture.check_expected(files, manifest)
        with self.assertRaisesRegex(capture.CaptureError, "could not be read"):
            capture.check_expected(files, tmp / "absent.json")

    def test_fewer_than_one_job_is_not_understood(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            capture.main(["import", "/nonexistent", "--archive", "a", "--staging", "s", "--derived-media", "m",
                          "--jobs", "0"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--jobs must be at least 1", err.getvalue())

    def test_the_default_jobs_leave_two_processors_and_never_fall_below_one(self) -> None:
        for count, jobs in ((8, 6), (2, 1), (1, 1), (None, 1)):
            with patch.object(capture.os, "cpu_count", return_value=count):
                self.assertEqual(capture.default_jobs(), jobs)

    def test_an_empty_session_name_is_not_understood(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            capture.main(["import", "/nonexistent", "--archive", "a", "--staging", "s", "--derived-media", "m",
                          "--session-name", " "])
        self.assertEqual(raised.exception.code, 2)

    def test_a_missing_input_directory_is_refused(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = capture.main(["import", "/nonexistent-capture-input", "--archive", "/tmp", "--staging", "/tmp/s",
                                 "--derived-media", "/tmp"])
        self.assertEqual(code, 1)
        self.assertIn("is not a directory", json.loads(err.getvalue())["detail"])

    def test_the_script_runs_as_a_command(self) -> None:
        completed = subprocess.run([sys.executable, str(HERE / "capture.py"), "import", "--help"],
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--session-name", completed.stdout)


if __name__ == "__main__":
    unittest.main()
