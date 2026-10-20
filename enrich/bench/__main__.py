"""python -m enrich.bench validate SET | run SET --variant NAME | check SET | equivalence | cost

Exit status: 0 passed, 1 a mean regressed beyond the tolerance, 2 the run
could not happen (a missing or changed file, a bad manifest or config, a
variant that failed).
"""

from __future__ import annotations

import argparse
import json
import sys

from . import cost, equivalence, variants
from .run import (DEFAULT_TOLERANCE, baseline_path, compare, record_baseline, run_set, summary,
                  write_result)
from .sets import BenchError, bench_root, load_set, verify


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
    k = commands.add_parser("cost", help="measure the backfill's CPU seconds per audio-second over the root's clip "
                                         "and fail on a regression (see enrich/bench/cost.py)")
    k.add_argument("--record-baseline", action="store_true", help="store this result as the baseline")
    args = parser.parse_args(argv)
    try:
        root = bench_root(args.root)
        if args.command == "equivalence":
            return equivalence.check(root)
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
