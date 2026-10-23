"""`python -m enrich.bench region`: a region's entries against the whole run's.

Under `--songs` (ADR 0112) the expensive extractors run over song regions,
and a region's entries must stand for the whole run's over the region, held
to the tolerance a stand-in stem is (ADR 0090's amendment). This runs
`enrich/extract.py region-equivalence` over the root's equivalence clips:
for each, the stems and what measures them (notes-basic-pitch and f0-pesto
on the vocals and bass, melody on the vocals) whole and over the region
(15 to 45 s by default), compared over the region, with each stem's sample
difference as evidence. It reads the `equivalence` settings and an optional
`region` object of the root's `config.json`:

    "region": {"bank": "equivalence/region-bank", "seconds": "15:45",
               "programs": {"python": "<an interpreter with Essentia, for melody>"}}

`python`, `config` and `clips` default to the equivalence check's, `bank`
and `seconds` to the above, `programs` to none. An unchanged bank reruns
nothing.

Exit status: 0 every region agrees, 1 one does not, 2 no comparison could
be made.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from . import equivalence
from .sets import BenchError
from .variants import load_config

EXTRACT = Path(__file__).resolve().parent.parent / "extract.py"
FIELDS = {"python", "config", "bank", "clips", "seconds", "programs"}
DEFAULTS = {"bank": "equivalence/region-bank", "seconds": "15:45"}


def settings(root: Path) -> dict[str, Any]:
    own = load_config(root).get("region", {})
    if not isinstance(own, dict) or set(own) - FIELDS:
        raise BenchError(f"{root / 'config.json'}: region may hold only {', '.join(sorted(FIELDS))}")
    programs = own.get("programs", {})
    if not isinstance(programs, dict) or not all(isinstance(v, str) for v in programs.values()):
        raise BenchError(f"{root / 'config.json'}: region.programs maps names to paths")
    shared = equivalence.settings(root)
    found = {**DEFAULTS, **{k: shared[k] for k in ("python", "config", "clips")},
             **{k: v for k, v in own.items() if k != "programs"}}
    if not all(isinstance(found[k], str) for k in ("python", "config", "bank", "clips", "seconds")):
        raise BenchError(f"{root / 'config.json'}: each region setting but programs is text")
    return {**found, "programs": programs}


def command(root: Path) -> list[str]:
    """The extract.py command the check runs, every path resolved and present."""
    found = settings(root)
    paths = {key: (root / found[key]).expanduser() for key in ("python", "config", "bank", "clips")}
    for key in ("python", "config", "clips"):
        if not paths[key].exists():
            raise BenchError(f"region: {key} {paths[key]} does not exist")
    clips = sorted(p for p in paths["clips"].iterdir() if p.suffix in (".wav", ".flac"))
    if not clips:
        raise BenchError(f"region: no .wav or .flac clip under {paths['clips']}")
    argv = [str(paths["python"]), str(EXTRACT), "region-equivalence", "--seconds", found["seconds"],
            "--bank", str(paths["bank"]), "--config", str(paths["config"])]
    for name, path in sorted(found["programs"].items()):
        argv += ["--program", f"{name}={(root / path).expanduser()}"]
    for clip in clips:
        argv += ["--content", str(clip)]
    return argv


def check(root: Path) -> int:
    completed = subprocess.run(command(root))
    return completed.returncode if completed.returncode in (0, 1) else 2
