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
        groups.append(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
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
        suite.addTest(unittest.defaultTestLoader.loadTestsFromTestCase(optional))
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

    def test_world_module_mode_preserves_module_fixtures(self):
        with patch.dict("sys.modules", {"test_other": SimpleNamespace(setUpModule=lambda: None)}):
            self.assertEqual(len(ids(partition(corpus(["test_other"]), 0, 1,
                                               suite_name="world"))), 2)

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
