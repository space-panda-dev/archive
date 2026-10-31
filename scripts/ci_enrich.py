"""Discovery-only CI shards; unittest and unittest-parallel own execution."""

import os
from pathlib import Path
import unittest


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def partition(suite, index, count):
    """Keep each discovered module intact and automatically assign new modules."""
    if count < 1 or not 0 <= index < count:
        raise ValueError("SHARD_INDEX must be in [0, SHARD_COUNT), count positive")
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
        modules.setdefault(name, []).append(group)
        for item in items:
            identifier = item.id()
            if identifier in identifiers:
                raise ValueError("duplicate test ID: " + identifier)
            identifiers.add(identifier)
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
    return partition(suite, index, count)
