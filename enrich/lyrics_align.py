"""`enrich align-lyrics`: a lyric document forced onto a recording's vocal
stem by an aligner, proposed as a `timed_lyrics` claim under one derivation
run (ADR 0085, amended 2026-10-19; how the aligner is run is its amendment of
2026-11-27, proposed).

The aligner is a program with an `archive-extractor/1` manifest (ADR 0074)
kept in `enrich/aligners/<id>/`, outside the feature-bank catalog, so `run
all` and the backfill never run it: alignment is a run by request. Its
command names programs the operator configures (`${align-python}`,
`${align-weights}`), so its environment and weights stay the operator's and
nothing is bundled. It is given the extractor request with one member more,
`document`: the path of the document's words by line, as World's
`document_lines` counts them, with the document's digest and scheme. It is
run as the runner runs an extractor (its own process group, a timeout) and
its output directory is checked as the runner checks one (`check_result`),
but nothing is filed in the bank: the run's answer goes straight to a claim.

The command reads, all standard library:

- the mix Artifact, its stream claim, and the stem `--stem` (default
  `stems/vocals`) from the feature bank, cited by bank key (ADR 0079); only
  an entry of the whole recording, on its timeline from sample 0;
- the lyric document Artifact's bytes from the archive's content, counted
  under `--scheme` (`text` or `lrc`);
- optionally a vocal notes claim (`--notes`) about the same mix revision.

It writes nothing but a Door proposal: the run `used` the document, the mix,
the stream, and the notes claim; its parameters Artifact names the aligner
(id, version, every setting, including the model and its weights' digest),
the language, the syllabification rule, the stem by bank key, the
carried-by rule and the confidence gate. The claim pins `document`,
`source`, `stream`, and the notes claim when a syllable is carried by it;
`alignment.method` is `forced alignment`, with no `max_error_samples`
(none is measured yet). Each syllable is carried by the notes whose onsets
fall from CARRY_LEAD before its start to CARRY_LEAD before the next
syllable's start, at most CARRY_MAX after its own; two or more are a
melisma. An alignment whose mean letter probability is below GATE proposes
nothing and answers `nothing_proposed` with `code` `low_confidence`. A rerun
on the same inputs and settings is `unchanged`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from bisect import bisect_left
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

import adapter
import bank
import extract
import proposers

HERE = Path(__file__).resolve().parent
ALIGNERS = HERE / "aligners"
DEFAULT_ALIGNER = "align-wav2vec2"
AGENT = "pkg:generic/archive-enrich-align-lyrics@0.1.0"
SOFTWARE = ("archive-enrich-align-lyrics", "0.1.0")
NOTHING_PROPOSED = 3
TIMED_LYRICS = ("archive.claim/timed_lyrics", "1")
NOTES = ("archive.claim/notes", "1")
REQUEST_DOCUMENT = "document"     # the member the request gains (see the module)
GATE = "0.10"
CARRY_LEAD = Fraction(1, 10)      # seconds
CARRY_MAX = 3                     # seconds
CARRY_RULE = ("a note carries the syllable sounding at its onset: from 0.1 s before the syllable's start to 0.1 s "
              "before the next syllable's start (all syllables in time order), at most 3 s after its own start; "
              "two or more notes are a melisma; onsets in samples, the window's edges rounded half to even; "
              "the first 64 onsets are kept")
TIMES = ("the aligner's samples at its rate are samples of the pinned stream, round(s * sample_rate / rate) half "
         "to even, exactly, clamped to sample_count; a word or line that becomes empty is dropped, and a word "
         "whose syllables would is kept without them")
CONFIDENCE_SCALE = "{id} {version}: mean CTC posterior probability of the aligned letters, 0 to 1"


class AlignLyricsError(Exception):
    pass


def add_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--artifact", required=True, help="the mix Artifact's object identifier")
    command.add_argument("--stream", help="its audio_stream claim, when it has several")
    command.add_argument("--document", required=True, help="the lyric document Artifact's object identifier")
    command.add_argument("--scheme", default="text", choices=("text", "lrc"),
                         help="how the document is counted (default text)")
    command.add_argument("--notes", help="a vocal notes claim about the same mix revision, whose notes may carry "
                                         "the syllables")
    command.add_argument("--stem", default="stems/vocals", help="the bank's vocal stem feature (default stems/vocals)")
    command.add_argument("--aligner", default=DEFAULT_ALIGNER, help=f"the aligner (default {DEFAULT_ALIGNER})")
    command.add_argument("--aligners", default=str(ALIGNERS), help="the directory of aligners")
    command.add_argument("--bank", help="the feature bank root (default: ARCHIVE_FEATURE_BANK)")
    command.add_argument("--config", help="the extractor runner's configuration (its programs and parameters)")
    command.add_argument("--program", action="append", default=[], metavar="NAME=PATH",
                         help="a program the aligner's command names, such as align-python or align-weights")
    command.add_argument("--timeout", type=float, default=3600.0, help="seconds the aligner may run")


# -- the aligner --------------------------------------------------------------

def load_aligner(directory: Path, aligner_id: str) -> bank.Extractor:
    path = directory / aligner_id / "extractor.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AlignLyricsError(f"no aligner {aligner_id} at {path}: {exc}") from exc
    try:
        bank.check_manifest(manifest, str(path))
    except bank.BankError as exc:
        raise AlignLyricsError(str(exc)) from exc
    if manifest["id"] != aligner_id:
        raise AlignLyricsError(f"{path} declares {manifest['id']}, not {aligner_id}")
    if set(manifest["outputs"]) != {"alignment", "mean_score", "active_seconds"} \
            or manifest["outputs"]["alignment"].get("kind") != "file":
        raise AlignLyricsError(f"{aligner_id} must declare exactly alignment (a file), mean_score and active_seconds")
    for field in ("language", "syllabification"):
        if not isinstance(manifest["parameters"].get(field), str):
            raise AlignLyricsError(f"{aligner_id} must state its {field} among its parameters")
    return bank.Extractor(path.parent, manifest)


def run_aligner(aligner: bank.Extractor, programs: dict[str, str], stem: tuple[Path, str, str],
                document: dict[str, Any], words: list[list[str]], timeout: float) -> dict[str, Any]:
    """Run the aligner once in a scratch directory and return its checked
    outputs and its alignment. The scratch directory, which holds the
    document's words, is removed whatever happens."""
    work = Path(tempfile.mkdtemp(prefix=f"{aligner.id}-"))
    try:
        os.chmod(work, 0o700)
        output_dir = work / "out"
        output_dir.mkdir()
        (work / "words.json").write_text(json.dumps({"lines": words}, ensure_ascii=False), encoding="utf-8")
        request = {"contract": bank.REQUEST_CONTRACT, "extractor": {"id": aligner.id, "version": aligner.version},
                   "parameters": aligner.manifest["parameters"],
                   "input": {"path": str(stem[0].resolve()), "digest": stem[1], "media_type": stem[2]},
                   REQUEST_DOCUMENT: {"path": str(work / "words.json"), "digest": document["digest"],
                                      "scheme": document["scheme"]},
                   "needs": {}, "output_dir": str(output_dir)}
        (work / "request.json").write_bytes(bank.canonical(request))
        try:
            command = extract.resolve_command(aligner, programs) + [str(work / "request.json")]
        except bank.BankError as exc:
            raise AlignLyricsError(str(exc)) from exc
        environment = {**os.environ, "ARCHIVE_EXTRACTOR_REQUEST": str(work / "request.json"),
                       "ARCHIVE_EXTRACTOR_INPUT": request["input"]["path"],
                       "ARCHIVE_EXTRACTOR_OUTPUT": str(output_dir)}
        clock = time.monotonic()
        try:
            code, log = extract.run_program(command, work, environment, timeout)
        except OSError as exc:
            raise AlignLyricsError(f"{aligner.id} could not start: {exc}") from exc
        if code is None:
            raise AlignLyricsError(f"{aligner.id} ran past {timeout} seconds")
        if code != 0:
            raise AlignLyricsError(f"{aligner.id} exited {code}: {log[-600:].decode('utf-8', 'replace').strip()}")
        try:
            checked = bank.check_result(output_dir, aligner)
            alignment = json.loads((output_dir / checked["outputs"]["alignment"]["file"]).read_text("utf-8"))
        except (bank.BankError, OSError, ValueError) as exc:
            raise AlignLyricsError(str(exc)) from exc
        return {"checked": checked, "alignment": alignment, "seconds": round(time.monotonic() - clock, 3)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# -- the claim ----------------------------------------------------------------

def _decimal(value: Any, where: str) -> str:
    if not isinstance(value, str) or not proposers.world_module("documents").DECIMAL.fullmatch(value):
        raise AlignLyricsError(f"{where} must be decimal text")
    return value


def _sample(value: Any, rate: int, sample_rate: int, count: int, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AlignLyricsError(f"{where} must be a sample, an integer at least 0")
    return min(round(Fraction(value * sample_rate, rate)), count)   # round() of a Fraction is half to even


def timed_payload(alignment: Any, lines: list[list[str]], sample_rate: int, sample_count: int,
                  scale: str, scheme: str) -> dict[str, Any]:
    """The `timed_lyrics` payload from an aligner's alignment, without
    carried_by (see `carry`). Every locator is checked against the
    document's lines; the aligner's samples are placed on the stream."""
    if not isinstance(alignment, dict) or not isinstance(alignment.get("rate"), int) or alignment["rate"] <= 0 \
            or not isinstance(alignment.get("lines"), list):
        raise AlignLyricsError("the alignment must state its rate and lines")
    rate = alignment["rate"]
    place = lambda v, w: _sample(v, rate, sample_rate, sample_count, w)
    out_lines = []
    for index, line in enumerate(alignment["lines"]):
        at = f"alignment line {index}"
        if not isinstance(line, dict) or not isinstance(line.get("line"), int) \
                or not 1 <= line["line"] <= len(lines) or not isinstance(line.get("words"), list):
            raise AlignLyricsError(f"{at} must name a line of the document and its words")
        words = []
        for number, word in enumerate(line["words"]):
            here = f"{at} word {number}"
            if not isinstance(word, dict) or not isinstance(word.get("word"), int) \
                    or not 1 <= word["word"] <= len(lines[line["line"] - 1]):
                raise AlignLyricsError(f"{here} must name a word of line {line['line']}")
            length = len(lines[line["line"] - 1][word["word"] - 1])
            start, end = place(word.get("start"), f"{here} start"), place(word.get("end"), f"{here} end")
            if end <= start:
                continue
            item: dict[str, Any] = {"word": word["word"], "start_sample": start, "end_sample": end,
                                    "confidence": _decimal(word.get("confidence"), f"{here} confidence")}
            syllables = []
            for s_index, syllable in enumerate(word.get("syllables", [])):
                there = f"{here} syllable {s_index}"
                if not isinstance(syllable, dict) or not all(isinstance(syllable.get(k), int) for k in
                                                             ("syllable", "start_char", "end_char")) \
                        or not 0 <= syllable["start_char"] < syllable["end_char"] <= length:
                    raise AlignLyricsError(f"{there} must name characters of its word")
                syllables.append({"syllable": syllable["syllable"], "start_char": syllable["start_char"],
                                  "end_char": syllable["end_char"],
                                  "start_sample": max(start, place(syllable.get("start"), f"{there} start")),
                                  "end_sample": min(end, place(syllable.get("end"), f"{there} end")),
                                  "confidence": _decimal(syllable.get("confidence"), f"{there} confidence")})
            if syllables and all(s["end_sample"] > s["start_sample"] for s in syllables):
                item["syllables"] = syllables
            if words and start < words[-1]["end_sample"]:
                raise AlignLyricsError(f"{here} starts before the word before it ends")
            words.append(item)
        if not words:
            continue
        if len(words) > 200:
            raise AlignLyricsError(f"{at} places {len(words)} words; a line holds at most 200")
        out_lines.append({"line": line["line"], "start_sample": words[0]["start_sample"],
                          "end_sample": words[-1]["end_sample"],
                          "confidence": _decimal(line.get("confidence"), f"{at} confidence"), "words": words})
    if not out_lines:
        raise AlignLyricsError("the alignment places no word")
    return {"sample_rate": sample_rate, "scheme": scheme, "alignment": {"method": "forced alignment"},
            "confidence_scale": scale, "lines": out_lines}


def carry(payload: dict[str, Any], onsets: list[int], notes_revision: str) -> dict[str, int]:
    """Add `carried_by` to each syllable by CARRY_RULE, in place; returns the
    counts of syllables, carried ones, and melismas."""
    rate = payload["sample_rate"]
    lead, longest = round(CARRY_LEAD * rate), CARRY_MAX * rate
    syllables = sorted((s for line in payload["lines"] for word in line["words"] for s in word.get("syllables", [])),
                       key=lambda s: s["start_sample"])
    onsets = sorted(set(onsets))
    carried = melismas = 0
    for index, syllable in enumerate(syllables):
        start = syllable["start_sample"]
        following = syllables[index + 1]["start_sample"] if index + 1 < len(syllables) else start + longest + lead
        low, high = start - lead, min(following - lead, start + longest)
        sung = onsets[bisect_left(onsets, low):bisect_left(onsets, high)][:64]
        if sung:
            syllable["carried_by"] = {"notes": notes_revision, "onsets": sung}
            carried += 1
            melismas += len(sung) >= 2
    return {"syllables": len(syllables), "carried": carried, "melisma": melismas}


# -- the command ----------------------------------------------------------------

def _target(record: dict[str, Any]) -> dict[str, Any]:
    """A record as World's targets describe a pinned revision."""
    contracts = record.get("contracts") or []
    target = {"id": record["object_id"], "shape": record["shape"], "payload": record["payload"],
              "refs": record.get("refs", [])}
    if contracts:
        target["contract"] = (contracts[0]["id"], contracts[0]["version"])
    return target


def run(args: argparse.Namespace, env: dict[str, str], reader: adapter.ArchiveReader,
        staging: adapter.Staging | None, choose_stream: Callable, stream_claims: Callable,
        content_path: Callable) -> tuple[int, dict[str, Any]]:
    import propose
    documents = proposers.world_module("documents")
    for contract in (adapter.RUN_CONTRACT, TIMED_LYRICS):
        reader.citation(contract)
    started = adapter.now()
    artifact = reader.head(args.artifact)
    if artifact["shape"] != "artifact":
        raise AlignLyricsError(f"{args.artifact} is a {artifact['shape']}, not an Artifact")
    stream = choose_stream(reader, artifact, args.stream)
    try:
        propose.first_stream(stream_claims(reader, artifact), stream)
    except propose.ProposeError as exc:
        raise AlignLyricsError(str(exc)) from exc
    document = reader.head(args.document)
    if document["shape"] != "artifact" or document["object_id"] == artifact["object_id"]:
        raise AlignLyricsError(f"{args.document} is not a lyric document Artifact other than the mix")
    data = content_path(reader.root, document).read_bytes()
    try:
        lines = documents.document_lines(data, args.scheme)
    except ValueError as exc:
        raise AlignLyricsError(f"the document cannot be counted as {args.scheme}: {exc}") from exc
    notes = None
    if args.notes is not None:
        reader.citation(NOTES)
        notes = reader.head(args.notes)
        if not reader.cites(notes, NOTES) or not any(
                ref.get("role") == "source" and ref.get("revision_id") == artifact["revision_id"]
                for ref in notes.get("refs", [])):
            raise AlignLyricsError(f"{args.notes} is not a notes claim about this revision of the mix")

    aligner = load_aligner(Path(args.aligners), args.aligner)
    try:
        config = extract.load_config(args.config)
        programs = extract.programs_of(args, config)
    except extract.RunError as exc:
        raise AlignLyricsError(str(exc)) from exc
    try:
        store = propose.bank_of(args)
    except propose.ProposeError as exc:
        raise AlignLyricsError(str(exc)) from exc
    mix = artifact["payload"]["content"]["digest"]
    try:
        found = store.feature(mix, args.stem)
    except bank.BankError as exc:
        raise AlignLyricsError(str(exc)) from exc
    if found is None:
        return NOTHING_PROPOSED, {"status": "nothing_proposed", "code": "missing", "missing": [args.stem],
                                  "detail": f"the bank holds no {args.stem} of this mix; separate it first"}
    entry, output = found
    if entry.timeline is not None:
        raise AlignLyricsError(f"{args.stem} of this mix is a region entry; align-lyrics reads a whole stem")
    described = entry.output(output)
    if described["kind"] != "file":
        raise AlignLyricsError(f"{args.stem} is not a file")
    stem = (entry.path / described["file"], described["sha256"], described["media_type"])

    digest = document["payload"]["content"]["digest"]
    made = run_aligner(aligner, programs, stem, {"digest": digest, "scheme": args.scheme}, lines, args.timeout)
    outputs = made["checked"]["outputs"]
    mean = Decimal(str(outputs["mean_score"]["value"]))
    answer: dict[str, Any] = {
        "artifact": {"id": artifact["object_id"], "revision_id": artifact["revision_id"]},
        "document": {"id": document["object_id"], "revision_id": document["revision_id"]},
        "aligner": {"id": aligner.id, "version": aligner.version}, "mean_score": str(mean),
        "active_seconds": outputs["active_seconds"]["value"], "aligner_seconds": made["seconds"]}
    if mean < Decimal(GATE):
        return NOTHING_PROPOSED, {**answer, "status": "nothing_proposed", "code": "low_confidence",
                                  "detail": f"mean letter probability {mean} is below {GATE}: the alignment is lost"}

    scale = CONFIDENCE_SCALE.format(id=aligner.id, version=aligner.version)
    facts = stream["payload"]
    payload = timed_payload(made["alignment"], lines, facts["sample_rate"], facts["sample_count"], scale, args.scheme)
    counts = {"syllables": sum(len(w.get("syllables", [])) for l in payload["lines"] for w in l["words"]),
              "carried": 0, "melisma": 0}
    pins = [adapter.Pin("document", document["object_id"], document["revision_id"]),
            adapter.Pin("source", artifact["object_id"], artifact["revision_id"]),
            adapter.Pin("stream", stream["object_id"], stream["revision_id"])]
    if notes is not None:
        onsets = [n["onset_sample"] for n in notes["payload"].get("notes", []) if isinstance(n, dict)]
        counts = carry(payload, onsets, notes["revision_id"])
        if counts["carried"]:
            pins.append(adapter.Pin("notes", notes["object_id"], notes["revision_id"]))
    answer.update(lines=len(payload["lines"]), words=sum(len(l["words"]) for l in payload["lines"]), **counts)
    claim = {"payload": payload, "refs": [pin.ref() for pin in pins]}
    targets = {record["revision_id"]: _target(record) for record in (artifact, stream, document, notes) if record}
    try:
        documents.check_timed_lyrics(claim, targets)
        documents.check_locators(claim, data)
    except ValueError as exc:
        raise AlignLyricsError(f"World refuses the timed lyrics claim: {exc}") from exc

    parameters = {
        "aligner": {"id": aligner.id, "version": aligner.version, "parameters": aligner.manifest["parameters"],
                    "requires": aligner.manifest["requires"]},
        "language": aligner.manifest["parameters"]["language"],
        "syllabification": aligner.manifest["parameters"]["syllabification"],
        "stem": {"extractor": entry.manifest["extractor"]["id"], "key": entry.manifest["key"], "output": output,
                 "sha256": described["sha256"]},
        "document": {"scheme": args.scheme},
        "carried_by": CARRY_RULE if notes is not None else "no notes claim was given",
        "confidence_gate": GATE, "times": TIMES}
    software = [SOFTWARE]
    for item in [*entry.manifest["software"], *made["checked"]["software"]]:
        if (item["name"], item["version"]) not in software:
            software.append((item["name"], item["version"]))
    used = tuple(adapter.Pin("used", r["object_id"], r["revision_id"])
                 for r in (document, artifact, stream, *([notes] if notes is not None else [])))
    result = adapter.RunResult(agent=AGENT, method_id=aligner.id, method_version=aligner.version,
                               software=tuple(software[:32]), started_at=started, ended_at=adapter.now(), used=used,
                               outputs=(adapter.Output(TIMED_LYRICS, "lyrics", payload, tuple(pins)),),
                               parameters=parameters)
    previous = reader.equivalent_run(result)
    if previous is not None:
        return 0, {**answer, "status": "unchanged",
                   "run": {"id": previous["object_id"], "revision_id": previous["revision_id"]}}
    admission = adapter.propose(reader, result, door=args.door, staging=staging, dry_run=args.dry_run, env=env)
    return 0, {**answer, **admission.as_json()}
