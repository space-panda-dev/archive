"""Documents, timed lyrics, sensitivity, and rights (ADR 0085): the payload
and reference rules, the relay's staging checks, the lyric view, and reach.

Run from the repository root: python3 -m unittest world.tests.test_documents.
The module needs only the standard library. Every example is synthetic
(INV-23): the lyric is invented, the people and the song are placeholders, and
the stream claim is the fixture archive's own stream record from
fixture/plan.json, lengthened so that lines fit. Audio and document bytes are
made by real writers (the standard library's `wave` and `zipfile`), not typed
in by hand, except where a format's writer is not in the standard library.
"""

import base64
import copy
import io
import json
import unittest
import wave
import zipfile
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from world import documents
from world.documents import (
    APP_SETTINGS,
    CLASSES,
    DOCUMENT_SOURCE,
    LABELS,
    RIGHTS,
    SENSITIVITY,
    TIMED_LYRICS,
    DocumentError,
    check_document_admission,
    check_document_bytes,
    check_document_source,
    check_locators,
    check_rights,
    check_sensitivity,
    check_stage_request,
    check_timed_lyrics,
    closure,
    document_lines,
    google_doc_url,
    lyric_view,
    may_show,
    reach,
    statement_uri,
)

ROOT = Path(__file__).resolve().parents[2]
RATE = 8000
TODAY = date.today()


def pin(n: int) -> dict:
    return {"id": f"urn:uuid:00000000-0000-4000-8000-{n:012x}", "revision_id": "sha256:" + f"{n:02x}" * 32}


OPERATOR = pin(0xA1)       # the active operator's person Entity
BANDMATE = pin(0xA2)       # a person in the operator's circle
SINGER = pin(0xA3)         # a person the recording records
LABEL_CO = pin(0xA4)       # an organisation holding rights
WORK = pin(0xB1)           # a musical_work
RECORDING_ENTITY = pin(0xB2)
MIX = pin(0xC1)            # a recording's Artifact
MIX_STREAM = pin(0xC2)
OTHER = pin(0xC3)          # another Artifact
LYRICS = pin(0xD1)         # a plain-text lyric document
TRANSCRIPT = pin(0xD2)     # an ASR transcript, admitted as a document
TAB = pin(0xD3)            # a paid tab
RECEIPT = pin(0xD4)
RUN = pin(0xE1)
EARLIER = pin(0xF1)        # an earlier timed lyrics claim
SEQUENCE = pin(0xF2)       # a chord sequence claim (not timed lyrics)


def fixture_stream(artifact: dict, seconds: int) -> dict:
    """The fixture archive's audio stream claim, about `artifact`."""
    plan = json.loads((ROOT / "fixture/plan.json").read_text())
    records = [r for step in plan["steps"] for r in step.get("records", [])]
    record = next(r for r in records if r.get("namespace") == "archive.claim/audio_stream")
    payload = dict(record["payload"])
    assert payload["sample_rate"] == RATE, "the fixture stream's rate changed; update RATE"
    payload["sample_count"] = seconds * RATE
    return {"shape": "claim", "contract": ("archive.claim/audio_stream", "1"), "payload": payload,
            "refs": [{"role": "artifact", **artifact}, {"role": "run", **RUN}]}


def entity(kind: str) -> dict:
    return {"shape": "entity", "kind": kind}


TARGETS = {
    OPERATOR["revision_id"]: entity("person"),
    BANDMATE["revision_id"]: entity("person"),
    SINGER["revision_id"]: entity("person"),
    LABEL_CO["revision_id"]: entity("organisation"),
    WORK["revision_id"]: entity("musical_work"),
    RECORDING_ENTITY["revision_id"]: entity("recording"),
    MIX["revision_id"]: {"shape": "artifact"},
    MIX_STREAM["revision_id"]: fixture_stream(MIX, 240),
    OTHER["revision_id"]: {"shape": "artifact"},
    LYRICS["revision_id"]: {"shape": "artifact"},
    TRANSCRIPT["revision_id"]: {"shape": "artifact"},
    TAB["revision_id"]: {"shape": "artifact"},
    RECEIPT["revision_id"]: {"shape": "artifact"},
    RUN["revision_id"]: {"shape": "claim", "contract": ("archive.claim/derivation_run", "1")},
    EARLIER["revision_id"]: {"shape": "claim", "contract": TIMED_LYRICS},
    SEQUENCE["revision_id"]: {"shape": "claim", "contract": ("archive.claim/chord_sequence", "1")},
}


def refs(*pairs) -> list[dict]:
    return [{"role": role, **value} for role, value in pairs]


def claim(payload: dict, *pairs, agent: str = OPERATOR["id"]) -> dict:
    return {"agent": agent, "payload": payload, "refs": refs(*pairs)}


def s(seconds: float) -> int:
    return int(seconds * RATE)


# An invented lyric, as a person would type it: two verses' worth of lines.
LYRIC = "Paper boats on the river\r\nCarry the morning away\r\n\r\nAll the lanterns are singing\n"
LRC = "[ti:Untitled]\n[00:12.00]Paper boats on the river\n[00:15.50]Carry <00:16.10>the morning away\n"


def wav_bytes() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"\x00\x00" * 800)
    return out.getvalue()


def zipped(members: dict[str, bytes], first: str | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        if first:
            z.writestr(first, members.pop(first), compress_type=zipfile.ZIP_STORED)
        for name, data in members.items():
            z.writestr(name, data)
    return out.getvalue()


def docx(extra: dict[str, bytes] | None = None) -> bytes:
    return zipped({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w:document/>", **(extra or {})})


# ---------------------------------------------------------------------------
# What the relay stages


class Staging(unittest.TestCase):
    def test_document_types_are_accepted(self):
        cases = {
            "text/plain": LYRIC.encode(),
            "text/plain; charset=utf-8": LRC.encode(),
            "text/markdown": b"# Song\n\nverse one\n",
            "application/json": b'{"releases": []}',
            "application/pdf": b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n",
            "application/rtf": b"{\\rtf1\\ansi hello}",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": docx(),
            "application/vnd.oasis.opendocument.text": zipped(
                {"mimetype": b"application/vnd.oasis.opendocument.text", "content.xml": b"<x/>"}, first="mimetype"),
            "application/vnd.recordare.musicxml+xml": b'<?xml version="1.0"?>\n<score-partwise version="4.0"/>',
            "application/vnd.recordare.musicxml": zipped({"META-INF/container.xml": b"<container/>"}),
            "application/x-guitar-pro": b"\x18FICHIER GUITAR PRO v5.10" + b"\x00" * 16,
            "audio/x-midi": b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x01\xe0",
            "image/png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 16,
            "image/jpeg": b"\xff\xd8\xff\xe0" + b"\x00" * 16,
        }
        for declared, data in cases.items():
            with self.subTest(declared=declared):
                media_type = check_document_bytes(declared, data)
                self.assertIn(media_type, documents.DOCUMENT_TYPES)
        self.assertEqual(check_document_bytes("audio/x-midi", cases["audio/x-midi"]), "audio/midi")

    def refused(self, declared: str, data: bytes, code: str):
        with self.assertRaises(DocumentError) as caught:
            check_document_bytes(declared, data)
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def test_audio_is_refused_whatever_it_is_declared_as(self):
        audio = wav_bytes()
        self.assertTrue(audio.startswith(b"RIFF"))  # the real writer made a WAV
        for declared in ("text/plain", "application/pdf", "audio/wav", "image/png"):
            with self.subTest(declared=declared):
                self.refused(declared, audio, "content_audio_or_video")

    def test_audio_and_video_signatures_are_refused(self):
        for data in (b"\x00\x00\x00\x20ftypisom" + b"\x00" * 32, b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 32,
                     b"fLaC\x00\x00\x00\x22", b"OggS\x00\x02", b"ID3\x04\x00", b"\xff\xfb\x90\x64",
                     b"\xff\xf1\x50\x80", b"\x1aE\xdf\xa3\x01", b"FORM\x00\x00\x00\x10AIFF",
                     b"\x47" + b"\x00" * 187 + b"\x47" + b"\x00" * 10):
            with self.subTest(data=data[:12]):
                self.refused("text/plain", data, "content_audio_or_video")

    def test_declared_audio_or_video_is_refused(self):
        self.refused("audio/mpeg", b"plain words", "content_audio_or_video")
        self.refused("video/mp4", b"plain words", "content_audio_or_video")

    def test_a_document_carrying_audio_inside_is_refused(self):
        bytes_ = docx({"word/media/sound.wav": wav_bytes()})
        self.refused("application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                     bytes_, "content_audio_or_video")

    def test_unknown_types_and_mismatches_are_refused(self):
        self.refused("application/zip", zipped({"a.txt": b"x"}), "media_type_unsupported")
        self.refused("text/html", b"<p>hi</p>", "media_type_unsupported")
        self.refused("application/pdf", b"not a pdf", "content_mismatch")
        self.refused("text/plain", b"\xc3\x28 broken", "content_mismatch")
        self.refused("text/plain", b"nul\x00inside", "content_mismatch")
        self.refused("application/json", b"{not json", "content_mismatch")
        self.refused("application/vnd.oasis.opendocument.text", docx(), "content_mismatch")
        # A zip that cannot be opened is a mismatch, never a pass.
        self.refused("application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                     b"PK\x03\x04 truncated", "content_mismatch")
        self.refused("text/plain", b"", "request_malformed")

    def test_size_is_bounded(self):
        with mock.patch.object(documents, "MAX_DOCUMENT_BYTES", 16):
            self.refused("text/plain", b"x" * 17, "body_too_large")
            self.assertEqual(check_document_bytes("text/plain", b"x" * 16), "text/plain")

    def test_only_the_operator_stages(self):
        body = {"media_type": "text/plain", "content": base64.b64encode(LYRIC.encode()).decode(),
                "filename": "lyrics.txt"}
        media_type, data, filename = check_stage_request(body, OPERATOR["id"], OPERATOR["id"])
        self.assertEqual((media_type, data, filename), ("text/plain", LYRIC.encode(), "lyrics.txt"))
        with self.assertRaises(DocumentError) as caught:
            check_stage_request(body, BANDMATE["id"], OPERATOR["id"])
        self.assertEqual(caught.exception.code, "operator_only")

    def test_malformed_stage_requests_are_refused(self):
        good = {"media_type": "text/plain", "content": base64.b64encode(b"words").decode()}
        for bad in ({**good, "content": "not base64!"}, {**good, "filename": "../etc/passwd"},
                    {**good, "extra": 1}, {"content": good["content"]}, {**good, "media_type": 3}):
            with self.subTest(bad=bad), self.assertRaises(DocumentError) as caught:
                check_stage_request(bad, OPERATOR["id"], OPERATOR["id"])
            self.assertEqual(caught.exception.code, "request_malformed")


def staged_batch(media_type: str = "text/plain", describe: bool = True) -> tuple[dict, list[dict]]:
    digest = "sha256:" + "ab" * 32
    manifest = {"contents": [{"digest": digest, "size": 10, "annex_key": "k", "staging_remote": "relay"}]}
    drafts = [{"shape": "artifact", "object_id": LYRICS["id"], "payload": {"content": {"digest": digest, "size": 10}}}]
    if describe:
        drafts.append({"shape": "claim", "object_id": pin(0x99)["id"],
                       "contracts": [{"id": DOCUMENT_SOURCE[0], "version": DOCUMENT_SOURCE[1],
                                      "digest": "sha256:" + "00" * 32}],
                       "payload": {"media_type": media_type},
                       "refs": [{"role": "document", "id": LYRICS["id"]}]})
    return manifest, drafts


class Admission(unittest.TestCase):
    STAGED = {"sha256:" + "ab" * 32: "text/plain"}

    def test_a_staged_and_described_document_passes(self):
        check_document_admission(*staged_batch(), self.STAGED)

    def test_content_the_relay_did_not_stage_is_refused(self):
        with self.assertRaises(DocumentError) as caught:
            check_document_admission(*staged_batch(), {})
        self.assertEqual(caught.exception.code, "content_not_relayed")

    def test_staged_content_needs_its_document_source_in_the_batch(self):
        for batch in (staged_batch(describe=False), staged_batch(media_type="application/pdf")):
            with self.subTest(), self.assertRaises(DocumentError) as caught:
                check_document_admission(*batch, self.STAGED)
            self.assertEqual(caught.exception.code, "content_not_described")


class GoogleDocs(unittest.TestCase):
    def test_every_view_of_a_doc_has_one_source_url(self):
        doc_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz_-0123456789"
        canonical = f"https://docs.google.com/document/d/{doc_id}"
        for url in (canonical, canonical + "/edit", canonical + "/edit?tab=t.0#heading=h.x",
                    canonical + "/export?format=txt"):
            self.assertEqual(google_doc_url(url), canonical)
        for url in ("https://docs.google.com/spreadsheets/d/" + doc_id, "http://docs.google.com/document/d/" + doc_id):
            with self.assertRaises(DocumentError):
                google_doc_url(url)


# ---------------------------------------------------------------------------
# archive.claim/document_source version 1


GDOC = "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz_-0123456789"


def google_export() -> dict:
    """A dated plain-text export of a Google Doc of the operator's lyrics."""
    return claim({"media_type": "text/plain", "kinds": ["chords", "lyrics"], "url": GDOC,
                  "retrieved_at": "2026-10-18T09:30:00Z", "filename": "Untitled song.txt"},
                 ("document", LYRICS), ("work", WORK))


def transcript_source() -> dict:
    """An ASR transcript: a document made by a run from the recording."""
    return claim({"media_type": "text/plain", "kinds": ["transcript"]},
                 ("document", TRANSCRIPT), ("derived_from", MIX), ("run", RUN), agent="pkg:generic/asr@1")


class DocumentSource(unittest.TestCase):
    def check(self, c: dict) -> dict:
        return check_document_source(c, TARGETS)

    def bad(self, c: dict):
        with self.assertRaises(DocumentError):
            self.check(c)

    def test_contract_is_named(self):
        self.assertEqual(DOCUMENT_SOURCE, ("archive.claim/document_source", "1"))

    def test_accepted(self):
        self.check(google_export())
        self.check(transcript_source())
        self.check(claim({"media_type": "application/pdf"}, ("document", TAB)))

    def test_a_url_needs_its_date_and_a_google_doc_its_canonical_url(self):
        c = google_export()
        del c["payload"]["retrieved_at"]
        self.bad(c)
        c = google_export()
        c["payload"]["url"] = GDOC + "/edit"
        self.bad(c)
        c = google_export()
        c["payload"]["retrieved_at"] = "2026-02-30"
        self.bad(c)

    def test_a_transcript_names_its_run_and_its_audio(self):
        for role in ("run", "derived_from"):
            c = transcript_source()
            c["refs"] = [r for r in c["refs"] if r["role"] != role]
            with self.subTest(missing=role):
                self.bad(c)

    def test_malformed(self):
        for change in (
                lambda p, r: p.update(media_type="audio/wav"),
                lambda p, r: p.update(kinds=["lyrics", "chords"]),
                lambda p, r: p.update(kinds=["poem"]),
                lambda p, r: p.update(filename="dir/name.txt"),
                lambda p, r: p.update(text="Paper boats on the river"),
                lambda p, r: p.update(url="ftp://example.org/x"),
                lambda p, r: r.append({"role": "derived_from", **LYRICS}),
                lambda p, r: r.append({"role": "work", **MIX}),
                lambda p, r: r.append({"role": "run", **WORK}),
                lambda p, r: r.append({"role": "document", **TAB}),
                lambda p, r: r.append({"role": "subject", **MIX}),
                lambda p, r: r.append({"role": "derived_from", **pin(0x77)}),
        ):
            c = google_export()
            change(c["payload"], c["refs"])
            with self.subTest(c=c):
                self.bad(c)


# ---------------------------------------------------------------------------
# archive.claim/timed_lyrics version 1


def tapped() -> dict:
    """A person's timing of the lyric's first two lines, with the first
    line's words."""
    return claim({
        "sample_rate": RATE,
        "scheme": "text",
        "alignment": {"method": "manual"},
        "lines": [
            {"line": 1, "start_sample": s(12), "end_sample": s(15),
             "words": [{"word": 1, "start_sample": s(12), "end_sample": s(12.4)},
                       {"word": 2, "start_sample": s(12.4), "end_sample": s(13)},
                       {"word": 5, "start_sample": s(14), "end_sample": s(15)}]},
            {"line": 2, "start_sample": s(15.5), "end_sample": s(19)},
            {"line": 1, "start_sample": s(60), "end_sample": s(63)},   # sung again
        ],
    }, ("document", LYRICS), ("source", MIX), ("stream", MIX_STREAM), ("work", WORK))


def from_lrc() -> dict:
    """An LRC file's line starts, each held to the next line's start."""
    return claim({
        "sample_rate": RATE,
        "scheme": "lrc",
        "alignment": {"method": "lrc timestamps", "max_error_samples": 80},
        "hold_rule": "a line lasts until the next line's timestamp",
        "lines": [
            {"line": 2, "start_sample": s(12), "end_sample": s(15.5), "end": "held"},
            {"line": 3, "start_sample": s(15.5), "end_sample": s(19), "end": "held",
             "words": [{"word": 2, "start_sample": s(16.1), "end_sample": s(17), "end": "held"}]},
        ],
    }, ("document", LYRICS), ("source", MIX), ("stream", MIX_STREAM))


def asr() -> dict:
    """A run's placement of its own transcript, with its confidences."""
    return claim({
        "sample_rate": RATE, "scheme": "text", "alignment": {"method": "asr word timestamps"},
        "confidence_scale": "asr word probability 0..1",
        "lines": [{"line": 1, "start_sample": s(30), "end_sample": s(33), "confidence": "0.82",
                   "words": [{"word": 1, "start_sample": s(30), "end_sample": s(31), "confidence": "0.9"}]}],
    }, ("document", TRANSCRIPT), ("source", MIX), ("stream", MIX_STREAM), ("run", RUN), agent="pkg:generic/asr@1")


class TimedLyrics(unittest.TestCase):
    def check(self, c: dict) -> dict:
        return check_timed_lyrics(c, TARGETS)

    def bad(self, c: dict):
        with self.assertRaises(DocumentError):
            self.check(c)

    def test_contract_is_named(self):
        self.assertEqual(TIMED_LYRICS, ("archive.claim/timed_lyrics", "1"))

    def test_accepted(self):
        self.check(tapped())
        self.check(from_lrc())
        self.check(asr())
        c = tapped()
        c["refs"].append({"role": "corrects", **EARLIER})
        self.check(c)

    def test_the_claim_holds_no_word_of_the_lyric(self):
        text = json.dumps(tapped())
        for word in ("Paper", "boats", "river", "lanterns"):
            self.assertIn(word, LYRIC)       # the anchor: the words exist
            self.assertNotIn(word, text)     # and the claim does not copy them
        for field in ("text", "words_text", "lyric"):
            c = tapped()
            c["payload"]["lines"][0][field] = "Paper boats on the river"
            with self.subTest(field=field):
                self.bad(c)
        c = tapped()
        c["payload"]["lines"][0]["words"][0]["text"] = "Paper"
        self.bad(c)

    def test_held_ends_and_their_rule_go_together(self):
        c = from_lrc()
        del c["payload"]["hold_rule"]
        self.bad(c)
        c = tapped()
        c["payload"]["hold_rule"] = "until the next line"
        self.bad(c)
        c = from_lrc()
        c["payload"]["lines"][0]["end"] = "guessed"
        self.bad(c)

    def test_lines_and_words_keep_their_order_and_bounds(self):
        def change(f):
            c = tapped()
            f(c["payload"]["lines"])
            return c
        for c in (
                change(lambda ls: ls.reverse()),
                change(lambda ls: ls[0]["words"].reverse()),
                change(lambda ls: ls[0]["words"][1].update(word=1)),
                change(lambda ls: ls[0]["words"][1].update(start_sample=s(12.2))),
                change(lambda ls: ls[0]["words"][2].update(end_sample=s(16))),
                change(lambda ls: ls[0]["words"][0].update(start_sample=s(11))),
                change(lambda ls: ls[1].update(end_sample=ls[1]["start_sample"])),
                change(lambda ls: ls[2].update(end_sample=s(241))),
                change(lambda ls: ls[0].update(line=0)),
                change(lambda ls: ls[0].update(line=True)),
                change(lambda ls: ls[0].update(words=[])),
                change(lambda ls: ls.clear()),
        ):
            with self.subTest(lines=c["payload"]["lines"]):
                self.bad(c)

    def test_overlapping_lines_are_two_voices(self):
        c = tapped()
        c["payload"]["lines"].insert(1, {"line": 4, "start_sample": s(14), "end_sample": s(18)})
        self.check(c)

    def test_confidence_rides_with_its_scale(self):
        c = asr()
        del c["payload"]["confidence_scale"]
        self.bad(c)
        c = asr()
        del c["payload"]["lines"][0]["words"][0]["confidence"]
        self.bad(c)
        c = asr()
        c["payload"]["lines"][0]["confidence"] = 0.82
        self.bad(c)

    def test_references(self):
        def change(f):
            c = tapped()
            f(c)
            return c
        for c in (
                change(lambda c: c["refs"].pop(0)),                                       # no document
                change(lambda c: c["refs"].append({"role": "document", **TAB})),          # two documents
                change(lambda c: c["refs"].append({"role": "corrects", **SEQUENCE})),     # not timed lyrics
                change(lambda c: c["refs"].append({"role": "run", **WORK})),
                change(lambda c: c["refs"].append({"role": "analysed", **OTHER})),
                change(lambda c: c["refs"].__setitem__(1, {"role": "source", **OTHER})),  # stream of another
                change(lambda c: c["refs"].__setitem__(0, {"role": "document", **MIX})),  # on itself
                change(lambda c: c["refs"].__setitem__(3, {"role": "work", **RECORDING_ENTITY})),
                change(lambda c: c["payload"].update(sample_rate=44100)),
                change(lambda c: c["payload"].update(scheme="webvtt")),
                change(lambda c: c["payload"].pop("alignment")),
                change(lambda c: c["payload"].update(alignment={"method": "manual", "max_error_samples": -1})),
                change(lambda c: c["payload"].update(document_url=GDOC)),
        ):
            with self.subTest(c=c):
                self.bad(c)


class Lines(unittest.TestCase):
    def test_text_lines_and_words(self):
        lines = document_lines(LYRIC.encode(), "text")
        self.assertEqual(len(lines), 4)          # CR LF, LF, and no line after the final break
        self.assertEqual(lines[0], ["Paper", "boats", "on", "the", "river"])
        self.assertEqual(lines[2], [])
        self.assertEqual(lines[3][:2], ["All", "the"])   # a no-break space separates words
        self.assertEqual(document_lines(b"\xef\xbb\xbfone\rtwo", "text"), [["one"], ["two"]])

    def test_lrc_tags_are_not_words(self):
        lines = document_lines(LRC.encode(), "lrc")
        self.assertEqual(lines[1], ["Paper", "boats", "on", "the", "river"])
        self.assertEqual(lines[2], ["Carry", "the", "morning", "away"])
        self.assertEqual(document_lines(LRC.encode(), "text")[2][0], "[00:15.50]Carry")

    def test_locators_must_be_in_the_document(self):
        check_locators(tapped(), LYRIC.encode())
        check_locators(from_lrc(), LRC.encode())
        c = tapped()
        c["payload"]["lines"][1]["line"] = 5
        with self.assertRaises(DocumentError):
            check_locators(c, LYRIC.encode())
        c = tapped()
        c["payload"]["lines"][0]["words"][2]["word"] = 6
        with self.assertRaises(DocumentError):
            check_locators(c, LYRIC.encode())
        with self.assertRaises(DocumentError):
            check_locators(tapped(), wav_bytes())

    def test_the_view_joins_words_from_the_document(self):
        view = lyric_view(tapped(), LYRIC.encode())
        self.assertEqual(view[0]["text"], "Paper boats on the river")
        self.assertEqual([w["text"] for w in view[0]["words"]], ["Paper", "boats", "river"])
        self.assertEqual(view[2]["text"], view[0]["text"])
        self.assertEqual(lyric_view(from_lrc(), LRC.encode())[1]["words"][0]["text"], "the")


# ---------------------------------------------------------------------------
# archive.claim/sensitivity version 1


def label(name: str, *subjects: dict, agent: str = OPERATOR["id"]) -> dict:
    return claim({"label": name}, *(("subject", subject) for subject in subjects), agent=agent)


class Sensitivity(unittest.TestCase):
    def check(self, c: dict, agent_kind: str | None = None) -> dict:
        return check_sensitivity(c, TARGETS, agent_kind)

    def test_contract_is_named(self):
        self.assertEqual(SENSITIVITY, ("archive.claim/sensitivity", "1"))

    def test_the_app_settings_are_tlp_labels(self):
        self.assertEqual({setting: LABELS[tlp] for setting, tlp in APP_SETTINGS.items()},
                         {"operator": "private", "circle": "circle", "published": "distribution"})
        self.assertEqual(list(LABELS.values()), [c for c in CLASSES if c not in ("open",)])

    def test_accepted(self):
        self.check(label("AMBER", MIX, LYRICS, WORK), "person")
        self.check(claim({"label": "RED", "reason": "a private conversation between takes"}, ("subject", MIX)))

    def test_malformed(self):
        for c in (label("PINK", MIX), label("amber", MIX), label("AMBER"), label("AMBER", MIX, MIX),
                  label("AMBER", pin(0x55)), claim({"label": "RED"}, ("subject", MIX), ("run", RUN)),
                  claim({"label": "RED", "reason": ""}, ("subject", MIX)),
                  claim({"label": "RED", "destination": "x"}, ("subject", MIX))):
            with self.subTest(c=c), self.assertRaises(DocumentError):
                self.check(c)
        with self.assertRaises(DocumentError):
            self.check(label("RED", MIX), "software")


# ---------------------------------------------------------------------------
# archive.claim/rights version 1


def in_copyright(*subjects: dict, holder: dict = LABEL_CO) -> dict:
    return claim({"statement": "InC", "jurisdiction": "US", "from": "2009"},
                 *(("subject", subject) for subject in subjects), ("holder", holder))


def paid_tab(licensee: dict = OPERATOR) -> dict:
    return claim({"statement": "personal-use-licence", "from": str(TODAY.year),
                  "text": "for personal, non-commercial use only", "document_url": "https://tabs.example/receipt/1",
                  "retrieved_at": TODAY.isoformat()},
                 ("subject", TAB), ("licensee", licensee), ("document", RECEIPT))


class Rights(unittest.TestCase):
    def check(self, c: dict) -> dict:
        return check_rights(c, TARGETS)

    def test_contract_is_named(self):
        self.assertEqual(RIGHTS, ("archive.claim/rights", "1"))
        self.assertEqual(statement_uri("InC-NC"), "http://rightsstatements.org/vocab/InC-NC/1.0/")
        with self.assertRaises(DocumentError):
            statement_uri("personal-use-licence")

    def test_accepted(self):
        self.check(in_copyright(WORK, MIX, RECORDING_ENTITY))
        self.check(paid_tab())
        self.check(claim({"statement": "NoC-US"}, ("subject", OTHER)))

    def test_malformed(self):
        def change(f, base=paid_tab):
            c = base()
            f(c)
            return c
        for c in (
                change(lambda c: c["refs"].pop(1)),                                         # no licensee
                change(lambda c: c["refs"].append({"role": "licensee", **OPERATOR}), in_copyright_work),
                change(lambda c: c["refs"].__setitem__(1, {"role": "licensee", **LABEL_CO})),
                change(lambda c: c["refs"].pop(2)),                                         # url without document
                change(lambda c: c["payload"].update(statement="CC-BY")),
                change(lambda c: c["payload"].update(jurisdiction="us")),
                change(lambda c: c["payload"].update(until="2001")),                       # ends before it begins
                change(lambda c: c["payload"].update(until="2026-13")),
                change(lambda c: c["refs"].append({"role": "subject", **BANDMATE})),        # a person has no rights
                change(lambda c: c["refs"].append({"role": "subject", **EARLIER})),         # nor a claim
                change(lambda c: c["refs"].append({"role": "holder", **WORK}), in_copyright_work),
                change(lambda c: c["payload"].update(text="")),
        ):
            with self.subTest(c=c), self.assertRaises(DocumentError):
                self.check(c)


def in_copyright_work() -> dict:
    return in_copyright(WORK)


# ---------------------------------------------------------------------------
# Reach


AT = iter(range(1, 10_000))


def record(object_id: str, shape: str, payload: dict | None = None, refs_: list | None = None,
           agent: str = OPERATOR["id"], contract=None, kind=None, supersedes=()) -> dict:
    n = next(AT)
    r = {"object_id": object_id, "revision_id": "sha256:" + f"{n:064x}", "shape": shape, "agent": agent,
         "at": f"2026-10-18T10:{n // 60 % 60:02d}:{n % 60:02d}Z", "payload": payload or {}, "refs": refs_ or [],
         "supersedes": [{"role": "supersedes", "id": object_id, "revision_id": s} for s in supersedes]}
    if contract:
        r["contract"] = contract
    if kind:
        r["kind"] = kind
    return r


def as_record(c: dict, object_n: int, contract, supersedes=()) -> dict:
    return record(pin(object_n)["id"], "claim", c["payload"], c["refs"], c["agent"], contract, supersedes=supersedes)


def world() -> list[dict]:
    """A recording, its lyrics, a lead-sheet-like claim pinning both, a Work,
    and the people: the subjects every reach test starts from."""
    return [
        record(OPERATOR["id"], "entity", kind="person"),
        record(BANDMATE["id"], "entity", kind="person"),
        record(SINGER["id"], "entity", kind="person"),
        record(WORK["id"], "entity", kind="musical_work"),
        record(MIX["id"], "artifact"),
        record(LYRICS["id"], "artifact"),
        record(TRANSCRIPT["id"], "artifact"),
        record(TAB["id"], "artifact"),
        as_record(tapped(), 0x60, TIMED_LYRICS),
        as_record(claim({"media_type": "text/plain"}, ("document", LYRICS), ("work", WORK)), 0x61, DOCUMENT_SOURCE),
        as_record(in_copyright(WORK, MIX, LYRICS, TAB, holder=OPERATOR), 0x62, RIGHTS),
    ]


TIMING = pin(0x60)["id"]


def shown(records, object_id, viewer=BANDMATE, audience="circle") -> bool:
    return may_show(object_id, viewer["id"], audience, records, OPERATOR["id"], TODAY)[0]


class Reach(unittest.TestCase):
    def test_closure_follows_pins_and_what_a_document_was_made_from(self):
        records = world() + [as_record(transcript_source(), 0x70, DOCUMENT_SOURCE)]
        found = closure(TIMING, records)
        self.assertEqual(set(found) >= {TIMING, LYRICS["id"], MIX["id"], WORK["id"]}, True)
        self.assertIn(MIX["id"], closure(TRANSCRIPT["id"], records))

    def test_unlabelled_own_work_reaches_the_circle_and_published(self):
        records = world()
        self.assertTrue(shown(records, TIMING))
        self.assertTrue(shown(records, TIMING, audience="open"))
        self.assertEqual(reach(TIMING, records, OPERATOR["id"], TODAY).furthest, "distribution")

    def test_a_label_on_anything_pinned_limits_what_pins_it(self):
        records = world() + [as_record(label("RED", MIX), 0x63, SENSITIVITY)]
        self.assertFalse(shown(records, TIMING))
        self.assertFalse(shown(records, MIX["id"]))
        self.assertTrue(shown(records, LYRICS["id"]))            # the lyrics pin no recording
        self.assertTrue(shown(records, TIMING, viewer=OPERATOR, audience="private"))
        records = world() + [as_record(label("AMBER", MIX), 0x63, SENSITIVITY)]
        self.assertTrue(shown(records, TIMING))
        self.assertFalse(shown(records, TIMING, audience="open"))

    def test_a_transcript_keeps_the_label_of_what_it_transcribed(self):
        records = world() + [as_record(transcript_source(), 0x70, DOCUMENT_SOURCE),
                             as_record(label("RED", MIX), 0x63, SENSITIVITY),
                             as_record(in_copyright(TRANSCRIPT, holder=OPERATOR), 0x71, RIGHTS)]
        self.assertFalse(shown(records, TRANSCRIPT["id"]))
        without = [r for r in records if r.get("contract") != DOCUMENT_SOURCE or r["object_id"] != pin(0x70)["id"]]
        self.assertTrue(shown(without, TRANSCRIPT["id"]))        # the anchor: the link is what limits it

    def test_only_the_person_lifts_their_limit(self):
        singers = as_record(label("RED", MIX, agent=SINGER["id"]), 0x63, SENSITIVITY)
        operators_lift = as_record(label("CLEAR", MIX), 0x64, SENSITIVITY)
        self.assertFalse(shown(world() + [singers, operators_lift], TIMING))
        singers_lift = as_record(label("AMBER", MIX, agent=SINGER["id"]), 0x65, SENSITIVITY)
        self.assertTrue(shown(world() + [singers, operators_lift, singers_lift], TIMING))
        # An earlier claim does not lift a later one: order is by Door's time.
        early = dict(singers_lift, at="2000-01-01T00:00:00Z")
        self.assertFalse(shown(world() + [singers, early], TIMING))

    def test_a_superseded_label_no_longer_counts(self):
        red = as_record(label("RED", MIX), 0x63, SENSITIVITY)
        revised = record(red["object_id"], "claim", {"label": "AMBER"}, red["refs"], contract=SENSITIVITY,
                         supersedes=[red["revision_id"]])
        self.assertFalse(shown(world() + [red], TIMING))
        self.assertTrue(shown(world() + [red, revised], TIMING))

    def test_unknown_rights_never_widen_past_service(self):
        records = [r for r in world() if r.get("contract") != RIGHTS]
        self.assertTrue(shown(records, TIMING))                         # circle: no rights claim needed
        self.assertFalse(shown(records, TIMING, audience="open"))
        found = reach(TIMING, records, OPERATOR["id"], TODAY)
        self.assertEqual(found.rights, "service")
        self.assertTrue(any("no rights claim" in why for why in found.why))

    def test_someone_elses_copyright_stops_at_service(self):
        records = [r for r in world() if r.get("contract") != RIGHTS] + [
            as_record(in_copyright(WORK, MIX, LYRICS, holder=LABEL_CO), 0x62, RIGHTS)]
        self.assertTrue(shown(records, TIMING))
        self.assertFalse(shown(records, TIMING, audience="open"))

    def test_a_personal_use_licence_reaches_the_circle_and_no_further(self):
        records = world() + [as_record(paid_tab(), 0x66, RIGHTS)]
        found = reach(TAB["id"], records, OPERATOR["id"], TODAY)
        self.assertEqual((found.rights, found.furthest), ("circle", "circle"))
        self.assertTrue(shown(records, TAB["id"]))                      # a bandmate, though the operator holds it
        self.assertTrue(shown(records, TAB["id"], viewer=SINGER))
        self.assertFalse(shown(records, TAB["id"], audience="service"))
        self.assertFalse(shown(records, TAB["id"], viewer=BANDMATE, audience="open"))
        self.assertTrue(shown(records, TAB["id"], viewer=OPERATOR, audience="private"))

    def test_rights_never_keep_anything_from_the_circle(self):
        # The circle is private: every statement, in or out of its terms,
        # still reaches it. Anchor: each one does limit something beyond it.
        later = (TODAY + timedelta(days=400)).strftime("%Y")
        base = [r for r in world() if r.get("contract") != RIGHTS]
        statements = [in_copyright(WORK, MIX, LYRICS, holder=LABEL_CO),
                      claim({"statement": "UND"}, *(("subject", x) for x in (WORK, MIX, LYRICS))),
                      claim({"statement": "NoC-US", "from": later}, *(("subject", x) for x in (WORK, MIX, LYRICS)))]
        for statement in statements:
            records = base + [as_record(statement, 0x68, RIGHTS)]
            with self.subTest(statement=statement["payload"]["statement"]):
                self.assertFalse(shown(records, TIMING, audience="open"))
                self.assertTrue(shown(records, TIMING))
                self.assertTrue(shown(records, MIX["id"]))
        # Sensitivity still keeps it from the circle.
        records = base + [as_record(statements[0], 0x68, RIGHTS), as_record(label("RED", MIX), 0x63, SENSITIVITY)]
        self.assertFalse(shown(records, TIMING))

    def test_terms_are_read_on_today(self):
        later = (TODAY + timedelta(days=400)).strftime("%Y")
        earlier = (TODAY - timedelta(days=400)).strftime("%Y")
        base = [r for r in world() if r.get("contract") != RIGHTS]
        pd = lambda **terms: as_record(claim({"statement": "NoC-US", **terms},  # noqa: E731
                                             *(("subject", x) for x in (WORK, MIX, LYRICS))), 0x67, RIGHTS)
        self.assertTrue(shown(base + [pd()], TIMING, audience="open"))
        self.assertFalse(shown(base + [pd(**{"from": later})], TIMING, audience="open"))
        self.assertFalse(shown(base + [pd(until=earlier)], TIMING, audience="open"))

    def test_audience_must_be_a_class(self):
        with self.assertRaises(DocumentError):
            may_show(TIMING, BANDMATE["id"], "friends", world(), OPERATOR["id"], TODAY)


if __name__ == "__main__":
    unittest.main()
