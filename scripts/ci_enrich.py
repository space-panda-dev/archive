"""Discovery-only CI shards; unittest and unittest-parallel own execution."""

import os
import sys
from pathlib import Path
import unittest


# These tests start their own process pools or own module cleanup. Optional
# heavyweight integrations also remain exclusive when installed and enabled.
SERIAL_MODULES = frozenset({"test_cover", "test_profile_cache", "test_windows"})
SERIAL_CLASSES = frozenset({
    "test_beats_chords.RealBeatThis", "test_beats_chords.RealChordino",
    "test_beats_chords.RealJiang", "test_f0.RealTrackers",
    "test_notes.RealModel", "test_stems.RealDemucs", "test_remote.RealSpool",
})


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def partition(suite, index, count, phase="parallel"):
    """Keep each discovered module intact and automatically assign new modules."""
    if count < 1 or not 0 <= index < count:
        raise ValueError("SHARD_INDEX must be in [0, SHARD_COUNT), count positive")
    if phase not in {"parallel", "serial"}:
        raise ValueError("CI_TEST_PHASE must be parallel or serial")
    if phase == "serial" and (index, count) != (0, 1):
        raise ValueError("serial phase must be unsharded")
    modules = {}
    identifiers = set()
    for group in suite:
        items = list(cases(group))
        if not items:
            continue
        names = {type(item).__module__ for item in items}
        if len(names) != 1:
            raise ValueError("discovery group spans multiple modules")
        name = names.pop()
        for item in items:
            identifier = item.id()
            if identifier in identifiers:
                raise ValueError("duplicate test ID: " + identifier)
            identifiers.add(identifier)
        classes = {}
        for item in items:
            cls = type(item)
            serial = name in SERIAL_MODULES or f"{name}.{cls.__name__}" in SERIAL_CLASSES
            if serial != (phase == "serial"):
                continue
            if not serial:
                module = sys.modules.get(name)
                if any(callable(getattr(module, fixture, None))
                       for fixture in ("setUpModule", "tearDownModule")):
                    raise ValueError("module fixtures require serial selection: " + name)
            classes.setdefault(cls, []).append(item)
        if classes:
            modules.setdefault(name, []).append(unittest.TestSuite(
                unittest.TestSuite(items) for items in classes.values()))
    selected = sorted(modules)[index::count]
    return unittest.TestSuite(group for name in selected for group in modules[name])


def load_tests(loader, tests, pattern):
    del tests, pattern
    count = int(os.environ.get("SHARD_COUNT", "1"))
    index = int(os.environ.get("SHARD_INDEX", "0"))
    # A separate loader avoids reusing discovery's scripts/ top-level directory.
    discover = unittest.TestLoader()
    discover.testNamePatterns = loader.testNamePatterns
    suite = discover.discover(str(Path(__file__).resolve().parent.parent / "enrich"),
                              pattern="test_*.py")
    if discover.errors:
        raise RuntimeError("enrichment discovery failed: " + "\n".join(discover.errors))
    return partition(suite, index, count, os.environ.get("CI_TEST_PHASE", "parallel"))
