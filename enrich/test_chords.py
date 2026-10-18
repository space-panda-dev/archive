"""The chords extractor (ADR 0074): its arithmetic on its own, and the real
runner making `frames`, `rhythm`, `tonal`, and `chords` from synthetic audio.

The audio is a progression of seventh chords and a slash chord over a bass
line, with a click on every beat, written by Python's `wave` writer and
decoded by ffmpeg, as a recording would be; the profiles and beats it reads
are the real extractors' entries.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy

import bank
import extract
import support
from support import needs_ffmpeg
from test_cover import needs_essentia

sys.path.insert(0, str(Path(__file__).resolve().parent / "extractors" / "chords"))
import chords  # noqa: E402

RATE = 44100
CHORD_SECONDS = 2.0  # four beats at 120 a minute
LEAD = 2.0           # seconds of silence before the first chord
# (label, bass note, upper notes)
PROGRESSION = (("Cmaj7", 48, (64, 67, 71, 72)), ("Am7", 45, (60, 64, 67, 69)), ("Dm7", 50, (65, 69, 72, 74)),
               ("G7", 43, (59, 62, 65, 67)), ("C/G", 43, (60, 64, 67, 72)))


def song(repeats: int = 4, seed: int = 0) -> numpy.ndarray:
    """Silence, then the progression with a click on every beat, peaking at 0.5."""
    rng = numpy.random.default_rng(seed)
    count = int(CHORD_SECONDS * RATE)
    t = numpy.arange(count) / RATE
    envelope = numpy.minimum(1, numpy.minimum(t / 0.02, (CHORD_SECONDS - t) / 0.05))
    parts = []
    for _, bass, upper in PROGRESSION * repeats:
        parts.append(envelope * sum(
            numpy.sin(2 * numpy.pi * 440 * 2 ** ((note - 69) / 12) * h * t + rng.uniform(0, 2 * numpy.pi))
            / h ** 1.2 * (1.5 if note == bass else 1.0) for note in (bass, *upper) for h in range(1, 7)))
    music = numpy.concatenate(parts)
    music = music / numpy.abs(music).max() * 0.35
    click = numpy.zeros(len(music))
    length = int(0.01 * RATE)
    burst = rng.standard_normal(length) * numpy.linspace(1, 0, length) * 0.3
    for start in range(0, len(music) - length, RATE // 2):
        click[start:start + length] += burst
    return numpy.concatenate([numpy.zeros(int(LEAD * RATE)), music + click + 0.002 * rng.standard_normal(len(music))])


def scratch(test: unittest.TestCase) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="bank-chords-"))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def run(directory: Path, source: Path, *extra: str) -> tuple[int, dict]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = extract.main(["run", "chords", "--content", str(source), "--bank", str(directory / "bank"), *extra])
    return code, json.loads(out.getvalue() or err.getvalue())


def a_request(directory: Path, source: Path) -> dict[str, Any]:
    """The request the runner would give chords, made by the runner's code
    once everything it needs is in the bank."""
    root = directory / "bank"
    with redirect_stdout(io.StringIO()):
        for upstream in ("frames", "rhythm", "tonal"):
            assert extract.main(["run", upstream, "--content", str(source), "--bank", str(root)]) == 0
    runner = extract.Run(bank.Catalog.load(), root.resolve(), source, "audio/wav", None, {}, {}, None)
    _, keys, _ = runner.graph(["chords"])
    output = directory / "out"
    output.mkdir(exist_ok=True)
    return runner.request("chords", keys, output)


class Manifest(unittest.TestCase):
    def test_the_manifest_declares_exactly_what_the_program_writes(self) -> None:
        extractor = bank.Catalog.load()["chords"]
        self.assertEqual(extractor.manifest["parameters"], chords.parameters())
        self.assertEqual(sorted(extractor.needs), sorted(["frames/samples", "rhythm/beats", *chords.NEEDS]))
        declared = {name: out["kind"] for name, out in extractor.outputs.items()}
        self.assertEqual(declared, {**{name: "scalar" for name in chords.SCALARS},
                                    **{name: "spans" for name in chords.SPANS}})
        # Every need is an output its extractor declares, of the shape read here.
        catalog = bank.Catalog.load()
        for need, columns in chords.NEEDS.items():
            source, output = need.split("/")
            self.assertEqual(catalog[source].outputs[output]["columns"], columns, need)
            self.assertEqual(catalog[source].outputs[output]["grid"], "frames", need)
        self.assertEqual(catalog["rhythm"].outputs["beats"]["kind"], "events")
        self.assertEqual(catalog["frames"].outputs["samples"]["kind"], "scalar")
        # The profiles are tonal's, on frames' grid at its rate and hop.
        tonal_parameters = catalog["tonal"].manifest["parameters"]
        self.assertEqual((tonal_parameters["analysis_rate"], tonal_parameters["hop_size"]), (chords.RATE, chords.HOP))

    def test_the_vocabulary_names_every_chord_once(self) -> None:
        self.assertEqual(len(chords.CHORDS), 12 * len(chords.VOCABULARY))
        notes = [frozenset((root + step) % 12 for step in dict(chords.VOCABULARY)[quality])
                 for root, quality in chords.CHORDS]
        named = {chords.name(root, quality) for root, quality in chords.CHORDS}
        self.assertEqual(len(named), len(chords.CHORDS))
        for (root, quality), held in zip(chords.CHORDS, notes):
            self.assertEqual(chords.notes_of(chords.name(root, quality)), set(held))
        self.assertTrue(numpy.allclose(numpy.linalg.norm(chords.TEMPLATES, axis=1), 1.0))
        # Some chords hold the same notes and differ only in their root: the
        # candidates exist for them.
        self.assertIn(chords.notes_of("Am7"), [chords.notes_of("C6")])


class Arithmetic(unittest.TestCase):
    def test_labels_are_read_back_or_refused(self) -> None:
        self.assertEqual(chords.notes_of("C"), {3, 7, 10})
        self.assertEqual(chords.notes_of("Abmaj7/Bb"), {11, 3, 6, 10, 1})
        self.assertEqual(chords.notes_of("F#m11"), {9, 0, 4, 7, 11, 2})
        self.assertIsNone(chords.notes_of("N"))
        for wrong in ("H", "Cmaj13", "C/H", "Db", ""):
            with self.assertRaisesRegex(chords.ChordsError, "not a chord"):
                chords.notes_of(wrong)
        self.assertEqual(chords.name(3, "maj7", 3), "Cmaj7")  # a bass on the root is no slash
        self.assertEqual(chords.name(3, "", 10), "C/G")

    def test_the_grid_cuts_each_beat_in_two_and_fills_the_gaps(self) -> None:
        beats = [1.0, 1.5, 2.0, 3.5, 4.0]  # a gap of three beats from 2.0 to 3.5
        edges, on_beat = chords.grid(beats, 5.0)
        self.assertEqual(edges.tolist(), [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0,
                                          3.25, 3.5, 3.75, 4.0, 4.25, 4.5, 4.75, 5.0])
        # Tracked: 1.0 to 2.0, and 3.5 to 4.0; filled before, in the gap, and after.
        self.assertEqual(on_beat.tolist(), [False] * 4 + [True] * 4 + [False] * 6 + [True] * 2 + [False] * 4)
        self.assertEqual(len(on_beat), len(edges) - 1)

    def test_the_grid_ends_at_the_audio_and_falls_back_without_beats(self) -> None:
        edges, on_beat = chords.grid([0.3, 0.8, 1.3, 9.0], 1.6)  # a beat past the end is left out
        self.assertEqual(edges[0], 0.0)
        self.assertEqual(edges[-1], 1.6)
        self.assertTrue(numpy.all(numpy.diff(edges) > 0))
        for beats in ([], [0.7]):
            edges, on_beat = chords.grid(beats, 1.2)
            self.assertEqual(edges.tolist(), [0.0, 0.25, 0.5, 0.75, 1.0, 1.1, 1.2])
            self.assertFalse(on_beat.any())

    def test_a_stretch_reads_the_frames_centred_in_it_or_the_nearest(self) -> None:
        hop = chords.HOP / chords.RATE
        edges = numpy.array([0.0, 2.5 * hop, 2.9 * hop, 10 * hop])
        # 2.5 to 2.9 hops holds no frame's centre; its middle, 2.7, is nearest frame 3.
        self.assertEqual(chords.frame_ranges(edges, 8), [(0, 3), (3, 4), (3, 8)])
        self.assertEqual(chords.frame_ranges(numpy.array([0.0, 20 * hop]), 8), [(0, 8)])

    def test_the_path_pays_for_each_change(self) -> None:
        emission = numpy.zeros((9, 2))
        emission[:, 0] = 1.0
        emission[4, 1] = 1.3  # one stretch prefers state 1 by less than the price of two changes
        emission[6:, 1] = 1.5  # three stretches prefer it by more than the price of one change
        self.assertEqual(chords.viterbi(emission, 0.4).tolist(), [0, 0, 0, 0, 0, 0, 1, 1, 1])
        # Anchor: without the price, every preference is followed.
        self.assertEqual(chords.viterbi(emission, 0.0).tolist(), [0, 0, 0, 0, 1, 0, 1, 1, 1])
        self.assertEqual(chords.runs([1, 1, 2, 2, 2, 1]), [(0, 2), (2, 5), (5, 6)])
        self.assertEqual(chords.runs([]), [])

    def test_a_held_chord_scores_its_template_and_its_bass(self) -> None:
        def profile(*notes: int) -> numpy.ndarray:
            values = numpy.zeros((1, 12))
            values[0, list(notes)] = 1.0
            return values
        cm7 = profile(3, 6, 10, 1)  # C Eb G Bb
        on_c = chords.scores(cm7, profile(3))[0][0]
        on_eb = chords.scores(cm7, profile(6))[0][0]
        best = lambda scored: chords.name(*chords.CHORDS[int(numpy.argmax(scored))])  # noqa: E731
        self.assertEqual((best(on_c), best(on_eb)), ("Cm7", "Eb6"))
        # With no bass the root is the template's to decide, and silence scores nothing in particular.
        self.assertIn(best(chords.scores(cm7, numpy.zeros((1, 12)))[0][0]), {"Cm7", "Eb6"})
        silent, shares = chords.scores(numpy.zeros((1, 12)), numpy.zeros((1, 12)))
        self.assertTrue(numpy.isfinite(silent).all())
        self.assertFalse(shares.any())
        # A sus2 needs more than a bare fifth: C and G alone read C, not Csus2.
        self.assertEqual(best(chords.scores(profile(3, 10), profile(3))[0][0]), "C")

    def test_decide_tiles_the_stretches_and_ranks_the_candidates(self) -> None:
        treble = numpy.zeros((8, 12))
        treble[:4, [3, 7, 10, 2]] = 1.0  # C E G B
        treble[4:6, [0, 3, 7, 10]] = 1.0  # A C E G
        bass = numpy.zeros((8, 12))
        bass[:4, 3] = 1.0
        bass[4:6, 0] = 1.0
        pitched = numpy.array([True] * 6 + [False] * 2)
        edges = numpy.arange(9) * 0.25
        out = chords.decide(treble, bass, pitched, edges, numpy.ones(8, dtype=bool))
        self.assertEqual([(c["start"], c["end"], c["label"]) for c in out["chords"]],
                         [(0.0, 1.0, "Cmaj7"), (1.0, 1.5, "Am7"), (1.5, 2.0, "N")])
        self.assertNotIn("value", out["chords"][-1])
        groups = [out["chord_candidates"][i:i + 3] for i in range(0, 6, 3)]
        emission, _ = chords.scores(treble, bass)
        for group, (first, stop), low in zip(groups, ((0, 4), (4, 6)), (3, 0)):
            mean = emission[first:stop].mean(axis=0)
            ranked = [chords.name(*chords.CHORDS[i], low) for i in numpy.argsort(-mean, kind="stable")]
            self.assertEqual([c["label"] for c in group], ranked[:3])  # here the chosen chord also scores best
            self.assertEqual([c["value"] for c in group], sorted(mean, reverse=True)[:3])
        self.assertIn("C6/A", [c["label"] for c in groups[1]])  # the same notes on another root, over A
        # Without a bass, A C E G is Am7 or C6 alike, and the candidates say so.
        bare = chords.decide(treble[4:6], numpy.zeros((2, 12)), pitched[:2], edges[:3], numpy.ones(2, dtype=bool))
        self.assertEqual([c["label"] for c in bare["chord_candidates"][:2]], ["Am7", "C6"])
        self.assertEqual(bare["chord_candidates"][0]["value"], bare["chord_candidates"][1]["value"])
        self.assertEqual(out["changes_per_minute"], 1 / (1.5 / 60))
        self.assertEqual(out["beat_share"], 1.0)
        chords.check(out, 2.0)
        nothing = chords.decide(treble, bass, numpy.zeros(8, dtype=bool), edges, numpy.ones(8, dtype=bool))
        self.assertEqual((nothing["chords"], nothing["chord_candidates"], nothing["changes_per_minute"],
                          nothing["beat_share"]), ([{"start": 0.0, "end": 2.0, "label": "N"}], [], 0.0, 0.0))

    def test_a_moving_bass_cuts_a_held_chord(self) -> None:
        """C E G held for two beats while the bass walks from G to C: two
        chords, C over G and then C; with no clear bass note, no slash."""
        treble = numpy.zeros((8, 12))
        treble[:, [3, 7, 10]] = 1.0
        bass = numpy.zeros((8, 12))
        bass[:4, 10] = 1.0  # G
        bass[4:, 3] = 1.0   # C
        edges = numpy.arange(9) * 0.25
        out = chords.decide(treble, bass, numpy.ones(8, dtype=bool), edges, numpy.ones(8, dtype=bool))
        self.assertEqual([(c["start"], c["label"]) for c in out["chords"]], [(0.0, "C/G"), (1.0, "C")])
        self.assertEqual([c["label"] for c in out["chord_candidates"][::3]], ["C/G", "C"])
        spread = numpy.full((8, 12), 1.0)  # a bass with no note standing out
        out = chords.decide(treble, spread, numpy.ones(8, dtype=bool), edges, numpy.ones(8, dtype=bool))
        self.assertEqual([c["label"] for c in out["chords"]], ["C"])
        # A passing note in the bass, a beat long, is not followed.
        bass[:, :] = 0.0
        bass[:, 3] = 1.0
        bass[3:5, 3], bass[3:5, 10] = 0.0, 1.0  # G for one beat
        out = chords.decide(treble, bass, numpy.ones(8, dtype=bool), edges, numpy.ones(8, dtype=bool))
        self.assertEqual([c["label"] for c in out["chords"]], ["C"])

    def test_check_refuses_what_the_outputs_must_not_be(self) -> None:
        good = {"chords": [{"start": 0.0, "end": 1.0, "label": "C", "value": 0.9},
                           {"start": 1.0, "end": 2.0, "label": "N"}],
                "chord_candidates": [{"start": 0.0, "end": 1.0, "label": "C", "value": 0.9},
                                     {"start": 0.0, "end": 1.0, "label": "Am7", "value": 0.8},
                                     {"start": 0.0, "end": 1.0, "label": "Csus2", "value": 0.7}]}
        chords.check(good, 2.0)  # anchor: the good outputs pass

        def changed(path: str, value: Any) -> dict[str, Any]:
            copy = json.loads(json.dumps(good))
            kind, index, field = path.split(".")
            copy[kind][int(index)][field] = value
            return copy
        for outputs, seconds, pattern in (
                (good, 3.0, "do not cover"), ({**good, "chords": []}, 2.0, "do not cover"),
                (changed("chords.1.start", 1.1), 2.0, "does not follow"),
                (changed("chords.0.label", "H"), 2.0, "not a chord"),
                (changed("chord_candidates.0.label", "Am7"), 2.0, "candidates are not its own"),
                (changed("chord_candidates.1.end", 2.0), 2.0, "candidates are not its own"),
                (changed("chord_candidates.2.value", 0.85), 2.0, "candidates are not its own"),
                (changed("chord_candidates.1.value", 0.95), 2.0, "candidates are not its own"),
                (changed("chord_candidates.2.label", "C13"), 2.0, "not a chord"),
                ({**good, "chord_candidates": good["chord_candidates"][:2]}, 2.0, "candidates are not its own"),
                ({**good, "chord_candidates": good["chord_candidates"] * 2}, 2.0, "candidates for no chord")):
            with self.subTest(pattern=pattern, seconds=seconds):
                with self.assertRaisesRegex(chords.ChordsError, pattern):
                    chords.check(outputs, seconds)


@needs_ffmpeg
@needs_essentia
class Progression(unittest.TestCase):
    """The real chain on a progression whose chords are known."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = Path(tempfile.mkdtemp(prefix="bank-chords-"))
        cls.source = support.write_wav(cls.directory / "song.wav", song(), RATE)
        out = io.StringIO()
        with redirect_stdout(out):
            cls.code = extract.main(["run", "chords", "--content", str(cls.source), "--bank",
                                     str(cls.directory / "bank")])
        cls.answer = json.loads(out.getvalue())
        found = bank.Bank(cls.directory / "bank").entry(bank.file_digest(cls.source), "chords")
        cls.entry = found

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.directory, True)

    def test_each_chord_is_named_where_it_sounds(self) -> None:
        self.assertEqual(self.code, 0, self.answer)
        spans = self.entry.items("chords")
        seconds = len(song()) / RATE
        self.assertEqual((spans[0]["start"], round(spans[-1]["end"], 6)), (0.0, round(seconds, 6)))
        self.assertEqual(spans[0]["label"], "N")  # the silence before the first chord
        self.assertLess(abs(spans[1]["start"] - LEAD), 0.3)
        right = total = 0.0
        for index, (label, _, _) in enumerate(PROGRESSION * 4):
            start = LEAD + index * CHORD_SECONDS + 0.3
            end = start + CHORD_SECONDS - 0.6
            for span in spans:
                overlap = min(end, span["end"]) - max(start, span["start"])
                if overlap > 0:
                    total += overlap
                    right += overlap if span["label"] == label else 0.0
        self.assertGreater(right / total, 0.9, [(round(s["start"], 2), s["label"]) for s in spans])
        # One span a chord: no flicker inside a held chord.
        self.assertLessEqual(len(spans), 1 + len(PROGRESSION) * 4 + 2)
        changes = [span["start"] for span in spans[2:]]
        offsets = [(start - LEAD) - round((start - LEAD) / CHORD_SECONDS) * CHORD_SECONDS for start in changes]
        self.assertLess(max(abs(offset) for offset in offsets), 0.3, offsets)
        self.assertGreater(self.entry.scalar("beat_share"), 0.8)  # anchor: the beats were tracked
        self.assertLess(abs(self.entry.scalar("changes_per_minute") - 30), 3)

    def test_every_chord_has_its_candidates_ranked(self) -> None:
        candidates = self.entry.items("chord_candidates")
        spans = [s for s in self.entry.items("chords") if s["label"] != "N"]
        self.assertEqual(len(candidates), 3 * len(spans))
        for index, span in enumerate(spans):
            group = candidates[3 * index:3 * index + 3]
            self.assertEqual(group[0]["label"], span["label"])
            self.assertEqual(group[0]["value"], span["value"])
            self.assertEqual(len({c["label"] for c in group}), 3)
            self.assertGreaterEqual(group[1]["value"], group[2]["value"])
            self.assertTrue(all((c["start"], c["end"]) == (span["start"], span["end"]) for c in group))

    def test_a_second_run_is_a_hit_and_starts_nothing(self) -> None:
        def bomb(*_args, **_kwargs):
            raise AssertionError("a cache hit started a program")
        with patch.object(extract.subprocess, "Popen", bomb):
            code, answer = run(self.directory, self.source)
        self.assertEqual(code, 0, answer)
        self.assertEqual({name: item["status"] for name, item in answer["extractors"].items()},
                         {"frames": "hit", "rhythm": "hit", "tonal": "hit", "chords": "hit"})


@needs_ffmpeg
@needs_essentia
class Refusals(unittest.TestCase):
    """Each way the extractor refuses, through its own entry points."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = Path(tempfile.mkdtemp(prefix="bank-chords-"))
        cls.source = support.write_wav(cls.directory / "song.wav", song(repeats=1), RATE)
        cls.request = a_request(cls.directory, cls.source)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.directory, True)

    def refused(self, request: dict[str, Any], pattern: str) -> None:
        with self.assertRaisesRegex(chords.ChordsError, pattern):
            chords.measure(request)

    def test_a_missing_or_misshapen_need_is_refused(self) -> None:
        self.assertTrue(chords.measure(self.request)["chords"])  # anchor: the request as made is measured
        needs = self.request["needs"]
        for name in ("tonal/chroma", "frames/samples", "rhythm/beats"):
            self.refused({**self.request, "needs": {k: v for k, v in needs.items() if k != name}},
                         "does not carry")
        chroma = needs["tonal/chroma"]
        for changed, pattern in (({**chroma, "grid": {**chroma["grid"], "hop": 1024}}, "not on the frames grid"),
                                 ({**chroma, "dtype": "i16"}, "not a series"),
                                 ({**chroma, "shape": [chroma["shape"][0], 11]}, "not a series"),
                                 ({**chroma, "shape": [chroma["shape"][0] + 1, 12]}, "holds")):
            self.refused({**self.request, "needs": {**needs, "tonal/chroma": changed}}, pattern)
        bass = needs["tonal/bass_chroma"]
        short = self.directory / "short.f32"
        numpy.zeros((bass["shape"][0] - 1) * 12, dtype="<f4").tofile(short)
        self.refused({**self.request, "needs": {**needs, "tonal/bass_chroma": {
            **bass, "path": str(short), "shape": [bass["shape"][0] - 1, 12]}}}, "holds")
        for value in (0, None, "1"):
            self.refused({**self.request, "needs": {**needs, "frames/samples": {**needs["frames/samples"],
                                                                                "value": value}}}, "decoded length")

    def test_a_recording_without_beats_is_measured_on_the_plain_grid(self) -> None:
        empty = self.directory / "beats.json"
        empty.write_text("[]")
        request = {**self.request, "needs": {**self.request["needs"],
                                             "rhythm/beats": {**self.request["needs"]["rhythm/beats"],
                                                              "path": str(empty)}}}
        found = chords.measure(request)
        self.assertEqual(found["beat_share"], 0.0)
        self.assertTrue(any(c["label"] != "N" for c in found["chords"]))
        self.assertTrue(all(round(c["start"] / 0.25, 6).is_integer() for c in found["chords"][:-1]))

    def test_main_reports_a_failure_and_writes_nothing(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(chords.main([str(self.directory / "absent.json")]), 1)
            self.assertEqual(chords.main([]), 2)
        self.assertIn("chords:", err.getvalue())
        self.assertIn("usage", err.getvalue())
        request = self.directory / "request.json"
        request.write_bytes(bank.canonical({**self.request, "parameters": {**self.request["parameters"],
                                                                            "decode": {}}}))
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(chords.main([str(request)]), 1)
        self.assertIn("fixed settings", err.getvalue())
        self.assertEqual(list(Path(self.request["output_dir"]).iterdir()), [])

    def test_other_settings_are_refused_by_the_runner(self) -> None:
        config = self.directory / "config.json"
        config.write_text(json.dumps({"parameters": {"chords": {"decode": {"method": "viterbi", "switch": "0"}}}}))
        code, answer = run(self.directory, self.source, "--config", str(config))
        self.assertEqual(code, 1)
        self.assertIn("fixed settings", answer["extractors"]["chords"]["log"])


if __name__ == "__main__":
    unittest.main()
