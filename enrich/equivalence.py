"""Whether entries of interchangeable parameters agree (ADR 0090's amendment).

An extractor whose manifest declares `interchangeable` (stems: `apply.device`,
cpu or mps) makes entries that may stand in for each other once an
equivalence record passes. `measure` makes that record: over each test input
it makes (or finds) the entry of each side, measures each side's file
outputs downstream with the extractors the declared measures name, compares
what they give, and files the record in the bank (`bank.write_equivalence`).

Downstream agreement decides; the sample-level differences of each audio
output are recorded as evidence and decide nothing. A measure with nothing
to compare (a bass stem on which no frame is confidently pitched) records
None, and a measure measured on no input fails the record: the test inputs
must hold what the measure needs.

The measures here are the ones `bank.MEASURES` names; a test holds the two
equal.
"""

from __future__ import annotations

import json
import math
import subprocess
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Mapping

import bank
import extract
import note_builder

# Notes agree when their onsets are within this and their MIDI pitches equal.
ONSET_SECONDS = 0.05


class EquivalenceError(Exception):
    """The comparison could not be made; nothing is recorded."""


def notes_of(entry: bank.Entry) -> list[tuple[float, int]]:
    """(onset in seconds, MIDI pitch) of each note of a notes-basic-pitch entry."""
    document = json.loads(entry.file("notes").read_text(encoding="utf-8"))
    columns = document["columns"]
    onset, midi = columns.index("onset_s"), columns.index("midi")
    return [(float(row[onset]), int(row[midi])) for row in document["notes"]]


def note_agreement(first: list[tuple[float, int]], second: list[tuple[float, int]]) -> tuple[float | None, dict]:
    """Note F1 of two note lists: pairs of the same pitch whose onsets are
    within ONSET_SECONDS, closest first, each note in one pair at most.
    None when neither list holds a note."""
    if not first and not second:
        return None, {"notes": [0, 0], "matched": 0}
    pairs = sorted((abs(a[0] - b[0]), i, j) for i, a in enumerate(first) for j, b in enumerate(second)
                   if a[1] == b[1] and abs(a[0] - b[0]) <= ONSET_SECONDS)
    used_first, used_second = set(), set()
    for _distance, i, j in pairs:
        if i not in used_first and j not in used_second:
            used_first.add(i)
            used_second.add(j)
    matched = len(used_first)
    return 2 * matched / (len(first) + len(second)), {"notes": [len(first), len(second)], "matched": matched}


def note_f1(first: bank.Entry, second: bank.Entry, output: str) -> tuple[float | None, dict]:
    return note_agreement(notes_of(first), notes_of(second))


def settings_for(output: str) -> Mapping[str, Any]:
    """The note builder's voicing for a stem: the bass's for `bass`, the voice's otherwise."""
    return note_builder.BASS if output == "bass" else note_builder.VOICE


def voiced_pitch(entry: bank.Entry, output: str) -> tuple[Any, Any]:
    """Each row's pitch in MIDI and whether the note builder takes it as voiced."""
    f0 = entry.series("f0")
    level = entry.series("level_db")[:, 0]
    grid = entry.grid("f0")
    hop_s = grid["hop"] / grid["sample_rate"] if grid else 0.01
    midi, voiced, _loud = note_builder.voiced_rows(f0[:, 0], f0[:, 1], level, settings_for(output), hop_s, 0.0)
    return midi, voiced


def _rows(first: bank.Entry, second: bank.Entry, output: str) -> tuple[Any, Any, Any, Any]:
    a_midi, a_voiced = voiced_pitch(first, output)
    b_midi, b_voiced = voiced_pitch(second, output)
    if len(a_midi) != len(b_midi):
        raise EquivalenceError(f"the two f0 tracks of {output} have {len(a_midi)} and {len(b_midi)} rows")
    return a_midi, a_voiced, b_midi, b_voiced


def f0_cents_p99(first: bank.Entry, second: bank.Entry, output: str) -> tuple[float | None, dict]:
    """The 99th percentile of the absolute pitch difference, in cents, over
    rows voiced in both; None when no row is."""
    import numpy
    a_midi, a_voiced, b_midi, b_voiced = _rows(first, second, output)
    both = a_voiced & b_voiced
    detail = {"rows": int(len(a_midi)), "voiced_in_both": int(both.sum())}
    if not both.any():
        return None, detail
    cents = numpy.abs(a_midi[both] - b_midi[both]) * 100
    detail["median_cents"] = round(float(numpy.median(cents)), 3)
    return round(float(numpy.percentile(cents, 99)), 3), detail


def voicing_agreement(first: bank.Entry, second: bank.Entry, output: str) -> tuple[float | None, dict]:
    """The share of rows voiced in either whose voicing both give; None when
    neither voices a row."""
    a_midi, a_voiced, b_midi, b_voiced = _rows(first, second, output)
    either = a_voiced | b_voiced
    agreeing = int((a_voiced == b_voiced)[either].sum())
    detail = {"rows": int(len(a_midi)), "voiced_in_either": int(either.sum()), "agreeing": agreeing}
    if not either.any():
        return None, detail
    return round(agreeing / int(either.sum()), 6), detail


MEASURES: dict[str, Callable[[bank.Entry, bank.Entry, str], tuple[float | None, dict]]] = {
    "note-f1": note_f1,
    "f0-cents-p99": f0_cents_p99,
    "voicing-agreement": voicing_agreement,
}


def samples_of(path: Path) -> Any:
    """A FLAC or other audio file's samples as 16-bit integers, by ffmpeg."""
    import numpy
    done = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(path),
                           "-f", "s16le", "-c:a", "pcm_s16le", "-"], capture_output=True)
    if done.returncode != 0:
        raise EquivalenceError(f"ffmpeg cannot decode {path}: {done.stderr.decode(errors='replace')[-300:]}")
    return numpy.frombuffer(done.stdout, dtype="<i2")


def sample_difference(first: Path, second: Path) -> dict[str, Any]:
    """Evidence only: the largest sample difference (in 16-bit steps) and the
    signal-to-difference ratio in dB (None when identical or silent)."""
    import numpy
    a, b = samples_of(first).astype(numpy.float64), samples_of(second).astype(numpy.float64)
    if len(a) != len(b):
        return {"samples": [len(a), len(b)], "max_abs_steps": None, "snr_db": None}
    difference = a - b
    power, noise = float((a * a).sum()), float((difference * difference).sum())
    return {"samples": int(len(a)), "max_abs_steps": int(numpy.abs(difference).max()) if len(a) else 0,
            "snr_db": round(10 * math.log10(power / noise), 2) if noise > 0 and power > 0 else None}


# -- making a record -----------------------------------------------------------

def made(catalog: bank.Catalog, root: Path, content: Path, media_type: str, digest: str | None,
         programs: dict[str, str], parameters: dict[str, Any], timeout: float | None, targets: list[str],
         jobs: int, filed_under: str | None = None) -> None:
    """Make (or find) exactly these parameters' entries, or say why not."""
    run = extract.Run(catalog, root, content, media_type, digest, programs, parameters, timeout,
                      filed_under=filed_under, exact=True)
    answer = run.execute(targets, jobs)
    for target in targets:
        said = answer["extractors"][target]
        if said["status"] not in ("hit", "ran"):
            raise EquivalenceError(f"{target} of {content} with {json.dumps(parameters.get(target, {}))[:200]} "
                                   f"was not made here: {said.get('error') or said.get('reason') or said['status']}")


def measure(catalog: bank.Catalog, root: Path, contents: list[Path], programs: dict[str, str],
            parameters: dict[str, dict[str, Any]], extractor_id: str, against: str, jobs: int = 1,
            timeout: float | None = None, now: Callable[[], str] | None = None) -> tuple[dict[str, Any], Path]:
    """Compare the configured parameters' entries with those of the value
    `against` over each content, and file the record. Every entry is made
    here unless the bank holds it (made elsewhere and collected)."""
    extractor = catalog[extractor_id]
    declared = extractor.interchangeable
    if declared is None:
        raise EquivalenceError(f"{extractor_id} declares nothing interchangeable")
    configured = catalog.parameters(extractor_id, parameters)
    others = dict(catalog.alternatives(extractor_id, parameters))
    if against not in others:
        raise EquivalenceError(f"{extractor_id} is configured with {declared['parameter']} = "
                               f"{bank.parameter_at(configured, declared['parameter'])!r}; compare it with one of "
                               f"{sorted(others)}, not {against!r}")
    sides = [(bank.parameter_at(configured, declared["parameter"]), configured), (against, others[against])]
    downstream = sorted({bank.MEASURES[item["measure"]] for item in declared["agree"]})
    for name in downstream:
        catalog[name]
    audio = [name for name, d in extractor.outputs.items() if d["kind"] == "file" and d["media_type"].startswith("audio/")]
    software: list[list[dict[str, str]] | None] = [None, None]
    inputs = []
    for content in contents:
        media_type = extract.media_type_of(content)
        digest = bank.file_digest(content)
        entries = []
        for index, (value, side) in enumerate(sides):
            overrides = {**parameters, extractor_id: side}
            made(catalog, root, content, media_type, digest, programs, overrides, timeout, [extractor_id], jobs)
            entry = bank.Bank(root, catalog, overrides, exact=True).entry(digest, extractor_id)
            if entry is None:
                raise EquivalenceError(f"the {value} entry of {content} was made but cannot be found")
            named = bank.named_software(entry.manifest["software"], declared["software"])
            if software[index] is None:
                software[index] = named
            elif software[index] != named:
                raise EquivalenceError(f"the {value} entries were made with other software: {software[index]} "
                                       f"and {named}; one record compares one pairing")
            entries.append(entry)
        measured_entries: dict[tuple[str, int], dict[str, bank.Entry]] = {}
        outputs = sorted({output for item in declared["agree"] for output in item["outputs"]})
        for output in outputs:
            feature = f"{extractor_id}/{output}"
            for index, entry in enumerate(entries):
                described = entry.output(output)
                stem = entry.path / described["file"]
                measuring = catalog.overrides_for(feature, parameters)
                made(catalog, root, stem, described["media_type"], described["sha256"], programs, measuring,
                     timeout, downstream, jobs, filed_under=digest)
                reader = bank.Bank(root, catalog, parameters, exact=True)
                measured_entries[(output, index)] = {
                    name: reader.entry(described["sha256"], name, filed_under=digest, feature=feature)
                    for name in downstream}
        measured = []
        for item in declared["agree"]:
            for output in item["outputs"]:
                name = bank.MEASURES[item["measure"]]
                first, second = measured_entries[(output, 0)][name], measured_entries[(output, 1)][name]
                if first is None or second is None:
                    raise EquivalenceError(f"{name} of {output} was made but cannot be found")
                value, detail = MEASURES[item["measure"]](first, second, output)
                detail = {**detail, "entries": [first.manifest["key"], second.manifest["key"]]}
                measured.append({"measure": item["measure"], "output": output, "value": value, "detail": detail})
        evidence = {name: sample_difference(entries[0].file(name), entries[1].file(name)) for name in audio}
        inputs.append({"content": digest, "entries": [e.manifest["key"] for e in entries],
                       "measured": measured, "evidence": evidence})
    if not inputs:
        raise EquivalenceError("no test input given")
    record = {"contract": bank.EQUIVALENCE_CONTRACT,
              "extractor": {"id": extractor.id, "version": extractor.version},
              "parameter": declared["parameter"],
              "sides": [{"value": value, "parameters": bank.digest_of(bank.canonical(side)), "software": software[i]}
                        for i, (value, side) in enumerate(sides)],
              "agree": [dict(item) for item in declared["agree"]],
              "inputs": inputs,
              "pass": bank.agreement_passes(declared["agree"], inputs),
              "made_at": (now or (lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))()}
    path = bank.write_equivalence(root, record, sides[0][1], sides[1][1])
    return record, path


def summary(record: dict[str, Any]) -> dict[str, Any]:
    """The record's decision and, per measure and output, the value that
    decides it, its threshold, and the worst single input's value."""
    measures: dict[str, Any] = {}
    for item, decision in zip([(i, o) for i in record["agree"] for o in i["outputs"]],
                              bank.decisions(record["agree"], record["inputs"])):
        declared, output = item
        bound = "at_least" if "at_least" in declared else "at_most"
        values = [m["value"] for i in record["inputs"] for m in i["measured"]
                  if m["measure"] == declared["measure"] and m["output"] == output and m["value"] is not None]
        pick = min if bound == "at_least" else max
        measures[f"{declared['measure']} {output}"] = {
            "decided": decision["value"], bound: declared[bound], "pass": decision["pass"],
            "measured_on": decision["measured_on"], "worst_input": pick(values) if values else None}
    return {"pass": record["pass"], "sides": [s["value"] for s in record["sides"]], "measures": measures,
            "inputs": len(record["inputs"])}


# -- a region against the whole (ADR 0112) -------------------------------------
#
# Under `--songs` the expensive extractors run over song regions, and a
# region's entries must be the whole run's over the region within the
# tolerance a stand-in is held to (the agreement `stems` declares): the
# region's stems, and what measures them, laid on the whole run's grid.
# `region_measure` makes both sides of each test input and compares them
# over the region; nothing is filed but the entries.

REGION_MEASURES = {"notes-basic-pitch": ("note-f1",), "f0-pesto": ("f0-cents-p99", "voicing-agreement"),
                   "melody": ("melody-cents-p99", "melody-voicing")}
REGION_FEATURES = {"stems/vocals": ("notes-basic-pitch", "f0-pesto", "melody"),
                   "stems/bass": ("notes-basic-pitch", "f0-pesto")}


def first_row(whole: bank.Entry, region: bank.Entry, grid: str) -> int:
    """The whole entry's row that is the region entry's first, by the
    region's offset at the grid's rate and the region's `first_centre`;
    `grid` names a series output on it."""
    placed = region.timeline
    shape = region.grid(grid)
    if placed is None or shape is None or whole.grid(grid) is None:
        raise EquivalenceError(f"{region.manifest['extractor']} over a region has no {grid} grid or region")
    rate, hop = shape["sample_rate"], shape["hop"]
    offset = round(Fraction(placed["start"] * rate, placed["rate"]))
    row, rest = divmod(offset + shape["first_centre"] - whole.grid(grid)["first_centre"], hop)
    if rest:
        raise EquivalenceError(f"the region's {grid} rows are not on the whole run's ({rest} samples off)")
    return row


def _over(whole: Any, region: Any, row: int) -> tuple[Any, Any]:
    """The whole's rows over the region's, and the region's rows the whole
    has (a whole run may stop a row short, or drop its last frames)."""
    count = max(0, min(len(region), len(whole) - row))
    return whole[row:row + count], region[:count]


def _cents_p99(a_pitch: Any, a_voiced: Any, b_pitch: Any, b_voiced: Any, scale: float) -> tuple[float | None, dict]:
    import numpy
    both = a_voiced & b_voiced
    detail = {"rows": int(len(a_pitch)), "voiced_in_both": int(both.sum())}
    if not both.any():
        return None, detail
    cents = numpy.abs(a_pitch[both] - b_pitch[both]) * scale
    detail["median_cents"] = round(float(numpy.median(cents)), 3)
    return round(float(numpy.percentile(cents, 99)), 3), detail


def _voicing(a_voiced: Any, b_voiced: Any) -> tuple[float | None, dict]:
    either = a_voiced | b_voiced
    agreeing = int((a_voiced == b_voiced)[either].sum())
    detail = {"rows": int(len(a_voiced)), "voiced_in_either": int(either.sum()), "agreeing": agreeing}
    if not either.any():
        return None, detail
    return round(agreeing / int(either.sum()), 6), detail


def region_agreement(measure: str, whole: bank.Entry, region: bank.Entry, output: str) -> tuple[float | None, dict]:
    """One measure of a region entry against the whole entry over the
    region: notes by onset in the region, rows from the region's first."""
    import numpy
    placed = region.timeline
    assert placed is not None
    start, end = placed["start"] / placed["rate"], placed["end"] / placed["rate"]
    if measure == "note-f1":
        mine = [(onset + start, midi) for onset, midi in notes_of(region)]
        theirs = [(onset, midi) for onset, midi in notes_of(whole) if start <= onset < end]
        return note_agreement(theirs, mine)
    if measure in ("f0-cents-p99", "voicing-agreement"):
        a_midi, a_voiced = voiced_pitch(whole, output)
        b_midi, b_voiced = voiced_pitch(region, output)
        row = first_row(whole, region, "f0")
        (a_midi, b_midi), (a_voiced, b_voiced) = (_over(a_midi, b_midi, row), _over(a_voiced, b_voiced, row))
        if measure == "voicing-agreement":
            return _voicing(a_voiced, b_voiced)
        return _cents_p99(a_midi, a_voiced, b_midi, b_voiced, 100)
    if measure in ("melody-cents-p99", "melody-voicing"):
        row = first_row(whole, region, "pitch")
        a, b = _over(whole.series("pitch")[:, 0], region.series("pitch")[:, 0], row)
        a_voiced, b_voiced = a > 0, b > 0
        if measure == "melody-voicing":
            return _voicing(a_voiced, b_voiced)
        safe = numpy.where(a_voiced, a, 1.0), numpy.where(b_voiced, b, 1.0)
        return _cents_p99(numpy.log2(safe[0]), a_voiced, numpy.log2(safe[1]), b_voiced, 1200)
    raise EquivalenceError(f"no region measure {measure!r}")


def region_bounds(catalog: bank.Catalog) -> dict[str, tuple[str, float]]:
    """Each measure's bound: those `stems` declares for a stand-in, and the
    melody's, held to the f0 track's."""
    bounds: dict[str, tuple[str, float]] = {}
    for item in (catalog["stems"].interchangeable or {}).get("agree", []):
        bound = "at_least" if "at_least" in item else "at_most"
        bounds[item["measure"]] = (bound, float(item[bound]))
    bounds["melody-cents-p99"] = bounds.get("f0-cents-p99", ("at_most", 5.0))
    bounds["melody-voicing"] = bounds.get("voicing-agreement", ("at_least", 0.99))
    return bounds


def region_measure(catalog: bank.Catalog, root: Path, contents: list[Path], programs: dict[str, str],
                   parameters: dict[str, dict[str, Any]], seconds: tuple[float, float], jobs: int = 1,
                   timeout: float | None = None) -> dict[str, Any]:
    """Each content's stems and their measures whole and over the region
    [seconds), compared over the region; the stems' sample differences are
    evidence. Every value is checked against `region_bounds`."""
    bounds = region_bounds(catalog)
    inputs = []
    for content in contents:
        media_type = extract.media_type_of(content)
        digest = bank.file_digest(content)
        rate = _stream_rate(content)
        region = {"start": round(seconds[0] * rate), "end": round(seconds[1] * rate), "rate": rate}
        sides = {"whole": catalog, "region": catalog.within(digest, region)}
        found: dict[str, dict[str, Any]] = {"whole": {}, "region": {}}
        for side, view in sides.items():
            for feature, targets in REGION_FEATURES.items():
                answer = extract.run_from(view, root, content, media_type, digest, programs, parameters, timeout,
                                          feature, list(targets), jobs, False, False)
                failed = {t: a for t, a in answer["extractors"].items() if a["status"] not in ("hit", "ran")}
                if answer["status"] != "ok" or failed:
                    raise EquivalenceError(f"{feature} of {content} over the {side} was not made: "
                                           f"{json.dumps(failed or answer.get('from'))[:400]}")
                reader = bank.Bank(root, view, parameters)
                for entry, measures in reader.measured_over(digest, feature, targets):
                    if side == "region" and entry.region != region:
                        continue
                    found[side][feature] = (entry, measures)
                    break
        measured = []
        for feature, targets in REGION_FEATURES.items():
            output = feature.split("/", 1)[1]
            (whole_stem, whole), (region_stem, mine) = found["whole"][feature], found["region"][feature]
            for name in targets:
                if whole[name] is None or mine[name] is None:
                    raise EquivalenceError(f"{name} of {feature} was made but cannot be found")
                for measure in REGION_MEASURES[name]:
                    value, detail = region_agreement(measure, whole[name], mine[name], output)
                    bound, limit = bounds[measure]
                    passed = value is None or (value >= limit if bound == "at_least" else value <= limit)
                    measured.append({"measure": measure, "output": output, "value": value, bound: limit,
                                     "pass": passed, "detail": detail})
        evidence = {}
        for feature in REGION_FEATURES:
            output = feature.split("/", 1)[1]
            whole_stem, region_stem = found["whole"][feature][0], found["region"][feature][0]
            evidence[output] = region_samples(whole_stem.file(output), region_stem.file(output), region)
        inputs.append({"content": str(content), "region": region, "measured": measured, "stems": evidence,
                       "pass": all(m["pass"] for m in measured)})
    if not inputs:
        raise EquivalenceError("no test input given")
    return {"seconds": list(seconds), "inputs": inputs, "pass": all(i["pass"] for i in inputs)}


def region_samples(whole: Path, region: Path, placed: Mapping[str, int]) -> dict[str, Any]:
    """Evidence: a region's stem against the whole stem over the region, as
    `sample_difference` gives (stereo 16-bit at 44,100 Hz)."""
    import numpy
    a, b = samples_of(whole).astype(numpy.float64), samples_of(region).astype(numpy.float64)
    first = round(Fraction(placed["start"] * 44100, placed["rate"])) * 2
    a = a[first:first + len(b)]
    if len(a) != len(b):
        return {"samples": [len(a), len(b)], "max_abs_steps": None, "snr_db": None}
    difference = a - b
    power, noise = float((a * a).sum()), float((difference * difference).sum())
    return {"samples": int(len(a) // 2), "max_abs_steps": int(numpy.abs(difference).max()) if len(a) else 0,
            "snr_db": round(10 * math.log10(power / noise), 2) if noise > 0 and power > 0 else None}


def _stream_rate(path: Path) -> int:
    done = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate",
                           "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    if done.returncode != 0 or not done.stdout.strip().isdigit():
        raise EquivalenceError(f"ffprobe cannot read the rate of {path}: {done.stderr.strip()[-200:]}")
    return int(done.stdout.strip())
