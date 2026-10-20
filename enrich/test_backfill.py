"""`enrich backfill` (ADR 0112): the slot and thread decision, the plan each
mode makes, the pool, the Spool fan-out, and one backfill end to end on a
scratch archive and a scratch bank with every learned model replaced."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import backfill
import bank
import support
from backfill import DONE, FAILED, TODO, WAITING, Job, Recording, Unit
from support import needs_door
from test_cover import needs_essentia

HERE = Path(__file__).resolve().parent
CATALOG = bank.Catalog.load()
STEMS, MELODY, RHYTHM, STRUCTURE = Unit("stems"), Unit("melody"), Unit("rhythm"), Unit("structure")
BASS_TONAL, BASS_F0 = Unit("tonal", "stems/bass"), Unit("f0-pesto", "stems/bass")


def scratch(test: unittest.TestCase) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="backfill-"))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def recording(name: str, songs: bool = False, seconds: float = 600.0) -> Recording:
    digest = "sha256:" + (name.encode().hex() * 64)[:64]
    return Recording(f"urn:uuid:{name}", digest, Path("/nonexistent") / name, seconds, songs, "audio/wav")


def schedule(units: tuple[Unit, ...] = (STEMS, RHYTHM, STRUCTURE, MELODY, BASS_TONAL, BASS_F0),
             **numbers) -> backfill.Schedule:
    return backfill.schedule_of({"backfill": {"units": [[u.extractor, *([u.source] if u.source else [])]
                                                        for u in units], **numbers}}, CATALOG)


class FakePlanner:
    """States by (recording, unit); a unit run makes it done, and stems make
    what measures them runnable."""

    def __init__(self, states: dict[tuple[str, Unit], str] | None = None, default: str = TODO):
        self.catalog, self.root, self.parameters = CATALOG, Path("/nonexistent"), {}
        self.states = dict(states or {})
        self.default = default
        self.lock = threading.Lock()

    def state(self, rec: Recording, unit: Unit) -> tuple[str, str | None]:
        with self.lock:
            found = self.states.get((rec.object_id, unit))
            if found is None:
                found = WAITING if unit.source and self.states.get((rec.object_id, STEMS)) != DONE else self.default
            return found, None

    def made(self, rec: Recording, unit: Unit) -> None:
        with self.lock:
            self.states[(rec.object_id, unit)] = DONE


def shape(sched: backfill.Schedule, cores: int = 8, memory: float = 32.0) -> dict:
    return backfill.layout(CATALOG, sched, {}, cores, memory)


class SlotsAndThreads(unittest.TestCase):
    def test_copies_fill_the_cores_at_their_threads_bounded_by_memory(self) -> None:
        self.assertEqual(backfill.copies(8, 64.0, 1, 1.0), 8)
        self.assertEqual(backfill.copies(8, 64.0, 2, 3.0), 4)
        self.assertEqual(backfill.copies(8, 7.0, 2, 3.0), 2)  # memory binds before cores
        self.assertEqual(backfill.copies(8, 1.0, 2, 3.0), 1)  # never none
        self.assertEqual(backfill.copies(1, 64.0, 2, 1.0), 1)

    def test_the_layout_takes_an_extractors_fixed_threads_over_the_configured(self) -> None:
        sched = schedule((STEMS, RHYTHM, Unit("chords-jiang")))
        self.assertEqual(CATALOG.parameters("chords-jiang", {}).get("threads"), 3)
        got = shape(sched, 8, 32.0)
        self.assertEqual(got["stems"], {"threads": 2, "memory_gb": 3.0, "copies": 4})
        self.assertEqual(got["rhythm"]["threads"], 1)
        self.assertEqual(got["rhythm"]["copies"], 8)
        self.assertEqual(got["chords-jiang"]["threads"], 3)
        self.assertEqual(got["chords-jiang"]["copies"], 2)
        # Configured threads change a copy's width; never wider than the machine.
        wide = backfill.schedule_of({"backfill": {"units": [["stems"]],
                                                  "extractors": {"stems": {"threads": 16}}}}, CATALOG)
        self.assertEqual(backfill.layout(CATALOG, wide, {}, 8, 32.0)["stems"]["threads"], 8)

    def test_every_numeric_library_is_held_to_the_threads(self) -> None:
        environment = backfill.thread_environment(2)
        for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
            self.assertEqual(environment[name], "2")

    def test_the_configuration_refuses_what_it_does_not_know(self) -> None:
        for given in ({"unknown": 1}, {"units": [["no-such-extractor"]]}, {"units": [["tonal", "rhythm/beats"]]},
                      {"extractors": {"stems": {"route": "cloud"}}}, {"extractors": {"stems": {"gpu": True}}}):
            with self.subTest(given=given), self.assertRaises((backfill.BackfillError, bank.BankError)):
                backfill.schedule_of({"backfill": given}, CATALOG)

    def test_expensive_is_the_recorded_cost_above_the_threshold(self) -> None:
        sched = schedule()
        expensive = {e for e in backfill.SETTINGS if sched.expensive(e)}
        self.assertEqual(expensive, {"stems", "f0-pesto", "melody", "chords-jiang", "notes-basic-pitch"})
        self.assertFalse(schedule(expensive_above=1.0).expensive("stems"))


class Plans(unittest.TestCase):
    def plan(self, recordings, mode, planner=None, sched=None, spool=False):
        sched = sched or schedule()
        return backfill.plan(recordings, sched, planner or FakePlanner(), mode, shape(sched), spool)

    def test_eager_plans_every_unit_and_what_measures_stems_waits_for_them(self) -> None:
        song, other = recording("a", songs=True), recording("b")
        planned = self.plan([other, song], "eager")
        self.assertEqual(len(planned.jobs), 12)
        self.assertEqual(planned.jobs[0].recording, other)  # eager keeps the archive's order
        jobs = {(j.recording.object_id, j.unit): j for j in planned.jobs}
        stems = jobs[(other.object_id, STEMS)]
        self.assertNotEqual(stems, jobs[(song.object_id, STEMS)])
        for unit in (MELODY, BASS_TONAL, BASS_F0):
            self.assertEqual(jobs[(other.object_id, unit)].after, [stems])
        self.assertEqual(jobs[(other.object_id, STRUCTURE)].after, [jobs[(other.object_id, RHYTHM)]])
        self.assertEqual(stems.dependents, 3)

    def test_songs_defers_expensive_units_and_what_measures_them_where_there_is_no_song(self) -> None:
        song, other = recording("a", songs=True), recording("b")
        planned = self.plan([other, song], "songs")
        self.assertEqual(planned.jobs[0].recording, song)  # song recordings first
        made = {(j.recording.object_id, j.unit) for j in planned.jobs}
        self.assertEqual({u for r, u in made if r == song.object_id},
                         {STEMS, RHYTHM, STRUCTURE, MELODY, BASS_TONAL, BASS_F0})
        self.assertEqual({u for r, u in made if r == other.object_id}, {RHYTHM, STRUCTURE})
        later = {u: why for r, u, why in planned.deferred if r is other}
        self.assertEqual(set(later), {STEMS, MELODY, BASS_TONAL, BASS_F0})
        self.assertIn("on demand", later[BASS_TONAL])

    def test_a_cheap_unit_runs_where_the_stems_it_measures_are_already_made(self) -> None:
        other = recording("b")
        planner = FakePlanner({(other.object_id, STEMS): DONE})
        planned = self.plan([other], "songs", planner)
        self.assertEqual({j.unit for j in planned.jobs}, {RHYTHM, STRUCTURE, BASS_TONAL})
        self.assertEqual(planned.settled, {DONE: 1})

    def test_what_failed_before_is_held_not_planned(self) -> None:
        rec = recording("a")
        planner = FakePlanner({(rec.object_id, RHYTHM): FAILED})
        planned = self.plan([rec], "eager", planner)
        self.assertNotIn(RHYTHM, {j.unit for j in planned.jobs})
        self.assertEqual([(u, s) for _, u, s, _ in planned.held], [(RHYTHM, FAILED)])
        # Structure needs rhythm, but the runner makes it on its own: planned without a job to wait for.
        self.assertEqual([j.after for j in planned.jobs if j.unit == STRUCTURE], [[]])

    def test_a_routed_unit_is_one_that_needs_nothing_and_only_with_a_spool(self) -> None:
        sched = backfill.schedule_of({"backfill": {"units": [["stems"], ["tonal"]], "extractors": {
            "stems": {"route": "spool"}, "tonal": {"route": "spool"}}}}, CATALOG)
        rec = recording("a")
        routed = {j.unit.extractor: j.routed for j in self.plan([rec], "eager", sched=sched, spool=True).jobs}
        self.assertEqual(routed, {"stems": True, "tonal": False})  # tonal needs frames: it cannot travel
        self.assertFalse(any(j.routed for j in self.plan([rec], "eager", sched=sched).jobs))

    def test_the_projection_counts_the_recorded_costs(self) -> None:
        rec = recording("a", seconds=3600.0)
        sched = schedule((STEMS, RHYTHM))
        planned = self.plan([rec], "eager", sched=sched)
        got = backfill.projection(planned.jobs, sched)
        self.assertEqual(got["audio_hours"], 1.0)
        self.assertAlmostEqual(got["compute_hours_here"], 0.576 + 0.031, places=2)
        self.assertAlmostEqual(got["projected_wall_hours"], (0.576 + 0.031) / 1.9, places=2)


class Real(unittest.TestCase):
    """The planner over a real bank directory: what failed is not retried."""

    def test_a_recorded_failure_holds_the_unit_until_asked(self) -> None:
        root = scratch(self)
        rec = recording("a")
        planner = backfill.Planner(CATALOG, root, {}, {"python": sys.executable, "sh": "/bin/sh"})
        self.assertEqual(planner.state(rec, RHYTHM)[0], TODO)
        self.assertEqual(planner.state(rec, BASS_TONAL)[0], WAITING)
        key = CATALOG.keys(rec.digest, ["rhythm"], {})["rhythm"]
        path = bank.failure_path(root, rec.digest, "rhythm", key)
        path.parent.mkdir(parents=True)
        path.write_text("{}")
        self.assertEqual(planner.state(rec, RHYTHM)[0], FAILED)
        again = backfill.Planner(CATALOG, root, {}, planner.programs, retry_failed=True)
        self.assertEqual(again.state(rec, RHYTHM)[0], TODO)

    def test_an_unconfigured_program_holds_the_unit(self) -> None:
        planner = backfill.Planner(CATALOG, scratch(self), {}, {"python": sys.executable})
        self.assertEqual(planner.state(recording("a"), STEMS)[0], backfill.UNCONFIGURED)

    def test_content_of_a_type_the_extractor_refuses_is_skipped(self) -> None:
        rec = recording("a")
        rec.media_type = "image/png"
        planner = backfill.Planner(CATALOG, scratch(self), {}, {"python": sys.executable, "sh": "/bin/sh"})
        self.assertEqual(planner.state(rec, RHYTHM)[0], backfill.SKIPPED)


class Pool(unittest.TestCase):
    def run_plan(self, recordings, run_unit, planner, sched=None, cores=8, memory=32.0, mode="eager"):
        sched = sched or schedule(poll_seconds=0.01)
        planned = backfill.plan(recordings, sched, planner, mode, shape(sched, cores, memory), False)
        notes = []
        runner = backfill.Runner(planned.jobs, planner, sched, shape(sched, cores, memory), cores, memory, mode,
                                 lambda **e: notes.append(e), run_unit)
        runner.run()
        return planned, notes

    def test_the_pool_holds_to_its_cores_and_dependents_wait_for_stems(self) -> None:
        planner = FakePlanner()
        busy, peak, order, lock = [0], [0], [], threading.Lock()

        def run_unit(job: Job):
            with lock:
                busy[0] += job.threads
                peak[0] = max(peak[0], busy[0])
                order.append((job.recording.object_id, job.unit))
            time.sleep(0.02)
            with lock:
                busy[0] -= job.threads
            planner.made(job.recording, job.unit)
            return {"status": "ok"}, 0.0

        recs = [recording(n) for n in "abcd"]
        planned, _ = self.run_plan(recs, run_unit, planner, cores=4)
        self.assertTrue(all(j.status == "done" for j in planned.jobs))
        self.assertLessEqual(peak[0], 4)
        self.assertGreater(peak[0], 1)
        for rec in recs:
            stems = order.index((rec.object_id, STEMS))
            for unit in (MELODY, BASS_TONAL, BASS_F0):
                self.assertLess(stems, order.index((rec.object_id, unit)))

    def test_a_failed_unit_blocks_what_measures_it_and_the_rest_goes_on(self) -> None:
        planner = FakePlanner()

        def run_unit(job: Job):
            if job.unit == STEMS:
                return {"status": "failed", "detail": "no stems today"}, 0.0
            planner.made(job.recording, job.unit)
            return {"status": "ok"}, 0.0

        planned, notes = self.run_plan([recording("a")], run_unit, planner)
        status = {j.unit: j.status for j in planned.jobs}
        self.assertEqual(status[STEMS], "failed")
        self.assertEqual({status[u] for u in (MELODY, BASS_TONAL, BASS_F0)}, {"blocked"})
        self.assertEqual({status[u] for u in (RHYTHM, STRUCTURE)}, {"done"})
        self.assertIn("no stems today", [n for n in notes if n.get("step") == "failed"][0]["error"])

    def test_a_run_that_answers_ok_but_files_nothing_is_a_failure(self) -> None:
        planned, _ = self.run_plan([recording("a")], lambda job: ({"status": "ok"}, 0.0), FakePlanner(),
                                   sched=schedule((RHYTHM,), poll_seconds=0.01))
        self.assertEqual([j.status for j in planned.jobs], ["failed"])
        self.assertIn("no entry", planned.jobs[0].detail)

    def test_a_run_that_raises_fails_that_job_only(self) -> None:
        planner = FakePlanner()

        def run_unit(job: Job):
            if job.recording.object_id.endswith("a"):
                raise OSError("disk")
            planner.made(job.recording, job.unit)
            return {"status": "ok"}, 0.0

        planned, _ = self.run_plan([recording("a"), recording("b")], run_unit, planner,
                                   sched=schedule((RHYTHM,), poll_seconds=0.01))
        self.assertEqual(sorted(j.status for j in planned.jobs), ["done", "failed"])


class Spool(unittest.TestCase):
    """The fan-out: routed units are put, collected as they arrive, and run
    here when the spool gives them up."""

    def setUp(self) -> None:
        self.planner = FakePlanner()
        self.sched = backfill.schedule_of({"backfill": {"units": [["stems"], ["tonal", "stems/bass"]],
                                                        "extractors": {"stems": {"route": "spool"}},
                                                        "poll_seconds": 0.01}}, CATALOG)
        self.recs = [recording("a"), recording("b")]
        self.ran: list[tuple[str, Unit]] = []

    def run_unit(self, job: Job):
        self.ran.append((job.recording.object_id, job.unit))
        self.planner.made(job.recording, job.unit)
        return {"status": "ok"}, 0.0

    def go(self, collect, mode="eager"):
        planned = backfill.plan(self.recs, self.sched, self.planner, mode, shape(self.sched), True)
        put = lambda catalog, root, spool, extractor, content, media_type, digest, parameters: {
            "status": "put", "task_id": f"task-{digest[7:12]}", "key": "k"}
        with patch.object(backfill.remote, "put", put), patch.object(backfill.remote, "collect", collect):
            runner = backfill.Runner(planned.jobs, self.planner, self.sched, shape(self.sched), 8, 32.0, mode,
                                     lambda **e: None, self.run_unit, spool=object())
            runner.run()
        return planned

    def test_routed_stems_are_collected_and_what_measures_them_runs_after(self) -> None:
        def collect(catalog, root, spool):
            for rec in self.recs:
                self.planner.made(rec, STEMS)
            return {"status": "ok", "tasks": {}, "failed": {}}

        planned = self.go(collect)
        self.assertTrue(all(j.status == "done" for j in planned.jobs))
        self.assertEqual({u for _, u in self.ran}, {BASS_TONAL})  # stems were never run here

    def test_a_task_the_spool_failed_for_good_runs_here(self) -> None:
        def collect(catalog, root, spool):
            return {"status": "failed", "tasks": {}, "failed": {f"task-{r.digest[7:12]}": "exit 65"
                                                                for r in self.recs}}

        planned = self.go(collect)
        self.assertTrue(all(j.status == "done" for j in planned.jobs))
        self.assertEqual(sum(1 for _, u in self.ran if u == STEMS), 2)

    def test_a_put_that_fails_runs_here(self) -> None:
        planned = backfill.plan(self.recs, self.sched, self.planner, "eager", shape(self.sched), True)

        def put(*arguments):
            raise backfill.remote.RemoteError("the spool cannot be reached")

        with patch.object(backfill.remote, "put", put):
            runner = backfill.Runner(planned.jobs, self.planner, self.sched, shape(self.sched), 8, 32.0, "eager",
                                     lambda **e: None, self.run_unit, spool=object())
            runner.run()
        self.assertTrue(all(j.status == "done" for j in planned.jobs))
        self.assertEqual(sum(1 for _, u in self.ran if u == STEMS), 2)

    def test_in_eager_mode_a_task_no_worker_takes_runs_here_after_a_while(self) -> None:
        self.sched.local_when_away_seconds = {"songs": None, "eager": 0.05}
        planned = self.go(lambda catalog, root, spool: {"status": "ok", "tasks": {}, "failed": {}})
        self.assertTrue(all(j.status == "done" for j in planned.jobs))
        self.assertEqual(sum(1 for _, u in self.ran if u == STEMS), 2)


# -- end to end --------------------------------------------------------------------

def stand_in_config(directory: Path, units: list[list[str]] | None = None) -> Path:
    from test_beats_chords import stand_in_programs
    from test_f0 import fake_python as f0_python
    from test_notes import fake_python as basic_pitch_python
    from test_stems import fake_python as demucs_python
    words = stand_in_programs(directory)
    programs = dict(w.split("=", 1) for w in words[1::2])
    programs.update({"demucs-python": str(demucs_python(directory)), "f0-python": str(f0_python(directory)),
                     "basic-pitch-python": str(basic_pitch_python(directory))})
    config = {"programs": programs, "backfill": {"poll_seconds": 0.1}}
    if units is not None:
        config["backfill"]["units"] = units
    path = directory / "config.json"
    path.write_text(json.dumps(config))
    return path


def entries(root: Path) -> list[Path]:
    return sorted(p.parent for p in root.rglob("entry.json") if p.parent.parent.parent.name == "bank")


@needs_essentia
@needs_door
class EndToEnd(unittest.TestCase):
    """A scratch archive with one probed and one unprobed recording, filled
    into a scratch bank by the real runner, proposed through the real Door."""

    def setUp(self) -> None:
        from test_propose import music
        self.tmp = scratch(self)
        self.wav = music(self.tmp / "take.wav")
        self.quiet = support.sine_wav(self.tmp / "quiet.wav", 44100, "2")
        self.root, manifest = support.build_archive(self, {"mix": self.wav, "quiet": self.quiet})
        self.objects = manifest["objects"]
        self.bank = self.tmp / "bank"
        self.config = stand_in_config(self.tmp)
        self.env = {k: v for k, v in support.environment().items() if k != "ARCHIVE_FEATURE_BANK"}

    def arguments(self, *extra: str) -> list[str]:
        return ["backfill", "--archive", str(self.root), "--bank", str(self.bank), "--config", str(self.config),
                "--door", support.DOOR, "--archive-command", support.ARCHIVE, "--ffmpeg", support.FFMPEG,
                "--ffprobe", support.FFPROBE, "--staging", str(self.tmp / "staging"), "--cores", "4",
                "--memory-gb", "8", "--eager", *extra]

    def backfill(self, *extra: str, run_unit=None) -> tuple[int, list[dict]]:
        import enrich
        log = Path(tempfile.mkstemp(dir=self.tmp, suffix=".jsonl")[1])
        extra = (*extra, "--log", str(log))
        out = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), redirect_stdout(out):
            if run_unit is None:
                code = enrich.main(self.arguments(*extra))
            else:
                code = backfill.main(self.parse(*extra), self.env, run_unit=run_unit)
        for line in out.getvalue().splitlines():
            json.loads(line)  # standard output is JSON lines, however long an event
        return code, [json.loads(line) for line in log.read_text().splitlines()]

    def parse(self, *extra: str) -> argparse.Namespace:
        import enrich
        captured = {}
        with patch.object(backfill, "main", lambda args, env=None, run_unit=None: captured.setdefault("a", args)
                          and 0):
            enrich.main(self.arguments(*extra))
        return captured["a"]

    def step(self, events: list[dict], name: str) -> dict:
        return [e for e in events if e["step"] == name][-1]

    def test_a_propose_that_fails_stops_the_backfill_rather_than_finding_nothing_unprobed(self) -> None:
        import enrich
        arguments = self.arguments()
        arguments[arguments.index("--archive-command") + 1] = str(self.tmp / "no-such-archive")
        err = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), redirect_stdout(io.StringIO()), \
                patch("sys.stderr", err):
            code = enrich.main(arguments)
        self.assertEqual(code, 1)
        self.assertIn("which Artifacts are unprobed is not known", json.loads(err.getvalue())["detail"])
        self.assertFalse(entries(self.bank))

    def test_a_dry_run_writes_nothing_and_says_what_it_would_make(self) -> None:
        code, events = self.backfill()
        self.assertEqual(code, 0, events)
        self.assertFalse(entries(self.bank))
        self.assertEqual(self.step(events, "unprobed audio or video Artifacts")["count"], 2)
        plan = self.step(events, "plan")
        self.assertEqual(plan["jobs"], 22)
        self.assertEqual(plan["projection"]["recordings_of_unknown_length"], 2)
        self.assertNotIn("propose --all (commit)", [e["step"] for e in events])

    def test_backfill_probes_fills_the_bank_proposes_and_a_rerun_runs_nothing(self) -> None:
        code, events = self.backfill("--commit")
        self.assertEqual(code, 0, [e for e in events if e.get("ok") is False or e["step"] == "failed"])
        made = entries(self.bank)
        extractors = sorted({p.parent.name for p in made})
        for extractor_id in ("stems", "rhythm", "tonal", "frames", "chords-jiang", "chords-chordino", "structure",
                             "melody", "f0-pesto", "notes-basic-pitch"):
            self.assertIn(extractor_id, extractors)
        self.assertEqual(self.step(events, "propose --all (commit)")["exit"], 0)
        self.assertGreater(self.step(events, "made")["recordings_complete"], 0)
        self.assertEqual(self.step(events, "done")["failures"], 0)

        def bomb(job):
            raise AssertionError(f"a rerun ran {job.unit.name}")

        code, again = self.backfill("--commit", run_unit=bomb)
        self.assertEqual(code, 0, again)
        self.assertEqual(self.step(again, "plan")["jobs"], 0)
        self.assertEqual(self.step(again, "unprobed audio or video Artifacts")["count"], 0)
        self.assertEqual(entries(self.bank), made)

    def test_a_killed_backfill_resumes_and_files_each_entry_once(self) -> None:
        log = self.tmp / "first.jsonl"
        process = subprocess.Popen([sys.executable, str(HERE / "enrich.py"), *self.arguments("--commit", "--log",
                                                                                              str(log))],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.env,
                                   start_new_session=True)
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline and process.poll() is None:
            if log.exists() and log.read_text().count('"step": "ran"') >= 2:
                break
            time.sleep(0.05)
        self.assertIsNone(process.poll(), log.read_text()[-3000:] if log.exists() else "no log")
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        first = entries(self.bank)
        self.assertTrue(first, "nothing was made before the kill")
        code, events = self.backfill("--commit")
        self.assertEqual(code, 0, [e for e in events if e["step"] == "failed"])
        after = entries(self.bank)
        self.assertTrue(set(first) <= set(after))
        # A killed run's scratch under tmp/ is left: operational, never read as an entry.
        ran = [(e["recording"], e["unit"]) for e in events if e["step"] == "ran"]
        self.assertEqual(len(ran), len(set(ran)))
        made_first = [json.loads(line) for line in log.read_text().splitlines() if '"step": "ran"' in line]
        self.assertTrue(made_first)
        self.assertFalse({(e["recording"], e["unit"]) for e in made_first} & set(ran),
                         "the rerun made again what the killed backfill had made")


if __name__ == "__main__":
    unittest.main()
