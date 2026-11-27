#!/usr/bin/env python3
"""Make the derived media every admitted recording is missing (ADR 0069, 0129).

    enrich/media_backfill.py --archive <dir> --root <derived-media root>
                             [--export <file>] [--archive-command archive]
                             [--kind peaks|listen]... [--jobs N] [--dry-run]

`enrich/media.py` makes the waveform peaks and listening copy of one file;
`capture import` runs it after admitting a card (ADR 0070). Recordings
admitted any other way, by the V2 migration or by hand, have none, and the
relay refuses their media with `media_not_derived`. This command finds them
and runs `media.derive` on each.

A recording is a current Artifact that a current `audio_stream` claim is
about, as the enrichment backfill finds them (`enrich/backfill.py`,
`recordings_of`). Content a destroy transition names is left out: the relay
never serves its derivatives, and none may be made of it (ADR 0004, 0024).
Content that is not present in the archive's worktree is reported and left
out. Several Artifacts with one content share one set of derivatives.

It reads the archive through `archive export` (or an export a caller holds)
and writes only into the derived-media root, which is regenerable: it never
proposes, admits, or changes the archive. A rerun on complete outputs writes
nothing. `--dry-run` reads the same and writes nothing, and says what would
be made, how many seconds of audio that is, and about how many bytes.

It prints one JSON object: the counts, the estimate, and an item per
recording that is missing something (its digest, never a title). A failed
recording does not stop the others; the command then exits 1.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import derived  # noqa: E402
import media  # noqa: E402
from waveform import MediaError  # noqa: E402

STREAM = ("archive.claim/audio_stream", "1")
DESTROY = "archive.transition/destroy"
LISTEN_BYTES_PER_SECOND = 192_000 / 8  # the listening copy's bit rate (ADR 0069)
PEAKS_PIXELS_PER_SECOND = 100           # about ten milliseconds a pixel
# MP4's boxes and the AAC frames' own overhead, measured on synthetic tones
# and on the shadow's recordings: a little over the bit rate alone.
LISTEN_OVERHEAD = 1.03


class BackfillError(Exception):
    pass


@dataclass
class Recording:
    """One content digest the archive holds as a recording."""

    digest: str
    size: int
    objects: list[str] = field(default_factory=list)
    seconds: float | None = None
    channels: int | None = None

    def estimate(self, kind: str) -> int | None:
        """About how many bytes ``kind`` of this recording takes, or None
        without a stream claim saying how long it is."""
        if self.seconds is None:
            return None
        if kind == "listen":
            return round(self.seconds * LISTEN_BYTES_PER_SECOND * LISTEN_OVERHEAD)
        return round(self.seconds * PEAKS_PIXELS_PER_SECOND * 2 * (self.channels or 2))


def heads(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    superseded = {r["revision_id"] for x in records for r in x.get("supersedes", [])}
    return {x["object_id"]: x for x in records if x["revision_id"] not in superseded}


def _cites(record: dict[str, Any], contract: tuple[str, str] | str) -> bool:
    for cited in record.get("contracts", []):
        if isinstance(contract, str) and cited.get("id") == contract:
            return True
        if isinstance(contract, tuple) and (cited.get("id"), cited.get("version")) == contract:
            return True
    return False


def _payload_digest(record: dict[str, Any]) -> str | None:
    payload = record.get("payload")
    content = payload.get("content") if isinstance(payload, dict) else None
    digest = content.get("digest") if isinstance(content, dict) else None
    return digest if derived.is_digest(digest) else None


def recordings(export: dict[str, Any]) -> list[Recording]:
    """Every content digest of a current Artifact a current audio stream
    claim is about, less destroyed content, in digest order."""
    records = export.get("records")
    if export.get("status") != "ok" or not isinstance(records, list):
        raise BackfillError("the export is not a complete, successful `archive export`")
    current = heads(records)
    streams: dict[str, dict[str, Any]] = {}
    destroyed: set[str] = set()
    for record in current.values():
        if record.get("shape") == "claim" and _cites(record, STREAM):
            for ref in record.get("refs", []):
                if isinstance(ref, dict) and ref.get("role") == "artifact":
                    streams.setdefault(ref.get("id"), record.get("payload") or {})
    for record in records:
        if _cites(record, DESTROY) and (digest := _payload_digest(record)):
            destroyed.add(digest)
    found: dict[str, Recording] = {}
    for object_id, record in sorted(current.items()):
        if record.get("shape") != "artifact" or object_id not in streams:
            continue
        digest = _payload_digest(record)
        if digest is None:
            raise BackfillError(f"Artifact {object_id} has no canonical content digest")
        if digest in destroyed:
            continue
        stream = streams[object_id]
        rate, count = stream.get("sample_rate"), stream.get("sample_count")
        recording = found.setdefault(digest, Recording(digest, int(record["payload"]["content"].get("size", 0))))
        recording.objects.append(object_id)
        if isinstance(rate, int) and isinstance(count, int) and rate > 0:
            recording.seconds = max(recording.seconds or 0.0, count / rate)
        if isinstance(stream.get("channel_count"), int):
            recording.channels = max(recording.channels or 0, stream["channel_count"])
    return [found[digest] for digest in sorted(found)]


def content_path(archive: Path, digest: str) -> Path:
    """Where a digest's bytes are in the archive's content worktree (layout
    archive/2, D-1); `media.derive` checks the digest before writing."""
    hexdigest = digest.removeprefix("sha256:")
    return archive / "content" / "sha256" / hexdigest[0:2] / hexdigest[2:4] / hexdigest


def missing(root: Path, recording: Recording, kinds: tuple[str, ...]) -> list[str]:
    return [kind for kind in kinds if not media.COMPLETE[kind](derived.path(root, recording.digest, kind))]


def export_of(archive: Path, archive_command: str) -> dict[str, Any]:
    try:
        done = subprocess.run([archive_command, "export"], cwd=archive, capture_output=True, text=True)
    except OSError as exc:
        raise BackfillError(f"archive export failed: {exc}") from exc
    try:
        answer = json.loads(done.stdout)
    except ValueError:
        answer = None
    if done.returncode != 0 or not isinstance(answer, dict):
        raise BackfillError(f"archive export failed: {done.stdout[-500:]} {done.stderr[-500:]}")
    return answer


def plan(export: dict[str, Any], archive: Path, root: Path, kinds: tuple[str, ...]) -> dict[str, Any]:
    """What a backfill would make, read only."""
    found = recordings(export)
    items, complete, absent = [], 0, 0
    for recording in found:
        wanted = missing(root, recording, kinds)
        if not wanted:
            complete += 1
            continue
        source = content_path(archive, recording.digest)
        here = source.is_file() and source.stat().st_size == recording.size
        absent += not here
        items.append({"digest": recording.digest, "make": wanted, "objects": len(recording.objects),
                      "seconds": None if recording.seconds is None else round(recording.seconds, 1),
                      "source_bytes": recording.size, "content": "here" if here else "not here",
                      "estimated_bytes": {kind: recording.estimate(kind) for kind in wanted},
                      "_recording": recording, "_source": source})
    makeable = [item for item in items if item["content"] == "here"]
    return {
        "recordings": len(found),
        "complete": complete,
        "missing": {kind: sum(kind in item["make"] for item in items) for kind in kinds},
        "content_not_here": absent,
        "estimate": {
            "seconds_of_audio": round(sum(item["seconds"] or 0 for item in makeable), 1),
            "source_bytes_to_read": sum(item["source_bytes"] for item in makeable),
            "bytes_to_write": {kind: sum(item["estimated_bytes"].get(kind) or 0 for item in makeable)
                               for kind in kinds},
            "unknown_length": sum(item["seconds"] is None for item in makeable),
        },
        "items": items,
    }


def run(planned: dict[str, Any], root: Path, ffmpeg: str, ffprobe: str, jobs: int) -> bool:
    """Make every planned derivative whose content is here, `jobs` at a time;
    each item's `status` says what happened. True when none failed."""
    todo = [item for item in planned["items"] if item["content"] == "here"]
    for item in planned["items"]:
        if item["content"] != "here":
            item["status"] = "skipped: content not here"

    def one(item: dict[str, Any]) -> None:
        try:
            made = media.derive(item["_source"], item["digest"], root, ffmpeg=ffmpeg, ffprobe=ffprobe,
                                kinds=tuple(item["make"]))
            item["status"] = {kind: answer["status"] for kind, answer in made["outputs"].items()}
        except (MediaError, OSError) as exc:
            item["status"] = f"failed: {type(exc).__name__}: {exc}"[:500]
        print(json.dumps({"digest": item["digest"], "status": item["status"]}, sort_keys=True),
              file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        list(pool.map(one, todo))
    return not any(isinstance(item.get("status"), str) and item["status"].startswith("failed")
                   for item in todo)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="enrich-media-backfill", description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, required=True, help="the archive, read only")
    parser.add_argument("--root", type=Path, required=True, help="the derived-media root (ADR 0069)")
    parser.add_argument("--export", type=Path, help="an `archive export` already held, instead of running one")
    parser.add_argument("--archive-command", default="archive")
    parser.add_argument("--kind", action="append", choices=sorted(derived.KINDS),
                        help="make only this kind (repeatable); every kind when omitted")
    parser.add_argument("--jobs", type=int, default=1, help="recordings made at once (each is one ffmpeg)")
    parser.add_argument("--dry-run", action="store_true", help="say what would be made; write nothing")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args(argv)
    kinds = tuple(args.kind) if args.kind else tuple(derived.KINDS)
    try:
        if not args.root.is_dir():
            raise BackfillError(f"the derived-media root is not a directory: {args.root}")
        if args.export is not None:
            export = json.loads(args.export.read_text(encoding="utf-8"))
        else:
            export = export_of(args.archive, args.archive_command)
        planned = plan(export, args.archive, args.root, kinds)
        ok = True if args.dry_run else run(planned, args.root, args.ffmpeg, args.ffprobe, args.jobs)
    except (BackfillError, OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "detail": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 1
    for item in planned["items"]:
        item.pop("_recording"), item.pop("_source")
    planned["status"] = "dry-run" if args.dry_run else ("done" if ok else "failed")
    print(json.dumps(planned, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
