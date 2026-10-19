"""A regenerable cache of the window matcher's corpus profiles.

`enrich match-windows` compares a new recording with every corpus
occurrence, and each occurrence's pitch-class profiles were measured afresh
on every run: its pieces decoded and analysed again, although the archive's
corpus changes rarely. This cache keeps what `cover.Comparer.measure_corpus`
made of an occurrence's pieces, so the next run reads it instead.

It is not the feature bank (ADR 0073). The bank's `frames/hpcp` is a whole
file's profiles, and an occurrence's profiles are measured on its spans
alone; a slice of the whole differs at its edges (ADR 0073, Consequences),
so reading the bank would change the method. This cache returns the bits a
fresh measurement made, so the decisions do not change.

**What decides the key.** The SHA-256 of the canonical JSON of the cache's
format, the measure's settings (`cover.ANALYSIS`), the decoder invocation
and sample scale, the software the run records (the decoder's and its
libraries' versions, Essentia's, numpy's, and the BLAS library's), the
decoder executable's resolved path, the machine and its CPU model, and the
pieces: each one's content digest, span, and rate. Profiles measured on two
machines can differ in low bits, so a cache root shared by two machines
keeps each machine's entries apart.

**What an entry holds.** A directory `<root>/<ab>/<key hex>/` holding
`entry.json` and, for a measured occurrence, `profiles.bin`: the profiles'
raw little-endian bytes, whose shape, type, and SHA-256 the entry states. An
occurrence too short to compare is kept as its refusal's text. An entry
appears by one rename, complete or not at all; an entry that cannot be read,
or whose bytes are not the ones it states, is refused, never taken for a
miss: delete it, or the whole root, and the next run measures again.

**Regenerable.** The root is configuration, outside any archive and the
source tree, and every entry can be measured again from the content. Losing
any of it loses nothing; a run without a cache measures everything. Nothing
prunes it: entries for content the archive no longer holds, or for older
software, stay until the operator deletes them. Like the bank, it holds
measurements of recordings and stays in the operator's custody (ADR 0016).
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy

import cover

FORMAT = "archive-enrich/cover-profile-cache/1"


class CacheError(cover.CoverError):
    """An entry that is damaged, or a root that cannot hold entries."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def cpu_model() -> str:
    """The CPU's model name, as Linux or macOS reports it, or the machine
    type when neither answers."""
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
            name, _, value = line.partition(":")
            if name.strip() in ("model name", "Model", "Hardware") and value.strip():
                return value.strip()
    except OSError:
        pass
    return cover._cpu_brand() or platform.machine() or "unknown"


def executable(program: str) -> str:
    """The program as it will run: its resolved path."""
    found = shutil.which(program)
    return os.path.realpath(found) if found else program


class ProfileCache:
    """Corpus profiles under `root`, for runs that record `software` and
    decode with `ffmpeg`."""

    def __init__(self, root: Path, software: Sequence[tuple[str, str]], ffmpeg: str):
        self.root = Path(root)
        self.basis = {
            "format": FORMAT,
            "analysis": cover.ANALYSIS,
            "decode_arguments": cover.decode_arguments("{input}", "{start}", "{end}"),
            "sample_scale": 32768,
            "software": [list(pair) for pair in software],
            "decoder": executable(ffmpeg),
            "machine": [platform.system(), platform.machine(), cpu_model()],
        }
        self.hits = 0
        self.stored = 0

    def key(self, pieces: Sequence[cover.Piece], digests: Sequence[str]) -> str:
        """The key of one occurrence's pieces, given each piece's content
        digest."""
        if len(pieces) != len(digests) or not pieces:
            raise CacheError("every piece needs its content digest")
        described = [[digest, piece.start_sample, piece.end_sample, piece.sample_rate]
                     for piece, digest in zip(pieces, digests)]
        return hashlib.sha256(canonical({**self.basis, "pieces": described})).hexdigest()

    def directory(self, key: str) -> Path:
        return self.root / key[:2] / key

    def get(self, key: str) -> cover.Features | cover.TooShort | None:
        """The cached measurement: features, the refusal of an occurrence
        too short to compare, or None when there is no entry."""
        where = self.directory(key)
        if not where.exists():
            return None
        try:
            entry = json.loads((where / "entry.json").read_text(encoding="utf-8"))
            if entry.get("format") != FORMAT or entry.get("key") != key:
                raise CacheError("it is not an entry of this cache for this key")
            if "too_short" in entry:
                self.hits += 1
                return cover.TooShort(entry["too_short"])
            data = (where / "profiles.bin").read_bytes()
            if "sha256:" + hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise CacheError("its profiles are not the bytes it states")
            dtype = numpy.dtype(entry["dtype"])
            profiles = numpy.frombuffer(data, dtype=dtype).reshape(entry["shape"]).copy()
        except CacheError as exc:
            raise CacheError(f"the profile cache entry {where} is damaged ({exc}); delete it") from None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CacheError(f"the profile cache entry {where} is damaged ({exc}); delete it") from exc
        self.hits += 1
        return cover.features_of(profiles, f"the cached profiles {where}")

    def put(self, key: str, measured: cover.Features | cover.TooShort) -> None:
        """Keep one measurement; an entry already there is kept as it is."""
        where = self.directory(key)
        if where.exists():
            return
        entry: dict[str, Any] = {"format": FORMAT, "key": key, "basis": self.basis}
        data = b""
        if isinstance(measured, cover.TooShort):
            entry["too_short"] = str(measured)
        else:
            profiles = numpy.ascontiguousarray(measured.profiles)
            little = profiles.dtype.newbyteorder("<")
            data = profiles.astype(little, copy=False).tobytes()
            entry.update(dtype=little.str, shape=list(profiles.shape),
                         sha256="sha256:" + hashlib.sha256(data).hexdigest())
        try:
            where.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".tmp-", dir=where.parent))
        except OSError as exc:
            raise CacheError(f"the profile cache at {self.root} cannot hold entries: {exc}") from exc
        try:
            if data:
                (staging / "profiles.bin").write_bytes(data)
            (staging / "entry.json").write_bytes(canonical(entry) + b"\n")
            try:
                os.rename(staging, where)
            except OSError:
                if not where.exists():
                    raise
                return  # another run stored it first
            self.stored += 1
        except OSError as exc:
            raise CacheError(f"the profile cache at {self.root} cannot hold entries: {exc}") from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def measure_corpus(comparer: cover.Comparer, corpus: dict[str, tuple[cover.Piece, ...]],
                   digests: dict[str, tuple[str, ...]], cache: ProfileCache | None,
                   before_measuring: Callable[[list[str]], None] = lambda keys: None
                   ) -> dict[str, cover.Features | cover.CoverError]:
    """`comparer.measure_corpus(corpus)`, reading what `cache` holds and
    keeping what it measures. `digests` gives each entry's pieces' content
    digests. `before_measuring` is called with the entries about to be
    decoded, before any is; the comparer then compares against every entry
    measured or read, in the order `measure_corpus` gives them."""
    keys = sorted(corpus)
    found: dict[str, cover.Features | cover.CoverError] = {}
    cache_keys = {}
    if cache is not None:
        for key in keys:
            cache_keys[key] = cache.key(corpus[key], digests[key])
            hit = cache.get(cache_keys[key])
            if hit is not None:
                found[key] = hit
    missing = [key for key in keys if key not in found]
    if missing:
        before_measuring(missing)
        measured = comparer.measure_corpus({key: corpus[key] for key in missing})
        for key in missing:
            result = measured[key]
            found[key] = result
            if cache is not None and isinstance(result, (cover.Features, cover.TooShort)):
                cache.put(cache_keys[key], result)
    answer = {key: found[key] for key in keys}
    if len(missing) < len(keys):
        # measure_corpus gave the comparer only what it measured.
        comparer.use_corpus({key: value for key, value in answer.items() if isinstance(value, cover.Features)})
    return answer
