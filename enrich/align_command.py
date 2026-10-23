"""`enrich align`: read a chart's untimed claims and a recording's machine
beats and chords from the archive, place the chart (`align.py`), and propose
the placement's claims to Door under one derivation run.

The run's `used` pins every revision read: the mix, its stream claim, the
untimed meter claim, the form and the sequence it pins, and the machine's
meter and chord claims. The meter and chord claims cite the machine claims
in `derived_from`. A rerun on the same inputs and parameters is `unchanged`.
A placement the method refuses proposes nothing and answers
`nothing_proposed` with `code` `low_confidence`, the reason and the numbers.
"""

from __future__ import annotations

import argparse
from typing import Any, Callable

import adapter
import align

METER_V1 = ("archive.claim/meter_layers", "1")
METER_V2 = ("archive.claim/meter_layers", "2")
FORM = ("archive.claim/form", "1")
FORM_SPANS = ("archive.claim/form_spans", "1")
CHORDS_V1 = ("archive.claim/chord_stacks", "1")
CHORDS_V2 = ("archive.claim/chord_stacks", "2")
SEQUENCES = (("archive.claim/chord_sequence", "1"), ("archive.claim/chord_sequence", "2"))


class AlignCommandError(Exception):
    pass


def add_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--artifact", required=True, help="the mix Artifact's object identifier")
    command.add_argument("--stream", help="its audio_stream claim, when it has several")
    command.add_argument("--meter", required=True,
                         help="the chart's untimed meter_layers version 2 claim (object identifier); its form and "
                              "chord sequence are the ones it pins")
    command.add_argument("--beats", help="the machine's timed meter_layers claim to place on, when there are several")
    command.add_argument("--chords", help="the machine's chord_stacks claim to compare with, when there are several")


def _ref(record: dict[str, Any], role: str) -> list[dict[str, Any]]:
    return [ref for ref in record.get("refs", []) if ref.get("role") == role]


def _about(record: dict[str, Any], artifact: dict[str, Any], stream: dict[str, Any]) -> bool:
    def pins(role: str, target: dict[str, Any]) -> bool:
        return any(ref.get("id") == target["object_id"] and ref.get("revision_id") == target["revision_id"]
                   for ref in _ref(record, role))
    return pins("source", artifact) and pins("stream", stream)


def _choose(reader: adapter.ArchiveReader, wanted: str | None, contracts: tuple[tuple[str, str], ...],
            fits: Callable[[dict[str, Any]], bool], what: str) -> dict[str, Any]:
    if wanted is not None:
        record = reader.head(wanted)
        if not any(reader.cites(record, c) for c in contracts) or not fits(record):
            raise AlignCommandError(f"{wanted} is not {what}")
        return record
    found = [record for record in reader.heads.values() if record["shape"] == "claim"
             and any(reader.cites(record, c) for c in contracts) and fits(record)]
    if not found:
        raise AlignCommandError(f"the archive holds no {what}")
    if len(found) > 1:
        raise AlignCommandError(f"several claims are {what}; name one: "
                                + ", ".join(sorted(r["object_id"] for r in found)))
    return found[0]


def run(args: argparse.Namespace, env: dict[str, str], reader: adapter.ArchiveReader,
        staging: adapter.Staging | None, choose_stream: Callable, pinned: Callable) -> tuple[int, dict[str, Any]]:
    form_tools = align.world_module("form")
    harmony = align.world_module("harmony")
    for contract in (adapter.RUN_CONTRACT, METER_V2, CHORDS_V2):
        reader.citation(contract)
    started = adapter.now()
    artifact = reader.head(args.artifact)
    if artifact["shape"] != "artifact":
        raise AlignCommandError(f"{args.artifact} is a {artifact['shape']}, not an Artifact")
    stream = choose_stream(reader, artifact, args.stream)
    untimed = reader.head(args.meter)
    if not reader.cites(untimed, METER_V2) or not form_tools.is_untimed_meter(untimed):
        raise AlignCommandError(f"{args.meter} is not an untimed meter_layers version 2 claim")
    sequence_refs, form_refs = _ref(untimed, "sequence"), _ref(untimed, "form")
    if not sequence_refs:
        raise AlignCommandError(f"{args.meter} pins no chord sequence: there is nothing to place")
    sequence = pinned(reader, sequence_refs[0])
    form = pinned(reader, form_refs[0]) if form_refs else None
    claim = lambda record: {"payload": record["payload"], "refs": record["refs"], "revision_id": record["revision_id"]}
    form_tools.check_meter_layers_v2(claim(untimed), form_claim=None if form is None else claim(form),
                                     sequence_claim=claim(sequence))

    def machine_meter(record: dict[str, Any]) -> bool:
        return (_about(record, artifact, stream) and not _ref(record, "meter")
                and any("beats" in layer for layer in record["payload"].get("layers", [])))

    def machine_chords(record: dict[str, Any]) -> bool:
        return _about(record, artifact, stream) and not _ref(record, "sequence")

    beats = _choose(reader, args.beats, (METER_V1, METER_V2), machine_meter,
                    "a machine meter_layers claim with a beat-only layer about this stream")
    chords = _choose(reader, args.chords, (CHORDS_V1, CHORDS_V2), machine_chords,
                     "a machine chord_stacks claim about this stream")
    try:
        chart = align.chart_of(untimed["payload"], None if form is None else form["payload"], sequence["payload"])
        machine = align.machine_of(beats["payload"], chords["payload"], stream["payload"]["sample_count"])
    except align.AlignError as exc:
        raise AlignCommandError(str(exc)) from exc
    pins = {"artifact": adapter.Pin("source", artifact["object_id"], artifact["revision_id"]),
            "stream": adapter.Pin("stream", stream["object_id"], stream["revision_id"])}
    answer: dict[str, Any] = {"artifact": {"id": artifact["object_id"], "revision_id": artifact["revision_id"]},
                              "meter": {"id": untimed["object_id"], "revision_id": untimed["revision_id"]}}
    found = align.place(chart, machine)
    if isinstance(found, align.Refused):
        return 3, {**answer, "status": "nothing_proposed", "code": "low_confidence", "detail": found.detail,
                   **found.numbers}
    answer.update(found.numbers())
    made = align.payloads(chart, untimed["payload"], machine, found)
    pin = lambda role, record: adapter.Pin(role, record["object_id"], record["revision_id"])
    derived = (pin("derived_from", beats), pin("derived_from", chords))
    outputs = [
        (METER_V2, "meter", made["meter"], (pins["artifact"], pins["stream"], pin("meter", untimed), *derived),
         lambda c: form_tools.check_meter_layers_v2(c, claim(stream), untimed_claim=claim(untimed))),
        (CHORDS_V2, "chords", made["chords"], (pins["artifact"], pins["stream"], pin("sequence", sequence), *derived),
         lambda c: harmony.check_chord_stacks_v2(c, claim(stream), claim(sequence))),
    ]
    if "form" in made:
        reader.citation(FORM_SPANS)
        outputs.append((FORM_SPANS, "form", made["form"], (pins["artifact"], pins["stream"], pin("form", form)),
                        lambda c: form_tools.check_form_spans(c, claim(stream), claim(form))))
    planned = []
    for contract, name, payload, refs, check in outputs:
        try:
            check({"payload": payload, "refs": [ref.ref() for ref in refs]})
        except ValueError as exc:
            raise AlignCommandError(f"World refuses the placement's {contract[0]} claim: {exc}") from exc
        planned.append(adapter.Output(contract=contract, name=name, payload=payload, refs=refs))
    used = tuple(adapter.Pin("used", r["object_id"], r["revision_id"])
                 for r in (artifact, stream, untimed, sequence, *([form] if form else []), beats, chords))
    import numpy
    result = adapter.RunResult(agent=align.AGENT, method_id=align.METHOD_ID, method_version=align.METHOD_VERSION,
                               software=(align.SOFTWARE, ("numpy", numpy.__version__)), started_at=started,
                               ended_at=adapter.now(), used=used, outputs=tuple(planned),
                               parameters=align.PARAMETERS)
    previous = reader.equivalent_run(result)
    if previous is not None:
        return 0, {**answer, "status": "unchanged",
                   "run": {"id": previous["object_id"], "revision_id": previous["revision_id"]}}
    admission = adapter.propose(reader, result, door=args.door, staging=staging, dry_run=args.dry_run, env=env)
    return 0, {**answer, **admission.as_json()}
