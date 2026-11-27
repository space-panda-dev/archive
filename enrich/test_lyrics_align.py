"""A lyric document forced onto a vocal stem (`enrich align-lyrics`, ADR 0085
amended 2026-10-19 and 2026-11-27): the aligner's text
and syllable rules without its model, the claim the command builds against
World's checks, and the command end to end through the real Door with a stem
from a stand-in separator and a stand-in aligner program that runs the real
program's assembly on made-up token spans. Every word here is invented."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import adapter
import bank
import lyrics_align
import support
from support import needs_door
from test_stems import fake_python, run as run_stems

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE / "aligners" / "align-wav2vec2"))

import align_wav2vec2 as program  # noqa: E402
from world import documents  # noqa: E402

LABELS = ("-", "|", "E", "T", "A", "O", "N", "I", "H", "S", "R", "D", "L", "U", "M", "W", "C", "F", "G", "Y",
          "P", "B", "V", "K", "'", "X", "J", "Q", "Z")
DOCUMENT = "Lumovar tak brindle\n\n— kasimir 10 ’vensa!\nzo\n"
RATE = 44100

# A stand-in for align-python: the real program's tokens and assembly, with
# each token placed FAKE_ALIGN_STEP frames after the one before, and every
# letter's probability FAKE_ALIGN_SCORE. It checks its weights directory and
# request as the real one does.
FAKE = r'''#!{python}
import json, os, sys
sys.path.insert(0, {program_dir!r})
import align_wav2vec2 as a
weights, request = sys.argv[2], json.load(open(sys.argv[3]))  # [interpreter, program, weights, request]
assert os.path.isdir(weights), weights
assert request["parameters"] == a.parameters()
lines = json.load(open(request["document"]["path"]))["lines"]
labels = {{label: index for index, label in enumerate({labels!r})}}
sequence, owners = a.tokens(lines, labels, labels["|"], len(labels))
step, score = int(os.environ.get("FAKE_ALIGN_STEP", "6")), float(os.environ.get("FAKE_ALIGN_SCORE", "0.6"))
spans = [(25 + step * i, 25 + step * i + 2, score) for i in range(len(sequence))]
alignment = a.assemble(lines, owners, spans, None)
out = request["output_dir"]
open(os.path.join(out, "alignment.json"), "w").write(json.dumps(alignment))
json.dump({{"contract": "archive-extractor-result/1", "software": [{{"name": "fake-aligner", "version": "1"}}],
           "grids": {{}}, "outputs": {{"alignment": {{}}, "mean_score": {{"value": alignment["mean_score"]}},
                                     "active_seconds": {{"value": "6.000"}}}}}}, open(os.path.join(out, "result.json"), "w"))
'''


def fake_aligner(directory: Path) -> Path:
    path = directory / "fake-align-python"
    path.write_text(FAKE.format(python=sys.executable, program_dir=str(HERE / "aligners" / "align-wav2vec2"),
                                labels=LABELS))
    path.chmod(0o755)
    return path


def lines_of(text: str) -> list[list[str]]:
    return documents.document_lines(text.encode("utf-8"), "text")


def fake_alignment(text: str = DOCUMENT, step: int = 6) -> dict:
    lines = lines_of(text)
    labels = {label: index for index, label in enumerate(LABELS)}
    sequence, owners = program.tokens(lines, labels, labels["|"], len(labels))
    spans = [(25 + step * i, 25 + step * i + 2, 0.6) for i in range(len(sequence))]
    return program.assemble(lines, owners, spans, None)


class AlignerText(unittest.TestCase):
    """The program's rules that need no model."""

    def test_syllables_cover_the_word_by_code_points(self) -> None:
        cases = {"lumovar": ["lu", "mo", "var"], "brindle": ["brin", "dle"], "tak": ["tak"],
                 "kasimir,": ["ka", "si", "mir,"], "zobes": ["zobes"], "voxes": ["vo", "xes"],
                 "10": ["10"], "—": [], "ﬁnaro": ["ﬁ", "na", "ro"]}
        for word, parts in cases.items():
            with self.subTest(word=word):
                found = program.syllables_of(word, None)
                self.assertEqual([word[a:b] for a, b in found], parts)
                if found:
                    self.assertEqual((found[0][0], found[-1][1]), (0, len(word)))

    def test_hyphenation_cuts_are_moved_to_code_point_edges(self) -> None:
        # "ﬁ" is one code point of two letters: a cut between them moves after it.
        found = program.syllables_of("ﬁnaro", lambda letters: [1, 4])
        self.assertEqual(found, [(0, 1), (1, 3), (3, 5)])

    def test_letters_digits_and_apostrophes(self) -> None:
        self.assertEqual("".join(l for l, _ in program.spelled("’Vensa!")), "vensa")
        self.assertEqual("".join(l for l, _ in program.spelled("Zoë's")), "zoe's")
        self.assertEqual({i for _, i in program.spelled("(10)")}, {1})
        self.assertEqual(program.spelled("—"), [])

    def test_the_token_sequence_has_a_star_at_each_line_edge_and_bars_between_words(self) -> None:
        labels = {label: index for index, label in enumerate(LABELS)}
        sequence, owners = program.tokens([["ab", "—", "c"], [], ["d"]], labels, 1, 29)
        self.assertEqual(sequence, [29, labels["A"], labels["B"], 1, labels["C"], 29, labels["D"], 29])
        self.assertEqual(owners[1:3], [(0, 0, 0), (0, 0, 1)])
        self.assertEqual(owners[4], (0, 2, 0))

    def test_assembled_words_and_syllables_are_ordered_and_never_hold_text(self) -> None:
        alignment = fake_alignment()
        self.assertEqual([line["line"] for line in alignment["lines"]], [1, 3, 4])
        self.assertEqual([w["word"] for w in alignment["lines"][1]["words"]], [2, 3, 4])  # "—" has no letters
        for line in alignment["lines"]:
            ends = 0
            for word in line["words"]:
                self.assertGreaterEqual(word["start"], ends)
                self.assertLess(word["start"], word["end"])
                ends = word["end"]
                for syllable in word.get("syllables", []):
                    self.assertLessEqual(word["start"], syllable["start"])
                    self.assertLess(syllable["start"], syllable["end"])
        for word in DOCUMENT.split():
            self.assertNotIn(word, json.dumps(alignment, ensure_ascii=False))

    def test_the_manifest_declares_the_programs_settings_and_stays_out_of_the_catalog(self) -> None:
        manifest = json.loads((HERE / "aligners" / "align-wav2vec2" / "extractor.json").read_text())
        bank.check_manifest(manifest, "align-wav2vec2")
        self.assertEqual(manifest["parameters"], program.parameters())
        self.assertIn("${align-weights}", manifest["command"])
        self.assertNotIn("align-wav2vec2", bank.Catalog.load().extractors)
        aligner = lyrics_align.load_aligner(HERE / "aligners", "align-wav2vec2")
        self.assertEqual(aligner.manifest["parameters"]["language"], "en")
        # Threads change the floats, so they are a setting the program uses, not the environment's.
        self.assertEqual(aligner.manifest["parameters"]["threads"], program.THREADS)
        # No weights are kept beside the program: the operator's directory holds them.
        self.assertEqual(sorted(p.name for p in (HERE / "aligners" / "align-wav2vec2").iterdir()
                                if p.name != "__pycache__"), ["align_wav2vec2.py", "extractor.json"])

    def test_an_aligner_that_does_not_state_its_threads_is_refused(self) -> None:
        directory = support.scratch(self, "aligner-") / "align-wav2vec2"
        shutil.copytree(HERE / "aligners" / "align-wav2vec2", directory,
                        ignore=shutil.ignore_patterns("__pycache__"))
        manifest = json.loads((directory / "extractor.json").read_text())
        del manifest["parameters"]["threads"]
        (directory / "extractor.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(lyrics_align.AlignLyricsError, "threads"):
            lyrics_align.load_aligner(directory.parent, "align-wav2vec2")


SOURCE = {"id": "urn:uuid:00000000-0000-4000-8000-000000000001", "revision_id": "sha256:" + "1" * 64}
STREAM = {"id": "urn:uuid:00000000-0000-4000-8000-000000000002", "revision_id": "sha256:" + "2" * 64}
DOC = {"id": "urn:uuid:00000000-0000-4000-8000-000000000003", "revision_id": "sha256:" + "3" * 64}
NOTE = {"id": "urn:uuid:00000000-0000-4000-8000-000000000004", "revision_id": "sha256:" + "4" * 64}


def targets(onsets: list[int], count: int = RATE * 8) -> dict:
    notes = [{"onset_sample": o, "offset_sample": o + 1000, "pitch": 60} for o in onsets]
    return {SOURCE["revision_id"]: {"id": SOURCE["id"], "shape": "artifact"},
            DOC["revision_id"]: {"id": DOC["id"], "shape": "artifact"},
            STREAM["revision_id"]: {"id": STREAM["id"], "shape": "claim", "contract": ("archive.claim/audio_stream", "1"),
                                    "payload": {"sample_rate": RATE, "sample_count": count},
                                    "refs": [{"role": "artifact", **SOURCE}]},
            NOTE["revision_id"]: {"id": NOTE["id"], "shape": "claim", "contract": ("archive.claim/notes", "1"),
                                  "payload": {"sample_rate": RATE, "part": "vocals", "notes": notes},
                                  "refs": [{"role": "source", **SOURCE}, {"role": "stream", **STREAM}]}}


def refs(with_notes: bool) -> list[dict]:
    pins = [{"role": "document", **DOC}, {"role": "source", **SOURCE}, {"role": "stream", **STREAM}]
    return pins + ([{"role": "notes", **NOTE}] if with_notes else [])


class Claim(unittest.TestCase):
    """The payload the command proposes, held to World's own checks."""

    def payload(self, count: int = RATE * 8) -> dict:
        return lyrics_align.timed_payload(fake_alignment(), lines_of(DOCUMENT), RATE, count, "test scale", "text")

    def test_the_payload_passes_check_timed_lyrics_and_check_locators(self) -> None:
        payload = self.payload()
        self.assertEqual(payload["alignment"], {"method": "forced alignment"})
        claim = {"payload": payload, "refs": refs(False)}
        documents.check_timed_lyrics(claim, targets([]))
        documents.check_locators(claim, DOCUMENT.encode("utf-8"))
        view = documents.lyric_view(claim, DOCUMENT.encode("utf-8"))
        self.assertEqual([s["text"] for s in view[0]["words"][0]["syllables"]], ["Lu", "mo", "var"])

    def test_samples_are_placed_on_the_stream_exactly(self) -> None:
        payload = self.payload()
        first = fake_alignment()["lines"][0]["words"][0]
        word = payload["lines"][0]["words"][0]
        self.assertEqual(word["start_sample"], round(first["start"] * RATE / 16000))

    def test_carried_by_follows_the_rule_and_world_finds_every_onset(self) -> None:
        payload = self.payload()
        syllables = sorted((s for l in payload["lines"] for w in l["words"] for s in w.get("syllables", [])),
                           key=lambda s: s["start_sample"])
        lead = RATE // 10
        first, second = syllables[0]["start_sample"], syllables[1]["start_sample"]
        # Two notes on the first syllable (one just inside its lead), one just
        # before the second syllable's lead (still the first's), one far
        # before anything (no syllable's).
        onsets = [first - lead, first + 10, second - lead - 1, 5]
        counts = lyrics_align.carry(payload, onsets, NOTE["revision_id"])
        self.assertEqual(syllables[0]["carried_by"], {"notes": NOTE["revision_id"],
                                                      "onsets": [first - lead, first + 10, second - lead - 1]})
        self.assertNotIn("carried_by", syllables[1])
        self.assertEqual((counts["carried"], counts["melisma"]), (1, 1))
        claim = {"payload": payload, "refs": refs(True)}
        documents.check_timed_lyrics(claim, targets(onsets))
        with self.assertRaises(documents.DocumentError):   # an onset that is no note of the claim
            documents.check_timed_lyrics(claim, targets([first - lead, first + 10]))

    def test_a_note_long_after_the_last_syllable_is_not_carried(self) -> None:
        payload = self.payload()
        last = max(s["start_sample"] for l in payload["lines"] for w in l["words"] for s in w.get("syllables", []))
        counts = lyrics_align.carry(payload, [last + 3 * RATE + 1], NOTE["revision_id"])
        self.assertEqual(counts["carried"], 0)

    def test_an_overlapping_line_is_another_voice_and_never_shortens_a_window(self) -> None:
        def line(start: int, end: int, *syllables: int) -> dict:
            return {"start_sample": start, "end_sample": end,
                    "words": [{"syllables": [{"start_sample": s} for s in syllables]}]}
        # Line one holds from 10000 to 80000; line two is sung over it from 20000 to 50000; line three
        # follows line one.
        payload = {"sample_rate": RATE, "lines": [line(10000, 80000, 10000, 60000), line(20000, 50000, 20000),
                                                  line(90000, 95000, 90000)]}
        lyrics_align.carry(payload, [40000, 85000], NOTE["revision_id"])
        first, later = payload["lines"][0]["words"][0]["syllables"]
        [other] = payload["lines"][1]["words"][0]["syllables"]
        # By time order alone line two's start would end the first syllable's window before 40000.
        self.assertEqual(first["carried_by"]["onsets"], [40000])
        self.assertIn(40000, other["carried_by"]["onsets"])
        # A line's last syllable runs to the next line after it, not into it.
        self.assertEqual(later["carried_by"]["onsets"], [85000])
        self.assertNotIn("carried_by", payload["lines"][2]["words"][0]["syllables"][0])

    def test_a_syllable_placing_empties_is_dropped_alone(self) -> None:
        alignment = fake_alignment()
        syllables = alignment["lines"][0]["words"][0]["syllables"]
        self.assertEqual(len(syllables), 3)
        syllables[1]["end"] = syllables[1]["start"]
        payload = lyrics_align.timed_payload(alignment, lines_of(DOCUMENT), RATE, RATE * 8, "s", "text")
        self.assertEqual([s["syllable"] for s in payload["lines"][0]["words"][0]["syllables"]], [1, 3])
        documents.check_timed_lyrics({"payload": payload, "refs": refs(False)}, targets([]))

    def test_spans_past_the_stream_are_clamped_and_empty_words_dropped(self) -> None:
        alignment = fake_alignment()
        cut = alignment["lines"][1]["words"][1]["start"] * RATE // 16000
        payload = lyrics_align.timed_payload(alignment, lines_of(DOCUMENT), RATE, cut, "s", "text")
        documents.check_timed_lyrics({"payload": payload, "refs": refs(False)}, targets([], count=cut))
        self.assertEqual([w["word"] for w in payload["lines"][-1]["words"]], [2])

    def test_a_locator_beyond_the_document_is_refused(self) -> None:
        for change in (lambda a: a["lines"][0].update(line=99),
                       lambda a: a["lines"][0]["words"][0].update(word=9),
                       lambda a: a["lines"][0]["words"][0]["syllables"][0].update(end_char=40),
                       lambda a: a["lines"][0]["words"][0].update(confidence=0.5)):
            alignment = fake_alignment()
            change(alignment)
            with self.subTest(), self.assertRaises(lyrics_align.AlignLyricsError):
                lyrics_align.timed_payload(alignment, lines_of(DOCUMENT), RATE, RATE * 8, "s", "text")


@needs_door
class Command(unittest.TestCase):
    """The mix, the document, and a vocal notes claim admitted through Door;
    the stem from the stand-in separator; `enrich align-lyrics` proposes."""

    def setUp(self) -> None:
        self.tmp = support.scratch(self, "align-lyrics-")
        self.env = {k: v for k, v in support.environment().items() if k != "ARCHIVE_FEATURE_BANK"}
        mix = support.sine_wav(self.tmp / "take.wav", RATE, "8")
        lyric = self.tmp / "lyrics.txt"
        lyric.write_text(DOCUMENT, encoding="utf-8")
        with patch.dict(os.environ):
            code, answer = run_stems(self.tmp, mix, "stems", program=fake_python(self.tmp))
        self.assertEqual(code, 0, answer)
        self.bank = self.tmp / "bank"
        self.root, manifest = support.build_archive(self, {"mix": mix, "lyrics": lyric})
        self.objects = manifest["objects"]
        code, answer = self.enrich("probe", "--artifact", self.objects["mix"]["object_id"],
                                   "--ffprobe", support.FFPROBE)
        self.assertEqual(code, 0, answer)
        reader = adapter.ArchiveReader(self.root, support.ARCHIVE, self.env)
        self.mix = reader.head(self.objects["mix"]["object_id"])
        [self.stream] = [r for r in reader.records if r["shape"] == "claim" and any(
            c["id"] == "archive.claim/audio_stream" for c in r.get("contracts", []))]
        self.reader = reader
        self.notes = self.notes_claim("vocals")
        self.weights = self.tmp / "weights"
        self.weights.mkdir()

    def notes_claim(self, part: str, **fields: object) -> str:
        """A notes claim about the mix, as a notes extractor would propose one."""
        notes = [{"onset_sample": o, "offset_sample": o + 2000, "pitch": 62} for o in range(0, RATE * 8, RATE // 4)]
        pins = (adapter.Pin("source", self.mix["object_id"], self.mix["revision_id"]),
                adapter.Pin("stream", self.stream["object_id"], self.stream["revision_id"]))
        result = adapter.RunResult(
            agent="pkg:generic/test-notes@1", method_id="test-notes", method_version="1",
            software=(("test-notes", "1"),), started_at=adapter.now(), ended_at=adapter.now(),
            used=(adapter.Pin("used", self.mix["object_id"], self.mix["revision_id"]),),
            outputs=(adapter.Output(("archive.claim/notes", "1"), part,
                                    {"sample_rate": RATE, "part": part, **fields, "notes": notes}, pins),))
        made = adapter.propose(self.reader, result, door=support.DOOR, staging=None, dry_run=False, env=self.env)
        return made.outputs[0]["object_id"]

    def enrich(self, command: str, *extra: str, env: dict | None = None) -> tuple[int, dict]:
        completed = subprocess.run(
            [sys.executable, str(HERE / "enrich.py"), command, "--archive", str(self.root), "--door", support.DOOR,
             "--archive-command", support.ARCHIVE, "--staging", str(self.tmp / "staging"), *extra],
            capture_output=True, text=True, env={**self.env, **(env or {})})
        return completed.returncode, json.loads(completed.stdout or completed.stderr)

    def align(self, *extra: str, env: dict | None = None) -> tuple[int, dict]:
        return self.enrich("align-lyrics", "--artifact", self.objects["mix"]["object_id"],
                           "--document", self.objects["lyrics"]["object_id"], "--bank", str(self.bank),
                           "--program", f"align-python={fake_aligner(self.tmp)}",
                           "--program", f"align-weights={self.weights}", *extra, env=env)

    def test_door_admits_the_timed_lyrics_world_checks_them_and_a_rerun_is_unchanged(self) -> None:
        code, answer = self.align("--notes", self.notes)
        self.assertEqual((code, answer["status"]), (0, "committed"), answer)
        self.assertGreater(answer["carried"], 0)
        records = support.export(self.root)["records"]
        by_revision = {r["revision_id"]: r for r in records}
        run = by_revision[answer["run"]["revision_id"]]
        self.assertEqual(run["payload"]["method"], {"id": "align-wav2vec2", "version": "1"})
        used = {ref["id"] for ref in run["refs"] if ref["role"] == "used"}
        self.assertEqual(used, {self.objects["mix"]["object_id"], self.objects["lyrics"]["object_id"],
                                self.stream["object_id"], self.notes})
        [parameters_ref] = [ref for ref in run["refs"] if ref["role"] == "parameters"]
        parameters_artifact = by_revision[parameters_ref["revision_id"]]
        digest = parameters_artifact["payload"]["content"]["digest"]
        hexdigest = digest.removeprefix("sha256:")
        parameters = json.loads((self.root / "content" / "sha256" / hexdigest[0:2] / hexdigest[2:4] / hexdigest)
                                .read_text(encoding="utf-8"))
        stems = bank.Bank(self.bank).entry(self.mix["payload"]["content"]["digest"], "stems")
        [claim] = [r for r in records if any(ref["role"] == "run" and ref["revision_id"] == run["revision_id"]
                                             for ref in r["refs"])]
        self.assertEqual(claim["contracts"][0]["id"], "archive.claim/timed_lyrics")
        self.assertEqual(sorted(ref["role"] for ref in claim["refs"]),
                         ["document", "notes", "run", "source", "stream"])
        self.assertEqual(claim["payload"]["alignment"], {"method": "forced alignment"})
        self.assertEqual(parameters["stem"]["key"], stems.manifest["key"])
        self.assertEqual((parameters["language"], parameters["aligner"]["id"]), ("en", "align-wav2vec2"))
        self.assertEqual(parameters["aligner"]["code"], bank.code_digest(HERE / "aligners" / "align-wav2vec2"))
        self.assertIn("Pyphen en_US", parameters["syllabification"])
        # World's own checks, against the committed revisions.
        targets = {}
        for ref in claim["refs"]:
            record = by_revision[ref["revision_id"]]
            target = {"id": record["object_id"], "shape": record["shape"], "payload": record["payload"],
                      "refs": record["refs"]}
            if record.get("contracts"):
                target["contract"] = (record["contracts"][0]["id"], record["contracts"][0]["version"])
            targets[ref["revision_id"]] = target
        documents.check_timed_lyrics({"payload": claim["payload"], "refs": claim["refs"]}, targets)
        documents.check_locators(claim, DOCUMENT.encode("utf-8"))
        # No word of the document is in any record.
        for word in ("Lumovar", "brindle", "kasimir"):
            self.assertNotIn(word, json.dumps(records, ensure_ascii=False))
        code, again = self.align("--notes", self.notes)
        self.assertEqual((code, again["status"]), (0, "unchanged"), again)

    def test_without_notes_no_syllable_is_carried(self) -> None:
        code, answer = self.align()
        self.assertEqual((code, answer["status"], answer["carried"]), (0, "committed", 0), answer)

    def test_a_dry_run_aligns_and_checks_but_commits_nothing(self) -> None:
        before = support.head(self.root)
        code, answer = self.align("--notes", self.notes, "--dry-run")
        self.assertEqual((code, answer["status"]), (0, "predicted"), answer)
        self.assertGreater(answer["carried"], 0)
        self.assertEqual(support.head(self.root), before)

    def test_only_the_stems_voice_carries_syllables(self) -> None:
        for part, fields in (("bass", {"bass": True}), ("mix", {})):
            with self.subTest(part=part):
                code, answer = self.align("--notes", self.notes_claim(part, **fields))
                self.assertEqual((code, answer["status"]), (1, "failed"), answer)
                self.assertIn("vocals", answer["detail"])

    def test_a_lost_alignment_proposes_nothing(self) -> None:
        before = support.head(self.root)
        code, answer = self.align(env={"FAKE_ALIGN_SCORE": "0.05"})
        self.assertEqual((code, answer["status"], answer["code"]), (3, "nothing_proposed", "low_confidence"), answer)
        self.assertEqual(support.head(self.root), before)

    def test_refusals(self) -> None:
        code, answer = self.enrich("align-lyrics", "--artifact", self.objects["mix"]["object_id"],
                                   "--document", self.objects["lyrics"]["object_id"], "--bank", str(self.bank))
        self.assertEqual((code, answer["status"]), (1, "failed"))
        self.assertIn("align-python", answer["detail"])
        empty = self.tmp / "empty-bank"
        empty.mkdir()
        code, answer = self.enrich("align-lyrics", "--artifact", self.objects["mix"]["object_id"],
                                   "--document", self.objects["lyrics"]["object_id"], "--bank", str(empty))
        self.assertEqual((code, answer["status"], answer["missing"]), (3, "nothing_proposed", ["stems/vocals"]))
        code, answer = self.align("--notes", self.stream["object_id"])
        self.assertEqual((code, answer["status"]), (1, "failed"))


if __name__ == "__main__":
    unittest.main()
