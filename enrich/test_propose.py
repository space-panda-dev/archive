"""The proposers (ADR 0079, D1.4): bank features read as claims.

Labels, times, bars, and the bass are tested on their own. Every proposer
is then run against a bank that the real runner filled: Essentia's `frames`,
`rhythm`, `tonal`, `structure`, and `melody` on synthetic music, the chord
extractors and `stems` with stand-ins for their models only (the rest of
each program is the real writer, as `test_beats_chords.py` and
`test_stems.py` run them), and `tonal` on the bass stem through
`--from stems/bass`. Each claim is checked by World's own check of its
contract.
"""

from __future__ import annotations

import io
import json
import os
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
import chord_labels
import extract
import proposers
import support
from test_beats_chords import (CHORDINO_CHANGES, CHORDINO_SECONDS, JIANG_LABELS, stand_in_programs)
from test_cover import needs_essentia
from test_f0 import fake_python as f0_python
from test_notes import fake_python as basic_pitch_python
from test_stems import fake_python

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from world import form, harmony, notes, tonality  # noqa: E402

RATE = 44100
SECONDS = 32
CHORD_SECONDS = 2
# Each chord: its bass (MIDI, inside tonal's 40-250 Hz bass band) and its
# upper notes (above it). The basses' pitch classes are C, A, F, G.
CHORDS = ((48, (60, 64, 67)), (45, (60, 64, 69)), (41, (60, 65, 69)), (43, (62, 67, 71)))
BASS_CLASSES = [bass % 12 for bass, _ in CHORDS]

SOURCE = {"role": "source", "id": "urn:uuid:00000000-0000-4000-8000-000000000001",
          "revision_id": "sha256:" + "1" * 64}
STREAM_REF = {"role": "stream", "id": "urn:uuid:00000000-0000-4000-8000-000000000002",
              "revision_id": "sha256:" + "2" * 64}
CHECKS = {proposers.CHORD_STACKS: harmony.check_chord_stacks_v2, proposers.METER_LAYERS: form.check_meter_layers_v2,
          proposers.KEY_REGIONS: tonality.check_key_regions, proposers.TUNING: tonality.check_tuning,
          proposers.NOTES: notes.check_notes, proposers.FORM_SPANS: form.check_form_spans}


def stream_claim(count: int, rate: int = RATE) -> dict:
    return {"payload": {"stream_index": 0, "sample_rate": rate, "sample_count": count},
            "refs": [{"role": "artifact", "id": SOURCE["id"], "revision_id": SOURCE["revision_id"]}],
            "revision_id": STREAM_REF["revision_id"]}


def check(planned: proposers.Planned, count: int) -> None:
    """World's own check of the claim, pinned as the command pins it."""
    CHECKS[planned.contract]({"payload": planned.payload, "refs": [SOURCE, STREAM_REF]}, stream_claim(count))


def music(path: Path) -> Path:
    """Chords of two seconds over a bass, with a click every half second."""
    t = numpy.arange(CHORD_SECONDS * RATE) / RATE
    envelope = numpy.minimum(1, numpy.minimum(t / 0.01, (CHORD_SECONDS - t) / 0.05))

    def tone(note: int, level: float) -> numpy.ndarray:
        frequency = 440 * 2 ** ((note - 69) / 12)
        return sum(numpy.sin(2 * numpy.pi * frequency * h * t) / h for h in (1, 2, 3)) * level

    pieces = [(tone(bass, 0.5) + sum(tone(n, 0.2) for n in upper)) * envelope for bass, upper in CHORDS]
    samples = numpy.concatenate(pieces * (SECONDS // (CHORD_SECONDS * len(CHORDS))))
    samples = samples / numpy.abs(samples).max() * 0.4
    rng = numpy.random.default_rng(3)
    click = rng.normal(0, 0.3, int(0.01 * RATE)) * numpy.exp(-numpy.arange(int(0.01 * RATE)) / (0.002 * RATE))
    for start in range(0, len(samples) - len(click), RATE // 2):
        samples[start:start + len(click)] += click
    return support.write_wav(path, samples + rng.normal(0, 0.002, len(samples)), RATE)


def run_extract(*argv: str) -> dict:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = extract.main(["run", *argv])
    answer = json.loads(out.getvalue() or err.getvalue())
    if code != 0:
        raise AssertionError(f"extract.py run {' '.join(argv)} failed: {answer}")
    return answer


def fill_bank(directory: Path, content: Path) -> Path:
    """The bank `enrich propose` reads, made by the real runner."""
    root = directory / "bank"
    common = ["--content", str(content), "--bank", str(root)]
    programs = stand_in_programs(directory)
    stems = ["--program", f"demucs-python={fake_python(directory)}"]
    for target in ("chords-jiang", "chords-chordino"):
        run_extract(target, *common, *programs)
    for target in ("rhythm", "tonal", "structure"):
        run_extract(target, *common)
    run_extract("stems", *common, *stems)
    run_extract("tonal", *common, "--from", "stems/bass", *stems)
    run_extract("notes-basic-pitch", *common, "--from", "stems/bass", *stems,
                "--program", f"basic-pitch-python={basic_pitch_python(directory)}")
    for stem in ("stems/vocals", "stems/bass"):
        run_extract("f0-pesto", *common, "--from", stem, *stems, "--program", f"f0-python={f0_python(directory)}")
    run_extract("melody", *common, *stems)
    return root


class Labels(unittest.TestCase):
    def test_harte_labels_name_root_bass_and_the_tones_above_it(self) -> None:
        cases = {
            "C:maj": {"bass": 0, "tones": [4, 7], "root": 0},
            "A:min7/b3": {"bass": 0, "tones": [4, 7, 9], "root": 9},
            "F:maj/3": {"bass": 9, "tones": [0, 5], "root": 5},
            "C:maj(*5,9)": {"bass": 0, "tones": [2, 4], "root": 0},
            "C:(1,b3,5)": {"bass": 0, "tones": [3, 7], "root": 0},
            "C:(*1,3,5)/3": {"bass": 4, "tones": [7], "root": 0},
            "Bb:7/b7": {"bass": 8, "tones": [2, 5, 10], "root": 10},
            "C#:sus4(b7)": {"bass": 1, "tones": [6, 8, 11], "root": 1},
            "D:min11": {"bass": 2, "tones": [0, 4, 5, 7, 9], "root": 2},
            "E": {"bass": 4, "tones": [8, 11], "root": 4},
        }
        for label, stack in cases.items():
            with self.subTest(label=label):
                reading = chord_labels.harte_reading(label)
                self.assertEqual(reading, {**stack, "text": label})
                harmony.check_reading_v2(reading, False)
        self.assertEqual(chord_labels.harte_reading("N"), {"no_chord": True, "text": "N"})
        for unread in ("X", "X:unknown", "C:weird", "H:maj", "C:maj/15", "C:maj(9"):
            with self.subTest(label=unread):
                self.assertEqual(chord_labels.harte_reading(unread), {"text": unread})

    def test_chordino_labels(self) -> None:
        cases = {"C": (0, [4, 7], 0), "Am7": (9, [0, 4, 7], 9), "F/A": (9, [0, 5], 5),
                 "F#m7b5": (6, [0, 4, 9], 6), "Bb7/D": (2, [5, 8, 10], 10), "Ebdim7": (3, [0, 6, 9], 3)}
        for label, (bass, tones, root) in cases.items():
            with self.subTest(label=label):
                self.assertEqual(chord_labels.chordino_reading(label),
                                 {"bass": bass, "tones": tones, "root": root, "text": label})
        self.assertEqual(chord_labels.chordino_reading("N"), {"no_chord": True, "text": "N"})
        self.assertEqual(chord_labels.chordino_reading("Cblues"), {"text": "Cblues"})


class Times(unittest.TestCase):
    def test_a_time_is_its_written_decimal_times_the_rate_rounded_half_to_even_and_clamped(self) -> None:
        self.assertEqual([proposers.sample_of(t, 2, 100) for t in (0.25, 0.75, 1.25, 0.0)], [0, 2, 2, 0])
        # The written decimal times 48,000 is 140891.49999...; the float
        # product rounds to exactly 140891.5, which would round up.
        self.assertEqual(2.9352395833333333 * 48000, 140891.5)  # anchor: floating point lands on the half
        self.assertEqual(proposers.sample_of(2.9352395833333333, 48000, 10 ** 6), 140891)
        self.assertEqual((proposers.sample_of(-0.5, 10, 100), proposers.sample_of(99.0, 10, 100)), (0, 100))
        with self.assertRaises(proposers.ProposerError):
            proposers.sample_of(True, 10, 100)

    def test_decimal_text_is_exact_and_never_negative_zero(self) -> None:
        self.assertEqual([proposers.decimal_text(v) for v in (0.6, 1e-05, -0.0, 3)], ["0.6", "0.00001", "0.0", "3"])
        self.assertEqual(proposers.decimal_text(0.12345, 4), "0.1234")
        self.assertEqual(proposers.decimal_text(0.12355, 4), "0.1236")


class Bars(unittest.TestCase):
    context = proposers.Context(None, "sha256:" + "0" * 64, 10, 1000)  # type: ignore[arg-type]

    def pulse(self, *times: float) -> list[dict]:
        return [{"time": t, "value": 1} for t in times]

    def test_a_bar_runs_to_the_pulse_its_value_of_beats_later(self) -> None:
        pulse = self.pulse(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0)
        bars = proposers.bars_of(pulse, [{"time": 1.0, "value": 4, "label": "4/4"},
                                         {"time": 5.0, "value": 4, "label": "4/4"}], self.context)
        self.assertEqual(bars, [{"start_sample": 10, "end_sample": 50, "numerator": 4, "denominator": 4,
                                 "beats": [10, 20, 30, 40]},
                                {"start_sample": 50, "end_sample": 90, "numerator": 4, "denominator": 4,
                                 "beats": [50, 60, 70, 80]}])

    def test_pulses_that_divide_the_numerator_are_equal_groups_and_others_no_beats(self) -> None:
        pulse = self.pulse(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0)
        bars = proposers.bars_of(pulse, [{"time": 1.0, "value": 4, "label": "12/8"},
                                         {"time": 5.0, "value": 3, "label": "4/4"}], self.context)
        self.assertEqual(bars[0]["grouping"], [3, 3, 3, 3])
        self.assertEqual(bars[0]["beats"], [10, 20, 30, 40])
        self.assertEqual(bars[1], {"start_sample": 50, "end_sample": 80, "numerator": 4, "denominator": 4})

    def test_downbeats_that_make_no_bar(self) -> None:
        pulse = self.pulse(1.0, 2.0, 3.0, 4.0, 5.0)
        made = proposers.bars_of(pulse, [
            {"time": 1.5, "value": 2, "label": "2/4"},     # not a pulse
            {"time": 1.0, "value": 2, "label": "2/3"},     # not a meter a bar can have
            {"time": 1.0, "value": 2, "label": "waltz"},   # not a meter
            {"time": 1.0, "value": 3, "label": "3/4"},     # the bar: 1.0 to 4.0
            {"time": 3.0, "value": 2, "label": "2/4"},     # would overlap it
            {"time": 4.0, "value": 2, "label": "2/4"},     # would run past the last pulse
        ], self.context)
        self.assertEqual([(b["start_sample"], b["end_sample"]) for b in made], [(10, 40)])


class BassOfIntervals(unittest.TestCase):
    """The bass of each interval between pulses, from a hand-made chroma:
    bin 0 is A, so a peak in bin 3 is C."""

    grid = {"sample_rate": 10, "hop": 1, "first_centre": 0}
    context = proposers.Context(None, "sha256:" + "0" * 64, 10, 100)  # type: ignore[arg-type]

    def chroma(self, bins: list[int], level: float = 1.0, other: float = 0.1) -> numpy.ndarray:
        rows = numpy.full((len(bins), 12), other)
        for row, peak in enumerate(bins):
            rows[row, peak] = level
        return rows

    def test_bins_are_classes_from_a_and_equal_neighbours_merge_at_their_least_lead(self) -> None:
        chroma = self.chroma([3] * 20 + [0] * 10)
        chroma[5:10, 7] = 0.5  # a weaker lead in the second interval: (1 - 0.5) / 1
        spans = proposers.bass_spans(chroma, numpy.ones(30), self.grid, [0.0, 0.5, 1.0, 2.0, 3.0], self.context)
        self.assertEqual(spans, [{"start_sample": 0, "end_sample": 20, "readings": [{"bass": 0, "weight": "0.5000"}]},
                                 {"start_sample": 20, "end_sample": 30, "readings": [{"bass": 9, "weight": "0.9000"}]}])

    def test_an_unpitched_or_unclear_interval_has_no_span(self) -> None:
        chroma = self.chroma([3] * 30)
        chroma[10:20, 4] = 0.9                  # second interval: the lead is 0.1, under the margin
        pitched = numpy.ones(30)
        pitched[20:30] = 0                      # third interval: unpitched
        spans = proposers.bass_spans(chroma, pitched, self.grid, [0.0, 1.0, 2.0, 3.0], self.context)
        self.assertEqual([(s["start_sample"], s["end_sample"]) for s in spans], [(0, 10)])
        self.assertEqual(proposers.bass_spans(numpy.zeros((30, 12)), numpy.ones(30), self.grid, [0.0, 3.0],
                                              self.context), [])


class Missing(unittest.TestCase):
    def test_an_empty_bank_says_what_each_proposer_lacks(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="propose-empty-"))
        self.addCleanup(shutil.rmtree, directory, True)
        context = proposers.Context(bank.Bank(directory), "sha256:" + "a" * 64, RATE, RATE * 10)
        lacking = {name: proposer.propose(context).as_json() for name, proposer in proposers.PROPOSERS.items()}
        self.assertEqual(lacking, {
            "chords-jiang": {"missing": ["chords-jiang/chords", "chords-jiang/readings"]},
            "chords-chordino": {"missing": ["chords-chordino/chords"]},
            "beats": {"missing": ["rhythm/pulse", "rhythm/downbeats"]},
            "bass": {"missing": ["stems/bass", "tonal/bass_chroma from stems/bass", "tonal/pitched from stems/bass",
                                 "rhythm/pulse"]},
            "key": {"missing": ["tonal/key_regions"]},
            "tuning": {"missing": ["tonal/tuning_cents", "tonal/tuning_confidence", "tonal/tuning_drift"]},
            "notes": {"missing": ["melody/notes"]},
            "bass-line": {"missing": ["stems/bass", "f0-pesto/f0 or notes-basic-pitch/notes from stems/bass",
                                      "tonal/bass_chroma from stems/bass", "tonal/pitched from stems/bass"]},
            "sections": {"missing": ["structure/sections"]},
            "timed-lyrics": {"code": "not_yet", "missing": list(proposers.ALIGNMENT_NEEDS),
                             "detail": "forced alignment of a lyric document to the vocal stem is not built yet"},
        })

    def test_the_default_proposers_are_registered_and_reach_their_functions(self) -> None:
        self.assertEqual(proposers.DEFAULT, ("chords-jiang", "chords-chordino", "beats", "bass"))
        self.assertTrue(set(proposers.DEFAULT) <= set(proposers.PROPOSERS))
        for name, proposer in proposers.PROPOSERS.items():
            self.assertEqual(proposer.id, name)
            self.assertIs(proposer.propose, getattr(proposers, "propose_" + {"chords-jiang": "jiang",
                                                                               "chords-chordino": "chordino"}
                                                    .get(name, name).replace("-", "_")))

    def test_the_melody_extractor_never_prefers_the_bass(self) -> None:
        manifest = json.loads((Path(proposers.__file__).parent / "extractors" / "melody" / "extractor.json")
                              .read_text())
        preferred = [item["input"] for item in manifest["prefers"]]
        self.assertTrue(preferred)  # anchor: it does prefer stems
        self.assertFalse([name for name in preferred if name.endswith("/bass")])


class BassLine(unittest.TestCase):
    """notes-basic-pitch rows of the bass stem, kept by the bass proposer's
    thresholds and made one line."""
    GRID = {"first_centre": 0, "hop": 441, "sample_rate": RATE}  # a frame every 10 ms

    def test_kept_by_chroma_and_pitch_then_one_note_at_a_time(self) -> None:
        chroma = numpy.full((100, 12), 0.1)
        chroma[:50, 3] = 1.0  # class C (bin 0 is A) leads in the first half; nothing leads after
        pitched = numpy.ones(100)
        pitched[95:] = 0
        rows = [[0.0, 0.4, 48, 0.5], [0.0, 0.4, 36, 0.5],  # one onset: the lower is kept
                [0.1, 0.3, 45, 0.5],                     # A under a leading C: dropped
                [0.6, 0.9, 43, 0.5], [0.7, 0.8, 40, 0.5],  # nothing leads: kept, the lower cuts
                [0.75, 0.95, 50, 0.5],                   # higher, under a sounding note: dropped
                [0.96, 0.99, 41, 0.5]]                   # unpitched frames: dropped
        context = proposers.Context(bank.Bank(Path(tempfile.gettempdir())), "sha256:" + "a" * 64, RATE, RATE)
        line = proposers.bass_line(rows, chroma, pitched, self.GRID, context)
        self.assertEqual(line, [{"onset_sample": 0, "offset_sample": 17640, "pitch": 36},
                                {"onset_sample": 26460, "offset_sample": 30870, "pitch": 43},
                                {"onset_sample": 30870, "offset_sample": 35280, "pitch": 40}])

    def test_its_thresholds_are_the_bass_proposers(self) -> None:
        for name in ("min_pitched_share", "min_lead", "lead", "bin_0"):
            self.assertEqual(proposers.BASS_LINE_SETTINGS[name], proposers.BASS_SETTINGS[name])
        self.assertIs(proposers.BASS_LINE_SETTINGS["builder"], proposers.note_builder.BASS)


class Rows(unittest.TestCase):
    def test_a_row_is_its_exact_time_as_a_sample(self) -> None:
        context = proposers.Context(bank.Bank(Path(tempfile.gettempdir())), "sha256:" + "a" * 64, RATE, RATE)
        grid = {"sample_rate": 16000, "hop": 160, "first_centre": 0}
        self.assertEqual([proposers.row_sample(row, grid, context) for row in (0, 3, 7, 101)],
                         [0, 1323, 3087, 44100])


class Bleed(unittest.TestCase):
    GRID = {"first_centre": 0, "hop": 441, "sample_rate": RATE}

    @staticmethod
    def note(pitch: int, start: float, end: float) -> dict:
        return {"onset_sample": round(start * RATE), "offset_sample": round(end * RATE), "pitch": pitch}

    def setUp(self) -> None:
        self.levels = numpy.full(300, -10.0)
        self.levels[100:150] = -40.0  # the voice is quiet from 1.0 to 1.5 s
        self.sung = [self.note(60, 0, 0.5), self.note(64, 0.5, 0.9), self.note(67, 1.5, 2.0),
                     self.note(72, 2.0, 2.5)]
        self.low = self.note(45, 2.5, 2.6)
        self.quiet_on_bass = self.note(55, 1.0, 1.4)
        self.loud_on_bass = self.note(55, 2.6, 2.9)
        self.bass = [self.note(43, 0.9, 1.5), self.note(43, 2.6, 3.0)]
        self.notes = sorted([*self.sung, self.low, self.quiet_on_bass, self.loud_on_bass],
                            key=lambda n: n["onset_sample"])

    def test_below_the_range_and_quiet_on_the_bass_are_dropped(self) -> None:
        self.assertEqual(self.quiet_on_bass["pitch"] % 12, self.bass[0]["pitch"] % 12)  # anchor: they match
        kept, dropped = proposers.drop_bleed(self.notes, self.levels, self.GRID, -10, self.bass, RATE)
        self.assertEqual(dropped, {"below_range": 1, "bass_match": 1})
        self.assertNotIn(self.low, kept)
        self.assertNotIn(self.quiet_on_bass, kept)
        self.assertIn(self.loud_on_bass, kept)  # sung over the bass, loud: the singer's
        self.assertEqual(len(kept), len(self.notes) - 2)

    def test_a_note_two_octaves_above_the_bass_note_is_the_singers(self) -> None:
        g4, g3 = self.note(67, 1.0, 1.4), self.note(55, 1.0, 1.4)
        self.assertEqual({g4["pitch"] % 12, g3["pitch"] % 12}, {self.bass[0]["pitch"] % 12})  # anchor: one class
        notes = sorted([*self.sung, g4], key=lambda n: (n["onset_sample"], n["pitch"]))
        kept, dropped = proposers.drop_bleed(notes, self.levels, self.GRID, -10, self.bass, RATE)
        self.assertIn(g4, kept)  # G4 over a G2 bass: 24 semitones apart, not the bass
        self.assertEqual(dropped, {"below_range": 0, "bass_match": 0})
        kept, dropped = proposers.drop_bleed([*notes, g3], self.levels, self.GRID, -10, self.bass, RATE)
        self.assertNotIn(g3, kept)  # G3: an octave above the bass note
        self.assertEqual(dropped["bass_match"], 1)

    def test_without_a_bass_line_only_the_range_applies(self) -> None:
        kept, dropped = proposers.drop_bleed(self.notes, None, self.GRID, None, None, RATE)
        self.assertEqual(dropped, {"below_range": 1, "bass_match": 0})
        self.assertIn(self.quiet_on_bass, kept)


class TimedLyrics(unittest.TestCase):
    def test_forced_alignment_refuses_not_yet_and_says_what_it_needs(self) -> None:
        context = proposers.Context(bank.Bank(Path(tempfile.gettempdir())), "sha256:" + "a" * 64, RATE, RATE)
        found = proposers.PROPOSERS["timed-lyrics"].propose(context)
        self.assertIsInstance(found, proposers.Lacking)
        self.assertEqual(found.as_json()["code"], "not_yet")
        self.assertTrue(found.missing)


@needs_essentia
@support.needs_ffmpeg
class FromTheBank(unittest.TestCase):
    """Every proposer against one bank the real runner filled."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = Path(tempfile.mkdtemp(prefix="propose-bank-"))
        cls.wav = music(cls.directory / "take.wav")
        with patch.dict(os.environ):
            cls.root = fill_bank(cls.directory, cls.wav)
        cls.count = SECONDS * RATE
        cls.digest = bank.file_digest(cls.wav)
        cls.bank = bank.Bank(cls.root)
        cls.context = proposers.Context(cls.bank, cls.digest, RATE, cls.count)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.directory, True)

    def propose(self, name: str) -> proposers.Proposal:
        found = proposers.PROPOSERS[name].propose(self.context)
        self.assertIsInstance(found, proposers.Proposal, found)
        for planned in found.outputs:
            check(planned, self.count)
        return found

    def test_jiang_proposes_its_weighted_candidates_in_its_order(self) -> None:
        found = self.propose("chords-jiang")
        [planned] = found.outputs
        self.assertEqual((planned.contract, planned.name), (proposers.CHORD_STACKS, "chords"))
        payload = planned.payload
        self.assertTrue(payload["weight_scale"].startswith("chords-jiang 1 readings: mean over the span's frames"))
        entry = self.bank.entry(self.digest, "chords-jiang")
        document = json.loads(entry.file("readings").read_text())
        self.assertEqual(len(payload["spans"]), len(JIANG_LABELS))
        for span, item, (start, end, _) in zip(payload["spans"], document["spans"], JIANG_LABELS):
            self.assertEqual((span["start_sample"], span["end_sample"]), (round(start * RATE), round(end * RATE)))
            self.assertEqual([r["text"] for r in span["readings"]], [name for name, _ in item["top"]])
            self.assertEqual([r["weight"] for r in span["readings"]],
                             [proposers.decimal_text(weight) for _, weight in item["top"]])
        first = payload["spans"][0]["readings"][0]
        self.assertEqual(first, {"bass": 0, "tones": [4, 7], "root": 0, "text": "C:maj",
                                 "weight": first["weight"]})
        self.assertIn({"no_chord": True, "text": "N", "weight": payload["spans"][3]["readings"][0]["weight"]},
                      payload["spans"][3]["readings"])
        self.assertEqual([f["feature"] for f in found.features], ["chords-jiang/chords", "chords-jiang/readings"])
        self.assertEqual(found.features[1]["sha256"], entry.output("readings")["sha256"])
        self.assertEqual(found.features[0]["entry"], entry.manifest["key"])
        self.assertEqual(found.software, [("fake-model", "1")])

    def test_chordino_proposes_one_reading_a_span(self) -> None:
        [planned] = self.propose("chords-chordino").outputs
        self.assertNotIn("weight_scale", planned.payload)
        spans = planned.payload["spans"]
        self.assertEqual([s["readings"] for s in spans],
                         [[chord_labels.chordino_reading(label)] for _, label in CHORDINO_CHANGES])
        self.assertEqual(spans[-1]["end_sample"], CHORDINO_SECONDS * RATE)
        self.assertEqual([s["start_sample"] for s in spans], [proposers.sample_of(t, RATE, self.count)
                                                              for t, _ in CHORDINO_CHANGES])

    def test_beats_are_the_pulse_and_bars_are_published_downbeats(self) -> None:
        [planned] = self.propose("beats").outputs
        entry = self.bank.entry(self.digest, "rhythm")
        pulse = entry.items("pulse")
        self.assertGreater(len(pulse), 20)  # anchor: the tracker found the clicks
        layers = planned.payload["layers"]
        self.assertEqual(layers[0], {"beats": sorted({proposers.sample_of(e["time"], RATE, self.count)
                                                      for e in pulse})})
        self.assertNotIn("role", layers[0])
        bars = proposers.bars_of(pulse, entry.items("downbeats"), self.context)
        self.assertEqual(layers[1:], [{"bars": bars}] if bars else [])

    def test_the_bass_is_read_from_the_bass_stem_and_cited_by_bank_key(self) -> None:
        found = self.propose("bass")
        [planned] = found.outputs
        stems = self.bank.entry(self.digest, "stems")
        self.assertEqual(planned.payload["stem"], {"extractor": "stems", "key": stems.manifest["key"],
                                                   "output": "bass", "sha256": stems.output("bass")["sha256"]})
        self.assertEqual(planned.payload["weight_scale"], proposers.BASS_SCALE)
        spans = planned.payload["spans"]
        self.assertTrue(all(harmony.is_bass_only(s["readings"][0]) for s in spans))
        # The bass of the chord sounding over each span, by duration.
        agree = total = 0
        for span in spans:
            middle = (span["start_sample"] + span["end_sample"]) / 2 / RATE
            expected = BASS_CLASSES[int(middle // CHORD_SECONDS) % len(CHORDS)]
            length = span["end_sample"] - span["start_sample"]
            total += length
            agree += length * (span["readings"][0]["bass"] == expected)
        self.assertGreater(total, self.count // 2)
        self.assertGreater(agree / total, 0.9, spans)
        self.assertEqual({s["readings"][0]["bass"] for s in spans}, set(BASS_CLASSES))
        measured = self.bank.entry_from(self.digest, "stems/bass", "tonal")
        self.assertEqual([(f["feature"], f["content"]) for f in found.features],
                         [("stems/bass", self.digest), ("tonal/bass_chroma", stems.output("bass")["sha256"]),
                          ("tonal/pitched", stems.output("bass")["sha256"]), ("rhythm/pulse", self.digest)])
        self.assertEqual(found.features[1]["entry"], measured.manifest["key"])
        self.assertTrue(all(f["filed_under"] == self.digest for f in found.features))

    def test_the_bass_line_is_its_own_notes_claim_from_the_bass_stem(self) -> None:
        found = self.propose("bass-line")
        [planned] = found.outputs
        stems = self.bank.entry(self.digest, "stems")
        self.assertEqual(planned.contract, proposers.NOTES)
        self.assertIs(planned.payload["bass"], True)
        self.assertEqual(planned.payload["stem"]["output"], "bass")
        self.assertEqual(planned.payload["stem"]["key"], stems.manifest["key"])
        line = planned.payload["notes"]
        self.assertTrue(line)
        self.assertTrue(all(a["offset_sample"] <= b["onset_sample"] for a, b in zip(line, line[1:])))
        features = [f["feature"] for f in found.features]
        self.assertEqual(features[:3], ["stems/bass", "tonal/bass_chroma", "tonal/pitched"])
        self.assertIn("f0-pesto/f0", features)  # built from PESTO's track of the bass stem
        self.assertIn("f0-pesto/level_db", features)
        self.assertNotIn("notes-basic-pitch/notes", features)  # the fallback, not read when PESTO's track is there
        pesto = self.bank.entry_from(self.digest, "stems/bass", "f0-pesto")
        self.assertIn(pesto.manifest["key"], [f["entry"] for f in found.features])
        self.assertEqual(found.settings["run"]["source"], "f0-pesto")
        self.assertEqual(found.settings["min_lead"], proposers.BASS_SETTINGS["min_lead"])
        # The stand-in tracker follows the loudest partial, the bass of each chord.
        middle = [BASS_CLASSES[int((n["onset_sample"] + n["offset_sample"]) / 2 / RATE // CHORD_SECONDS)
                               % len(CHORDS)] for n in line]
        self.assertGreater(sum(n["pitch"] % 12 == c for n, c in zip(line, middle)) / len(line), 0.8, line)

    def test_no_proposer_makes_a_melody_and_any_bass_stem_notes_say_bass(self) -> None:
        melody = {("archive.claim/melody", "1"), ("archive.claim/melody_spans", "1")}
        made = 0
        for name, proposer in proposers.PROPOSERS.items():
            found = proposer.propose(self.context)
            if isinstance(found, proposers.Lacking):
                continue
            for planned in found.outputs:
                made += 1
                self.assertNotIn(planned.contract[0], {c for c, _ in melody}, name)
                if planned.payload.get("stem", {}).get("output") == "bass":
                    self.assertIs(planned.payload.get("bass", False) or planned.contract != proposers.NOTES,
                                  True, name)
        self.assertGreater(made, 5)  # anchor: the bank made claims to look at

    def test_key_tuning_notes_and_sections_make_claims_world_accepts(self) -> None:
        [keys] = self.propose("key").outputs
        self.assertTrue(all(r["readings"][0].get("collection") for r in keys.payload["regions"]))
        # tonal 6 names no tonic: every reading is a collection only, weighted by its margin.
        self.assertTrue(keys.payload["regions"])  # anchor: there are readings to look at
        self.assertTrue(all("tonic" not in r["readings"][0] and "mode" not in r["readings"][0]
                            for r in keys.payload["regions"]))
        self.assertIn("leads the best reading on other notes", keys.payload["weight_scale"])
        [tuning] = self.propose("tuning").outputs
        self.assertLess(abs(float(tuning.payload["reference_cents"])), 5)  # the music is tuned to 440 Hz
        [sections] = self.propose("sections").outputs
        self.assertTrue(all(s["label"] for s in sections.payload["spans"]))
        found = proposers.PROPOSERS["notes"].propose(self.context)
        # melody prefers the vocal stem, and passes over it when it is mostly unvoiced.
        entry, feature = self.bank.default_entry(self.digest, "melody")
        if isinstance(found, proposers.Lacking):
            self.assertEqual(found.missing, ())
            self.assertIn("no note", found.detail)
        else:
            [planned] = found.outputs
            check(planned, self.count)
            self.assertEqual(planned.payload["part"], "mix" if feature is None else feature.split("/")[1])
            self.assertEqual("stem" in planned.payload, feature is not None)
            features = [f["feature"] for f in found.features]
            if feature == "stems/vocals":  # the vocal stem's line is built from its PESTO track
                self.assertEqual(found.settings["notes"], proposers.NOTES_FROM_PESTO)
                self.assertIn("f0-pesto/f0", features)
                self.assertNotIn("melody/notes", features)
                self.assertEqual(found.settings["bleed"]["dropped"].keys(), {"below_range", "bass_match"})
            else:
                self.assertEqual(found.settings["notes"], proposers.NOTES_FROM_MELODY)

    def test_the_vocal_stems_pesto_track_is_built_into_notes(self) -> None:
        pesto = self.bank.entry_from(self.digest, "stems/vocals", "f0-pesto")
        self.assertIsNotNone(pesto)  # anchor: the runner measured the vocal stem
        found = proposers.Proposal([], {})
        notes, how = proposers.build_line(found, self.context, pesto, proposers.note_builder.VOICE, tempo=True)
        self.assertTrue(notes)
        self.assertTrue(all(a["offset_sample"] <= b["onset_sample"] for a, b in zip(notes, notes[1:])))
        self.assertEqual(how["tuning_from"], "tonal/tuning_cents")
        self.assertIsNotNone(how["eighth_s"])
        self.assertEqual(how["counts"]["notes"], len(notes))
        self.assertEqual([f["feature"] for f in found.features][:2], ["f0-pesto/f0", "f0-pesto/level_db"])


def fill_region_bank(directory: Path, content: Path, region: str) -> Path:
    """The bank `enrich backfill --songs` leaves (ADR 0112): the cheap
    extractors whole, the expensive ones and every measure of their stems
    over one region, each by the real runner with `--region`."""
    root = directory / "bank"
    common = ["--content", str(content), "--bank", str(root)]
    over = [*common, "--region", region]
    programs = stand_in_programs(directory)
    stems = ["--program", f"demucs-python={fake_python(directory)}"]
    run_extract("chords-chordino", *common, *programs)
    for target in ("rhythm", "tonal", "structure"):
        run_extract(target, *common)
    run_extract("chords-jiang", *over, *programs)
    run_extract("stems", *over, *stems)
    run_extract("tonal", *over, "--from", "stems/bass", *stems)
    run_extract("notes-basic-pitch", *over, "--from", "stems/bass", *stems,
                "--program", f"basic-pitch-python={basic_pitch_python(directory)}")
    for stem in ("stems/vocals", "stems/bass"):
        run_extract("f0-pesto", *over, "--from", stem, *stems, "--program", f"f0-python={f0_python(directory)}")
    run_extract("melody", *over, *stems)
    return root


def at_middles(spans: list[dict], low: int, high: int) -> dict[int, Any]:
    """Each span's first reading by the sample at its middle, for spans
    wholly inside [low, high)."""
    return {(s["start_sample"] + s["end_sample"]) // 2: s["readings"][0] for s in spans
            if low <= s["start_sample"] and s["end_sample"] <= high}


def reading_at(spans: list[dict], sample: int) -> Any:
    return next((s["readings"][0] for s in spans if s["start_sample"] <= sample < s["end_sample"]), None)


def note_f1(first: list[dict], second: list[dict], rate: int = RATE) -> float:
    """Onsets within 50 ms on the same pitch, each matched once."""
    unmatched = list(second)
    hits = 0
    for note in first:
        match = next((n for n in unmatched if n["pitch"] == note["pitch"]
                      and abs(n["onset_sample"] - note["onset_sample"]) <= 0.05 * rate), None)
        if match is not None:
            unmatched.remove(match)
            hits += 1
    return 2 * hits / (len(first) + len(second)) if first or second else 1.0


class FromRegions(unittest.TestCase):
    """A recording made under `--songs` (ADR 0112): the proposers find what
    they lack whole over each region, through `bank.RegionView`, and claim
    the same samples a whole bank gives over that span."""

    # Frame-aligned for every grid the extractors use (4,096-sample hops),
    # away from the edges of the 32 s take.
    START, END = 86 * 4096, 302 * 4096

    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = Path(tempfile.mkdtemp(prefix="propose-regions-"))
        cls.wav = music(cls.directory / "take.wav")
        cls.region = {"start": cls.START, "end": cls.END, "rate": RATE}
        for name in ("whole", "songs"):
            (cls.directory / name).mkdir()
        with patch.dict(os.environ):
            whole = fill_bank(cls.directory / "whole", cls.wav)
            songs = fill_region_bank(cls.directory / "songs", cls.wav, f"{cls.START}:{cls.END}:{RATE}")
        cls.count = SECONDS * RATE
        cls.digest = bank.file_digest(cls.wav)
        cls.whole = proposers.Context(bank.Bank(whole), cls.digest, RATE, cls.count)
        cls.songs = bank.Bank(songs)
        cls.flat = proposers.Context(cls.songs, cls.digest, RATE, cls.count)
        cls.over = proposers.Context(bank.RegionView(cls.songs, cls.digest, cls.region), cls.digest, RATE, cls.count,
                                     cls.region)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.directory, True)

    def both(self, name: str) -> tuple[proposers.Proposal, proposers.Proposal]:
        found = []
        for context in (self.whole, self.over):
            proposal = proposers.PROPOSERS[name].propose(context)
            self.assertIsInstance(proposal, proposers.Proposal, (name, proposal))
            for planned in proposal.outputs:
                check(planned, self.count)
            found.append(proposal)
        return found[0], found[1]

    def inside(self, planned: proposers.Planned, field: str, start: str, end: str) -> None:
        items = planned.payload[field]
        self.assertTrue(items)
        self.assertTrue(all(self.START <= item[start] and item[end] <= self.END for item in items), items[:3])

    def test_the_region_is_found_and_a_whole_read_lacks_what_was_made_over_it(self) -> None:
        self.assertEqual(bank.regions(self.songs, self.digest), [self.region])
        for name in ("chords-jiang", "bass", "bass-line"):
            with self.subTest(name=name):
                lacking = proposers.PROPOSERS[name].propose(self.flat)
                self.assertIsInstance(lacking, proposers.Lacking)
                self.assertTrue(lacking.missing)
        # Cheap extractors were made whole: the region view finds those whole.
        rhythm = self.over.bank.entry(self.digest, "rhythm")
        self.assertIsNone(rhythm.region)
        self.assertEqual(self.over.bank.entry(self.digest, "stems").region, self.region)

    def test_a_measure_of_a_region_stem_is_placed_on_the_region(self) -> None:
        [(stems, measures)] = self.over.bank.measured_over(self.digest, "stems/bass", ["tonal", "f0-pesto"])
        self.assertEqual(stems.region, self.region)
        for name, measured in measures.items():
            with self.subTest(measure=name):
                self.assertIsNone(measured.region)  # the stem is content of its own, measured whole
                self.assertEqual((measured.timeline, measured.shift(RATE)), (self.region, self.START))
                self.assertAlmostEqual(measured.offset, self.START / RATE)

    def test_region_chords_are_placed_at_the_regions_start(self) -> None:
        # The stand-in names the same chords at the same times of whatever it
        # reads, so a region's are the whole's moved by the region's start.
        whole, region = self.both("chords-jiang")
        [planned] = region.outputs
        self.inside(planned, "spans", "start_sample", "end_sample")
        moved = [{**s, "start_sample": s["start_sample"] + self.START, "end_sample": s["end_sample"] + self.START}
                 for s in whole.outputs[0].payload["spans"]]
        self.assertTrue(moved)
        self.assertEqual(planned.payload["spans"], moved)
        self.assertEqual([f["entry"] for f in region.features],
                         [self.over.bank.entry(self.digest, "chords-jiang").manifest["key"]] * 2)

    def test_region_bass_is_the_whole_bass_over_the_region(self) -> None:
        whole, region = self.both("bass")
        [planned] = region.outputs
        self.inside(planned, "spans", "start_sample", "end_sample")
        self.assertEqual(planned.payload["stem"]["key"], self.over.bank.entry(self.digest, "stems").manifest["key"])
        expected = at_middles(whole.outputs[0].payload["spans"], self.START + 2 * RATE, self.END - 2 * RATE)
        self.assertGreater(len(expected), 3)
        agree = sum((reading_at(planned.payload["spans"], m) or {}).get("bass") == r["bass"] for m, r in expected.items())
        self.assertGreaterEqual(agree / len(expected), 0.9, (expected, planned.payload["spans"][:6]))

    def test_region_bass_line_is_the_whole_bass_line_over_the_region(self) -> None:
        whole, region = self.both("bass-line")
        [planned] = region.outputs
        self.inside(planned, "notes", "onset_sample", "offset_sample")
        low, high = self.START + 2 * RATE, self.END - 2 * RATE
        a = [n for n in whole.outputs[0].payload["notes"] if low <= n["onset_sample"] < high]
        b = [n for n in planned.payload["notes"] if low <= n["onset_sample"] < high]
        self.assertGreater(len(a), 3)
        self.assertGreaterEqual(note_f1(a, b), 0.9, (a[:5], b[:5]))


if __name__ == "__main__":
    unittest.main()
