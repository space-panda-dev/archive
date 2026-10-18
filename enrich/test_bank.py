"""The feature bank and its runner (ADRs 0073, 0074).

The extractors here are real programs run by the real runner: a small Python
script whose behaviour its parameters choose, and the shipped shell
extractor. Nothing is faked between the runner and an extractor; a test that
must prove nothing ran replaces the runner's process launch and hashing with
calls that raise.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import bank
import extract
import support
from support import needs_ffmpeg

# One extractor program for every test extractor: it writes one output of
# each kind, and its `mode` parameter chooses how it misbehaves.
PROGRAM = r'''
import json, math, os, struct, subprocess, sys, time
request = json.load(open(sys.argv[1]))
mode = request["parameters"].get("mode", "ok")
out = request["output_dir"]
if mode in ("grandchild", "linger"):
    # A child of its own, as a shell script around ffmpeg has; "linger"
    # leaves it running after a successful exit.
    child = subprocess.Popen(["/bin/sh", "-c", "sleep 60"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(os.environ["BANK_TEST_PIDS"], "a") as handle:
        handle.write("%d\n" % child.pid)
    if mode == "grandchild":
        time.sleep(30)
if mode == "slow":
    time.sleep(1)
log = os.environ.get("BANK_TEST_LOG")
if log:
    with open(log, "a") as handle:
        handle.write(request["extractor"]["id"] + "\n")
for need, described in request["needs"].items():
    if not os.path.isfile(described.get("path", "")) and described["kind"] != "scalar":
        sys.exit(3)
if mode == "fail" or request["extractor"]["id"] in os.environ.get("BANK_TEST_FAIL", "").split():
    print("failing on purpose")
    sys.exit(1)
if mode == "sleep":
    with open(os.path.join(out, "x.f32"), "wb") as handle:
        handle.write(b"\0" * 4)
    time.sleep(30)
rows = 3
values = [float(i) for i in range(rows * 2)]
if mode == "nan":
    values[1] = math.nan
data = struct.pack("<%df" % len(values), *values)
if mode == "short":
    data = data[:-4]
with open(os.path.join(out, "x.f32"), "wb") as handle:
    handle.write(data)
events = [{"time": 0.5}, {"time": 0.25}] if mode == "badevents" else [{"time": 0.25, "label": "a"}, {"time": 0.5}]
spans = [{"start": 2, "end": 1}] if mode == "badspans" else [{"start": 0, "end": 1.5, "value": 2}]
json.dump(events, open(os.path.join(out, "e.json"), "w"))
json.dump(spans, open(os.path.join(out, "s.json"), "w"))
if mode == "hardlink":
    os.link(request["input"]["path"], os.path.join(out, "f.txt"))
else:
    with open(os.path.join(out, "f.txt"), "w") as handle:
        handle.write(json.dumps(sorted(request["needs"])))
if mode == "extra":
    open(os.path.join(out, "stray.bin"), "w").write("x")
shape = [rows, 3] if mode == "columns" else [rows, 2]
result = {"contract": "archive-extractor-result/1", "software": [{"name": "test", "version": "1"}],
          "grids": {"g": {"sample_rate": 100, "hop": 10, "frame": 20, "first_centre": 0}},
          "outputs": {"x": {"shape": shape}, "y": {"value": len(request["needs"])}, "e": {}, "s": {}, "f": {}}}
if mode != "noresult":
    json.dump(result, open(os.path.join(out, "result.json"), "w"))
'''

OUTPUTS = {"x": {"kind": "series", "dtype": "f32", "columns": 2, "grid": "g", "unit": "1"},
           "y": {"kind": "scalar", "unit": "1"},
           "e": {"kind": "events", "unit": "s"},
           "s": {"kind": "spans", "unit": "s"},
           "f": {"kind": "file", "media_type": "application/json", "suffix": ".txt"}}


def manifest(extractor_id: str, needs: tuple[str, ...] = (), version: str = "1", mode: str = "ok",
             accepts: tuple[str, ...] = ("audio/*",)) -> dict:
    return {"contract": "archive-extractor/1", "id": extractor_id, "version": version,
            "command": ["${python}", "program.py"], "accepts": list(accepts), "needs": list(needs),
            "parameters": {"mode": mode}, "outputs": OUTPUTS}


# Spool's capability grammar, from spool/spec/protocol.md ("Tasks"): name@version,
# name [A-Za-z0-9._-]+, version [A-Za-z0-9.]+, 1 to 128 characters in all.
SPOOL_CAPABILITY = r"[A-Za-z0-9._-]+@[A-Za-z0-9.]+"

# The code of each shipped extractor version, and of what it imports from
# outside its directory. A test fails when the code changes and the version
# does not: bump the version if the outputs' bits or meaning can change
# (a new key; old entries are never read again), or else record the new
# digest here. Nothing else catches an edit that keeps the key.
SHIPPED = {
    "frames": {"version": "1",
               "code": "sha256:8d05ebb444442eb4b08a516df7f2d03f403c4ab56aef7a74aff16446592a3282",
               "outside": {"cover.py": "sha256:9170a3edb3dfbecfac99b2d17967f3ba3ae2449021427c805dc559d936c05c55"}},
    "tonal": {"version": "2",
              "code": "sha256:81bcd9c9a129dbe60a025a3e00cfad58dd6e61f336b248039bcf43fc02fd06a4",
              "outside": {"cover.py": "sha256:9170a3edb3dfbecfac99b2d17967f3ba3ae2449021427c805dc559d936c05c55",
                          "extractors/frames/frames.py":
                              "sha256:acac662e87efbcafcebab714b858e8b42c287de7963156c7e2a14adfd860c565"}},
    "loudness-r128": {"version": "1",
                      "code": "sha256:37384ca259e5a38d61a4f09891b496bfeba339e5a10791aea2dc6b401aed0898",
                      "outside": {}},
    "rhythm": {"version": "1",
               "code": "sha256:df5fd6e9521df87351d7e1e8d844c0e73ba8847573362ce40f1baa1afd8e53c5",
               "outside": {}},
    # structure imports the span detector for its novelty curve, and the detector imports probe.
    "structure": {"version": "1",
                  "code": "sha256:717ad0dbb0cf663a154e1f59edf74e454d26ed8a3169c63d6cb4e1b2e4300393",
                  "outside": {"detector.py": "sha256:892a47fc87e7ffdd71b2c0fa38c553284ba4b4fd5ed607c0956a1a814075d9e4",
                              "probe.py": "sha256:6be7514d1671f50e43a07646fe6aa2a54cf4d35a44e608d6a27f58d8d9732384"}},
    "quality": {"version": "1",
                "code": "sha256:c0e72d32098e4726ac1dffdb6d81be36d2cceff05d2b69fab3cd12005927002d",
                "outside": {}},
    "melody": {"version": "1",
               "code": "sha256:b678b71cc9ab9e80fee833c32722bfea343bf44c418a6d5ec17556169235212d",
               "outside": {}},
    # colour reads the peaks' format and decode from waveform.py, not the
    # derived-media tool media.py, so an edit to that tool's routes or
    # command line leaves colour's pin alone.
    "colour": {"version": "2",
               "code": "sha256:ebee70e03d192d968e2ca2406fa369da5aa5ada9f67750b94c0c48ba11c7be6e",
               "outside": {"waveform.py": "sha256:353aceedf74c7a1c2bbaa330fe03847440f1e74213146bcf6bc0b863db39a424",
                           "derived.py": "sha256:d1da556693ea266247cbac481f17672578d5cc90043b3f28730e899cd3c28132"}},
    "stems": {"version": "1", "code": "sha256:235a4c30a38d1f897958ef1354441011e435709c34732cfe4aad503ab1481c3e", "outside": {}},
}


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def gone_soon(pid: int, seconds: float = 5.0) -> bool:
    """Whether the process has ended (and been reaped) within a few seconds."""
    import time
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


class Fixture:
    """A scratch directory with extractors, a content file, and a bank."""

    def __init__(self, test: unittest.TestCase):
        self.test = test
        self.dir = Path(tempfile.mkdtemp(prefix="bank-"))
        test.addCleanup(shutil.rmtree, self.dir, True)
        self.extractors = self.dir / "extractors"
        self.extractors.mkdir()
        self.bank = self.dir / "bank"
        self.content = self.dir / "take.wav"
        self.content.write_bytes(b"RIFF not really audio, but the test extractors do not decode it")
        self.digest = bank.file_digest(self.content)
        self.log = self.dir / "runs.log"
        self.pids = self.dir / "pids"
        patcher = patch.dict(os.environ, {"BANK_TEST_LOG": str(self.log), "BANK_TEST_PIDS": str(self.pids)})
        patcher.start()
        test.addCleanup(patcher.stop)

    def add(self, extractor_id: str, **options) -> Path:
        directory = self.extractors / extractor_id
        directory.mkdir(exist_ok=True)
        (directory / "program.py").write_text(PROGRAM)
        (directory / "extractor.json").write_text(json.dumps(manifest(extractor_id, **options)))
        return directory

    def runs(self) -> list[str]:
        return self.log.read_text().split() if self.log.exists() else []

    def run(self, target: str = "all", *extra: str, digest: bool = False) -> tuple[int, dict]:
        argv = ["run", target, "--content", str(self.content), "--bank", str(self.bank),
                "--extractors", str(self.extractors), *extra]
        if digest:
            argv += ["--digest", self.digest]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = extract.main(argv)
        answer = json.loads(out.getvalue() or err.getvalue())
        return code, answer

    def statuses(self, answer: dict) -> dict[str, str]:
        return {name: value["status"] for name, value in answer["extractors"].items()}

    def entries(self) -> list[Path]:
        return sorted(self.bank.glob("sha256/*/*/*/bank/*/*/entry.json"))

    def reader(self) -> bank.Bank:
        return bank.Bank(self.bank, bank.Catalog.load([self.extractors]))


class Graph(unittest.TestCase):
    def test_runs_what_is_needed_upstream_first_and_passes_it_on(self) -> None:
        f = Fixture(self)
        f.add("a")
        f.add("b", needs=("a/x",))
        f.add("c", needs=("b/y", "a/x"))
        f.add("unrelated")
        code, answer = f.run("c")
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.statuses(answer), {"a": "ran", "b": "ran", "c": "ran"})
        self.assertEqual(f.runs(), ["a", "b", "c"])
        entry = f.reader().entry(f.digest, "c")
        self.assertIsNotNone(entry)
        # c saw both needs, and its key pins the entries that held them.
        self.assertEqual(json.loads(entry.file("f").read_text()), ["a/x", "b/y"])
        self.assertEqual(entry.scalar("y"), 2)
        keys = f.reader().catalog.keys(f.digest, ["c"])
        self.assertEqual(entry.manifest["needs"], {"a/x": keys["a"], "b/y": keys["b"]})

    def test_every_extractor_comes_after_what_it_needs(self) -> None:
        f = Fixture(self)
        f.add("a")
        f.add("b", needs=("a/x",))
        f.add("c", needs=("b/y",))
        f.add("d", needs=("a/x", "c/e"))
        catalog = bank.Catalog.load([f.extractors])
        for extractor_id in catalog.extractors:
            order = catalog.order([extractor_id])
            self.assertEqual(order[-1], extractor_id)
            for need in catalog[extractor_id].needs:
                self.assertLess(order.index(need.split("/")[0]), order.index(extractor_id))

    def test_parallel_jobs_give_the_same_entries(self) -> None:
        f = Fixture(self)
        for name in ("a", "b", "c", "d"):
            f.add(name)
        f.add("e", needs=("a/x", "b/x", "c/x", "d/x"))
        code, answer = f.run("all", "--jobs", "3")
        self.assertEqual(code, 0, answer)
        self.assertEqual(set(f.statuses(answer).values()), {"ran"})
        self.assertEqual(f.runs()[-1], "e")
        self.assertEqual(len(f.entries()), 5)

    def test_cycles_and_unknown_needs_are_refused(self) -> None:
        f = Fixture(self)
        f.add("a", needs=("b/x",))
        f.add("b", needs=("a/x",))
        with self.assertRaisesRegex(bank.BankError, "cycle"):
            bank.Catalog.load([f.extractors])
        g = Fixture(self)
        g.add("a", needs=("nothing/x",))
        with self.assertRaisesRegex(bank.BankError, "no extractor here declares"):
            bank.Catalog.load([g.extractors])
        h = Fixture(self)
        h.add("a", needs=("a/x",))
        with self.assertRaisesRegex(bank.BankError, "cycle"):
            bank.Catalog.load([h.extractors])

    def test_skips_what_does_not_accept_the_media_type_and_what_needs_it(self) -> None:
        f = Fixture(self)
        f.add("pictures", accepts=("image/*",))
        f.add("after", needs=("pictures/x",))
        f.add("sound")
        code, answer = f.run("all")
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.statuses(answer), {"after": "skipped", "pictures": "skipped", "sound": "ran"})
        self.assertIn("pictures", answer["extractors"]["after"]["reason"])
        self.assertEqual(f.runs(), ["sound"])


class Cache(unittest.TestCase):
    def test_a_complete_bank_runs_and_hashes_nothing(self) -> None:
        f = Fixture(self)
        f.add("a")
        f.add("b", needs=("a/x",))

        def bomb(*_args, **_kwargs):
            raise AssertionError("a cache hit ran or hashed something")
        # Anchor: on a miss, the same bombs go off.
        with patch.object(extract.subprocess, "Popen", bomb), patch.object(bank, "file_digest", bomb):
            with self.assertRaisesRegex(AssertionError, "cache hit"):
                f.run("b", digest=True)
        self.assertEqual(f.run("b", digest=True)[0], 0)
        before = sorted(p.stat().st_mtime_ns for p in f.entries())
        with patch.object(extract.subprocess, "Popen", bomb), patch.object(bank, "file_digest", bomb):
            code, answer = f.run("b", digest=True)
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.statuses(answer), {"a": "hit", "b": "hit"})
        self.assertEqual(f.runs(), ["a", "b"])
        self.assertEqual(sorted(p.stat().st_mtime_ns for p in f.entries()), before)
        self.assertFalse((f.bank / "tmp").exists() and any((f.bank / "tmp").iterdir()))

    def test_a_new_version_upstream_reruns_everything_below_it(self) -> None:
        f = Fixture(self)
        f.add("a")
        f.add("b", needs=("a/x",))
        f.add("c")
        self.assertEqual(f.run()[0], 0)
        f.add("a", version="2")
        code, answer = f.run()
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.statuses(answer), {"a": "ran", "b": "ran", "c": "hit"})
        self.assertEqual(f.runs(), ["a", "b", "c", "a", "b"])
        # The old entries remain, unread: the bank is regenerable, so stale
        # entries cost only space.
        self.assertEqual(len(f.entries()), 5)

    def test_other_parameters_are_another_entry(self) -> None:
        f = Fixture(self)
        f.add("a")
        f.add("b", needs=("a/x",))
        self.assertEqual(f.run()[0], 0)
        config = f.dir / "config.json"
        config.write_text(json.dumps({"parameters": {"a": {"mode": "also ok"}}}))
        code, answer = f.run("all", "--config", str(config))
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.statuses(answer), {"a": "ran", "b": "ran"})
        keys_default = bank.Catalog.load([f.extractors]).keys(f.digest, ["b"])
        keys_other = bank.Catalog.load([f.extractors]).keys(f.digest, ["b"], {"a": {"mode": "also ok"}})
        self.assertNotEqual(keys_default["a"], keys_other["a"])
        self.assertNotEqual(keys_default["b"], keys_other["b"])

    def test_a_misspelt_override_is_refused_not_ignored(self) -> None:
        f = Fixture(self)
        f.add("a")
        config = f.dir / "config.json"
        for overrides, reason in (({"b": {"mode": "x"}}, "not an extractor here"),
                                  ({"a": {"mdoe": "x"}}, "does not declare")):
            with self.subTest(overrides=overrides):
                config.write_text(json.dumps({"parameters": overrides}))
                code, answer = f.run("all", "--config", str(config))
                self.assertEqual(code, 1)
                self.assertIn(reason, answer["detail"])
                with self.assertRaisesRegex(bank.BankError, reason):
                    bank.Catalog.load([f.extractors]).check_overrides(overrides)
        self.assertEqual(f.runs(), [])

    def test_two_runners_make_one_entry_once(self) -> None:
        f = Fixture(self)
        f.add("a", mode="slow")
        command = [sys.executable, str(Path(extract.__file__)), "run", "a", "--content", str(f.content),
                   "--bank", str(f.bank), "--extractors", str(f.extractors)]
        runners = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                   for _ in range(2)]
        answers = []
        for runner in runners:
            out, err = runner.communicate(timeout=60)
            self.assertEqual(runner.returncode, 0, err)
            answers.append(json.loads(out)["extractors"]["a"])
        # Anchor: both runners started before either finished, or the second simply hit.
        self.assertEqual(sorted(a["status"] for a in answers), ["hit", "ran"], answers)
        self.assertTrue([a for a in answers if a["status"] == "hit"][0].get("waited"), answers)
        self.assertEqual(f.runs(), ["a"])
        self.assertEqual(len(f.entries()), 1)

    def test_a_key_is_the_digest_of_its_canonical_description(self) -> None:
        """The key another language must compute, written out."""
        content = "sha256:" + "ab" * 32
        parameters = {"b": [1, "0.5"], "a": {"z": True, "y": None}}
        described = ('{"content":"' + content + '","contract":"archive-feature-bank-key/1","extractor":"x",'
                     '"needs":{"y/z":"sha256:' + "cd" * 32 + '"},"parameters":"'
                     + bank.digest_of(b'{"a":{"y":null,"z":true},"b":[1,"0.5"]}') + '","version":"3"}')
        self.assertEqual(bank.key_of("x", "3", parameters, content, {"y/z": "sha256:" + "cd" * 32}),
                         bank.digest_of(described.encode()))
        with self.assertRaisesRegex(bank.BankError, "decimal text"):
            bank.check_parameters({"threshold": 0.5})

    def test_parameters_are_limited_to_what_every_language_encodes_alike(self) -> None:
        """Within these limits the canonical JSON is RFC 8785's."""
        bank.check_parameters({"n": 2 ** 53 - 1, "m": -(2 ** 53 - 1), "text": "caf\u00e9 \u2028 <&>"})
        for value, reason in (({"n": 2 ** 53}, "2\\^53"), ({"caf\u00e9": 1}, "ASCII"),
                              ({"t": "\ud800"}, "lone surrogate"), ({1: 1}, "ASCII")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(bank.BankError, reason):
                    bank.check_parameters(value)
        # Non-ASCII text, U+2028, and <&> are written as themselves, as RFC 8785 writes them.
        self.assertEqual(bank.canonical({"b": "\u2028<&>\u00e9", "a": "\n\x01"}),
                         '{"a":"\\n\\u0001","b":"\u2028<&>\u00e9"}'.encode("utf-8"))


class Failure(unittest.TestCase):
    def test_a_failure_is_recorded_blocks_what_needs_it_and_spares_the_rest(self) -> None:
        f = Fixture(self)
        f.add("a")
        f.add("b", needs=("a/x",))
        f.add("c", needs=("b/x",))
        f.add("d")
        with patch.dict(os.environ, {"BANK_TEST_FAIL": "b"}):
            code, answer = f.run()
        self.assertEqual(code, 1)
        self.assertEqual(answer["status"], "failed")
        self.assertEqual(f.statuses(answer), {"a": "ran", "b": "failed", "c": "blocked", "d": "ran"})
        self.assertIn("exited 1", answer["extractors"]["b"]["error"])
        self.assertIn("failing on purpose", answer["extractors"]["b"]["log"])
        key = answer["extractors"]["b"]["key"]
        self.assertFalse(bank.entry_directory(f.bank, f.digest, "b", key).exists())
        record = bank.failure_path(f.bank, f.digest, "b", key)
        self.assertEqual(json.loads(record.read_text())["key"], key)
        self.assertIsNone(f.reader().entry(f.digest, "b"))
        # The next run retries the same key; once it succeeds, the record goes.
        code, answer = f.run()
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.statuses(answer), {"a": "hit", "b": "ran", "c": "ran", "d": "hit"})
        self.assertFalse(record.exists())

    def test_each_malformed_result_is_refused_and_leaves_no_entry(self) -> None:
        reasons = {"nan": "not finite", "short": "bytes; its shape needs", "extra": "undeclared files",
                   "noresult": "wrote no result.json", "badevents": "non-decreasing",
                   "badspans": "start and end", "columns": "is not [rows, 2]"}
        for mode, reason in reasons.items():
            with self.subTest(mode=mode):
                f = Fixture(self)
                f.add("a", mode=mode)
                code, answer = f.run()
                self.assertEqual(code, 1)
                self.assertEqual(f.statuses(answer), {"a": "failed"})
                self.assertIn(reason, answer["extractors"]["a"]["error"])
                self.assertEqual(f.entries(), [])
                self.assertEqual(list((f.bank / "tmp").iterdir()), [])

    def test_a_timeout_leaves_nothing_half_written(self) -> None:
        f = Fixture(self)
        f.add("a", mode="sleep")
        code, answer = f.run("a", "--timeout", "1")
        self.assertEqual(code, 1)
        self.assertIn("ran past", answer["extractors"]["a"]["error"])
        self.assertEqual(f.entries(), [])
        self.assertEqual(list((f.bank / "tmp").iterdir()), [])

    def test_a_timeout_stops_the_extractors_children_too(self) -> None:
        f = Fixture(self)
        f.add("a", mode="grandchild")
        code, answer = f.run("a", "--timeout", "2")
        self.assertEqual(code, 1)
        self.assertIn("ran past", answer["extractors"]["a"]["error"])
        pids = [int(line) for line in f.pids.read_text().split()]
        self.assertEqual(len(pids), 1)  # anchor: the extractor did start a child
        self.assertTrue(gone_soon(pids[0]), f"child {pids[0]} outlived its extractor's timeout")
        self.assertEqual(list((f.bank / "tmp").iterdir()), [])

    def test_what_an_extractor_leaves_running_is_stopped(self) -> None:
        f = Fixture(self)
        f.add("a", mode="linger")
        code, answer = f.run("a")
        self.assertEqual(code, 0, answer)
        pids = [int(line) for line in f.pids.read_text().split()]
        self.assertEqual(len(pids), 1)
        self.assertTrue(gone_soon(pids[0]), f"child {pids[0]} kept running after its extractor exited")

    def test_a_hard_link_to_the_input_is_refused_and_the_input_is_unchanged(self) -> None:
        f = Fixture(self)
        f.add("a", mode="hardlink")
        f.content.chmod(0o444)  # as git-annex leaves content
        self.addCleanup(f.content.chmod, 0o644)
        code, answer = f.run()
        self.assertEqual(code, 1)
        self.assertIn("hard link", answer["extractors"]["a"]["error"])
        self.assertEqual(f.content.stat().st_mode & 0o777, 0o444)
        self.assertEqual(f.content.stat().st_nlink, 1)
        self.assertEqual(f.entries(), [])

    def test_a_failure_that_cannot_be_recorded_is_still_reported(self) -> None:
        f = Fixture(self)
        f.add("a", mode="fail")
        f.add("b")

        def no_replace(*_args):
            raise OSError("read-only file system")
        with patch.object(extract.os, "replace", no_replace):
            code, answer = f.run()
        self.assertEqual(code, 1)
        self.assertEqual(f.statuses(answer), {"a": "failed", "b": "ran"})
        self.assertIn("exited 1", answer["extractors"]["a"]["error"])
        self.assertIn("read-only file system", answer["extractors"]["a"]["unrecorded"])
        key = answer["extractors"]["a"]["key"]
        record = bank.failure_path(f.bank, f.digest, "a", key)
        self.assertFalse(record.exists())
        self.assertEqual([p.name for p in record.parent.iterdir() if p.name.startswith(".")], [])

    def test_an_interrupted_install_leaves_the_bank_as_it_was(self) -> None:
        f = Fixture(self)
        f.add("a")

        def no_rename(*_args):
            raise OSError("disk went away")
        with patch.object(extract.os, "rename", no_rename):
            code, answer = f.run()
        self.assertEqual(code, 1)
        self.assertIn("disk went away", answer["extractors"]["a"]["error"])
        self.assertEqual(f.entries(), [])
        self.assertEqual(list((f.bank / "tmp").iterdir()), [])
        self.assertIsNone(f.reader().entry(f.digest, "a"))
        self.assertEqual(f.run()[0], 0)

    def test_an_entry_another_runner_filed_first_stands(self) -> None:
        f = Fixture(self)
        f.add("a")
        real = os.rename

        def racing(source, destination):
            shutil.copytree(source, destination)  # the other runner's entry
            real(source, destination)
        with patch.object(extract.os, "rename", racing):
            code, answer = f.run()
        self.assertEqual(code, 0, answer)
        self.assertEqual(answer["extractors"]["a"], {**answer["extractors"]["a"], "status": "hit", "raced": True})
        self.assertEqual(len(f.entries()), 1)

    def test_a_wrong_digest_is_refused_before_anything_is_filed(self) -> None:
        f = Fixture(self)
        f.add("a")
        f.digest = "sha256:" + "0" * 64
        code, answer = f.run(digest=True)
        self.assertEqual(code, 1)
        self.assertIn("not the sha256:000", answer["detail"])
        self.assertEqual(f.entries(), [])
        self.assertEqual(f.runs(), [])

    def test_an_unconfigured_program_is_a_failure_not_a_guess(self) -> None:
        f = Fixture(self)
        directory = f.add("a")
        f.add("b", needs=("a/x",))
        data = json.loads((directory / "extractor.json").read_text())
        data["command"] = ["${demucs-python}", "program.py"]
        (directory / "extractor.json").write_text(json.dumps(data))
        # Named, or needed by what is named, it fails.
        for target in ("a", "b"):
            code, answer = f.run(target)
            self.assertEqual(code, 1)
            self.assertIn("--program demucs-python=", answer["extractors"]["a"]["error"])
        # In `all` it is skipped, and says why, as is what needs it; the rest runs.
        f.add("c")
        code, answer = f.run("all")
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.statuses(answer), {"a": "skipped", "b": "skipped", "c": "ran"})
        self.assertIn("--program demucs-python=", answer["extractors"]["a"]["reason"])
        code, answer = f.run("all", "--program", f"demucs-python={sys.executable}")
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.statuses(answer), {"a": "ran", "b": "ran", "c": "hit"})


class Reading(unittest.TestCase):
    def test_reads_every_kind_the_runner_wrote(self) -> None:
        f = Fixture(self)
        f.add("a")
        self.assertEqual(f.run()[0], 0)
        entry = f.reader().entry(f.digest, "a")
        x = entry.series("x")
        self.assertEqual(x.shape, (3, 2))
        self.assertEqual(x.dtype.str, "<f4")
        self.assertEqual(x.tolist(), [[0, 1], [2, 3], [4, 5]])
        self.assertFalse(x.flags.writeable)
        self.assertEqual(entry.grid("x"), {"sample_rate": 100, "hop": 10, "frame": 20, "first_centre": 0})
        self.assertEqual(entry.scalar("y"), 0)
        self.assertEqual(entry.items("e"), [{"time": 0.25, "label": "a"}, {"time": 0.5}])
        self.assertEqual(entry.items("s"), [{"start": 0, "end": 1.5, "value": 2}])
        self.assertEqual(entry.output("f")["sha256"], bank.file_digest(entry.file("f")))
        self.assertEqual(entry.path.stat().st_mode & 0o777, 0o700)
        self.assertEqual({p.stat().st_mode & 0o777 for p in entry.path.iterdir()}, {0o600})
        with self.assertRaisesRegex(bank.BankError, "not a series"):
            entry.series("y")

    def test_absent_is_none_and_damage_is_an_error(self) -> None:
        f = Fixture(self)
        f.add("a")
        self.assertIsNone(f.reader().entry(f.digest, "a"))
        self.assertEqual(f.run()[0], 0)
        path = f.reader().entry(f.digest, "a").path
        (path / "entry.json").write_text("{")
        with self.assertRaisesRegex(bank.BankError, "unreadable"):
            f.reader().entry(f.digest, "a")
        (path / "entry.json").unlink()
        with self.assertRaisesRegex(bank.BankError, "without entry.json"):
            f.reader().entry(f.digest, "a")

    def test_damage_fails_only_what_needs_it(self) -> None:
        f = Fixture(self)
        f.add("a")
        f.add("b", needs=("a/x",))
        f.add("c")
        self.assertEqual(f.run("a")[0], 0)
        path = f.reader().entry(f.digest, "a").path
        (path / "entry.json").unlink()
        # The runner refuses to call damage a miss, and spares what does not need it.
        code, answer = f.run()
        self.assertEqual(code, 1)
        self.assertEqual(f.statuses(answer), {"a": "failed", "b": "blocked", "c": "ran"})
        self.assertIn("without entry.json", answer["extractors"]["a"]["error"])
        self.assertIn("delete it", answer["extractors"]["a"]["error"])
        # The bank is regenerable: deleting the damage repairs it.
        shutil.rmtree(path)
        code, answer = f.run()
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.statuses(answer), {"a": "ran", "b": "ran", "c": "hit"})

    def test_a_run_that_read_features_keeps_their_software(self) -> None:
        f = Fixture(self)
        f.add("a")
        self.assertEqual(f.run()[0], 0)
        entry = f.reader().entry(f.digest, "a")
        provenance = entry.provenance("x")
        self.assertEqual(provenance, {"feature": "a/x", "extractor": {"id": "a", "version": "1"},
                                      "entry": entry.manifest["key"],
                                      "software": [{"name": "test", "version": "1"}]})
        # A copy: deleting the entry, as the bank allows, leaves it whole.
        shutil.rmtree(entry.path)
        self.assertEqual(provenance["software"], [{"name": "test", "version": "1"}])
        with self.assertRaisesRegex(bank.BankError, "no output"):
            entry.provenance("nothing")

    def test_a_large_finite_series_is_accepted_and_any_nan_refused(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="bank-values-"))
        self.addCleanup(shutil.rmtree, directory, True)
        path = directory / "x.f64"
        import struct
        # Their sum overflows, so the values are each checked.
        path.write_bytes(struct.pack("<3d", 1e308, 1e308, 1.0))
        bank._check_series_values(path, "f64", "x")
        for bad in (float("nan"), float("inf")):
            path.write_bytes(struct.pack("<3d", 1e308, 1e308, bad))
            with self.assertRaisesRegex(bank.BankError, "not finite"):
                bank._check_series_values(path, "f64", "x")

    def test_an_entry_whose_key_does_not_match_is_refused(self) -> None:
        f = Fixture(self)
        f.add("a")
        self.assertEqual(f.run()[0], 0)
        path = f.reader().entry(f.digest, "a").path / "entry.json"
        data = json.loads(path.read_text())
        data["parameters"] = {"mode": "other"}
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(bank.BankError, "key does not match"):
            f.reader().entry(f.digest, "a")


class Manifests(unittest.TestCase):
    def test_every_extractor_is_a_spool_capability(self) -> None:
        import re
        extremes = manifest("a" + "b" * 62, version="1" + ".9" * 19 + "9")
        catalog = list(bank.Catalog.load().extractors.values()) + [bank.Extractor(Path("."), extremes)]
        self.assertEqual(len(extremes["id"]), 63)
        self.assertEqual(len(extremes["version"]), 40)
        bank.check_manifest(extremes, "extremes")
        for extractor in catalog:
            with self.subTest(extractor=extractor.id):
                capability = extract.spool_capability(extractor)
                self.assertTrue(re.fullmatch(SPOOL_CAPABILITY, capability), capability)
                self.assertLessEqual(len(capability), 128)
        for version in ("1_0", "1-rc", "1" * 41):
            with self.subTest(version=version):
                with self.assertRaisesRegex(bank.BankError, "version"):
                    bank.check_manifest(manifest("a", version=version), version)

    def test_a_shipped_extractor_changes_only_with_its_version(self) -> None:
        catalog = bank.Catalog.load()
        enrich = Path(bank.__file__).resolve().parent
        self.assertEqual(set(catalog.extractors), set(SHIPPED))
        for extractor in catalog.extractors.values():
            with self.subTest(extractor=extractor.id):
                pinned = SHIPPED[extractor.id]
                found = {"version": extractor.version, "code": bank.code_digest(extractor.directory),
                         "outside": {name: bank.file_digest(enrich / name) for name in pinned["outside"]}}
                self.assertEqual(found, pinned, "the code changed: bump the version, or record the digests")

    def test_each_shipped_pin_names_every_module_its_extractor_imports_from_outside(self) -> None:
        import ast
        enrich = Path(bank.__file__).resolve().parent
        local = {path.stem for path in enrich.glob("*.py")}
        for extractor in bank.Catalog.load().extractors.values():
            with self.subTest(extractor=extractor.id):
                imported = set()
                for program in extractor.directory.glob("*.py"):
                    for node in ast.walk(ast.parse(program.read_text(encoding="utf-8"))):
                        if isinstance(node, ast.Import):
                            imported |= {alias.name.split(".")[0] for alias in node.names}
                        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                            imported.add(node.module.split(".")[0])
                self.assertEqual({f"{name}.py" for name in imported & local}, set(SHIPPED[extractor.id]["outside"]))

    def test_a_code_digest_sees_every_file_but_byte_code(self) -> None:
        f = Fixture(self)
        directory = f.add("a")
        before = bank.code_digest(directory)
        (directory / "__pycache__").mkdir()
        (directory / "__pycache__" / "program.cpython-314.pyc").write_bytes(b"x")
        self.assertEqual(bank.code_digest(directory), before)
        (directory / "helper.sh").write_text("true\n")
        self.assertNotEqual(bank.code_digest(directory), before)

    def test_the_shipped_extractors_load_and_their_programs_exist(self) -> None:
        catalog = bank.Catalog.load()
        self.assertIn("frames", catalog.extractors)
        self.assertIn("loudness-r128", catalog.extractors)
        programs = {"python": sys.executable, "sh": "/bin/sh", "demucs-python": sys.executable}
        for extractor in catalog.extractors.values():
            command = extract.resolve_command(extractor, programs)
            self.assertTrue(any(Path(word).is_file() and Path(word).parent == extractor.directory
                                for word in command), command)

    def test_the_worked_example_is_the_shipped_extractor(self) -> None:
        guide = (Path(bank.__file__).resolve().parents[1] / "docs" / "adding-an-extractor.md").read_text()
        directory = bank.EXTRACTORS / "loudness-r128"
        self.assertIn("```sh\n" + (directory / "loudness.sh").read_text() + "```", guide)
        self.assertIn("```json\n" + (directory / "extractor.json").read_text() + "```", guide)

    def test_refuses_what_is_not_the_contract(self) -> None:
        good = manifest("a")
        bad = {"float parameter": {**good, "parameters": {"t": 0.5}},
               "unknown field": {**good, "colour": "blue"},
               "bad id": {**good, "id": "A"},
               "no outputs": {**good, "outputs": {}},
               "series without columns": {**good, "outputs": {"x": {"kind": "series", "dtype": "f32"}}},
               "unknown kind": {**good, "outputs": {"x": {"kind": "video"}}},
               "file without suffix": {**good, "outputs": {"x": {"kind": "file", "media_type": "audio/flac"}}},
               "wrong contract": {**good, "contract": "archive-extractor/2"},
               "bad need": {**good, "needs": ["x"]}}
        for name, value in bad.items():
            with self.subTest(name):
                with self.assertRaises(bank.BankError):
                    bank.check_manifest(value, name)
        self.assertEqual(bank.check_manifest(good, "good"), good)


@needs_ffmpeg
class ShellExtractor(unittest.TestCase):
    """An extractor in another language, run by the same runner."""

    def test_the_shell_extractor_measures_and_is_cached(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="bank-shell-"))
        self.addCleanup(shutil.rmtree, directory, True)
        audio = support.sine_wav(directory / "tone.wav", 48000, "3")
        argv = ["run", "loudness-r128", "--content", str(audio), "--bank", str(directory / "bank")]
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(extract.main(argv), 0, out.getvalue())
        entry = bank.Bank(directory / "bank").entry(bank.file_digest(audio), "loudness-r128")
        value = entry.scalar("integrated")
        self.assertIsInstance(value, float)
        # A 440 Hz sine at ffmpeg's default amplitude (1/8) reads about -21 LUFS.
        self.assertTrue(-25 < value < -15, value)
        self.assertEqual(entry.manifest["software"][0]["name"], "ffmpeg")
        with patch.object(extract.subprocess, "Popen", side_effect=AssertionError("ran")):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(extract.main(argv), 0)
            self.assertEqual(json.loads(out.getvalue())["extractors"]["loudness-r128"]["status"], "hit")

    def test_the_shell_extractor_refuses_settings_it_does_not_implement(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="bank-shell-"))
        self.addCleanup(shutil.rmtree, directory, True)
        audio = support.sine_wav(directory / "tone.wav", 8000, "1")
        config = directory / "config.json"
        config.write_text(json.dumps({"parameters": {"loudness-r128": {"stream": "0:a:1"}}}))
        out = io.StringIO()
        with redirect_stdout(out):
            code = extract.main(["run", "loudness-r128", "--content", str(audio), "--bank", str(directory / "b"),
                                 "--config", str(config)])
        self.assertEqual(code, 1)
        self.assertIn("fixed settings", json.loads(out.getvalue())["extractors"]["loudness-r128"]["log"])

    def test_the_command_line_runs_without_this_process(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="bank-shell-"))
        self.addCleanup(shutil.rmtree, directory, True)
        audio = support.sine_wav(directory / "tone.wav", 8000, "1")
        completed = subprocess.run([sys.executable, str(Path(extract.__file__)), "run", "loudness-r128",
                                    "--content", str(audio), "--bank", str(directory / "b"), "--dry-run"],
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        task = json.loads(completed.stdout)["extractors"]["loudness-r128"]
        self.assertEqual(task["status"], "would_run")
        self.assertEqual(task["task"]["capability"], "archive-extractor.loudness-r128@1")
        self.assertFalse((directory / "b").exists())


if __name__ == "__main__":
    unittest.main()
