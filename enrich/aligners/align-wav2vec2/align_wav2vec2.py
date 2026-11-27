#!/usr/bin/env python3
"""The align-wav2vec2 aligner: a known lyric document's words forced onto a
vocal stem by a CTC character model, in its own environment (ADR 0085,
amendment of 2026-11-27).

`enrich align-lyrics` starts this program with the interpreter the operator
configured as `align-python` (ADR 0074), so the archive's environment never
imports PyTorch, and with the directory the operator configured as
`align-weights`, which must hold the pinned checkpoint. Only numpy and
`ffmpeg` are needed until the model is loaded; `torch`, `torchaudio`, and
`pyphen` are imported by `load_model` and `load_hyphenator` alone, so the
text and syllable rules below can be tested without them.

What it does, every setting fixed (the request must name exactly
`parameters()`):

- decodes the stem's first audio stream as 16 kHz mono float32;
- skips the silent stem: 0.25 s blocks below max(p95 - 35 dB, -55 dBFS) are
  left out, the rest padded by 1 s and gaps under 2 s merged; a skipped frame
  is the model's blank;
- runs torchaudio's `WAV2VEC2_ASR_BASE_960H` (fairseq weights, MIT) over
  30 s chunks with 2 s of context either side, on a 20 ms frame grid;
- spells each document word as the model's letters (NFKD, combining marks
  dropped, lower case a-z and the apostrophe, a word of digits alone spelled
  out), separated by `|` within a line, with a free `*` token (log
  probability 0) at every line boundary, and aligns the whole document in
  one pass (`torchaudio.functional.forced_align`), with no line anchors;
- splits each word into syllables with Pyphen `en_US` (left 2, right 2),
  falling back to a vowel-group split where Pyphen keeps the word whole; a
  silent final "e" and a plain "-es" are not syllables; a syllable starts at
  its first letter's frame.

It writes `alignment.json`: for every document line with letters, its line
number (counted as World counts lines), and each word with letters by its
number in the line, its start and end sample at 16 kHz, its mean character
probability as decimal text, and its syllables by code point offsets into
the document's word. The lyric's characters are never written: a locator
and its times are all it says. `mean_score` is the mean character
probability over the whole document, which the command gates on.

Invoked as `<align-python> align_wav2vec2.py <weights directory> <request.json>`.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import sys
import unicodedata
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, Callable, Iterable

RATE = 16000
FRAME = 320                       # samples a model frame: 20 ms at 16 kHz
CHUNK_SECONDS = 30
CONTEXT_SECONDS = 2
THREADS = 2                       # a setting: another count gives other floats, so another run
BUNDLE = "WAV2VEC2_ASR_BASE_960H"
WEIGHTS_FILE = "wav2vec2_fairseq_base_ls960_asr_ls960.pth"
WEIGHTS = "sha256:488fd4f16de84438ffc945334278c1b9fb9b7159a806c1080b16111a958c945d"
TORCHAUDIO = "2.11.0"
LANGUAGE = "en"
GATE = {"block_seconds": "0.25", "below_p95_db": 35, "floor_dbfs": -55, "pad_seconds": 1, "merge_seconds": 2}
SYLLABIFICATION = ("the document word's code points, each spelled as its letters (NFKD, combining marks dropped, "
                   "lower case a-z and the apostrophe); Pyphen en_US left=2 right=2 on the letters, and where it "
                   "keeps the word whole a cut between vowel groups (before the consonant of one, after the first "
                   "of several), a silent final e (not -le) and a plain -es (not after s, z, x, ch, sh, c, g) "
                   "making no vowel group of their own; a cut inside one code point's letters moves to its end; "
                   "a word of digits alone is one syllable")
NUMBERS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven",
           "8": "eight", "9": "nine", "10": "ten", "11": "eleven", "12": "twelve", "13": "thirteen",
           "14": "fourteen", "15": "fifteen", "16": "sixteen", "17": "seventeen", "18": "eighteen",
           "19": "nineteen", "20": "twenty", "30": "thirty", "40": "forty", "50": "fifty", "100": "hundred"}
VOWELS = re.compile(r"[aeiouy]+")


class AlignError(Exception):
    pass


def decode_arguments(path: str) -> list[str]:
    """The ffmpeg arguments after the program name: the first audio stream,
    mixed to mono, as 16 kHz float32."""
    return ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-i", path, "-map", "0:a:0", "-ac", "1",
            "-ar", str(RATE), "-c:a", "pcm_f32le", "-f", "f32le", "-"]


def parameters() -> dict[str, Any]:
    """The settings, which the manifest must declare exactly."""
    return {"aligner": f"torchaudio=={TORCHAUDIO} pipelines.{BUNDLE}, functional.forced_align",
            "model": BUNDLE, "weights": WEIGHTS, "weights_file": WEIGHTS_FILE, "language": LANGUAGE,
            "rate": RATE, "frame": FRAME, "threads": THREADS, "chunk_seconds": CHUNK_SECONDS, "context_seconds": CONTEXT_SECONDS,
            "pass": "the whole document in one pass over the whole stem, no line anchors; a free * token "
                    "(log probability 0) at every line boundary, | between words",
            "gate": GATE, "text": "NFKD, combining marks dropped, lower case a-z and the apostrophe (a leading or "
                                  "trailing apostrophe dropped), a word of digits alone spelled out",
            "syllabification": SYLLABIFICATION, "syllable_start": "the frame of the syllable's first letter",
            "confidence": "mean CTC posterior probability of the aligned letters, 0 to 1, to 3 places",
            "decode_arguments": decode_arguments("{input}")}


# -- text: the model's letters for a document word ---------------------------

def letters_of(character: str) -> str:
    """One code point as the model's letters: NFKD, combining marks dropped,
    lower case, a-z and the apostrophe (curly ones too) kept."""
    spelled = unicodedata.normalize("NFKD", character)
    spelled = "".join(c for c in spelled if not unicodedata.combining(c)).lower()
    spelled = spelled.replace("’", "'").replace("‘", "'")
    return "".join(c for c in spelled if "a" <= c <= "z" or c == "'")


def spelled(word: str) -> list[tuple[str, int]]:
    """A document word as (letter, code point index) pairs: each code point's
    letters, a leading or trailing apostrophe dropped; a word of digits alone
    (punctuation around it aside) spelled out, every letter on its first
    digit."""
    digits = re.sub(r"[^0-9]", "", word)
    if digits and re.fullmatch(r"[^0-9A-Za-z]*[0-9]+[^0-9A-Za-z]*", word) and digits in NUMBERS:
        first = next(i for i, c in enumerate(word) if c.isdigit())
        return [(letter, first) for letter in NUMBERS[digits]]
    pairs = [(letter, index) for index, character in enumerate(word) for letter in letters_of(character)]
    while pairs and pairs[0][0] == "'":
        pairs.pop(0)
    while pairs and pairs[-1][0] == "'":
        pairs.pop()
    return pairs


def vowel_cuts(letters: str) -> list[int]:
    """Cut positions between vowel groups: before a lone consonant, after the
    first of several; a silent final e (not -le) and a plain -es make no
    group of their own."""
    groups = [m.span() for m in VOWELS.finditer(letters)]
    if len(groups) >= 2 and letters.endswith("e") and groups[-1] == (len(letters) - 1, len(letters)) \
            and not letters.endswith("le"):
        groups = groups[:-1]
    if letters.endswith("es") and len(groups) >= 2 and groups[-1] == (len(letters) - 2, len(letters) - 1) \
            and not re.search(r"(s|z|x|ch|sh|c|g)es$", letters):
        groups = groups[:-1]
    if len(groups) < 2 or len(letters) < 3:
        return []
    cuts = []
    for (_, end), (start, _) in zip(groups, groups[1:]):
        cuts.append(end if start - end <= 1 else end + 1)
    return [c for c in cuts if 0 < c < len(letters)]


def syllables_of(word: str, hyphenate: Callable[[str], list[int]] | None) -> list[tuple[int, int]]:
    """A word's syllables as code point ranges [start, end) covering the word,
    by SYLLABIFICATION; `hyphenate` gives Pyphen's cut positions in a string
    of letters (None: the vowel rule alone, for a test). A word without
    letters has none."""
    pairs = spelled(word)
    if not pairs:
        return []
    if pairs[0][1] == pairs[-1][1] and len(set(i for _, i in pairs)) == 1:
        return [(0, len(word))]
    letters = "".join(letter for letter, _ in pairs).replace("'", "")
    owners = [index for letter, index in pairs if letter != "'"]
    cuts = hyphenate(letters) if hyphenate is not None else []
    if not cuts:
        cuts = vowel_cuts(letters)
    bounds = []
    for cut in cuts:
        # The code point holding the letter after the cut starts a syllable,
        # unless the letter before is in that same code point (a ligature):
        # then the syllable starts after it.
        before, after = owners[cut - 1], owners[cut]
        bound = after if after != before else before + 1
        if 0 < bound < len(word) and (not bounds or bound > bounds[-1]):
            bounds.append(bound)
    edges = [0, *bounds, len(word)]
    return [(a, b) for a, b in zip(edges, edges[1:]) if b > a]


def load_hyphenator() -> tuple[Callable[[str], list[int]], str]:
    try:
        import pyphen
        from importlib.metadata import version
    except ImportError as exc:
        raise AlignError(f"this interpreter has no pyphen ({exc}); configure align-python") from exc
    dictionary = pyphen.Pyphen(lang="en_US", left=2, right=2)
    return (lambda letters: list(dictionary.positions(letters))), version("pyphen")


# -- the token sequence and what the alignment says of it --------------------

def tokens(lines: list[list[str]], labels: dict[str, int], separator: int, star: int) \
        -> tuple[list[int], list[tuple[int, int, int] | None]]:
    """The whole document as one token sequence, and for each token the
    (line index, word index, code point index) it spells, or None for `*`
    and `|`. Letters the model lacks are dropped."""
    sequence, owners = [star], [None]
    for line_index, words in enumerate(lines):
        placed = False
        for word_index, word in enumerate(words):
            ids = [(labels[letter.upper()], index) for letter, index in spelled(word) if letter.upper() in labels]
            if not ids:
                continue
            if placed:
                sequence.append(separator)
                owners.append(None)
            for token, index in ids:
                sequence.append(token)
                owners.append((line_index, word_index, index))
            placed = True
        if placed:
            sequence.append(star)
            owners.append(None)
    return sequence, owners


def decimal3(value: float) -> str:
    return format(Decimal(repr(float(value))).quantize(Decimal("0.001"), ROUND_HALF_EVEN), "f")


def assemble(lines: list[list[str]], owners: list[tuple[int, int, int] | None],
             spans: list[tuple[int, int, float]], hyphenate: Callable[[str], list[int]] | None) -> dict[str, Any]:
    """The alignment from each token's (start frame, end frame exclusive,
    probability): lines and words in samples at RATE, syllables starting at
    their first letter's frame and ending where the next starts (the last
    at its word's end). Pure, so a test can give it spans."""
    if len(spans) != len(owners):
        raise AlignError(f"the aligner placed {len(spans)} tokens of {len(owners)}")
    letters: dict[tuple[int, int], list[tuple[int, int, int, float]]] = {}
    for owner, (start, end, score) in zip(owners, spans):
        if owner is not None:
            letters.setdefault(owner[:2], []).append((owner[2], start, end, score))
    out_lines, scores = [], []
    for line_index, words in enumerate(lines):
        placed = []
        for word_index, word in enumerate(words):
            found = letters.get((line_index, word_index))
            if not found:
                continue
            word_scores = [s for _, _, _, s in found]
            scores.extend(word_scores)
            start, end = found[0][1] * FRAME, found[-1][2] * FRAME
            item: dict[str, Any] = {"word": word_index + 1, "start": start, "end": end,
                                    "confidence": decimal3(sum(word_scores) / len(word_scores))}
            syllables = []
            for number, (a, b) in enumerate(syllables_of(word, hyphenate), start=1):
                inside = [(s, sc) for index, s, _, sc in found if a <= index < b]
                if not inside:
                    if syllables:   # no letter of its own: the syllable before holds its characters
                        syllables[-1]["end_char"] = b
                    continue
                syllables.append({"start_char": a, "end_char": b, "start": inside[0][0] * FRAME,
                                  "confidence": decimal3(sum(sc for _, sc in inside) / len(inside))})
            if syllables:
                syllables[0]["start_char"] = 0
                for number, syllable in enumerate(syllables, start=1):
                    syllable["syllable"] = number
                    syllable["end"] = syllables[number]["start"] if number < len(syllables) else end
                if all(s["end"] > s["start"] for s in syllables):
                    item["syllables"] = syllables
            placed.append(item)
        if placed:
            line_scores = [float(w["confidence"]) for w in placed]
            out_lines.append({"line": line_index + 1, "start": placed[0]["start"], "end": placed[-1]["end"],
                              "confidence": decimal3(sum(line_scores) / len(line_scores)), "words": placed})
    mean = sum(scores) / len(scores) if scores else 0.0
    return {"rate": RATE, "lines": out_lines, "mean_score": decimal3(mean)}


# -- audio --------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def decode(path: str, ffmpeg: str = "ffmpeg") -> Any:
    import numpy
    completed = subprocess.run([ffmpeg, *decode_arguments(path)], stdin=subprocess.DEVNULL, capture_output=True)
    if completed.returncode != 0 or len(completed.stdout) % 4:
        raise AlignError(f"ffmpeg could not decode {path}: {completed.stderr.decode('utf-8', 'replace').strip()[:300]}")
    signal = numpy.frombuffer(completed.stdout, dtype="<f4").copy()
    if not len(signal):
        raise AlignError(f"{path} decodes to no samples")
    return signal


def active_regions(signal: Any) -> list[tuple[int, int]]:
    """Where the stem has signal, in samples (GATE)."""
    import numpy
    hop = RATE // 4
    blocks = signal[: len(signal) // hop * hop].reshape(-1, hop).astype(numpy.float64)
    if not len(blocks):
        return [(0, len(signal))]
    level = 20 * numpy.log10(numpy.sqrt((blocks ** 2).mean(axis=1)) + 1e-9)
    threshold = max(float(numpy.percentile(level, 95)) - GATE["below_p95_db"], GATE["floor_dbfs"])
    regions: list[list[int]] = []
    for index, loud in enumerate(level > threshold):
        if not loud:
            continue
        start = max(0, index * hop - GATE["pad_seconds"] * RATE)
        end = min(len(signal), (index + 1) * hop + GATE["pad_seconds"] * RATE)
        if regions and start - regions[-1][1] < GATE["merge_seconds"] * RATE:
            regions[-1][1] = max(regions[-1][1], end)
        else:
            regions.append([start, end])
    return [(a, b) for a, b in regions]


class Model:
    def __init__(self, weights_dir: Path):
        path = weights_dir / WEIGHTS_FILE
        if not path.is_file():
            raise AlignError(f"{path} is missing; align-weights must name the directory holding {WEIGHTS_FILE}")
        if sha256_file(path) != WEIGHTS:
            raise AlignError(f"{path} is not the pinned checkpoint {WEIGHTS}")
        try:
            import torch
            import torchaudio
        except ImportError as exc:
            raise AlignError(f"this interpreter cannot load torchaudio ({exc}); configure align-python") from exc
        if torchaudio.__version__.split("+")[0] != TORCHAUDIO:
            raise AlignError(f"torchaudio {torchaudio.__version__} is not the pinned {TORCHAUDIO}")
        torch.set_num_threads(THREADS)
        bundle = getattr(torchaudio.pipelines, BUNDLE)
        # The checked file is the one loaded: torch.hub finds it in model_dir
        # and downloads nothing.
        self.model = bundle.get_model(dl_kwargs={"model_dir": str(weights_dir), "progress": False}).eval()
        labels = list(bundle.get_labels())
        self.labels = {label: index for index, label in enumerate(labels)}
        self.separator, self.blank, self.star = self.labels["|"], 0, len(labels)
        self.torch, self.functional = torch, torchaudio.functional
        self.software = [{"name": "torch", "version": torch.__version__},
                         {"name": "torchaudio", "version": torchaudio.__version__}]

    def emissions(self, signal: Any, regions: Iterable[tuple[int, int]]) -> Any:
        """Log probabilities on the 20 ms grid, a `*` column of zeros added;
        a frame outside every region is the blank."""
        torch = self.torch
        frames = len(signal) // FRAME
        emitted = torch.full((frames, self.star), -20.0)
        emitted[:, self.blank] = 0.0
        audio = torch.from_numpy(signal)
        chunk, context = CHUNK_SECONDS * RATE, CONTEXT_SECONDS * RATE
        for start, end in regions:
            position = start
            while position < end:
                a, z = max(0, position - context), min(len(signal), position + chunk + context, end + context)
                piece = audio[a:z]
                if len(piece) < RATE // 10:
                    break
                with torch.inference_mode():
                    out, _ = self.model(piece.unsqueeze(0))
                    out = torch.log_softmax(out, dim=-1)[0]
                ratio = (z - a) / out.shape[0]
                k0 = int(round((position - a) / ratio))
                k1 = int(round((min(end, position + chunk) - a) / ratio))
                g0 = position // FRAME
                count = max(0, min(k1 - k0, frames - g0))
                emitted[g0:g0 + count] = out[k0:k0 + count]
                position += chunk
        return torch.cat([emitted, torch.zeros(frames, 1)], dim=1)

    def align(self, emitted: Any, sequence: list[int]) -> list[tuple[int, int, float]]:
        torch = self.torch
        if emitted.shape[0] < len(sequence) + 2:
            raise AlignError(f"the stem is {emitted.shape[0]} frames, too short for {len(sequence)} tokens")
        target = torch.tensor([sequence], dtype=torch.int32)
        path, scores = self.functional.forced_align(emitted.unsqueeze(0), target, blank=self.blank)
        merged = self.functional.merge_tokens(path[0], scores[0].exp(), blank=self.blank)
        if len(merged) != len(sequence):
            raise AlignError(f"the aligner placed {len(merged)} tokens of {len(sequence)}")
        return [(span.start, span.end, float(span.score)) for span in merged]


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> str:
    first = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout.split("\n", 1)[0].split()
    return first[2] if len(first) > 2 and first[:2] == ["ffmpeg", "version"] else "unknown"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: align_wav2vec2.py <weights directory> <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        if request.get("parameters") != parameters():
            raise AlignError("align-wav2vec2 has fixed settings; the request names others")
        document = json.loads(Path(request["document"]["path"]).read_text(encoding="utf-8"))
        lines = document["lines"]
        if not (isinstance(lines, list) and all(isinstance(l, list) and all(isinstance(w, str) for w in l)
                                                for l in lines)):
            raise AlignError("the document's words must be a list of lines, each a list of words")
        output = Path(request["output_dir"])
        hyphenate, pyphen_version = load_hyphenator()
        model = Model(Path(argv[0]))
        signal = decode(request["input"]["path"])
        regions = active_regions(signal)
        sequence, owners = tokens(lines, model.labels, model.separator, model.star)
        if len(sequence) < 3:
            raise AlignError("the document has no word the model can spell")
        spans = model.align(model.emissions(signal, regions), sequence)
        alignment = assemble(lines, owners, spans, hyphenate)
        (output / "alignment.json").write_text(json.dumps(alignment, separators=(",", ":")))
        active = sum(b - a for a, b in regions)
        software = [*model.software, {"name": "pyphen", "version": pyphen_version},
                    {"name": "ffmpeg", "version": ffmpeg_version()},
                    {"name": "numpy", "version": __import__("numpy").__version__},
                    {"name": "python", "version": platform.python_version()}]
        result = {"contract": "archive-extractor-result/1", "software": software, "grids": {},
                  "outputs": {"alignment": {}, "mean_score": {"value": alignment["mean_score"]},
                              "active_seconds": {"value": decimal3(active / RATE)}}}
        (output / "result.json").write_text(json.dumps(result))
    except (AlignError, OSError, KeyError, ValueError) as exc:
        print(f"align-wav2vec2: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
