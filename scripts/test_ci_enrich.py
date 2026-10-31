"""Contract checks for discovery partitioning, without running enrichment."""

import unittest
from ci_enrich import cases, partition


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
