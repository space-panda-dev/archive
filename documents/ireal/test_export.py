"""Exporter lead-sheet-ireal, on invented charts only: a chart read into
claims and exported again is equal in claims (ADR 0116, section 5), and
what it cannot write is listed in its losses."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import codec  # noqa: E402
import export  # noqa: E402
import stand_in  # noqa: E402
from export import ExportRefused, render  # noqa: E402

REQUEST = {"format": "ireal", "exporter": export.EXPORTER}

# Invented charts, one per feature the exporter writes.
CHARTS = {
    "marks, repeats, endings, segno and coda":
        "[T44*iC^7 |A-7 D7 ]{*AG^7 E-7 |A-7 D7 |N1B-7 E7 |A-7 D7 }N2C^7 C#o7 |G6 ]"
        "[*BSF^7 |W/A Bb7(E7) |QA-7 D7 |G^7 <D.S. al Coda>|QG6 | x Z",
    "three endings and a mark inside the last":
        "{*AT44C |F |N1G7 |C }N2G7 |F }[*BN3D-7 |G7 <D.C. al Fine>|C ]*CF |C<Fine>Z",
    "a repeat count, D.C. to the end":
        "[*AT34C  F |G7  C |{D-7 G7  | x <3x>}*BA-  D7 |G7<D.C.>Z",
    "bar repeats, slashes, N.C. and held bars":
        "T44C7 F7 |x |Bb7 |r| |n  pp|    |F7 |W/A,W/Ab,Gb7,F7 Z",
    "odd cells, time changes and alternates":
        "T44C(E-7)  |T34D-7  G7 |T68C^7 A7 E-7 |T54F^7 Bb7  |T12C   Z",
    "text-only symbols written back as the chart's tokens":
        "T44C2 F** |C*mine* G7 |CZ",
    "comments as directions":
        "[*AT44<Intro vamp>C |F |<Solos><Open>G7 |C<Repeat and Fade>Z",
}


def chart_records(markup: str, key: str = "C", title: str = "Paper Lanterns", composer: str = "Ada Example",
                  style: str = "", tempo: str = ""):
    payloads = stand_in.read(markup, key, style, tempo)
    records, pin = stand_in.records(title, composer, payloads)
    return payloads, records, pin


def reread(link: bytes) -> tuple[codec.Song, dict]:
    song = codec.decode_playlist(link).songs[0]
    return song, stand_in.read(song.music, song.key, song.style, song.field("tempo"))


class RoundTrip(unittest.TestCase):
    def test_every_chart_is_equal_in_claims_after_export(self) -> None:
        for name, markup in CHARTS.items():
            with self.subTest(name):
                payloads, records, pin = chart_records(markup, "Eb-", style="Up Tempo Swing", tempo="220")
                link, losses = render({**REQUEST, "rendered": [pin]}, records)
                self.assertEqual(losses, [])
                song, again = reread(link)
                self.assertEqual(again, payloads)
                self.assertEqual((song.title, song.composer, song.key, song.style, song.field("tempo")),
                                 ("Paper Lanterns", "Ada Example", "Eb-", "Up Tempo Swing", "220"))

    def test_style_tempo_and_comments_are_directions_that_come_back(self) -> None:
        payloads, records, pin = chart_records(CHARTS["comments as directions"], style="Bossa Nova", tempo="140")
        bars = payloads["meter"]["layers"][0]["bars"]
        self.assertEqual(bars[0]["directions"], [{"text": "Bossa Nova", "tempo": {"bpm": "140"}},
                                                 {"text": "Intro vamp"}])
        self.assertEqual(bars[2]["directions"], [{"text": "Solos"}, {"text": "Open"}])
        self.assertEqual(bars[3]["directions"], [{"text": "Repeat and Fade"}])
        link, losses = render({**REQUEST, "rendered": [pin]}, records)
        self.assertEqual(losses, [])
        song, again = reread(link)
        self.assertEqual((song.style, song.field("tempo")), ("Bossa Nova", "140"))
        self.assertEqual(again, payloads)

    def test_a_chart_with_no_style_or_tempo_keeps_none(self) -> None:
        payloads, records, pin = chart_records("T44C |G7 Z")
        self.assertNotIn("directions", payloads["meter"]["layers"][0]["bars"][0])
        link, _ = render({**REQUEST, "rendered": [pin]}, records)
        song = codec.decode_playlist(link).songs[0]
        self.assertEqual((song.style, song.field("tempo")), (export.DEFAULT_STYLE, "0"))
        payloads, _, _ = chart_records("T44C |G7 Z", tempo="96")
        self.assertNotIn("directions", payloads["meter"]["layers"][0]["bars"][0])

    def test_options_override_the_directions_and_a_tempo_iReal_cannot_write_is_lost(self) -> None:
        payloads, records, pin = chart_records("T44C |G7 Z", style="Ballad", tempo="60")
        link, losses = render({**REQUEST, "rendered": [pin], "options": {"style": "Waltz", "tempo": 90}}, records)
        song = codec.decode_playlist(link).songs[0]
        self.assertEqual((song.style, song.field("tempo"), losses), ("Waltz", "90", []))
        records = copy.deepcopy(records)
        meter = next(r for r in records if r["contract"] == ["archive.claim/meter_layers", "3"])
        bars = meter["payload"]["layers"][0]["bars"]
        bars[0]["directions"][0]["tempo"] = {"bpm": "72.5"}
        bars[1]["directions"] = [{"text": "Faster", "tempo": {"bpm": "120", "beat": {"denominator": 2}}}]
        link, losses = render({**REQUEST, "rendered": [pin]}, records)
        song = codec.decode_playlist(link).songs[0]
        self.assertEqual((song.style, song.field("tempo")), ("Ballad", "0"))
        self.assertEqual(len(losses), 2)

    def test_the_link_is_one_chart_in_the_canonical_encoding(self) -> None:
        _, records, pin = chart_records(CHARTS["bar repeats, slashes, N.C. and held bars"])
        link, _ = render({**REQUEST, "rendered": [pin], "options": {"style": "Bossa Nova", "tempo": 140}}, records)
        playlist = codec.decode_playlist(link)
        self.assertIsNone(playlist.name)
        self.assertEqual(codec.encode_chart(playlist.songs[0]), link)
        self.assertEqual((playlist.songs[0].style, playlist.songs[0].field("tempo")), ("Bossa Nova", "140"))

    def test_identical_bars_are_written_x(self) -> None:
        _, records, pin = chart_records("T44C7 F7 |C7 F7 |Bb7 |Bb7 |Bb7 Z")
        link, _ = render({**REQUEST, "rendered": [pin]}, records)
        music = codec.decode_playlist(link).songs[0].music
        # The second bar repeats the first; a held downbeat chord is a bar of its own entry, never x.
        self.assertEqual(music.count(" x "), 1)
        self.assertEqual(reread(link)[1], stand_in.read("T44C7 F7 |C7 F7 |Bb7 |Bb7 |Bb7 Z", "C",
                                                                export.DEFAULT_STYLE))


class Transposition(unittest.TestCase):
    def test_moves_every_chord_and_the_key_spelled_by_the_key(self) -> None:
        _, records, pin = chart_records("T44F^7 |Bb7 |E-7b5 A7b9 |D-7 W/C Z", "F")
        link, losses = render({**REQUEST, "rendered": [pin], "options": {"transpose": 2}}, records)
        song = codec.decode_playlist(link).songs[0]
        self.assertEqual(song.key, "G")
        self.assertEqual(losses, [])
        symbols = [cell.text for bar in __import__("chart").read_chart(song.music).bars
                   for _, cell in bar.chord_cells()]
        self.assertEqual(symbols, ["G^7", "C7", "F#h7", "B7b9", "E-7", "E-7/D"])

    def test_a_text_only_reading_cannot_move_and_is_lost(self) -> None:
        _, records, pin = chart_records("T44C2 |G7 Z")
        _, losses = render({**REQUEST, "rendered": [pin], "options": {"transpose": -3}}, records)
        self.assertEqual(losses, ["text-only readings"])


class Losses(unittest.TestCase):
    def edited(self, change) -> tuple[list[dict], dict]:
        payloads = stand_in.read("T44C7 |F7 |G7 |C7 Z", "C")
        change(payloads)
        return stand_in.records("Paper Lanterns", "Ada Example", payloads)

    def test_readings_after_the_second(self) -> None:
        def three(payloads):
            payloads["sequence"]["entries"][0]["readings"] += [
                {"root": 0, "bass": 0, "tones": [4, 7, 11]}, {"root": 0, "bass": 0, "tones": [3, 7, 10]}]
        records, pin = self.edited(three)
        link, losses = render({**REQUEST, "rendered": [pin]}, records)
        self.assertIn("readings after the second", losses)
        self.assertIn("C7(C^7)", codec.decode_playlist(link).songs[0].music)

    def test_a_stack_no_quality_writes_is_a_comment_over_an_empty_cell(self) -> None:
        def cluster(payloads):
            payloads["sequence"]["entries"][1]["readings"] = [{"root": 5, "bass": 5, "tones": [6, 7], "text": "F cluster"}]
        records, pin = self.edited(cluster)
        link, losses = render({**REQUEST, "rendered": [pin]}, records)
        self.assertEqual(losses, ["uncertain tones and stacks no iReal quality writes"])
        self.assertIn("<F cluster>", codec.decode_playlist(link).songs[0].music)

    def test_a_label_that_is_no_mark_and_a_time_iReal_has_no_code_for(self) -> None:
        def odd(payloads):
            payloads["form"]["sections"][0]["label"] = "Bridge"
            for bar in payloads["meter"]["layers"][0]["bars"][2:]:
                bar["numerator"], bar["denominator"] = 11, 8
        records, pin = self.edited(odd)
        link, losses = render({**REQUEST, "rendered": [pin]}, records)
        self.assertEqual(losses, ["labels other than iReal's marks (written as comments)",
                                  "time signatures iReal has no code for"])
        self.assertTrue(codec.decode_playlist(link).songs[0].music.startswith("T44<Bridge>C7"))

    def test_kinds_and_nested_repeats(self) -> None:
        payloads = stand_in.read("{*AT44C |F }{*BG7 |C }Z", "C")
        payloads["form"]["sections"][0]["kind"] = "chorus"
        payloads["form"]["repeats"] = [{"first": 0, "last": 1, "times": 2}, {"first": 1, "last": 1, "times": 2}]
        records, pin = stand_in.records("Paper Lanterns", "", payloads)
        link, losses = render({**REQUEST, "rendered": [pin]}, records)
        self.assertEqual(sorted(losses), ["nested repeats", "section kinds other than intro and verse"])
        self.assertEqual(codec.decode_playlist(link).songs[0].composer, export.NO_COMPOSER)


    def test_only_composer_credits_fill_the_composer_field(self) -> None:
        _, records, pin = chart_records("T44C7 |F7 Z")
        records = copy.deepcopy(records)
        lead = records[-1]
        credit = next(r for r in records if r["contract"] == ["archive.claim/contribution", "1"])
        lyricist = {**copy.deepcopy(credit), "revision_id": "sha256:" + "1" * 64}
        lyricist["payload"].update(role={"type": "lyricist"}, text="Bo Invented", credited_as="Bo Invented")
        records.insert(-1, lyricist)
        lead["refs"].insert(0, {"role": "credits", "id": lyricist["id"], "revision_id": lyricist["revision_id"]})
        link, losses = render({**REQUEST, "rendered": [pin]}, records)
        self.assertEqual(losses, ["credits other than composers"])
        self.assertEqual(codec.decode_playlist(link).songs[0].composer, "Ada Example")


class Refusals(unittest.TestCase):
    def setUp(self) -> None:
        _, self.records, self.pin = chart_records("T44C7 |F7 Z")

    def test_requests_this_exporter_does_not_render(self) -> None:
        for request, reason in [
            ({**REQUEST, "format": "musicxml", "rendered": [self.pin]}, "format"),
            ({**REQUEST, "exporter": {"id": "lead-sheet-ireal", "version": "2"}, "rendered": [self.pin]}, "version"),
            ({**REQUEST, "rendered": [self.pin, self.pin]}, "exactly one"),
            ({**REQUEST, "rendered": [self.pin], "options": {"capo": 2}}, "unknown options"),
            ({**REQUEST, "rendered": [self.pin], "options": {"tempo": 20}}, "tempo"),
        ]:
            with self.subTest(reason), self.assertRaisesRegex(ExportRefused, reason):
                render(request, self.records)

    def test_a_timed_lead_sheet_is_refused(self) -> None:
        records = copy.deepcopy(self.records)
        lead = records[-1]
        lead["refs"].append({"role": "source", "id": lead["id"], "revision_id": lead["revision_id"]})
        with self.assertRaisesRegex(ExportRefused, "iReal has no time"):
            render({**REQUEST, "rendered": [self.pin]}, records)

    def test_inputs_that_break_worlds_rules_are_refused(self) -> None:
        records = copy.deepcopy(self.records)
        meter = next(r for r in records if r["contract"] == ["archive.claim/meter_layers", "3"])
        meter["payload"]["layers"][0]["bars"][0]["chords"][0]["entry"] = 7
        with self.assertRaisesRegex(ExportRefused, "World's rules"):
            render({**REQUEST, "rendered": [self.pin]}, records)


class Command(unittest.TestCase):
    def test_runs_as_the_relay_runs_an_exporter(self) -> None:
        _, records, pin = chart_records("T44C7 |F7 Z")
        with tempfile.TemporaryDirectory() as directory:
            given, output, losses = (Path(directory) / name for name in ("input.json", "out.irealb", "losses.json"))
            given.write_text(json.dumps({"request": {**REQUEST, "rendered": [pin]}, "key": "sha256:" + "0" * 64,
                                         "records": records, "joined": []}), encoding="utf-8")
            self.assertEqual(export.main([str(given), str(output), str(losses)]), 0)
            self.assertTrue(output.read_bytes().startswith(b"irealb://Paper%20Lanterns="))
            self.assertEqual(json.loads(losses.read_text(encoding="utf-8")), [])
            given.write_text(json.dumps({"request": {**REQUEST, "rendered": []}, "records": records}), encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()) as said:
                self.assertEqual(export.main([str(given), str(output), str(losses)]), 1)
            self.assertIn("exactly one lead sheet", said.getvalue())


class Pages(unittest.TestCase):
    def test_a_playlist_of_exports_and_the_page_iReal_opens(self) -> None:
        links = []
        for number, markup in enumerate(CHARTS.values()):
            _, records, pin = chart_records(markup, title=f"Invented {number}")
            links.append(render({**REQUEST, "rendered": [pin]}, records)[0])
        joined = export.playlist(links, "Invented & Co")
        playlist = codec.decode_playlist(joined)
        self.assertEqual((playlist.name, len(playlist.songs)), ("Invented & Co", len(CHARTS)))
        page = export.page(joined, "Invented & Co").decode("utf-8")
        self.assertIn(f'href="{joined.decode("ascii")}"', page)
        self.assertIn("<title>Invented &amp; Co</title>", page)


if __name__ == "__main__":
    unittest.main()
