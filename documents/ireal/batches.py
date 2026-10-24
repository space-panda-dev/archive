"""Door batches for the iReal commands: drafts planned with their revisions,
written as proposal directories, and handed to `door admit`.

Every draft's revision is computed before Door sees it (the enrichment
adapter's `revision_of`), so a later batch can pin a revision of an earlier
one, and Door's answer is compared with the plan. Nothing here writes a
record: Door decides every revision (INV-2).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENRICH = Path(__file__).resolve().parents[2] / "enrich"
if str(ENRICH) not in sys.path:
    sys.path.insert(0, str(ENRICH))

import adapter  # noqa: E402  (the enrichment adapter: reading the archive, staging, identities)

PROTOCOL = "archive-admission/1"


class BatchError(Exception):
    """Door refused a batch, or answered other than the plan."""


@dataclass
class Batch:
    label: str
    reader: adapter.ArchiveReader
    drafts: list[dict[str, Any]] = field(default_factory=list)
    identities: list[dict[str, str]] = field(default_factory=list)
    contents: list[dict[str, Any]] = field(default_factory=list)
    staged: list[tuple[bytes, str]] = field(default_factory=list)   # (bytes, staging file name)

    def add(self, namespace: str, name: str, shape: str, payload: dict[str, Any], refs: list[dict[str, str]],
            *, agent: str, by: str, contract: tuple[str, str] | None = None) -> dict[str, str]:
        """Plan one new object's first revision; return its pin."""
        object_id = adapter.object_id(self.reader.archive_id, namespace, name)
        draft: dict[str, Any] = {"spec": self.reader.spec, "object_id": object_id, "shape": shape, "by": by,
                                 "agent": agent, "payload": payload, "refs": refs, "supersedes": []}
        if contract is not None:
            draft["contracts"] = [self.reader.citation(contract)]
        self.drafts.append(draft)
        self.identities.append({"object_id": object_id, "namespace": namespace, "name": name})
        return {"id": object_id, "revision_id": adapter.revision_of(draft)}

    def add_content(self, data: bytes, filename: str, staging: adapter.Staging, remote: str) -> None:
        """Content an Artifact of this batch carries, staged when admitted;
        `remote` is the archive's name for the staging repository."""
        self.contents.append({"digest": adapter.digest_of(data), "size": len(data),
                              "annex_key": staging.expected_key(data, filename),
                              "staging_remote": remote})
        self.staged.append((data, filename))

    def planned(self) -> list[str]:
        return [adapter.revision_of(draft) for draft in self.drafts]

    def manifest(self) -> dict[str, Any]:
        return {"protocol": PROTOCOL,
                "records": [f"records/{index:04d}.json" for index in range(len(self.drafts))],
                "identities": self.identities, "contents": self.contents,
                "expected_heads": [{"object_id": d["object_id"], "revision_id": None} for d in self.drafts]}

    def write(self, directory: Path) -> Path:
        """The batch as a proposal directory `door admit --proposal` reads."""
        (directory / "records").mkdir(parents=True)
        manifest = self.manifest()
        (directory / "manifest.json").write_bytes(adapter.canonical(manifest))
        for path, draft in zip(manifest["records"], self.drafts):
            (directory / path).write_bytes(adapter.canonical(draft))
        return directory


def combined(label: str, batches: list[Batch]) -> Batch:
    """Several batches as one, in order: what a dry run asks Door to predict,
    since a later batch pins revisions only an earlier one would commit."""
    whole = Batch(label, batches[0].reader)
    for batch in batches:
        whole.drafts += batch.drafts
        whole.identities += batch.identities
        whole.contents += batch.contents
        whole.staged += batch.staged
    return whole


def admit(batch: Batch, *, door: str, dry_run: bool, staging: adapter.Staging | None,
          env: dict[str, str] | None, keep: Path | None = None) -> list[str]:
    """Hand one batch to `door admit` (or `--dry-run`); return its revisions.
    Content is staged, and the staging remote configured, only for a real
    admission. With `keep`, the proposal directory is written there and kept."""
    if batch.contents and not dry_run:
        if staging is None:
            raise BatchError(f"batch {batch.label} carries content but no staging repository was given")
        for data, filename in batch.staged:
            staging.stage(data, filename)
        staging.remote_name(batch.reader.root, create=True)
    temporary = keep is None
    directory = Path(tempfile.mkdtemp(prefix="documents-proposal-")) if temporary else keep
    try:
        batch.write(directory)
        command = [door, "admit", "--proposal", str(directory)] + (["--dry-run"] if dry_run else [])
        try:
            completed = subprocess.run(command, cwd=batch.reader.root, env=env, capture_output=True, text=True)
        except OSError as exc:
            raise BatchError(f"door admit could not run: {exc}") from exc
    finally:
        if temporary:
            shutil.rmtree(directory, ignore_errors=True)
    if completed.returncode != 0:
        raise BatchError(f"Door refused batch {batch.label}: "
                         f"{adapter.refusal_text(completed.stderr) or completed.stdout.strip()}")
    try:
        answer = json.loads(completed.stdout)
    except ValueError:
        answer = None
    planned = batch.planned()
    expected = "predicted" if dry_run else "committed"
    if not isinstance(answer, dict) or answer.get("status") != expected or answer.get("revision_ids") != planned:
        raise BatchError(f"Door answered {str(answer)[:300]} for batch {batch.label}, but it was planned as "
                         f"{len(planned)} revisions")
    return planned
