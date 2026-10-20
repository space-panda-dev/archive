"""Song regions, region entries, and bank requests (ADR 0112).

Region entries are made by the real runner over a real cut of synthetic
audio; song spans are read from export records shaped as the fixture's
(`fixture/plan.json`) are.
"""

from __future__ import annotations

import io
import json
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import adapter
import bank
import extract
import regions
import support
from regions import Region
from support import needs_ffmpeg
from test_bank import Fixture

ARTIFACT = "obj-recording"
OTHER = "obj-other"
RATE = 8000


def claim(name: str, contract: tuple[str, str], payload: dict, refs: list[dict], supersedes: str | None = None) -> dict:
    record = {"object_id": f"obj-{name}", "revision_id": f"rev-{name}", "shape": "claim",
              "contracts": [{"id": contract[0], "version": contract[1]}], "payload": payload, "refs": refs}
    if supersedes:
        record["supersedes"] = [{"revision_id": supersedes}]
        record["object_id"] = f"obj-{supersedes.removeprefix('rev-')}"
    return record


def piece(artifact: str = ARTIFACT) -> dict:
    return {"role": "piece", "id": artifact, "revision_id": f"rev-of-{artifact}"}


def selection(name: str, spans: list[tuple[int, int]], rate: int = RATE, artifact: str = ARTIFACT) -> dict:
    return claim(name, ("archive.claim/audio_selection", "2"),
                 {"tracks": [{"pieces": [{"start_sample": s, "end_sample": e, "sample_rate": rate}
                                         for s, e in spans]}]},
                 [piece(artifact) for _ in spans])


def occurrence(name: str, chosen: dict) -> dict:
    return claim(name, ("archive.claim/song_occurrence", "2"), {},
                 [{"role": "selection", "id": chosen["object_id"], "revision_id": chosen["revision_id"]}])


def suggestions(name: str, spans: list[tuple[int, int, str]], artifact: str = ARTIFACT) -> dict:
    return claim(name, ("archive.claim/span_suggestions", "1"),
                 {"sample_rate": RATE, "spans": [{"start_sample": s, "end_sample": e, "class": c} for s, e, c in spans]},
                 [{"role": "source", "id": artifact, "revision_id": "r"}])


def stream(rate: int = RATE, samples: int = 600 * RATE, index: int = 0) -> dict:
    return claim(f"stream-{index}", ("archive.claim/audio_stream", "1"),
                 {"stream_index": index, "codec": "pcm_s16le", "sample_rate": rate, "channel_count": 1,
                  "sample_count": samples, "bits_per_sample": 16},
                 [{"role": "artifact", "id": ARTIFACT, "revision_id": "r"}])


def reader(*records: dict) -> adapter.ArchiveReader:
    return adapter.ArchiveReader.from_export(Path("."), {"archive_id": "a", "spec": "0.0.10", "snapshot": None,
                                                         "records": list(records), "contracts": []})


def seconds(*pairs: tuple[float, float]) -> list[tuple[int, int]]:
    return [(round(a * RATE), round(b * RATE)) for a, b in pairs]


class SongSpans(unittest.TestCase):
    def test_each_source_wins_whole_over_those_below_it(self) -> None:
        chosen = selection("sel-song", seconds((100, 200)))
        loose = selection("sel-loose", seconds((300, 320)))
        guessed = suggestions("sugg", [(0, 50 * RATE, "music"), (50 * RATE, 60 * RATE, "talk")])
        playing = [{"start": 400.0, "end": 450.5}]
        # Anchor: the four sources give four different answers.
        everything = (chosen, occurrence("occ", chosen), loose, guessed)
        self.assertEqual(regions.song_spans(reader(*everything), ARTIFACT, RATE, playing),
                         ("song_occurrences", seconds((100, 200))))
        self.assertEqual(regions.song_spans(reader(chosen, loose, guessed), ARTIFACT, RATE, playing),
                         ("selections", seconds((100, 200), (300, 320))))
        self.assertEqual(regions.song_spans(reader(guessed), ARTIFACT, RATE, playing),
                         ("span_suggestions", seconds((0, 50))))
        self.assertEqual(regions.song_spans(reader(), ARTIFACT, RATE, playing),
                         ("playing", seconds((400, 450.5))))
        self.assertEqual(regions.song_spans(reader(), ARTIFACT, RATE), ("none", []))

    def test_only_this_recordings_head_spans_count_at_its_stream_rate(self) -> None:
        elsewhere = selection("sel-other", seconds((10, 20)), artifact=OTHER)
        old = selection("sel-old", seconds((30, 40)))
        new = claim("sel-new", ("archive.claim/audio_selection", "2"),
                    {"tracks": [{"pieces": [{"start_sample": 48000 * 50, "end_sample": 48000 * 60 + 1,
                                             "sample_rate": 48000}]}]}, [piece()], supersedes="rev-sel-old")
        other_suggestions = suggestions("sugg-other", [(0, RATE, "music")], artifact=OTHER)
        source, spans = regions.song_spans(reader(elsewhere, old, new, other_suggestions), ARTIFACT, RATE)
        # 48 kHz pieces on an 8 kHz stream, widened outward to whole samples.
        self.assertEqual((source, spans), ("selections", [(50 * RATE, 60 * RATE + 1)]))

    def test_a_selection_whose_pieces_and_references_disagree_is_refused(self) -> None:
        broken = selection("sel", seconds((1, 2), (3, 4)))
        broken["refs"] = broken["refs"][:1]
        with self.assertRaisesRegex(regions.RegionError, "2 pieces and 1 piece references"):
            regions.song_spans(reader(broken), ARTIFACT, RATE)

    def test_the_rate_is_the_first_streams(self) -> None:
        self.assertEqual(regions.stream_of(reader(stream(44100, 10, index=1), stream(RATE, 99)), ARTIFACT),
                         (RATE, 99))
        with self.assertRaisesRegex(regions.RegionError, "probe it first"):
            regions.stream_of(reader(), ARTIFACT)


class Widening(unittest.TestCase):
    def test_spans_are_widened_snapped_clipped_and_merged(self) -> None:
        spans = seconds((5.5, 60.2), (95, 120), (300, 310), (590, 599.5))
        self.assertEqual(regions.regions_of(spans, RATE, length=600 * RATE - 3),
                         [Region(0, 130 * RATE, RATE), Region(290 * RATE, 320 * RATE, RATE),
                          Region(580 * RATE, 600 * RATE - 3, RATE)])
        # 30 s apart is not merged; 29 s is.
        self.assertEqual(len(regions.regions_of(seconds((0, 10), (60, 70)), RATE)), 2)
        self.assertEqual(len(regions.regions_of(seconds((0, 10), (59, 70)), RATE)), 1)

    def test_a_span_edited_within_its_margin_is_still_covered(self) -> None:
        made = regions.regions_of(seconds((100, 200)), RATE)
        for start, end in ((90, 210), (95.5, 200), (100, 209.9)):
            with self.subTest((start, end)):
                self.assertTrue(regions.covered(made, round(start * RATE), round(end * RATE), RATE))
        self.assertFalse(regions.covered(made, 89 * RATE, 150 * RATE, RATE))
        # The same span at another rate compares exactly.
        self.assertTrue(regions.covered(made, 90 * 48000, 210 * 48000, 48000))
        self.assertFalse(regions.covered(made, 90 * 48000 - 1, 210 * 48000, 48000))

    def test_regions_for_reads_the_stream_and_the_spans(self) -> None:
        chosen = selection("sel", seconds((100, 200)))
        found = regions.regions_for(reader(stream(samples=150 * RATE), chosen, occurrence("occ", chosen)),
                                    {"object_id": ARTIFACT})
        self.assertEqual((found["source"], found["rate"], found["regions"]),
                         ("song_occurrences", RATE, [Region(90 * RATE, 150 * RATE, RATE)]))


class Plans(unittest.TestCase):
    EXTRACTORS = ("stems", "f0-pesto", "melody", "chords-jiang", "notes-basic-pitch", "frames", "rhythm",
                  "structure", "quality")

    def test_expensive_is_the_measured_cost_above_the_threshold(self) -> None:
        self.assertEqual(sorted(e for e in self.EXTRACTORS if regions.expensive(e)),
                         ["chords-jiang", "f0-pesto", "melody", "notes-basic-pitch", "stems"])
        self.assertTrue(regions.expensive("rhythm", {"rhythm": 0.06}))
        self.assertFalse(regions.expensive("chords-chordino"))  # 0.042: the threshold was 0.04 until measured
        self.assertFalse(regions.expensive("stems", {"stems": 0.5}, threshold=1))

    def test_each_mode_plans_what_it_should(self) -> None:
        songs = [Region(0, RATE, RATE)]
        eager = regions.plan("eager", self.EXTRACTORS, songs)
        self.assertEqual(set(eager.values()), {"whole"})
        self.assertEqual(set(regions.plan("on-demand", self.EXTRACTORS, songs).values()), {None})
        planned = regions.plan("songs", self.EXTRACTORS, songs)
        self.assertEqual({e for e, p in planned.items() if p == songs},
                         {"stems", "f0-pesto", "melody", "chords-jiang", "notes-basic-pitch"})
        self.assertEqual({e for e, p in planned.items() if p == "whole"}, {"frames", "rhythm", "structure", "quality"})
        # A recording with no song spans makes its expensive features only on request.
        self.assertEqual(regions.plan("songs", ["stems", "frames"], []), {"stems": None, "frames": "whole"})
        # One region that is the whole content is made whole; in on-demand, still nothing.
        self.assertEqual(set(regions.plan("songs", self.EXTRACTORS, songs, length=RATE).values()), {"whole"})
        self.assertEqual(set(regions.plan("on-demand", self.EXTRACTORS, songs, length=RATE).values()), {None})
        self.assertEqual(regions.plan("songs", ["stems"], songs, length=RATE + 1), {"stems": songs})
        with self.assertRaisesRegex(regions.RegionError, "not one of"):
            regions.plan("lazy", self.EXTRACTORS, songs)


class RegionKeys(unittest.TestCase):
    def test_a_region_key_is_the_digest_of_its_canonical_description(self) -> None:
        content = "sha256:" + "ab" * 32
        described = ('{"content":"' + content + '","contract":"archive-feature-bank-key/2","extractor":"x",'
                     '"needs":{},"parameters":"' + bank.digest_of(b"{}") + '",'
                     '"region":{"end":16000,"rate":8000,"start":8000},"version":"1"}')
        region = {"start": 8000, "end": 16000, "rate": 8000}
        self.assertEqual(bank.key_of("x", "1", {}, content, {}, region), bank.digest_of(described.encode()))
        # The whole content's key is still the /1 key.
        self.assertNotEqual(bank.key_of("x", "1", {}, content, {}), bank.key_of("x", "1", {}, content, {}, region))
        for bad in ({"start": 5, "end": 5, "rate": 8000}, {"start": -1, "end": 5, "rate": 8000},
                    {"start": 0, "end": 5, "rate": 0}, {"start": 0, "end": 5}, {"start": 0, "end": 5.0, "rate": 1},
                    {"start": True, "end": 5, "rate": 1}):
            with self.subTest(bad), self.assertRaises(bank.BankError):
                bank.key_of("x", "1", {}, content, {}, bad)

    def test_a_view_keys_only_its_content_over_the_region(self) -> None:
        fixture = Fixture(self)
        fixture.add("a")
        fixture.add("b", needs=("a/x",))
        catalog = bank.Catalog.load([fixture.extractors])
        region = {"start": 0, "end": 10, "rate": 1}
        view = catalog.within(fixture.digest, region)
        whole, there = catalog.keys(fixture.digest, ["b"]), view.keys(fixture.digest, ["b"])
        self.assertNotEqual(whole, there)
        self.assertEqual(there["b"], bank.key_of("b", "1", {"mode": "ok"}, fixture.digest, {"a/x": there["a"]}, region))
        other = "sha256:" + "cd" * 32
        self.assertEqual(view.keys(other, ["b"]), catalog.keys(other, ["b"]))
        self.assertIsNone(catalog.region)


@needs_ffmpeg
class RegionRuns(unittest.TestCase):
    def fixture(self) -> Fixture:
        fixture = Fixture(self)
        fixture.content = support.sine_wav(fixture.dir / "take.wav", RATE, "20")
        fixture.digest = bank.file_digest(fixture.content)
        fixture.add("a")
        fixture.add("b", needs=("a/x",))
        return fixture

    def test_a_region_run_files_region_entries_over_a_checked_cut(self) -> None:
        fixture = self.fixture()
        code, answer = fixture.run("b", "--region", f"{2 * RATE}:{5 * RATE}:{RATE}")
        self.assertEqual((code, fixture.statuses(answer)), (0, {"a": "ran", "b": "ran"}), answer)
        region = {"start": 2 * RATE, "end": 5 * RATE, "rate": RATE}
        self.assertEqual(answer["region"], region)
        reader = fixture.reader()
        view = bank.Bank(fixture.bank, reader.catalog.within(fixture.digest, region))
        entry = view.entry(fixture.digest, "b")
        self.assertEqual((entry.manifest["contract"], entry.region, entry.offset, entry.manifest["media_type"]),
                         (bank.REGION_ENTRY_CONTRACT, region, 2.0, "audio/flac"))
        self.assertEqual(entry.manifest["needs"], {"a/x": view.entry(fixture.digest, "a").manifest["key"]})
        # The cut is the region's samples, and cutting again gives the same bytes.
        again = fixture.dir / "again.flac"
        self.assertEqual(extract.cut_region(shutil.which("ffmpeg"), fixture.content, region, again),
                         entry.manifest["cut"])
        self.assertEqual(extract.flac_stream_info(again), (RATE, 3 * RATE))
        # Filed under the content, beside its whole entries, which it is not.
        self.assertIsNone(reader.entry(fixture.digest, "b"))
        self.assertTrue(entry.path.is_relative_to(fixture.bank / "sha256"))
        # A rerun hashes and runs nothing.
        with patch.object(extract, "run_program", side_effect=AssertionError("ran")), \
                patch.object(extract, "cut_region", side_effect=AssertionError("cut")):
            code, answer = fixture.run("b", "--region", f"{2 * RATE}:{5 * RATE}:{RATE}", digest=True)
        self.assertEqual(fixture.statuses(answer), {"a": "hit", "b": "hit"})

    def test_covering_finds_the_whole_entry_or_a_containing_region_never_a_stitch(self) -> None:
        fixture = self.fixture()
        for start, end in ((1, 4), (4, 9), (2, 6)):
            self.assertEqual(fixture.run("a", "--region", f"{start * RATE}:{end * RATE}:{RATE}")[0], 0)
        reader = fixture.reader()
        found = bank.covering(reader, fixture.digest, "a", 2 * RATE, 3 * RATE, RATE)
        self.assertEqual(found.region, {"start": RATE, "end": 4 * RATE, "rate": RATE})  # the narrowest
        self.assertEqual(bank.covering(reader, fixture.digest, "a", 5 * 48000, 6 * 48000, 48000).region["end"],
                         6 * RATE)
        self.assertIsNone(bank.covering(reader, fixture.digest, "a", RATE, 7 * RATE, RATE))
        self.assertIsNone(bank.covering(reader, fixture.digest, "a", 9 * RATE, 10 * RATE, RATE))
        # An entry of another version covers nothing.
        manifest = json.loads((fixture.extractors / "a" / "extractor.json").read_text())
        (fixture.extractors / "a" / "extractor.json").write_text(json.dumps({**manifest, "version": "2"}))
        self.assertIsNone(bank.covering(fixture.reader(), fixture.digest, "a", 2 * RATE, 3 * RATE, RATE))
        # The whole entry, once made, covers every span.
        (fixture.extractors / "a" / "extractor.json").write_text(json.dumps(manifest))
        self.assertEqual(fixture.run("a", "--no-prefer")[0], 0)
        self.assertIsNone(bank.covering(fixture.reader(), fixture.digest, "a", RATE, 7 * RATE, RATE).region)

    def test_a_region_view_reads_whole_entries_first_then_the_regions(self) -> None:
        fixture = self.fixture()
        self.assertEqual(fixture.run("b", "--region", f"{4 * RATE}:{9 * RATE}:{RATE}")[0], 0)
        self.assertEqual(fixture.run("a", "--region", f"{RATE}:{4 * RATE}:{RATE}")[0], 0)
        reader = fixture.reader()
        region = {"start": 4 * RATE, "end": 9 * RATE, "rate": RATE}
        self.assertEqual(bank.regions(reader, fixture.digest),
                         [{"start": RATE, "end": 4 * RATE, "rate": RATE}, region])
        view = bank.RegionView(reader, fixture.digest, region)
        found = view.entry(fixture.digest, "b")
        self.assertEqual((found.region, found.offset, found.shift(RATE), found.shift(2 * RATE)),
                         (region, 4.0, 4 * RATE, 8 * RATE))
        self.assertEqual(view.entry(fixture.digest, "a").region, region)  # its need, over the same region
        self.assertIsNone(reader.entry(fixture.digest, "b"))  # the plain reader finds only whole entries
        # Once a whole entry exists the view prefers it.
        self.assertEqual(fixture.run("a", "--no-prefer")[0], 0)
        self.assertIsNone(bank.RegionView(fixture.reader(), fixture.digest, region).entry(fixture.digest, "a").region)
        # Other content is keyed whole, as the runner keys a stem made over a region.
        self.assertIsNone(view.entry("sha256:" + "ab" * 32, "a"))

    def test_a_region_the_content_does_not_hold_is_refused(self) -> None:
        fixture = self.fixture()
        for region, message in ((f"{18 * RATE}:{21 * RATE}:{RATE}", "runs past the content's end"),
                                (f"0:{RATE}:44100", "is at 44100 Hz"),
                                ("0:10", "<start>:<end>:<rate>"), (f"{RATE}:{RATE}:{RATE}", "start < end")):
            with self.subTest(region):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    code = extract.main(["run", "a", "--content", str(fixture.content), "--bank", str(fixture.bank),
                                         "--extractors", str(fixture.extractors), "--region", region])
                self.assertEqual(code, 1)
                self.assertIn(message, err.getvalue())
        self.assertEqual(fixture.entries(), [])


class Requests(unittest.TestCase):
    def test_a_second_request_is_the_same_file_and_the_first_stands(self) -> None:
        fixture = Fixture(self)
        fixture.add("a")
        reader = fixture.reader()
        path, key, wrote = bank.ask(reader, fixture.digest, "a", "relay")
        self.assertTrue(wrote)
        self.assertEqual(key, reader.catalog.keys(fixture.digest, ["a"])["a"])
        first = path.read_bytes()
        again = bank.ask(reader, fixture.digest, "a", "enrich propose")
        self.assertEqual((again[0], again[1], again[2], path.read_bytes()), (path, key, False, first))
        region = {"start": 0, "end": 8000, "rate": 8000}
        _, region_key, _ = bank.ask(reader, fixture.digest, "a", "scheduler", region=region)
        self.assertEqual(region_key, reader.catalog.within(fixture.digest, region).keys(fixture.digest, ["a"])["a"])
        listed = bank.requests(fixture.bank)
        self.assertEqual(sorted(r["key"] for r in listed), sorted([key, region_key]))
        self.assertEqual([r["asked_by"] for r in listed if r["key"] == key], ["relay"])
        self.assertEqual([p.name for p in (fixture.bank / bank.REQUESTS).iterdir() if p.name.startswith(".")], [])

    def test_no_requests_is_empty_and_a_damaged_one_is_refused_not_skipped(self) -> None:
        fixture = Fixture(self)
        fixture.add("a")
        self.assertEqual(bank.requests(fixture.bank), [])
        path, key, _ = bank.ask(fixture.reader(), fixture.digest, "a", "relay")
        moved = path.with_name("ab" * 32 + ".json")
        path.rename(moved)
        with self.assertRaisesRegex(bank.BankError, "not its own name"):
            bank.requests(fixture.bank)
        moved.write_text("{")
        with self.assertRaisesRegex(bank.BankError, "unreadable"):
            bank.requests(fixture.bank)
        moved.write_text(json.dumps({"contract": bank.BANK_REQUEST_CONTRACT, "key": key}))
        with self.assertRaisesRegex(bank.BankError, "fields must be"):
            bank.requests(fixture.bank)


if __name__ == "__main__":
    unittest.main()
