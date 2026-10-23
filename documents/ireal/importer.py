"""`documents ireal import`: one iReal Pro playlist file into Door batches
(ADR 0116, section 1).

The playlist file is admitted verbatim as a document Artifact with an
`archive.claim/document_source` claim (the importer's, no run). Each chart is
its own document Artifact, `irealb://` and the chart's slice of the playlist
byte for byte, admitted in a batch of its own with one `ireal-chart`
derivation run (its `used` pins the playlist and the chart document, its
`parameters` the grammar's choices) and the run's claims about the chart
document: the chart's `document_source` (derived from the playlist, by the
run), `chord_sequence` v2, `form` v1, untimed `meter_layers` v3 (with the chart's
style, tempo and comments as written directions) and, when the key field is
not empty, `key_sequence` v1. Meter layers version 3 is specification
0.0.11's, so the archive must be at 0.0.11.

Quiet reruns: content already an Artifact's is not admitted again, and a
chart document some `ireal-chart` run with these parameters already used
yields nothing. A chart the grammar cannot read yields its document, its
source claim and the run, no other claim, and is listed in the report with
the reason; the command then exits 1.

A dry run asks Door to predict the playlist batch followed by up to
`DRY_RUN_CHARTS` chart batches as one proposal (chart batches pin the
revisions the playlist batch would commit), group by group, and stages and
configures nothing.
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENRICH = Path(__file__).resolve().parents[2] / "enrich"
if str(ENRICH) not in sys.path:
    sys.path.insert(0, str(ENRICH))

import adapter  # noqa: E402  (the enrichment adapter: reading the archive, staging, identities)
from batches import Batch, admit, combined
from chart import UnsupportedChart
from claims import METHOD, PARAMETERS, chart_claims
from codec import CodecError, chart_link, decode_playlist
from symbols import GRAMMAR_VERSION

VERSION = "0.1.0"
AGENT_PREFIX = "pkg:generic/archive-documents@"
AGENT = f"{AGENT_PREFIX}{VERSION}"
SOFTWARE = (("archive-documents", VERSION),)
ARTIFACT_NAMESPACE = "archive.core/artifact"
RUN_NAMESPACE = "archive.derivation/run"
RUN = ("archive.claim/derivation_run", "1")
SOURCE = ("archive.claim/document_source", "1")
SEQUENCE = ("archive.claim/chord_sequence", "2")
FORM = ("archive.claim/form", "1")
METER = ("archive.claim/meter_layers", "3")
KEY = ("archive.claim/key_sequence", "1")
CONTRACTS = (RUN, SOURCE, SEQUENCE, FORM, METER, KEY)
MEDIA_TYPE = "text/plain"
KINDS = ["chords"]


class ImportError_(Exception):
    """The import was refused before anything was proposed."""


@dataclass
class ChartPlan:
    number: int                    # 1-based position in the playlist
    digest: str
    status: str                    # read | failed | known | duplicate
    document: dict[str, str] | None = None
    reason: str | None = None
    text_only: int = 0
    batch: Batch | None = None
    outputs: dict[str, dict[str, str]] = field(default_factory=dict)

    def report(self) -> dict[str, Any]:
        out: dict[str, Any] = {"number": self.number, "digest": self.digest, "status": self.status}
        if self.document:
            out["document"] = self.document["id"]
        if self.reason:
            out["reason"] = self.reason
        if self.status == "read":
            out["text_only"] = self.text_only
        return out


def parameter_bytes() -> bytes:
    return adapter.canonical(PARAMETERS)


def ours(record: dict[str, Any]) -> bool:
    """Asserted by this tool at any release: the method's version and the
    parameters, not the package's version, say what a run read."""
    return record["agent"].startswith(AGENT_PREFIX)


def used_documents(reader: adapter.ArchiveReader, parameters_digest: str) -> set[str]:
    """Object IDs every recorded `ireal-chart` run of this grammar version and
    these parameters used: their charts yield nothing again."""
    used: set[str] = set()
    method = {"id": METHOD, "version": str(GRAMMAR_VERSION)}
    for record in reader.records:
        if record["shape"] != "claim" or not reader.cites(record, RUN) or not ours(record):
            continue
        if record["payload"].get("method") != method:
            continue
        pinned = [ref for ref in record.get("refs", []) if ref["role"] == "parameters"]
        artifact = reader.by_revision.get(pinned[0]["revision_id"]) if pinned else None
        if artifact is None or artifact["payload"]["content"]["digest"] != parameters_digest:
            continue
        used.update(ref["id"] for ref in record["refs"] if ref["role"] == "used")
    return used


def _pin(record: dict[str, Any]) -> dict[str, str]:
    return {"id": record["object_id"], "revision_id": record["revision_id"]}


def _ref(role: str, pin: dict[str, str]) -> dict[str, str]:
    return {"role": role, **pin}


def _staged_name(digest: str) -> str:
    return digest.removeprefix("sha256:") + ".txt"


def plan(data: bytes, filename: str, reader: adapter.ArchiveReader, staging: adapter.Staging, remote: str,
         *, retrieved_at: str | None, now=adapter.now) -> tuple[Batch | None, list[ChartPlan], dict[str, Any]]:
    """The playlist batch (None when nothing in it is new) and each chart's
    plan, in playlist order. Raises ImportError_ for a file that is not a
    playlist or an archive missing a contract."""
    for contract in CONTRACTS:
        reader.citation(contract)
    try:
        playlist = decode_playlist(data)
    except CodecError as error:
        raise ImportError_(f"{filename} is not an iReal Pro link: {error}") from None

    head = Batch("playlist", reader)
    digest = adapter.digest_of(data)
    existing = reader.artifact_with_digest(digest)
    if existing is not None:
        playlist_pin = _pin(existing)
    else:
        playlist_pin = head.add(ARTIFACT_NAMESPACE, "ireal-" + digest.removeprefix("sha256:"), "artifact",
                                {"content": {"digest": digest, "size": len(data)}}, [], agent=AGENT, by=AGENT)
        head.add_content(data, _staged_name(digest), staging, remote)
        payload: dict[str, Any] = {"media_type": MEDIA_TYPE, "kinds": KINDS, "filename": filename}
        if retrieved_at:
            payload["retrieved_at"] = retrieved_at
        head.add(SOURCE[0], "ireal-source-" + digest.removeprefix("sha256:"), "claim", payload,
                 [_ref("document", playlist_pin)], agent=AGENT, by=AGENT, contract=SOURCE)

    parameters = parameter_bytes()
    parameters_digest = adapter.digest_of(parameters)
    known_parameters = reader.artifact_with_digest(parameters_digest)
    if known_parameters is not None:
        parameters_pin = _pin(known_parameters)
    else:
        parameters_pin = head.add(ARTIFACT_NAMESPACE, "ireal-parameters-" + parameters_digest.removeprefix("sha256:"),
                                  "artifact", {"content": {"digest": parameters_digest, "size": len(parameters)}},
                                  [], agent=AGENT, by=AGENT)
        head.add_content(parameters, parameters_digest.removeprefix("sha256:") + ".json", staging, remote)

    used = used_documents(reader, parameters_digest)
    seen: set[str] = set()
    charts: list[ChartPlan] = []
    for number, song in enumerate(playlist.songs, start=1):
        link = chart_link(song)
        chart_digest = adapter.digest_of(link)
        item = ChartPlan(number, chart_digest, "read")
        charts.append(item)
        if chart_digest in seen:
            item.status = "duplicate"
            continue
        seen.add(chart_digest)
        known = reader.artifact_with_digest(chart_digest)
        if known is not None and known["object_id"] in used:
            item.status, item.document = "known", _pin(known)
            continue
        batch = Batch(f"chart-{number}", reader)
        if known is not None:
            document = _pin(known)
        else:
            document = batch.add(ARTIFACT_NAMESPACE, "ireal-" + chart_digest.removeprefix("sha256:"), "artifact",
                                 {"content": {"digest": chart_digest, "size": len(link)}}, [], agent=AGENT, by=AGENT)
            batch.add_content(link, _staged_name(chart_digest), staging, remote)
        item.document, item.batch = document, batch
        try:
            read = chart_claims(song.music, song.key, song.style,
                                song.field("tempo") if song.scheme == "irealb" else "")
        except UnsupportedChart as error:
            item.status, item.reason, read = "failed", str(error), None
        started = now()
        run_name = "run-" + str(uuid.uuid4())
        run = batch.add(RUN_NAMESPACE, run_name, "claim",
                        {"method": {"id": METHOD, "version": str(GRAMMAR_VERSION)},
                         "software": [{"name": n, "version": v} for n, v in SOFTWARE],
                         "started_at": adapter.utc_instant(started), "ended_at": adapter.utc_instant(now())},
                        [_ref("used", playlist_pin), _ref("used", document), _ref("parameters", parameters_pin)],
                        agent=AGENT, by=AGENT, contract=RUN)
        about = [_ref("document", document), _ref("run", run)]
        if known is None:
            item.outputs["source"] = batch.add(
                SOURCE[0], f"{run_name}-source", "claim", {"media_type": MEDIA_TYPE, "kinds": KINDS},
                [_ref("document", document), _ref("derived_from", playlist_pin), _ref("run", run)],
                agent=AGENT, by=AGENT, contract=SOURCE)
        if read is None:
            continue
        item.text_only = len(read.text_only)
        sequence = batch.add(SEQUENCE[0], f"{run_name}-sequence", "claim", read.sequence, about,
                             agent=AGENT, by=AGENT, contract=SEQUENCE)
        form = batch.add(FORM[0], f"{run_name}-form", "claim", read.form, about,
                         agent=AGENT, by=AGENT, contract=FORM)
        meter = batch.add(METER[0], f"{run_name}-meter", "claim", read.meter,
                          about + [_ref("form", form), _ref("sequence", sequence)],
                          agent=AGENT, by=AGENT, contract=METER)
        item.outputs.update(sequence=sequence, form=form, meter=meter)
        if read.key is not None:
            item.outputs["key"] = batch.add(KEY[0], f"{run_name}-key", "claim", read.key, about,
                                            agent=AGENT, by=AGENT, contract=KEY)
    report = {"playlist": {"digest": digest, "document": playlist_pin["id"], "new": existing is None,
                           "charts": len(playlist.songs)},
              "parameters": {"digest": parameters_digest, "new": known_parameters is None}}
    return (head if head.drafts else None), charts, report


# Charts per dry-run proposal (with the playlist batch before them).
DRY_RUN_CHARTS = 100

def run_import(path: Path, *, archive: Path, staging_path: Path, door: str = "door",
               archive_command: str = "archive", dry_run: bool = False, retrieved_at: str | None = None,
               proposals: Path | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Import one playlist file. Returns the report; its `status` is
    `committed`, `predicted` or `unchanged`, and `failed` counts the charts
    read into no claim."""
    data = Path(path).read_bytes()
    reader = adapter.ArchiveReader(archive, archive_command, env)
    git = (env or os.environ).get("ARCHIVE_GIT_EXECUTABLE", "git")
    annex = (env or os.environ).get("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")
    staging = adapter.Staging(staging_path, git, annex, env, remote="documents-staging")
    remote = staging.remote_name(reader.root, create=False)
    head, charts, report = plan(data, Path(path).name, reader, staging, remote, retrieved_at=retrieved_at)
    batches = ([head] if head else []) + [c.batch for c in charts if c.batch is not None]
    if proposals is not None:
        proposals.mkdir(parents=True, exist_ok=True)
    if batches:
        if dry_run:
            # Chart batches pin only what the playlist batch commits, so each
            # group of them is predicted after it; one proposal for a whole
            # long playlist would pass Door's proposal size limit.
            chart_batches = batches[1:] if head else batches
            groups = [chart_batches[i:i + DRY_RUN_CHARTS]
                      for i in range(0, len(chart_batches), DRY_RUN_CHARTS)] or [[]]
            for index, group in enumerate(groups):
                whole = combined("import", ([head] if head else []) + group)
                admit(whole, door=door, dry_run=True, staging=None, env=env,
                      keep=proposals / f"{index:04d}-import" if proposals else None)
        else:
            for index, batch in enumerate(batches):
                admit(batch, door=door, dry_run=False, staging=staging, env=env,
                      keep=proposals / f"{index:04d}-{batch.label}" if proposals else None)
    counts: dict[str, int] = {}
    for chart in charts:
        counts[chart.status] = counts.get(chart.status, 0) + 1
    report.update(status="unchanged" if not batches else ("predicted" if dry_run else "committed"),
                  batches=len(batches), records=sum(len(b.drafts) for b in batches), counts=counts,
                  text_only=sum(c.text_only for c in charts),
                  failed=[c.report() for c in charts if c.status == "failed"],
                  charts=[c.report() for c in charts])
    return report
