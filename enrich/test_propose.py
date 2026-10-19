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
from unittest.mock import patch

import numpy

import bank
import chord_labels
import extract
import proposers
import support
from test_beats_chords import (CHORDINO_CHANGES, CHORDINO_SECONDS, JIANG_LABELS, stand_in_programs)
from test_cover import needs_essentia
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
            "sections": {"missing": ["structure/sections"]},
        })

    def test_the_default_proposers_are_registered_and_reach_their_functions(self) -> None:
        self.assertEqual(proposers.DEFAULT, ("chords-jiang", "chords-chordino", "beats", "bass"))
        self.assertTrue(set(proposers.DEFAULT) <= set(proposers.PROPOSERS))
        for name, proposer in proposers.PROPOSERS.items():
            self.assertEqual(proposer.id, name)
            self.assertIs(proposer.propose, getattr(proposers, "propose_" + {"chords-jiang": "jiang",
                                                                               "chords-chordino": "chordino"}
                                                    .get(name, name)))


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

    def test_key_tuning_notes_and_sections_make_claims_world_accepts(self) -> None:
        [keys] = self.propose("key").outputs
        self.assertTrue(all(r["readings"][0].get("collection") for r in keys.payload["regions"]))
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


if __name__ == "__main__":
    unittest.main()
