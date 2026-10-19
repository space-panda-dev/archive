"""Door and World agree on specification 0.0.10's twenty-four contracts (D-18).

As test_door_agreement.py does for 0.0.9's four: each case starts from a
record the real Door admitted into the published fixture archive
(fixture/build.py), changes one thing, and gives the result to both. Door
judges it by `door admit --dry-run`. World judges it with the check its
module states for the contract, given the pinned records read back from
`archive export`: harmony, form, tonality, judgments, lead_sheets, documents,
notes, catalogue, circle, and exports. Every case states the expected
verdict, and each contract's cases include accepted and refused ones, so the
two cannot agree by both being wrong or by both refusing everything.

The cases aim at D-18's profile rules, which Door evaluates in its own
modules and World in its own code. The cross-claim checks D-18 leaves to
World (a section or entry index within a pinned form or sequence, a placed
bar's time signature, a tune fitting its meter, a melody span within its
notes range) are not compared here: Door does not refuse them.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from world import catalogue, circle, documents, exports, form, harmony, judgments, lead_sheets, notes, tonality
from world.tests.test_door_agreement import (
    BY,
    ROOT,
    _canonical,
    Agreement,
    Change,
    both,
    delete_in,
    drop_role,
    keep,
    payload,
    pin,
    repin,
    set_in,
    whole,
)
from world.tests.test_conformance import build_fixture_archive, integration_binaries, remove_tree


def agent(key: str) -> Change:
    """Assert the record as the fixture Entity `key`."""
    def change(record: dict[str, Any], test: "Agreement010") -> None:
        record["agent"] = test.pinned(key)["id"]
    return change


def as_tool(record: dict[str, Any], _: Any) -> None:
    record["agent"] = "pkg:generic/archive-door-agreement-test@0"


def at(*path: Any) -> Callable[[dict[str, Any]], Any]:
    def walk(p: dict[str, Any]) -> Any:
        for step in path:
            p = p[step]
        return p
    return walk


def append_to(path: Callable[[dict[str, Any]], Any], value: Any) -> Change:
    return payload(lambda p: path(p).append(copy.deepcopy(value)))


def syllable(index: int, *rest: str) -> Callable[[dict[str, Any]], Any]:
    """The fixture's first word's syllable `index` (0.0.10 amendment)."""
    return at("lines", 0, "words", 0, "syllables", index, *rest)


def carried_by(key: str) -> Change:
    """Name the fixture claim `key` as what carries the first syllable."""
    def change(record: dict[str, Any], test: "Agreement010") -> None:
        syllable(0, "carried_by")(record["payload"])["notes"] = test.pinned(key)["revision_id"]
    return change


Case = tuple[str, str, Change, bool]

HARMONY: list[Case] = [
    ("chords-v2", "chord stacks version 2 as admitted, with a bass-only reading and a stem", keep, True),
    ("chords-v2", "a bass-only reading with a voicing", set_in(at("spans", 0, "readings", 0), "voicing", [40]), False),
    ("chords-v2", "a stem beside an analysed Artifact", pin("analysed", "take-a"), False),
    ("chords-v2", "a claim derived from and correcting one revision", pin("corrects", "chords"), False),
    ("chords-v2", "a correction of another claim beside the derivation", pin("corrects", "chords-correction"), True),
    ("chords-v2", "a span past the stream", set_in(at("spans", 1), "end_sample", 4001), False),
    ("sequence-v2", "chord sequence version 2 as admitted", keep, True),
    ("sequence-v2", "tones out of order",
     set_in(at("entries", 0, "readings", 0), "tones", [7, 4]), False),
    ("sequence-v2", "a sequence derived from and correcting one revision", pin("corrects", "sequence"), False),
]

METER: list[Case] = [
    ("meter-written", "the written meter as admitted", keep, True),
    ("meter-written", "a grouping that does not add up", set_in(at("layers", 0, "bars", 4), "grouping", [3, 2]), False),
    ("meter-written", "a section that goes back", set_in(at("layers", 0, "bars", 2), "section", 0), False),
    ("meter-written", "a bar in the same section as the one before", set_in(at("layers", 0, "bars", 2), "section", 1), True),
    ("meter-written", "an entry held over a bar line and listed again", set_in(at("layers", 0, "bars", 1, "chords", 0), "entry", 0), True),
    ("meter-written", "an entry before the previous bar's",
     both(set_in(at("layers", 0, "bars", 0, "chords", 0), "entry", 1),
          set_in(at("layers", 0, "bars", 1, "chords", 0), "entry", 0)), False),
    ("meter-written", "a chord at the bar's end",
     set_in(at("layers", 0, "bars", 1, "chords", 1), "at", {"count": 4, "unit": 4}), False),
    ("meter-written", "a chord at the same offset as the one before",
     set_in(at("layers", 0, "bars", 1, "chords", 1), "at", {"count": 0, "unit": 4}), False),
    ("meter-written", "a triplet offset", set_in(at("layers", 0, "bars", 1, "chords", 1), "at", {"count": 1, "unit": 12}), True),
    ("meter-written", "a modulation of the first bar", set_in(at("modulations", 0), "bar", 0), False),
    ("meter-written", "a modulation past the last bar", set_in(at("modulations", 0), "bar", 5), False),
    ("meter-written", "a modulation of a layer that does not exist", set_in(at("modulations", 0), "layer", 1), False),
    ("meter-written", "a regrouping whose pulse does not move",
     set_in(whole, "modulations", [{"layer": 0, "bar": 4, "kind": "regrouping",
                                    "pulse_before": {"denominator": 4}, "pulse_after": {"denominator": 4, "dots": 0}}]), False),
    ("meter-written", "two modulations out of order",
     set_in(whole, "modulations", [{"layer": 0, "bar": 4, "kind": "equation", "before": {"denominator": 4},
                                    "after": {"denominator": 8}},
                                   {"layer": 0, "bar": 2, "kind": "equation", "before": {"denominator": 4},
                                    "after": {"denominator": 8}}]), False),
    ("meter-written", "a document bar that goes back",
     both(pin("document", "chart"), set_in(at("layers", 0, "bars", 0), "locator", {"bar": 2}),
          set_in(at("layers", 0, "bars", 1), "locator", {"bar": 1})), False),
    ("meter-placed", "the placed meter as admitted", keep, True),
    ("meter-placed", "a placed bar past the stream", set_in(at("layers", 0, "bars", 1), "end_sample", 4001), False),
    ("meter-placed", "placed bars that overlap", set_in(at("layers", 0, "bars", 1), "start_sample", 1999), False),
    ("meter-placed", "a placement of a timed meter", repin("meter", "meter-chart"), False),
    ("meter-placed", "a correction of a timed meter", pin("corrects", "meter-chart"), True),
]

FORM: list[Case] = [
    ("form", "the form as admitted", keep, True),
    ("form", "sections located by line and by bar", set_in(at("sections", 2), "locator", {"bar": 3}), False),
    ("form", "a section located before the one before it", set_in(at("sections", 2), "locator", {"line": 1}), False),
    ("form", "endings that give pass 1 twice", set_in(at("repeats", 0, "endings", 1), "passes", [1]), False),
    ("form", "endings with a gap", set_in(at("repeats", 0, "endings", 0), "last", 1), False),
    ("form", "an ending with no body before it",
     set_in(at("repeats", 0), "endings", [{"first": 1, "last": 2, "passes": [1]}, {"first": 3, "last": 3, "passes": [2]}]), False),
    ("form", "a coda inside the repeat", set_in(at("jumps", 0), "coda", 2), False),
    ("form", "a da capo to the end", set_in(at("jumps", 0), "then", "end"), False),
    ("form", "a da capo to the end, without coda fields",
     set_in(whole, "jumps", [{"after": 3, "kind": "da_capo", "then": "end"}]), True),
    ("form", "a dal segno landing inside the repeat",
     set_in(whole, "jumps", [{"after": 3, "kind": "dal_segno", "to": 2, "then": "end"}]), False),
    ("form", "a dal segno to the repeat's first section",
     set_in(whole, "jumps", [{"after": 3, "kind": "dal_segno", "to": 1, "then": "end"}]), True),
    ("form", "jumps out of written order",
     set_in(whole, "jumps", [{"after": 3, "kind": "da_capo", "then": "end"}, {"after": 0, "kind": "da_capo", "then": "end"}]), False),
    ("form-spans", "the form spans as admitted", keep, True),
    ("form-spans", "form spans that overlap", set_in(at("spans", 1), "start_sample", 999), False),
]

KEYS: list[Case] = [
    ("key-sequence", "the key sequence as admitted", keep, True),
    ("key-sequence", "a spelling that does not sound its class",
     set_in(at("regions", 0, "readings", 0), "spelling", ["C#", "D", "E", "F", "G", "A", "B"]), False),
    ("key-sequence", "an enharmonic spelling",
     set_in(at("regions", 0, "readings", 0), "spelling", ["B#", "D", "Fb", "E#", "G", "A", "Cb"]), True),
    ("key-sequence", "a spelling one name short",
     set_in(at("regions", 0, "readings", 0), "spelling", ["C", "D", "E", "F", "G", "A"]), False),
    ("key-sequence", "a region that ends before it starts", set_in(at("regions", 0), "first", 2), False),
    ("key-sequence", "regions that overlap", set_in(at("regions", 1), "first", 1), False),
    ("key-sequence", "a sequence derived from and correcting one revision",
     both(pin("derived_from", "sequence"), pin("corrects", "key-sequence")), True),
    ("key-regions", "the key regions as admitted", keep, True),
    ("key-regions", "a spelling that does not sound its class",
     set_in(at("regions", 0, "readings", 0), "spelling", ["C", "D", "E", "F", "G", "A", "Bb"]), False),
    ("key-regions", "regions that overlap", set_in(at("regions", 1), "start_sample", 1999), False),
    ("key-regions", "a region past the stream", set_in(at("regions", 1), "end_sample", 4001), False),
    ("tuning", "the tuning as admitted", keep, True),
    ("tuning", "a drift span past the stream", set_in(at("drift", 0), "end_sample", 4001), False),
    ("tuning", "a tuning without drift", delete_in(whole, "drift"), True),
]

JUDGMENTS: list[Case] = [
    ("judgment-v2", "a judgment of a span as admitted", keep, True),
    ("judgment-v2", "a span that ends before it starts", set_in(at("span"), "start_sample", 2000), False),
    ("judgment-v2", "a span past the judged claim's stream", set_in(at("span"), "end_sample", 4001), False),
    ("judgment-v2", "a span at another rate", set_in(whole, "sample_rate", 16000), False),
    ("judgment-v2", "a span of a claim that is not timed", repin("assessment_of", "sequence-v2"), False),
    ("judgment-v2", "a withdrawal by the claim's own asserter", set_in(whole, "verdict", "withdrawn"), True),
    ("judgment-v2", "a withdrawal of a tool's claim by the operator",
     both(repin("assessment_of", "chords"), set_in(whole, "verdict", "withdrawn")), False),
    ("judgment-v2", "an affirmation of a tool's claim by the operator", repin("assessment_of", "chords"), True),
    ("judgment-v2", "a reading of a span",
     both(payload(lambda p: (p.pop("span"), p.pop("sample_rate"))), set_in(whole, "reading", {"reading": 0, "span": 1})), True),
    ("judgment-v2", "a reading a span does not have",
     both(payload(lambda p: (p.pop("span"), p.pop("sample_rate"))), set_in(whole, "reading", {"reading": 1, "span": 1})), False),
    ("judgment-v2", "a span the claim does not have",
     both(payload(lambda p: (p.pop("span"), p.pop("sample_rate"))), set_in(whole, "reading", {"reading": 0, "span": 2})), False),
    ("judgment-v2", "entries of a sequence",
     both(repin("assessment_of", "sequence-v2"), payload(lambda p: (p.pop("span"), p.pop("sample_rate"))),
          set_in(whole, "entries", {"first": 1, "last": 2})), True),
    ("judgment-v2", "entries past the sequence's last",
     both(repin("assessment_of", "sequence-v2"), payload(lambda p: (p.pop("span"), p.pop("sample_rate"))),
          set_in(whole, "entries", {"first": 1, "last": 3})), False),
    ("judgment-v2", "entries of a timed claim", both(payload(lambda p: (p.pop("span"), p.pop("sample_rate"))),
                                                     set_in(whole, "entries", {"first": 0, "last": 0})), False),
    ("judgment-v2", "entries in reverse",
     both(repin("assessment_of", "sequence-v2"), payload(lambda p: (p.pop("span"), p.pop("sample_rate"))),
          set_in(whole, "entries", {"first": 2, "last": 1})), False),
    ("annotation", "the mark as admitted", keep, True),
    ("annotation", "a mark past the stream", set_in(at("span"), "end_sample", 4001), False),
    ("annotation", "a mark at another rate", set_in(whole, "sample_rate", 16000), False),
    ("annotation", "a mark that ends before it starts", set_in(at("span"), "start_sample", 1000), False),
    ("annotation", "a mark by a tool", as_tool, False),
    ("annotation", "a mark by a band", agent("ensemble"), False),
    ("annotation", "a mark about a claim of the Work", repin("about", "sequence-v2"), False),
    ("annotation", "a mark about two claims of the recording", pin("about", "chords"), True),
    ("annotation", "a mark of a sequence's entries",
     both(drop_role("source"), drop_role("stream"), drop_role("about"), pin("sequence", "sequence-v2"),
          payload(lambda p: (p.pop("span"), p.pop("sample_rate"))), set_in(whole, "entries", {"first": 0, "last": 2})), True),
    ("annotation", "a mark of entries past the sequence's last",
     both(drop_role("source"), drop_role("stream"), drop_role("about"), pin("sequence", "sequence-v2"),
          payload(lambda p: (p.pop("span"), p.pop("sample_rate"))), set_in(whole, "entries", {"first": 0, "last": 3})), False),
    ("annotation", "a mark of a sequence naming its Work",
     both(drop_role("source"), drop_role("stream"), drop_role("about"), pin("sequence", "sequence-v2"), pin("work", "work-a"),
          payload(lambda p: (p.pop("span"), p.pop("sample_rate"))), set_in(whole, "entries", {"first": 0, "last": 0})), True),
    ("annotation", "a mark of a sequence naming another Work",
     both(drop_role("source"), drop_role("stream"), drop_role("about"), pin("sequence", "sequence-v2"), pin("work", "work-b"),
          payload(lambda p: (p.pop("span"), p.pop("sample_rate"))), set_in(whole, "entries", {"first": 0, "last": 0})), False),
    ("annotation", "a mark of a sequence about a claim of its Work",
     both(drop_role("source"), drop_role("stream"), repin("about", "key-sequence"), pin("sequence", "sequence-v2"),
          payload(lambda p: (p.pop("span"), p.pop("sample_rate"))), set_in(whole, "entries", {"first": 0, "last": 0})), True),
]

LEAD_SHEETS: list[Case] = [
    ("lead-sheet", "a person's lead sheet as admitted", keep, True),
    ("lead-sheet-band", "a band's lead sheet as admitted", keep, True),
    ("lead-sheet", "a lead sheet by a tool", as_tool, False),
    ("lead-sheet", "two parts of one name", set_in(at("parts", 1), "name", "Keys"), False),
    ("lead-sheet", "a part citing a document it does not pin", set_in(at("parts", 1), "document", 1), False),
    ("lead-sheet", "a credit that is not about the Work", repin("credits", "contribution-release"), False),
    ("lead-sheet", "a timed input without the lead sheet's source", pin("chords", "chords-v2"), False),
    ("lead-sheet", "a timed input counting the lead sheet's source",
     both(pin("chords", "chords-v2"), pin("source", "recording")), True),
    ("lead-sheet", "a person's lead sheet naming a band", pin("band", "ensemble"), False),
    ("lead-sheet", "lyric timing that places the lead sheet's lyrics on its source",
     both(pin("lyric_timing", "timed-lyrics"), pin("source", "recording")), True),
    ("lead-sheet", "lyric timing without the lyric document",
     both(pin("lyric_timing", "timed-lyrics"), pin("source", "recording"), drop_role("lyrics")), False),
    ("lead-sheet-band", "a band's lead sheet nobody adopted", drop_role("adopted_by"), False),
    ("lead-sheet-band", "a band's lead sheet asserted by a member instead of the band", agent("guest"), False),
    ("lead-sheet-band", "a band's lead sheet derived from two lead sheets", pin("derived_from", "lead-sheet-band"), True),
]

DOCUMENTS: list[Case] = [
    ("document-source", "the document's source as admitted", keep, True),
    ("document-source", "a file name with a slash", set_in(whole, "filename", "a/lyrics.txt"), False),
    ("document-source", "a file name of ..", set_in(whole, "filename", ".."), False),
    ("document-source", "a file name with a backslash", set_in(whole, "filename", "a\\lyrics.txt"), False),
    ("document-source", "a file name with dots inside", set_in(whole, "filename", "lyrics..v2.txt"), True),
    ("timed-lyrics", "the timed lyrics as admitted", keep, True),
    ("timed-lyrics", "a word outside its line", set_in(at("lines", 0, "words", 1), "end_sample", 2001), False),
    ("timed-lyrics", "words out of order", set_in(at("lines", 0, "words", 1), "word", 1), False),
    ("timed-lyrics", "lines out of order of start",
     set_in(whole, "lines", [{"line": 2, "start_sample": 2000, "end_sample": 4000},
                             {"line": 1, "start_sample": 0, "end_sample": 2000}]), False),
    ("timed-lyrics", "lines that overlap", set_in(at("lines", 1), "start_sample", 1500), True),
    ("timed-lyrics", "a line that ends before it starts", set_in(at("lines", 1), "end_sample", 2000), False),
    ("timed-lyrics", "a line past the stream", set_in(at("lines", 1), "end_sample", 4001), False),
    ("timed-syllables", "syllables carried by notes, as admitted", keep, True),
    ("timed-syllables", "syllables that overlap in time", set_in(syllable(1), "start_sample", 300), False),
    ("timed-syllables", "syllables whose characters overlap", set_in(syllable(1), "start_char", 2), False),
    ("timed-syllables", "an empty syllable", set_in(syllable(0), "end_char", 0), False),
    ("timed-syllables", "a syllable past its word", set_in(syllable(1), "end_sample", 1100), False),
    ("timed-syllables", "syllables out of order", set_in(syllable(1), "syllable", 1), False),
    ("timed-syllables", "carrying onsets out of order",
     set_in(syllable(1, "carried_by"), "onsets", [400, 0]), False),
    ("timed-syllables", "a syllable carried by a claim it does not pin", carried_by("notes"), False),
    ("timed-syllables", "a syllable carried by a claim it also pins",
     both(pin("notes", "notes"), carried_by("notes")), True),
    ("sensitivity", "the limit as admitted", keep, True),
    ("sensitivity", "a limit by a band", agent("ensemble"), False),
    ("sensitivity", "a limit by another person", agent("guest"), True),
    ("rights", "the rights statement as admitted", keep, True),
    ("rights", "a holder that is also a subject", pin("subject", "ensemble"), False),
]

NOTES: list[Case] = [
    ("notes", "the notes as admitted", keep, True),
    ("notes", "notes out of order", both(set_in(at("notes", 1), "onset_sample", 0), set_in(at("notes", 1), "pitch", 30)), False),
    ("notes", "two notes at one onset", set_in(at("notes", 1), "onset_sample", 0), True),
    ("notes", "two notes at one onset in pitch order",
     append_to(at("notes"), {"onset_sample": 1000, "offset_sample": 1500, "pitch": 50}), True),
    ("notes", "a note that ends before it starts", set_in(at("notes", 0), "offset_sample", 0), False),
    ("notes", "a note past the stream", set_in(at("notes", 1), "offset_sample", 4001), False),
    ("notes", "a range past the stream", set_in(at("range"), "end_sample", 4001), False),
    ("notes", "a note before its range", set_in(at("range"), "start_sample", 500), False),
    ("notes", "a range that ends before it starts", set_in(at("range"), "start_sample", 4000), False),
    ("notes", "notes derived from and correcting one revision",
     both(pin("derived_from", "chords"), pin("corrects", "notes")), True),
    ("notes-sung", "articulated notes as admitted", keep, True),
    ("notes-sung", "an articulation named twice", set_in(at("notes", 0), "articulation", ["staccato", "staccato"]), False),
    ("notes-sung", "an articulation outside the list", set_in(at("notes", 0), "articulation", ["legato"]), False),
    ("notes-sung", "every articulation", set_in(at("notes", 0), "articulation",
     ["staccato", "staccatissimo", "tenuto", "accent", "marcato", "fermata"]), True),
    ("notes-bass-line", "the bass line as admitted", keep, True),
    ("notes-bass-line", "a bass line that says false", set_in(whole, "bass", False), False),
    ("notes-bass-line", "a part without the bass mark", delete_in(whole, "bass"), True),
    ("melody-articulated", "the articulated melody as admitted", keep, True),
    ("melody-articulated", "an articulation on a rest", set_in(at("notes", 1), "articulation", ["staccato"]), False),
    ("melody", "the melody as admitted", keep, True),
    ("melody", "a melody laid on a timed meter", repin("meter", "meter-placed"), False),
    ("melody", "a melody derived from and correcting one revision",
     both(pin("derived_from", "sequence"), pin("corrects", "melody")), True),
    ("melody-spans", "the melody spans as admitted", keep, True),
    ("melody-spans", "a span naming a revision it does not pin", set_in(at("spans", 0), "notes", "sha256:" + "0" * 64), False),
    ("melody-spans", "melody spans that overlap",
     payload(lambda p: p["spans"].append(dict(p["spans"][0], start_sample=1999, end_sample=3000))), False),
]

CATALOGUE: list[Case] = [
    ("tracklist", "the tracklist as admitted", keep, True),
    ("tracklist", "a complete tracklist whose medium is 2", set_in(at("media", 0), "position", 2), False),
    ("tracklist", "a partial tracklist whose medium is 2",
     both(set_in(at("media", 0), "position", 2), set_in(whole, "partial", True)), True),
    ("tracklist", "a complete tracklist starting at track 2", set_in(at("media", 0, "tracks", 0), "position", 2), False),
    ("tracklist", "a hidden pregap track", set_in(at("media", 0, "tracks", 0), "position", 0), True),
    ("tracklist", "two tracks and one track reference",
     append_to(at("media", 0, "tracks"), {"position": 2}), False),
    ("tracklist", "two tracks and their references",
     both(append_to(at("media", 0, "tracks"), {"position": 2}), pin("track", "recording-entity")), True),
    ("tracklist", "tracks with a gap",
     both(append_to(at("media", 0, "tracks"), {"position": 3}), pin("track", "recording-entity")), False),
    ("recording-audio", "the recording's audio as admitted", keep, True),
    ("recording-audio", "a track the tracklist does not list", set_in(whole, "track", 2), False),
    ("recording-audio", "a medium and track without the tracklist", drop_role("tracklist"), False),
    ("recording-audio", "the audio alone", both(drop_role("tracklist"), payload(lambda p: (p.pop("medium"), p.pop("track")))), True),
    ("catalogue", "the correspondence as admitted", keep, True),
    ("catalogue", "a release as close to a recording", set_in(whole, "external", "https://musicbrainz.org/recording/0f0e0d0c-0b0a-4908-8706-050403020100"), False),
]

BANDMATES: list[Case] = [
    ("origin", "the co-written origin as admitted", keep, True),
    ("origin", "a co-written Work the operator did not write", both(drop_role("writer"), pin("writer", "guest")), False),
    ("origin", "a co-written Work of one writer", both(drop_role("writer"), pin("writer", "operator")), False),
    ("origin", "the operator's own Work", both(set_in(whole, "origin", "own"), drop_role("writer"), drop_role("band")), True),
    ("origin", "the operator's own Work naming the operator",
     both(set_in(whole, "origin", "own"), drop_role("writer"), drop_role("band"), pin("writer", "operator")), True),
    ("origin", "the operator's own Work naming a band",
     both(set_in(whole, "origin", "own"), drop_role("writer")), False),
    ("origin", "the operator's own Work naming another writer",
     both(set_in(whole, "origin", "own"), drop_role("writer"), drop_role("band"), pin("writer", "guest")), False),
    ("origin", "another's Work naming the operator",
     both(set_in(whole, "origin", "others"), drop_role("band")), False),
    ("origin", "another's Work", both(set_in(whole, "origin", "others"), drop_role("band"), drop_role("writer"),
                                      pin("writer", "guest")), True),
    ("consent", "the co-writer's consent as admitted", keep, True),
    ("consent", "a consent by the operator", agent("operator"), False),
    ("consent", "a consent by a band", agent("ensemble"), False),
    ("consent", "a refusal", set_in(whole, "decision", "refuse"), True),
]

EXPORTS: list[Case] = [
    ("export", "the export as admitted", keep, True),
    ("export", "a key that is not the request's", set_in(whole, "key", "sha256:" + "0" * 64), False),
    ("export", "another option under the same key", set_in(at("options"), "transpose", 3), False),
    ("export", "the rendered revisions in another order",
     lambda record, _: record.update(refs=[r for r in record["refs"] if r["role"] != "rendered"]
                                     + [r for r in record["refs"] if r["role"] == "rendered"][::-1]), False),
    ("export", "the exported file among what it renders", pin("joined", "export-file"), False),
]

# Rules only Door applies, because World's module check is given nothing to
# decide them by: the Entity kind of a work origin's agent, whether an agent
# that is a Package URL may limit a subject, and the form of a claim a timed
# meter corrects. Door's verdict is checked; World's projection decides these
# with the records it holds.
DOOR_ONLY: list[Case] = [
    ("origin", "an origin asserted by a band", agent("ensemble"), False),
    ("sensitivity", "a limit by a tool", as_tool, False),
    ("meter-placed", "a correction of the untimed meter", pin("corrects", "meter-written"), False),
    ("meter-placed", "a correction of a timed meter (anchor)", pin("corrects", "meter-chart"), True),
]

# Publication beyond the circle (ADR 0085, amended 2026-10-19): Door's
# refusal in `door transition` and World's `circle.beyond_circle` read the
# same sensitivity, rights, origin, and consent claims on the same UTC date.
# In the fixture, `title` reaches a take labelled AMBER, `notes` has rights
# no claim lifts past service, and `origin` touches a co-written Work whose
# co-writer consented to the circle only. Every approval and withdrawal there
# is already withdrawn, so a withdrawal here is refused for that.
BEYOND: list[Case] = [
    ("publication-v2", "an approval to the circle as admitted", keep, True),
    ("publication-v2", "to a service of what a label keeps in the circle",
     both(set_in(whole, "class", "service"), set_in(whole, "destination", "fixture-site")), False),
    ("publication-v2", "to a service of notes nothing limits",
     both(set_in(whole, "class", "service"), set_in(whole, "destination", "fixture-site"), repin("covers", "notes")),
     True),
    ("publication-v2", "openly, of notes whose rights stop at service",
     both(set_in(whole, "class", "open"), set_in(whole, "destination", "fixture-site"), repin("covers", "notes")),
     False),
    ("publication-v2", "to a service, of a co-written Work's origin, with no co-writer's consent",
     both(set_in(whole, "class", "service"), set_in(whole, "destination", "fixture-site"), repin("covers", "origin")),
     False),
    ("publication-v2", "held back from distribution, which nothing need allow",
     both(set_in(whole, "class", "distribution"), set_in(whole, "decision", "hold_back")), True),
    ("publication-v2", "an approval with no class", delete_in(whole, "class"), False),
    ("withdrawal-v2", "a second withdrawal of an approval already withdrawn", keep, False),
    ("withdrawal-v2", "a version 2 withdrawal of a version 1 approval already withdrawn",
     repin("withdraws", "publication"), False),
    ("withdrawal-v2", "a withdrawal of something that is no approval", repin("withdraws", "title"), False),
]

ALL = HARMONY + METER + FORM + KEYS + JUDGMENTS + LEAD_SHEETS + DOCUMENTS + NOTES + CATALOGUE + BANDMATES + EXPORTS + DOOR_ONLY


class Agreement010(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.door, cls.archive = integration_binaries()
        cls.tmp = Path(tempfile.mkdtemp())
        cls.manifest, cls.export = build_fixture_archive(cls.tmp)
        cls.root = cls.tmp / "archive"
        cls.by_revision = {r["revision_id"]: r for r in cls.export["records"]}
        cls.environment = dict(os.environ)
        for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
            cls.environment.setdefault(variable, os.path.realpath(shutil.which(program) or program))
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        remove_tree(cls.tmp)

    pinned = Agreement.pinned
    record = Agreement.record
    door_admits = Agreement.door_admits

    # -- World's verdict, from its modules' checks ---------------------------

    def view(self, revision: str, *extra: str) -> dict[str, Any]:
        found = self.by_revision[revision]
        return {"payload": found["payload"], "refs": found["refs"], "revision_id": revision,
                **{key: found[key] for key in extra if key in found}}

    def one(self, record: dict[str, Any], role: str, *extra: str) -> dict[str, Any] | None:
        refs = [r for r in record["refs"] if r["role"] == role and r.get("revision_id") in self.by_revision]
        return self.view(refs[0]["revision_id"], *extra) if refs else None

    def heads(self) -> dict[str, dict[str, Any]]:
        superseded = {ref["revision_id"] for r in self.export["records"] for ref in r.get("supersedes", [])}
        return {r["object_id"]: r for r in self.export["records"] if r["revision_id"] not in superseded}

    def kind_of(self, object_id: str) -> str | None:
        head = self.heads().get(object_id)
        return head["payload"].get("kind") if head and head["shape"] == "entity" else None

    def targets(self) -> dict[str, dict[str, Any]]:
        out = {}
        for r in self.export["records"]:
            target = {"id": r["object_id"], "shape": r["shape"], "payload": r["payload"], "refs": r["refs"],
                      "agent": r["agent"]}
            if r["shape"] == "entity":
                target["kind"] = r["payload"].get("kind")
            if r.get("contracts"):
                target["contract"] = (r["contracts"][0]["id"], r["contracts"][0]["version"])
                target["contracts"] = r["contracts"]
            out[r["revision_id"]] = target
        return out

    def world_admits(self, record: dict[str, Any]) -> tuple[bool, str]:
        cited = {(c["id"], c["version"]) for c in record.get("contracts", [])}
        claim = {"payload": record["payload"], "refs": record["refs"], "agent": record["agent"]}
        operator = self.manifest["operator_id"]
        targets = self.targets()

        def run() -> None:
            if ("archive.claim/chord_stacks", "2") in cited:
                harmony.check_chord_stacks_v2(claim, self.one(record, "stream"), self.one(record, "sequence"))
            elif ("archive.claim/chord_sequence", "2") in cited:
                harmony.check_chord_sequence_v2(claim)
            elif ("archive.claim/meter_layers", "2") in cited:
                form.check_meter_layers_v2(claim, self.one(record, "stream"), untimed_claim=self.one(record, "meter"),
                                           form_claim=self.one(record, "form"), sequence_claim=self.one(record, "sequence"))
            elif ("archive.claim/form", "1") in cited:
                form.check_form(claim)
            elif ("archive.claim/form_spans", "1") in cited:
                form.check_form_spans(claim, self.one(record, "stream"), self.one(record, "form"))
            elif ("archive.claim/key_sequence", "1") in cited:
                tonality.check_key_sequence(claim, self.one(record, "chord_sequence"))
            elif ("archive.claim/key_regions", "1") in cited:
                tonality.check_key_regions(claim, self.one(record, "stream"), self.one(record, "sequence"),
                                           self.one(record, "tuning"))
            elif ("archive.claim/tuning", "1") in cited:
                tonality.check_tuning(claim, self.one(record, "stream"))
            elif ("archive.assessment/judgment", "2") in cited:
                judged = self.one(record, "assessment_of", "agent", "contracts")
                stream = self.one(self.by_revision[judged["revision_id"]], "stream") if judged else None
                judgments.check_judgment(dict(claim, contracts=record["contracts"]), judged, stream)
            elif ("archive.claim/span_annotation", "1") in cited:
                about = {r["revision_id"]: self.view(r["revision_id"]) for r in record["refs"]
                         if r["role"] == "about" and r["revision_id"] in self.by_revision}
                judgments.check_span_annotation(claim, self.one(record, "stream"),
                                                self.one(record, "sequence", "agent", "contracts"), about,
                                                self.kind_of(record["agent"]))
            elif ("archive.claim/lead_sheet", "1") in cited:
                agents = {object_id: head["payload"]["kind"] for object_id, head in self.heads().items()
                          if head["shape"] == "entity"}
                lead_sheets.check_lead_sheet(claim, targets, agents)
            elif ("archive.claim/document_source", "1") in cited:
                documents.check_document_source(claim, targets)
            elif ("archive.claim/timed_lyrics", "1") in cited:
                documents.check_timed_lyrics(claim, targets)
            elif ("archive.claim/sensitivity", "1") in cited:
                documents.check_sensitivity(claim, targets, self.kind_of(record["agent"]))
            elif ("archive.claim/rights", "1") in cited:
                documents.check_rights(claim, targets)
            elif ("archive.claim/notes", "1") in cited:
                notes.check_notes(claim, self.one(record, "stream"), self.one(record, "tuning"))
            elif ("archive.claim/melody", "1") in cited:
                notes.check_melody(claim, self.one(record, "meter"))
            elif ("archive.claim/melody_spans", "1") in cited:
                pinned = {r["revision_id"]: self.view(r["revision_id"]) for r in record["refs"]
                          if r["role"] == "notes" and r["revision_id"] in self.by_revision}
                notes.check_melody_spans(claim, self.one(record, "stream"), pinned)
            elif ("archive.claim/tracklist", "1") in cited:
                catalogue.check_tracklist(claim, targets)
            elif ("archive.claim/recording_audio", "1") in cited:
                catalogue.check_recording_audio(claim, targets)
            elif ("archive.claim/catalogue_correspondence", "1") in cited:
                catalogue.check_correspondence(claim, targets)
            elif ("archive.claim/work_origin", "1") in cited:
                circle.check_work_origin(claim, targets, operator)
            elif ("archive.claim/sharing_consent", "1") in cited:
                circle.check_sharing_consent(claim, targets, operator)
            elif ("archive.claim/export", "1") in cited:
                exports.check_export(claim, targets)
            else:
                raise AssertionError(f"no World check for {sorted(cited)}")

        try:
            run()
        except AssertionError:
            raise
        except Exception as exc:  # each module raises its own error class
            return False, f"{type(exc).__name__}: {exc}"
        return True, ""

    # -- transitions: publication beyond the circle --------------------------

    def door_transitions(self, record: dict[str, Any]) -> tuple[bool, str]:
        """Door's verdict on `record` as a new transition, by `door
        transition --dry-run`, which writes nothing; every object it pins is
        expected at its head, as the fixture builder expects them."""
        type(self).count += 1
        name = f"door-agreement-transition-{self.count}"
        namespace = record["contracts"][0]["id"]
        from fixture.build import object_id
        objectid = object_id(self.export["archive_id"], namespace, name)
        draft = {key: record[key] for key in ("spec", "shape", "agent", "payload", "refs", "contracts")}
        draft.update({"object_id": objectid, "supersedes": [], "by": BY})
        heads = {object_: r["revision_id"] for object_, r in self.heads().items()}
        expected = [{"object_id": objectid, "revision_id": None}] + [
            {"object_id": object_, "revision_id": heads[object_]}
            for object_ in sorted({ref["id"] for ref in record["refs"]})]
        proposal = self.tmp / "proposals" / name
        (proposal / "records").mkdir(parents=True)
        (proposal / "records/transition.json").write_bytes(_canonical(draft))
        (proposal / "manifest.json").write_bytes(_canonical({
            "protocol": "archive-transition/1", "records": ["records/transition.json"],
            "identities": [{"object_id": objectid, "namespace": namespace, "name": name}],
            "expected_heads": expected}))
        completed = subprocess.run([self.door, "transition", "--proposal", str(proposal), "--dry-run"],
                                   cwd=self.root, env=self.environment, capture_output=True, text=True)
        return completed.returncode == 0, (completed.stderr or completed.stdout).strip()

    def world_transitions(self, record: dict[str, Any]) -> tuple[bool, str]:
        """World's verdict: the contract's check, and for an approval, nothing
        `circle.beyond_circle` names on today's UTC date, Door's date."""
        claim = {"payload": record["payload"], "refs": record["refs"], "agent": record["agent"]}
        cited = {(c["id"], c["version"]) for c in record["contracts"]}
        today = datetime.now(timezone.utc).date()
        try:
            if circle.PUBLICATION_APPROVAL_2 in cited:
                payload = circle.check_publication_approval_v2(claim, self.targets())
                covers = [ref["revision_id"] for ref in record["refs"] if ref["role"] == "covers"]
                blockers = circle.beyond_circle(payload, covers, self.by_revision, self.manifest["operator_id"],
                                                today)
                if blockers:
                    return False, "; ".join(blockers)
            elif circle.PUBLICATION_WITHDRAWAL_2 in cited:
                circle.check_publication_withdrawal_v2(claim, self.targets(), self.export["records"])
            else:
                raise AssertionError(f"no World check for {sorted(cited)}")
        except AssertionError:
            raise
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"
        return True, ""

    def test_beyond_the_circle(self):
        self.assertEqual({case[3] for case in BEYOND}, {True, False})
        for key, label, change, admitted in BEYOND:
            with self.subTest(base=key, case=label):
                record = copy.deepcopy(self.record(key))
                change(record, self)
                door, door_reason = self.door_transitions(record)
                self.assertEqual(door, admitted, f"Door: {door_reason}")
                world, world_reason = self.world_transitions(record)
                self.assertEqual(world, admitted, f"World: {world_reason}")

    def agree(self, cases: list[Case], world_too: bool = True) -> None:
        for key, label, change, admitted in cases:
            with self.subTest(base=key, case=label):
                record = copy.deepcopy(self.record(key))
                change(record, self)
                door, door_reason = self.door_admits(record)
                self.assertEqual(door, admitted, f"Door: {door_reason}")
                if world_too:
                    world, world_reason = self.world_admits(record)
                    self.assertEqual(world, admitted, f"World: {world_reason}")

    def test_harmony_version_2(self):
        self.agree(HARMONY)

    def test_meter_version_2(self):
        self.agree(METER)

    def test_form(self):
        self.agree(FORM)

    def test_keys_and_tuning(self):
        self.agree(KEYS)

    def test_judgments_and_marks(self):
        self.agree(JUDGMENTS)

    def test_lead_sheets(self):
        self.agree(LEAD_SHEETS)

    def test_documents_limits_and_rights(self):
        self.agree(DOCUMENTS)

    def test_notes_and_melody(self):
        self.agree(NOTES)

    def test_tracklists_and_catalogues(self):
        self.agree(CATALOGUE)

    def test_bandmates(self):
        self.agree(BANDMATES)

    def test_exports(self):
        self.agree(EXPORTS)

    def test_rules_only_door_applies(self):
        self.agree(DOOR_ONLY, world_too=False)

    def test_every_case_list_has_both_verdicts(self):
        for name, cases in (("harmony", HARMONY), ("meter", METER), ("form", FORM), ("keys", KEYS),
                            ("judgments", JUDGMENTS), ("lead sheets", LEAD_SHEETS), ("documents", DOCUMENTS),
                            ("notes", NOTES), ("catalogue", CATALOGUE), ("bandmates", BANDMATES),
                            ("exports", EXPORTS), ("Door only", DOOR_ONLY)):
            with self.subTest(cases=name):
                self.assertEqual({case[3] for case in cases}, {True, False})

    def test_every_new_contract_is_covered(self):
        # Enumerate the release's additions rather than naming them here.
        def catalog(version: str) -> set[tuple[str, str]]:
            data = json.loads((ROOT / "spec" / version / "contracts.json").read_text())
            return {(c["id"], c["version"]) for c in data["contracts"]}
        added = catalog("0.0.10") - catalog("0.0.9")
        covered = {(c["id"], c["version"]) for key, *_ in ALL + BEYOND for c in self.record(key)["contracts"]}
        self.assertEqual(added, covered)


if __name__ == "__main__":
    unittest.main()
