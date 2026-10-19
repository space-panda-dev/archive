#!/usr/bin/env python3
"""The notes-basic-pitch extractor: polyphonic note events by Spotify's Basic
Pitch (ICASSP 2022), run by ONNX Runtime in its own environment.

The runner starts this program with the interpreter the operator configured
as `basic-pitch-python` (ADR 0074), so the archive's environment never
imports the model's libraries. It needs only numpy and `ffmpeg` until the
model is loaded; `basic_pitch`, `onnxruntime`, and `librosa` are imported by
`load_model` alone. TensorFlow is never needed: the pinned model is the
package's `nmp.onnx`, checked against its digest, and a model loaded by any
other runtime is refused.

It decodes the content's first audio stream to a float WAV at the stream's
own rate and channels (so a video or any codec ffmpeg reads is measured, and
a FLAC's samples reach Basic Pitch exactly as its own reader would give
them), and Basic Pitch resamples it to 22,050 Hz mono, runs the model, and
makes notes with its default thresholds. For each note it writes:

- onset and offset in seconds, the MIDI note, and Basic Pitch's amplitude;
- `cents`: how far the note sits from its MIDI note's equal-tempered pitch
  (A4 = 440 Hz), the median over the note's frames of the peak of the
  model's pitch contour (three bins a semitone) within a semitone of the
  note, placed between bins by a parabola through the logarithms of the peak
  and its neighbours;
- `bends`: per model frame from the onset, the contour's peak within 25 bins
  of the note under Basic Pitch's own Gaussian weighting, in bins (thirds of
  a semitone) from the note.

The pinned model's contour axis sits one bin above where Basic Pitch's own
`midi_pitch_to_contour_bin` puts a note: a sine at exactly A4 peaks at bin
145, not 144, at every pitch measured from E2 to A5, sine or harmonic. So
`cents` and `bends` are read against the bin above; Basic Pitch's own
`get_pitch_bends`, which does not, reads every note a third of a semitone
sharp (the 31 cents an earlier trial measured). The opt-in test in
`test_notes.py` plays tones of known pitch through the real model.

Invoked by the runner as `<basic-pitch-python> notes.py <request.json>`; the
settings are fixed, and a request naming others is refused.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NamedTuple, Protocol

import numpy

BASIC_PITCH = "0.4.0"
MODEL_FILE = "nmp.onnx"
MODEL_DIGEST = "sha256:2c3c1d144bfa61ad236e92e169c13535c880469a12a047d4e73451f2c059a0ec"
ONSET_THRESHOLD = "0.5"
FRAME_THRESHOLD = "0.3"
MINIMUM_NOTE_FRAMES = 11
BINS_PER_SEMITONE = 3            # Basic Pitch's CONTOURS_BINS_PER_SEMITONE
LOWEST_MIDI = 21                 # contour bin 0 is A0, 27.5 Hz
CONTOUR_OFFSET_BINS = 1          # measured: where the pinned model puts a note, above Basic Pitch's own bin
CENTS_REACH_BINS = 3             # a semitone either side of the note
BEND_REACH_BINS = 25             # Basic Pitch's n_bins_tolerance
BEND_WEIGHT_STD = 5              # Basic Pitch's Gaussian weighting of the bend window
FRAME_RATE = 86                  # Basic Pitch's ANNOTATIONS_FPS: 256 samples at 22,050 Hz


class NotesError(Exception):
    pass


def decode_arguments(path: str, output: str) -> list[str]:
    """The ffmpeg arguments after the program name: the first audio stream at
    its own rate and channels, as 32-bit float WAV."""
    return ["-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-i", path, "-map", "0:a:0",
            "-c:a", "pcm_f32le", "-f", "wav", output]


def parameters() -> dict[str, Any]:
    """The settings, which the manifest must declare exactly."""
    return {"model": {"name": "basic-pitch icassp_2022 nmp.onnx", "package": f"basic-pitch=={BASIC_PITCH}",
                      "sha256": MODEL_DIGEST, "runtime": "onnxruntime"},
            "decoder": "ffmpeg", "decode_arguments": decode_arguments("{input}", "{wav}"),
            "decode": "basic_pitch.inference.run_inference (librosa load of the WAV, 22050 Hz mono)",
            "onset_threshold": ONSET_THRESHOLD, "frame_threshold": FRAME_THRESHOLD,
            "minimum_note_length_frames": MINIMUM_NOTE_FRAMES, "minimum_frequency": None,
            "maximum_frequency": None, "infer_onsets": True, "melodia_trick": True,
            "contour_offset_bins": CONTOUR_OFFSET_BINS,
            "cents": "median over the note's frames of the contour peak within 3 bins of the note's bin plus the "
                     "offset, interpolated by a parabola through the logarithms",
            "bends": "per frame, the contour peak within 25 bins of the note's bin plus the offset, weighted by "
                     "a Gaussian of standard deviation 5 bins, in bins from that bin"}


# -- the model ------------------------------------------------------------------

class Transcription(NamedTuple):
    """What the model and Basic Pitch's note maker give: notes as (first
    frame, frame after the last, MIDI note, amplitude); the pitch contour,
    [frames, bins]; and each frame's time in seconds."""
    notes: list[tuple[int, int, int, float]]
    contour: numpy.ndarray
    times: numpy.ndarray


class Model(Protocol):
    software: list[dict[str, str]]

    def transcribe(self, wav: Path) -> Transcription: ...


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


class BasicPitch:
    def __init__(self) -> None:
        import importlib.metadata
        import basic_pitch
        import librosa
        import onnxruntime
        from basic_pitch.inference import Model as BasicPitchModel
        from basic_pitch.inference import run_inference
        from basic_pitch.note_creation import model_frames_to_time, output_to_notes_polyphonic

        version = importlib.metadata.version("basic-pitch")
        if version != BASIC_PITCH:
            raise NotesError(f"basic-pitch is {version}, not the pinned {BASIC_PITCH}")
        path = Path(basic_pitch.__file__).parent / "saved_models" / "icassp_2022" / MODEL_FILE
        if not path.is_file():
            raise NotesError(f"the model file {path} was not found")
        actual = sha256_file(path)
        if actual != MODEL_DIGEST:
            raise NotesError(f"the model file {MODEL_FILE} is {actual}, not the pinned {MODEL_DIGEST}")
        self.model = BasicPitchModel(path)
        if self.model.model_type != BasicPitchModel.MODEL_TYPES.ONNX:
            raise NotesError(f"basic-pitch loaded the model with {self.model.model_type.name}, not ONNX Runtime")
        self.run_inference, self.to_notes, self.to_time = run_inference, output_to_notes_polyphonic, \
            model_frames_to_time
        self.software = [{"name": "basic-pitch", "version": version},
                         {"name": "onnxruntime", "version": onnxruntime.__version__},
                         {"name": "librosa", "version": librosa.__version__}]

    def transcribe(self, wav: Path) -> Transcription:
        output = self.run_inference(wav, self.model)
        notes = self.to_notes(output["note"], output["onset"], onset_thresh=float(ONSET_THRESHOLD),
                              frame_thresh=float(FRAME_THRESHOLD), infer_onsets=True,
                              min_note_len=MINIMUM_NOTE_FRAMES, max_freq=None, min_freq=None, melodia_trick=True)
        contour = numpy.asarray(output["contour"])
        return Transcription([(int(s), int(e), int(p), float(a)) for s, e, p, a in notes], contour,
                             numpy.asarray(self.to_time(contour.shape[0])))


def load_model() -> Model:
    """The pinned model. Imports Basic Pitch, so only its environment can run it."""
    try:
        return BasicPitch()
    except ImportError as exc:
        raise NotesError(f"this interpreter cannot load Basic Pitch ({exc}); configure basic-pitch-python") from exc


# -- reading the contour ------------------------------------------------------------

def note_bin(midi: int) -> int:
    """The contour bin the pinned model puts an equal-tempered MIDI note at."""
    return BINS_PER_SEMITONE * (midi - LOWEST_MIDI) + CONTOUR_OFFSET_BINS


def peak_offset(left: float, centre: float, right: float) -> float:
    """Where a peak lies between bins, -0.5 to 0.5 from the centre bin, by a
    parabola through the logarithms of the three values (exact for a
    Gaussian peak; the model's contour is a sigmoid output, so positive)."""
    tiny = 1e-12
    a, b, c = (float(numpy.log(max(v, tiny))) for v in (left, centre, right))
    curve = a - 2 * b + c
    if curve >= 0:
        return 0.0
    return min(max(0.5 * (a - c) / curve, -0.5), 0.5)


def note_cents(contour: numpy.ndarray, midi: int) -> float:
    """A note's frames' median distance from its MIDI note, in cents."""
    bins = contour.shape[1]
    centre = note_bin(midi)
    low, high = max(centre - CENTS_REACH_BINS, 1), min(centre + CENTS_REACH_BINS, bins - 2)
    if low > high or not len(contour):
        return 0.0
    places = []
    for row in contour:
        k = low + int(numpy.argmax(row[low:high + 1]))
        places.append(k + peak_offset(row[k - 1], row[k], row[k + 1]))
    return float(numpy.median(places) - centre) * 100 / BINS_PER_SEMITONE


def note_bends(contour: numpy.ndarray, midi: int) -> list[int]:
    """Per frame, the weighted contour peak near the note, in bins from it:
    Basic Pitch's `get_pitch_bends`, centred on the bin the model uses."""
    bins = contour.shape[1]
    centre = note_bin(midi)
    offsets = numpy.arange(-BEND_REACH_BINS, BEND_REACH_BINS + 1)
    weights = numpy.exp(-0.5 * (offsets / BEND_WEIGHT_STD) ** 2)
    keep = (centre + offsets >= 0) & (centre + offsets < bins)
    window = contour[:, centre + offsets[keep]] * weights[keep]
    return [int(v) for v in offsets[keep][numpy.argmax(window, axis=1)]] if len(contour) else []


def note_rows(transcription: Transcription) -> list[list[Any]]:
    """The notes as rows, by onset and then pitch."""
    rows = []
    contour, times = transcription.contour, transcription.times
    if len(contour) != len(times):
        raise NotesError(f"the contour has {len(contour)} frames and the times {len(times)}")
    for start, end, midi, amplitude in sorted(transcription.notes, key=lambda n: (n[0], n[2])):
        # Basic Pitch ends a note at the frame after its last, which is a frame.
        if not 0 <= start < end < len(times):
            raise NotesError(f"a note spans frames {start} to {end} of {len(times)}")
        frames = contour[start:end]
        rows.append([round(float(times[start]), 4), round(float(times[end]), 4), int(midi),
                     round(float(amplitude), 4), round(note_cents(frames, midi), 1), note_bends(frames, midi)])
    return rows


# -- running ---------------------------------------------------------------------------

def decode(path: str, wav: Path, ffmpeg: str = "ffmpeg") -> None:
    completed = subprocess.run([ffmpeg, *decode_arguments(path, str(wav))], stdin=subprocess.DEVNULL,
                               capture_output=True)
    if completed.returncode != 0 or not wav.is_file():
        raise NotesError(f"ffmpeg could not decode {path}: {completed.stderr.decode('utf-8', 'replace').strip()[:300]}")


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> str:
    first = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout.split("\n", 1)[0].split()
    return first[2] if len(first) > 2 and first[:2] == ["ffmpeg", "version"] else "unknown"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: notes.py <request.json>", file=sys.stderr)
        return 2
    try:
        request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        if request.get("parameters") != parameters():
            raise NotesError("notes-basic-pitch has fixed settings; the request names others")
        output = Path(request["output_dir"])
        model = load_model()
        with tempfile.TemporaryDirectory(prefix="notes-") as work:
            wav = Path(work) / "input.wav"
            decode(request["input"]["path"], wav)
            transcription = model.transcribe(wav)
        rows = note_rows(transcription)
        (output / "notes.json").write_text(json.dumps(
            {"frame_rate": FRAME_RATE, "bend_unit": "1/3 semitone",
             "columns": ["onset_s", "offset_s", "midi", "amplitude", "cents", "bends"], "notes": rows},
            separators=(",", ":")))
        times = transcription.times
        software = [*model.software, {"name": "ffmpeg", "version": ffmpeg_version()},
                    {"name": "numpy", "version": numpy.__version__},
                    {"name": "python", "version": platform.python_version()}]
        result = {"contract": "archive-extractor-result/1", "software": software, "grids": {},
                  "outputs": {"notes": {}, "note_count": {"value": len(rows)},
                              "seconds": {"value": round(float(times[-1]), 3) if len(times) else 0}}}
        (output / "result.json").write_text(json.dumps(result))
    except (NotesError, OSError, KeyError, ValueError) as exc:
        print(f"notes-basic-pitch: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
