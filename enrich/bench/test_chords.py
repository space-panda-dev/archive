"""The chord bench on invented charts and synthetic machine outputs: the
reference from taps, its validation, mir_eval scoring over the placed
stretch, missing metrics, the gate, and the command. Needs numpy and
mir_eval (the bench interpreter); no model and no private data.

Run from the repository root:
    python -m unittest discover -s enrich/bench -p "test_chords.py"
"""

from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from enrich.bench import __main__ as cli
from enrich.bench import chords
from enrich.bench.sets import BenchError, sha256_file


def stack(bass, tones, root):
    return {"bass": bass, "tones": tones, "root": root}


# An invented tune: A (four bars) twice, then B (four bars), in C major.
ENTRIES = [stack(0, [4, 7], 0), stack(9, [0, 4], 9), stack(5, [0, 9], 5), stack(7, [2, 11], 7),
           stack(2, [0, 5, 9], 2), stack(7, [2, 5, 11], 7), stack(0, [4, 7, 11], 0), stack(4, [2, 8, 11], 4)]
BARS = [[(0, 0)], [(1, 0)], [(2, 0)], [(3, 0)], [(4, 0), (5, 2)], [(6, 0)], [(7, 0)], []]
CLAIMS = {
    "meter": {"layers": [{"role": "written", "bars": [
        {"numerator": 4, "denominator": 4, "section": 0 if n < 4 else 1,
         **({"chords": [{"entry": e, **({"at": {"count": at, "unit": 4}} if at else {})} for e, at in bar]}
            if bar else {})} for n, bar in enumerate(BARS)]}]},
    "form": {"sections": [{"label": "A"}, {"label": "B"}], "repeats": [{"first": 0, "last": 0, "times": 2}]},
    "sequence": {"entries": [{"readings": [r]} for r in ENTRIES]},
    "key": {"regions": [{"readings": [{"collection": [0, 2, 4, 5, 7, 9, 11], "tonic": 0, "mode": "major"}]}]},
}
DIGEST_A, DIGEST_B = "sha256:" + "a" * 64, "sha256:" + "b" * 64
TAPS = [10.0, 18.0, 26.0, 34.0]   # A, A, B: two seconds a bar


def reference(**changes):
    made = chords.reference_from_taps(CLAIMS, TAPS, recording_sha256=DIGEST_A, chart_sha256=DIGEST_B)
    made.update(changes)
    return made


def perfect(ref):
    """A machine that heard exactly the reference, with a little either side."""
    return {"chords": [[0.0, ref["stretch"][0], "N"], *ref["chords"], [ref["stretch"][1], 40.0, "N"]],
            "numerator": ref["numerator"], "downbeats": [2.0, *ref["downbeats"]],
            "keys": [[0.0, 40.0, [0, 2, 4, 5, 7, 9, 11], None, None]],
            "sections": [[0.0, 10.0, "intro"], *[[s, e, f"x{label}"] for s, e, label in ref["sections"]]]}


class Reference(unittest.TestCase):
    def test_taps_divide_each_section_by_its_beats(self) -> None:
        ref = reference()
        chords.parse_reference(ref)
        self.assertEqual(ref["stretch"], [10.0, 34.0])
        self.assertEqual([s[2] for s in ref["sections"]], ["A", "A", "B"])
        self.assertEqual(ref["downbeats"], [10.0 + 2 * n for n in range(12)])
        self.assertEqual(ref["numerator"], 4)
        self.assertEqual(ref["key"], "C major")
        names = [c[2] for c in ref["chords"]]
        self.assertEqual(names[:4], ["C:maj", "A:min", "F:maj", "G:maj"])
        self.assertEqual(names[8:], ["D:min7", "G:7", "C:maj7", "E:7"])
        # G7 enters on beat three; E7 is held through the last bar.
        self.assertEqual(ref["chords"][9][0], 27.0)
        self.assertEqual((ref["chords"][11][0], ref["chords"][11][1]), (30.0, 34.0))

    def test_a_transposed_placement_names_the_transposed_chords(self) -> None:
        ref = chords.reference_from_taps(CLAIMS, TAPS, recording_sha256=DIGEST_A, chart_sha256=DIGEST_B,
                                         transposition=3)
        self.assertEqual(ref["chords"][0][2], "Eb:maj")
        self.assertEqual(ref["key"], "Eb major")

    def test_every_problem_is_reported(self) -> None:
        broken = reference(placement="guess", transposition=12)
        broken["chords"][1][0] += 0.5
        with self.assertRaises(BenchError) as caught:
            chords.parse_reference(broken)
        for words in ("placement", "transposition", "gap"):
            self.assertIn(words, str(caught.exception))


class Scoring(unittest.TestCase):
    def test_a_machine_that_hears_the_chart_scores_one(self) -> None:
        ref = reference()
        scores = chords.score_item(ref, perfect(ref))
        for metric in (*chords.CHORD_METRICS, "key", "key_collection", "meter", "downbeat_f", "boundary_f_0.5",
                       "boundary_f_3", "pairwise_f"):
            self.assertAlmostEqual(scores[metric], 1.0, msg=metric)

    def test_triads_for_sevenths_lose_only_the_sevenths(self) -> None:
        ref = reference()
        estimate = perfect(ref)
        estimate["chords"] = [[s, e, n.replace("min7", "min").replace(":maj7", ":maj").replace(":7", ":maj")]
                              for s, e, n in estimate["chords"]]
        scores = chords.score_item(ref, estimate)
        self.assertAlmostEqual(scores["root"], 1.0)
        self.assertAlmostEqual(scores["majmin"], 1.0)
        self.assertLess(scores["sevenths"], 1.0)

    def test_outside_the_stretch_nothing_counts(self) -> None:
        ref = reference()
        estimate = perfect(ref)
        estimate["chords"][0][2] = "F#:dim"
        estimate["chords"][-1][2] = "F#:dim"
        self.assertAlmostEqual(chords.score_item(ref, estimate)["mirex"], 1.0)

    def test_a_relative_key_and_a_wrong_meter(self) -> None:
        ref = reference(key="A minor")
        estimate = perfect(ref)
        estimate["numerator"] = 3
        scores = chords.score_item(ref, estimate)
        self.assertAlmostEqual(scores["key"], 0.3)       # read as C major, the relative key
        self.assertEqual(scores["key_collection"], 1.0)
        self.assertEqual(scores["meter"], 0.0)

    def test_a_chord_blind_reference_scores_no_downbeats(self) -> None:
        ref = reference(placement="chord-blind")
        self.assertIsNone(chords.score_item(ref, perfect(ref))["downbeat_f"])

    def test_what_the_machine_lacks_is_absent_not_zero(self) -> None:
        ref = reference()
        scores = chords.score_item(ref, {"chords": perfect(ref)["chords"]})
        self.assertEqual([scores[m] for m in ("key", "meter", "downbeat_f", "pairwise_f")], [None] * 4)
        with self.assertRaisesRegex(BenchError, "no chords"):
            chords.score_item(ref, {"chords": []})


class Fake:
    name, version = "fake", "1"

    def __init__(self, edit=lambda e: e):
        self.edit = edit

    def parameters(self):
        return {}

    def provenance(self):
        return {}

    def estimate(self, item, audio, work):
        ref = json.loads((Path(audio).parent / f"{item.label}.json").read_text())
        return self.edit(perfect(ref))


class Set(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "sets").mkdir()
        data = self.root / "data"
        data.mkdir()
        items = []
        for n, (label, subset) in enumerate((("a", "studio"), ("b", "rehearsal"))):
            audio, chart = data / f"{label}.flac", data / f"{label}.irealb"
            audio.write_bytes(b"fLaC" + bytes([n]) * 10)
            chart.write_text(f"irealb://Invented{n}")
            ref = chords.reference_from_taps(CLAIMS, TAPS, recording_sha256=sha256_file(audio),
                                             chart_sha256=sha256_file(chart))
            (data / f"{label}.json").write_text(json.dumps(ref))
            items.append({"id": sha256_file(audio), "label": label, "subset": subset,
                          "recording": {"path": f"data/{label}.flac", "sha256": sha256_file(audio), "seconds": 40.0},
                          "chart": {"path": f"data/{label}.irealb", "sha256": sha256_file(chart)},
                          "reference": {"path": f"data/{label}.json", "sha256": sha256_file(data / f"{label}.json")}})
        self.manifest = {"contract": chords.SET_CONTRACT, "set": "tiny-chords", "task": "chords", "items": items}
        (self.root / "sets" / "tiny-chords.json").write_text(json.dumps(self.manifest))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def loaded(self):
        return chords.parse_set("tiny-chords", self.manifest)

    def test_the_manifest_reports_every_problem(self) -> None:
        broken = copy.deepcopy(self.manifest)
        broken["task"] = "notes"
        broken["items"][1]["id"] = broken["items"][0]["id"]
        del broken["items"][0]["recording"]["seconds"]
        with self.assertRaises(BenchError) as caught:
            chords.parse_set("tiny-chords", broken)
        for words in ("task", "listed twice", "seconds"):
            self.assertIn(words, str(caught.exception))

    def test_a_reference_of_another_recording_is_refused(self) -> None:
        item = self.loaded().items[0]
        with self.assertRaisesRegex(BenchError, "item's recording"):
            chords.parse_reference(reference(), item)

    def test_the_gate_fails_a_drop_over_the_tolerance(self) -> None:
        quiet = lambda *_: None
        base = chords.run_set(self.root, self.loaded(), Fake(), log=quiet)
        self.assertEqual(base["means"]["all"]["mirex"], 1.0)
        self.assertEqual(chords.compare(base, base), [])

        def worse(estimate):
            estimate["chords"] = [[s, e, "N" if n == "C:maj" else n] for s, e, n in estimate["chords"]]
            return estimate
        now = chords.run_set(self.root, self.loaded(), Fake(worse), log=quiet)
        failures = chords.compare(now, base)
        self.assertTrue(any("mirex" in f for f in failures), failures)

        def keyless(estimate):
            estimate.pop("keys")
            return estimate
        with self.assertRaisesRegex(BenchError, "baseline has it"):
            chords.compare(chords.run_set(self.root, self.loaded(), Fake(keyless), log=quiet), base)

    def test_a_changed_reference_stops_the_run(self) -> None:
        (self.root / "data" / "b.json").write_text("{}")
        with self.assertRaisesRegex(BenchError, "b.json has changed"):
            chords.run_set(self.root, self.loaded(), Fake(), log=lambda *_: None)

    def test_the_command_validates_and_writes_a_reference(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(["--root", str(self.root), "chords", "validate", "tiny-chords"]), 0)
        self.assertIn("2 items", out.getvalue())
        claims, taps = self.root / "claims.json", self.root / "taps.txt"
        claims.write_text(json.dumps(CLAIMS))
        taps.write_text("\n".join(map(str, TAPS)))
        with redirect_stdout(io.StringIO()):
            code = cli.main(["chords", "reference", "--claims", str(claims), "--taps", str(taps),
                             "--recording", str(self.root / "data" / "a.flac"),
                             "--chart", str(self.root / "data" / "a.irealb"), "--out", str(self.root / "r.json")])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((self.root / "r.json").read_text()),
                         json.loads((self.root / "data" / "a.json").read_text()))
