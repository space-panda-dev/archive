"""Process-local, immutable output of the real published fixture writer.

Only tests that need JSON, not an archive or its content, opt in. Writer and
incremental-read tests keep using build_fixture_archive with fresh directories.
No archive is shared, copied, or retained after the export is made.
"""
from functools import cache
import json
from pathlib import Path
import tempfile


@cache
def _snapshot(door: str, archive: str) -> str:
    # Import lazily: test_conformance also consumes this helper, and unittest
    # may import that test module both by its discovery and package names.
    from world.tests.test_conformance import build_fixture_archive, remove_tree

    directory = Path(tempfile.mkdtemp(prefix="world-fixture-export-"))
    try:
        manifest, export = build_fixture_archive(directory)
        # A snapshot promises no usable archive path. Fail loudly if a new
        # consumer accidentally needs the writer's deleted scratch directory.
        del manifest["archive_root"]
        return json.dumps([manifest, export])
    finally:
        remove_tree(directory)


def fixture_export() -> tuple[dict, dict]:
    """An independent JSON copy, seeded once per writer pair in this process."""
    from world.tests.test_conformance import integration_binaries

    # The cached value is a string: callers cannot mutate the shared seed.
    manifest, export = json.loads(_snapshot(*integration_binaries()))
    return manifest, export
