"""The relay's metrics (ADR 0118).

The counting functions are checked on synthetic records, banks, and roots;
the route on a relay reading a World built from a captured export, as the
local probe and as the operator. A member's refusal is checked with the
circle in test_circle.py.
"""

from __future__ import annotations

import http.client
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "relay"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import relay  # noqa: E402  (first: it puts world/ and enrich/ on the path)
import derived  # noqa: E402
import metrics  # noqa: E402
import world  # noqa: E402
from test_relay import BOOTSTRAP_EXPORT, LOGIN, OPENAPI, ORIGIN, RelayHarness, fake_archive, private  # noqa: E402

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def parse(text: str) -> dict[tuple[str, frozenset], float]:
    """The samples of a text exposition, by name and labels; every family
    has HELP and TYPE before its samples."""
    samples: dict[tuple[str, frozenset], float] = {}
    typed: set[str] = set()
    for line in text.splitlines():
        if line.startswith("# TYPE "):
            typed.add(line.split()[2])
            continue
        if not line or line.startswith("#"):
            continue
        head, value = line.rsplit(" ", 1)
        name, _, labels = head.partition("{")
        pairs = frozenset()
        if labels:
            pairs = frozenset((key, json.loads(raw)) for key, raw in
                              (item.split("=", 1) for item in _split_labels(labels.rstrip("}"))))
        family = next((f for f in typed if name == f or name in (f + "_bucket", f + "_sum", f + "_count")), None)
        assert family is not None, f"{name} has no TYPE line before it"
        samples[(name, pairs)] = float(value)
    return samples


def _split_labels(text: str) -> list[str]:
    items, current, quoted, escaped = [], "", False, False
    for char in text:
        if escaped:
            current, escaped = current + char, False
        elif char == "\\":
            current, escaped = current + char, True
        elif char == '"':
            current, quoted = current + char, not quoted
        elif char == "," and not quoted:
            items.append(current)
            current = ""
        else:
            current += char
    if current:
        items.append(current)
    return items


def value(samples: dict, name: str, **labels: str) -> float | None:
    return samples.get((name, frozenset(labels.items())))


def record(revision: str, object_id: str, shape: str, contract: str | None = None, payload=None,
           refs=(), head: bool = True, kind: str | None = None) -> dict:
    found = {"revision_id": revision, "object_id": object_id, "id": object_id, "shape": shape,
             "contracts": [{"id": contract, "version": "1"}] if contract else [],
             "payload": payload if payload is not None else {}, "refs": list(refs), "head": head}
    if kind is not None:
        found["kind"] = kind
    return found


def ref(role: str, object_id: str, revision: str | None = None) -> dict:
    return {"role": role, "id": object_id, **({"revision_id": revision} if revision else {})}


class ExpositionChecks(unittest.TestCase):
    def test_families_are_written_in_the_text_format(self) -> None:
        text = metrics.exposition([
            metrics.Family("archive_example", "gauge", "An example.",
                           [("", {"label": 'a "quoted"\\ value\n'}, 2), ("", {}, 0.5)]),
        ])
        self.assertEqual(text, '# HELP archive_example An example.\n# TYPE archive_example gauge\n'
                               'archive_example{label="a \\"quoted\\"\\\\ value\\n"} 2\n'
                               'archive_example 0.5\n')

    def test_a_histogram_counts_into_cumulative_buckets(self) -> None:
        histogram = metrics.Histogram("archive_relay_request_duration_seconds", "Time.", ("route", "outcome"),
                                      buckets=(0.1, 1.0))
        histogram.observe(("/health", "ok"), 0.05)
        histogram.observe(("/health", "ok"), 0.5)
        histogram.observe(("/health", "ok"), 5)
        samples = parse(metrics.exposition([histogram.family()]))
        name = "archive_relay_request_duration_seconds"
        self.assertEqual(value(samples, name + "_bucket", route="/health", outcome="ok", le="0.1"), 1)
        self.assertEqual(value(samples, name + "_bucket", route="/health", outcome="ok", le="1"), 2)
        self.assertEqual(value(samples, name + "_bucket", route="/health", outcome="ok", le="+Inf"), 3)
        self.assertEqual(value(samples, name + "_count", route="/health", outcome="ok"), 3)
        self.assertAlmostEqual(value(samples, name + "_sum", route="/health", outcome="ok"), 5.55)

    def test_outcomes_follow_the_status_class(self) -> None:
        self.assertEqual([metrics.outcome(s) for s in (200, 206, 304, 403, 404, 500, 503)],
                         ["ok", "ok", "ok", "refused", "refused", "failed", "failed"])


class ArchiveCountChecks(unittest.TestCase):
    """What the records of World's selected generation say."""

    def records(self) -> list[dict]:
        work, other_work = "urn:uuid:w1", "urn:uuid:w2"
        return [
            record("r-a1", "urn:uuid:a1", "artifact", payload={"content": {"digest": DIGEST_A}}),
            record("r-a2", "urn:uuid:a2", "artifact", payload={"content": {"digest": DIGEST_B}}),
            record("r-a3", "urn:uuid:a3", "artifact", payload={"content": {"digest": DIGEST_C}}),
            record("r-run1", "urn:uuid:run1", "claim", "archive.claim/derivation_run",
                   {"method": {"id": "archive-enrich/probe", "version": "1"}}),
            record("r-run2", "urn:uuid:run2", "claim", "archive.claim/derivation_run",
                   {"method": {"id": "archive-enrich/probe", "version": "2"}}),
            record("r-run3", "urn:uuid:run3", "claim", "archive.claim/derivation_run",
                   {"method": {"id": "archive-enrich/match", "version": "1"}}),
            record("r-s1", "urn:uuid:s1", "claim", "archive.claim/audio_stream", refs=[ref("artifact", "urn:uuid:a1", "r-a1")]),
            record("r-s2", "urn:uuid:s2", "claim", "archive.claim/audio_stream", refs=[ref("artifact", "urn:uuid:a2", "r-a2")]),
            record("r-s3", "urn:uuid:s3", "claim", "archive.claim/audio_stream", refs=[ref("artifact", "urn:uuid:a3", "r-a3")]),
            record("r-d", "urn:uuid:d", "transition", "archive.transition/destroy",
                   {"content": {"digest": DIGEST_C}}),
            record("r-w1", work, "entity", payload={"kind": "musical_work"}, kind="musical_work"),
            record("r-w2", other_work, "entity", payload={"kind": "musical_work"}, kind="musical_work"),
            record("r-p", "urn:uuid:p", "entity", payload={"kind": "person"}, kind="person"),
            record("r-ls-old", "urn:uuid:ls", "claim", "archive.claim/lead_sheet", refs=[ref("work", work)], head=False),
            record("r-ls", "urn:uuid:ls", "claim", "archive.claim/lead_sheet", refs=[ref("work", work)]),
            record("r-o1", "urn:uuid:o1", "claim", "archive.claim/song_occurrence"),
            record("r-o2", "urn:uuid:o2", "claim", "archive.claim/song_occurrence"),
            record("r-o3", "urn:uuid:o3", "claim", "archive.claim/song_occurrence"),
            record("r-m1", "urn:uuid:m1", "claim", "archive.claim/work_match",
                   refs=[ref("occurrence", "urn:uuid:o1", "r-o1"), ref("work", other_work)]),
            record("r-m2", "urn:uuid:m2", "claim", "archive.claim/work_match",
                   refs=[ref("occurrence", "urn:uuid:o1", "r-o1"), ref("work", work)]),
            # A version 3 window match names suggested spans, not an occurrence.
            record("r-m3", "urn:uuid:m3", "claim", "archive.claim/work_match",
                   refs=[ref("suggestions", "urn:uuid:sg", "r-sg"), ref("work", work),
                         ref("corpus", "urn:uuid:o2", "r-o2")]),
        ]

    def test_records_are_counted_by_shape_and_contract(self) -> None:
        counts = metrics.archive_counts(self.records())
        self.assertEqual(counts["shapes"], {"artifact": 3, "claim": 14, "transition": 1, "entity": 3})
        self.assertEqual(counts["contracts"]["archive.claim/lead_sheet"], 2)
        self.assertEqual(counts["contracts"]["archive.claim/derivation_run"], 3)
        self.assertNotIn("", counts["contracts"])

    def test_runs_are_counted_by_method(self) -> None:
        self.assertEqual(metrics.archive_counts(self.records())["methods"],
                         {"archive-enrich/probe": 2, "archive-enrich/match": 1})

    def test_audio_artifacts_leave_out_destroyed_content(self) -> None:
        self.assertEqual(metrics.archive_counts(self.records())["audio"], [DIGEST_A, DIGEST_B])

    def test_lead_sheets_cover_works_and_matches_cover_occurrences(self) -> None:
        counts = metrics.archive_counts(self.records())
        self.assertEqual((counts["works"], counts["works_with_lead_sheet"]), (2, 1))
        self.assertEqual((counts["occurrences"], counts["occurrences_matched"]), (3, 1))

    def test_the_counts_cross_a_process_boundary(self) -> None:
        counts = metrics.archive_counts(self.records())
        self.assertEqual(json.loads(json.dumps(counts)), counts)


def write_entry(root: Path, content: str, extractor: str, key_hex: str, version: str, filed_under: str | None = None) -> Path:
    directory = derived.directory(root, filed_under or content) / "bank" / extractor / key_hex
    directory.mkdir(parents=True)
    (directory / "entry.json").write_text(json.dumps({"extractor": {"id": extractor, "version": version}}))
    (directory / "out.bin").write_bytes(b"x" * 10)
    return directory


class Installed:
    """The part of a bank catalog the counts read."""

    def __init__(self, versions: dict[str, str], separators: set[str]):
        self.versions, self.separators = versions, separators


class BankCountChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="relay-metrics-bank-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_entries_failures_outdated_and_requests(self) -> None:
        write_entry(self.root, DIGEST_A, "stems", "1" * 64, "3")
        write_entry(self.root, DIGEST_A, "tonal", "2" * 64, "1")
        write_entry(self.root, DIGEST_B, "tonal", "3" * 64, "2")
        write_entry(self.root, DIGEST_B, "retired", "4" * 64, "1")
        failed = derived.directory(self.root, DIGEST_B) / "bank" / "stems" / ("5" * 64 + ".failed.json")
        failed.parent.mkdir(parents=True)
        failed.write_text("{}")
        (failed.parent / ("5" * 64 + ".lock")).write_text("")
        private(self.root / "requests")
        (self.root / "requests" / ("6" * 64 + ".json")).write_text("{}")
        (self.root / "tmp").mkdir()
        counts = metrics.BankCounter().count(self.root, Installed({"stems": "3", "tonal": "2"}, {"stems"}))
        self.assertEqual(counts.made, {"stems": 1, "tonal": 2, "retired": 1})
        self.assertEqual(counts.failed, {"stems": 1})
        self.assertEqual(counts.outdated, {"tonal": 1, "retired": 1})
        self.assertEqual(counts.requests, 1)
        self.assertEqual(counts.with_stems, {DIGEST_A})

    def test_an_entry_is_read_once(self) -> None:
        write_entry(self.root, DIGEST_A, "tonal", "2" * 64, "1")
        counter = metrics.BankCounter()
        installed = Installed({"tonal": "2"}, set())
        counter.count(self.root, installed)
        reads = []
        original = metrics._entry_version
        metrics._entry_version = lambda path: reads.append(path) or original(path)
        try:
            self.assertEqual(counter.count(self.root, installed).outdated, {"tonal": 1})
        finally:
            metrics._entry_version = original
        self.assertEqual(reads, [])

    def test_an_unreadable_entry_is_reported_not_counted_as_current(self) -> None:
        directory = write_entry(self.root, DIGEST_A, "tonal", "2" * 64, "2")
        (directory / "entry.json").write_text("{")
        counts = metrics.BankCounter().count(self.root, Installed({"tonal": "2"}, set()))
        self.assertEqual((counts.made, counts.unreadable), ({"tonal": 1}, 1))
        self.assertEqual(counts.outdated, {})

    def test_the_installed_catalog_names_versions_and_separators(self) -> None:
        installed = metrics.installed_extractors()
        self.assertIn("stems", installed.separators)
        self.assertNotIn("tonal", installed.separators)
        self.assertTrue(installed.versions["tonal"])


class MediaAndStoreChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="relay-metrics-media-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_media_kinds_and_practice_renders_by_audio_artifact(self) -> None:
        peaks = derived.path(self.root, DIGEST_A, "peaks")
        peaks.parent.mkdir(parents=True)
        peaks.write_bytes(b"p")
        (peaks.parent / "practice").mkdir()
        (peaks.parent / "practice" / ("7" * 64 + ".flac")).write_bytes(b"f")
        media = metrics.media_counts(self.root, [DIGEST_A, DIGEST_B])
        self.assertEqual(media.derived, {"peaks": 1, "listen": 0})
        self.assertEqual(media.with_practice, {DIGEST_A})

    def test_store_bytes_split_the_bank_from_derived_media_in_one_root(self) -> None:
        peaks = derived.path(self.root, DIGEST_A, "peaks")
        peaks.parent.mkdir(parents=True)
        peaks.write_bytes(b"p" * 3)
        entry = write_entry(self.root, DIGEST_A, "tonal", "2" * 64, "1")
        (self.root / "requests").mkdir()
        (self.root / "requests" / "r.json").write_text("{}")
        (self.root / "link").symlink_to(peaks)
        entry_bytes = sum(path.stat().st_size for path in entry.iterdir())
        self.assertEqual(metrics.derived_bytes(self.root, self.root), {"media": 3, "bank": entry_bytes + 2})

    def test_separate_roots_are_each_their_own(self) -> None:
        media, bank_root = self.root / "media", self.root / "bank"
        peaks = derived.path(media, DIGEST_A, "peaks")
        peaks.parent.mkdir(parents=True)
        peaks.write_bytes(b"p" * 3)
        (bank_root / "tmp").mkdir(parents=True)
        (bank_root / "tmp" / "work").write_bytes(b"w" * 4)
        self.assertEqual(metrics.derived_bytes(media, bank_root), {"media": 3, "bank": 4})
        self.assertEqual(metrics.derived_bytes(media, None), {"media": 3})
        self.assertEqual(metrics.derived_bytes(None, None), {})

    def test_tree_bytes_counts_regular_files_and_a_missing_tree_as_absent(self) -> None:
        (self.root / "a").mkdir()
        (self.root / "a" / "f").write_bytes(b"12345")
        self.assertEqual(metrics.tree_bytes(self.root), 5)
        self.assertIsNone(metrics.tree_bytes(self.root / "missing"))


def scrape(harness: RelayHarness, headers: dict[str, str] | None = None) -> tuple[int, dict, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", harness.server.server_address[1], timeout=60)
    try:
        connection.request("GET", "/metrics", headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


class RouteChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="relay-metrics-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.archive_root = fake_archive(self.tmp / "archive")
        (self.archive_root / "records").mkdir()
        (self.archive_root / "records" / "r.json").write_bytes(b"{}" * 8)
        self.store = self.tmp / "world-store"
        world.rebuild_store(self.archive_root, self.store, export_file=BOOTSTRAP_EXPORT)
        self.media = private(self.tmp / "derived")

    def harness(self, **options) -> RelayHarness:
        options.setdefault("archive_root", self.archive_root)
        options.setdefault("door_socket", self.tmp / "no-door" / "door.sock")
        options.setdefault("world_store", self.store)
        options.setdefault("media_root", self.media)
        harness = RelayHarness(self.tmp, **options)
        self.addCleanup(harness.close)
        return harness

    def test_described_as_text(self) -> None:
        described = OPENAPI["paths"]["/metrics"]["get"]["responses"]["200"]["content"]
        self.assertEqual(list(described), [metrics.CONTENT_TYPE])

    def test_a_local_probe_reads_the_relay_and_the_archive(self) -> None:
        harness = self.harness()
        harness.call("GET", "/health", login=None)
        harness.relay.metrics.collect()
        status, headers, body = scrape(harness)
        self.assertEqual((status, headers["Content-Type"]), (200, metrics.CONTENT_TYPE))
        samples = parse(body.decode())
        self.assertEqual(value(samples, "archive_specification_info", spec="archive/0.0.6"), 1)
        self.assertEqual(value(samples, "archive_relay_info", role="shadow", writes="dry-run",
                               commit=harness.relay.commit or "", build=harness.relay.build or ""), 1)
        self.assertEqual(value(samples, "archive_relay_request_duration_seconds_count",
                               route="/health", outcome="failed"), 1)
        self.assertEqual(value(samples, "archive_records", shape="claim"), 1)
        self.assertEqual(value(samples, "archive_records", shape="entity"), 1)
        self.assertEqual(value(samples, "archive_store_bytes", store="records", **{"class": "evidence"}), 16)
        self.assertIsNotNone(value(samples, "archive_store_bytes", store="world", **{"class": "regenerable"}))
        self.assertEqual(value(samples, "archive_store_bytes", store="media", **{"class": "regenerable"}), 0)
        self.assertIsNotNone(value(samples, "archive_metrics_collected_timestamp_seconds"))
        # No bank is configured, so it has no series rather than zeros.
        self.assertFalse([key for key in samples if key[0].startswith("archive_bank_")])
        self.assertNotIn(relay.read_archive_manifest(self.archive_root)[0], body.decode())

    def test_the_first_scrape_starts_collecting_and_answers_at_once(self) -> None:
        harness = self.harness()
        status, _, body = scrape(harness)
        self.assertEqual(status, 200)
        self.assertIn("archive_relay_info", body.decode())
        harness.relay.metrics.collected.wait(30)
        self.assertIn("archive_records{", scrape(harness)[2].decode())

    def test_unknown_paths_are_one_route_label(self) -> None:
        harness = self.harness()
        for path in ("/nope-1", "/nope-2"):
            harness.call("GET", path)
        samples = parse(scrape(harness)[2].decode())
        self.assertEqual(value(samples, "archive_relay_request_duration_seconds_count", route="other",
                               outcome="refused"), 2)

    def test_the_operator_reads_it_and_a_forwarded_request_without_identity_is_refused(self) -> None:
        harness = self.harness()
        status, _, _ = scrape(harness, {"Tailscale-User-Login": LOGIN})
        self.assertEqual(status, 200)
        for extra in ({"X-Forwarded-For": "100.64.0.9"}, {"Origin": ORIGIN}, {"Tailscale-User-Name": "Someone"}):
            with self.subTest(extra=extra):
                status, _, body = scrape(harness, extra)
                self.assertEqual((status, json.loads(body)["error"]["code"]), (403, "forbidden_identity"))
        status, _, body = scrape(harness, {"Tailscale-User-Login": "someone@example.test"})
        self.assertEqual((status, json.loads(body)["error"]["code"]), (403, "forbidden_identity"))

    def test_a_collection_failure_is_logged_and_the_rest_still_answers(self) -> None:
        harness = self.harness(world_store=self.tmp / "no-world")
        import io
        from contextlib import redirect_stderr
        logged = io.StringIO()
        with redirect_stderr(logged):
            harness.relay.metrics.collect()
        samples = parse(scrape(harness)[2].decode())
        self.assertFalse([key for key in samples if key[0] == "archive_records"])
        self.assertIsNotNone(value(samples, "archive_store_bytes", store="records", **{"class": "evidence"}))
        self.assertIn("relay: metrics:", logged.getvalue())


if __name__ == "__main__":
    unittest.main()
