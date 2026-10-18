"""The structure extractor, run by the real runner on synthetic rehearsals (ADR 0074).

Every input is written with numpy and Python's `wave` (`support.write_wav`)
and measured by the real `frames` and `rhythm` extractors, which the runner
runs first because `structure` needs them; nothing here hand-writes a
feature. The pieces are what a rehearsal holds: room noise, a guitar tuned
string by string, drumstick count-ins, songs of looped chord progressions
over a beat, a false start, a final chord ringing out, a fade, and a
recording that stops while the band plays. One is a master instead: no
room under it, limited, with quiet passages that are music.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import numpy

import bank
import extract
import support
from support import needs_ffmpeg
from test_cover import needs_essentia

sys.path.insert(0, str(Path(__file__).resolve().parent / "extractors" / "structure"))
import structure  # noqa: E402

RATE = 44100

# Chords as MIDI notes. The verse and chorus share no chord; B and the
# other song are in other keys.
VERSE = ((57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62))  # Am F C G
CHORUS = ((54, 58, 61), (59, 63, 66), (49, 53, 56), (59, 63, 66))  # F# B C# B
SONG_B = ((52, 56, 59), (57, 61, 64), (59, 63, 66), (57, 61, 64))  # E A B A
SONG_C = ((50, 53, 57), (46, 50, 53), (48, 52, 55), (45, 48, 52))  # Dm Bb C Am
OTHER = ((51, 55, 58), (56, 60, 63), (58, 62, 65), (49, 53, 56))  # Eb Ab Bb Db
GUITAR = (40, 45, 50, 55, 59, 64)  # E A D G B E


# -- synthetic rehearsal pieces ----------------------------------------------------

def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def room(seconds: float, seed: int = 0, amplitude: float = 0.002) -> numpy.ndarray:
    """A room's noise floor, about -54 dBFS."""
    return numpy.random.default_rng(seed).normal(0, amplitude, int(round(seconds * RATE)))


def place(samples: numpy.ndarray, sound: numpy.ndarray, at: float) -> None:
    index = int(round(at * RATE))
    end = min(len(samples), index + len(sound))
    if 0 <= index < len(samples):
        samples[index:end] += sound[:end - index]


def hit(amplitude: float = 0.35) -> numpy.ndarray:
    """A drum hit: a 1.5 kHz tone decaying in about 50 ms."""
    t = numpy.arange(int(0.08 * RATE)) / RATE
    return amplitude * numpy.exp(-60 * t) * numpy.sin(2 * numpy.pi * 1500 * t)


def stick(seed: int, amplitude: float = 0.4) -> numpy.ndarray:
    """A drumstick click: a noise burst decaying in about 20 ms."""
    t = numpy.arange(int(0.06 * RATE)) / RATE
    return amplitude * numpy.exp(-150 * t) * numpy.random.default_rng(seed).uniform(-1, 1, len(t))


def held(notes, seconds: float, amplitude: float = 0.08) -> numpy.ndarray:
    """A chord of three-harmonic tones held for `seconds`."""
    t = numpy.arange(int(round(seconds * RATE))) / RATE
    envelope = numpy.minimum(1, numpy.minimum(t / 0.01, (seconds - t) / 0.03))
    return amplitude * envelope * sum(numpy.sin(2 * numpy.pi * hz(m) * h * t) / h for m in notes for h in (1, 2, 3))


def song(seconds: float, progression, bpm: float = 120.0, seed: int = 0, drums: bool = True) -> numpy.ndarray:
    """A progression a bar (four beats) a chord, looped, with a hit on every beat."""
    beat = 60.0 / bpm
    out = room(seconds, seed)
    at, index = 0.0, 0
    while at < seconds - 1e-9:
        place(out, held(progression[index % len(progression)], min(4 * beat, seconds - at)), at)
        at += 4 * beat
        index += 1
    if drums:
        for k, time in enumerate(numpy.arange(0, seconds - 0.05, beat)):
            place(out, hit(0.35 if k % 2 == 0 else 0.25), time)
    return out


def ring_out(notes, seconds: float = 3.0, seed: int = 0) -> numpy.ndarray:
    """A last chord left to ring: falling about 26 dB a second."""
    t = numpy.arange(int(round(seconds * RATE))) / RATE
    return held(notes, seconds) * numpy.exp(-3 * t) + room(seconds, seed)


def pluck(midi: int, seconds: float, cents: float = 0.0, amplitude: float = 0.04) -> numpy.ndarray:
    """A plucked string: six harmonics falling as 1/n, decaying."""
    t = numpy.arange(int(round(seconds * RATE))) / RATE
    f = hz(midi + cents / 100)
    return (amplitude * numpy.minimum(1, t / 0.005) * numpy.exp(-0.6 * t)
            * sum(numpy.sin(2 * numpy.pi * f * h * t) / h for h in range(1, 7)))


def tuning(notes=GUITAR, plucks: int = 3, seed: int = 0) -> numpy.ndarray:
    """Tuning up: each string plucked three times, out by up to 30 cents and
    coming into tune, with a pause between strings."""
    rng = numpy.random.default_rng(seed)
    pieces = []
    for note in notes:
        for k in range(plucks):
            cents = rng.uniform(-30, 30) * (plucks - 1 - k) / max(1, plucks - 1)
            pieces.append(pluck(note, 1.4, cents) + room(1.4, seed + 10 * note + k))
        pieces.append(room(0.6, seed + note))
    return numpy.concatenate(pieces)


def count_in(clicks: int = 4, bpm: float = 120.0, seed: int = 0) -> numpy.ndarray:
    beat = 60.0 / bpm
    out = room(clicks * beat, seed)
    for k in range(clicks):
        place(out, stick(seed + k), k * beat)
    return out


def fade(samples: numpy.ndarray, seconds: float) -> numpy.ndarray:
    gain = numpy.ones(len(samples))
    count = int(round(seconds * RATE))
    gain[-count:] = numpy.linspace(1, 0, count) ** 2
    return samples * gain


def dry(seconds: float, progression, bpm: float = 120.0, seed: int = 0) -> numpy.ndarray:
    """A song with no room under it: a studio's, not a rehearsal's."""
    return song(seconds, progression, bpm, seed) - room(seconds, seed)


def line(notes, seconds: float, step: float = 0.5, amplitude: float = 0.012) -> numpy.ndarray:
    """A quiet single-note line, a note every `step` seconds, with nothing under it."""
    out = numpy.zeros(int(round(seconds * RATE)))
    for k, at in enumerate(numpy.arange(0, seconds, step)):
        place(out, pluck(notes[k % len(notes)], step + 0.5, amplitude=amplitude), at)
    return out


def mastered(pieces: list[tuple[str, numpy.ndarray]]) -> tuple[numpy.ndarray, dict[str, float]]:
    """The pieces in order, softly limited and brought up to a peak of
    -0.1 dBFS, as a master is, with no noise floor anywhere."""
    samples, marks = rehearsal(pieces)
    limited = numpy.tanh(3 * samples / numpy.max(numpy.abs(samples)))
    return limited / numpy.max(numpy.abs(limited)) * 10 ** (-0.1 / 20), marks


def rehearsal(pieces: list[tuple[str, numpy.ndarray]]) -> tuple[numpy.ndarray, dict[str, float]]:
    """The pieces in order, and the second each starts at."""
    marks, at = {}, 0
    for name, samples in pieces:
        marks[name] = at / RATE
        at += len(samples)
    marks["end"] = at / RATE
    return numpy.concatenate([samples for _, samples in pieces]), marks


# -- running ---------------------------------------------------------------------

def scratch(test: unittest.TestCase | type) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="bank-structure-"))
    if isinstance(test, unittest.TestCase):
        test.addCleanup(shutil.rmtree, directory, True)
    return directory


def run(directory: Path, audio: Path, parameters: dict | None = None, target: str = "structure") -> tuple[int, dict]:
    argv = ["run", target, "--content", str(audio), "--bank", str(directory / "bank")]
    if parameters is not None:
        config = directory / "config.json"
        config.write_text(json.dumps({"parameters": {"structure": parameters}}))
        argv += ["--config", str(config)]
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = extract.main(argv)
    return code, json.loads(out.getvalue() or err.getvalue())


def entry_of(directory: Path, audio: Path, parameters: dict | None = None, extractor: str = "structure") -> bank.Entry:
    overrides = None if parameters is None else {"structure": parameters}
    found = bank.Bank(directory / "bank", parameters=overrides).entry(bank.file_digest(audio), extractor)
    assert found is not None
    return found


def measured(directory: Path, samples: numpy.ndarray, name: str, parameters: dict | None = None) -> bank.Entry:
    audio = support.write_wav(directory / f"{name}.wav", samples, RATE)
    code, answer = run(directory, audio, parameters)
    if code != 0:
        raise AssertionError(answer)
    return entry_of(directory, audio, parameters)


def labels(entry: bank.Entry, name: str) -> list[str]:
    return [item.get("label") for item in entry.items(name)]


def features_of(directory: Path, audio: Path) -> structure.Features:
    """The features the extractor would read, from the real entries, as its
    request would name them."""
    needs = needs_of(directory, audio)
    return structure.read_needs(needs, structure.parameters()["frames_grid"])


def needs_of(directory: Path, audio: Path) -> dict:
    """The request's `needs`, built from the bank as the runner builds it."""
    found = {}
    for need in structure.NEEDS:
        source, output = need.split("/")
        entry = entry_of(directory, audio, extractor=source)
        described = dict(entry.output(output))
        if "file" in described:
            described["path"] = str(entry.path / described["file"])
        if described.get("grid"):
            described["grid"] = {"name": described["grid"], **entry.manifest["grids"][described["grid"]]}
        found[need] = described
    return found


# -- the tests ----------------------------------------------------------------------

class Manifest(unittest.TestCase):
    def test_the_manifest_declares_exactly_what_the_program_implements(self) -> None:
        extractor = bank.Catalog.load()["structure"]
        self.assertEqual(extractor.manifest["parameters"], structure.parameters())
        self.assertEqual(extractor.manifest["needs"], list(structure.NEEDS))
        self.assertEqual(structure.settings(structure.parameters()), structure.parameters())
        # It reads the frames grid exactly as frames declares it.
        frames = bank.Catalog.load()["frames"]
        grid = structure.parameters()["frames_grid"]
        self.assertEqual((grid["sample_rate"], grid["hop"], grid["frame"]),
                         (frames.manifest["parameters"]["analysis_rate"], frames.manifest["parameters"]["hop_size"],
                          frames.manifest["parameters"]["frame_size"]))
        self.assertEqual(set(structure.TUNABLE) - set(structure.parameters()), set())

    def test_settings_outside_what_it_implements_are_refused(self) -> None:
        for change, message in (({"open_strings": ["E"]}, "implements open_strings"),
                                ({"novelty": "other"}, "implements novelty"),
                                ({"range_db": 5}, "range_db must be"),
                                ({"range_db": True}, "range_db must be"),
                                ({"held_ms": "700"}, "held_ms must be"),
                                ({"count_in_min_interval_ms": 900, "count_in_max_interval_ms": 800},
                                 "below count_in_max"),
                                ({"loop_min_period_ms": 9000, "loop_max_period_ms": 8000}, "below loop_max")):
            with self.subTest(change=change):
                with self.assertRaisesRegex(structure.StructureError, message):
                    structure.settings({**structure.parameters(), **change})
        with self.assertRaisesRegex(structure.StructureError, "not structure's"):
            structure.settings({**structure.parameters(), "extra": 1})
        with self.assertRaisesRegex(structure.StructureError, "not structure's"):
            structure.settings(None)


class Pieces(unittest.TestCase):
    def test_small_pieces(self) -> None:
        self.assertEqual([structure.section_name(i) for i in (0, 1, 25, 26, 27, 701, 702)],
                         ["A", "B", "Z", "AA", "AB", "ZZ", "AAA"])
        self.assertEqual(structure.merge([(0, 5), (7, 9), (20, 30)], 2), [(0, 9), (20, 30)])
        self.assertEqual(structure.merge([], 2), [])
        self.assertEqual(structure.runs(numpy.array([0, 1, 1, 0, 1], dtype=bool)), [(1, 3), (4, 5)])
        self.assertEqual((structure.ramp(5, 0, 10), structure.ramp(-1, 0, 10), structure.ramp(3, 10, 0)),
                         (0.5, 0.0, 0.7))
        self.assertEqual(structure.ramp(1, 1, 1), 1.0)
        mask = numpy.array([1, 1, 1, 0, 0, 0, 0, 1], dtype=bool)
        self.assertEqual(list(structure.centred_fraction(mask, 3)), [1.0, 1.0, 2 / 3, 1 / 3, 0, 0, 1 / 3, 0.5])

    def test_a_floor_is_steady_onset_free_quiet_well_below_the_playing(self) -> None:
        p = structure.parameters()
        rng = numpy.random.default_rng(3)
        none = numpy.zeros(0)
        # Playing at -20 with a few dB of movement, and room noise at -60 steady to a dB.
        playing = -20 + rng.uniform(-3, 3, 200)
        room = -60 + rng.uniform(-0.5, 0.5, 100)
        found = structure.levels(numpy.r_[room[:50], playing, room[50:]], none, p)
        self.assertTrue(found.floor)
        self.assertLess(abs(found.floor_db + 60), 0.5)
        self.assertEqual(found.threshold_db, found.floor_db + p["floor_margin_db"])
        # The same quiet struck again and again (a quiet passage of music) is no floor ...
        times = numpy.arange(0, 300 * structure.SECONDS, 0.4)
        struck = structure.levels(numpy.r_[room[:50], playing, room[50:]], times, p)
        self.assertFalse(struck.floor)
        # ... nor is it when it moves (a fade, a crescendo) ...
        moving = numpy.r_[numpy.linspace(-70, -50, 50), playing, numpy.linspace(-50, -70, 50)]
        self.assertFalse(structure.levels(moving, none, p).floor)
        # ... nor when it lies within floor_depth_db of the playing (a held drone in a master).
        lift = 40 - p["floor_depth_db"] + 5  # the drone at -60 + lift, a few dB less than floor_depth_db below
        drone = numpy.r_[room[:50] + lift, playing, room[50:] + lift]
        self.assertLess(numpy.percentile(drone, 95) - (room + lift).min(), p["floor_depth_db"])  # anchor
        self.assertFalse(structure.levels(drone, none, p).floor)
        # ... nor when there is less than floor_min_ms of it.
        width = structure.frames_of(p["sustain_ms"])
        # The room frames whose window reaches into the playing move too much to count.
        enough = structure.frames_of(p["floor_min_ms"]) + width - width // 2 - 1
        self.assertTrue(structure.levels(numpy.r_[room[:enough], playing], none, p).floor)  # anchor
        self.assertFalse(structure.levels(numpy.r_[room[:enough - 1], playing], none, p).floor)

    def test_without_a_floor_levels_measure_from_the_reference_and_never_below_silence(self) -> None:
        p = structure.parameters()
        none = numpy.zeros(0)
        loud = structure.levels(numpy.r_[numpy.full(90, -20.0), numpy.full(10, -30.0)], none, p)
        self.assertFalse(loud.floor)
        self.assertEqual(loud.threshold_db, -20.0 - p["range_db"])  # the recording has no quiet to call a floor
        self.assertEqual(loud.floor_db, -30.0)  # the 5th percentile, reported all the same
        quiet = structure.levels(numpy.r_[numpy.full(50, -20.0), numpy.full(50, -60.0)], none, p)
        self.assertTrue(quiet.floor)
        self.assertEqual(quiet.threshold_db, -60.0 + p["floor_margin_db"])
        silent = structure.levels(numpy.full(100, -120.0), none, p)
        self.assertFalse(silent.floor)  # digital silence is below silence_db, no floor
        self.assertEqual(silent.threshold_db, p["silence_db"])
        self.assertFalse(silent.active.any())

    def test_the_lowest_band_and_the_moving_range(self) -> None:
        self.assertEqual(structure.lowest_band(numpy.array([-70.0, -50, -49, -48, -10, -9]), 6, 3), -49.0)
        self.assertEqual(structure.lowest_band(numpy.array([-70.0, -50, -40]), 6, 2), None)
        self.assertEqual(structure.lowest_band(numpy.zeros(0), 6, 1), None)
        values = numpy.array([0.0, 0, 5, 0, 0, 0, 0])
        self.assertEqual(list(structure.moving_range(values, 3)), [0, 5, 5, 5, 0, 0, 0])
        self.assertEqual(list(structure.moving_range(values, 4)), [0, 5, 5, 5, 5, 0, 0])

    def test_loops_measured_a_chunk_at_a_time_are_those_measured_at_once(self) -> None:
        rng = numpy.random.default_rng(4)
        period = rng.normal(0, 1, (37, 25))
        frames = numpy.vstack([numpy.tile(period, (6, 1)), rng.normal(0, 1, (150, 25)),
                               numpy.tile(rng.normal(0, 1, (51, 25)), (4, 1))]) + rng.normal(0, 0.1, (576, 25))
        unit = structure.unit_rows(frames).astype(numpy.float32)
        p = structure.parameters()
        lags = range(structure.frames_of(p["loop_min_period_ms"]), structure.frames_of(p["loop_max_period_ms"]) + 1)
        whole = structure.loop_choices(structure.lag_similarity(unit, lags), p)
        self.assertGreater((whole[0] >= 0).sum(), 100)  # anchor: some frames are in a loop
        f = structure.Features(numpy.zeros(len(frames)), numpy.ones((len(frames), 12)), numpy.zeros((len(frames), 13)),
                               numpy.zeros(0), numpy.zeros(0), len(frames) * structure.SECONDS)
        stretch = [structure.Stretch(0, len(frames))]
        at_once = structure.loops(frames, f, stretch, p)
        with patch.object(structure, "LOOP_CHUNK", 50):
            chunked = structure.loops(frames, f, stretch, p)
        self.assertEqual(chunked, at_once)
        self.assertEqual([item["label"].split()[0] for item in at_once], ["period=3.437", "period=4.737"])

    def test_openings_alike_only_as_everything_is_are_no_restart(self) -> None:
        p = structure.parameters()
        rng = numpy.random.default_rng(5)
        varied = rng.uniform(0, 1, (400, 12))
        first, second = structure.Stretch(0, 100), structure.Stretch(150, 400)
        varied[150:250] = varied[0:100]  # the same opening, then other music
        self.assertGreater(structure.opening_similarity(varied, first, second, p), 0.5)  # anchor
        # Every frame the same profile: the baseline is 1, and nothing is more alike than chance.
        self.assertEqual(structure.opening_similarity(numpy.ones((400, 12)), first, second, p), 0.0)

    def test_spans_end_with_the_audio(self) -> None:
        items = [{"start": 1.0, "end": 2.5, "value": 1.0}, {"start": 3.1, "end": 3.2}]
        self.assertEqual(structure.within(items, 3.0),
                         [{"start": 1.0, "end": 2.5, "value": 1.0}, {"start": 3.0, "end": 3.0}])

    def test_values_that_are_not_finite_are_refused(self) -> None:
        f = structure.Features(numpy.array([-20.0, numpy.nan]), numpy.ones((2, 12)), numpy.zeros((2, 13)),
                               numpy.zeros(0), numpy.zeros(0), 0.1)
        with self.assertRaisesRegex(structure.StructureError, "not finite"):
            structure.analyse(f, structure.parameters())
        empty = structure.Features(numpy.zeros(0), numpy.zeros((0, 12)), numpy.zeros((0, 13)), numpy.zeros(0),
                                   numpy.zeros(0), 0.0)
        with self.assertRaisesRegex(structure.StructureError, "empty"):
            structure.analyse(empty, structure.parameters())


@needs_ffmpeg
@needs_essentia
class Rehearsal(unittest.TestCase):
    """One synthetic rehearsal, measured once: tuning, three count-ins, a song
    with a verse, a chorus, and the verse again, a false start, the same song
    started again and faded, and a third song the recording cuts off."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = scratch(cls)
        samples, cls.marks = rehearsal([
            ("room", room(5, 1)),
            ("tuning", tuning()),
            ("gap1", room(4, 2)),
            ("count_in_a", count_in(4, 120, 3)),
            ("song_a", numpy.concatenate([song(32, VERSE, 120, 4), song(16, CHORUS, 120, 5), song(16, VERSE, 120, 6)])),
            ("ring", ring_out(VERSE[0], 3.0, 7) + room(3.0, 7)),
            ("gap2", room(6, 8)),
            ("count_in_false", count_in(4, 140, 9)),
            ("false", song(9, SONG_B, 140, 10)),
            ("gap3", room(5, 11)),
            ("count_in_b", count_in(4, 140, 12)),
            ("song_b", fade(song(50, SONG_B, 140, 13), 10)),
            ("gap4", room(4, 14)),
            ("song_c", song(30, SONG_C, 100, 15)),
        ])
        cls.audio = support.write_wav(cls.directory / "rehearsal.wav", samples, RATE)
        code, cls.answer = run(cls.directory, cls.audio)
        assert code == 0, cls.answer
        cls.entry = entry_of(cls.directory, cls.audio)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.directory, True)

    def near(self, found: float, expected: float, within: float) -> None:
        self.assertLess(abs(found - expected), within, (found, expected))

    def test_upstream_ran_first_and_a_second_run_starts_nothing(self) -> None:
        self.assertEqual([self.answer["extractors"][name]["status"] for name in ("frames", "rhythm", "structure")],
                         ["ran", "ran", "ran"])
        manifest = self.entry.manifest
        self.assertEqual(set(manifest["needs"]), set(structure.NEEDS))
        with patch.object(extract.subprocess, "Popen", side_effect=AssertionError("ran again")):
            code, again = run(self.directory, self.audio)
        self.assertEqual(code, 0)
        self.assertEqual(again["extractors"]["structure"]["status"], "hit")

    def test_playing(self) -> None:
        playing = self.entry.items("playing")
        m = self.marks
        expected = [(m["song_a"], m["ring"] + 1.0), (m["false"], m["gap3"]), (m["song_b"], m["gap4"] - 1.5),
                    (m["song_c"], m["end"])]
        self.assertEqual(len(playing), len(expected), playing)
        for span, (start, end) in zip(playing, expected):
            self.near(span["start"], start, 0.2)
            self.near(span["end"], end, 1.6)
        # The count-ins and the tuning are not playing.
        self.assertGreater(playing[0]["start"], m["count_in_a"] + 1.5)

    def test_tuning(self) -> None:
        tuning_spans = self.entry.items("tuning")
        self.assertEqual(len(tuning_spans), 1, tuning_spans)
        span = tuning_spans[0]
        self.near(span["start"], self.marks["tuning"], 0.3)
        self.assertGreater(span["end"], self.marks["tuning"] + 23)  # at least the first five strings
        self.assertLess(span["end"], self.marks["gap1"] + 0.5)
        self.assertTrue(span["label"].startswith("E A D G B"), span)
        self.assertGreaterEqual(span["value"], 0.75)

    def test_count_ins(self) -> None:
        found = self.entry.items("count_ins")
        m = self.marks
        self.assertEqual(len(found), 3, found)
        for span, (start, music, interval) in zip(found, ((m["count_in_a"], m["song_a"], 0.5),
                                                         (m["count_in_false"], m["false"], 60 / 140),
                                                         (m["count_in_b"], m["song_b"], 60 / 140))):
            self.near(span["start"], start, 0.03)  # an onset is up to 15 ms early
            self.near(span["end"], music, 0.2)
            clicks, spacing = span["label"].split()
            self.assertEqual(clicks, "clicks=4")
            self.near(float(spacing.split("=")[1]), interval, 0.015)
            self.assertGreater(span["value"], 0.8)

    def test_false_start(self) -> None:
        found = self.entry.items("false_starts")
        self.assertEqual(len(found), 1, found)
        self.near(found[0]["start"], self.marks["false"], 0.2)
        self.assertGreater(float(found[0]["label"].split("=")[1]), 0.9)  # the same song, started again
        self.assertGreater(found[0]["value"], 0.9)

    def test_endings(self) -> None:
        found = self.entry.items("endings")
        self.assertEqual(labels(self.entry, "endings"), ["stop", "abrupt", "fade", "abrupt"], found)
        stop, cut, faded, unfinished = found
        self.near(stop["start"], self.marks["ring"], 0.3)  # the last chord starts ringing out
        self.assertLess(cut["end"] - cut["start"], 0.31)
        self.assertGreater(faded["end"] - faded["start"], 5)
        self.assertLess(faded["start"], self.marks["gap4"] - 6)
        self.near(unfinished["end"], self.marks["end"], 0.2)
        self.assertEqual(unfinished["value"], 1.0)
        # It ends with the audio, not with the frames grid, whose last rows reach past it.
        frames = entry_of(self.directory, self.audio, extractor="frames")
        seconds = frames.scalar("seconds")
        self.assertGreater(len(frames.series("rms_db")) * 2048 / 22050, seconds + 0.05)  # anchor
        self.assertEqual(unfinished["end"], round(seconds, 6))
        self.assertEqual(self.entry.items("playing")[-1]["end"], round(seconds, 6))
        for name in ("playing", "tuning", "count_ins", "false_starts", "endings", "sections", "loops"):
            for item in self.entry.items(name):
                self.assertLessEqual(item["end"], round(seconds, 6), (name, item))
        for item in found:
            self.assertGreaterEqual(item["value"], 0.5)

    def test_sections_of_the_first_song(self) -> None:
        song_a = [item for item in self.entry.items("sections")
                  if self.marks["song_a"] - 1 < item["start"] < self.marks["ring"] + 3]
        self.assertEqual([item["label"] for item in song_a], ["A", "B", "A"], song_a)
        # A boundary is on a block's edge: within a block (1.02 s) of the change.
        self.near(song_a[1]["start"], self.marks["song_a"] + 32, 1.03)
        self.near(song_a[2]["start"], self.marks["song_a"] + 48, 1.03)
        self.assertGreater(min(item["value"] for item in song_a), 0.15)
        novelty = self.entry.series("novelty").reshape(-1)
        grid = self.entry.grid("novelty")
        self.assertEqual(grid, {"sample_rate": 22050, "hop": 11 * 2048, "frame": 2 * 8 * 11 * 2048, "first_centre": 0})
        seconds = (grid["first_centre"] + numpy.arange(len(novelty)) * grid["hop"]) / grid["sample_rate"]
        # A boundary's row stands at the section's start, holds its value, and is the curve's peak there.
        for section in song_a[1:]:
            row = int(numpy.argmin(numpy.abs(seconds - section["start"])))
            self.assertAlmostEqual(float(seconds[row]), section["start"], places=5)
            self.assertAlmostEqual(float(novelty[row]), section["value"], places=3)
            self.assertEqual(float(novelty[row]), float(novelty[row - 3:row + 4].max()))
        self.assertLessEqual(float(novelty.max()), 1.0)
        # The curve peaks at the chorus, and is 0 in the quiet between songs.
        around = (seconds > self.marks["song_a"] + 28) & (seconds < self.marks["song_a"] + 36)
        self.assertGreater(float(novelty[around].max()), 0.15)
        self.assertEqual(float(novelty[(seconds > self.marks["gap2"]) & (seconds < self.marks["false"])].max()), 0.0)
        self.assertTrue(numpy.isfinite(novelty).all())
        # Every stretch is cut into sections that tile it.
        playing = self.entry.items("playing")
        for span in playing:
            inside = [s for s in self.entry.items("sections") if span["start"] <= s["start"] < span["end"]]
            self.assertEqual(inside[0]["start"], span["start"])
            self.assertEqual(inside[-1]["end"], span["end"])
            for a, b in zip(inside, inside[1:]):
                self.assertEqual(a["end"], b["start"])

    def test_loops(self) -> None:
        found = self.entry.items("loops")
        periods = {}
        for item in found:
            fields = dict(word.split("=") for word in item["label"].split())
            periods[round(item["start"])] = (float(fields["period"]), int(fields["repeats"]), fields.get("beats"))
        m = self.marks
        verse = [v for k, v in periods.items() if abs(k - m["song_a"]) < 2]
        self.assertEqual(len(verse), 1, periods)
        self.near(verse[0][0], 8.0, 0.1)
        self.assertEqual(verse[0][1:], (4, "16"))
        song_b = [v for k, v in periods.items() if abs(k - m["song_b"]) < 2]
        self.assertEqual(len(song_b), 1, periods)
        self.near(song_b[0][0], 4 * 4 * 60 / 140, 0.1)
        self.assertGreaterEqual(song_b[0][1], 5)
        # The false start is too short to repeat three times; the chorus repeats only twice.
        self.assertFalse(any(m["false"] - 1 < k < m["gap3"] for k in periods), periods)
        self.assertFalse(any(m["song_a"] + 30 < k < m["song_a"] + 46 for k in periods), periods)
        for item in found:
            self.assertGreaterEqual(item["value"], 0.5)

    def test_scalars(self) -> None:
        self.near(self.entry.scalar("floor_db"), -54, 4)
        self.near(self.entry.scalar("reference_db"), -18, 4)
        self.assertEqual(self.entry.scalar("threshold_db"), round(self.entry.scalar("floor_db") + 10, 3))
        self.assertIs(self.entry.scalar("floor"), True)

    def test_another_setting_is_another_entry(self) -> None:
        shorter = {**structure.parameters(), "false_start_max_ms": 5000}
        code, answer = run(self.directory, self.audio, shorter)
        self.assertEqual(code, 0, answer)
        self.assertNotEqual(answer["extractors"]["structure"]["key"], self.answer["extractors"]["structure"]["key"])
        self.assertEqual(answer["extractors"]["frames"]["status"], "hit")
        # Anchor: the default entry has the 9 s false start; a 5 s limit does not.
        self.assertEqual(len(self.entry.items("false_starts")), 1)
        self.assertEqual(entry_of(self.directory, self.audio, shorter).items("false_starts"), [])

    def test_it_decodes_nothing(self) -> None:
        # An ffmpeg that leaves a mark: with frames and rhythm in the bank, structure never starts it.
        directory = scratch(self)
        fake = directory / "bin"
        fake.mkdir()
        mark = directory / "ffmpeg-ran"
        (fake / "ffmpeg").write_text(f"#!/bin/sh\ntouch {mark}\nexit 1\n")
        (fake / "ffmpeg").chmod(stat.S_IRWXU)
        other = {**structure.parameters(), "range_db": 40}
        with patch.dict(os.environ, {"PATH": f"{fake}:{os.environ.get('PATH', '')}"}):
            code, answer = run(self.directory, self.audio, other)
        self.assertEqual(code, 0, answer)
        self.assertEqual(answer["extractors"]["structure"]["status"], "ran")
        self.assertFalse(mark.exists())

    def test_the_request_s_needs_are_checked(self) -> None:
        needs = needs_of(self.directory, self.audio)
        grid = structure.parameters()["frames_grid"]
        features = structure.read_needs(needs, grid)
        self.assertEqual(features.hpcp.shape[1], 12)
        self.assertEqual(len(features.onsets), len(entry_of(self.directory, self.audio, extractor="rhythm")
                                                   .items("onsets")))
        missing = dict(needs)
        del missing["rhythm/beats"]
        with self.assertRaisesRegex(structure.StructureError, "does not give rhythm/beats"):
            structure.read_needs(missing, grid)
        moved = json.loads(json.dumps(needs))
        moved["frames/mfcc"]["grid"]["hop"] = 1024
        with self.assertRaisesRegex(structure.StructureError, "frames/mfcc is on grid"):
            structure.read_needs(moved, grid)
        wrong = json.loads(json.dumps(needs))
        wrong["frames/hpcp"]["dtype"] = "f64"
        with self.assertRaisesRegex(structure.StructureError, "not an f32 series"):
            structure.read_needs(wrong, grid)
        short = json.loads(json.dumps(needs))
        short["frames/rms_db"]["shape"] = [short["frames/rms_db"]["shape"][0] + 1, 1]
        with self.assertRaisesRegex(structure.StructureError, "does not hold its shape"):
            structure.read_needs(short, grid)
        # Series of different lengths: a level file cut short, declared at its new shape.
        cut = scratch(self) / "rms_db.f32"
        cut.write_bytes(Path(needs["frames/rms_db"]["path"]).read_bytes()[:-8])
        unequal = json.loads(json.dumps(needs))
        unequal["frames/rms_db"].update(path=str(cut), shape=[needs["frames/rms_db"]["shape"][0] - 2, 1])
        with self.assertRaisesRegex(structure.StructureError, "disagree on their length"):
            structure.read_needs(unequal, grid)

    def test_the_audio_s_length_is_checked(self) -> None:
        needs = needs_of(self.directory, self.audio)
        grid = structure.parameters()["frames_grid"]
        self.assertGreater(structure.read_needs(needs, grid).seconds, 60)  # anchor: the real scalar reads
        for value in (None, "60", True, -1.0, float("nan")):
            bad = json.loads(json.dumps({k: v for k, v in needs.items() if k != "frames/seconds"}))
            bad["frames/seconds"] = {**needs["frames/seconds"], "value": value}
            with self.subTest(value=value), self.assertRaisesRegex(structure.StructureError, "not a length"):
                structure.read_needs(bad, grid)
        series = json.loads(json.dumps(needs))
        series["frames/seconds"] = needs["frames/hpcp"]
        with self.assertRaisesRegex(structure.StructureError, "not a length"):
            structure.read_needs(series, grid)

    def test_a_bad_request_fails_with_a_message(self) -> None:
        directory = scratch(self)
        request = directory / "request.json"
        request.write_text(json.dumps({"parameters": structure.parameters(), "needs": {},
                                       "output_dir": str(directory)}))
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(structure.main([str(request)]), 1)
        self.assertIn("does not give", err.getvalue())
        # A request without its needs, a request that is not there, and no request at all.
        bare = directory / "bare.json"
        bare.write_text(json.dumps({"parameters": structure.parameters()}))
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(structure.main([str(bare)]), 1)
        self.assertIn("structure: 'needs'", err.getvalue())
        bare.unlink()
        with redirect_stderr(io.StringIO()):
            self.assertEqual(structure.main([str(directory / "absent.json")]), 1)
            self.assertEqual(structure.main([]), 2)
        self.assertEqual(sorted(p.name for p in directory.iterdir()), ["request.json"])


@needs_ffmpeg
@needs_essentia
class Cases(unittest.TestCase):
    def test_a_held_chord_over_a_beat_is_no_loop(self) -> None:
        directory = scratch(self)
        drone = measured(directory, song(40, (VERSE[0],), 120, 1), "drone")
        looped = measured(directory, song(40, VERSE, 120, 1), "looped")
        # Anchor: both are one stretch of playing; only the progression loops.
        self.assertEqual(len(drone.items("playing")), 1)
        self.assertEqual(len(looped.items("playing")), 1)
        self.assertEqual(drone.items("loops"), [])
        self.assertEqual(len(looped.items("loops")), 1)

    def test_a_fast_count_in_is_still_clicks_and_clicks_alone_are_not_playing(self) -> None:
        directory = scratch(self)
        fast = measured(directory, numpy.concatenate([room(3, 1), count_in(4, 180, 2), song(20, VERSE, 180, 3),
                                                      room(2, 4)]), "fast")
        playing = fast.items("playing")
        self.assertEqual(len(playing), 1, playing)
        self.assertLess(abs(playing[0]["start"] - (3 + 4 / 3)), 0.2)
        self.assertEqual(labels(fast, "count_ins"), ["clicks=4 interval=0.333"])
        clicks = measured(directory, numpy.concatenate([room(3, 5), count_in(8, 120, 6), room(5, 7)]), "clicks")
        self.assertEqual(clicks.items("playing"), [])
        self.assertEqual(clicks.items("count_ins"), [])
        self.assertEqual(clicks.items("endings"), [])

    def test_a_loud_single_note_riff_is_playing_not_tuning(self) -> None:
        directory = scratch(self)
        riff = numpy.concatenate([support.tune_samples(support.TUNE, RATE, repeats=6) * 1.5, room(3, 1)])
        entry = measured(directory, numpy.concatenate([room(3, 2), riff]), "riff")
        self.assertEqual(entry.items("tuning"), [])
        self.assertEqual(len(entry.items("playing")), 1)

    def test_a_restart_of_another_song_is_a_less_likely_false_start(self) -> None:
        directory = scratch(self)

        def take(second) -> bank.Entry:
            name = "same" if second is SONG_B else "other"
            return measured(directory, numpy.concatenate([room(2, 1), song(9, SONG_B, 140, 2), room(5, 3),
                                                          song(30, second, 140, 4), room(3, 5)]), name)

        same, other = take(SONG_B), take(OTHER)
        self.assertEqual(len(same.items("false_starts")), 1)
        self.assertEqual(len(other.items("false_starts")), 1)
        self.assertGreater(same.items("false_starts")[0]["value"], other.items("false_starts")[0]["value"] + 0.1)

    def test_talk_before_a_song_is_an_unlikely_false_start(self) -> None:
        directory = scratch(self)
        # Talk's stand-in: noise at about a voice's level, its loudness moving at syllable rate.
        rng = numpy.random.default_rng(7)
        t = numpy.arange(12 * RATE) / RATE
        talk = 0.05 * (0.6 + 0.4 * numpy.sin(2 * numpy.pi * 4 * t)) * rng.normal(0, 1, len(t))
        talked = measured(directory, numpy.concatenate([room(2, 1), talk, room(5, 3), song(30, SONG_B, 140, 4),
                                                        room(3, 5)]), "talk")
        restarted = measured(directory, numpy.concatenate([room(2, 1), song(9, SONG_B, 140, 2), room(5, 3),
                                                           song(30, SONG_B, 140, 4), room(3, 5)]), "restart")
        # Anchor: both are short playing followed by more, so both are candidates.
        self.assertEqual(len(talked.items("false_starts")), 1, talked.items("playing"))
        self.assertEqual(len(restarted.items("false_starts")), 1)
        self.assertLess(talked.items("false_starts")[0]["value"], 0.45)
        self.assertGreater(restarted.items("false_starts")[0]["value"], 0.85)

    def test_silence_has_no_structure(self) -> None:
        directory = scratch(self)
        entry = measured(directory, numpy.zeros(20 * RATE), "silence")
        for name in ("playing", "tuning", "count_ins", "false_starts", "endings", "sections", "loops"):
            self.assertEqual(entry.items(name), [], name)
        self.assertTrue(numpy.all(entry.series("novelty") == 0))
        self.assertEqual(entry.scalar("threshold_db"), structure.parameters()["silence_db"])
        self.assertIs(entry.scalar("floor"), False)

    def test_a_master_is_one_stretch_of_playing_whose_quiet_passages_are_music(self) -> None:
        directory = scratch(self)
        bass = (40, 43, 45, 47, 45, 43)
        samples, marks = mastered([
            ("intro", line(bass, 8)),
            ("verse", dry(24, VERSE, 120, 1)),
            ("chorus", dry(16, CHORUS, 120, 2)),
            ("breakdown", line(bass, 8)),
            ("last", dry(16, VERSE, 120, 3)),
            ("ring", held(VERSE[0], 3.0) * numpy.exp(-3 * numpy.arange(3 * RATE) / RATE)),
        ])
        entry = measured(directory, samples, "master")
        level = entry_of(directory, directory / "master.wav", extractor="frames").series("rms_db").reshape(-1)
        p = structure.parameters()
        seconds = numpy.arange(len(level)) * structure.SECONDS
        quiet = (seconds > marks["breakdown"] + 1) & (seconds < marks["last"] - 1)
        # Anchor: the quiet passages are a dozen dB and more under the rest, so a floor at the 5th
        # percentile would have put the breakdown below the threshold and cut the song in two.
        self.assertGreater(numpy.percentile(level, 95) - numpy.median(level[quiet]), 12)
        self.assertGreater(numpy.percentile(level, 5) + p["floor_margin_db"], numpy.median(level[quiet]))
        self.assertFalse(entry.scalar("floor"))
        self.assertEqual(entry.scalar("threshold_db"), round(entry.scalar("reference_db") - p["range_db"], 3))
        playing = entry.items("playing")
        self.assertEqual(len(playing), 1, playing)
        self.assertLess(playing[0]["start"], 0.3)
        self.assertGreater(playing[0]["end"], marks["ring"])
        for name in ("tuning", "count_ins", "false_starts"):
            self.assertEqual(entry.items(name), [], name)
        sections = entry.items("sections")
        self.assertGreaterEqual(len(sections), 3, sections)
        self.assertEqual((sections[0]["start"], sections[-1]["end"]), (playing[0]["start"], playing[0]["end"]))

    def test_clipped_playing_with_no_quiet_runs_to_the_end(self) -> None:
        directory = scratch(self)
        loud = numpy.clip(song(40, VERSE, 120, 1) * 6, -1, 1)
        entry = measured(directory, loud, "clipped")
        playing = entry.items("playing")
        self.assertEqual(len(playing), 1, playing)
        self.assertLess(playing[0]["start"], 0.2)
        self.assertEqual(labels(entry, "endings"), ["abrupt"])
        self.assertTrue(numpy.isfinite(entry.series("novelty")).all())

    def test_a_recording_frames_cannot_measure_blocks_structure(self) -> None:
        directory = scratch(self)
        code, answer = run(directory, support.empty_wav(directory / "empty.wav"))
        self.assertEqual(code, 1)
        self.assertEqual(answer["extractors"]["frames"]["status"], "failed")
        self.assertEqual(answer["extractors"]["structure"]["status"], "blocked")
        self.assertEqual(list((directory / "bank").glob("sha256/*/*/*/bank/structure/*/entry.json")), [])

    def test_settings_it_does_not_implement_are_refused_through_the_runner(self) -> None:
        directory = scratch(self)
        audio = support.write_wav(directory / "short.wav", song(8, VERSE, 120, 1), RATE)
        code, answer = run(directory, audio, {**structure.parameters(), "open_strings": ["E"]})
        self.assertEqual(code, 1)
        self.assertIn("implements open_strings", answer["extractors"]["structure"]["log"])


if __name__ == "__main__":
    unittest.main()
