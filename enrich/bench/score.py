"""Note scoring with mir_eval, and reading note lists from MIDI.

Notes are (onset_s, offset_s, midi) rows; midi may carry a fraction of a
semitone. The score is mir_eval's note precision, recall and F1, once with
onsets and pitch only and once with offsets too, at the set's tolerances.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

Note = tuple[float, float, float]
MIN_DURATION_S = 0.01   # mir_eval refuses zero-length notes; the spike's scorer used the same floor


def read_midi(path: Path) -> list[Note]:
    """Every pitched note in a MIDI file, by onset."""
    import pretty_midi
    midi = pretty_midi.PrettyMIDI(str(path))
    notes = [(float(n.start), float(n.end), float(n.pitch))
             for instrument in midi.instruments if not instrument.is_drum for n in instrument.notes]
    return sorted(notes)


def _arrays(notes: Sequence[Note]):
    import numpy
    intervals = numpy.array([[on, max(off, on + MIN_DURATION_S)] for on, off, _ in notes], dtype=float).reshape(-1, 2)
    hz = numpy.array([440.0 * 2.0 ** ((midi - 69.0) / 12.0) for _, _, midi in notes], dtype=float)
    return intervals, hz


def score(reference: Sequence[Note], estimate: Sequence[Note], scoring: dict[str, float]) -> dict[str, float]:
    """Precision, recall and F1 without offsets, and F1 with offsets."""
    if not reference:
        raise ValueError("a reference with no notes cannot be scored")
    if not estimate:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "f1_offsets": 0.0}
    from mir_eval.transcription import precision_recall_f1_overlap
    ri, rp = _arrays(reference)
    ei, ep = _arrays(estimate)
    common = {"onset_tolerance": scoring["onset_tolerance_s"], "pitch_tolerance": scoring["pitch_tolerance_cents"],
              "offset_min_tolerance": scoring["offset_min_tolerance_s"]}
    p, r, f, _ = precision_recall_f1_overlap(ri, rp, ei, ep, offset_ratio=None, **common)
    f_off = precision_recall_f1_overlap(ri, rp, ei, ep, offset_ratio=scoring["offset_ratio"], **common)[2]
    return {"precision": float(p), "recall": float(r), "f1": float(f), "f1_offsets": float(f_off)}


def scorer_version() -> str:
    import mir_eval
    return f"mir_eval {mir_eval.__version__}"
