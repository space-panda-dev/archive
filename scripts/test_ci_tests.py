"""Contract checks for discovery partitioning, without running enrichment."""

import unittest
from unittest.mock import patch
from types import SimpleNamespace
from ci_tests import cases, partition


def corpus(names):
    groups = []
    for name in names:
        cls = type("Example", (unittest.TestCase,), {
            "__module__": name, "test_one": lambda self: None,
            "test_two": lambda self: None})
        groups.append(unittest.TestLoader().loadTestsFromTestCase(cls))
    return unittest.TestSuite(groups)


def ids(suite):
    return [item.id() for item in cases(suite)]


class Shards(unittest.TestCase):
    def test_partition_is_exact_disjoint_and_deterministic(self):
        expected = ids(corpus(["b", "a", "c", "d"]))
        shards = [ids(partition(corpus(["b", "a", "c", "d"]), i, 3))
                  for i in range(3)]
        actual = sum(shards, [])
        self.assertCountEqual(actual, expected)
        self.assertEqual(len(actual), len(set(actual)))
        self.assertEqual(shards[0], ids(partition(corpus(["d", "c", "a", "b"]), 0, 3)))

    def test_new_module_is_automatically_assigned(self):
        self.assertCountEqual(sum((ids(partition(corpus(["a", "new"]), i, 3))
                                  for i in range(3)), []), ids(corpus(["a", "new"])))

    def test_unrelated_module_does_not_reshuffle_existing_assignments(self):
        names = ["b", "c", "d", "e", "f", "g"]
        for count in [2, 3, 5]:
            for index in range(count):
                with self.subTest(count=count, index=index):
                    before = ids(partition(corpus(names), index, count))
                    after = ids(partition(corpus(["a"] + names), index, count))
                    self.assertEqual(before, [identifier for identifier in after
                                              if not identifier.startswith("a.")])

    def test_default_partition_is_whole_corpus(self):
        self.assertCountEqual(ids(partition(corpus(["a", "b"]), 0, 1)),
                              ids(corpus(["a", "b"])))

    def test_invalid_bounds_fail(self):
        for index, count in [(-1, 3), (3, 3), (0, 0), (0, -1)]:
            with self.subTest(index=index, count=count), self.assertRaises(ValueError):
                partition(corpus(["a"]), index, count)

    def test_duplicate_ids_fail(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            partition(corpus(["a", "a"]), 0, 3)

    def test_both_phases_partition_full_corpus_exactly(self):
        names = ["test_cover", "test_profile_cache", "test_windows", "ordinary"]
        suite = corpus(names)
        optional = type("RealModel", (unittest.TestCase,), {
            "__module__": "test_notes", "test_model": lambda self: None})
        suite.addTest(unittest.TestLoader().loadTestsFromTestCase(optional))
        expected = ids(suite)
        selected = sum((ids(partition(suite, i, 3)) for i in range(3)), [])
        serial = ids(partition(suite, 0, 1, "serial"))
        self.assertEqual(len(serial), 7)
        self.assertCountEqual(selected + serial, expected)
        self.assertTrue(set(selected).isdisjoint(serial))

    def test_serial_phase_is_unsharded(self):
        for index, count in [(0, 3), (1, 3)]:
            with self.assertRaisesRegex(ValueError, "unsharded"):
                partition(corpus(["test_windows"]), index, count, "serial")

    def test_unknown_phase_fails(self):
        with self.assertRaisesRegex(ValueError, "CI_TEST_PHASE"):
            partition(corpus(["ordinary"]), 0, 1, "typo")

    def test_parallel_module_fixtures_fail_closed(self):
        for fixture in ["setUpModule", "tearDownModule"]:
            with self.subTest(fixture=fixture), patch.dict(
                    "sys.modules", {"new_module": SimpleNamespace(**{fixture: lambda: None})}):
                with self.assertRaisesRegex(ValueError, "module fixtures"):
                    partition(corpus(["new_module"]), 0, 1)

    def test_serial_module_keeps_module_fixture(self):
        with patch.dict("sys.modules", {"test_windows": SimpleNamespace(setUpModule=lambda: None)}):
            self.assertEqual(len(ids(partition(corpus(["test_windows"]), 0, 1, "serial"))), 2)

    def test_world_nested_pool_is_serial_and_union_exact(self):
        suite = corpus(["test_conformance_parallel", "test_other"])
        parallel = ids(partition(suite, 0, 1, suite_name="world"))
        serial = ids(partition(suite, 0, 1, "serial", "world"))
        self.assertEqual(len(serial), 2)
        self.assertEqual(len(parallel), 2)
        self.assertCountEqual(parallel + serial, ids(suite))
        self.assertTrue(set(parallel).isdisjoint(serial))

    def test_world_class_mode_rejects_module_fixtures(self):
        with patch.dict("sys.modules", {"test_other": SimpleNamespace(setUpModule=lambda: None)}):
            with self.assertRaisesRegex(ValueError, "module fixtures"):
                partition(corpus(["test_other"]), 0, 1, suite_name="world")

    def test_unknown_suite_fails(self):
        with self.assertRaisesRegex(ValueError, "CI_TEST_SUITE"):
            partition(corpus(["ordinary"]), 0, 1, suite_name="typo")

    def test_load_tests_forwards_pattern_and_name_filter(self):
        import ci_tests
        from unittest.mock import Mock
        discovered = Mock(errors=[])
        discovered.discover.return_value = corpus(["ordinary"])
        loader = SimpleNamespace(testNamePatterns=["*needle*"])
        with patch.dict("os.environ", {"CI_TEST_PATTERN": "test_bank.py",
                                       "CI_TEST_SUITE": "enrich",
                                       "CI_TEST_PHASE": "parallel",
                                       "SHARD_COUNT": "1", "SHARD_INDEX": "0"}), \
                patch.object(ci_tests.unittest, "TestLoader", return_value=discovered):
            self.assertEqual(len(ids(ci_tests.load_tests(loader, None, None))), 2)
        self.assertEqual(discovered.testNamePatterns, ["*needle*"])
        self.assertEqual(discovered.discover.call_args.kwargs["pattern"], "test_bank.py")


class LocalWrapper(unittest.TestCase):
    def test_serial_phase_still_runs_when_parallel_phase_fails(self):
        import os
        from pathlib import Path
        import subprocess
        import tempfile

        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temporary:
            interpreter = Path(temporary) / "interpreter"
            phases = Path(temporary) / "phases"
            interpreter.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = scripts/ci_tests.py ]; then exit 0; fi\n"
                "printf '%s\\n' \"$CI_TEST_PHASE\" >> \"$PHASE_LOG\"\n"
                "test \"$CI_TEST_PHASE\" = serial\n")
            interpreter.chmod(0o700)
            result = subprocess.run(
                ["bash", str(root / "scripts/run-enrich-tests.sh")], cwd=root,
                env={**os.environ, "ENRICH_TEST_PYTHON": str(interpreter),
                     "PHASE_LOG": str(phases)}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(phases.read_text().splitlines(), ["parallel", "serial"])


class SelectionPreflight(unittest.TestCase):
    def test_parallel_only_selection_succeeds(self):
        import ci_tests
        with patch.object(ci_tests, "discover_tests", return_value=(corpus(["ordinary"]), "enrich")):
            self.assertEqual(ci_tests.main([]), 0)

    def test_serial_only_selection_succeeds(self):
        import ci_tests
        with patch.object(ci_tests, "discover_tests", return_value=(corpus(["test_windows"]), "enrich")):
            self.assertEqual(ci_tests.main([]), 0)

    def test_no_matches_returns_five(self):
        import ci_tests
        with patch.object(ci_tests, "discover_tests", return_value=(unittest.TestSuite(), "enrich")):
            self.assertEqual(ci_tests.main([]), 5)

    def test_name_filter_uses_unittest_substring_and_glob_rules(self):
        import ci_tests
        with patch.object(ci_tests, "discover_tests", return_value=(corpus(["ordinary"]), "enrich")) as discover:
            self.assertEqual(ci_tests.main(["-k", "needle", "-k", "prefix*end"]), 0)
        self.assertEqual(discover.call_args.args[0].testNamePatterns, ["*needle*", "prefix*end"])

    def test_wrapper_allows_empty_phases_but_preserves_other_failures(self):
        import os
        from pathlib import Path
        import subprocess
        import tempfile
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temporary:
            interpreter = Path(temporary) / "interpreter"
            interpreter.write_text(
                '#!/bin/sh\n'
                'if [ "$1" = scripts/ci_tests.py ]; then exit "$PREFLIGHT_EXIT"; fi\n'
                'if [ "$CI_TEST_PHASE" = parallel ]; then exit "$PARALLEL_EXIT"; fi\n'
                'exit "$SERIAL_EXIT"\n')
            interpreter.chmod(0o700)
            for preflight, parallel, serial, expected in [
                    (0, 0, 5, 0), (0, 5, 0, 1), (5, 0, 0, 5),
                    (0, 5, 1, 1), (0, 1, 5, 1)]:
                with self.subTest(preflight=preflight, parallel=parallel, serial=serial):
                    result = subprocess.run(
                        ["bash", str(root / "scripts/run-enrich-tests.sh")], cwd=root,
                        env={**os.environ, "ENRICH_TEST_PYTHON": str(interpreter),
                             "PREFLIGHT_EXIT": str(preflight), "PARALLEL_EXIT": str(parallel),
                             "SERIAL_EXIT": str(serial)}, capture_output=True, text=True)
                    self.assertEqual(result.returncode, expected, result.stderr)


class UpstreamGrouping(unittest.TestCase):
    def test_bootstrap_uses_multiple_class_groups_without_losing_tests(self):
        import os
        try:
            from unittest_parallel.main import _iter_class_suites
        except ImportError:
            if os.environ.get("ARCHIVE_REQUIRE_INTEGRATION"):
                self.fail("the pinned unittest-parallel integration dependency is required")
            self.skipTest("the pinned unittest-parallel interpreter is needed")
        bootstrap = unittest.TestSuite([
            partition(corpus(["test_first", "test_second"]), 0, 1, suite_name="world")])
        groups = list(_iter_class_suites(bootstrap))
        self.assertEqual(len(groups), 2)
        selected = [identifier for group in groups for identifier in ids(group)]
        self.assertCountEqual(selected, ids(bootstrap))
        self.assertEqual(len(selected), len(set(selected)))


class MaintainedRunnerFailures(unittest.TestCase):
    def test_exactly_five_parallel_failures_cannot_pass_the_wrapper(self):
        import importlib.util
        import os
        from pathlib import Path
        import shutil
        import subprocess
        import sys
        import tempfile
        if importlib.util.find_spec("unittest_parallel") is None:
            if os.environ.get("ARCHIVE_REQUIRE_INTEGRATION"):
                self.fail("the pinned unittest-parallel integration dependency is required")
            self.skipTest("the pinned unittest-parallel interpreter is needed")
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            (checkout / "scripts").mkdir()
            (checkout / "enrich").mkdir()
            shutil.copyfile(root / "scripts/ci_tests.py", checkout / "scripts/ci_tests.py")
            methods = "".join(f"    def test_{i}(self): self.fail('expected failure')\n"
                              for i in range(5))
            (checkout / "enrich/test_five.py").write_text(
                "import unittest\nclass Five(unittest.TestCase):\n" + methods)
            answer = subprocess.run(
                ["bash", str(root / "scripts/run-enrich-tests.sh")], cwd=checkout,
                env={**os.environ, "ENRICH_TEST_PYTHON": sys.executable},
                capture_output=True, text=True, timeout=30)
            self.assertIn("failures=5", answer.stdout + answer.stderr)
            self.assertEqual(answer.returncode, 1, answer.stdout + answer.stderr)
