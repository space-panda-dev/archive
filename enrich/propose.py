"""`enrich propose`: bank features proposed as claims (ADR 0079, D1.4).

    enrich propose --archive <dir> (--artifact <object id> [--stream <claim id>] | --all)
                   [--proposers chords-jiang,chords-chordino,beats,bass] [--bank <root>] [--config <file>]
                   [--staging <annex>] [--export <file>] [--dry-run]

For each Artifact, each named proposer (`proposers.py`) reads the bank
entries of the Artifact's content and, when it finds what it needs, its
claims are proposed with one derivation run in one Door batch: the run's
method is the proposer's id and version, its `used` pins the mix Artifact
revision and the stream claim, its parameters Artifact holds the proposer's
settings and every feature it read, and its software is the proposer's and
every read entry's recorded software. Each claim pins the mix as `source`,
the stream claim as `stream`, and the run, and has the tool's Package URL as
its agent. World's own check of each contract runs before Door sees it.

A run equivalent to a recorded one (same method, parameters, and inputs)
proposes nothing and answers `unchanged`: history stays quiet. A new
extractor version or a stem made again with other bits is a new key or
digest, so a new run, whose claims sit beside the old ones.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import adapter  # noqa: E402
import bank  # noqa: E402
import extract  # noqa: E402
import proposers  # noqa: E402

AGENT = "pkg:generic/archive-enrich-propose@0.1.0"
SOFTWARE = ("archive-enrich-propose", "0.1.0")
NOTHING_PROPOSED = 3
STRENGTH = ("nothing_proposed", "unchanged", "predicted", "committed")
STREAM_CONTRACT = ("archive.claim/audio_stream", "1")

# World's check of each contract a proposer makes, given the claim and the
# stream claim it pins: (module, function).
CHECKS: dict[tuple[str, str], tuple[str, str]] = {
    proposers.CHORD_STACKS: ("harmony", "check_chord_stacks_v2"),
    proposers.METER_LAYERS: ("form", "check_meter_layers_v2"),
    proposers.KEY_REGIONS: ("tonality", "check_key_regions"),
    proposers.TUNING: ("tonality", "check_tuning"),
    proposers.NOTES: ("notes", "check_notes"),
    proposers.FORM_SPANS: ("form", "check_form_spans"),
}


def world_check(contract: tuple[str, str]) -> Callable[[dict[str, Any], dict[str, Any]], Any]:
    module, function = CHECKS[contract]
    return getattr(proposers.world_module(module), function)


class ProposeError(Exception):
    """The command refused, or a proposer failed; `answer` holds what the
    proposers before it made, which is kept."""

    def __init__(self, detail: str, answer: dict[str, Any] | None = None):
        super().__init__(detail)
        self.answer = answer or {}


def proposer_names(text: str) -> list[str]:
    names = [name.strip() for name in text.split(",") if name.strip()]
    unknown = [name for name in names if name not in proposers.PROPOSERS]
    if not names or unknown or len(set(names)) != len(names):
        raise ProposeError(f"--proposers takes distinct names among {', '.join(proposers.PROPOSERS)}, "
                           f"not {text!r}")
    return names


def bank_of(args: argparse.Namespace) -> bank.Bank:
    """The bank, read with the catalog and parameters the extractor runner
    is configured with, so entries are found by the keys it gives them."""
    root = Path(args.bank) if args.bank else bank.environment_root()
    if root is None:
        raise ProposeError("no feature bank: give --bank or set ARCHIVE_FEATURE_BANK")
    if not root.is_dir():
        raise ProposeError(f"the feature bank {root} is not a directory")
    try:
        config = extract.load_config(args.config)
        directories = [Path(d) for d in config.get("extractors", [])]
        catalog = bank.Catalog.load(directories or [bank.EXTRACTORS])
        catalog.check_overrides(config.get("parameters", {}), f"configuration {args.config}")
    except (extract.RunError, bank.BankError) as exc:
        raise ProposeError(str(exc)) from exc
    return bank.Bank(root, catalog, config.get("parameters", {}))


def first_stream(claims: list[dict[str, Any]], chosen: dict[str, Any]) -> None:
    """The bank measures the first audio stream (every extractor decodes
    `0:a:0`), so its times are about that stream and no other."""
    first = min(claim["payload"]["stream_index"] for claim in claims)
    if chosen["payload"]["stream_index"] != first:
        raise ProposeError(f"{chosen['object_id']} describes stream {chosen['payload']['stream_index']}, but the bank "
                           f"measures the first audio stream ({first})")


def pin(record: dict[str, Any]) -> dict[str, str]:
    return {"id": record["object_id"], "revision_id": record["revision_id"]}


def strongest(statuses: list[str]) -> str:
    return max(statuses, key=STRENGTH.index, default="nothing_proposed")


def propose_one(proposer: proposers.Proposer, context: proposers.Context, reader: adapter.ArchiveReader,
                artifact: dict[str, Any], stream: dict[str, Any], args: argparse.Namespace,
                staging: adapter.Staging | None, env: dict[str, str]) -> dict[str, Any]:
    started = adapter.now()
    try:
        found = proposer.propose(context)
    except (proposers.ProposerError, bank.BankError) as exc:
        raise ProposeError(f"{proposer.id}: {exc}") from exc
    if isinstance(found, proposers.Lacking):
        return {"name": proposer.id, "status": "nothing_proposed", **found.as_json()}
    ended = adapter.now()
    refs = (adapter.Pin("source", artifact["object_id"], artifact["revision_id"]),
            adapter.Pin("stream", stream["object_id"], stream["revision_id"]))
    stream_claim = {"payload": stream["payload"], "refs": stream["refs"], "revision_id": stream["revision_id"]}
    outputs = []
    for planned in found.outputs:
        reader.citation(planned.contract)  # refuse an archive that cannot hold it before anything else
        claim = {"payload": planned.payload, "refs": [ref.ref() for ref in refs]}
        try:
            world_check(planned.contract)(claim, stream_claim)
        except ValueError as exc:
            raise ProposeError(f"{proposer.id}: World refuses its {planned.contract[0]} claim: {exc}") from exc
        outputs.append(adapter.Output(contract=planned.contract, name=planned.name, payload=planned.payload,
                                      refs=refs))
    software = (SOFTWARE, *found.software)
    if len(software) > 32:
        raise ProposeError(f"{proposer.id} read entries recording {len(software)} programs; a run lists at most 32")
    parameters = {"proposer": {"id": proposer.id, "version": proposer.version}, "settings": found.settings,
                  "features": found.features}
    used = (adapter.Pin("used", artifact["object_id"], artifact["revision_id"]),
            adapter.Pin("used", stream["object_id"], stream["revision_id"]))
    run = adapter.RunResult(agent=AGENT, method_id=proposer.id, method_version=proposer.version, software=software,
                            started_at=started, ended_at=ended, used=used, outputs=tuple(outputs),
                            parameters=parameters)
    previous = reader.equivalent_run(run)
    if previous is not None:
        return {"name": proposer.id, "status": "unchanged", "run": pin(previous)}
    admission = adapter.propose(reader, run, door=args.door, staging=staging, dry_run=args.dry_run, env=env)
    if not args.dry_run:
        reader.refresh(args.archive_command, env)
    return {"name": proposer.id, "status": admission.status,
            "run": {"id": admission.run["object_id"], "revision_id": admission.run["revision_id"]},
            "claims": len(admission.outputs)}


def propose_artifact(reader: adapter.ArchiveReader, artifact: dict[str, Any], stream: dict[str, Any],
                     names: list[str], store: bank.Bank, args: argparse.Namespace,
                     staging: adapter.Staging | None, env: dict[str, str]) -> dict[str, Any]:
    """Every named proposer on one Artifact, in order. A failed proposer
    fails the command once the ones before it have finished; what they made
    is kept and reported."""
    answer: dict[str, Any] = {"artifact": pin(artifact), "stream": pin(stream), "proposers": []}
    payload = stream["payload"]
    context = proposers.Context(store, artifact["payload"]["content"]["digest"], payload["sample_rate"],
                                payload["sample_count"])
    for name in names:
        try:
            answer["proposers"].append(propose_one(proposers.PROPOSERS[name], context, reader, artifact, stream,
                                                   args, staging, env))
        except (ProposeError, adapter.AdapterError) as exc:
            answer["proposers"].append({"name": name, "status": "failed"})
            answer["status"] = "failed"
            raise ProposeError(str(exc), answer) from exc
    answer["status"] = strongest([item["status"] for item in answer["proposers"]])
    return answer


def run(args: argparse.Namespace, env: dict[str, str], reader: adapter.ArchiveReader,
        staging: adapter.Staging | None, stream_claims: Callable[[adapter.ArchiveReader, dict[str, Any]], list],
        choose_stream: Callable[[adapter.ArchiveReader, dict[str, Any], str | None], dict[str, Any]]
        ) -> tuple[int, dict[str, Any]]:
    names = proposer_names(args.proposers)
    store = bank_of(args)
    reader.citation(adapter.RUN_CONTRACT)
    if not args.all:
        artifact = reader.head(args.artifact)
        if artifact["shape"] != "artifact":
            raise ProposeError(f"{args.artifact} is a {artifact['shape']}, not an Artifact")
        stream = choose_stream(reader, artifact, args.stream)
        first_stream(stream_claims(reader, artifact), stream)
        answer = propose_artifact(reader, artifact, stream, names, store, args, staging, env)
        return (NOTHING_PROPOSED if answer["status"] == "nothing_proposed" else 0), answer
    answers: list[dict[str, Any]] = []
    artifacts = [record for record in reader.records
                 if record["shape"] == "artifact" and reader.heads.get(record["object_id"]) is record]
    unprobed: list[dict[str, Any]] = []
    unexamined = 0
    for artifact in artifacts:
        artifact = reader.head(artifact["object_id"])  # the reader may have been refreshed since
        claims = stream_claims(reader, artifact)
        if not claims:
            media_type = local_media_type(reader.root, artifact)
            if media_type is None:
                unexamined += 1
            elif media_type.startswith(("audio/", "video/")):
                unprobed.append({"artifact": pin(artifact), "media_type": media_type})
            continue
        if len(claims) > 1:
            answers.append({"artifact": pin(artifact), "status": "nothing_proposed",
                            "detail": "several audio stream claims are about it; propose it alone with --stream"})
            continue
        try:
            first_stream(claims, claims[0])
            answers.append(propose_artifact(reader, artifact, claims[0], names, store, args, staging, env))
        except ProposeError as exc:
            if exc.answer:
                answers.append(exc.answer)
            raise ProposeError(str(exc), {"artifacts": answers}) from exc
    status = strongest([answer["status"] for answer in answers])
    answer = {"status": status, "artifacts": answers}
    answer.update(unprobed_report(unprobed, unexamined))
    return (NOTHING_PROPOSED if status == "nothing_proposed" else 0), answer


def local_media_type(root: Path, artifact: dict[str, Any]) -> str | None:
    """The media type the first bytes of the Artifact's local content declare
    ("" when they name none), or None when its content is not here to read."""
    hexdigest = artifact["payload"]["content"]["digest"].removeprefix("sha256:")
    path = Path(root) / "content" / "sha256" / hexdigest[0:2] / hexdigest[2:4] / hexdigest
    if not path.is_file():
        return None
    return extract.sniff_media_type(path) or ""


def unprobed_report(unprobed: list[dict[str, Any]], unexamined: int) -> dict[str, Any]:
    """What `--all` passed over because no audio_stream claim is about it:
    proposers measure against a probed stream, so a recording never probed
    proposes nothing until `enrich probe` has run on it."""
    if not unprobed and not unexamined:
        return {}
    parts = []
    if unprobed:
        parts.append(f"{len(unprobed)} audio or video Artifact{'s' if len(unprobed) != 1 else ''} "
                     f"{'have' if len(unprobed) != 1 else 'has'} no audio_stream claim, so nothing was proposed "
                     f"for {'them' if len(unprobed) != 1 else 'it'}: run `enrich probe --artifact <id>` on each "
                     f"listed in `unprobed`, then propose again")
    if unexamined:
        parts.append(f"{unexamined} other Artifact{'s' if unexamined != 1 else ''} with no audio_stream claim "
                     f"{'have' if unexamined != 1 else 'has'} no local content to examine")
    return {"unprobed": unprobed, "detail": "; ".join(parts)}


def add_arguments(command: argparse.ArgumentParser) -> None:
    target = command.add_mutually_exclusive_group(required=True)
    target.add_argument("--artifact", help="the mix Artifact's object identifier")
    target.add_argument("--all", action="store_true", help="every Artifact with an audio stream claim: the backfill")
    command.add_argument("--stream", help="the audio_stream claim, when the Artifact has several")
    command.add_argument("--proposers", default=",".join(proposers.DEFAULT),
                         help=f"comma-separated, in order (default {','.join(proposers.DEFAULT)}; also "
                              f"{', '.join(n for n in proposers.PROPOSERS if n not in proposers.DEFAULT)})")
    command.add_argument("--bank", help="the feature bank root (default: ARCHIVE_FEATURE_BANK)")
    command.add_argument("--config", help="the extractor runner's configuration (extract.py --config), whose "
                                          "parameters and extractors decide the entries' keys")
