"""`python -m enrich.bench equivalence`: the stem equivalence check as a suite.

Runs `enrich/extract.py equivalence` (ADR 0090's amendment) over the bench
root's test clips and bank, under the interpreter and extract configuration
the root's `config.json` names:

    "equivalence": {"python": "envs/f0/bin/python",
                    "config": "equivalence/extract-config.json",
                    "bank": "equivalence/bank",
                    "clips": "equivalence/clips",
                    "extractor": "stems", "against": "mps"}

Every `*.wav` and `*.flac` under `clips` is a test input. The configured
side (the CPU's stems) is made here when missing; the other (the Mac GPU's)
must already be in the bank, made by the Mac's worker through Spool and
collected (`enrich/remote.py put ... --config <mps config>`, then
`collect`), or the check cannot happen. An unchanged bank reruns nothing,
so the suite costs the measures alone, and an upgrade of torch or demucs
makes entries of other software, which no longer match the record.

Exit status: 0 the record passes, 1 it fails, 2 no record could be made.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .sets import BenchError
from .variants import load_config

EXTRACT = Path(__file__).resolve().parent.parent / "extract.py"
FIELDS = {"python", "config", "bank", "clips", "extractor", "against"}


def settings(root: Path) -> dict[str, Any]:
    found = load_config(root).get("equivalence")
    if not isinstance(found, dict) or set(found) != FIELDS or not all(isinstance(v, str) for v in found.values()):
        raise BenchError(f"{root / 'config.json'}: equivalence must be {{{', '.join(sorted(FIELDS))}}}, each text")
    return found


def command(root: Path) -> list[str]:
    """The extract.py command the check runs, every path resolved and present."""
    found = settings(root)
    paths = {key: (root / found[key]).expanduser() for key in ("python", "config", "bank", "clips")}
    for key in ("python", "config", "clips"):
        if not paths[key].exists():
            raise BenchError(f"equivalence: {key} {paths[key]} does not exist")
    clips = sorted(p for p in paths["clips"].iterdir() if p.suffix in (".wav", ".flac"))
    if not clips:
        raise BenchError(f"equivalence: no .wav or .flac clip under {paths['clips']}")
    argv = [str(paths["python"]), str(EXTRACT), "equivalence", found["extractor"], "--against", found["against"],
            "--bank", str(paths["bank"]), "--config", str(paths["config"])]
    for clip in clips:
        argv += ["--content", str(clip)]
    return argv


def check(root: Path) -> int:
    completed = subprocess.run(command(root))
    return completed.returncode if completed.returncode in (0, 1) else 2
