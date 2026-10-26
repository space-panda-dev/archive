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
from regions import Region
from support import needs_door
from test_cover import needs_essentia

HERE = Path(__file__).resolve().parent
CATALOG = bank.Catalog.load()
STEMS, MELODY, RHYTHM, STRUCTURE = Unit("stems"), Unit("melody"), Unit("rhythm"), Unit("structure")
BASS_TONAL, BASS_F0 = Unit("tonal", "stems/bass"), Unit("f0-pesto", "stems/bass")
VOCAL_F0 = Unit("f0-pesto", "stems/vocals")
SONG = Region(44100 * 60, 44100 * 240, 44100)


def scratch(test: unittest.TestCase) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="backfill-"))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def recording(name: str, songs: bool = False, seconds: float = 600.0, regions: list | None = None) -> Recording:
    """A recording; one with songs has one song region (`SONG`) unless given others."""
    digest = "sha256:" + (name.encode().hex() * 64)[:64]
    found = list(regions if regions is not None else [SONG] if songs else [])
    return Recording(f"urn:uuid:{name}", digest, Path("/nonexistent") / name, seconds, bool(found), "audio/wav",
                     found)


def schedule(units: tuple[Unit, ...] = (STEMS, RHYTHM, STRUCTURE, MELODY, BASS_TONAL, BASS_F0),
             **numbers) -> backfill.Schedule:
    return backfill.schedule_of({"backfill": {"units": [[u.extractor, *([u.source] if u.source else [])]
                                                        for u in units], **numbers}}, CATALOG)


class FakePlanner:
    """States by (recording, unit); a unit run makes it done, and stems make
    what measures them runnable."""

    def __init__(self, states: dict[tuple[str, Unit], str] | None = None, default: str = TODO):
        self.catalog, self.root, self.parameters = CATALOG, Path("/nonexistent"), {}
        self.programs: dict[str, str] = {}
        self.states = dict(states or {})
        self.default = default
        self.lock = threading.Lock()

    def state(self, rec: Recording, unit: Unit, region: Region | None = None) -> tuple[str, str | None]:
        with self.lock:
            found = self.states.get((rec.object_id, unit) if region is None else (rec.object_id, unit, region))
            if found is None:
                stems = self.states.get((rec.object_id, STEMS) if region is None else (rec.object_id, STEMS, region))
                found = WAITING if unit.source and stems != DONE else self.default
            return found, None

    def made(self, rec: Recording, unit: Unit, region: Region | None = None) -> None:
        with self.lock:
            self.states[(rec.object_id, unit) if region is None else (rec.object_id, unit, region)] = DONE


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
        self.assertEqual(got["stems"], {"threads": 2, "cores": 2, "memory_gb": 3.0, "copies": 4})
        self.assertEqual(got["rhythm"]["threads"], 1)
        self.assertEqual(got["rhythm"]["copies"], 8)
        self.assertEqual(got["chords-jiang"]["threads"], 3)
        # Its threads parameter is 3 (in its key), but a copy uses 1.7 cores: reserved 2.
        self.assertEqual((got["chords-jiang"]["cores"], got["chords-jiang"]["copies"]), (2, 4))
        # Configured threads change a copy's width; never wider than the machine.
        wide = backfill.schedule_of({"backfill": {"units": [["stems"]],
                                                  "extractors": {"stems": {"threads": 16}}}}, CATALOG)
        self.assertEqual(backfill.layout(CATALOG, wide, {}, 8, 32.0)["stems"]["threads"], 8)

    def test_a_copy_is_reserved_the_cores_it_uses_not_its_threads(self) -> None:
        # The cost gate: chordino's threads parameter is 3 and it uses one core (CPU time = wall
        # time); basic-pitch is held to one thread and uses about three (CPU time 3.4x wall).
        got = shape(schedule((Unit("chords-chordino"), Unit("notes-basic-pitch", "stems/bass"))), 8, 32.0)
        self.assertEqual(CATALOG.parameters("chords-chordino", {}).get("threads"), 3)  # anchor: unchanged key
        self.assertEqual(got["chords-chordino"], {"threads": 3, "cores": 1, "memory_gb": 1.0, "copies": 8})
        self.assertEqual(got["notes-basic-pitch"], {"threads": 1, "cores": 3, "memory_gb": 1.5, "copies": 2})
        planned = backfill.plan([recording("a")], schedule((STEMS, Unit("chords-chordino"))), FakePlanner(),
                                "eager", shape(schedule((STEMS, Unit("chords-chordino"))), 8, 32.0), False)
        self.assertEqual({j.unit.extractor: j.reserved for j in planned.jobs}, {"stems": 2, "chords-chordino": 1})

    def test_the_costs_are_the_cost_gates_baseline(self) -> None:
        import regions
        sched = schedule()
        for extractor_id in ("stems", "f0-pesto", "chords-jiang", "notes-basic-pitch", "melody", "chords-chordino",
                             "rhythm", "tonal", "structure"):
            self.assertEqual(sched.costs()[extractor_id], regions.PROFILED_COSTS[extractor_id], extractor_id)
        self.assertEqual(sched.costs()["stems"], 1.445)

    def test_every_numeric_library_is_held_to_the_threads(self) -> None:
        environment = backfill.thread_environment(2)
        for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
            self.assertEqual(environment[name], "2")

    def test_the_configuration_refuses_what_it_does_not_know(self) -> None:
        for given in ({"unknown": 1}, {"units": [["no-such-extractor"]]}, {"units": [["tonal", "rhythm/beats"]]},
                      {"extractors": {"stems": {"route": "cloud"}}}, {"extractors": {"stems": {"gpu": True}}},
                      {"extractors": {"stems": {"tier": "now"}}}, {"local_when_away_seconds": None},
                      {"local_when_away_seconds": {"songs": None}}, {"extractors": {"stems": {"transport": "mp3"}}}):
            with self.subTest(given=given), self.assertRaises((backfill.BackfillError, bank.BankError)):
                backfill.schedule_of({"backfill": given}, CATALOG)

    def test_stems_compiled_for_the_cpu_take_one_core_a_copy(self) -> None:
        apply = {**CATALOG.parameters("stems")["apply"], "device": "cpu-inductor"}
        config = {"parameters": {"stems": {"apply": apply}}, "backfill": {"units": [["stems"]]}}
        compiled = backfill.schedule_of(config, CATALOG)
        self.assertEqual({k: compiled.setting("stems")[k] for k in ("threads", "cores", "cost")},
                         backfill.DEVICE_SETTINGS[("stems", "cpu-inductor")])
        self.assertEqual(backfill.layout(CATALOG, compiled, config["parameters"], 8, 64.0)["stems"]["copies"], 8)
        self.assertEqual(schedule().setting("stems")["threads"], 2)  # the eager default is unchanged
        # A configured setting still wins over the device's.
        config["backfill"]["extractors"] = {"stems": {"threads": 2}}
        self.assertEqual(backfill.layout(CATALOG, backfill.schedule_of(config, CATALOG), config["parameters"], 8,
                                         64.0)["stems"]["cores"], 2)

    def test_stems_travel_as_wavpack_unless_configured_otherwise(self) -> None:
        self.assertEqual(schedule().setting("stems")["transport"], "wavpack")
        self.assertIsNone(schedule().setting("tonal")["transport"])
        plain = backfill.schedule_of({"backfill": {"extractors": {"stems": {"transport": None}}}}, CATALOG)
        self.assertIsNone(plain.setting("stems")["transport"])

    def test_expensive_is_the_recorded_cost_above_the_threshold(self) -> None:
        sched = schedule()
        expensive = {e for e in backfill.SETTINGS if sched.expensive(e)}
        self.assertEqual(expensive, {"stems", "f0-pesto", "melody", "chords-jiang", "notes-basic-pitch"})
        self.assertFalse(schedule(expensive_above=2.0).expensive("stems"))


class FakeSteps:
    """`Steps` as Proposals uses it: each proposal's own outcome, and the
    shared failure count."""

    def __init__(self, outcomes=None):
        self.failures, self.calls, self.outcomes = 0, [], list(outcomes or [])
        self.lock = threading.Lock()

    def failed(self) -> None:
        self.failures += 1

    def proposed(self, label: str, artifacts) -> bool:
        self.calls.append((label, artifacts))
        good = self.outcomes.pop(0) if self.outcomes else True
        if not good:
            self.failed()
        return good


def pool_of(jobs):
    """What Proposals reads of the pool: its jobs and its lock."""
    return argparse.Namespace(jobs=jobs, changed=threading.Condition())


class UsableProposal(unittest.TestCase):
    """Each recording is proposed once its fast tier settles; usable when
    all are; a usable proposal that fails makes nothing usable, the next
    good one does."""

    def proposals(self, steps, notes, every=0.0):
        return backfill.Proposals(steps, argparse.Namespace(world_store=None), lambda **e: notes.append(e),
                                  time.monotonic(), every, True)

    def fast(self, rec, unit=RHYTHM):
        return Job(rec, unit, 0, tier="fast", status="running")

    def test_each_recording_is_proposed_when_its_fast_tier_settles_while_the_others_run(self) -> None:
        a, b = recording("a"), recording("b")
        jobs = [self.fast(a), self.fast(a, STRUCTURE), self.fast(b)]
        notes, steps = [], FakeSteps()
        proposals = self.proposals(steps, notes, every=3600.0)
        proposals.runner = pool_of(jobs)
        proposals.start([a, b], jobs)
        for job in jobs[:2]:
            job.status = "done"
            proposals.settled(job)
        proposals.tick()
        proposals.thread.join()
        self.assertEqual(steps.calls, [("propose (usable)", [a.object_id])])  # b's fast tier still runs
        self.assertTrue(proposals.hold_background())
        jobs[2].status = "failed"  # settled, if not made: b is proposed with what it has
        proposals.settled(jobs[2])
        proposals.finish()
        self.assertEqual(steps.calls[1], ("propose (usable)", [b.object_id]))
        self.assertEqual([n["step"] for n in notes], ["usable"])
        self.assertIsNotNone(proposals.usable_s)
        self.assertFalse(proposals.hold_background())

    def test_many_recordings_waiting_are_proposed_with_one_all(self) -> None:
        recs = [recording(n) for n in "abc"]
        notes, steps = [], FakeSteps()
        proposals = self.proposals(steps, notes)
        proposals.runner = pool_of([])
        proposals.start(recs, [])  # their fast tier is in the bank already
        proposals.finish()
        self.assertEqual(steps.calls, [("propose (usable, all)", None)])
        self.assertEqual(notes[-1]["step"], "usable")

    def test_a_failed_usable_proposal_is_not_usable_and_the_next_good_one_is(self) -> None:
        notes, steps = [], FakeSteps([False])
        proposals = self.proposals(steps, notes)
        proposals.runner = pool_of([])
        proposals.start([recording("a")], [])
        proposals.finish()
        self.assertEqual([n["step"] for n in notes], ["usable proposal failed"])
        self.assertIsNone(proposals.usable_s)
        self.assertFalse(proposals.hold_background())  # the background is not held for ever
        proposals.tick()  # the next proposal, at the background interval, though no background unit landed
        proposals.finish()
        self.assertEqual([c[0] for c in steps.calls], ["propose (usable)", "propose --all (background batch)"])
        self.assertEqual([n["step"] for n in notes], ["usable proposal failed", "usable"])
        self.assertIsNotNone(proposals.usable_s)
        self.assertFalse(proposals.usable_pending)

    def test_a_failure_beside_the_proposal_is_not_the_proposals(self) -> None:
        # A --follow rescan failing while a proposal runs used to be counted as the proposal failing.
        class Beside(FakeSteps):
            def proposed(self, label, artifacts):
                self.failed()  # another step, on another thread, failed meanwhile
                return super().proposed(label, artifacts)

        notes, steps = [], Beside()
        proposals = self.proposals(steps, notes)
        proposals.runner = pool_of([])
        proposals.start([recording("a")], [])
        proposals.finish()
        self.assertEqual([n["step"] for n in notes], ["usable"])
        self.assertEqual(steps.failures, 1)  # still counted for the exit status

    def test_a_usable_proposal_that_cannot_run_is_a_failure_not_usable(self) -> None:
        class Broken(FakeSteps):
            def proposed(self, label, artifacts):
                raise OSError("no such program")

        notes, steps = [], Broken()
        proposals = self.proposals(steps, notes)
        proposals.runner = pool_of([])
        proposals.start([recording("a")], [])
        proposals.finish()
        self.assertEqual([n["step"] for n in notes], ["proposal could not run", "usable proposal failed"])
        self.assertEqual((steps.failures, proposals.usable_s, proposals.usable_pending), (1, None, True))


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

    def test_explicit_feature_only_units_do_not_expand_to_default_proposer_needs(self) -> None:
        sched = backfill.schedule_of({"backfill": {"units": [["tonal"]]}}, CATALOG)
        planned = self.plan([recording("a")], "eager", sched=sched)
        self.assertEqual([job.unit for job in planned.jobs], [Unit("tonal")])
        self.assertEqual(planned.jobs[0].after, [])
        self.assertNotIn(Unit("stems"), [job.unit for job in planned.jobs])

    def test_a_copy_that_grows_with_its_audio_reserves_memory_by_the_hour(self) -> None:
        jiang = Unit("chords-jiang")
        self.assertEqual(backfill.SETTINGS["chords-jiang"]["memory_gb_per_hour"], 0.8)
        long_, short = recording("long", seconds=5400.0), recording("short", seconds=600.0, songs=True)
        sched = schedule((jiang, RHYTHM))
        jobs = {(j.recording.object_id, j.unit): j for j in self.plan([long_, short], "eager", sched=sched).jobs}
        self.assertAlmostEqual(jobs[(long_.object_id, jiang)].memory_gb, 2.0 + 0.8 * 1.5)
        self.assertAlmostEqual(jobs[(short.object_id, jiang)].memory_gb, 2.0 + 0.8 / 6, places=2)
        self.assertEqual(jobs[(long_.object_id, RHYTHM)].memory_gb, 1.0)  # no hourly term
        given = schedule((jiang, RHYTHM), extractors={"rhythm": {"memory_gb_per_hour": 2.0}})
        jobs = {(j.recording.object_id, j.unit): j for j in self.plan([long_], "eager", sched=given).jobs}
        self.assertEqual(jobs[(long_.object_id, RHYTHM)].memory_gb, 1.0 + 2.0 * 1.5)

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

    def test_the_projection_counts_the_recorded_costs_to_usable_and_to_done(self) -> None:
        recs = [recording(n, seconds=3600.0) for n in "abcdefgh"]
        sched = schedule((STEMS, RHYTHM))
        planned = self.plan(recs, "eager", sched=sched)
        got = backfill.projection(planned.jobs, sched, 8)
        self.assertEqual(got["audio_hours"], 8.0)
        load = sched.load_factor
        self.assertAlmostEqual(got["compute_hours_here"], 8 * (1.445 + 0.032) * load, places=2)
        # Usable: the fast tier (rhythm) over eight cores, then the last recording's proposal;
        # done: then stems over them.
        proposal = sched.propose_seconds_per_recording
        self.assertAlmostEqual(got["time_to_usable_hours"], 0.032 * load + proposal / 3600, places=3)
        self.assertAlmostEqual(got["time_to_done_hours"], (0.032 + 1.445) * load + proposal / 3600, places=2)
        # Proposals slower than the fast pass bound it.
        slow = backfill.projection(planned.jobs, schedule((STEMS, RHYTHM), propose_seconds_per_recording=600.0), 8)
        self.assertAlmostEqual(slow["time_to_usable_hours"], 9 * 600 / 3600, places=3)
        # With the worker taking stems too, the laptop takes what it is not holding: sooner than either alone.
        for job in planned.jobs:
            job.routed = job.unit == STEMS
        both = backfill.projection(planned.jobs, sched, 8)
        alone = 8 * sched.setting("stems")["remote_cost"]
        self.assertLess(both["time_to_done_hours"] - both["time_to_usable_hours"], min(alone, 1.445 * load))
        self.assertGreater(both["time_to_done_hours"], got["time_to_usable_hours"])

    def test_songs_makes_expensive_units_over_each_song_region_and_cheap_ones_whole(self) -> None:
        second = Region(44100 * 400, 44100 * 500, 44100)
        song = recording("a", regions=[SONG, second])
        self.assertNotEqual(SONG, second)  # anchor: two regions, so two jobs each
        planned = self.plan([song], "songs")
        made = {}
        for job in planned.jobs:
            made.setdefault(job.unit, []).append(job.region)
        for unit in (STEMS, MELODY, BASS_TONAL, BASS_F0):  # expensive, or measuring what is made over regions
            self.assertEqual(made[unit], [SONG, second], unit)
        self.assertEqual(made[RHYTHM], [None])
        self.assertEqual(made[STRUCTURE], [None])
        jobs = {(j.unit, j.region): j for j in planned.jobs}
        self.assertEqual(jobs[(BASS_F0, second)].after, [jobs[(STEMS, second)]])  # the same region's stems
        self.assertFalse(any(j.routed for j in planned.jobs if j.region))
        self.assertEqual(planned.jobs[0].seconds, 180.0)  # a region job measures its region

    def test_a_song_region_that_is_the_whole_recording_is_made_whole_and_may_be_routed(self) -> None:
        # A recording selected end to end: one region [0, length). Made as region
        # entries, no reader of whole entries would find it and no worker could take it.
        rec = recording("a", regions=[Region(0, 44100 * 600, 44100)])
        rec.length = 44100 * 600
        sched = schedule()
        sched.settings["stems"]["route"] = "spool"
        planned = self.plan([rec], "songs", sched=sched, spool=True)
        self.assertEqual({j.region for j in planned.jobs}, {None})
        self.assertTrue(any(j.routed for j in planned.jobs if j.unit == STEMS))
        rec.length = 44100 * 601  # anchor: a region short of the end stays a region
        self.assertIn(rec.regions[0], {j.region for j in self.plan([rec], "songs").jobs})

    def test_eager_makes_everything_whole_even_with_song_regions(self) -> None:
        planned = self.plan([recording("a", songs=True)], "eager")
        self.assertEqual({j.region for j in planned.jobs}, {None})

    def test_melody_waits_for_the_measure_its_probe_reads_whatever_the_configured_order(self) -> None:
        rec = recording("a")
        sched = schedule((STEMS, MELODY, VOCAL_F0))
        self.assertEqual(backfill.unit_order(sched.units, CATALOG), [STEMS, VOCAL_F0, MELODY])
        planned = self.plan([rec], "eager", sched=sched)
        jobs = {j.unit: j for j in planned.jobs}
        self.assertEqual(set(jobs[MELODY].after), {jobs[STEMS], jobs[VOCAL_F0]})
        self.assertEqual(backfill.UNITS.index(("f0-pesto", "stems/vocals")) < backfill.UNITS.index(("melody",)),
                         True)

    def test_a_cheap_unit_on_stems_made_whole_runs_whole_and_an_expensive_one_waits_for_a_song(self) -> None:
        song = recording("a", songs=True)
        planner = FakePlanner({(song.object_id, STEMS): DONE})
        planned = self.plan([song], "songs", planner)
        made = {(j.unit, j.region) for j in planned.jobs}
        self.assertIn((BASS_TONAL, None), made)
        self.assertIn((BASS_F0, None), made)  # a song recording: its stems are whole, so measured whole

    def test_on_demand_plans_only_requests_and_a_request_asks_for_the_feature_it_measures(self) -> None:
        rec, other = recording("a"), recording("b")
        f0 = CATALOG["f0-pesto"]
        asked = [{"key": "sha256:" + "1" * 64, "content": rec.digest, "region": SONG.as_json(),
                  "feature": "stems/vocals", "extractor": {"id": "f0-pesto", "version": f0.version}},
                 {"key": "sha256:" + "2" * 64, "content": other.digest, "region": None, "feature": None,
                  "extractor": {"id": "rhythm", "version": "0"}},
                 {"key": "sha256:" + "3" * 64, "content": "sha256:" + "f" * 64, "region": None, "feature": None,
                  "extractor": {"id": "rhythm", "version": CATALOG["rhythm"].version}}]
        asked[0]["key"] = backfill.request_key(FakePlanner(), asked[0])  # the key this configuration makes
        sched = schedule()
        planned = backfill.plan([rec, other], sched, FakePlanner(), "on-demand", shape(sched), False, asked)
        self.assertEqual([(j.unit, j.region, j.requested) for j in planned.jobs],
                         [(STEMS, SONG, True), (VOCAL_F0, SONG, True)])
        self.assertEqual(planned.jobs[1].after, [planned.jobs[0]])
        self.assertEqual([k[-1] for k, _ in planned.unplaced], ["2", "3"])
        self.assertTrue(all("on-demand" in why for _, _, why in planned.deferred))
        # In songs mode the request is served first, and not planned twice.
        song = recording("a", songs=True)
        planned = backfill.plan([song], sched, FakePlanner(), "songs", shape(sched), False, asked[:1])
        self.assertEqual(planned.jobs[0].unit, STEMS)
        self.assertTrue(planned.jobs[0].requested)
        self.assertEqual(sum(1 for j in planned.jobs if (j.unit, j.region) == (STEMS, SONG)), 1)


class Tiers(unittest.TestCase):
    """ADR 0112, "Usable": the fast tier over every recording first, then the
    background by priority, a request ahead of both."""

    def test_the_fast_tier_is_what_measures_the_content_itself_cheaply(self) -> None:
        sched = schedule()
        fast = {u.name for u in map(lambda i: Unit(*i), backfill.UNITS) if sched.tier(u, CATALOG) == "fast"}
        self.assertEqual(fast, {"rhythm", "tonal", "chords-jiang", "chords-chordino", "structure"})
        self.assertEqual(sched.tier(Unit("tonal", "stems/bass"), CATALOG), "background")  # cheap, but a stem
        self.assertEqual(sched.tier(MELODY, CATALOG), "background")  # it prefers the vocal stem
        cheaper = schedule(fast_above=0.1)
        self.assertEqual(cheaper.tier(Unit("chords-jiang"), CATALOG), "background")
        told = backfill.schedule_of({"backfill": {"extractors": {"chords-jiang": {"tier": "background"}}}}, CATALOG)
        self.assertEqual(told.tier(Unit("chords-jiang"), CATALOG), "background")

    def test_recordings_are_queued_songs_first_then_the_most_recent(self) -> None:
        old, new, song, unknown = recording("old"), recording("new"), recording("song", songs=True), recording("u")
        old.at, new.at, song.at = "2001-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "1999-01-01T00:00:00Z"
        backfill.prioritise([old, unknown, new, song])
        self.assertEqual(sorted([old, new, song, unknown], key=lambda r: r.priority), [song, new, old, unknown])

    def run_plan(self, recs, planner, units=(STEMS, RHYTHM, STRUCTURE, BASS_TONAL), cores=2, asked=None,
                 intake=None, hold=None):
        sched = schedule(units, poll_seconds=0.01)
        shaped = shape(sched, cores, 32.0)
        planned = backfill.plan(recs, sched, planner, "eager", shaped, False, asked)
        order, lock = [], threading.Lock()

        def run_unit(job: Job):
            with lock:
                order.append(("start", job.recording.object_id, job.unit, job.rank))
            time.sleep(0.01)
            planner.made(job.recording, job.unit, job.region)
            with lock:
                order.append(("end", job.recording.object_id, job.unit, job.rank))
            return {"status": "ok"}, 0.0

        stops = [False]
        runner = backfill.Runner(planned.jobs, planner, sched, shaped, cores, 32.0, "eager", lambda **e: None,
                                 run_unit, intake=intake and (lambda: intake(planned)),
                                 stop=(lambda: stops[0]) if intake else None, hold_background=hold)
        if intake:
            threading.Timer(1.5, lambda: stops.__setitem__(0, True)).start()
        runner.run()
        return planned, order

    def test_every_recording_has_its_fast_units_made_before_any_background_job_starts(self) -> None:
        recs = [recording(n) for n in "abcdef"]
        planned, order = self.run_plan(recs, FakePlanner())
        self.assertTrue(all(j.status == "done" for j in planned.jobs))
        first_background = min(i for i, e in enumerate(order) if e[0] == "start" and e[3] == 2)
        last_fast = max(i for i, e in enumerate(order) if e[0] == "end" and e[3] == 1)
        self.assertLess(last_fast, first_background)
        self.assertEqual({e[1] for e in order[:first_background] if e[2] == RHYTHM}, {r.object_id for r in recs})

    def test_the_background_waits_while_the_fast_tiers_proposal_is_outstanding(self) -> None:
        released = [False]
        threading.Timer(0.3, lambda: released.__setitem__(0, True)).start()
        began = time.monotonic()
        seen = []

        def hold() -> bool:
            if not released[0]:
                return True
            seen.append(time.monotonic() - began)
            return False

        planned, order = self.run_plan([recording("a")], FakePlanner(), hold=hold)
        self.assertTrue(all(j.status == "done" for j in planned.jobs))
        self.assertGreaterEqual(seen[0], 0.3)

    def test_a_request_goes_ahead_of_the_background_and_of_the_fast_tier(self) -> None:
        recs = [recording(n) for n in "abcd"]
        request = {"key": None, "content": recs[3].digest, "region": None, "feature": None,
                   "extractor": {"id": "stems", "version": CATALOG["stems"].version}}
        request["key"] = backfill.request_key(FakePlanner(), request)
        planned, order = self.run_plan(recs, FakePlanner(), asked=[request], cores=2)
        starts = [e for e in order if e[0] == "start"]
        self.assertEqual(starts[0][1:3], (recs[3].object_id, STEMS))
        self.assertEqual([j.requests for j in planned.jobs if j.requested], [[request["key"]]])

    def test_a_request_that_arrives_while_running_jumps_the_background_queue(self) -> None:
        recs = [recording(n) for n in "abcdef"]
        planner = FakePlanner()
        last = recs[-1]
        asked = []

        def intake(planned):
            started = [j for j in planned.jobs if j.unit == STEMS and j.status != "pending"]
            if started and not asked:
                request = {"key": None, "content": last.digest, "region": None, "feature": "stems/bass",
                           "extractor": {"id": "tonal", "version": CATALOG["tonal"].version}}
                request["key"] = backfill.request_key(planner, request)
                asked.append(request)
                return planned.planning.request(request, {r.digest: r for r in recs})
            return []

        planned, order = self.run_plan(recs, planner, intake=intake, cores=2)
        self.assertTrue(asked)
        self.assertTrue(all(j.status == "done" for j in planned.jobs), [(j.unit, j.status) for j in planned.jobs])
        starts = [(e[1], e[2]) for e in order if e[0] == "start" and e[2] == STEMS]
        # Its stems were promoted ahead of the recordings queued before it.
        self.assertLess(starts.index((last.object_id, STEMS)), len(recs) - 1)
        self.assertTrue(any(j.requested and j.unit == BASS_TONAL for j in planned.jobs))

    def test_a_request_whose_key_this_configuration_does_not_make_is_kept_and_not_made(self) -> None:
        rec = recording("a")
        request = {"key": "sha256:" + "4" * 64, "content": rec.digest, "region": None, "feature": None,
                   "extractor": {"id": "rhythm", "version": CATALOG["rhythm"].version}}
        sched = schedule()
        planned = backfill.plan([rec], sched, FakePlanner(), "on-demand", shape(sched), False, [request])
        self.assertEqual(planned.jobs, [])
        self.assertIn("bank_parameters", planned.unplaced[0][1])
        made = FakePlanner({(rec.object_id, RHYTHM): DONE})
        request["key"] = backfill.request_key(made, request)
        planned = backfill.plan([rec], sched, made, "on-demand", shape(sched), False, [request])
        self.assertEqual((planned.jobs, planned.answered), ([], [request["key"]]))


class LaterSources(unittest.TestCase):
    """ADR 0070, amendment on twins: the background analyses each take once,
    so a part of a session's later source is left out."""
    ARCHIVE = "urn:uuid:00000000-0000-4000-8000-000000000001"

    def artifact(self, object_id: str, hexdigest: str) -> dict:
        return {"object_id": object_id, "revision_id": "r-" + object_id, "shape": "artifact",
                "payload": {"content": {"digest": "sha256:" + hexdigest, "size": 1}}, "refs": []}

    def parts(self, object_id: str, session: str, *parts: str) -> dict:
        return {"object_id": object_id, "revision_id": "r-" + object_id, "shape": "claim",
                "contracts": [{"id": backfill.PARTS, "version": "1"}], "payload": {},
                "refs": [{"role": "session", "id": session}] + [{"role": "part", "id": p} for p in parts]}

    def test_parts_of_a_later_source_are_left_out_and_the_first_sources_kept(self) -> None:
        import adapter
        mix, mic, alone = "a" * 64, "b" * 64, "c" * 64
        session = adapter.object_id(self.ARCHIVE, backfill.SESSION_NAMESPACE, "session-" + mix)
        records = [self.artifact("mix", mix), self.artifact("mic", mic), self.artifact("alone", alone),
                   self.parts("p1", session, "mix"), self.parts("p2", session, "mic")]
        self.assertEqual(backfill.later_sources({"archive_id": self.ARCHIVE, "records": records}), {"mic"})


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
            planner.made(job.recording, job.unit, job.region)
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
            planner.made(job.recording, job.unit, job.region)
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
            planner.made(job.recording, job.unit, job.region)
            return {"status": "ok"}, 0.0

        planned, _ = self.run_plan([recording("a"), recording("b")], run_unit, planner,
                                   sched=schedule((RHYTHM,), poll_seconds=0.01))
        self.assertEqual(sorted(j.status for j in planned.jobs), ["done", "failed"])


ALL_UNITS = tuple(Unit(*u) for u in backfill.UNITS)


def job(name: str, unit: Unit, seconds: float, memory: float, priority: int, cores: int = 1, **fields) -> Job:
    rec = recording(name, seconds=seconds)
    rec.priority = priority
    return Job(rec, unit, priority, threads=cores, memory_gb=memory, cores=cores, **fields)


class Backfilling(unittest.TestCase):
    """ADR 0112, round 3: a job that cannot start for memory no longer holds
    every job behind it; it is reserved for once it has waited long enough;
    and free cores take routed work from the back of the worker's queue."""

    def runner(self, jobs, cores=8, memory=12.0, spool=None, **numbers):
        sched = schedule(ALL_UNITS, poll_seconds=0.01, **numbers)
        return backfill.Runner(jobs, FakePlanner(), sched, shape(sched, cores, memory), cores, memory, "eager",
                               lambda **e: None, lambda j: ({"status": "ok"}, 0.0), spool=spool)

    def running(self, runner, job, now):
        job.status, job.since = "running", now
        runner.free_cores -= job.reserved
        runner.free_memory -= job.memory_gb
        runner.copies[job.unit.extractor] = runner.copies.get(job.unit.extractor, 0) + 1

    def test_a_memory_blocked_head_lets_what_ends_before_it_or_fits_beside_it_start(self) -> None:
        # The measured case: a long recording's Jiang waits for memory two f0-pesto copies hold.
        f0 = [job(f"f{i}", BASS_F0, 3600.0, 3.0, i) for i in range(2)]
        head = job("long", Unit("chords-jiang"), 5580.0, 10.0, 2, cores=2)
        short = job("short", RHYTHM, 600.0, 1.0, 3)  # 22 s: ends long before the f0 copies
        wide = job("wide", STEMS, 3600.0, 3.0, 4, cores=2)  # long, and more memory than Jiang leaves
        narrow = job("narrow", MELODY, 3600.0, 1.0, 5)  # long, but within what Jiang leaves (2 GB)
        runner = self.runner([*f0, head, short, wide, narrow])
        now = time.monotonic()
        for j in f0:
            self.running(runner, j, now)
        chosen = runner.choose(runner.ready(), now)
        self.assertEqual(chosen, [short, narrow])
        self.assertIs(runner.head, head)
        # The f0 copies end by their recorded cost: then Jiang fits, with 2 GB and 6 cores left over.
        when, cores, memory = runner.shadow(head, now, runner.free_cores, runner.free_memory, [])
        self.assertAlmostEqual(when - now, 0.186 * 3600 * 1.15, places=3)
        self.assertEqual((cores, memory), (6, 2.0))
        # Once it has waited long enough, it is reserved for: nothing new starts.
        runner.head_since = now - runner.schedule.reserve_after_seconds
        self.assertEqual(runner.choose(runner.ready(), now), [])

    def test_a_simulated_pool_keeps_its_cores_busy_while_the_head_waits_and_the_head_still_runs(self) -> None:
        # Jobs sleep their recorded cost, scaled down: one long job holds memory the head needs, and
        # short jobs behind the head used to wait for it; now they run beside the long one.
        long = job("long", BASS_F0, 0.6 / (0.186 * 1.15), 5.0, 0)
        head = job("head", Unit("chords-jiang"), 0.2 * 2 / (0.151 * 1.15), 6.0, 1, cores=2)
        shorts = [job(f"s{i}", RHYTHM, 0.1 / (0.032 * 1.15), 1.0, 2 + i) for i in range(6)]
        for index, j in enumerate([long, head, *shorts]):
            j.order = index
        events, lock = [], threading.Lock()
        runner = self.runner([long, head, *shorts], cores=4, memory=8.0)

        def run_unit(j: Job):
            with lock:
                events.append(("start", j, time.monotonic()))
            time.sleep(runner.expected_wall(j))
            runner.planner.made(j.recording, j.unit)
            with lock:
                events.append(("end", j, time.monotonic()))
            return {"status": "ok"}, 0.0

        runner.run_unit = run_unit
        runner.run()
        self.assertTrue(all(j.status == "done" for j in runner.jobs))
        at = {(kind, j.recording.object_id): t for kind, j, t in events}
        head_start, long_end = at[("start", head.recording.object_id)], at[("end", long.recording.object_id)]
        self.assertGreaterEqual(head_start, long_end - 0.01)  # it waited for the memory
        for j in shorts:  # and every short job ran while it waited, not after it
            self.assertLess(at[("start", j.recording.object_id)], head_start)
        self.assertLess(head_start - at[("start", long.recording.object_id)], 1.0)  # not starved
        self.assertEqual(runner.backfilled, len(shorts))

    def test_reserving_at_once_is_the_old_head_of_line_order(self) -> None:
        f0 = job("f", BASS_F0, 3600.0, 6.0, 0)
        head = job("long", Unit("chords-jiang"), 5580.0, 10.0, 1, cores=2)
        short = job("short", RHYTHM, 600.0, 1.0, 2)
        runner = self.runner([f0, head, short], reserve_after_seconds=0.0)
        now = time.monotonic()
        self.running(runner, f0, now)
        self.assertEqual(runner.choose(runner.ready(), now), [])

    def test_free_cores_take_routed_stems_from_the_back_of_the_workers_queue(self) -> None:
        # Here a 600 s recording's stems take 498 s on two cores; the worker 134 s each, one at a time.
        routed = [job(f"r{i}", STEMS, 600.0, 3.0, i, cores=2, routed=True) for i in range(4)]
        local = job("local", RHYTHM, 600.0, 1.0, 9)
        runner = self.runner([*routed, local], memory=32.0, spool=object(), spool_in_flight=1)
        now = time.monotonic()
        routed[0].status, routed[0].since = "put", now
        chosen = runner.choose(runner.ready(), now)
        # r1 would start on the worker in 134 s and end in 268, r2 end in 402: both sooner there;
        # r3 would end there in 536 s, later than here; the local job runs too.
        self.assertEqual(chosen, [routed[3], local])
        runner.schedule.overflow = False
        self.assertEqual(runner.choose(runner.ready(), now), [local])
        runner.schedule.overflow = True
        runner.put_paused_until = now + 60  # the worker is away: everything runs here
        self.assertEqual(runner.choose(runner.ready(), now)[:3], routed[1:])


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
        self.planner.made(job.recording, job.unit, job.region)
        return {"status": "ok"}, 0.0

    def go(self, collect, mode="eager"):
        planned = backfill.plan(self.recs, self.sched, self.planner, mode, shape(self.sched), True)
        self.put_options: list[dict] = []
        put = lambda catalog, root, spool, extractor, content, media_type, digest, parameters, **options: (
            self.put_options.append(options) or {"status": "put", "task_id": f"task-{digest[7:12]}", "key": "k"})
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
        # Their content travels as WavPack, the stems default, encoded by the configured ffmpeg.
        self.assertEqual(self.put_options, [{"transport": "wavpack", "ffmpeg": None}] * 2)

    def test_routed_background_work_is_put_while_the_fast_tier_still_runs(self) -> None:
        # The worker starts at once: tonight's run put its first stems only after the usable proposal.
        self.sched = backfill.schedule_of({"backfill": {"units": [["stems"], ["rhythm"]],
                                                        "extractors": {"stems": {"route": "spool"}},
                                                        "poll_seconds": 0.01}}, CATALOG)
        planned = backfill.plan(self.recs, self.sched, self.planner, "eager", shape(self.sched), True)
        put_done = threading.Event()

        def put(*arguments, **options):
            put_done.set()
            return {"status": "put", "task_id": f"task-{arguments[6][7:12]}", "key": "k"}

        runner = backfill.Runner(planned.jobs, self.planner, self.sched, shape(self.sched), 8, 32.0, "eager",
                                 lambda **e: None, self.run_unit, spool=object(), hold_background=lambda: True)
        with patch.object(backfill.remote, "put", put):
            with runner.changed:
                self.assertFalse(runner.background_open())  # rhythm is still to run, and the proposal held
                runner.put_window()
                window = [j for j in planned.jobs if j.status == "putting"]
            self.assertTrue(put_done.wait(10))
            # Every put of the window ends inside the patch: a put left running would reach the next test's.
            with runner.changed:
                self.assertTrue(runner.changed.wait_for(lambda: all(j.status != "putting" for j in window), 10))
        self.assertTrue(any(j.unit == STEMS and j.status == "put" for j in planned.jobs))
        self.assertTrue(all(j.status == "pending" for j in planned.jobs if j.unit == RHYTHM))

    def test_what_runs_here_does_not_wait_for_the_puts(self) -> None:
        # A put encodes its transport first; rhythm, which needs no stems, runs meanwhile.
        self.sched = backfill.schedule_of({"backfill": {"units": [["stems"], ["rhythm"]],
                                                        "extractors": {"stems": {"route": "spool"}},
                                                        "poll_seconds": 0.01}}, CATALOG)
        rhythm_ran = threading.Event()
        planned = backfill.plan(self.recs, self.sched, self.planner, "eager", shape(self.sched), True)

        def put(*arguments, **options):
            self.assertTrue(rhythm_ran.wait(10), "the pool waited for the put")
            return {"status": "put", "task_id": f"task-{arguments[6][7:12]}", "key": "k"}

        def collect(catalog, root, spool):
            for rec in self.recs:
                self.planner.made(rec, STEMS)
            return {"status": "ok", "tasks": {}, "failed": {}}

        def run_unit(job: Job):
            result = self.run_unit(job)
            if job.unit == RHYTHM:
                rhythm_ran.set()
            return result

        with patch.object(backfill.remote, "put", put), patch.object(backfill.remote, "collect", collect):
            backfill.Runner(planned.jobs, self.planner, self.sched, shape(self.sched), 8, 32.0, "eager",
                            lambda **e: None, run_unit, spool=object()).run()
        self.assertTrue(all(j.status == "done" for j in planned.jobs))
        self.assertEqual({u for _, u in self.ran}, {RHYTHM})

    def test_a_task_the_spool_failed_for_good_runs_here(self) -> None:
        def collect(catalog, root, spool):
            return {"status": "failed", "tasks": {}, "failed": {f"task-{r.digest[7:12]}": "exit 65"
                                                                for r in self.recs}}

        planned = self.go(collect)
        self.assertTrue(all(j.status == "done" for j in planned.jobs))
        self.assertEqual(sum(1 for _, u in self.ran if u == STEMS), 2)

    def test_a_put_that_fails_runs_here(self) -> None:
        planned = backfill.plan(self.recs, self.sched, self.planner, "eager", shape(self.sched), True)

        def put(*arguments, **options):
            raise backfill.remote.RemoteError("the spool cannot be reached")

        with patch.object(backfill.remote, "put", put):
            runner = backfill.Runner(planned.jobs, self.planner, self.sched, shape(self.sched), 8, 32.0, "eager",
                                     lambda **e: None, self.run_unit, spool=object())
            runner.run()
        self.assertTrue(all(j.status == "done" for j in planned.jobs))
        self.assertEqual(sum(1 for _, u in self.ran if u == STEMS), 2)

    def test_in_eager_mode_a_task_no_worker_takes_runs_here_after_a_while(self) -> None:
        self.sched.local_when_away_seconds = {"songs": 1800.0, "eager": 0.05, "on-demand": 600.0}
        self.sched.settings["stems"]["remote_cost"] = None  # no allowance for the task's own time
        planned = self.go(lambda catalog, root, spool: {"status": "ok", "tasks": {}, "failed": {}})
        self.assertTrue(all(j.status == "done" for j in planned.jobs))
        self.assertEqual(sum(1 for _, u in self.ran if u == STEMS), 2)


class SimSpool:
    """A spool and its workers in miniature, read as `remote.Spool.tasks`
    reads one: tasks leased in task-id order (as Spool does, whatever the
    put order), each worker one task at a time, renewing while it works.
    A worker given `None` seconds never comes: an absent one."""

    def __init__(self, test: unittest.TestCase, planner: FakePlanner, recs: list[Recording],
                 workers: list[float | None]):
        self.planner, self.recs, self.workers = planner, recs, workers
        self.pending: set[str] = set()
        self.leased: dict[str, tuple[float, str]] = {}
        self.by_task: dict[str, Recording] = {}
        self.made: list[tuple[str, str]] = []  # (task, worker)
        self.puts: list[str] = []
        self.lock, self.stopped = threading.Lock(), threading.Event()
        test.addCleanup(self.stopped.set)

    def start(self) -> None:
        for index, seconds in enumerate(self.workers):
            if seconds is not None:
                threading.Thread(target=self.worker, args=(f"w{index}", seconds), daemon=True).start()

    def put(self, catalog, root, spool, extractor, content, media_type, digest, parameters, **options):
        task = f"task-{digest[7:12]}"
        with self.lock:
            self.by_task[task] = next(r for r in self.recs if r.digest == digest)
            self.pending.add(task)
            self.puts.append(task)
        return {"status": "put", "task_id": task, "key": "k"}

    def collect(self, catalog, root, spool):
        return {"status": "ok", "tasks": {}, "failed": {}}

    def tasks(self) -> dict:
        with self.lock:
            view = {t: {"state": "pending"} for t in self.pending}
            view.update({t: {"state": "leased", "leased_at": at, "renewed_at": time.time(), "worker": who}
                         for t, (at, who) in self.leased.items()})
        return view

    def worker(self, name: str, seconds: float) -> None:
        while not self.stopped.is_set():
            with self.lock:
                task = min(self.pending) if self.pending else None
                if task is not None:
                    self.pending.discard(task)
                    self.leased[task] = (time.time(), name)
            if task is None:
                self.stopped.wait(0.005)
                continue
            self.stopped.wait(seconds)
            with self.lock:
                self.planner.made(self.by_task[task], STEMS)
                del self.leased[task]
                self.made.append((task, name))


class SimulatedPool(unittest.TestCase):
    """ADR 0112, the Mac's path: the backfill reads what the spool holds, so
    a worker holding a lease is never given up, the absent clock counts from
    when the worker could have started, a given-up task the worker takes
    after all is not made here too, and nothing made since the plan is put."""

    def setUp(self) -> None:
        self.planner = FakePlanner()
        self.sched = backfill.schedule_of({"backfill": {
            "units": [["stems"], ["tonal", "stems/bass"]], "extractors": {"stems": {"route": "spool"}},
            "poll_seconds": 0.01, "overflow": False,
            "local_when_away_seconds": {"songs": 0.15, "eager": 0.15, "on-demand": 0.15}}}, CATALOG)
        self.sched.settings["stems"]["remote_cost"] = None  # no allowance: only the clock's own seconds
        self.ran: list[tuple[str, Unit]] = []
        self.notes: list[dict] = []

    def run_unit(self, job: Job):
        self.ran.append((job.recording.object_id, job.unit))
        self.planner.made(job.recording, job.unit, job.region)
        return {"status": "ok"}, 0.0

    def go(self, recs, workers, in_flight=2):
        self.sched.spool_in_flight = in_flight
        sim = SimSpool(self, self.planner, recs, workers)
        planned = backfill.plan(recs, self.sched, self.planner, "eager", shape(self.sched), True)
        with patch.object(backfill.remote, "put", sim.put), patch.object(backfill.remote, "collect", sim.collect):
            runner = backfill.Runner(planned.jobs, self.planner, self.sched, shape(self.sched), 8, 32.0, "eager",
                                     lambda **e: self.notes.append(e), self.run_unit, spool=sim)
            sim.start()
            runner.run()
        self.assertTrue(all(j.status == "done" for j in planned.jobs))
        stems_here = {rec for rec, unit in self.ran if unit == STEMS}
        by_worker = {sim.by_task[task].object_id for task, _ in sim.made}
        self.assertEqual(stems_here & by_worker, set(), "a recording's stems were made twice")
        return runner, sim, stems_here, by_worker

    def steps(self, step: str) -> list[dict]:
        return [n for n in self.notes if n.get("step") == step]

    def test_a_slow_worker_holding_its_lease_is_never_given_up(self) -> None:
        # Each task takes 0.4 s there, against 0.15 s with nothing filed; the
        # second waits behind the first, and its clock starts only when the
        # worker frees its slot.
        recs = [recording("a"), recording("b")]
        runner, sim, here, there = self.go(recs, [0.4])
        self.assertEqual(here, set())
        self.assertEqual(there, {r.object_id for r in recs})
        self.assertEqual(self.steps("no worker took the task in time; runs here too"), [])
        self.assertEqual(len(self.steps("the worker leased the task")), 2)
        self.assertGreater(runner.pace["stems"], 0.0)  # measured lease to filed

    def test_a_fast_and_a_slow_worker_share_the_work_once(self) -> None:
        recs = [recording(name) for name in "abcdef"]
        runner, sim, here, there = self.go(recs, [0.03, 0.3], in_flight=3)
        self.assertEqual(here, set())
        self.assertEqual(there, {r.object_id for r in recs})
        self.assertEqual({who for _, who in sim.made}, {"w0", "w1"})
        self.assertEqual(len(sim.made), len(recs))

    def test_an_absent_worker_is_given_up_and_its_tasks_are_made_here_once(self) -> None:
        recs = [recording("a"), recording("b")]
        runner, sim, here, there = self.go(recs, [None])
        self.assertEqual(here, {r.object_id for r in recs})
        self.assertEqual(len(self.steps("no worker took the task in time; runs here too")), 2)

    def test_a_given_up_task_the_worker_then_holds_or_files_is_not_made_here(self) -> None:
        recs = [recording("a"), recording("b")]
        sim = SimSpool(self, self.planner, recs, [])
        jobs = [Job(rec, STEMS, i, routed=False, status="pending", given_up=True, detail=f"task-{rec.digest[7:12]}")
                for i, rec in enumerate(recs)]
        runner = backfill.Runner(jobs, self.planner, self.sched, shape(self.sched), 8, 32.0, "eager",
                                 lambda **e: self.notes.append(e), self.run_unit, spool=sim)
        sim.by_task = {j.detail: j.recording for j in jobs}
        sim.leased[jobs[0].detail] = (time.time(), "w0")  # leased after the give-up
        self.planner.made(recs[1], STEMS)  # filed after the give-up
        with patch.object(backfill.remote, "collect", sim.collect):
            runner.collect()
        self.assertEqual((jobs[0].status, jobs[0].routed, jobs[0].given_up), ("put", True, False))
        self.assertEqual(jobs[1].status, "done")
        self.assertEqual(runner.given_back, 1)
        # A stale lease (no renewal within lease_fresh_seconds) is nobody's.
        self.assertFalse(runner.held({"state": "leased", "renewed_at": time.time() - 301}, time.time()))

    def test_a_recording_made_since_the_plan_is_not_put(self) -> None:
        recs = [recording("a"), recording("b")]
        sim = SimSpool(self, self.planner, recs, [0.02])
        planned = backfill.plan(recs, self.sched, self.planner, "eager", shape(self.sched), True)
        self.planner.made(recs[0], STEMS)  # a stand-in filed after the plan
        with patch.object(backfill.remote, "put", sim.put), patch.object(backfill.remote, "collect", sim.collect):
            runner = backfill.Runner(planned.jobs, self.planner, self.sched, shape(self.sched), 8, 32.0, "eager",
                                     lambda **e: self.notes.append(e), self.run_unit, spool=sim)
            sim.start()
            runner.run()
        self.assertEqual(sim.puts, [f"task-{recs[1].digest[7:12]}"])
        self.assertEqual(len(self.steps("already made; not put")), 1)

    def test_tasks_the_backfill_did_not_put_are_named(self) -> None:
        recs = [recording("a")]
        sim = SimSpool(self, self.planner, recs, [0.02])
        sim.pending.add("task-stale")  # a task from an earlier run; sorts after this run's
        sim.by_task["task-stale"] = recs[0]
        self.go_with(sim, recs)
        foreign = self.steps("the spool holds tasks this backfill did not put")
        self.assertTrue(foreign)
        self.assertEqual(foreign[0]["count"], 1)

    def test_spool_tasks_are_read_from_its_directory(self) -> None:
        root = scratch(self)
        for name in ("pending", "leased"):
            (root / name).mkdir()
        (root / "pending" / "bank-aa.json").write_text("{}")
        lease = root / "leased" / "lease_1792554021233164_33_bank-bb.json"
        lease.write_text(json.dumps({"task_id": "bank-bb", "capability": "c"}))
        lease.with_suffix(".renewed").write_text("1792555474737584")
        lease.with_suffix(".worker").write_text("mac")
        found = backfill.remote.Spool(f"spool --dir {root}").tasks()
        self.assertEqual(found, {"bank-aa": {"state": "pending"},
                                 "bank-bb": {"state": "leased", "leased_at": 1792554021.233164,
                                             "renewed_at": 1792555474.737584, "worker": "mac"}})
        self.assertIsNone(backfill.remote.Spool("ssh host spool --dir /nonexistent").tasks())
        self.assertIsNone(backfill.remote.Spool("spool").tasks())

    def go_with(self, sim, recs):
        planned = backfill.plan(recs, self.sched, self.planner, "eager", shape(self.sched), True)
        with patch.object(backfill.remote, "put", sim.put), patch.object(backfill.remote, "collect", sim.collect):
            runner = backfill.Runner(planned.jobs, self.planner, self.sched, shape(self.sched), 8, 32.0, "eager",
                                     lambda **e: self.notes.append(e), self.run_unit, spool=sim)
            sim.start()
            runner.run()
        return runner


class StandIns(unittest.TestCase):
    """ADR 0090's amendment through the scheduler: an entry made on another
    device counts as made once a passing record lets it stand in, a routed
    extractor put with the other device needs that record before anything
    runs, and a worker's entry that does not stand in is made here."""

    def setUp(self) -> None:
        from test_equivalence import Fixture
        self.f = Fixture(self)
        self.catalog = self.f.catalog()
        self.rec = Recording("urn:uuid:take", self.f.digest, self.f.content, 10.0, False, "audio/wav")
        self.cpu = {"sep": {"apply": {"device": "cpu", "shifts": 0}}}
        self.planner = backfill.Planner(self.catalog, self.f.bank, self.cpu, {"python": sys.executable})

    def sched(self, remote: dict | None = None) -> backfill.Schedule:
        setting = {"route": "spool", **({"remote_parameters": remote} if remote is not None else {})}
        return backfill.schedule_of({"backfill": {"units": [["sep"]], "extractors": {"sep": setting},
                                                  "poll_seconds": 0.01}}, self.catalog)

    def test_the_other_devices_entry_counts_as_made_only_with_a_passing_record(self) -> None:
        self.assertEqual(self.f.run("sep", "--config", str(self.f.config("gpu")))[0], 0)
        self.assertIsNotNone(self.f.entry_of("gpu"))  # anchor: the gpu entry is there, the cpu one is not
        self.assertIsNone(self.f.entry_of("cpu"))
        self.assertEqual(self.planner.state(self.rec, Unit("sep"))[0], TODO)
        self.assertEqual(self.f.measure()[0], 0)
        self.f.drop("cpu")
        self.assertEqual(self.planner.state(self.rec, Unit("sep"))[0], DONE)

    def test_routing_with_the_other_device_needs_a_passing_record_first(self) -> None:
        gpu = {"apply": {"device": "gpu", "shifts": 0}}
        with self.assertRaisesRegex(backfill.BackfillError, "no passing equivalence record"):
            backfill.check_remote(self.catalog, self.f.bank, self.cpu, self.sched(gpu))
        with self.assertRaisesRegex(backfill.BackfillError, "more than its interchangeable parameter"):
            backfill.check_remote(self.catalog, self.f.bank, self.cpu,
                                  self.sched({"apply": {"device": "gpu", "shifts": 1}}))
        backfill.check_remote(self.catalog, self.f.bank, self.cpu, self.sched())  # the same device needs nothing
        self.assertEqual(self.f.measure()[0], 0)
        backfill.check_remote(self.catalog, self.f.bank, self.cpu, self.sched(gpu))
        self.assertEqual(backfill.remote_parameters(self.cpu, "sep", self.sched(gpu)), {"sep": gpu})

    def test_a_worker_entry_that_does_not_stand_in_is_made_here(self) -> None:
        gpu = {"apply": {"device": "gpu", "shifts": 0}}
        sched = self.sched(gpu)
        shape = backfill.layout(self.catalog, sched, self.cpu, 4, 8.0)
        planned = backfill.plan([self.rec], sched, self.planner, "eager", shape, True)
        self.assertTrue(planned.jobs[0].routed)
        given = []

        def put(catalog, root, spool, extractor, content, media_type, digest, parameters, **options):
            given.append(parameters)
            return {"status": "put", "task_id": "task-1", "key": "k"}

        def collect(catalog, root, spool):
            # The worker's gpu entry arrives; no record names its software.
            self.assertEqual(self.f.run("sep", "--config", str(self.f.config("gpu")))[0], 0)
            return {"status": "ok", "tasks": {}, "failed": {}}

        ran = []

        def run_unit(job):
            ran.append(job.unit)
            return {"status": "ok"}, 0.0 if self.f.run("sep", "--exact")[0] == 0 else 1.0

        notes = []
        with patch.object(backfill.remote, "put", put), patch.object(backfill.remote, "collect", collect):
            runner = backfill.Runner(planned.jobs, self.planner, sched, shape, 4, 8.0, "songs",
                                     lambda **e: notes.append(e), run_unit, spool=object())
            runner.run()
        self.assertEqual(given, [{"sep": gpu}])
        self.assertEqual(ran, [Unit("sep")])
        self.assertEqual(planned.jobs[0].status, "done")
        self.assertIn("made on the worker, but it does not stand in here; runs here", [e["step"] for e in notes])


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
        self.mode = "--eager"

    def arguments(self, *extra: str) -> list[str]:
        return ["backfill", "--archive", str(self.root), "--bank", str(self.bank), "--config", str(self.config),
                "--door", support.DOOR, "--archive-command", support.ARCHIVE, "--ffmpeg", support.FFMPEG,
                "--ffprobe", support.FFPROBE, "--staging", str(self.tmp / "staging"), "--cores", "4",
                "--memory-gb", "8", self.mode, *extra]

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

    def test_selected_bass_result_reaches_extraction_and_door_without_unused_alternatives(self) -> None:
        config = json.loads(self.config.read_text())
        config["composition"] = {"backfill": {"features": [], "attempt": [], "produce": [
            {"contract": ["archive.claim/chord_stacks", "2"], "methods": ["bass"]}]}}
        self.config.write_text(json.dumps(config))
        code, events = self.backfill("--commit")
        self.assertEqual(code, 0, [e for e in events if e.get("ok") is False])
        made = {p.parent.name for p in entries(self.bank)}
        self.assertTrue({"stems", "tonal", "rhythm"} <= made)
        self.assertFalse({"f0-pesto", "notes-basic-pitch", "melody"} & made)
        self.assertEqual(self.step(events, "propose --all (commit)")["exit"], 0)
        def bomb(job):
            raise AssertionError("unchanged result reran extraction")
        code, rerun = self.backfill("--commit", run_unit=bomb)
        self.assertEqual(code, 0, rerun)
        self.assertEqual(self.step(rerun, "plan")["jobs"], 0)

    def test_an_export_that_fails_stops_the_backfill_rather_than_finding_nothing_unprobed(self) -> None:
        import enrich
        arguments = self.arguments()
        arguments[arguments.index("--archive-command") + 1] = str(self.tmp / "no-such-archive")
        err = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), redirect_stdout(io.StringIO()), \
                patch("sys.stderr", err):
            code = enrich.main(arguments)
        self.assertEqual(code, 1)
        self.assertIn("archive export failed", json.loads(err.getvalue())["detail"])
        self.assertFalse(entries(self.bank))

    def test_a_dry_run_writes_nothing_and_says_what_it_would_make(self) -> None:
        code, events = self.backfill()
        self.assertEqual(code, 0, events)
        self.assertFalse(entries(self.bank))
        self.assertEqual(self.step(events, "unprobed audio or video Artifacts")["count"], 2)
        plan = self.step(events, "plan")
        self.assertEqual(plan["jobs"], 22)
        # Unprobed in a dry run: no stream claim yet, so no song regions, said rather than guessed.
        self.assertEqual(self.step(events, "recordings")["song_spans_from"], {"none": 2})
        self.assertEqual(sum(1 for e in events if e["step"] == "no song regions"), 2)
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

        # Songs mode over the filled bank: every recording is probed, so each has its song
        # regions read (structure/playing from the bank is the only source here), and what
        # was made whole stands for every region, so nothing is planned.
        self.mode = "--songs"
        code, songs = self.backfill(run_unit=bomb)
        self.assertEqual(code, 0, songs)
        recordings = self.step(songs, "recordings")
        self.assertEqual(sum(recordings["song_spans_from"].values()), 2)
        self.assertLessEqual(set(recordings["song_spans_from"]), {"playing", "none"})
        self.assertNotIn("no song regions", [e["step"] for e in songs])
        self.assertEqual(self.step(songs, "plan")["jobs"], 0)

    def test_songs_mode_makes_region_entries_through_the_real_runner_and_a_rerun_makes_nothing(self) -> None:
        # The chain the backfill drives: plan -> `extract.py run --region` -> region entries -> the
        # planner reading them as made. Stems, f0 on its vocals (the probe), melody over one region.
        units = [["stems"], ["f0-pesto", "stems/vocals"], ["melody"], ["rhythm"]]
        config = stand_in_config(self.tmp, units)
        catalog = bank.Catalog.load()
        loaded = json.loads(config.read_text())
        sched = backfill.schedule_of(loaded, catalog)
        programs = {"python": sys.executable, "sh": "/bin/sh", "ffmpeg": support.FFMPEG, **loaded["programs"]}
        region = Region(44100 * 4, 44100 * 12, 44100)
        rec = Recording("urn:uuid:mix", bank.file_digest(self.wav), self.wav, 32.0, True, "audio/wav", [region])
        planner = backfill.Planner(catalog, self.bank, {}, programs)
        shaped = backfill.layout(catalog, sched, {}, 4, 8.0)
        planned = backfill.plan([rec], sched, planner, "songs", shaped, False)
        self.assertEqual(sorted((j.unit.name, j.region) for j in planned.jobs),
                         [("f0-pesto --from stems/vocals", region), ("melody", region), ("rhythm", None),
                          ("stems", region)])
        notes = []
        with patch.dict(os.environ, self.env, clear=True):
            runner = backfill.Runner(planned.jobs, planner, sched, shaped, 4, 8.0, "songs",
                                     lambda **e: notes.append(e),
                                     lambda job: backfill.run_extract(job, self.bank, str(config), support.FFMPEG))
            runner.run()
        self.assertEqual([j.status for j in planned.jobs], ["done"] * 4, [e for e in notes if e.get("error")])
        made = [json.loads((p / "entry.json").read_text()) for p in entries(self.bank)]
        regional = {m["extractor"]["id"] for m in made if m.get("region") == region.as_json()}
        self.assertIn("stems", regional)  # f0, and melody on the vocal stem, measure a stem: content of its own
        self.assertNotIn("stems", {m["extractor"]["id"] for m in made if "region" not in m})  # never made whole
        for unit in (Unit("stems"), Unit("f0-pesto", "stems/vocals"), MELODY):
            self.assertEqual(planner.state(rec, unit, region)[0], DONE, unit)
            self.assertEqual(planner.state(rec, unit)[0] == DONE, False, unit)  # anchor: not done whole
        again = backfill.plan([rec], sched, planner, "songs", shaped, False)
        self.assertEqual(again.jobs, [])
        # A narrower span inside the region is covered by it: `bank.covering`.
        inner = Region(44100 * 5, 44100 * 6, 44100)
        self.assertEqual(planner.state(rec, Unit("stems"), inner)[0], DONE)

    def test_every_recording_is_usable_before_any_stem_job_starts_and_a_request_goes_first(self) -> None:
        # The relay's request for the quiet recording's stems (its bank_parameters are the configuration's).
        quiet = bank.file_digest(self.quiet)
        _, key, wrote = bank.ask(bank.Bank(self.bank, bank.Catalog.load(), {}), quiet, "stems", "relay")
        self.assertTrue(wrote)
        starts = []

        def run_unit(job: Job):
            starts.append((job.recording.digest, job.unit.name, job.tier, job.requested))
            return backfill.run_extract(job, self.bank.resolve(), str(self.config), support.FFMPEG)

        code, events = self.backfill("--commit", run_unit=run_unit)
        self.assertEqual(code, 0, [e for e in events if e.get("ok") is False or e["step"] == "failed"])
        self.assertEqual(starts[0], (quiet, "stems", "background", True))  # the request first
        first_background = min(i for i, s in enumerate(starts) if s[2] == "background" and not s[3])
        fast = [s for s in starts if s[2] == "fast"]
        self.assertEqual({s[1] for s in fast}, {"rhythm", "tonal", "chords-jiang", "chords-chordino", "structure"})
        self.assertEqual({s[0] for s in fast}, {quiet, bank.file_digest(self.wav)})  # every recording
        self.assertTrue(all(starts.index(s) < first_background for s in fast))
        # Usable (the fast tier made and proposed through Door) before the first background job started.
        steps = [e["step"] for e in events]
        self.assertEqual([e["ok"] for e in events if e["step"] == "propose (usable)"], [True, True])  # one each
        usable, made = self.step(events, "usable"), self.step(events, "made")
        self.assertLessEqual(usable["s"], made["background_started_s"])
        self.assertLess(steps.index("usable"), steps.index("made"))
        done = self.step(events, "done")
        self.assertLessEqual(done["time_to_usable_s"], done["time_to_done_s"])
        # The request is made, and removed.
        self.assertIn("request made; removed", steps)
        self.assertFalse(bank.request_path(self.bank, key).exists())
        self.assertIsNotNone(bank.Bank(self.bank, bank.Catalog.load(), {}).entry(quiet, "stems"))

    def test_follow_makes_a_request_that_arrives_and_removes_it(self) -> None:
        self.mode = "--on-demand"
        code, events = self.backfill("--commit", "--proposers", "")  # probes both; on demand makes nothing
        self.assertEqual((code, entries(self.bank)), (0, []))
        mix = bank.file_digest(self.wav)
        reader = bank.Bank(self.bank, bank.Catalog.load(), {})
        asked = []
        timer = threading.Timer(1.0, lambda: asked.append(bank.ask(reader, mix, "rhythm", "relay")))
        timer.start()
        self.addCleanup(timer.cancel)
        code, events = self.backfill("--commit", "--proposers", "", "--follow", "--follow-seconds", "10")
        self.assertEqual(code, 0, [e for e in events if e["step"] in ("failed", "request not planned")])
        self.assertTrue(asked)
        steps = [e["step"] for e in events]
        self.assertIn("request planned", steps)
        self.assertIn("request made; removed", steps)
        self.assertIsNotNone(reader.entry(mix, "rhythm"))
        self.assertFalse(bank.request_path(self.bank, asked[0][1]).exists())

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
