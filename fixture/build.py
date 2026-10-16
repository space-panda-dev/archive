#!/usr/bin/env python3
"""Build Archive's published fixture archive through the real Door (ADR 0059).

The builder never writes a record itself. It runs `archive init`, stages the
fixture content in a git-annex repository beside the archive, and hands each
step of `plan.json` to `door admit` or `door transition` as a proposal. Door
decides every revision; the builder only checks that Door's revisions are the
ones it pinned in later drafts.

Standard library only, so a client's CI can run it from Archive's release
image or from a source checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BY = "pkg:generic/archive-fixture@1"
STAGING_REMOTE = "fixture-staging"
PLAN_PROTOCOL = "archive-fixture/1"
MANIFEST_PROTOCOL = "archive-fixture-manifest/1"


class BuildError(Exception):
    pass


def canonical(value: Any) -> bytes:
    """RFC 8785 for the values a plan holds: objects, arrays, strings, and
    integers. Door rechecks every revision this derives."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def revision_of(draft: dict[str, Any]) -> str:
    identity = {key: value for key, value in draft.items() if key not in ("revision_id", "at")}
    return "sha256:" + hashlib.sha256(canonical(identity)).hexdigest()


def object_id(archive_id: str, namespace: str, name: str) -> str:
    """D-2 identity allocation: UUIDv5 in the archive's namespace."""
    return "urn:uuid:" + str(uuid.uuid5(uuid.UUID(archive_id.removeprefix("urn:uuid:")), namespace + "\0" + name))


class Builder:
    def __init__(self, root: Path, plan: dict[str, Any], content: Path, door: str, archive: str,
                 git: str, annex: str, environment: dict[str, str]):
        self.root, self.plan, self.content = root, plan, content
        self.door, self.archive, self.git, self.annex = door, archive, git, annex
        self.environment = environment
        self.heads: dict[str, tuple[str, str]] = {}  # key -> (object_id, latest revision)
        self.keys_of_object: dict[str, str] = {}
        self.contents: dict[str, dict[str, Any]] = {}
        self.steps: list[dict[str, Any]] = []

    def run(self, arguments: list[str], cwd: Path, what: str) -> str:
        completed = subprocess.run(arguments, cwd=cwd, env=self.environment, capture_output=True, text=True)
        if completed.returncode != 0:
            raise BuildError(f"{what} failed ({completed.returncode}): "
                             f"{completed.stderr.strip() or completed.stdout.strip()}")
        return completed.stdout

    # -- set-up ----------------------------------------------------------

    def initialize(self) -> None:
        initialized = json.loads(self.run(
            [self.archive, "init", "--operator", self.plan["operator"], str(self.root)], self.root.parent,
            "archive init"))
        self.archive_id = initialized["archive_id"]
        self.operator_id = initialized["operator_id"]
        exported = json.loads(self.run([self.archive, "export"], self.root, "archive export"))
        if exported.get("status") != "ok":
            raise BuildError(f"archive export after init is not complete: {exported.get('status')!r}")
        self.spec = exported["spec"]
        operator = [r for r in exported["records"] if r["object_id"] == self.operator_id]
        if len(operator) != 1:
            raise BuildError("archive export does not hold exactly one operator revision")
        self.heads["operator"] = (self.operator_id, operator[0]["revision_id"])
        # The archive's installed bundles, by the digest their path names.
        self.installed = {}
        for path in sorted((self.root / "contracts").glob("sha256-*.json")):
            bundle = json.loads(path.read_bytes())
            self.installed[(bundle["id"], bundle["version"])] = "sha256:" + path.stem.removeprefix("sha256-")
        if not self.installed:
            raise BuildError("the new archive holds no contract bundles")

    def stage_content(self) -> None:
        names = sorted({record["content"] for step in self.plan["steps"] for record in step["records"]
                        if "content" in record})
        if not names:
            return
        staging = self.root.parent / (self.root.name + "-staging")
        staging.mkdir()
        self.run([self.git, "init", "-q"], staging, "git init for the staging repository")
        self.run([self.annex, "init", "-q", "fixture staging"], staging, "git-annex init")
        for name in names:
            source = self.content / name
            if not source.is_file():
                raise BuildError(f"fixture content {name} is missing from {self.content}")
            shutil.copyfile(source, staging / name)
        self.run([self.annex, "add", "-q", *names], staging, "git-annex add")
        self.run([self.git, "commit", "-q", "-m", "Stage fixture content"], staging, "staging commit")
        self.run([self.git, "remote", "add", STAGING_REMOTE, str(staging)], self.root, "adding the staging remote")
        for name in names:
            data = (staging / name).read_bytes()
            key = self.run([self.annex, "lookupkey", name], staging, f"git-annex lookupkey {name}").strip()
            self.contents[name] = {"digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                                   "size": len(data), "annex_key": key, "staging_remote": STAGING_REMOTE}

    # -- drafts ----------------------------------------------------------

    def resolve(self, value: Any) -> Any:
        if isinstance(value, str) and value.startswith("$content:"):
            declared = self.contents[value.removeprefix("$content:")]
            return {"digest": declared["digest"], "size": declared["size"]}
        if isinstance(value, dict):
            return {key: self.resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item) for item in value]
        return value

    def pin(self, key: str) -> tuple[str, str]:
        if key not in self.heads:
            raise BuildError(f"plan pins {key!r} before it exists")
        return self.heads[key]

    def draft(self, record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        key = record["key"]
        if key in self.heads:
            raise BuildError(f"plan key {key!r} is used twice")
        identity = None
        supersedes = []
        if "revises" in record:
            target, previous = self.pin(record["revises"])
            supersedes = [{"role": "supersedes", "id": target, "revision_id": previous}]
            target_id = target
        else:
            target_id = object_id(self.archive_id, record["namespace"], record["name"])
            identity = {"object_id": target_id, "namespace": record["namespace"], "name": record["name"]}
        payload = self.resolve(record.get("payload", {}))
        if record["shape"] == "artifact":
            declared = self.contents[record["content"]]
            payload = {"content": {"digest": declared["digest"], "size": declared["size"]}}
        refs = []
        for ref in record.get("refs", []):
            target, revision = self.pin(ref["pin"])
            refs.append({"role": ref["role"], "id": target, "revision_id": revision})
        draft = {"spec": self.spec, "object_id": target_id, "shape": record["shape"], "by": BY,
                 "agent": self.operator_id, "payload": payload, "refs": refs, "supersedes": supersedes}
        if "source" in record:
            draft["source"] = record["source"]
        if record.get("contracts"):
            citations = []
            for citation in record["contracts"]:
                digest = self.installed.get((citation["id"], citation["version"]))
                if digest is None:
                    raise BuildError(f"{citation['id']} version {citation['version']} is not installed")
                citations.append({"id": citation["id"], "version": citation["version"], "digest": digest})
            draft["contracts"] = citations
        return draft, identity

    # -- steps -----------------------------------------------------------

    def step(self, number: int, step: dict[str, Any]) -> None:
        command = step["command"]
        if command not in ("admit", "transition"):
            raise BuildError(f"step {step['name']!r} has unknown command {command!r}")
        before = dict(self.heads)
        drafts, identities, contents, paths, planned = [], [], [], [], []
        for record in step["records"]:
            draft, identity = self.draft(record)
            revision = revision_of(draft)
            self.heads[record["key"]] = (draft["object_id"], revision)
            drafts.append(draft)
            planned.append(revision)
            if identity:
                identities.append(identity)
            if record["shape"] == "artifact":
                contents.append(self.contents[record["content"]])
            paths.append(f"records/{len(paths):02d}-{record['key']}.json")
        drafted = {draft["object_id"] for draft in drafts}
        new = {identity["object_id"] for identity in identities}
        heads_needed = set(drafted)
        if command == "transition":
            heads_needed |= {ref["id"] for draft in drafts for ref in draft["refs"] + draft["supersedes"]}
        current = {object_: revision for object_, revision in before.values()}
        expected = [{"object_id": object_, "revision_id": None if object_ in new else current[object_]}
                    for object_ in sorted(heads_needed)]
        manifest: dict[str, Any] = {"records": paths, "identities": identities, "expected_heads": expected}
        if command == "admit":
            manifest["protocol"] = "archive-admission/1"
            manifest["contents"] = contents
        else:
            manifest["protocol"] = "archive-transition/1"
        proposal = self.root.parent / (self.root.name + "-proposals") / f"{number:02d}"
        (proposal / "records").mkdir(parents=True)
        (proposal / "manifest.json").write_bytes(canonical(manifest))
        for path, draft in zip(paths, drafts):
            (proposal / path).write_bytes(canonical(draft))
        answer = json.loads(self.run([self.door, command, "--proposal", str(proposal)], self.root,
                                     f"step {step['name']!r}: door {command}"))
        if answer.get("status") != "committed" or answer.get("revision_ids") != planned:
            raise BuildError(f"step {step['name']!r}: Door committed {answer} but the plan pinned {planned}")
        commit = self.run([self.git, "rev-parse", "HEAD"], self.root, "reading HEAD").strip()
        self.steps.append({"name": step["name"], "command": command, "commit": commit})

    def build(self) -> dict[str, Any]:
        self.initialize()
        self.stage_content()
        for number, step in enumerate(self.plan["steps"], start=1):
            self.step(number, step)
        cited = sorted({(c["id"], c["version"]) for step in self.plan["steps"]
                        for record in step["records"] for c in record.get("contracts", [])}
                       | {("archive.transition/operator_initialization", "1")})
        return {
            "protocol": MANIFEST_PROTOCOL,
            "archive_root": str(self.root),
            "archive_id": self.archive_id,
            "spec": self.spec,
            "operator_id": self.operator_id,
            "objects": {key: {"object_id": object_, "revision_id": revision}
                        for key, (object_, revision) in sorted(self.heads.items())},
            "steps": self.steps,
            "cited_contracts": [{"id": i, "version": v} for i, v in cited],
        }


def load_plan(path: Path) -> dict[str, Any]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or plan.get("protocol") != PLAN_PROTOCOL or not plan.get("steps"):
        raise BuildError(f"{path} is not an {PLAN_PROTOCOL} plan with steps")
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build-fixture-archive",
                                     description="Admit Archive's published fixture records into a fresh archive")
    parser.add_argument("root", type=Path, help="where to create the archive; must not exist")
    parser.add_argument("--plan", type=Path, default=HERE / "plan.json")
    parser.add_argument("--content", type=Path, default=HERE / "content")
    parser.add_argument("--door", default="door")
    parser.add_argument("--archive", default="archive")
    parser.add_argument("--manifest", type=Path, help="also write the fixture manifest here")
    args = parser.parse_args(argv)
    environment = dict(os.environ)
    for name, value in (("GIT_AUTHOR_NAME", "Archive fixture"), ("GIT_AUTHOR_EMAIL", "fixture@example.invalid"),
                        ("GIT_COMMITTER_NAME", "Archive fixture"), ("GIT_COMMITTER_EMAIL", "fixture@example.invalid")):
        environment.setdefault(name, value)
    root = args.root.absolute()
    try:
        # Door needs absolute executables; take them from PATH when unset.
        for variable, program in (("ARCHIVE_GIT_EXECUTABLE", "git"), ("ARCHIVE_GIT_ANNEX_EXECUTABLE", "git-annex")):
            if variable not in environment:
                found = shutil.which(program)
                if found is None:
                    raise BuildError(f"{program} is not on PATH and {variable} is unset")
                environment[variable] = os.path.realpath(found)
        git, annex = environment["ARCHIVE_GIT_EXECUTABLE"], environment["ARCHIVE_GIT_ANNEX_EXECUTABLE"]
        if root.exists():
            raise BuildError(f"{root} already exists; the fixture archive is always built fresh")
        if not root.parent.is_dir():
            raise BuildError(f"{root.parent} is not a directory")
        builder = Builder(root, load_plan(args.plan), args.content, args.door, args.archive, git, annex, environment)
        manifest = builder.build()
    except (BuildError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)}), file=sys.stderr)
        return 1
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.manifest:
        args.manifest.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
