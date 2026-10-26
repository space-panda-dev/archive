"""Feature execution needs content and a bank, without Archive services."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from test_bank import Fixture


class ExecutionSeam(unittest.TestCase):
    def test_isolated_cold_and_warm_execution_without_archive_services(self) -> None:
        fixture = Fixture(self)
        fixture.add("a")
        fixture.add("b", needs=("a/x",))
        # A fresh interpreter guards imports as well as executable discovery.
        # Keep the real fixture writer and runner; forbid only Archive-side
        # services and host commands. There is no archive or Door configuration.
        script = r'''
import builtins, sys
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.split(".")[0] in {"adapter", "propose", "proposers", "world", "capture", "backfill"}:
        raise AssertionError("feature execution imported archive service: " + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import extract
if sys.argv[1] == "warm":
    def bomb(*args, **kwargs):
        raise AssertionError("warm execution launched a process")
    extract.subprocess.Popen = bomb
raise SystemExit(extract.main(sys.argv[2:]))
'''
        env = {"PATH": "", "HOME": str(fixture.dir), "TMPDIR": str(fixture.dir),
               "PYTHONPATH": str(Path(__file__).resolve().parent), "BANK_TEST_LOG": str(fixture.log)}
        argv = ["run", "b", "--content", str(fixture.content), "--digest", fixture.digest,
                "--bank", str(fixture.bank), "--extractors", str(fixture.extractors)]
        for mode, expected in (("cold", "ran"), ("warm", "hit")):
            with self.subTest(mode=mode):
                result = subprocess.run([sys.executable, "-c", script, mode, *argv],
                                        cwd=fixture.dir, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                self.assertEqual(fixture.statuses(json.loads(result.stdout)), {"a": expected, "b": expected})
        self.assertEqual(fixture.runs(), ["a", "b"])
        self.assertEqual(len(fixture.entries()), 2)
