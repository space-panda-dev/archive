"""The capture import's fifth step: bank entries, then `enrich propose`
(ADR 0079, D1.4).

After window matching, when a feature bank root is configured (`--bank`, or
ARCHIVE_FEATURE_BANK), the import makes the mix's chord entries
(`chords-jiang`, `chords-chordino`) and the entries the default proposers
need (`rhythm`, and `tonal` with the `frames` it needs) for each Artifact of
each session's primary source, with `enrich/extract.py` and the runner
configuration given by `--extract-config`, then runs `enrich propose` on it
with the default proposers. It does not separate stems: `bass` answers
`nothing_proposed` until the stems exist, and a later `enrich propose --all`
proposes what has become available.

An extractor whose interpreter is not configured is skipped and said to be,
so a machine without a model still proposes what the others give. Without a
bank root the step is reported as skipped, not failed. A failure stops the
chain once the step has finished: every Artifact is still measured and
proposed, and what was made is kept and reported.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ENRICH = HERE.parent / "enrich"
sys.path.insert(0, str(ENRICH))

import bank  # noqa: E402
import extract  # noqa: E402

TARGETS = ("chords-jiang", "chords-chordino", "rhythm", "tonal")
SKIPPED = "skipped: no feature bank (give --bank or set ARCHIVE_FEATURE_BANK)"


class StepError(Exception):
    pass


def bank_root(args: argparse.Namespace, env: dict[str, str]) -> Path | None:
    value = getattr(args, "bank", None) or env.get("ARCHIVE_FEATURE_BANK")
    return Path(value) if value else None


def make_entries(root: Path, digest: str, path: Path, file_name: str, args: argparse.Namespace,
                 env: dict[str, str]) -> tuple[dict[str, str], str | None]:
    """Each target's entry for one recording, and the first failure. The
    answer maps every extractor the runner touched to what it did."""
    try:
        config = extract.load_config(args.extract_config)
        catalog = bank.Catalog.load([Path(d) for d in config.get("extractors", [])] or [bank.EXTRACTORS])
        media_type = extract.media_type_of(Path(file_name))
    except (extract.RunError, bank.BankError) as exc:
        return {}, str(exc)
    programs = {"python": sys.executable, "sh": "/bin/sh", **config.get("programs", {})}
    statuses: dict[str, str] = {}
    failure = None
    for target in TARGETS:
        missing = extract.unconfigured(catalog[target], programs)
        if missing is not None:
            statuses[target] = f"skipped: {missing} is not configured"
            continue
        arguments = [sys.executable, str(ENRICH / "extract.py"), "run", target, "--content", str(path),
                     "--digest", digest, "--media-type", media_type, "--bank", str(root), "--jobs", str(args.jobs)]
        if args.extract_config:
            arguments += ["--config", str(args.extract_config)]
        try:
            completed = subprocess.run(arguments, env=env, capture_output=True, text=True)
        except OSError as exc:
            statuses[target] = "failed"
            failure = failure or f"extract.py could not run: {exc}"
            continue
        try:
            answer = json.loads(completed.stdout or completed.stderr)
        except ValueError:
            answer = {}
        for extractor, done in answer.get("extractors", {}).items():
            statuses[extractor] = done.get("status", "failed") if isinstance(done, dict) else "failed"
        if completed.returncode != 0:
            statuses.setdefault(target, "failed")
            failure = failure or (f"extract.py run {target} on {file_name} failed ({completed.returncode}): "
                                  f"{answer.get('detail') or completed.stderr.strip()[-500:]}")
    return statuses, failure


def propose_all(primary: list[tuple[dict[str, Any], dict[str, Any]]], args: argparse.Namespace,
                env: dict[str, str], enrich: Callable[..., tuple[int, dict[str, Any]]],
                content_path: Callable[[Path, str], Path], refresh: Callable[[], Path]) -> None:
    """Make the entries and propose, for each (artifact, report entry) of the
    sessions' primary sources. `enrich` runs one enrich command; `refresh`
    brings the importer's export up to date and returns its file."""
    root = bank_root(args, env)
    if root is None:
        for _, entry in primary:
            entry["propose"] = SKIPPED
        return
    failure = None
    for artifact, entry in primary:
        statuses, failed = make_entries(root, artifact["digest"], content_path(args.archive, artifact["digest"]),
                                        artifact["file"], args, env)
        failure = failure or failed
        report: dict[str, Any] = {"bank": statuses}
        entry["propose"] = report
        if not root.is_dir():
            report["status"] = "failed"
            failure = failure or f"the feature bank {root} is not a directory"
            continue
        try:
            _, answer = enrich("propose", artifact["object_id"], args, env, refresh(), bank=root)
        except StepError as exc:
            report["status"] = "failed"
            failure = failure or str(exc)
            continue
        report.update(status=answer["status"], proposers=answer.get("proposers", []))
    if failure is not None:
        raise StepError(failure)


def add_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--bank", default=None,
                         help="the feature bank root; the import then makes the chord, rhythm, and tonal entries of "
                              "each primary recording and proposes their claims (default: ARCHIVE_FEATURE_BANK; "
                              "without either, that step is skipped)")
    command.add_argument("--extract-config", default=None,
                         help="the extractor runner's configuration (extract.py --config): the model interpreters "
                              "and any parameters, read by the runner and by enrich propose alike")

