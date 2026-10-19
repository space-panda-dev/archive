"""Variants: the note transcribers a set is scored against.

A variant takes one audio file and returns notes. It is built from its
settings in the bench root's `config.json` (`{"variants": {name: {...}}}`),
which say where its interpreter and any private program live; nothing about
the machine is committed here. Every variant states a name, a version and its
parameters, which a result records, and `provenance()` adds what it actually
ran (software versions, digests of the programs).

To add one, write a class with this interface and list it in `VARIANTS`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Protocol

from .score import Note, read_midi
from .sets import BenchError, sha256_file

CONFIG_CONTRACT = "archive-bench-config/1"
EXTRACTORS = Path(__file__).resolve().parent.parent / "extractors"


class Variant(Protocol):
    name: str
    version: str

    def parameters(self) -> dict[str, Any]: ...

    def provenance(self) -> dict[str, Any]: ...

    def transcribe(self, audio: Path, work: Path) -> list[Note]: ...


def _run(command: list[str], what: str, cwd: Path | None = None) -> None:
    done = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if done.returncode != 0:
        tail = "\n".join((done.stderr or done.stdout).strip().splitlines()[-8:])
        raise BenchError(f"{what} failed (exit {done.returncode}):\n{tail}")


def _setting_path(root: Path, settings: dict[str, Any], key: str, variant: str) -> Path:
    raw = settings.get(key)
    if not isinstance(raw, str) or not raw:
        raise BenchError(f"variant {variant}: config.json names no {key!r}")
    path = (root / raw).expanduser()
    if not path.exists():
        raise BenchError(f"variant {variant}: {key} {path} does not exist")
    return path


class BankBasicPitch:
    """The feature bank's notes-basic-pitch extractor, run through its own
    program and request, so the bench scores exactly what the bank writes.
    Settings: `python`, the interpreter the bank calls basic-pitch-python."""

    name = "basic-pitch"
    extractor = EXTRACTORS / "notes-basic-pitch"

    def __init__(self, root: Path, settings: dict[str, Any]) -> None:
        self.python = _setting_path(root, settings, "python", self.name)
        self.manifest = json.loads((self.extractor / "extractor.json").read_text(encoding="utf-8"))
        self.version = f"{self.manifest['id']}/{self.manifest['version']}"
        self.software: list[dict[str, str]] = []

    def parameters(self) -> dict[str, Any]:
        return self.manifest["parameters"]

    def provenance(self) -> dict[str, Any]:
        return {"program": f"enrich/extractors/{self.manifest['id']}/notes.py",
                "program_sha256": sha256_file(self.extractor / "notes.py"), "software": self.software}

    def transcribe(self, audio: Path, work: Path) -> list[Note]:
        out = work / "out"
        out.mkdir()
        request = work / "request.json"
        request.write_text(json.dumps({"parameters": self.parameters(), "output_dir": str(out),
                                       "input": {"path": str(audio)}}))
        _run([str(self.python), "notes.py", str(request)], f"{self.name} on {audio.name}", cwd=self.extractor)
        notes_file = out / "notes.json"
        if not notes_file.is_file():
            raise BenchError(f"{self.name} on {audio.name} exited 0 but wrote no notes.json")
        doc = json.loads(notes_file.read_text())
        columns = doc["columns"]
        at = {c: columns.index(c) for c in ("onset_s", "offset_s", "midi")}
        result = json.loads((out / "result.json").read_text())
        self.software = result.get("software", [])
        return [(float(r[at["onset_s"]]), float(r[at["offset_s"]]), float(r[at["midi"]])) for r in doc["notes"]]


# The horn settings of the melody note builder ("horn-d0"), from the
# transcription spike: no bridging across re-attacks, a split at any 3 dB
# loudness dip, 30 ms minimum notes, no onset shift.
HORN_D0 = {"bridge_s": 0.0, "dip_db_alone": 3.0, "dip_side_s": 0.04, "min_plateau_s": 0.03, "min_note_s": 0.03,
           "glide_s": 0.03, "stable_range_st": 1.0}


class SpikeBuilderAdapter:
    """ADAPTER, to be replaced: PESTO f0 and the melody note builder with the
    horn-d0 settings, run by the transcription spike's own scripts, which are
    private and live only where config.json says. When the builder is ported
    into the feature bank, a variant calling the bank's extractors replaces
    this class under the same name, and its baseline is recorded again.

    Settings: `python` (an interpreter with PESTO, torch, soundfile and
    pretty_midi), `f0_script` (audio -> f0 .npz at 10 ms), `builder_script`
    (f0 .npz, prefix -> <prefix>-<variant>.mid for each of its variants)."""

    name = "horn-d0"
    version = "spike-adapter/1"
    builder_variant = "horn-d0"

    def __init__(self, root: Path, settings: dict[str, Any]) -> None:
        self.python = _setting_path(root, settings, "python", self.name)
        self.f0_script = _setting_path(root, settings, "f0_script", self.name)
        self.builder_script = _setting_path(root, settings, "builder_script", self.name)

    def parameters(self) -> dict[str, Any]:
        return {"f0": "PESTO, 10 ms step, confidence and 10 ms RMS kept", "builder": HORN_D0, "onset_shift_s": 0.0}

    def provenance(self) -> dict[str, Any]:
        found = {p.name: sha256_file(p) for p in sorted(self.builder_script.parent.glob("*.py"))}
        return {"adapter": "private spike scripts named by config.json", "scripts_sha256": found}

    def transcribe(self, audio: Path, work: Path) -> list[Note]:
        f0 = work / "f0.npz"
        _run([str(self.python), str(self.f0_script), str(audio), str(f0)], f"{self.name} f0 on {audio.name}")
        _run([str(self.python), str(self.builder_script), str(f0), str(work / "est")],
             f"{self.name} builder on {audio.name}")
        midi = work / f"est-{self.builder_variant}.mid"
        if not midi.is_file():
            raise BenchError(f"{self.name} on {audio.name}: the builder wrote no {midi.name}")
        return read_midi(midi)


VARIANTS: dict[str, Callable[[Path, dict[str, Any]], Variant]] = {
    BankBasicPitch.name: BankBasicPitch,
    SpikeBuilderAdapter.name: SpikeBuilderAdapter,
}


def load_config(root: Path) -> dict[str, Any]:
    path = root / "config.json"
    if not path.is_file():
        raise BenchError(f"no {path}: it names each variant's interpreter and programs")
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("contract") != CONFIG_CONTRACT or not isinstance(config.get("variants"), dict):
        raise BenchError(f"{path}: must be {{'contract': {CONFIG_CONTRACT!r}, 'variants': {{...}}}}")
    return config


def build(name: str, root: Path) -> Variant:
    if name not in VARIANTS:
        raise BenchError(f"no variant {name!r}; known: {', '.join(sorted(VARIANTS))}")
    settings = load_config(root)["variants"].get(name)
    if not isinstance(settings, dict):
        raise BenchError(f"variant {name}: {root / 'config.json'} has no settings for it")
    return VARIANTS[name](root, settings)
