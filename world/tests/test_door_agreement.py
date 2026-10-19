"""Door and World agree on specification 0.0.9's four contracts (D-17).

World re-checks chord stacks and meter layers with world/harmony.py, and
contributions and band membership with world/contributions.py, before it
projects one, and checks every pin; Door checks the same rules through the
installed bundles' schemas and profile rules. Each case here starts from a
record the real Door admitted into the published fixture archive
(fixture/build.py), changes one thing, and gives the result to both: Door by
`door admit --dry-run`, which writes nothing, and World by projecting it
beside the fixture's records read back from `archive export`. Every case
states the expected verdict, so the two cannot agree by both being wrong, and
each list has accepted and refused cases, so agreement is not vacuous.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from world.tests.test_conformance import build_fixture_archive, integration_binaries, remove_tree
from world.world import ProjectionError, build_projection

ROOT = Path(__file__).resolve().parents[2]
BY = "pkg:generic/archive-door-agreement-test@0"
TROMBONE = "https://musicbrainz.org/instrument/f6100277-c7b8-4c8d-aa26-d8cd014b6761"

Change = Callable[[dict[str, Any], "Agreement"], None]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def payload(change: Callable[[dict[str, Any]], None]) -> Change:
    return lambda record, _: change(record["payload"])


def drop_role(role: str) -> Change:
    def change(record: dict[str, Any], _: "Agreement") -> None:
        record["refs"] = [ref for ref in record["refs"] if ref["role"] != role]
    return change


def pin(role: str, key: str) -> Change:
    """Add a reference in `role` to the fixture record `key`."""
    def change(record: dict[str, Any], test: "Agreement") -> None:
        record["refs"].append({"role": role, **test.pinned(key)})
    return change


def repin(role: str, key: str) -> Change:
    def change(record: dict[str, Any], test: "Agreement") -> None:
        drop_role(role)(record, test)
        pin(role, key)(record, test)
    return change


def both(*changes: Change) -> Change:
    def change(record: dict[str, Any], test: "Agreement") -> None:
        for each in changes:
            each(record, test)
    return change


def keep(_: dict[str, Any], __: "Agreement") -> None:
    return None


def reading(span: int, rank: int) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda p: p["spans"][span]["readings"][rank]


def set_in(path: Callable[[dict[str, Any]], dict[str, Any]], key: str, value: Any) -> Change:
    def change(p: dict[str, Any]) -> None:
        path(p)[key] = value
    return payload(change)


def delete_in(path: Callable[[dict[str, Any]], dict[str, Any]], key: str) -> Change:
    def change(p: dict[str, Any]) -> None:
        del path(p)[key]
    return payload(change)


def whole(p: dict[str, Any]) -> dict[str, Any]:
    return p


def bar(layer: int, index: int) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda p: p["layers"][layer]["bars"][index]


def link(index: int) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda p: p["modulations"][index]


def role(p: dict[str, Any]) -> dict[str, Any]:
    return p["role"]


def unweighted(p: dict[str, Any]) -> None:
    del p["weight_scale"]
    for span in p["spans"]:
        for item in span["readings"]:
            item.pop("weight", None)


# (base fixture key, what changes, whether both must admit it)
CHORDS: list[tuple[str, str, Change, bool]] = [
    ("chords", "the machine's chord stacks as admitted", keep, True),
    ("chords-chart", "the chart's chord stacks as admitted", keep, True),
    ("chords", "no weight scale and no weights", payload(unweighted), True),
    ("chords", "a maybe group of one class", set_in(reading(1, 0), "uncertain",
                                                  [{"pitch_classes": [7], "present": "maybe"}]), True),
    ("chords", "a rootless reading naming a root that does not sound", set_in(reading(1, 1), "root", 2), True),
    ("chords", "tones out of order", set_in(reading(1, 0), "tones", [4, 1]), False),
    ("chords", "a tone twice", set_in(reading(1, 0), "tones", [1, 1, 4]), False),
    ("chords", "a thirteenth tone", set_in(reading(1, 1), "tones", list(range(12)) + [0]), False),
    ("chords", "a pitch class of 12", set_in(reading(1, 1), "bass", 12), False),
    ("chords", "a bass that is true", set_in(reading(1, 1), "bass", True), False),
    ("chords", "overlapping spans", set_in(lambda p: p["spans"][1], "start_sample", 999), False),
    ("chords", "a span that ends before it starts", set_in(lambda p: p["spans"][2], "end_sample", 3000), False),
    ("chords", "a span past the stream", set_in(lambda p: p["spans"][2], "end_sample", 4001), False),
    ("chords", "a repeated reading", payload(lambda p: p["spans"][1]["readings"].append(
        copy.deepcopy(p["spans"][1]["readings"][1]))), False),
    ("chords", "seventeen readings", payload(lambda p: p["spans"][1]["readings"].extend(
        {"bass": 9, "tones": [n], "weight": "0"} for n in range(15))), False),
    ("chords", "a weight without a scale", payload(lambda p: p.pop("weight_scale")), False),
    ("chords", "a reading without its weight", delete_in(reading(1, 1), "weight"), False),
    ("chords", "a weight that is not decimal text", set_in(reading(1, 1), "weight", "0,3"), False),
    ("chords", "an uncertain class that is a tone", set_in(reading(1, 0), "uncertain",
                                                         [{"pitch_classes": [4, 8], "present": "exactly_one"}]), False),
    ("chords", "an uncertain class that is the bass", set_in(reading(1, 0), "uncertain",
                                                           [{"pitch_classes": [8, 9], "present": "exactly_one"}]), False),
    ("chords", "exactly one of one class", set_in(reading(1, 0), "uncertain",
                                                 [{"pitch_classes": [7], "present": "exactly_one"}]), False),
    ("chords", "uncertain groups out of order", set_in(reading(1, 0), "uncertain",
                                                     [{"pitch_classes": [8], "present": "maybe"},
                                                      {"pitch_classes": [7], "present": "maybe"}]), False),
    ("chords", "a class in two uncertain groups", set_in(reading(1, 0), "uncertain",
                                                        [{"pitch_classes": [7], "present": "maybe"},
                                                         {"pitch_classes": [7, 8], "present": "exactly_one"}]), False),
    ("chords", "a voicing whose lowest note is not the bass", set_in(reading(1, 0), "voicing", [61, 64, 69]), False),
    ("chords", "a voicing missing a tone", set_in(reading(1, 0), "voicing", [57, 61]), False),
    ("chords", "a voicing with a note outside the stack", set_in(reading(1, 0), "voicing", [57, 61, 64, 70]), False),
    ("chords", "a voicing sounding an uncertain class", set_in(reading(1, 0), "voicing", [57, 61, 64, 67]), True),
    ("chords", "a voicing out of order", set_in(reading(1, 0), "voicing", [57, 64, 61]), False),
    ("chords", "no chord that is false", set_in(reading(0, 0), "no_chord", False), False),
    ("chords", "no chord with a bass", set_in(reading(0, 0), "bass", 0), False),
    ("chords", "an empty text", set_in(reading(2, 0), "text", ""), False),
    ("chords", "a reading with tones but no bass", set_in(reading(2, 0), "tones", [0]), False),
    ("chords", "a part of 101 characters", set_in(whole, "part", "x" * 101), False),
    ("chords", "a sample rate other than the stream's", set_in(whole, "sample_rate", 44100), False),
    ("chords", "an alignment without a document", set_in(whole, "alignment", {"method": "manual"}), False),
    ("chords", "a contribution that is a stream", pin("contribution", "stream"), False),
    ("chords", "two runs", pin("run", "run-detect"), False),
    ("chords", "a stem that is a claim", pin("analysed", "stream"), False),
    ("chords", "a stream of another Artifact", repin("source", "take-a"), False),
    ("chords-chart", "a chart without its alignment", delete_in(whole, "alignment"), False),
    ("chords-chart", "a locator without a document",
     both(drop_role("document"), delete_in(whole, "alignment"), delete_in(whole, "document_url")), False),
    ("chords-chart", "a URL without a document",
     both(drop_role("document"), delete_in(whole, "alignment"),
          payload(lambda p: [span.pop("locator") for span in p["spans"]])), False),
    ("chords-chart", "a document's chords with nothing more",
     both(drop_role("document"), delete_in(whole, "alignment"), delete_in(whole, "document_url"),
          payload(lambda p: [span.pop("locator") for span in p["spans"]])), True),
    ("chords-chart", "a URL with a space", set_in(whole, "document_url", "https://example.org/a chart"), False),
    ("chords-chart", "a URL that is not http", set_in(whole, "document_url", "ftp://example.org/chart"), False),
    ("chords-chart", "a locator of bar 0", set_in(lambda p: p["spans"][0]["locator"], "bar", 0), False),
    ("chords-chart", "an alignment without a method", set_in(whole, "alignment", {"max_error_samples": 4}), False),
    ("chords-placed", "the placed sequence as admitted", keep, True),
    ("chords-correction", "the correction as admitted", keep, True),
    ("chords-placed", "a held span without a hold rule", delete_in(whole, "hold_rule"), False),
    ("chords-placed", "a hold rule of 501 characters", set_in(whole, "hold_rule", "x" * 501), False),
    ("chords-placed", "every end asserted, without a hold rule",
     both(delete_in(whole, "hold_rule"), payload(lambda p: [span.update(end="asserted") for span in p["spans"]])), True),
    ("chords-placed", "every end asserted, with a hold rule",
     payload(lambda p: [span.update(end="asserted") for span in p["spans"]]), False),
    ("chords-placed", "an end that is guessed", set_in(lambda p: p["spans"][0], "end", "guessed"), False),
    ("chords-placed", "an entry past the sequence's last", set_in(lambda p: p["spans"][2], "entry", 3), False),
    ("chords-placed", "the sequence's last entry", set_in(lambda p: p["spans"][2], "entry", 2), True),
    ("chords-placed", "a placement without its alignment", delete_in(whole, "alignment"), False),
    ("chords-placed", "a sequence that is a chord stacks claim", repin("sequence", "chords"), False),
    ("chords-placed", "entries without the sequence",
     both(drop_role("sequence"), delete_in(whole, "alignment")), False),
    ("chords-placed", "a placement without entries",
     payload(lambda p: [span.pop("entry") for span in p["spans"]]), True),
    ("chords-correction", "a correction with a hold rule", set_in(whole, "hold_rule", "until the next"), False),
    ("chords-correction", "a correction of a sequence", repin("corrects", "sequence"), False),
    ("chords-correction", "a correction of two claims", pin("corrects", "chords-chart"), True),
    ("chords-correction", "nine corrections", both(
        *[pin("corrects", key) for key in ("chords-chart", "chords-placed", "chords", "chords-chart",
                                           "chords-placed", "chords", "chords-chart", "chords-placed")]), False),
    ("chords-correction", "an alignment without a document or sequence", set_in(whole, "alignment", {"method": "manual"}), False),
]

METER: list[tuple[str, str, Change, bool]] = [
    ("meter-chart", "the chart's meter as admitted", keep, True),
    ("meter-beats", "the tracked beats as admitted", keep, True),
    ("meter-chart", "no modulations", delete_in(whole, "modulations"), True),
    ("meter-chart", "an empty list of modulations", set_in(whole, "modulations", []), True),
    ("meter-chart", "a link that still states a ratio", set_in(link(0), "ratio", [2, 3]), False),
    ("meter-chart", "an equation of a note value with itself", set_in(link(0), "after", {"denominator": 4}), True),
    ("meter-chart", "a regrouping whose pulse does not move", set_in(link(1), "pulse_after", {"denominator": 4}), False),
    ("meter-chart", "a regrouping to the eighth",
     set_in(link(1), "pulse_after", {"denominator": 8, "dots": 0}), True),
    ("meter-chart", "an equation with a regrouping's fields", set_in(link(0), "kind", "regrouping"), False),
    ("meter-chart", "a link of another kind", set_in(link(0), "kind", "modulation"), False),
    ("meter-chart", "a modulation of the first bar", set_in(link(0), "bar", 0), False),
    ("meter-chart", "a modulation past the last bar", set_in(link(0), "bar", 2), False),
    ("meter-chart", "a modulation of a layer that does not exist", set_in(link(1), "layer", 2), False),
    ("meter-chart", "a modulation joining bars that do not touch", set_in(bar(0, 0), "end_sample", 1999), False),
    ("meter-chart", "two modulations of one bar", payload(lambda p: p["modulations"].append(
        copy.deepcopy(p["modulations"][0]))), False),
    ("meter-chart", "modulations out of order", payload(lambda p: p["modulations"].reverse()), False),
    ("meter-chart", "three dots", set_in(lambda p: p["modulations"][0]["after"], "dots", 3), False),
    ("meter-chart", "a grouping that does not add up", set_in(bar(0, 1), "grouping", [3, 2]), False),
    ("meter-chart", "a grouping of 2+2+2 with three beats",
     both(set_in(bar(0, 1), "grouping", [2, 2, 2]), set_in(bar(0, 1), "beats", [2000, 2600, 3300])), True),
    ("meter-chart", "too few beats", set_in(bar(0, 0), "beats", [0, 500, 1000]), False),
    ("meter-chart", "a first beat after the downbeat", set_in(bar(0, 0), "beats", [1, 500, 1000, 1500]), False),
    ("meter-chart", "beats that fall back", set_in(bar(0, 0), "beats", [0, 1000, 500, 1500]), False),
    ("meter-chart", "a beat at the bar's end", set_in(bar(0, 1), "beats", [2000, 4000]), False),
    ("meter-chart", "a denominator of 6", set_in(bar(0, 0), "denominator", 6), False),
    ("meter-chart", "a numerator of 65", set_in(bar(1, 0), "numerator", 65), False),
    ("meter-chart", "overlapping bars in one layer", set_in(bar(0, 1), "start_sample", 1900), False),
    ("meter-chart", "a bar past the stream", set_in(bar(1, 1), "end_sample", 4001), False),
    ("meter-chart", "nine layers", payload(lambda p: p["layers"].extend(copy.deepcopy(p["layers"][1])
                                                                        for _ in range(7))), False),
    ("meter-chart", "eight layers", payload(lambda p: p["layers"].extend(copy.deepcopy(p["layers"][1])
                                                                         for _ in range(6))), True),
    ("meter-chart", "an empty label", set_in(lambda p: p["layers"][0], "label", ""), False),
    ("meter-chart", "a role that is neither written nor felt", set_in(lambda p: p["layers"][0], "role", "heard"), False),
    ("meter-chart", "a felt layer without a written one", delete_in(lambda p: p["layers"][0], "role"), False),
    ("meter-chart", "two written layers", set_in(lambda p: p["layers"][1], "role", "written"), True),
    ("meter-chart", "a bar locator with a beat", set_in(bar(0, 0), "locator", {"bar": 1, "beat": 1}), False),
    ("meter-chart", "a locator without a document",
     both(drop_role("document"), delete_in(whole, "alignment")), False),
    ("meter-chart", "a beat-only layer beside the bars", payload(lambda p: p["layers"].append(
        {"role": "felt", "beats": [0, 1333, 2667], "downbeats": [0]})), True),
    ("meter-chart", "a link of a beat-only layer", both(payload(lambda p: p["layers"].insert(0, {"beats": [0, 2000]})),
                                                        set_in(link(1), "layer", 2)), False),
    ("meter-beats", "a layer with both bars and beats", set_in(lambda p: p["layers"][0], "bars",
        [{"start_sample": 0, "end_sample": 4000, "numerator": 4, "denominator": 4}]), False),
    ("meter-beats", "beats out of order", set_in(lambda p: p["layers"][0], "beats", [0, 2000, 1000, 3000]), False),
    ("meter-beats", "a beat twice", set_in(lambda p: p["layers"][0], "beats", [0, 1000, 1000, 3000]), False),
    ("meter-beats", "a beat at the stream's last sample",
     both(set_in(lambda p: p["layers"][0], "beats", [0, 3999]), set_in(lambda p: p["layers"][0], "downbeats", [0])), True),
    ("meter-beats", "a beat at the stream's end",
     both(set_in(lambda p: p["layers"][0], "beats", [0, 4000]), set_in(lambda p: p["layers"][0], "downbeats", [0])), False),
    ("meter-beats", "a downbeat that is not a beat", set_in(lambda p: p["layers"][0], "downbeats", [0, 1500]), False),
    ("meter-beats", "downbeats out of order", set_in(lambda p: p["layers"][0], "downbeats", [2000, 0]), False),
    ("meter-beats", "no downbeats and no beat unit",
     both(delete_in(lambda p: p["layers"][0], "downbeats"), delete_in(lambda p: p["layers"][0], "beat_unit")), True),
    ("meter-beats", "a beat unit of a sixth", set_in(lambda p: p["layers"][0], "beat_unit", {"denominator": 6}), False),
    ("meter-beats", "a beat-only layer with a time signature",
     set_in(lambda p: p["layers"][0], "numerator", 4), False),
    ("meter-beats", "a beat-only layer felt with nothing written",
     set_in(lambda p: p["layers"][0], "role", "felt"), False),
]

SEQUENCES: list[tuple[str, str, Change, bool]] = [
    ("sequence", "the chart's sequence as admitted", keep, True),
    ("sequence", "a sequence about the Work alone",
     both(drop_role("document"), delete_in(whole, "document_url"),
          payload(lambda p: [entry.pop("locator", None) for entry in p["entries"]])), True),
    ("sequence", "a sequence about nothing",
     both(drop_role("document"), drop_role("work"), delete_in(whole, "document_url"),
          payload(lambda p: [entry.pop("locator", None) for entry in p["entries"]])), False),
    ("sequence", "a sequence from a document alone", drop_role("work"), True),
    ("sequence", "a Work that is a release", repin("work", "release"), False),
    ("sequence", "a sequence with samples", set_in(whole, "sample_rate", 8000), False),
    ("sequence", "a sequence with an alignment", set_in(whole, "alignment", {"method": "manual"}), False),
    ("sequence", "a locator without a document",
     both(drop_role("document"), delete_in(whole, "document_url")), False),
    ("sequence", "an entry located before the one before it",
     set_in(lambda p: p["entries"][1], "locator", {"line": 1, "word": 4}), False),
    ("sequence", "an entry at the same place as the one before it",
     set_in(lambda p: p["entries"][1], "locator", {"line": 2, "word": 1}), True),
    ("sequence", "a locator with a word and a column",
     set_in(lambda p: p["entries"][1], "locator", {"line": 2, "word": 3, "column": 9}), False),
    ("sequence", "a locator of line 0", set_in(lambda p: p["entries"][0], "locator", {"line": 0}), False),
    ("sequence", "a section of 101 characters", set_in(lambda p: p["entries"][0], "section", "x" * 101), False),
    ("sequence", "an entry with a time", set_in(lambda p: p["entries"][0], "start_sample", 0), False),
    ("sequence", "a repeat played once", set_in(lambda p: p["repeats"][0], "times", 1), False),
    ("sequence", "a repeat past the last entry", set_in(lambda p: p["repeats"][0], "last", 3), False),
    ("sequence", "a repeat that ends before it starts", set_in(lambda p: p["repeats"][0], "first", 2), False),
    ("sequence", "nested repeats, outer first", payload(lambda p: p["repeats"].extend(
        [{"first": 0, "last": 0, "times": 2}])), True),
    ("sequence", "nested repeats, inner first", payload(lambda p: p["repeats"].insert(
        0, {"first": 0, "last": 0, "times": 2})), False),
    ("sequence", "crossing repeats", payload(lambda p: p["repeats"].append({"first": 1, "last": 2, "times": 2})), False),
    ("sequence", "repeats apart", payload(lambda p: p["repeats"].append({"first": 2, "last": 2, "times": 3})), True),
    ("sequence", "a correction of a sequence", pin("corrects", "sequence"), True),
    ("sequence", "a correction of timed chords", pin("corrects", "chords"), False),
    ("sequence", "tones out of order", set_in(lambda p: p["entries"][0]["readings"][0], "tones", [7, 4]), False),
    ("sequence", "a weight without a scale", set_in(lambda p: p["entries"][0]["readings"][0], "weight", "1"), False),
]
CREDITS: list[tuple[str, str, Change, bool]] = [
    *[(key, f"{key} as admitted", keep, True) for key in (
        "contribution-work", "contribution-release", "contribution-recording", "contribution-span",
        "contribution-channel", "contribution-place", "contribution-detected", "membership", "membership-ended")],
    ("contribution-work", "a credit kept only as text", both(delete_in(whole, "role"),
                                                           set_in(whole, "text", "words by the guest")), True),
    ("contribution-work", "neither a role nor text", delete_in(whole, "role"), False),
    ("contribution-work", "a composer of a recording",
     both(set_in(whole, "scope", "recording"), drop_role("work"), pin("recording", "recording-entity")), False),
    ("contribution-work", "a work scope pinning a release", both(drop_role("work"), pin("work", "release")), False),
    ("contribution-work", "a work scope with spans", set_in(whole, "spans", [{"start_sample": 0, "end_sample": 1}]), False),
    ("contribution-work", "a role from another type's attributes", set_in(role, "attributes", ["guest"]), False),
    ("contribution-work", "a contributor that is a Work", repin("contributor", "work-b"), False),
    ("contribution-release", "attributes out of order", set_in(role, "attributes", ["re", "co"]), False),
    ("contribution-release", "an attribute the type does not take", set_in(role, "attributes", ["co", "solo"]), False),
    ("contribution-release", "the local label type", set_in(whole, "role", {"type": "label"}), True),
    ("contribution-release", "a release scope with a recording too", pin("recording", "recording-entity"), False),
    ("contribution-recording", "an instrument without instruments", delete_in(role, "instruments"), False),
    ("contribution-recording", "an instrument twice", set_in(role, "instruments", [TROMBONE, TROMBONE]), False),
    ("contribution-recording", "an instrument that is not a MusicBrainz URI",
     set_in(role, "instruments", ["https://example.org/trombone"]), False),
    ("contribution-recording", "a performer with instruments", set_in(role, "type", "performer"), False),
    ("contribution-recording", "a vocal role with vocals and instruments",
     set_in(whole, "role", {"type": "vocal", "vocals": ["lead vocals"], "instruments": [TROMBONE]}), False),
    ("contribution-recording", "a vocal attribute MusicBrainz does not have",
     set_in(whole, "role", {"type": "vocal", "vocals": ["humming"]}), False),
    ("contribution-recording", "a document's credit without its text", delete_in(whole, "text"), False),
    ("contribution-recording", "retrieval on a day that does not exist",
     set_in(whole, "retrieved_at", "2026-02-30"), False),
    ("contribution-recording", "retrieval at an instant", set_in(whole, "retrieved_at", "2026-10-18T09:30:00.5Z"), True),
    ("contribution-recording", "retrieval in year 0", set_in(whole, "retrieved_at", "0000-01-01"), False),
    ("contribution-recording", "retrieval with an offset", set_in(whole, "retrieved_at", "2026-10-18T09:30:00+01:00"), False),
    ("contribution-recording", "retrieval without the document",
     both(drop_role("document"), delete_in(whole, "document_url"), delete_in(whole, "locator")), False),
    ("contribution-recording", "a recording that is a selection", repin("recording", "selection-v2"), True),
    ("contribution-recording", "a recording that is a whole file", repin("recording", "take-a"), True),
    ("contribution-recording", "a recording that is an old selection", repin("recording", "selection-v1"), False),
    ("contribution-recording", "a recording that is a person", repin("recording", "guest"), False),
    ("contribution-span", "a span past the stream", set_in(lambda p: p["spans"][0], "end_sample", 4001), False),
    ("contribution-span", "overlapping spans", set_in(whole, "spans", [{"start_sample": 0, "end_sample": 2000},
                                                                      {"start_sample": 1999, "end_sample": 3000}]), False),
    ("contribution-span", "1001 spans", set_in(whole, "spans", [{"start_sample": n, "end_sample": n + 1}
                                                               for n in range(1001)]), False),
    ("contribution-span", "a span claim at another rate", set_in(whole, "sample_rate", 16000), False),
    ("contribution-span", "a span without its stream", drop_role("stream"), False),
    ("contribution-span", "a span with a channel", set_in(whole, "channel", 1), False),
    ("contribution-span", "a span from a document without its alignment",
     both(pin("document", "chart"), set_in(whole, "text", "trombone 0:00-0:01")), False),
    ("contribution-span", "a span from a document with its alignment",
     both(pin("document", "chart"), set_in(whole, "text", "trombone 0:00-0:01"),
          set_in(whole, "alignment", {"method": "manual"})), True),
    ("contribution-span", "a stream of another Artifact", repin("source", "take-a"), False),
    ("contribution-channel", "a channel past the stream's channels", set_in(whole, "channel", 2), False),
    ("contribution-channel", "a channel index without a stream", drop_role("stream"), False),
    ("contribution-channel", "a stem without a stream or index", both(drop_role("stream"), delete_in(whole, "channel")), True),
    ("contribution-channel", "a channel credit with an alignment", set_in(whole, "alignment", {"method": "manual"}), False),
    ("contribution-channel", "a composer of a channel", set_in(role, "type", "composer"), False),
    ("contribution-place", "a place with a contributor", pin("contributor", "ensemble"), False),
    ("contribution-place", "a place without its text", delete_in(whole, "text"), False),
    ("contribution-place", "a place credited as written", set_in(whole, "credited_as", "the fixture room"), True),
    ("contribution-detected", "a detector naming a name", set_in(whole, "credited_as", "someone"), False),
    ("contribution-detected", "a detector with neither role nor contributor", delete_in(whole, "role"), False),
    ("contribution-detected", "a confidence that is a number", set_in(whole, "confidence",
                                                                      {"scale": "probability", "value": 0.8}), False),
    ("contribution-detected", "a run that is a stream", repin("run", "stream"), False),
    ("membership", "a membership that ends before it begins", set_in(whole, "to", "2019-12"), False),
    ("membership", "a membership that ends in the month it begins",
     both(set_in(whole, "from", "2021-06-30"), set_in(whole, "to", "2021-06")), True),
    ("membership", "a month 13", set_in(whole, "from", "2021-13"), False),
    ("membership", "a year 0", set_in(whole, "from", "0000"), False),
    ("membership", "ended with an end date", set_in(whole, "ended", True), False),
    ("membership", "an attribute of another type", set_in(whole, "attributes", ["guest"]), False),
    ("membership", "attributes out of order", set_in(whole, "attributes", ["original", "additional"]), False),
    ("membership", "a band that is a person", repin("band", "guest"), False),
    ("membership", "a member that is a band", repin("member", "ensemble"), False),
    ("membership", "a locator without a document", set_in(whole, "locator", "page 2"), False),
    ("membership-ended", "ended false", set_in(whole, "ended", False), False),
    ("membership-ended", "a document without text", delete_in(whole, "text"), False),
]


class Agreement(unittest.TestCase):
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

    def pinned(self, key: str) -> dict[str, str]:
        found = self.manifest["objects"][key]
        return {"id": found["object_id"], "revision_id": found["revision_id"]}

    def record(self, key: str) -> dict[str, Any]:
        return self.by_revision[self.manifest["objects"][key]["revision_id"]]

    def door_admits(self, record: dict[str, Any]) -> tuple[bool, str]:
        """Door's verdict on `record` as a new object, by a dry run that
        writes nothing."""
        type(self).count += 1
        name = f"door-agreement-{self.count}"
        namespace = "archive.claim/agreement"
        from fixture.build import object_id
        objectid = object_id(self.export["archive_id"], namespace, name)
        draft = {key: record[key] for key in ("spec", "shape", "by", "agent", "payload", "refs", "contracts")}
        draft.update({"object_id": objectid, "supersedes": [], "by": BY})
        proposal = self.tmp / "proposals" / name
        (proposal / "records").mkdir(parents=True)
        (proposal / "records/claim.json").write_bytes(_canonical(draft))
        (proposal / "manifest.json").write_bytes(_canonical({
            "protocol": "archive-admission/1", "records": ["records/claim.json"],
            "identities": [{"object_id": objectid, "namespace": namespace, "name": name}],
            "expected_heads": [{"object_id": objectid, "revision_id": None}], "contents": []}))
        completed = subprocess.run([self.door, "admit", "--proposal", str(proposal), "--dry-run"],
                                   cwd=self.root, env=self.environment, capture_output=True, text=True)
        return completed.returncode == 0, (completed.stderr or completed.stdout).strip()

    def world_admits(self, record: dict[str, Any]) -> tuple[bool, str]:
        """World's verdict: whether it projects the record beside the
        fixture's records. Its projection re-checks the record with
        world/harmony.py or world/contributions.py and checks every pin."""
        from fixture.build import revision_of
        candidate = dict(record, object_id="urn:uuid:00000000-0000-4000-8000-00000000a9ee", by=BY)
        candidate["revision_id"] = revision_of({k: v for k, v in candidate.items() if k != "revision_id"})
        export = dict(self.export, records=self.export["records"] + [candidate])
        try:
            build_projection(export)
        except ProjectionError as exc:
            return False, str(exc)
        return True, ""

    def agree(self, cases: list[tuple[str, str, Change, bool]]) -> None:
        verdicts = {case[3] for case in cases}
        self.assertEqual(verdicts, {True, False}, "a case list needs accepted and refused cases")
        for key, label, change, admitted in cases:
            with self.subTest(base=key, case=label):
                record = copy.deepcopy(self.record(key))
                change(record, self)
                door, door_reason = self.door_admits(record)
                world, world_reason = self.world_admits(record)
                self.assertEqual(door, admitted, f"Door: {door_reason}")
                self.assertEqual(world, admitted, f"World: {world_reason}")

    def test_chord_stacks(self):
        self.agree(CHORDS)

    def test_meter_layers(self):
        self.agree(METER)

    def test_chord_sequences(self):
        self.agree(SEQUENCES)

    def test_contributions_and_band_membership(self):
        self.agree(CREDITS)

    def test_every_new_contract_is_covered(self):
        # Enumerate the release's additions rather than naming them here.
        def catalog(version: str) -> set[tuple[str, str]]:
            data = json.loads((ROOT / "spec" / version / "contracts.json").read_text())
            return {(c["id"], c["version"]) for c in data["contracts"]}
        added = catalog("0.0.9") - catalog("0.0.8")
        covered = {(c["id"], c["version"]) for key, *_ in CHORDS + METER + SEQUENCES + CREDITS
                   for c in self.record(key)["contracts"]}
        self.assertEqual(added, covered)


if __name__ == "__main__":
    unittest.main()
