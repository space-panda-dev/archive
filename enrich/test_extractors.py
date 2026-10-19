"""Every shipped extractor together, run by the real runner on one recording
(ADRs 0073, 0074, 0075).

Each extractor has its own tests; these prove that they work as one bank: a
three-minute synthetic recording shaped like a recorder's file (48 kHz
stereo FLAC: room noise, a chord progression with a tune and a click on the
beat, room noise) goes through `run all`, with `stems` and `stems-6s` run by
the stand-in for Demucs's interpreter that `test_stems` uses (the real
programs with only the model replaced), and `notes-basic-pitch`,
`f0-pesto`, and `f0-torchcrepe` by the stand-ins of `test_notes` and
`test_f0`. Then a second run must run and hash nothing, the
whole bank must be what `bank.py` accepts, and `cover.py` must find in it
the profiles it would measure.

Set ARCHIVE_TEST_TIMINGS to a file to have each extractor's speed written
there as JSON.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy

import bank
import cover
import extract
import support
from support import needs_ffmpeg
from test_cover import needs_essentia
from test_f0 import fake_python as fake_f0_python
from test_notes import fake_python as fake_notes_python
from test_stems import fake_python

RATE = 48000
SECONDS = 180


def recording(path: Path) -> Path:
    """Three minutes: 6 s of room noise, 160 s of a progression with a tune
    over it and a click every half second, then 14 s of room noise."""
    rng = numpy.random.default_rng(3)
    lead, tail = 6 * RATE, 14 * RATE
    playing = 160 * RATE
    music = support.progression_samples(support.PROGRESSION, RATE, repeats=20)
    tune = support.tune_samples(support.TUNE, RATE, repeats=40)
    music = music[:playing] + 0.5 * tune[:playing]
    click = numpy.zeros(playing)
    length = int(0.01 * RATE)
    burst = rng.standard_normal(length) * numpy.linspace(1, 0, length) * 0.4
    for start in range(0, playing - length, RATE // 2):
        click[start:start + length] += burst
    signal = numpy.concatenate([rng.normal(0, 0.002, lead), music + click, rng.normal(0, 0.002, tail)])
    signal = signal / numpy.abs(signal).max() * 0.7
    stereo = numpy.stack([signal, 0.9 * signal + rng.normal(0, 0.002, len(signal))], axis=1)
    pcm = numpy.clip(numpy.round(stereo * 32767), -32768, 32767).astype("<i2")
    wav = path.with_suffix(".wav")
    with wave.open(str(wav), "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(pcm.tobytes())
    support.ffmpeg("-i", str(wav), "-c:a", "flac", str(path))
    wav.unlink()
    return path


def run(argv: list[str]) -> tuple[int, dict]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = extract.main(argv)
    return code, json.loads(out.getvalue() or err.getvalue())


def seconds_between(started: str, ended: str) -> float:
    parse = lambda text: datetime.fromisoformat(text.replace("Z", "+00:00"))  # noqa: E731
    return (parse(ended) - parse(started)).total_seconds()


def check_bank(test: unittest.TestCase, root: Path, catalog: bank.Catalog) -> list[bank.Entry]:
    """Every entry under the root as the format says it must be, and
    nothing else but locks: no work in progress, no recorded failure."""
    entries = []
    for manifest in sorted(root.glob("sha256/*/*/*/bank/*/*/entry.json")):
        entry = bank.read_entry(manifest.parent)
        test.assertIsNotNone(entry)
        extractor = catalog[entry.manifest["extractor"]["id"]]
        test.assertEqual(entry.manifest["extractor"]["version"], extractor.version)
        test.assertEqual(entry.manifest["key"], "sha256:" + entry.path.name)
        test.assertEqual(set(entry.manifest["outputs"]), set(extractor.outputs))
        files = {"entry.json"}
        for name, output in entry.manifest["outputs"].items():
            where = f"{extractor.id}/{name}"
            declared = {k: v for k, v in extractor.outputs[name].items() if k != "description"}
            test.assertEqual({k: output[k] for k in declared}, declared, where)
            if "file" not in output:
                test.assertEqual(output["kind"], "scalar", where)
                continue
            path = entry.path / output["file"]
            files.add(output["file"])
            test.assertEqual(path.stat().st_size, output["bytes"], where)
            test.assertEqual(bank.file_digest(path), output["sha256"], where)
            test.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, where)
            if output["kind"] == "series":
                values = entry.series(name)
                test.assertEqual(values.shape[1], output["columns"], where)
                if output["dtype"].startswith("f"):
                    test.assertTrue(numpy.isfinite(values).all(), where)
                if output.get("grid") is not None:
                    test.assertIn(output["grid"], entry.manifest["grids"], where)
            elif output["kind"] == "events":
                bank.check_events(entry.items(name), where)
            elif output["kind"] == "spans":
                bank.check_spans(entry.items(name), where)
        test.assertEqual({p.name for p in entry.path.iterdir()}, files, extractor.id)
        test.assertEqual(stat.S_IMODE(entry.path.stat().st_mode), 0o700, extractor.id)
        entries.append(entry)
    test.assertEqual(list(root.glob("sha256/*/*/*/bank/*/*.failed.json")), [])
    test.assertFalse((root / "tmp").exists() and any((root / "tmp").iterdir()))
    return entries


@needs_ffmpeg
@needs_essentia
class EveryExtractor(unittest.TestCase):
    def test_all_runs_in_order_is_cached_valid_and_read_by_cover(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="bank-all-"))
        self.addCleanup(shutil.rmtree, directory, True)
        audio = recording(directory / "take.flac")
        root = directory / "bank"
        catalog = bank.Catalog.load()
        everything = set(catalog.extractors)
        demucs = fake_python(directory)
        argv = ["run", "all", "--content", str(audio), "--bank", str(root),
                "--program", f"demucs-python={demucs}", "--program", f"stems-6s-python={demucs}",
                "--program", f"basic-pitch-python={fake_notes_python(directory)}",
                "--program", f"f0-python={fake_f0_python(directory)}"]

        code, answer = run(argv)
        self.assertEqual(code, 0, answer)
        order = list(answer["extractors"])
        self.assertEqual(set(order), everything)
        self.assertEqual({e: o["status"] for e, o in answer["extractors"].items()}, dict.fromkeys(order, "ran"))
        # Anchor: the graph has edges, so the order says something.
        self.assertTrue(catalog.upstream("structure"))
        for extractor_id in order:
            for source in catalog.upstream(extractor_id):
                self.assertLess(order.index(source), order.index(extractor_id), (source, extractor_id))

        digest = answer["content"]
        entries = check_bank(self, root, catalog)
        self.assertEqual(sorted(e.manifest["extractor"]["id"] for e in entries), sorted(everything))
        self.assertTrue(all(e.manifest["content"] == digest for e in entries))
        # Each need an entry recorded is the entry the bank holds now.
        current = {e.manifest["extractor"]["id"]: e.manifest["key"] for e in entries}
        for e in entries:
            for need, key in e.manifest["needs"].items():
                self.assertEqual(key, current[need.split("/")[0]], need)

        timings = {}
        for e in entries:
            took = seconds_between(e.manifest["started_at"], e.manifest["ended_at"])
            timings[e.manifest["extractor"]["id"]] = {"seconds": round(took, 3),
                                                      "times_real_time": round(SECONDS / took, 1)}
        if os.environ.get("ARCHIVE_TEST_TIMINGS"):
            Path(os.environ["ARCHIVE_TEST_TIMINGS"]).write_text(json.dumps(timings, indent=1, sort_keys=True))

        # A second run runs and hashes nothing: every extractor is launched
        # through Popen, and the content is hashed through file_digest.
        def bomb(*_args, **_kwargs):
            raise AssertionError("a cache hit ran or hashed something")
        cached = [*argv, "--digest", digest]
        before = sorted((p, p.stat().st_mtime_ns) for p in root.glob("sha256/**/*") if p.is_file())
        with patch.object(extract.subprocess, "Popen", bomb), patch.object(bank, "file_digest", bomb):
            code, again = run(cached)
        self.assertEqual(code, 0, again)
        self.assertEqual({e: o["status"] for e, o in again["extractors"].items()}, dict.fromkeys(order, "hit"))
        self.assertEqual(sorted((p, p.stat().st_mtime_ns) for p in root.glob("sha256/**/*") if p.is_file()),
                         before)
        # Anchor: with one entry gone, the same bombs go off.
        leaf = next(e for e in entries if e.manifest["extractor"]["id"] == "colour")
        shutil.rmtree(leaf.path)
        with patch.object(extract.subprocess, "Popen", bomb), patch.object(bank, "file_digest", bomb):
            with self.assertRaisesRegex(AssertionError, "cache hit"):
                run(cached)

        # cover.py reads the whole recording's profiles from the bank, and
        # they are the bits it would measure.
        found = bank.Bank(root).feature(digest, "frames/hpcp")
        self.assertIsNotNone(found)
        stored = found[0].series("hpcp")
        samples = int(SECONDS * RATE)
        fresh = cover.Analyser().features(cover.load([cover.Piece(audio, 0, samples, RATE)])).profiles
        self.assertGreater(len(fresh), 1000)
        self.assertEqual(stored.shape, fresh.shape)
        self.assertEqual(stored.tobytes(), fresh.astype("<f4").tobytes())
        self.assertTrue(numpy.array_equal(cover.banked_profiles(root, str(audio)), fresh))


if __name__ == "__main__":
    unittest.main()
