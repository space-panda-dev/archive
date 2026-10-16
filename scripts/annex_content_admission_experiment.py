#!/usr/bin/env python3
"""Probe git-annex history effects using disposable local repositories only.

The experiment creates a source repository and a directory special remote in a
fresh temporary directory. It never reads a user archive or contacts a network
remote. The JSON output records each git-annex branch snapshot and command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import Any


PAYLOAD = b"Archive synthetic git-annex admission probe\n\x00\xff\n"


class ExperimentError(RuntimeError):
    pass


def run(
    argv: list[str],
    cwd: Path,
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        raise ExperimentError(
            f"command failed ({result.returncode}): {argv!r}\n"
            f"stdout: {result.stdout.decode(errors='replace')}\n"
            f"stderr: {result.stderr.decode(errors='replace')}"
        )
    return result


def git(executable: str, cwd: Path, env: dict[str, str], *args: str) -> bytes:
    return run([executable, *args], cwd, env=env).stdout


def annex(
    executable: str, cwd: Path, env: dict[str, str], *args: str
) -> dict[str, Any]:
    argv = [executable, *args]
    result = run(argv, cwd, env=env)
    return {
        "argv": argv,
        "exit_code": result.returncode,
        "stdout": result.stdout.decode("utf-8", errors="replace").strip(),
        "stderr": result.stderr.decode("utf-8", errors="replace").strip(),
    }


def branch_snapshot(git_executable: str, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    ref = "refs/heads/git-annex"
    resolved = git(git_executable, cwd, env, "rev-parse", "--verify", ref).decode().strip()
    entries = git(git_executable, cwd, env, "ls-tree", "-rz", "--full-tree", resolved).split(b"\0")
    files: list[dict[str, Any]] = []
    for entry in entries:
        if not entry:
            continue
        metadata, path_bytes = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        if kind != "blob":
            continue
        data = git(git_executable, cwd, env, "cat-file", "blob", object_id)
        files.append({
            "path": path_bytes.decode("utf-8", errors="surrogateescape"),
            "mode": mode,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    files.sort(key=lambda item: item["path"].encode("utf-8", errors="surrogateescape"))
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {
        "ref": ref,
        "commit": resolved,
        "tree": git(git_executable, cwd, env, "rev-parse", f"{resolved}^{{tree}}").decode().strip(),
        "files": files,
        "branch_bytes_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def observe(
    label: str,
    command: dict[str, Any],
    git_executable: str,
    cwd: Path,
    env: dict[str, str],
    before: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    after = branch_snapshot(git_executable, cwd, env)
    return ({
        "label": label,
        "command": command,
        "before": before,
        "after": after,
        "branch_changed": before["commit"] != after["commit"],
        "branch_bytes_changed": before["branch_bytes_sha256"] != after["branch_bytes_sha256"],
    }, after)


def resolve_annex(explicit: str | None) -> str:
    candidate = explicit or os.environ.get("ARCHIVE_GIT_ANNEX_EXECUTABLE") or shutil.which("git-annex")
    if not candidate:
        raise ExperimentError("set ARCHIVE_GIT_ANNEX_EXECUTABLE or pass --git-annex")
    path = Path(candidate).expanduser().resolve(strict=True)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ExperimentError(f"git-annex is not an executable file: {path}")
    return str(path)


def resolve_git(explicit: str | None) -> str:
    candidate = explicit or os.environ.get("ARCHIVE_GIT_EXECUTABLE") or shutil.which("git")
    if not candidate:
        raise ExperimentError("set ARCHIVE_GIT_EXECUTABLE or pass --git")
    path = Path(candidate).expanduser().resolve(strict=True)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ExperimentError(f"Git is not an executable file: {path}")
    return str(path)


def isolated_environment(git_executable: str, home: Path) -> dict[str, str]:
    """Keep Git and git-annex away from ambient Git settings and redirection."""
    # Preserve PATH for git-annex's local helper programs, while selecting the
    # requested Git first. Drop every Git environment variable: these include
    # repository/index/object redirections, config injection, hooks, and SSH
    # command overrides.
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "LC_CTYPE"}
    }
    env["PATH"] = str(Path(git_executable).parent) + os.pathsep + env.get("PATH", os.defpath)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / "config")
    (home / "config").mkdir(parents=True, exist_ok=True)
    # Avoid system and user Git config, including global hooksPath and
    # init.templateDir. A private empty global config also works on platforms
    # without /dev/null.
    global_config = home / "gitconfig"
    global_config.write_text("", encoding="utf-8")
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = str(global_config)
    return env


def experiment(executable: str, git_executable: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="archive-annex-experiment-") as temporary:
        root = Path(temporary)
        repo = root / "repo"
        special_remote = root / "directory-remote"
        repo.mkdir()
        special_remote.mkdir()
        env = isolated_environment(git_executable, root / "home")
        run([git_executable, "init", "--quiet", "--template=/dev/null", str(repo)], root, env=env)
        git(git_executable, repo, env, "config", "--local", "core.hooksPath", os.devnull)
        git(git_executable, repo, env, "config", "user.name", "Archive synthetic experiment")
        git(git_executable, repo, env, "config", "user.email", "archive-experiment@example.invalid")
        annex(executable, repo, env, "init", "synthetic admission experiment")

        payload = repo / "payload.bin"
        payload.write_bytes(PAYLOAD)
        annex(executable, repo, env, "add", str(payload))
        key = run([executable, "lookupkey", "payload.bin"], repo, env=env).stdout.decode().strip()
        annex(
            executable,
            repo,
            env,
            "initremote",
            "synthetic",
            "type=directory",
            f"directory={special_remote}",
            "encryption=none",
        )
        annex(executable, repo, env, "copy", "--to=synthetic", "payload.bin")
        annex(executable, repo, env, "drop", "--force", "payload.bin")
        if payload.exists() and not payload.is_symlink():
            raise ExperimentError("drop did not remove the synthetic local content")

        operations: list[dict[str, Any]] = []
        state = branch_snapshot(git_executable, repo, env)
        cmd = annex(executable, repo, env, "get", "--from=synthetic", "payload.bin")
        item, state = observe("get_first", cmd, git_executable, repo, env, state)
        operations.append(item)

        cmd = annex(executable, repo, env, "get", "--from=synthetic", "payload.bin")
        item, state = observe("get_retry", cmd, git_executable, repo, env, state)
        operations.append(item)

        for label, args in (
            ("whereis_read_only", ("whereis", "--json", "payload.bin")),
            ("whereis_read_only_retry", ("whereis", "--json", "payload.bin")),
            ("info_read_only", ("info", "--json", "payload.bin")),
            ("info_read_only_retry", ("info", "--json", "payload.bin")),
            ("fsck_from_first", ("fsck", "--from=synthetic", "payload.bin")),
            ("fsck_from_retry", ("fsck", "--from=synthetic", "payload.bin")),
        ):
            before = state
            cmd = annex(executable, repo, env, *args)
            item, state = observe(label, cmd, git_executable, repo, env, before)
            operations.append(item)

        actual = payload.read_bytes()
        if actual != PAYLOAD:
            raise ExperimentError("retrieved synthetic bytes differ from the fixture")
        return {
            "schema": "archive-git-annex-experiment/1",
            "scope": "synthetic-local-only",
            "git_annex_executable": executable,
            "git_executable": git_executable,
            "git_annex_version": run([executable, "version"], repo, env=env).stdout.decode().splitlines()[0],
            "git_version": git(git_executable, repo, env, "--version").decode().strip(),
            "platform": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
            "synthetic_fixture": {
                "filename": "payload.bin",
                "size": len(PAYLOAD),
                "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                "annex_key": key,
            },
            "remote": {"type": "directory", "network_contact": False},
            "operations": operations,
            "result": {
                "first_get_changed_branch": operations[0]["branch_changed"],
                "retry_get_changed_branch": operations[1]["branch_changed"],
                "read_only_checks_changed_branch": any(
                    operation["branch_changed"] for operation in operations[2:6]
                ),
                "fsck_first_changed_branch": operations[6]["branch_changed"],
                "fsck_retry_changed_branch": operations[7]["branch_changed"],
                "retrieved_bytes_sha256": hashlib.sha256(actual).hexdigest(),
            },
            "limitations": [
                "This synthetic local probe does not satisfy the approved-real-archive-copy gate in docs/open-questions.md.",
                "It does not establish SSH transport, the managed repository target, Door durability, or filesystem power-loss behavior.",
            ],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-annex", help="absolute or resolvable path to the git-annex executable")
    parser.add_argument("--git", help="absolute or resolvable path to the Git executable")
    parser.add_argument("--output", type=Path, help="write JSON to this path instead of stdout")
    args = parser.parse_args()
    try:
        report = experiment(resolve_annex(args.git_annex), resolve_git(args.git))
    except (ExperimentError, OSError, subprocess.SubprocessError) as error:
        print(f"annex experiment failed: {error}", file=sys.stderr)
        return 1
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
