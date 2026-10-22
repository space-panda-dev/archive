"""python -m enrich.bench validate SET | run SET --variant NAME | check SET | equivalence | region | cost | chords ...

Exit status: 0 passed, 1 a mean regressed beyond the tolerance, 2 the run
could not happen (a missing or changed file, a bad manifest or config, a
variant that failed).
"""

from __future__ import annotations

import argparse
import json
import sys

from . import chords, cost, equivalence, region, variants
from .run import (DEFAULT_TOLERANCE, baseline_path, compare, record_baseline, run_set, summary,
                  write_result)
from .sets import BenchError, bench_root, load_set, sha256_file, verify


def _gate(root, result, tolerance) -> int:
    path = baseline_path(root, result["set"], result["variant"]["name"], result["input"])
    if not path.is_file():
        print(f"no baseline at {path}; nothing to compare (record one with --record-baseline)")
        return 0
    baseline = json.loads(path.read_text())
    failures = compare(result, baseline, tolerance)
    for group, m in result["means"].items():
        b = baseline["means"][group]
        print(f"  vs baseline {group:<9} F1 {m['f1'] - b['f1']:+.3f}  F1+offsets {m['f1_offsets'] - b['f1_offsets']:+.3f}")
    print(f"  cost {result['cost']['seconds_per_minute']:.1f} s/min, baseline {baseline['cost']['seconds_per_minute']:.1f}")
    if failures:
        print("REGRESSION:\n  " + "\n  ".join(failures))
        return 1
    print(f"ok: within {tolerance} of the baseline")
    return 0


def _chords(args) -> int:
    if args.chords_command == "reference":
        from pathlib import Path
        taps = [float(line) for line in Path(args.taps).read_text().split() if line.strip()]
        reference = chords.reference_from_taps(
            json.loads(Path(args.claims).read_text()), taps, recording_sha256=sha256_file(Path(args.recording)),
            chart_sha256=sha256_file(Path(args.chart)), transposition=args.transposition,
            first_occurrence=args.first_occurrence)
        chords.parse_reference(reference)
        Path(args.out).write_text(json.dumps(reference, indent=1) + "\n")
        print(f"reference {args.out}: {len(reference['chords'])} chords over {len(reference['sections'])} sections")
        return 0
    root = bench_root(args.root)
    path = root / "sets" / f"{args.set}.json"
    if not path.is_file():
        raise BenchError(f"no set {args.set}: {path} does not exist")
    chord_set = chords.parse_set(args.set, json.loads(path.read_text(encoding="utf-8")), sha256_file(path))
    if args.chords_command == "validate":
        chords.verify(root, chord_set)
        for item in chord_set.items:
            chords.parse_reference(json.loads((root / item.reference.path).read_text(encoding="utf-8")), item)
        print(f"{chord_set.name}: {len(chord_set.items)} items, every file present and unchanged, every reference valid")
        return 0
    if args.chords_command == "run":
        names = [args.variant]
    else:
        found = sorted((root / "baselines" / chord_set.name).glob("*@recording.json"))
        if not found:
            raise BenchError(f"no baselines for {chord_set.name} under {root / 'baselines'}")
        names = [p.stem.split("@", 1)[0] for p in found]
    status = 0
    for name in names:
        result = chords.run_set(root, chord_set, chords.build(name, root))
        print(chords.summary(result))
        print(f"  result {write_result(root, result)}")
        if args.chords_command == "run" and args.record_baseline:
            print(f"  baseline {record_baseline(root, result)}")
            continue
        base = baseline_path(root, chord_set.name, name, "recording")
        if not base.is_file():
            print(f"no baseline at {base}; nothing to compare (record one with --record-baseline)")
            continue
        failures = chords.compare(result, json.loads(base.read_text()), args.tolerance)
        if failures:
            print("REGRESSION:\n  " + "\n  ".join(failures))
            status = 1
        else:
            print(f"ok: within {args.tolerance} of the baseline")
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m enrich.bench", description=__doc__.split("\n")[0])
    parser.add_argument("--root", help="the bench root (default: $ARCHIVE_BENCH_ROOT)")
    commands = parser.add_subparsers(dest="command", required=True)
    v = commands.add_parser("validate", help="check a set's manifest and every file it names")
    v.add_argument("set")
    r = commands.add_parser("run", help="run one variant over a set, record the result, compare with its baseline")
    r.add_argument("set")
    r.add_argument("--variant", required=True, choices=sorted(variants.VARIANTS))
    r.add_argument("--input", help="the input to run on for every item (default: each item's primary)")
    r.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    r.add_argument("--record-baseline", action="store_true", help="store this result as the baseline")
    c = commands.add_parser("check", help="rerun every baseline of a set and fail on a regression")
    c.add_argument("set")
    c.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    commands.add_parser("equivalence", help="measure the stem equivalence record over the root's clips "
                                            "(see enrich/bench/equivalence.py)")
    commands.add_parser("region", help="compare a region's stems and their measures with the whole run's over "
                                       "the root's clips (see enrich/bench/region.py)")
    k = commands.add_parser("cost", help="measure the backfill's CPU seconds per audio-second over the root's clip "
                                         "and fail on a regression (see enrich/bench/cost.py)")
    k.add_argument("--record-baseline", action="store_true", help="store this result as the baseline")
    h = commands.add_parser("chords", help="the chord bench: chart chords placed on recordings against the "
                                           "machine's (see enrich/bench/chords.py)")
    hc = h.add_subparsers(dest="chords_command", required=True)
    hv = hc.add_parser("validate", help="check a chord set's manifest, every file it names, and every reference")
    hv.add_argument("set")
    hr = hc.add_parser("run", help="score one chord variant over a set, record it, compare with its baseline")
    hr.add_argument("set")
    hr.add_argument("--variant", required=True, choices=sorted(chords.VARIANTS))
    hr.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    hr.add_argument("--record-baseline", action="store_true", help="store this result as the baseline")
    hk = hc.add_parser("check", help="rerun every baseline of a chord set and fail on a regression")
    hk.add_argument("set")
    hk.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    hf = hc.add_parser("reference", help="a person's reference from the chart's claims and tapped section downbeats")
    hf.add_argument("--claims", required=True, help="JSON: the chart's untimed payloads {meter, form, sequence, key}")
    hf.add_argument("--taps", required=True, help="a text file of seconds, one per line: each played section's "
                                                  "downbeat, then the end of the last")
    hf.add_argument("--recording", required=True, help="the recording (its sha256 is recorded)")
    hf.add_argument("--chart", required=True, help="the chart document (its sha256 is recorded)")
    hf.add_argument("--transposition", type=int, default=0)
    hf.add_argument("--first-occurrence", type=int, default=0,
                    help="the played section the first tap starts (default the first)")
    hf.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.command == "chords":
        try:
            return _chords(args)
        except BenchError as exc:
            print(f"bench: {exc}", file=sys.stderr)
            return 2
    try:
        root = bench_root(args.root)
        if args.command == "equivalence":
            return equivalence.check(root)
        if args.command == "region":
            return region.check(root)
        if args.command == "cost":
            return cost.check(root, record_baseline=args.record_baseline)
        bench_set = load_set(root, args.set)
        if args.command == "validate":
            verify(root, bench_set)
            print(f"{bench_set.name}: {len(bench_set.items)} items, every file present and unchanged")
            return 0
        if args.command == "run":
            jobs = [(args.variant, args.input)]
        else:
            found = sorted((root / "baselines" / bench_set.name).glob("*@*.json"))
            if not found:
                raise BenchError(f"no baselines for {bench_set.name} under {root / 'baselines'}")
            jobs = [tuple(p.stem.split("@", 1)) for p in found]
        status = 0
        for name, input_name in jobs:
            input_name = None if input_name in (None, "primary") else input_name
            variant = variants.build(name, root)
            print(f"{bench_set.name}: {name} on {input_name or 'each primary input'}")
            result = run_set(root, bench_set, variant, input_name)
            print(summary(result))
            print(f"  result {write_result(root, result)}")
            if args.command == "run" and args.record_baseline:
                print(f"  baseline {record_baseline(root, result)}")
            else:
                status = max(status, _gate(root, result, args.tolerance))
        return status
    except BenchError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
