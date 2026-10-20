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
import math
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
             accepts: tuple[str, ...] = ("audio/*",), prefers: list | None = None) -> dict:
    declared = {"contract": "archive-extractor/1", "id": extractor_id, "version": version,
                "command": ["${python}", "program.py"], "accepts": list(accepts), "needs": list(needs),
                "parameters": {"mode": mode}, "outputs": OUTPUTS}
    if prefers is not None:
        declared["prefers"] = prefers
    return declared


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
               "outside": {"cover.py": "sha256:1d3d8e48959d97ec6d633ab683eebea5bcb75616a1d29e218c25da30a7a4884c"}},
    # Version 3: the tuning from a histogram of the windows, profiles
    # referenced to it, the key as a mode; chords move to their own extractor.
    # Version 4: mains hum left out; a tonic named only where it leads; key
    # regions by note collection. Version 5: the tuning only from enough
    # windows; the tonic by the bass among the collection's notes, with a
    # calibrated confidence. Version 6: no tonic named; spans labelled and
    # valued by their collection.
    "tonal": {"version": "6",
              "code": "sha256:a21912bfd4c779778569730996b567aa0f88358bba6ff1286e44bb55d6356751",
              "outside": {"cover.py": "sha256:1d3d8e48959d97ec6d633ab683eebea5bcb75616a1d29e218c25da30a7a4884c",
                          "extractors/frames/frames.py":
                              "sha256:acac662e87efbcafcebab714b858e8b42c287de7963156c7e2a14adfd860c565"}},
    "loudness-r128": {"version": "1",
                      "code": "sha256:37384ca259e5a38d61a4f09891b496bfeba339e5a10791aea2dc6b401aed0898",
                      "outside": {}},
    # Version 2: the primary tempo level, its alternatives, meter, downbeats, spans and changes (meter.py).
    # Version 3: each take on its own, a preference for the tracker's octave, downbeats with a margin.
    # Version 4: bar lines from the low accent less the backbeat's and the beat-synchronous chroma, published
    # from a margin of 1; 5 preferred to 4 in a song that changes meter; an unmeasurable level is no evidence.
    "rhythm": {"version": "4",
               "code": "sha256:f16c19ac0501d0353ef63313e854c8db118ec92efc7b716e334ffefbbcf7c086",
               "outside": {}},
    # structure imports the span detector for its novelty curve, and the detector imports probe.
    # Version 2: the threshold is measured from a noise floor only when the recording has one
    # (steady, onset-free quiet, or unpitched quiet), so a master is all playing; a `floor`
    # scalar says which.
    "structure": {"version": "2",
                  "code": "sha256:9fc498fabca00b759a976939890ac94782c8b18110a331ef124bad6da31cfaa1",
                  # detector.py changed for ADR 0091's talk gate only; structure uses its
                  # runs, checkerboard_kernel, and novelty_curve, which did not.
                  "outside": {"detector.py": "sha256:6cb9ce7e11a2cbd89b4d5094545f2922921901944c25b7e0e311d3733d591eb1",
                              "probe.py": "sha256:6be7514d1671f50e43a07646fe6aa2a54cf4d35a44e608d6a27f58d8d9732384"}},
    # Version 3: clipping at full scale or long at the recording's own peak,
    # overs, events kept where they stand out from their surroundings or
    # stand alone in their own neighbourhood, and mains hum only.
    "quality": {"version": "3",
                "code": "sha256:43d3e0007efc65b10ccb5aa90de85b6477e8f71f50a0bd33c7b47c40e8769ea3",
                "outside": {}},
    # Version 2: 2-cent bins, a level gate on voicing, and level_db.
    # Version 3: only the rows within reach of the gate are measured; 5-cent bins,
    # and 2 on the vocal stem it prefers; measured_fraction. Still 3 when the
    # preference gained at_least, and again when it gained stems-6s/vocals after
    # stems/vocals, and again when each preference gained f0-pesto's probe: no
    # entry's bits change, only which is the default.
    "melody": {"version": "3",
               "code": "sha256:988efc9141cf5132528401484f9225dd1866a796f81afe21b467c6bd10d0bbde",
               "outside": {}},
    # colour reads the peaks' format and decode from waveform.py, not the
    # derived-media tool media.py, so an edit to that tool's routes or
    # command line leaves colour's pin alone.
    # Version 3: the centroid hue's default limits are 500 Hz to 4 kHz.
    "colour": {"version": "3",
               "code": "sha256:37bdd43515505b256e90c06f859c2e10797a569183717515c5eca0fb31e6fcc1",
               "outside": {"waveform.py": "sha256:353aceedf74c7a1c2bbaa330fe03847440f1e74213146bcf6bc0b863db39a424",
                           "derived.py": "sha256:d1da556693ea266247cbac481f17672578d5cc90043b3f28730e899cd3c28132"}},
    # The code was made to serve stems-6s too (a Separation names the model and stems); no output changed.
    # Version 1 also runs on Apple's GPU when `apply.device` is "mps": another
    # parameter, so another key; the CPU's stems are the same bits as before.
    # The manifest declares cpu and mps interchangeable (ADR 0090's amendment):
    # lookup changes, no output does.
    "stems": {"version": "1", "code": "sha256:f59644148c1d29a5f163491f422797bb1a34dbee7f155585669977022b3a1b17", "outside": {}},
    # stems-6s is the stems program with another model and six stems.
    "stems-6s": {"version": "1",
                 "code": "sha256:23494ee6838986398b491f243a017b1687921f655db1c51693275931ebf892a2",
                 "outside": {"extractors/stems/stems.py":
                                 "sha256:9329ebecb2f5c583826aa5678468d3fbfd1f81761f5f725324433fbdd2a4a057"}},
    "notes-basic-pitch": {"version": "1",
                          "code": "sha256:b81ad3ab4528b6c557e4a3ad254c5262754689efcbd157ac5bff976f28d40133",
                          "outside": {}},
    # f0-pesto version 2: the level of the same audio on the same rows.
    "f0-pesto": {"version": "2",
                 "code": "sha256:cb9e2915b3f5c4b59caac11830467fc7c0dafcf6d95bdacbbb6e6c9036c02feb",
                 "outside": {}},
    "f0-torchcrepe": {"version": "1",
                      "code": "sha256:3ea7b08895a262f9c175bcadb49fdf1ad59c6a8c69d6492aa5341e6abcafa4e7",
                      "outside": {}},
    # The learned beat and chord extractors keep the album trial's id, version, and
    # parameters, so their keys are the trial's (test_beats_chords checks one).
    "beats-beatthis": {"version": "1",
                       "code": "sha256:68c308ce04c2874d4f73399fe44491f667de4387e4e5e8760425b8f8ac1abab0",
                       "outside": {}},
    # chords-jiang pins its repository's code: a clean checkout of the pinned
    # commit makes the same bits. 2 bounds its memory: over ten minutes, its
    # convolutional layers are normalised by a sample's statistics.
    "chords-jiang": {"version": "2",
                     "code": "sha256:7a4760737d313db4842e8aa9e62fcbbe8baf8dc77bb4b7f6149ded975698aa4f",
                     "outside": {}},
    "chords-chordino": {"version": "1",
                        "code": "sha256:e404cea891963fd123d5d865c8f58ece5191bb5d0ab729aaf3d87de4426881ba",
                        "outside": {}},
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



KIND_HEADINGS = {"series": "series", "scalar": "scalars", "events": "events", "spans": "spans", "file": "files"}


def shipped_table_rows(catalog: bank.Catalog) -> str:
    """The rows of the table of shipped extractors in docs/feature-bank.md:
    each extractor after what it needs, its version, its needs by
    extractor, and its outputs by kind in manifest order."""
    rows = []
    for extractor_id in catalog.order(sorted(catalog.extractors)):
        extractor = catalog[extractor_id]
        by_source: dict[str, list[str]] = {}
        for need in extractor.needs:
            source, output = need.split("/", 1)
            by_source.setdefault(source, []).append(output)
        needs = "; ".join(f"`{source}`: " + ", ".join(outputs) for source, outputs in by_source.items()) or "none"
        by_kind: dict[str, list[str]] = {}
        for name, declared in extractor.outputs.items():
            by_kind.setdefault(declared["kind"], []).append(f"`{name}`")
        outputs = "; ".join(f"{KIND_HEADINGS[kind]} " + ", ".join(names)
                            for kind, names in sorted(by_kind.items(), key=lambda item: list(KIND_HEADINGS).index(item[0])))
        rows.append(f"| `{extractor.id}` | {extractor.version} | {needs} | {outputs} |\n")
    return "".join(rows)

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
        """Every module of enrich/ an extractor's code reaches, directly or
        through another (structure reaches probe through the detector, tonal
        cover through frames), is pinned: an edit to any of them can change
        the extractor's outputs."""
        import ast
        enrich = Path(bank.__file__).resolve().parent
        # A module name an extractor can import from outside its directory:
        # enrich/<name>.py, or another extractor's <name>.py that it puts on its path.
        modules = {path.stem: path for path in enrich.glob("*.py")}

        def imports(path: Path) -> set[str]:
            """What a module imports when it is loaded: not inside a function,
            which runs only if called (cover imports bank only to read the bank)."""
            names = set()
            nodes = [ast.parse(path.read_text(encoding="utf-8"))]
            while nodes:
                node = nodes.pop()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    continue
                nodes.extend(ast.iter_child_nodes(node))
                if isinstance(node, ast.Import):
                    names |= {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names.add(node.module.split(".")[0])
            return names

        catalog = bank.Catalog.load()
        for extractor in catalog.extractors.values():
            with self.subTest(extractor=extractor.id):
                outside = dict(modules)
                for other in catalog.extractors.values():
                    if other.directory != extractor.directory:
                        outside.update({p.stem: p for p in other.directory.glob("*.py")})
                own = {p.stem for p in extractor.directory.glob("*.py")}
                reached: set[Path] = set()
                todo = [name for program in extractor.directory.glob("*.py") for name in imports(program)]
                while todo:
                    name = todo.pop()
                    if name in own or name not in outside or outside[name] in reached:
                        continue
                    reached.add(outside[name])
                    todo.extend(imports(outside[name]))
                self.assertEqual({p.relative_to(enrich).as_posix() for p in reached},
                                 set(SHIPPED[extractor.id]["outside"]))

    def test_a_retired_extractors_entries_stay_readable_and_regenerable(self) -> None:
        """Retiring an extractor removes it from the catalog
        (docs/feature-bank.md, retired extractors): `run all` no longer
        makes or reads its entries, but they stay, read_entry reads them
        without a catalog, and its directory from history makes the same key."""
        f = Fixture(self)
        retired = f.add("retired")
        f.add("kept")
        self.assertEqual(f.run()[0], 0)
        made = f.reader().entry(f.digest, "retired")
        self.assertIsNotNone(made)
        kept_aside = f.dir / "history" / "retired"
        kept_aside.parent.mkdir()
        shutil.move(str(retired), str(kept_aside))
        self.assertNotIn("retired", bank.Catalog.load([f.extractors]).extractors)
        code, answer = f.run()
        self.assertEqual((code, f.statuses(answer)), (0, {"kept": "hit"}))
        found = bank.read_entry(made.path)
        self.assertIsNotNone(found)
        self.assertEqual(found.manifest, made.manifest)
        self.assertEqual(found.scalar("y"), made.scalar("y"))
        before = f.runs()
        code, answer = f.run("all", "--extractors", str(kept_aside.parent))
        self.assertEqual((code, f.statuses(answer)), (0, {"kept": "hit", "retired": "hit"}))
        self.assertEqual(f.runs(), before)

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
        programs = {name: sys.executable for name in ("python", "demucs-python", "stems-6s-python",
                                                      "basic-pitch-python", "f0-python", "beatthis-python",
                                                      "jiang-python", "chordino-python")}
        programs.update({"sh": "/bin/sh", "beatthis-checkpoint": "/model.ckpt", "jiang-repo": "/jiang"})
        for extractor in catalog.extractors.values():
            command = extract.resolve_command(extractor, programs)
            self.assertTrue(any(Path(word).is_file() and Path(word).parent == extractor.directory
                                for word in command), command)

    def test_the_format_lists_every_shipped_extractor_as_its_manifest_declares_it(self) -> None:
        catalog = bank.Catalog.load()
        self.assertGreater(len(catalog.extractors), 1)
        document = (Path(bank.__file__).resolve().parents[1] / "docs" / "feature-bank.md").read_text()
        _, heading, after = document.partition("\n## The shipped extractors\n")
        self.assertTrue(heading, "docs/feature-bank.md has no section on the shipped extractors")
        listed = [line + "\n" for line in after.splitlines() if line.startswith("| `")]
        self.assertEqual("".join(listed), shipped_table_rows(catalog),
                         "the table no longer matches the manifests: paste shipped_table_rows' output")

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
               "bad need": {**good, "needs": ["x"]},
               "preference not a list": {**good, "prefers": {"input": "s/f"}},
               "preference not a feature": {**good, "prefers": [{"input": "s"}]},
               "preference without input": {**good, "prefers": [{"parameters": {}}]},
               "preference with other fields": {**good, "prefers": [{"input": "s/f", "when": "always"}]},
               "preferred parameters undeclared": {**good, "prefers": [{"input": "s/f", "parameters": {"x": 1}}]},
               "preferred parameters unencodable": {**good, "prefers": [{"input": "s/f",
                                                                        "parameters": {"mode": 0.5}}]},
               "a feature preferred twice": {**good, "prefers": [{"input": "s/f"}, {"input": "s/f"}]},
               "minimum not decimal text": {**good, "prefers": [{"input": "s/f", "at_least": {"y": 0.2}}]},
               "minimum not a number": {**good, "prefers": [{"input": "s/f", "at_least": {"y": "much"}}]},
               "minimum of a series": {**good, "prefers": [{"input": "s/f", "at_least": {"x": "1"}}]},
               "minimum of an undeclared output": {**good, "prefers": [{"input": "s/f", "at_least": {"z": "1"}}]},
               "minimums not an object": {**good, "prefers": [{"input": "s/f", "at_least": ["y"]}]},
               **{f"probe {name}": {**good, "prefers": [{"input": "s/f", "probe": probe}]} for name, probe in {
                   "not an object": ["p/x"],
                   "missing a field": {"series": "p/x", "column": 1, "at_or_above": "0.5"},
                   "with another field": {**PROBE, "when": "always"},
                   "not a feature": {**PROBE, "series": "p"},
                   "column not a number": {**PROBE, "column": "1"},
                   "column a boolean": {**PROBE, "column": True},
                   "column negative": {**PROBE, "column": -1},
                   "value not decimal text": {**PROBE, "at_or_above": 0.5},
                   "share not decimal text": {**PROBE, "share_at_least": "some"}}.items()}}
        for name, value in bad.items():
            with self.subTest(name):
                with self.assertRaises(bank.BankError):
                    bank.check_manifest(value, name)
        self.assertEqual(bank.check_manifest(good, "good"), good)
        preferring = {**good, "prefers": [{"input": "s/f", "parameters": {"mode": "fine"}, "at_least": {"y": "0.2"}},
                                          {"input": "t/f", "probe": PROBE}]}
        self.assertEqual(bank.check_manifest(preferring, "preferring"), preferring)


# A probe of the fixture's series: column 1 of `x` holds 1, 3, 5, so two rows in three are at or above 3.
PROBE = {"series": "p/x", "column": 1, "at_or_above": "3", "share_at_least": "0.6"}


class Preferences(unittest.TestCase):
    """An extractor that prefers a file feature measures it by default where
    the bank holds it, with the parameters it prefers there; the runner and
    the reader decide which by one rule. `sep` stands in for a separator: its
    file output `f` is the feature."""

    def fixture(self) -> Fixture:
        f = Fixture(self)
        f.add("sep")
        f.add("m", accepts=("audio/*", "application/*"),
              prefers=[{"input": "sep/f", "parameters": {"mode": "preferred"}}])
        return f

    def test_without_the_feature_it_measures_the_content_and_never_makes_it(self) -> None:
        f = self.fixture()
        code, answer = f.run("m")
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.runs(), ["m"])
        self.assertNotIn("input", answer["extractors"]["m"])
        entry, feature = f.reader().default_entry(f.digest, "m")
        self.assertIsNone(feature)
        self.assertEqual((entry.manifest["content"], entry.manifest["parameters"]), (f.digest, {"mode": "ok"}))

    def test_with_the_feature_it_measures_it_and_the_reader_finds_that(self) -> None:
        f = self.fixture()
        self.assertEqual(f.run("sep")[0], 0)
        code, answer = f.run("m")
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.runs(), ["sep", "m"])  # sep made once, by its own run
        said = answer["extractors"]["m"]
        self.assertEqual((said["status"], said["input"]), ("ran", "sep/f"))
        self.assertEqual(answer["inputs"]["sep/f"]["extractors"]["m"]["key"], said["key"])
        reader = f.reader()
        _, feature_digest, _ = reader.input_of(f.digest, "sep/f")
        self.assertNotEqual(feature_digest, f.digest)  # anchor: the feature is other content
        entry, feature = reader.default_entry(f.digest, "m")
        self.assertEqual(feature, "sep/f")
        self.assertEqual(entry.manifest["key"], said["key"])
        self.assertEqual(entry.manifest["content"], feature_digest)
        self.assertEqual(entry.manifest["parameters"], {"mode": "preferred"})
        self.assertIsNone(reader.entry(f.digest, "m"))  # the content itself was not measured
        # Measured --from the feature, it is the same entry: the preference's parameters apply there too.
        with patch.object(extract, "run_program", side_effect=AssertionError("ran")):
            code, again = f.run("m", "--from", "sep/f")
        self.assertEqual((code, again["extractors"]["m"]["status"]), (0, "hit"))
        # A reader that does not say which feature it reads is refused, not given nothing.
        with self.assertRaisesRegex(bank.BankError, "which feature"):
            reader.entry(feature_digest, "m", filed_under=f.digest)
        # --no-prefer measures the content.
        code, plain = f.run("m", "--no-prefer")
        self.assertEqual((code, plain["extractors"]["m"]["status"]), (0, "ran"))
        self.assertNotIn("input", plain["extractors"]["m"])
        self.assertEqual(reader.entry(f.digest, "m").manifest["parameters"], {"mode": "ok"})

    def test_all_measures_the_feature_it_has_just_made(self) -> None:
        f = self.fixture()
        f.add("other")
        code, answer = f.run("all")
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.runs()[-1], "m")
        self.assertEqual(sorted(f.runs()), ["m", "other", "sep"])
        self.assertEqual(answer["extractors"]["m"]["input"], "sep/f")
        self.assertEqual(f.reader().default_entry(f.digest, "m")[0].manifest["key"], answer["extractors"]["m"]["key"])
        # A second run runs nothing.
        with patch.object(extract, "run_program", side_effect=AssertionError("ran")):
            code, again = f.run("all")
        self.assertEqual(code, 0, again)
        self.assertEqual(set(f.statuses(again).values()), {"hit"})

    def test_a_failure_on_the_feature_fails_the_run(self) -> None:
        f = self.fixture()
        self.assertEqual(f.run("sep")[0], 0)
        with patch.dict(os.environ, {"BANK_TEST_FAIL": "m"}):
            code, answer = f.run("m")
        self.assertEqual((code, answer["status"], answer["extractors"]["m"]["status"]), (1, "failed", "failed"))

    def test_a_dry_run_names_the_input_and_runs_nothing(self) -> None:
        f = self.fixture()
        self.assertEqual(f.run("sep")[0], 0)
        code, answer = f.run("m", "--dry-run")
        self.assertEqual(code, 0, answer)
        said = answer["extractors"]["m"]
        self.assertEqual((said["status"], said["input"]), ("would_run", "sep/f"))
        self.assertEqual(said["task"]["parameters"], {"mode": "preferred"})
        self.assertEqual(f.runs(), ["sep"])

    def test_what_cannot_be_preferred_is_refused(self) -> None:
        f = Fixture(self)
        f.add("sep")
        f.add("m", prefers=[{"input": "sep/x"}])  # a series, not a file
        with self.assertRaisesRegex(bank.BankError, "not a file feature"):
            bank.Catalog.load([f.extractors])
        g = Fixture(self)
        g.add("sep")
        g.add("m", needs=("sep/x",), prefers=[{"input": "sep/f"}])
        with self.assertRaisesRegex(bank.BankError, "makes or needs"):
            bank.Catalog.load([g.extractors])
        h = Fixture(self)
        h.add("m", prefers=[{"input": "m/f"}])
        with self.assertRaisesRegex(bank.BankError, "makes or needs"):
            bank.Catalog.load([h.extractors])
        # A preference no extractor here can satisfy is never present: the content is measured.
        i = Fixture(self)
        i.add("m", prefers=[{"input": "elsewhere/f"}])
        self.assertEqual(i.run("m")[0], 0)
        self.assertEqual(i.reader().default_entry(i.digest, "m")[1], None)

    def falling_short(self, minimum: str) -> Fixture:
        """`m` prefers `sep/f` while its `y` there reaches `minimum`; the
        test program writes y = 0 (the number of its needs)."""
        f = Fixture(self)
        f.add("sep")
        f.add("m", accepts=("audio/*", "application/*"),
              prefers=[{"input": "sep/f", "parameters": {"mode": "preferred"}, "at_least": {"y": minimum}}])
        return f

    def test_a_measure_below_its_minimum_is_passed_over_for_the_content(self) -> None:
        f = self.falling_short("1")
        self.assertEqual(f.run("sep")[0], 0)
        # Until the feature's measure is in the bank, the feature is the default.
        self.assertEqual(f.reader().default_input(f.digest, "m"), "sep/f")
        code, answer = f.run("m")
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.runs(), ["sep", "m", "m"])  # the stem's measure, then the content's
        said = answer["extractors"]["m"]
        self.assertNotIn("input", said)
        reader = f.reader()
        short = reader.entry_from(f.digest, "sep/f", "m")
        self.assertEqual(short.scalar("y"), 0)
        self.assertEqual(said["passed_over"], {"sep/f": short.manifest["key"]})
        entry, feature = reader.default_entry(f.digest, "m")
        self.assertIsNone(feature)
        self.assertNotEqual(entry.manifest["key"], short.manifest["key"])  # anchor: two entries
        self.assertEqual((entry.manifest["content"], entry.manifest["parameters"], entry.manifest["key"]),
                         (f.digest, {"mode": "ok"}, said["key"]))
        # The next run decides from the bank as it stands, and runs nothing.
        with patch.object(extract, "run_program", side_effect=AssertionError("ran")):
            code, again = f.run("m")
        self.assertEqual((code, again["extractors"]["m"]["status"]), (0, "hit"))
        self.assertNotIn("input", again["extractors"]["m"])
        # --from still measures the feature, and a dry run names the content.
        self.assertEqual(f.run("m", "--from", "sep/f")[1]["extractors"]["m"]["status"], "hit")
        self.assertNotIn("input", f.run("m", "--dry-run")[1]["extractors"]["m"])

    def test_all_falls_back_after_making_the_feature(self) -> None:
        f = self.falling_short("1")
        code, answer = f.run("all")
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.runs(), ["sep", "m", "m"])
        self.assertIn("sep/f", answer["extractors"]["m"]["passed_over"])
        self.assertIsNone(f.reader().default_entry(f.digest, "m")[1])

    def test_a_measure_that_reaches_its_minimum_stays_the_default(self) -> None:
        f = self.falling_short("0")
        self.assertEqual(f.run("sep")[0], 0)
        code, answer = f.run("m")
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.runs(), ["sep", "m"])
        self.assertEqual(answer["extractors"]["m"]["input"], "sep/f")
        self.assertNotIn("passed_over", answer["extractors"]["m"])
        self.assertEqual(f.reader().default_entry(f.digest, "m")[1], "sep/f")

    def test_a_failed_measure_of_the_feature_does_not_fall_back(self) -> None:
        f = self.falling_short("1")
        self.assertEqual(f.run("sep")[0], 0)
        with patch.dict(os.environ, {"BANK_TEST_FAIL": "m"}):
            code, answer = f.run("m")
        self.assertEqual((code, answer["extractors"]["m"]["status"]), (1, "failed"))
        self.assertEqual(answer["extractors"]["m"]["input"], "sep/f")
        self.assertEqual(f.runs(), ["sep", "m"])  # tried once, on the feature

    def test_a_minimum_read_from_what_is_not_a_number_is_refused(self) -> None:
        f = self.falling_short("1")
        self.assertEqual(f.run("sep")[0], 0)
        self.assertEqual(f.run("m", "--from", "sep/f")[0], 0)
        for value in (None, "0.1", True, math.nan):
            with self.subTest(value=value), patch.object(bank.Entry, "scalar", return_value=value):
                with self.assertRaisesRegex(bank.BankError, "not a number"):
                    f.reader().default_input(f.digest, "m")

    def probing(self, share: str, minimum: str = "0") -> Fixture:
        """`m` prefers `sep/f` while its `y` there reaches `minimum` (the
        test program writes 0, so "0" reaches it), and while `p`'s measure of
        `sep/f` has at least `share` of its rows at or above 3 in column 1
        of `x` (two in three do)."""
        f = Fixture(self)
        f.add("sep")
        f.add("p", accepts=("audio/*", "application/*"))
        f.add("m", accepts=("audio/*", "application/*"),
              prefers=[{"input": "sep/f", "parameters": {"mode": "preferred"}, "at_least": {"y": minimum},
                        "probe": {**PROBE, "share_at_least": share}}])
        self.assertEqual(f.run("sep")[0], 0)
        return f

    def test_a_probe_that_falls_short_passes_the_feature_over_unmeasured(self) -> None:
        f = self.probing("0.7")
        # Until the probe's measure is in the bank, the feature is the default.
        self.assertEqual(f.reader().default_input(f.digest, "m"), "sep/f")
        self.assertEqual(f.run("p", "--from", "sep/f")[0], 0)
        reader = f.reader()
        probe = reader.entry_from(f.digest, "sep/f", "p")
        self.assertIsNotNone(probe)  # anchor: the probe measured the feature
        self.assertIsNone(reader.default_input(f.digest, "m"))
        code, answer = f.run("m")
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.runs(), ["sep", "p", "m"])  # m ran once, on the content
        said = answer["extractors"]["m"]
        self.assertNotIn("input", said)
        self.assertNotIn("passed_over", said)
        self.assertEqual(said["probed_over"], {"sep/f": probe.manifest["key"]})
        self.assertIsNone(f.reader().entry_from(f.digest, "sep/f", "m"))
        entry, feature = f.reader().default_entry(f.digest, "m")
        self.assertIsNone(feature)
        self.assertEqual((entry.manifest["content"], entry.manifest["key"]), (f.digest, said["key"]))
        # The next run decides from the bank as it stands, and runs nothing.
        with patch.object(extract, "run_program", side_effect=AssertionError("ran")):
            code, again = f.run("m")
        self.assertEqual((code, again["extractors"]["m"]["status"]), (0, "hit"))

    def test_a_probe_that_reaches_its_share_leaves_the_feature_measured(self) -> None:
        f = self.probing("0.6")
        self.assertEqual(f.run("p", "--from", "sep/f")[0], 0)
        code, answer = f.run("m")
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.runs(), ["sep", "p", "m"])
        self.assertEqual(answer["extractors"]["m"]["input"], "sep/f")
        self.assertNotIn("probed_over", answer["extractors"]["m"])
        self.assertEqual(f.reader().default_entry(f.digest, "m")[1], "sep/f")

    def test_without_the_probe_the_feature_is_measured_and_its_minimum_decides(self) -> None:
        # The probe would fall short, but it is not in the bank: as without a probe.
        f = self.probing("0.7", minimum="1")
        code, answer = f.run("m")
        self.assertEqual(code, 0, answer)
        self.assertEqual(f.runs(), ["sep", "m", "m"])
        self.assertIn("sep/f", answer["extractors"]["m"]["passed_over"])
        self.assertNotIn("probed_over", answer["extractors"]["m"])
        g = self.probing("0.7")
        self.assertEqual(g.run("m")[1]["extractors"]["m"]["input"], "sep/f")

    def test_the_extractors_own_measure_decides_over_its_probe(self) -> None:
        f = self.probing("0.7")
        self.assertEqual(f.run("m", "--from", "sep/f")[0], 0)
        self.assertEqual(f.run("p", "--from", "sep/f")[0], 0)
        reader = f.reader()
        self.assertIsNotNone(reader.probe_falls_short(f.digest, reader.catalog["m"].prefers[0]))  # anchor
        self.assertEqual(reader.default_input(f.digest, "m"), "sep/f")
        with patch.object(extract, "run_program", side_effect=AssertionError("ran")):
            code, answer = f.run("m")
        self.assertEqual((code, answer["extractors"]["m"]["input"]), (0, "sep/f"))
        self.assertNotIn("probed_over", answer["extractors"]["m"])

    def test_a_probe_reads_the_measure_of_the_feature_not_of_the_content(self) -> None:
        f = self.probing("0.7")
        self.assertEqual(f.run("p")[0], 0)  # p of the content, not of sep/f
        self.assertEqual(f.reader().default_input(f.digest, "m"), "sep/f")

    def test_a_probe_must_read_a_column_of_another_extractors_float_series(self) -> None:
        for name, probe, message in (
                ("its own", {**PROBE, "series": "m/x"}, "its own"),
                ("a scalar", {**PROBE, "series": "p/y"}, "floating-point series"),
                ("a column it lacks", {**PROBE, "column": 2}, "which has 2")):
            with self.subTest(name):
                f = Fixture(self)
                f.add("sep")
                f.add("p")
                f.add("m", prefers=[{"input": "sep/f", "probe": probe}])
                with self.assertRaisesRegex(bank.BankError, message):
                    bank.Catalog.load([f.extractors])
        # A probe no extractor here declares is never present.
        g = Fixture(self)
        g.add("sep")
        g.add("m", prefers=[{"input": "sep/f", "probe": {**PROBE, "series": "elsewhere/x"}}])
        self.assertEqual(g.run("sep")[0], 0)
        self.assertEqual(g.reader().default_input(g.digest, "m"), "sep/f")

    def test_the_preferred_parameters_yield_to_the_operators(self) -> None:
        catalog = bank.Catalog([bank.Extractor(Path("."), manifest("sep")), bank.Extractor(
            Path("."), manifest("m", prefers=[{"input": "sep/f", "parameters": {"mode": "preferred"}}]))])
        self.assertEqual(catalog.overrides_for("sep/f"), {"m": {"mode": "preferred"}})
        self.assertEqual(catalog.overrides_for("sep/f", {"m": {"mode": "mine"}}), {"m": {"mode": "mine"}})
        self.assertEqual(catalog.overrides_for("other/f", {"sep": {"mode": "x"}}), {"sep": {"mode": "x"}})


class MediaType(unittest.TestCase):
    """Archive content files have no telling suffix (`.blob`), so the runner
    reads the media type from the first bytes."""

    def test_suffix_less_content_is_read_by_its_first_bytes(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="bank-type-"))
        self.addCleanup(shutil.rmtree, directory, True)
        wav = support.write_wav(directory / "tone.wav", [0.0] * 800, 8000)
        blob = directory / "content.blob"
        shutil.copyfile(wav, blob)
        # Anchor: the name alone tells nothing.
        self.assertIsNone(extract.mimetypes.guess_type(blob.name)[0])
        self.assertEqual(extract.media_type_of(blob), "audio/wav")
        flac = directory / "other.blob"
        flac.write_bytes(b"fLaC" + bytes(40))
        self.assertEqual(extract.media_type_of(flac), "audio/flac")
        completed = subprocess.run([sys.executable, str(Path(extract.__file__)), "run", "loudness-r128",
                                    "--content", str(blob), "--bank", str(directory / "b"), "--dry-run"],
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["extractors"]["loudness-r128"]["status"], "would_run")

    def test_unknown_or_unreadable_content_still_needs_media_type(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="bank-type-"))
        self.addCleanup(shutil.rmtree, directory, True)
        unknown = directory / "content.blob"
        unknown.write_bytes(b"not a known container")
        for path in (unknown, directory / "missing.blob"):
            with self.assertRaisesRegex(extract.RunError, "pass --media-type"):
                extract.media_type_of(path)
        # Unreadable is refused as unreadable, never as an unknown type.
        unreadable = directory / "folder"
        unreadable.mkdir()
        with self.assertRaises(OSError):
            extract.sniff_media_type(unreadable)
        with self.assertRaisesRegex(extract.RunError, "cannot read the first bytes .*pass --media-type"):
            extract.media_type_of(unreadable)
        with self.assertRaisesRegex(extract.RunError, "from its name or its first bytes"):
            extract.media_type_of(unknown)
        # The suffix still wins, without reading the file.
        self.assertEqual(extract.media_type_of(directory / "card.WAV"), "audio/wav")


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
