"""A chart placed on a recording (`enrich align`, ADR 0116 section 6,
proposed): the method on synthetic beats and chords, and the command end to
end through the real Door. Every chart and progression here is invented."""

from __future__ import annotations

import json
import random
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "enrich"))
sys.path.insert(0, str(ROOT))

import adapter  # noqa: E402
import align  # noqa: E402
import support  # noqa: E402
from support import needs_door  # noqa: E402
from world import form as world_form  # noqa: E402
from world import harmony  # noqa: E402

RATE = 8000
BEAT = 4000   # half a second


def stack(bass: int, tones: list[int], root: int | None = None, text: str | None = None) -> dict:
    reading = {"bass": bass, "tones": sorted(tones)}
    if root is not None:
        reading["root"] = root
    if text:
        reading["text"] = text
    return reading


# An invented tune: section A four bars, played twice; section B four bars,
# its first bar with a second chord on beat three, its last bar held.
ENTRIES = [stack(0, [4, 7], 0, "C"), stack(9, [0, 4], 9, "Am"), stack(5, [9, 0], 5, "F"),
           stack(7, [11, 2], 7, "G"), stack(2, [5, 9, 0], 2, "Dm7"), stack(7, [11, 2, 5], 7, "G7"),
           stack(0, [4, 7, 11], 0, "Cmaj7"), stack(4, [8, 11, 2], 4, "E7")]
BARS = [[(0, 0)], [(1, 0)], [(2, 0)], [(3, 0)], [(4, 0), (5, 2)], [(6, 0)], [(7, 0)], []]


def chart_payloads(pickup: bool = False) -> tuple[dict, dict, dict]:
    """(meter, form, sequence) payloads of the invented tune; with `pickup`,
    a one-beat bar of the first chord as a section of its own before A."""
    shift = 1 if pickup else 0
    bars = [{"numerator": 1, "denominator": 4, "section": 0, "chords": [{"entry": 0}]}] if pickup else []
    for index, chords in enumerate(BARS):
        bar = {"numerator": 4, "denominator": 4, "section": shift + (0 if index < 4 else 1)}
        if chords:
            bar["chords"] = [{"entry": e, **({"at": {"count": at, "unit": 4}} if at else {})} for e, at in chords]
        bars.append(bar)
    meter = {"layers": [{"role": "written", "bars": bars}]}
    sections = [{"label": "pickup"}] * shift + [{"label": "A"}, {"label": "B"}]
    form = {"sections": sections, "repeats": [{"first": shift, "last": shift, "times": 2}]}
    sequence = {"entries": [{"readings": [r]} for r in ENTRIES]}
    return meter, form, sequence


def played_entries(pickup: bool = False) -> list[int]:
    """The chart's chord per beat over one pass (A A B), as written."""
    beats, current = [], None
    order = ([0, 1, 2, 3] * 2) + [4, 5, 6, 7]
    for bar in order:
        changes = dict((at, e) for e, at in BARS[bar])
        for beat in range(4):
            current = changes.get(beat, current)
            beats.append(current)
    return ([0] if pickup else []) + beats


def machine_payloads(entries: list[int | None], transpose: int, intro: int, tail: int, *, seed: int = 1,
                     errors: float = 0.0, downbeat_every: int | None = 4, drop_beat: int | None = None,
                     first_offset: int = 0) -> tuple[dict, dict, int]:
    """A machine's (meter, chords, sample count) for a recording of `intro`
    beats of other chords, then `entries` (one per beat) transposed, then
    `tail` beats. `errors` is the share of beats given a wrong chord."""
    rng = random.Random(seed)
    per_beat: list[dict | None] = []
    for _ in range(intro):
        per_beat.append(stack(rng.randrange(12), [rng.randrange(12)]) if rng.random() < 0.5 else None)
    for entry in entries:
        if rng.random() < errors:
            per_beat.append(stack(rng.randrange(12), [rng.randrange(12)]))
            continue
        reading = ENTRIES[entry]
        per_beat.append({"bass": (reading["bass"] + transpose) % 12,
                         "tones": sorted((pc + transpose) % 12 for pc in reading["tones"])})
    for _ in range(tail):
        per_beat.append(None)
    count = len(per_beat)
    beats = [first_offset + n * BEAT for n in range(count)]
    if drop_beat is not None:
        del beats[drop_beat]
    downbeats = [] if downbeat_every is None else [beats[n] for n in range(intro, len(beats) - tail, downbeat_every)
                                                   if n < len(beats)]
    layer = {"beats": beats}
    if downbeats:
        layer["downbeats"] = downbeats
    spans = []
    for n, reading in enumerate(per_beat):
        start, end = first_offset + n * BEAT, first_offset + (n + 1) * BEAT
        spans.append({"start_sample": start, "end_sample": end,
                      "readings": [reading if reading is not None else {"no_chord": True}]})
    sample_count = first_offset + count * BEAT + BEAT
    return {"sample_rate": RATE, "layers": [layer]}, {"sample_rate": RATE, "spans": spans}, sample_count


class Method(unittest.TestCase):
    def place(self, machine, pickup=False):
        meter, form, sequence = chart_payloads(pickup)
        chart = align.chart_of(meter, form, sequence)
        return chart, align.place(chart, align.machine_of(*machine))

    def test_two_choruses_transposed_after_an_intro_are_placed(self) -> None:
        chart, found = self.place(machine_payloads(played_entries() * 2, transpose=2, intro=6, tail=5, errors=0.1))
        self.assertIsInstance(found, align.Placement, getattr(found, "detail", ""))
        self.assertEqual(found.transposition, 2)
        self.assertEqual(len(found.bars), 24)
        self.assertEqual(found.bars[0].beat, 6)
        self.assertEqual(found.passes, 2)
        self.assertEqual([chart.bars[b.played].bar for b in found.bars[:12]], [0, 1, 2, 3] * 2 + [4, 5, 6, 7])
        self.assertTrue(all(b.beats == 4 for b in found.bars))

    def test_a_dropped_beat_shortens_one_bar(self) -> None:
        chart, found = self.place(machine_payloads(played_entries() * 2, transpose=0, intro=4, tail=4,
                                                   drop_beat=4 + 21, downbeat_every=None))
        self.assertIsInstance(found, align.Placement, getattr(found, "detail", ""))
        self.assertEqual(sorted(b.beats for b in found.bars).count(3), 1)
        self.assertEqual(len(found.bars), 24)

    def test_a_pickup_is_placed_before_the_first_bar(self) -> None:
        chart, found = self.place(machine_payloads(played_entries(True), transpose=0, intro=3, tail=2,
                                                   downbeat_every=None), pickup=True)
        self.assertIsInstance(found, align.Placement, getattr(found, "detail", ""))
        self.assertEqual((found.bars[0].played, found.bars[0].beat, found.bars[0].beats), (0, 3, 1))

    def test_unrelated_chords_are_refused_loudly(self) -> None:
        rng = random.Random(5)
        noise = [rng.randrange(len(ENTRIES)) for _ in range(96)]
        _, found = self.place(machine_payloads(noise, transpose=0, intro=0, tail=0, errors=0.6, seed=3))
        self.assertIsInstance(found, align.Refused)
        self.assertIn("mean_cost", found.numbers)

    def test_no_chord_everywhere_cannot_choose_a_transposition(self) -> None:
        _, found = self.place(machine_payloads([], transpose=0, intro=0, tail=96))
        self.assertIsInstance(found, align.Refused)

    def test_a_bar_the_rule_cannot_count_is_refused(self) -> None:
        meter, form, sequence = chart_payloads()
        meter["layers"][0]["bars"][0].update(numerator=7, denominator=8)
        with self.assertRaisesRegex(align.AlignError, "7/8"):
            align.chart_of(meter, form, sequence)

    def test_readings_move_with_the_transposition_and_lose_their_text(self) -> None:
        reading = {**ENTRIES[7], "uncertain": [{"pitch_classes": [1, 3], "present": "maybe"}]}
        self.assertEqual(align.transpose_reading(reading, 3),
                         {"bass": 7, "root": 7, "tones": [2, 5, 11],
                          "uncertain": [{"pitch_classes": [4, 6], "present": "maybe"}]})
        self.assertIsNone(align.transpose_reading({"text": "Fsus*"}, 3))
        self.assertEqual(align.transpose_reading({"text": "Fsus*"}, 0), {"text": "Fsus*"})

    def test_the_payloads_pass_worlds_checks(self) -> None:
        meter, form, sequence = chart_payloads()
        machine = align.machine_of(*machine_payloads(played_entries() * 2, transpose=5, intro=4, tail=3))
        chart = align.chart_of(meter, form, sequence)
        found = align.place(chart, machine)
        made = align.payloads(chart, meter, machine, found)
        ids = iter(f"urn:uuid:00000000-0000-4000-8000-{n:012d}" for n in range(1, 99))
        rev = lambda n: "sha256:" + f"{n:x}" * 64
        doc = {"role": "document", "id": next(ids), "revision_id": rev(1)}
        form_claim = {"payload": form, "refs": [doc], "revision_id": rev(2)}
        sequence_claim = {"payload": sequence, "refs": [doc], "revision_id": rev(3)}
        untimed = {"payload": meter, "revision_id": rev(4),
                   "refs": [doc, {"role": "form", "id": next(ids), "revision_id": rev(2)},
                            {"role": "sequence", "id": next(ids), "revision_id": rev(3)}]}
        source = {"role": "source", "id": next(ids), "revision_id": rev(5)}
        stream_claim = {"revision_id": rev(6), "refs": [{"role": "artifact", "id": source["id"],
                                                         "revision_id": rev(5)}],
                        "payload": {"stream_index": 0, "codec": "pcm_s16le", "sample_rate": RATE,
                                    "channel_count": 1, "sample_count": machine.sample_count}}
        common = [source, {"role": "stream", "id": next(ids), "revision_id": rev(6)}]
        world_form.check_meter_layers_v2(
            {"payload": made["meter"], "refs": common + [{"role": "meter", "id": next(ids), "revision_id": rev(4)}]},
            stream_claim, untimed_claim=untimed)
        spans = harmony.check_chord_stacks_v2(
            {"payload": made["chords"], "refs": common + [{"role": "sequence", "id": next(ids),
                                                           "revision_id": rev(3)}]},
            stream_claim, sequence_claim)
        sections = world_form.check_form_spans(
            {"payload": made["form"], "refs": common + [{"role": "form", "id": next(ids), "revision_id": rev(2)}]},
            stream_claim, form_claim)
        # Two choruses of A A B: six section spans, and every chart chord once per pass.
        self.assertEqual([s["section"] for s in sections], [0, 0, 1] * 2)
        self.assertEqual(spans[0]["readings"], [{"bass": 5, "root": 5, "tones": [0, 9]}])
        self.assertEqual(spans[0]["start_sample"], 4 * BEAT)
        self.assertEqual(len(spans), 2 * (4 + 4 + 4))
        b_first = [s for s in spans if s["entry"] == 5][0]
        self.assertEqual(b_first["start_sample"] - [s for s in spans if s["entry"] == 4][0]["start_sample"],
                         2 * BEAT)
        self.assertEqual(spans[11]["end_sample"] - spans[11]["start_sample"], 2 * 4 * BEAT)  # E7 held a bar
        self.assertTrue(all(s["end"] == "held" for s in spans))


def claim_record(key: str, contract: tuple[str, str], payload: dict, refs: list[dict]) -> dict:
    return {"key": key, "namespace": contract[0], "name": f"test-{key}", "shape": "claim",
            "contracts": [{"id": contract[0], "version": contract[1]}], "payload": payload, "refs": refs}


@needs_door
class Command(unittest.TestCase):
    """The chart's claims and the machine's are admitted through Door; `enrich
    align` places the chart and Door admits the placement."""

    def build(self, errors: float) -> None:
        self.tmp = support.scratch(self, "align-")
        self.env = support.environment()
        meter, form, sequence = chart_payloads()
        machine_meter, machine_chords, count = machine_payloads(played_entries() * 2, transpose=2, intro=6,
                                                                tail=4, errors=errors, seed=4)
        wav = support.sine_wav(self.tmp / "take.wav", RATE, str(count / RATE))
        chart = self.tmp / "chart.txt"
        chart.write_text("irealb://Invented%20Tune=Nobody==Medium%20Swing=C==1r34LbKcu7\n")
        doc = [{"role": "document", "pin": "chart"}]
        steps = ({"name": "chart", "command": "admit", "records": [
                     claim_record("form", ("archive.claim/form", "1"), form, doc),
                     claim_record("sequence", ("archive.claim/chord_sequence", "2"), sequence, doc)]},
                 {"name": "meter", "command": "admit", "records": [
                     claim_record("meter", ("archive.claim/meter_layers", "2"), meter,
                                  doc + [{"role": "form", "pin": "form"}, {"role": "sequence", "pin": "sequence"}])]})
        self.root, manifest = support.build_archive(self, {"mix": wav, "chart": chart}, steps)
        self.objects = manifest["objects"]
        code, answer = self.enrich("probe", "--artifact", self.objects["mix"]["object_id"],
                                   "--ffprobe", support.FFPROBE)
        self.assertEqual(code, 0, answer)
        reader = adapter.ArchiveReader(self.root, support.ARCHIVE, self.env)
        mix = reader.head(self.objects["mix"]["object_id"])
        [stream] = [r for r in reader.records if r["shape"] == "claim" and any(
            c["id"] == "archive.claim/audio_stream" for c in r.get("contracts", []))]
        self.assertEqual(stream["payload"]["sample_count"], count)
        pins = (adapter.Pin("source", mix["object_id"], mix["revision_id"]),
                adapter.Pin("stream", stream["object_id"], stream["revision_id"]))
        result = adapter.RunResult(
            agent="pkg:generic/test-machine@1", method_id="test-machine", method_version="1",
            software=(("test-machine", "1"),), started_at=adapter.now(), ended_at=adapter.now(),
            used=(adapter.Pin("used", mix["object_id"], mix["revision_id"]),),
            outputs=(adapter.Output(("archive.claim/meter_layers", "2"), "beats", machine_meter, pins),
                     adapter.Output(("archive.claim/chord_stacks", "2"), "chords", machine_chords, pins)))
        adapter.propose(reader, result, door=support.DOOR, staging=None, dry_run=False, env=self.env)

    def enrich(self, command: str, *extra: str) -> tuple[int, dict]:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "enrich" / "enrich.py"), command, "--archive", str(self.root),
             "--door", support.DOOR, "--archive-command", support.ARCHIVE, "--staging", str(self.tmp / "staging"),
             *extra],
            capture_output=True, text=True, env=self.env)
        return completed.returncode, json.loads(completed.stdout or completed.stderr)

    def align(self) -> tuple[int, dict]:
        return self.enrich("align", "--artifact", self.objects["mix"]["object_id"],
                           "--meter", self.objects["meter"]["object_id"])

    def test_door_admits_the_placement_and_a_rerun_is_unchanged(self) -> None:
        self.build(errors=0.05)
        code, answer = self.align()
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        self.assertEqual((answer["transposition"], answer["placed_bars"], answer["passes"]), (2, 24, 2))
        records = support.export(self.root)["records"]
        [run] = [r for r in records if r["revision_id"] == answer["run"]["revision_id"]]
        self.assertEqual(run["payload"]["method"], {"id": "chart-placement", "version": "1"})
        made = [r for r in records if any(ref["role"] == "run" and ref["revision_id"] == run["revision_id"]
                                          for ref in r["refs"])]
        self.assertEqual(sorted(r["contracts"][0]["id"] for r in made),
                         ["archive.claim/chord_stacks", "archive.claim/form_spans", "archive.claim/meter_layers"])
        meter = next(r for r in made if r["contracts"][0]["id"] == "archive.claim/meter_layers")
        self.assertEqual(sorted(ref["role"] for ref in meter["refs"]),
                         ["derived_from", "derived_from", "meter", "run", "source", "stream"])
        used = {ref["id"] for ref in run["refs"] if ref["role"] == "used"}
        for key in ("mix", "meter", "form", "sequence"):
            self.assertIn(self.objects[key]["object_id"], used)
        code, again = self.align()
        self.assertEqual((code, again["status"]), (0, "unchanged"), again)

    def test_a_chart_the_machine_does_not_hear_proposes_nothing(self) -> None:
        self.build(errors=0.7)
        before = support.head(self.root)
        code, answer = self.align()
        self.assertEqual((code, answer["status"], answer["code"]), (3, "nothing_proposed", "low_confidence"), answer)
        self.assertIn("mean_cost", answer)
        self.assertEqual(support.head(self.root), before)
