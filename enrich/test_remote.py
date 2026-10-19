"""Running an extractor through Spool and filing what comes back (ADR 0079).

The worker side is the real `remote.py work` program running the real runner
and the real `stems.py`, with only Demucs replaced by the fixed gains of
test_stems (so no test needs PyTorch or a GPU; the gains ignore the device,
which only the parameters and so the key say). The spool is a stand-in that
keeps tasks, results, and result attachments in a directory, as Spool 0.0.4
does for `put --attachments`, `results`, `take`, `collect`, and `failures`;
its results are what the real worker printed and left in `outputs/`. One
test runs the whole loop through a real Spool binary when
ARCHIVE_TEST_SPOOL names one (0.0.4 or later: result attachments).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import bank
import extract
import remote
from support import needs_ffmpeg
from test_stems import chord_with_clicks, fake_python, stems

SPOOL = os.environ.get("ARCHIVE_TEST_SPOOL")
REMOTE = Path(remote.__file__).resolve()

# A stand-in for `spool --dir DIR`: the commands the requester uses, over a
# directory: tasks/<id>.json, attachments/<hex>, done/<id>.json (the
# `results` line), results/<id>/<hex> (its attachments), failures.jsonl.
FAKE_SPOOL = r'''
import hashlib, json, os, shutil, sys
from pathlib import Path
root = Path(sys.argv[2]); words = sys.argv[3:]
for name in ("tasks", "attachments", "done", "results"):
    (root / name).mkdir(parents=True, exist_ok=True)
with open(root / "calls.jsonl", "a") as log:
    log.write(json.dumps(words) + "\n")
if words[:2] == ["put", "--attachments"]:
    for line in sys.stdin.read().splitlines():
        task = json.loads(line)
        for item in task["attachments"]:
            data = (Path(words[2]) / item["sha256"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == item["sha256"] and len(data) == item["size"]
            (root / "attachments" / item["sha256"]).write_bytes(data)
        path = root / "tasks" / (task["task_id"] + ".json")
        status = "existing" if path.exists() else "inserted"
        path.write_text(json.dumps(task))
        print(json.dumps({"task_id": task["task_id"], "status": status}))
elif words == ["results"]:
    for path in sorted((root / "done").glob("*.json")):
        print(path.read_text().strip())
elif words == ["failures"]:
    if (root / "failures.jsonl").exists():
        sys.stdout.write((root / "failures.jsonl").read_text())
elif words == ["take"]:
    request = json.loads(sys.stdin.read())
    path = root / "results" / request["task_id"] / request["sha256"]
    if not path.exists():
        sys.exit(2)
    sys.stdout.buffer.write(path.read_bytes())
elif words == ["collect"]:
    for line in sys.stdin.read().splitlines():
        name = json.loads(line)["task_id"]
        found = (root / "done" / (name + ".json")).exists()
        for path in (root / "done" / (name + ".json"), root / "tasks" / (name + ".json")):
            path.unlink(missing_ok=True)
        shutil.rmtree(root / "results" / name, ignore_errors=True)
        print(json.dumps({"task_id": name, "status": "collected" if found else "unknown"}))
else:
    sys.exit(2)
'''


def scratch(test: unittest.TestCase) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="bank-remote-"))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def mps_config(directory: Path) -> Path:
    path = directory / "mps.json"
    path.write_text(json.dumps({"parameters": {"stems": {"apply": stems.apply_on("mps")}}}))
    return path


def worker_config(directory: Path) -> Path:
    path = directory / "worker.json"
    path.write_text(json.dumps({"programs": {"demucs-python": str(fake_python(directory))}}))
    return path


def call(*argv: str) -> tuple[int, dict]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = remote.main(list(argv))
    return code, json.loads(out.getvalue() or err.getvalue())


class FakeSpool:
    """The stand-in spool, and a worker that runs its tasks as Spool would:
    a fresh directory holding the attachments, the payload on stdin, and
    `outputs/` sent back."""

    def __init__(self, directory: Path):
        self.root = directory / "spool"
        self.script = directory / "fake-spool.py"
        self.script.write_text(FAKE_SPOOL)
        self.command = f"{sys.executable} {self.script} --dir {self.root}"

    def calls(self) -> list[list[str]]:
        path = self.root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def tasks(self) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted((self.root / "tasks").glob("*.json"))]

    def work(self, config: Path, env: dict[str, str] | None = None) -> list[subprocess.CompletedProcess]:
        runs = []
        for task in self.tasks():
            if (self.root / "done" / (task["task_id"] + ".json")).exists():
                continue
            directory = Path(tempfile.mkdtemp(prefix="run-", dir=self.root))
            try:
                (directory / "attachments").mkdir()
                for item in task["attachments"]:
                    shutil.copyfile(self.root / "attachments" / item["sha256"],
                                    directory / "attachments" / item["sha256"])
                completed = subprocess.run([sys.executable, str(REMOTE), "work", "--config", str(config)],
                                           cwd=directory, input=json.dumps(task["payload"]).encode(),
                                           capture_output=True, env={**os.environ, **(env or {})})
                runs.append(completed)
                if completed.returncode != 0:
                    continue
                kept = self.root / "results" / task["task_id"]
                kept.mkdir(parents=True)
                sent = []
                for path in sorted((directory / "outputs").iterdir()):
                    shutil.copyfile(path, kept / path.name)
                    sent.append({"sha256": path.name, "size": path.stat().st_size})
                record = {"task_id": task["task_id"], "lease_id": "lease_1", "capability": task["capability"],
                          "worker": "w", "finished_at": "2026-10-18T00:00:00Z",
                          "result": json.loads(completed.stdout), "attachments": sent}
                (self.root / "done" / (task["task_id"] + ".json")).write_text(json.dumps(record))
            finally:
                shutil.rmtree(directory, ignore_errors=True)
        return runs


@needs_ffmpeg
class Keys(unittest.TestCase):
    def test_stems_on_a_gpu_are_another_key_beside_the_cpus(self) -> None:
        catalog = bank.Catalog.load()
        content = "sha256:" + "ab" * 32
        cpu = catalog.keys(content, ["stems"])["stems"]
        gpu = catalog.keys(content, ["stems"], {"stems": {"apply": stems.apply_on("mps")}})["stems"]
        self.assertNotEqual(cpu, gpu)
        self.assertEqual(catalog.keys(content, ["stems"], {"stems": {"apply": stems.apply_on("cpu")}})["stems"], cpu)

    def test_the_program_runs_on_its_devices_and_refuses_others(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 1)
        fake = fake_python(directory)
        for device, code in (("mps", 0), ("cuda", 1)):
            with self.subTest(device=device):
                config = directory / f"{device}.json"
                config.write_text(json.dumps({"parameters": {"stems": {"apply": stems.apply_on(device)}}}))
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    got = extract.main(["run", "stems", "--content", str(audio), "--bank", str(directory / "bank"),
                                        "--config", str(config), "--program", f"demucs-python={fake}"])
                self.assertEqual(got, code, out.getvalue())
                if code:
                    self.assertIn("fixed settings but for the device", json.loads(out.getvalue())
                                  ["extractors"]["stems"]["log"])


@needs_ffmpeg
class PutWorkCollect(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = scratch(self)
        self.audio = chord_with_clicks(self.directory / "take.flac", 3)
        self.digest = bank.file_digest(self.audio)
        self.config = mps_config(self.directory)
        self.spool = FakeSpool(self.directory)
        self.bank = self.directory / "bank"

    def put(self) -> tuple[int, dict]:
        return call("put", "stems", "--content", str(self.audio), "--bank", str(self.bank),
                    "--spool", self.spool.command, "--config", str(self.config))

    def collect(self) -> tuple[int, dict]:
        return call("collect", "--bank", str(self.bank), "--spool", self.spool.command, "--config", str(self.config))

    def test_a_task_carries_the_content_and_the_key_it_must_make(self) -> None:
        code, answer = self.put()
        self.assertEqual(code, 0, answer)
        key = bank.Catalog.load().keys(self.digest, ["stems"], {"stems": {"apply": stems.apply_on("mps")}})["stems"]
        self.assertEqual(answer["status"], "put")
        self.assertEqual(answer["key"], key)
        [task] = self.spool.tasks()
        self.assertEqual(task["task_id"], "bank-" + key.removeprefix("sha256:"))
        self.assertEqual(task["capability"], "archive-extractor.stems@1")
        self.assertEqual(task["attachments"], [{"sha256": self.digest.removeprefix("sha256:"),
                                                "size": self.audio.stat().st_size}])
        self.assertEqual((self.spool.root / "attachments" / self.digest.removeprefix("sha256:")).read_bytes(),
                         self.audio.read_bytes())
        payload = task["payload"]
        self.assertEqual(payload["parameters"]["apply"]["device"], "mps")
        self.assertEqual(payload["input"], {"digest": self.digest, "media_type": "audio/flac"})
        self.assertEqual(remote.check_payload(payload), payload)
        # Again while the spool holds it: the same task, not a second run.
        code, again = self.put()
        self.assertEqual((code, again["status"]), (0, "existing"))
        self.assertEqual(len(self.spool.tasks()), 1)
        self.assertFalse(list(self.bank.glob("sha256/*/*/*/bank/stems/*/entry.json")))
        self.assertFalse(list((self.bank / "tmp").iterdir()))

    def test_what_a_worker_makes_is_filed_as_a_local_run_files_it(self) -> None:
        self.assertEqual(self.put()[0], 0)
        [run] = self.spool.work(worker_config(self.directory))
        self.assertEqual(run.returncode, 0, run.stderr)
        code, answer = self.collect()
        self.assertEqual(code, 0, answer)
        [(name, task)] = answer["tasks"].items()
        self.assertEqual((task["status"], task["collected"]), ("filed", True))
        self.assertEqual(self.spool.tasks(), [])
        # The same extractor run here, on the same content and parameters, makes the same entry.
        local = self.directory / "local"
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(extract.main(["run", "stems", "--content", str(self.audio), "--bank", str(local),
                                           "--config", str(self.config),
                                           "--program", f"demucs-python={fake_python(self.directory)}"]), 0)
        parameters = {"stems": {"apply": stems.apply_on("mps")}}
        filed = bank.Bank(self.bank, parameters=parameters).entry(self.digest, "stems")
        made = bank.Bank(local, parameters=parameters).entry(self.digest, "stems")
        self.assertIsNotNone(filed)
        self.assertEqual(filed.manifest["key"], made.manifest["key"])
        self.assertEqual(filed.manifest["outputs"], made.manifest["outputs"])
        self.assertEqual(name, "bank-" + filed.manifest["key"].removeprefix("sha256:"))
        for stem in stems.STEMS:
            self.assertEqual(bank.file_digest(filed.file(stem)), made.output(stem)["sha256"])
            self.assertEqual(filed.file(stem).stat().st_mode & 0o777, 0o600)
        # The CPU's entry is another key: it is not there.
        self.assertIsNone(bank.Bank(self.bank).entry(self.digest, "stems"))
        # A run on this bank now starts nothing, and a put reaches no spool.
        with patch.object(subprocess, "Popen", side_effect=AssertionError("nothing may start")):
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(extract.main(["run", "stems", "--content", str(self.audio), "--bank",
                                               str(self.bank), "--config", str(self.config)]), 0)
            self.assertEqual(json.loads(out.getvalue())["extractors"]["stems"]["status"], "hit")
        calls = len(self.spool.calls())
        code, answer = self.put()
        self.assertEqual((code, answer["status"]), (0, "hit"))
        self.assertEqual(len(self.spool.calls()), calls)
        # Collecting again finds nothing to do.
        code, answer = self.collect()
        self.assertEqual((code, answer["tasks"]), (0, {}))

    def test_files_that_are_not_the_entrys_are_refused_and_nothing_is_filed(self) -> None:
        self.assertEqual(self.put()[0], 0)
        self.spool.work(worker_config(self.directory))
        [kept] = (self.spool.root / "results").iterdir()
        record = json.loads(next((self.spool.root / "done").glob("*.json")).read_text())
        vocals = record["result"]["entry"]["outputs"]["vocals"]["sha256"].removeprefix("sha256:")
        damaged = bytearray((kept / vocals).read_bytes())
        damaged[-100] ^= 1
        (kept / vocals).write_bytes(bytes(damaged))
        code, answer = self.collect()
        self.assertEqual(code, 1)
        [task] = answer["tasks"].values()
        self.assertEqual(task["status"], "refused")
        self.assertIn("not what the entry names", task["error"])
        self.assertFalse(list(self.bank.glob("sha256/*/*/*/bank/stems/*/entry.json")))
        self.assertTrue(Path(task["failure"]).is_file())
        self.assertFalse(list((self.bank / "tmp").iterdir()))
        self.assertEqual(len(self.spool.tasks()), 1, "a refused result is left on the spool")

    def test_a_result_whose_entry_is_not_its_tasks_is_refused(self) -> None:
        self.assertEqual(self.put()[0], 0)
        self.spool.work(worker_config(self.directory))
        path = next((self.spool.root / "done").glob("*.json"))
        original = json.loads(path.read_text())
        for change, expected in (
                (lambda r: r["result"]["entry"]["parameters"]["apply"].update(device="cpu"), "key does not match"),
                (lambda r: r.update(task_id="bank-" + "0" * 64), "capability or ID"),
                (lambda r: r.update(capability="archive-extractor.stems@2"), "capability or ID"),
                (lambda r: r.update(attachments=r["attachments"][1:]), "does not carry"),
                (lambda r: r["result"].update(contract="other/1"), "archive-extractor-task-result/1")):
            with self.subTest(expected=expected):
                record = json.loads(json.dumps(original))
                change(record)
                path.write_text(json.dumps(record))
                code, answer = self.collect()
                self.assertEqual(code, 1)
                [task] = answer["tasks"].values()
                self.assertEqual(task["status"], "refused")
                self.assertIn(expected, task["error"])
                self.assertFalse(list(self.bank.glob("sha256/*/*/*/bank/stems/*/entry.json")))
        path.write_text(json.dumps(original))
        code, answer = self.collect()
        self.assertEqual(code, 0, answer)
        # The filed entry clears the failure the refusals recorded.
        self.assertFalse(list(self.bank.glob("sha256/*/*/*/bank/stems/*.failed.json")))

    def test_a_worker_refuses_for_good_what_it_can_never_run(self) -> None:
        self.assertEqual(self.put()[0], 0)
        [task] = self.spool.tasks()
        config = worker_config(self.directory)
        cases = {
            "version": lambda p: p["extractor"].update(version="2"),
            "key": lambda p: p.update(key="sha256:" + "0" * 64),
            "parameters": lambda p: p["parameters"].update(chunk_seconds=60),
            "field": lambda p: p["parameters"].pop("levels"),
            "contract": lambda p: p.update(contract="other/1"),
            "needs": lambda p: p["extractor"].update(id="chords", version="2"),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                payload = json.loads(json.dumps(task["payload"]))
                change(payload)
                path = self.spool.root / "tasks" / (task["task_id"] + ".json")
                path.write_text(json.dumps({**task, "payload": payload}))
                [run] = self.spool.work(config)
                self.assertEqual(run.returncode, remote.EX_DATAERR, run.stderr)
        # A run that fails is a failure Spool retries, not a refusal.
        (self.spool.root / "tasks" / (task["task_id"] + ".json")).write_text(json.dumps(task))
        [run] = self.spool.work(config, env={"STEMS_FAKE": "exit"})
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertIn(b"crashed on purpose", run.stderr)
        # And a worker's configuration does not choose parameters.
        bad = self.directory / "bad-worker.json"
        bad.write_text(json.dumps({"programs": {}, "parameters": {"stems": {"chunk_seconds": 60}}}))
        [run] = self.spool.work(bad)
        self.assertEqual(run.returncode, remote.EX_DATAERR, run.stderr)

    def test_only_an_extractor_that_needs_nothing_travels(self) -> None:
        code, answer = call("put", "chords", "--content", str(self.audio), "--bank", str(self.bank),
                            "--spool", self.spool.command)
        self.assertEqual(code, 1)
        self.assertIn("only an extractor that needs no other feature", answer["detail"])
        self.assertEqual(self.spool.calls(), [])


class Stopping(unittest.TestCase):
    def test_a_stopped_worker_stops_the_extractor_it_started(self) -> None:
        """Spool stops a run by signalling its process group; the extractor
        runs in a group of its own, which the worker must stop itself."""
        directory = scratch(self)
        extractors = directory / "extractors" / "slow"
        extractors.mkdir(parents=True)
        (extractors / "extractor.json").write_text(json.dumps({
            "contract": "archive-extractor/1", "id": "slow", "version": "1", "command": ["${sh}", "slow.sh"],
            "accepts": ["audio/*"], "outputs": {"value": {"kind": "scalar"}}}))
        pid_file = directory / "pid"
        (extractors / "slow.sh").write_text(f"echo $$ > {pid_file}\nexec sleep 60\n")
        content = directory / "take.wav"
        content.write_bytes(b"RIFF")
        digest = bank.file_digest(content)
        catalog = bank.Catalog.load([directory / "extractors"])
        key = catalog.keys(digest, ["slow"])["slow"]
        payload = {"contract": remote.TASK_CONTRACT, "extractor": {"id": "slow", "version": "1"}, "key": key,
                   "parameters": {}, "input": {"digest": digest, "media_type": "audio/wav"}}
        run = directory / "run"
        (run / "attachments").mkdir(parents=True)
        shutil.copyfile(content, run / "attachments" / digest.removeprefix("sha256:"))
        worker = subprocess.Popen([sys.executable, str(REMOTE), "work", "--extractors", str(directory / "extractors")],
                                  cwd=run, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  start_new_session=True)
        assert worker.stdin is not None
        worker.stdin.write(json.dumps(payload).encode())
        worker.stdin.close()
        deadline = time.monotonic() + 20
        while not pid_file.exists() or not pid_file.read_text().strip():
            self.assertLess(time.monotonic(), deadline, "the extractor never started")
            time.sleep(0.05)
        extractor = int(pid_file.read_text())
        self.assertNotEqual(os.getpgid(extractor), worker.pid, "the extractor runs in a group of its own")
        os.killpg(worker.pid, signal.SIGTERM)  # as Spool stops a run
        self.assertEqual(worker.wait(timeout=10), 128 + signal.SIGTERM)
        worker.stdout.close()  # type: ignore[union-attr]
        worker.stderr.close()  # type: ignore[union-attr]
        deadline = time.monotonic() + 5
        while True:
            try:
                os.kill(extractor, 0)
            except ProcessLookupError:
                break
            self.assertLess(time.monotonic(), deadline, "the extractor outlived its worker")
            time.sleep(0.05)


@unittest.skipUnless(SPOOL and Path(SPOOL).is_file(),
                     "set ARCHIVE_TEST_SPOOL to a Spool 0.0.4 binary to run the loop through Spool itself")
@needs_ffmpeg
class RealSpool(unittest.TestCase):
    def test_put_work_and_collect_through_spool(self) -> None:
        directory = scratch(self)
        audio = chord_with_clicks(directory / "take.flac", 3)
        config = mps_config(directory)
        spool_dir = directory / "spool"
        subprocess.run([SPOOL, "--dir", str(spool_dir), "init"], check=True)
        command = f"{SPOOL} --dir {spool_dir}"
        code, answer = call("put", "stems", "--content", str(audio), "--bank", str(directory / "bank"),
                            "--spool", command, "--config", str(config))
        self.assertEqual((code, answer["status"]), (0, "put"), answer)
        worker = directory / "spool-worker.json"
        worker.write_text(json.dumps({
            "max_concurrent": 1, "renew_seconds": 5,
            "env": {"PATH": os.environ["PATH"], "HOME": str(directory)},
            "capabilities": {"archive-extractor.stems@1": {
                "exec": sys.executable, "args": [str(REMOTE), "work", "--config", str(worker_config(directory))],
                "timeout_seconds": 600, "max_payload_bytes": 65536, "max_output_bytes": 1 << 20,
                "max_result_attachment_bytes": 1 << 30}}}))
        worked = subprocess.run([SPOOL, "--dir", str(spool_dir), "work", "--worker", "w", "--config", str(worker),
                                 "--max-tasks", "1"], capture_output=True, timeout=600)
        self.assertEqual(worked.returncode, 0, worked.stderr)
        code, answer = call("collect", "--bank", str(directory / "bank"), "--spool", command, "--config", str(config))
        self.assertEqual(code, 0, answer)
        [task] = answer["tasks"].values()
        self.assertEqual((task["status"], task["collected"]), ("filed", True))
        entry = bank.Bank(directory / "bank", parameters={"stems": {"apply": stems.apply_on("mps")}}).entry(
            bank.file_digest(audio), "stems")
        self.assertEqual(entry.manifest["parameters"]["apply"]["device"], "mps")
        status = json.loads(subprocess.run([SPOOL, "--dir", str(spool_dir), "status", "--json"],
                                           capture_output=True, check=True).stdout)
        self.assertEqual(status, {"pending": 0, "leased": 0, "done": 0, "failed": 0})


if __name__ == "__main__":
    unittest.main()
