"""Practice audio and exports (ADR 0089): the export request and its key,
the `archive.claim/export` version 1 rules, the relay's practice-audio
decision, and the MusicXML `<harmony>` mapping of ADR 0077.

Run from the repository root: python3 -m unittest world.tests.test_exports.
The module needs only the standard library. Every example is synthetic
(INV-23): the claims, documents, and people are invented pins. The stems
manifest is the installed extractor's own declaration, read from
enrich/extractors/stems/extractor.json, so the practice decision is tested
against what the bank actually declares.
"""

import copy
import json
import unittest
from pathlib import Path

from world.exports import (
    EXPORT,
    FORMATS,
    KINDS,
    ExportError,
    check_export,
    check_request,
    export_key,
    export_path,
    feature_name,
    musicxml_harmony,
    practice_refusal,
    request_of,
)

ROOT = Path(__file__).resolve().parents[2]
STEMS = json.loads((ROOT / "enrich/extractors/stems/extractor.json").read_text())["outputs"]


def pin(n: int) -> dict:
    return {"id": f"urn:uuid:00000000-0000-4000-8000-{n:012x}", "revision_id": "sha256:" + f"{n:02x}" * 32}


LEAD_SHEET = pin(0xA1)     # a claim: a lead sheet (ADR 0084)
CHORDS = pin(0xA2)         # a claim: chord stacks version 1
METER = pin(0xA3)          # a claim: meter layers version 1
LYRICS = pin(0xB1)         # an Artifact: the lyric document joined at export
SENT_FILE = pin(0xC1)      # an Artifact: the exported file as sent
PERSON = pin(0xD1)         # an Entity: a person, never renderable
OPERATOR = "urn:uuid:00000000-0000-4000-8000-0000000000e1"
BANDMATE = "urn:uuid:00000000-0000-4000-8000-0000000000e2"

TARGETS = {
    LEAD_SHEET["revision_id"]: {"id": LEAD_SHEET["id"], "shape": "claim", "contract": ("archive.claim/lead_sheet", "1")},
    CHORDS["revision_id"]: {"id": CHORDS["id"], "shape": "claim", "contract": ("archive.claim/chord_stacks", "1")},
    METER["revision_id"]: {"id": METER["id"], "shape": "claim", "contract": ("archive.claim/meter_layers", "1")},
    LYRICS["revision_id"]: {"id": LYRICS["id"], "shape": "artifact"},
    SENT_FILE["revision_id"]: {"id": SENT_FILE["id"], "shape": "artifact"},
    PERSON["revision_id"]: {"id": PERSON["id"], "shape": "entity", "kind": "person"},
}


def request(**changes) -> dict:
    base = {
        "format": "pdf",
        "exporter": {"id": "lead-sheet-lilypond", "version": "1"},
        "rendered": [LEAD_SHEET],
        "joined": [LYRICS],
        "options": {"transpose": 2, "staff_size": 20, "chord_font": "jazz"},
    }
    base.update(changes)
    return base


def claim_for(req: dict, **payload_changes) -> dict:
    payload = {
        "format": req["format"],
        "media_type": FORMATS[req["format"]][0],
        "exporter": req["exporter"],
        "key": export_key(req),
    }
    if req.get("options"):
        payload["options"] = req["options"]
    payload.update(payload_changes)
    refs = [{"role": "artifact", **SENT_FILE}]
    refs += [{"role": "rendered", **p} for p in req["rendered"]]
    refs += [{"role": "joined", **p} for p in req.get("joined", [])]
    return {"payload": payload, "refs": refs, "agent": OPERATOR}


class ExportRequestTest(unittest.TestCase):
    def test_accepts_each_format(self):
        for name in FORMATS:
            self.assertEqual(check_request(request(format=name))["format"], name)

    def test_key_is_stable_and_decided_by_every_field(self):
        base = export_key(request())
        self.assertRegex(base, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(base, export_key(copy.deepcopy(request())))
        # Anchor: each change below is a different request, so a different key.
        variants = [
            request(format="musicxml"),
            request(exporter={"id": "lead-sheet-lilypond", "version": "2"}),
            request(rendered=[pin(0xA9)]),
            request(rendered=[LEAD_SHEET, CHORDS]),
            request(joined=[]),
            request(options={"transpose": 3, "staff_size": 20, "chord_font": "jazz"}),
        ]
        keys = {export_key(v) for v in variants}
        self.assertEqual(len(keys), len(variants))
        self.assertNotIn(base, keys)

    def test_absent_and_empty_options_and_joins_share_a_key(self):
        bare = {"format": "jams", "exporter": {"id": "jams", "version": "1"}, "rendered": [CHORDS]}
        self.assertEqual(export_key(bare), export_key({**bare, "options": {}, "joined": []}))

    def test_order_of_rendered_is_part_of_the_key(self):
        self.assertNotEqual(export_key(request(rendered=[CHORDS, METER])),
                            export_key(request(rendered=[METER, CHORDS])))

    def test_option_order_is_not(self):
        a = request(options={"a": 1, "b": "x"})
        b = request(options={"b": "x", "a": 1})
        self.assertEqual(export_key(a), export_key(b))

    def test_refuses_malformed_requests(self):
        bad = [
            request(format="docx"),
            request(exporter={"id": "Lead Sheet", "version": "1"}),
            request(exporter={"id": "lead", "version": ""}),
            request(exporter={"id": "lead"}),
            request(rendered=[]),
            request(rendered=[LEAD_SHEET, LEAD_SHEET]),
            request(rendered=[{"id": LEAD_SHEET["id"]}]),
            request(rendered=[{"id": "song-1", "revision_id": LEAD_SHEET["revision_id"]}]),
            request(rendered=[pin(i) for i in range(1, 66)]),
            request(joined=[LEAD_SHEET]),
            request(joined=[pin(i) for i in range(1, 18)]),
            request(options={"Tempo": 1}),
            request(options={"scale": 1.5}),
            request(options={"nested": {"a": 1}}),
            request(options={"empty": ""}),
            request(options={"big": 2**60}),
            request(options={f"o{i}": 1 for i in range(33)}),
            {**request(), "recipient": PERSON},
            "pdf",
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ExportError):
                check_request(value)

    def test_path_is_under_the_derived_media_root(self):
        key = export_key(request())
        hexd = key.split(":")[1]
        path = export_path(Path("/media"), key, "pdf")
        self.assertEqual(path, Path("/media/exports") / hexd[:2] / hexd[2:4] / hexd / "export.pdf")
        with self.assertRaises(ExportError):
            export_path(Path("/media"), "sha256:XYZ", "pdf")
        with self.assertRaises(ExportError):
            export_path(Path("/media"), key, "docx")


class ExportClaimTest(unittest.TestCase):
    def test_accepts_a_sent_lead_sheet_pdf(self):
        claim = claim_for(request(), software=[{"name": "lilypond", "version": "2.24.4"}])
        self.assertEqual(check_export(claim, TARGETS)["format"], "pdf")

    def test_accepts_musicxml_with_losses_and_no_options(self):
        req = {"format": "musicxml", "exporter": {"id": "lead-sheet-musicxml", "version": "1"},
               "rendered": [CHORDS, METER]}
        claim = claim_for(req, losses=["alternative readings", "uncertain tones"])
        self.assertEqual(check_export(claim, TARGETS)["losses"], ["alternative readings", "uncertain tones"])

    def test_request_round_trips(self):
        req = request()
        self.assertEqual(export_key(request_of(claim_for(req))), export_key(req))

    def test_contract_name(self):
        self.assertEqual(EXPORT, ("archive.claim/export", "1"))

    def test_refuses_malformed_claims(self):
        good = claim_for(request())

        def changed(fn):
            value = copy.deepcopy(good)
            fn(value)
            return value

        bad = {
            "wrong key": changed(lambda c: c["payload"].update(key="sha256:" + "0" * 64)),
            "key of another order": changed(lambda c: c["payload"].update(
                key=export_key(request(rendered=[LEAD_SHEET], options={"transpose": 3})))),
            "media type mismatch": changed(lambda c: c["payload"].update(media_type="application/json")),
            "unknown format": changed(lambda c: c["payload"].update(format="docx")),
            "unknown field": changed(lambda c: c["payload"].update(recipient="a bandmate")),
            "missing key": changed(lambda c: c["payload"].pop("key")),
            "empty software": changed(lambda c: c["payload"].update(software=[])),
            "software without version": changed(lambda c: c["payload"].update(software=[{"name": "x"}])),
            "repeated loss": changed(lambda c: c["payload"].update(losses=["weights", "weights"])),
            "empty loss": changed(lambda c: c["payload"].update(losses=[""])),
            "no artifact": changed(lambda c: c.update(refs=[r for r in c["refs"] if r["role"] != "artifact"])),
            "two artifacts": changed(lambda c: c["refs"].append({"role": "artifact", **LYRICS})),
            "nothing rendered": changed(lambda c: c.update(refs=[r for r in c["refs"] if r["role"] != "rendered"])),
            "unknown role": changed(lambda c: c["refs"].append({"role": "recipient", **PERSON})),
            "unpinned ref": changed(lambda c: c["refs"][0].pop("revision_id")),
            "rendered is an entity": changed(lambda c: c["refs"].__setitem__(1, {"role": "rendered", **PERSON})),
            "rendered is an artifact": changed(lambda c: c["refs"].__setitem__(1, {"role": "rendered", **LYRICS})),
            "joined is a claim": changed(lambda c: c["refs"].__setitem__(2, {"role": "joined", **CHORDS})),
            "artifact is a claim": changed(lambda c: c["refs"].__setitem__(0, {"role": "artifact", **CHORDS})),
            "uncommitted revision": changed(lambda c: c["refs"].__setitem__(0, {"role": "artifact", **pin(0xFE)})),
            "artifact is its own input": changed(lambda c: c["refs"].__setitem__(0, {"role": "artifact", **LYRICS})),
            "revision of another object": changed(lambda c: c["refs"].__setitem__(
                0, {"role": "artifact", "id": LYRICS["id"], "revision_id": SENT_FILE["revision_id"]})),
            "extra record field": changed(lambda c: c.update(contracts=[])),
        }
        for name, value in bad.items():
            with self.subTest(name), self.assertRaises(ExportError):
                check_export(value, TARGETS)


class PracticeAudioTest(unittest.TestCase):
    SERVED = frozenset({"stems/vocals", "stems/drums", "stems/bass", "stems/other",
                        "practice/without_bass", "stems/levels"})
    MIX = {"kind": "file", "media_type": "audio/flac", "suffix": ".flac"}

    def test_feature_names(self):
        self.assertEqual(feature_name("stems/bass"), ("stems", "bass"))
        self.assertEqual(feature_name("practice/without_bass"), ("practice", "without_bass"))
        for bad in ("stems", "stems/", "/bass", "Stems/bass", "stems/bass/x", "../x/y", None):
            with self.subTest(bad), self.assertRaises(ExportError):
                feature_name(bad)

    def test_operator_hears_every_served_stem(self):
        # The real stems extractor declares its stems as audio files.
        for stem in ("vocals", "drums", "bass", "other"):
            self.assertIsNone(practice_refusal(f"stems/{stem}", STEMS[stem], self.SERVED,
                                               OPERATOR, OPERATOR, None))

    def test_a_bandmate_takes_the_recordings_answer(self):
        self.assertNotEqual(BANDMATE, OPERATOR)  # anchor: two callers
        self.assertIsNone(practice_refusal("stems/bass", STEMS["bass"], self.SERVED, BANDMATE, OPERATOR, True))
        self.assertEqual(practice_refusal("stems/bass", STEMS["bass"], self.SERVED, BANDMATE, OPERATOR, False),
                         "media_forbidden")

    def test_unknown_reach_never_widens(self):
        # A sensitivity or disclosure that cannot be established is refused,
        # and is not the same answer as a known yes.
        self.assertEqual(practice_refusal("practice/without_bass", self.MIX, self.SERVED,
                                          BANDMATE, OPERATOR, None), "media_forbidden")
        self.assertEqual(practice_refusal("stems/bass", STEMS["bass"], self.SERVED, OPERATOR, None, True),
                         "media_forbidden")

    def test_only_configured_audio_files_are_served(self):
        self.assertEqual(STEMS["levels"]["kind"], "series")  # anchor: a real non-file output
        cases = [
            ("stems/levels", STEMS["levels"], self.SERVED),          # configured, but a series
            ("stems/vocals_rms_db", STEMS["vocals_rms_db"], self.SERVED | {"stems/vocals_rms_db"}),
            ("stems/vocals", STEMS["vocals"], frozenset()),          # an audio file, not configured
            ("practice/without_drums", self.MIX, self.SERVED),       # not configured
            ("practice/without_bass", None, self.SERVED),            # no installed extractor declares it
            ("frames/hpcp", {"kind": "file", "media_type": "application/octet-stream"}, {"frames/hpcp"}),
        ]
        for feature, declared, served in cases:
            with self.subTest(feature):
                self.assertEqual(practice_refusal(feature, declared, served, OPERATOR, OPERATOR, True),
                                 "feature_not_served")


def stack(bass, tones, **extra):
    return {"bass": bass, "tones": tones, **extra}


class MusicXmlHarmonyTest(unittest.TestCase):
    def kind(self, xml):
        return xml.split("<kind", 1)[1].split(">", 1)[1].split("<", 1)[0]

    def test_known_qualities_take_their_kind(self):
        cases = {
            "major-seventh": stack(0, [4, 7, 11]),
            "minor-seventh": stack(0, [3, 7, 10]),
            "dominant": stack(0, [4, 7, 10]),
            "half-diminished": stack(0, [3, 6, 10]),
            "diminished-seventh": stack(0, [3, 6, 9]),
            "major-minor": stack(0, [3, 7, 11]),
            "suspended-fourth": stack(0, [5, 7]),
            "power": stack(0, [7]),
        }
        for kind, reading in cases.items():
            xml, losses = musicxml_harmony([reading])
            with self.subTest(kind):
                self.assertEqual(self.kind(xml), kind)
                self.assertEqual(losses, [])
                self.assertNotIn("<degree>", xml)

    def test_slash_bass_and_spelling(self):
        xml, _ = musicxml_harmony([stack(7, [0, 3, 10])])  # Cm7/G
        self.assertIn("<root><root-step>C</root-step></root>", xml)
        self.assertIn('<kind text="m7">minor-seventh</kind>', xml)
        self.assertIn("<bass><bass-step>G</bass-step></bass>", xml)
        xml, _ = musicxml_harmony([stack(7, [0, 2, 5, 10])])  # Bb6(9)/G
        self.assertIn("<root-step>B</root-step><root-alter>-1</root-alter>", xml)
        self.assertIn('<kind text="6(9)">major-sixth</kind>', xml)

    def test_a_key_spells_the_root(self):
        sharps = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        xml, _ = musicxml_harmony([stack(3, [7, 10])], spelling=sharps)
        self.assertIn("<root-step>D</root-step><root-alter>1</root-alter>", xml)
        with self.assertRaises(ExportError):
            musicxml_harmony([stack(3, [7, 10])], spelling=("C",) * 12)  # does not spell pitch class 3
        with self.assertRaises(ExportError):
            musicxml_harmony([stack(3, [7, 10])], spelling=("C", "D"))

    def test_extensions_are_degrees(self):
        xml, _ = musicxml_harmony([stack(0, [3, 4, 7, 10])])  # C7#9
        self.assertEqual(self.kind(xml), "dominant")
        self.assertIn("<degree-value>9</degree-value><degree-alter>1</degree-alter><degree-type>add</degree-type>", xml)
        xml, _ = musicxml_harmony([stack(0, [2, 4, 7])])  # Cadd9
        self.assertEqual(self.kind(xml), "major")
        self.assertIn("<degree-value>9</degree-value><degree-alter>0</degree-alter><degree-type>add</degree-type>", xml)
        xml, _ = musicxml_harmony([stack(3, [])])  # a lone note: a power chord without its fifth
        self.assertIn("<degree-value>5</degree-value><degree-alter>0</degree-alter><degree-type>subtract</degree-type>", xml)

    def test_every_kind_is_a_musicxml_kind_with_a_root(self):
        musicxml_kinds = {
            "major", "minor", "augmented", "diminished", "dominant", "major-seventh", "minor-seventh",
            "diminished-seventh", "augmented-seventh", "half-diminished", "major-minor", "major-sixth",
            "minor-sixth", "dominant-ninth", "major-ninth", "minor-ninth", "dominant-11th", "major-11th",
            "minor-11th", "dominant-13th", "major-13th", "minor-13th", "suspended-second",
            "suspended-fourth", "power"}
        self.assertEqual(set(KINDS), musicxml_kinds)
        for kind, degrees in KINDS.items():
            self.assertEqual(degrees[0], (1, 0), kind)

    def test_what_harmony_cannot_hold_is_reported(self):
        readings = [stack(0, [4, 7], uncertain=[{"pitch_classes": [10, 11], "present": "exactly_one"}],
                          voicing=[48, 52, 55], weight="0.7"),
                    stack(0, [3, 7], weight="0.3")]
        xml, losses = musicxml_harmony(readings)
        self.assertIsNotNone(xml)
        self.assertEqual(set(losses), {"alternative readings", "weights", "uncertain tones", "voicing"})
        xml, losses = musicxml_harmony([stack(4, [2, 7, 10], root=0)])  # rootless C9
        self.assertIn("<root-step>C</root-step>", xml)
        self.assertEqual(losses, ["a root that does not sound"])

    def test_no_chord_and_text(self):
        xml, losses = musicxml_harmony([{"no_chord": True}])
        self.assertIn('<kind text="N.C.">none</kind>', xml)
        xml, losses = musicxml_harmony([{"text": "Gm(maj13)/D"}])
        self.assertIsNone(xml)
        self.assertEqual(losses, ["a reading with no stack"])

    def test_refuses_malformed_readings(self):
        for readings in ([], None, [stack(0, [7, 4])], [{"bass": 12, "tones": []}], [{"no_chord": False}]):
            with self.subTest(readings), self.assertRaises(ExportError):
                musicxml_harmony(readings)


if __name__ == "__main__":
    unittest.main()
