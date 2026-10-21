"""The backfill's queue: its jobs, and the pool that runs them (ADR 0112).

`enrich backfill` (`backfill.py`) plans jobs; `Runner` runs them on this
machine's cores and memory, and puts the units routed to a worker through
Spool (ADR 0090).

**Order.** Ready jobs are offered in `Job.priority` order: requests, then
the fast tier, then the background by recording. No background job starts
while a fast job is still to run or the fast tier's proposal is outstanding.

**No head-of-line blocking** (ADR 0112, round 3 amendment). The first ready
job that cannot start for cores or memory is the *head*. It does not hold
the rest: the pool works out when the head will fit (the *shadow time*: the
running jobs end, by their recorded costs, until enough is free) and starts
a later job now when it fits and either ends before then, or takes only
what the head will leave over (cores and memory beyond the head's). This is
the backfilling of batch schedulers (EASY); the head is never delayed by a
job whose cost is known. A head that has waited `reserve_after_seconds`
anyway (costs run long under load) is reserved for outright: nothing new
starts until it does. A job wider than the machine's memory starts only on
an idle machine, as before.

**Overflow.** A routed job the worker is not holding (beyond
`spool_in_flight`) runs here when it would end here sooner than the worker
could end it: here, its recorded cost over its reserved cores; there, the
worker's backlog ahead of it (what it holds, and the routed jobs ahead in
the queue, by `remote_cost`) over its tasks in flight, plus its own time.
So free cores here take stems early, from the back of the worker's queue,
not only once local work runs out.

**The worker's leases** (ADR 0112, the Mac's path). When the spool's
directory can be read here (`remote.Spool.tasks`), a put task the worker
holds (leased, renewed within `lease_fresh_seconds`) is never given up, and
the absent clock runs only for tasks nobody holds, from when the worker
could have started them (its last filing, the put, or the end of its last
lease seen). A given-up task the worker leases after all, or that is filed
before it starts here, is not made here too. A job made since the plan is
not put. The worker's pace, lease to filed, replaces `remote_cost` once
measured, so a worker off the home network is costed as it runs. A
collector beside the pool files results whatever the pool and the
proposals are doing. Without the directory (a spool behind `ssh` or
`sudo`), the rule is the old one: a worker that files nothing is absent.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import bank
import extract
import remote

if TYPE_CHECKING:  # the planner's types; backfill.py imports this module
    from backfill import Planner, Recording, Schedule, Unit
    from regions import Region

DONE = "done"
RANKS = {"requested": 0, "fast": 1, "background": 2}
OPEN = ("pending", "putting", "put", "running")


class BackfillError(Exception):
    pass


@dataclass(eq=False)
class Job:
    recording: "Recording"
    unit: "Unit"
    order: int
    threads: int = 1
    memory_gb: float = 1.0
    routed: bool = False
    after: list["Job"] = field(default_factory=list)
    dependents: int = 0
    status: str = "pending"  # pending, putting, put, running, done, failed, blocked
    detail: str | None = None
    since: float = 0.0
    cpu_s: float = 0.0
    wall_s: float = 0.0
    region: "Region | None" = None  # made over this region of the recording, or whole
    requested: bool = False  # asked for by a bank request
    cores: int | None = None  # reserved on the machine; its threads when None
    tier: str = "background"
    requests: list[str] = field(default_factory=list)  # the request keys this job makes good
    given_up: bool = False  # its spool task was given up to run here; the task may still be taken
    leased_at: float | None = None  # when the worker leased its task (epoch seconds), when seen

    @property
    def seconds(self) -> float | None:
        """The audio this job measures: its region, or the whole recording."""
        if self.region is not None:
            return (self.region.end - self.region.start) / self.region.rate
        return self.recording.seconds

    @property
    def reserved(self) -> int:
        return self.cores if self.cores is not None else self.threads

    @property
    def rank(self) -> int:
        """Requests first, then the fast tier, then the background."""
        return RANKS["requested"] if self.requested else RANKS[self.tier]

    def priority(self) -> tuple[Any, ...]:
        """Its place in the queue: rank, then the recording's priority (song
        recordings, then the most recent), then what others wait for first."""
        return self.rank, self.recording.priority, self.dependents == 0, self.order

    def describe(self) -> dict[str, Any]:
        return {"recording": self.recording.object_id, "content": self.recording.digest[7:19],
                "unit": self.unit.name, **({"region": self.region.as_json()} if self.region else {})}


def remote_parameters(parameters: dict[str, Any], extractor_id: str, schedule: "Schedule") -> dict[str, Any]:
    """The parameters a unit routed to Spool is put with: the configured
    ones, with its `remote_parameters` (the Mac's `apply` with device mps)."""
    given = schedule.setting(extractor_id)["remote_parameters"]
    if not given:
        return parameters
    return {**parameters, extractor_id: {**parameters.get(extractor_id, {}), **given}}


class Runner:
    """A pool of copies over the plan (see the module's description). A job
    starts when what it waits for is done, its extractor has a free copy,
    and its reserved cores and memory fit, by `choose`.

    Routed jobs are put to the spool at most `spool_in_flight` at a time, in
    the queue's order, and collected every `poll_seconds`; with `overflow`,
    a routed job the worker is not holding runs here when it would end
    sooner here (`take_here`). A worker that files nothing for
    `local_when_away_seconds` (plus the time its oldest task should take) is
    absent: its tasks are made here, and nothing more is put to it for that
    long again.

    With `intake` (`--follow`), it does not stop when the plan is done: it
    asks `intake` for new jobs every `poll_seconds` until `stop` says so.
    `tick` is called on every turn, outside the lock (the proposals);
    `settled` with each job that ends, inside it."""

    def __init__(self, jobs: list[Job], planner: "Planner", schedule: "Schedule", shape: dict[str, dict[str, Any]],
                 cores: int, memory_gb: float, mode: str, note: Callable[..., None],
                 run_unit: Callable[[Job], tuple[dict[str, Any], float]],
                 spool: remote.Spool | None = None, config: str | None = None, *,
                 intake: Callable[[], list[Job]] | None = None, stop: Callable[[], bool] | None = None,
                 tick: Callable[[], None] | None = None, hold_background: Callable[[], bool] | None = None,
                 settled: Callable[[Job], None] | None = None):
        self.jobs, self.planner, self.schedule, self.shape = jobs, planner, schedule, shape
        self.cores, self.memory_gb, self.mode, self.note = cores, memory_gb, mode, note
        self.run_unit, self.spool, self.config = run_unit, spool, config
        self.intake, self.stop, self.tick = intake, stop or (lambda: False), tick or (lambda: None)
        self.hold_background = hold_background or (lambda: False)
        self.settled = settled or (lambda job: None)
        self.changed = threading.Condition()
        self.free_cores, self.free_memory = cores, memory_gb
        self.copies: dict[str, int] = {}
        self.last_filed = time.monotonic()
        self.put_paused_until = 0.0
        self.first_background: float | None = None  # when the first background job started
        self.head: Job | None = None  # the first ready job that could not start, and since when
        self.head_since = 0.0
        self.head_noted: Job | None = None
        self.backfilled = 0  # jobs started past a waiting head
        self.taken_here = 0  # routed jobs taken here
        # What the spool holds, task by task (`remote.Spool.tasks`), when it can be read here.
        self.view: Callable[[], dict[str, dict[str, Any]] | None] | None = getattr(spool, "tasks", None)
        self.lease_seen = 0.0  # when the worker was last seen holding one of these tasks
        self.pace: dict[str, float] = {}  # measured worker seconds per audio-second, lease to filed
        self.foreign_noted: int | None = None
        self.given_back = 0  # given-up tasks the worker took after all, so not made here

    def end(self, job: Job, status: str, detail: str | None = None) -> None:
        """Inside the lock: the job is done, failed or blocked."""
        job.status = status
        if detail is not None:
            job.detail = detail
        self.settled(job)

    # -- costs --

    def expected_wall(self, job: Job) -> float | None:
        """Seconds the job should take here, from its recorded cost (times the
        load factor) over the cores it is reserved; None when unknown."""
        cost = self.schedule.setting(job.unit.extractor)["cost"]
        if cost is None or job.seconds is None:
            return None
        return float(cost) * job.seconds * self.schedule.load_factor / max(1, job.reserved)

    def remote_wall(self, job: Job) -> float | None:
        """Seconds the worker should take, lease to filed: its measured pace
        this run when there is one (transfer included, so a worker off the
        home network counts as slower), else the recorded `remote_cost`."""
        cost = self.pace.get(job.unit.extractor, self.schedule.setting(job.unit.extractor)["remote_cost"])
        if cost is None or job.seconds is None:
            return None
        return float(cost) * job.seconds

    # -- Spool --

    def put_window(self) -> None:
        """Inside the lock: put the next routed jobs, in priority order, until
        `spool_in_flight` are with the spool."""
        if self.spool is None or time.monotonic() < self.put_paused_until:
            return
        room = self.schedule.spool_in_flight - sum(1 for j in self.jobs if j.status in ("put", "putting"))
        if room <= 0:
            return
        open_background = self.background_open()
        chosen = sorted((j for j in self.jobs if j.routed and j.status == "pending"
                         and all(a.status == "done" for a in j.after) and (j.rank < 2 or open_background)),
                        key=Job.priority)[:room]
        if not chosen:
            return
        for job in chosen:
            job.status = "putting"
        # Beside the pool: a transport takes about 0.01 s per audio-second to
        # encode and check, which must not hold back what runs here.
        threading.Thread(target=lambda: [self.put_one(job) for job in chosen], daemon=True).start()

    def put_one(self, job: Job) -> None:
        if self.planner.state(job.recording, job.unit, job.region)[0] == DONE:
            # Made since the plan (a stand-in filed from the spool, say): nothing to encode or send.
            with self.changed:
                self.end(job, "done")
                self.changed.notify_all()
            self.note(step="already made; not put", **job.describe())
            return
        try:
            answer = remote.put(self.planner.catalog, self.planner.root, self.spool, job.unit.extractor,
                                job.recording.path, job.recording.media_type
                                or extract.media_type_of(job.recording.path), job.recording.digest,
                                remote_parameters(self.planner.parameters, job.unit.extractor, self.schedule),
                                transport=self.schedule.setting(job.unit.extractor)["transport"],
                                ffmpeg=self.planner.programs.get("ffmpeg"))
        except Exception as exc:  # noqa: BLE001  (the job runs here; the backfill goes on)
            with self.changed:
                job.routed, job.status = False, "pending"
                self.changed.notify_all()
            self.note(step="spool put failed; runs here", **job.describe(), error=str(exc)[-600:])
            return
        with self.changed:
            job.detail = answer["task_id"]
            if answer["status"] == "hit":
                self.end(job, "done")
                self.changed.notify_all()
                return
            if not any(j.status == "put" for j in self.jobs):
                self.last_filed = time.monotonic()  # the worker's clock starts with its first task
            job.status, job.since = "put", time.monotonic()
            self.changed.notify_all()
        self.note(step="spool put", **job.describe(), status=answer["status"], task=answer["task_id"],
                  transport=answer.get("transport"))

    def collect(self) -> None:
        """Outside the lock: file what the spool holds, and give back to this
        machine what the spool gave up or an absent worker holds."""
        try:
            answer = remote.collect(self.planner.catalog, self.planner.root, self.spool)
        except (remote.RemoteError, bank.BankError, OSError) as exc:
            self.note(step="spool collect failed", error=str(exc)[-600:])
            answer = {"tasks": {}, "failed": {}}
        if answer.get("tasks") or answer.get("failed"):
            self.note(step="spool collect", status=answer.get("status"), failed=answer.get("failed"),
                      tasks={k[:17]: v.get("status") for k, v in answer.get("tasks", {}).items()})
        tasks = self.read_tasks()
        wall = time.time()
        with self.changed:
            waiting = [j for j in self.jobs if j.status == "put"]
            given = [j for j in self.jobs if j.given_up and j.status == "pending"]
        events = []
        for job in waiting:
            task = job.detail
            if self.planner.state(job.recording, job.unit, job.region)[0] == DONE:
                events.append((job, "done", "filed from the spool", {"waited_s": round(time.monotonic() - job.since,
                                                                                       1)}))
            elif self.made_elsewhere(job):
                events.append((job, "pending", "made on the worker, but it does not stand in here; runs here",
                               {"task": task, "detail": "no passing equivalence record names the software it "
                                                        "was made with"}))
            elif task in (answer.get("failed") or {}) \
                    or (answer.get("tasks") or {}).get(task, {}).get("status") == "refused":
                events.append((job, "pending", "the spool gave the task up; runs here", {"task": task}))
        # Given up but not started here: the task the spool still holds may
        # have been filed meanwhile, or leased after all. Either way it is
        # not made here too.
        for job in given:
            if self.planner.state(job.recording, job.unit, job.region)[0] == DONE:
                events.append((job, "done", "filed from the spool after it was given up; not made here",
                               {"task": job.detail}))
            elif tasks is not None and self.held(tasks.get(job.detail or ""), wall):
                events.append((job, "put", "the worker took it after all; not made here", {"task": job.detail}))
        with self.changed:
            now = time.monotonic()
            for job, status, step, extra in events:
                if job.status not in ("put", "pending"):
                    continue  # started here meanwhile
                if status == "done":
                    if job.status == "put":
                        job.wall_s = now - job.since
                        self.measured(job, wall, extra)
                    self.last_filed = now
                    self.end(job, "done")
                elif status == "put":
                    job.status, job.routed, job.given_up = "put", True, False
                    self.given_back += 1
                else:
                    job.status, job.routed = "pending", False
                self.note(step=step, **job.describe(), **extra)
            still = [j for j in self.jobs if j.status == "put"]
            queued = still
            if tasks is not None:
                held = [j for j in still if self.held(tasks.get(j.detail or ""), wall)]
                for job in held:
                    if job.leased_at is None:
                        job.leased_at = tasks[job.detail or ""]["leased_at"]
                        self.note(step="the worker leased the task", **job.describe(), task=job.detail,
                                  worker=tasks[job.detail or ""].get("worker"),
                                  queued_s=round(max(0.0, job.leased_at - (wall - (now - job.since))), 1))
                if held:
                    self.lease_seen = now  # the worker is busy with this backfill's work
                queued = [j for j in still if all(j is not h for h in held)]
                self.note_foreign(tasks)
            if queued:
                # The absent clock runs only for tasks nobody holds, and from
                # when the worker could have started them: its last filing,
                # the task's put, or the end of the last lease seen.
                oldest = min(queued, key=lambda j: j.since)
                allowance = self.remote_wall(oldest) or 0.0
                limit = self.schedule.away(self.mode) + allowance
                if now - max(self.last_filed, oldest.since, self.lease_seen) > limit:
                    for job in queued:
                        job.status, job.routed, job.given_up = "pending", False, True
                        self.note(step="no worker took the task in time; runs here too", **job.describe(),
                                  task=job.detail, limit_s=round(limit, 1))
                    self.put_paused_until = now + self.schedule.away(self.mode)
            self.changed.notify_all()

    def read_tasks(self) -> dict[str, dict[str, Any]] | None:
        """Outside the lock: the spool's tasks, or None when unknown."""
        if self.view is None:
            return None
        try:
            return self.view()
        except Exception as exc:  # noqa: BLE001  (unknown is the old rule, never a failed backfill)
            self.note(step="spool tasks unreadable", error=str(exc)[-300:])
            return None

    def held(self, task: dict[str, Any] | None, wall: float) -> bool:
        """Whether a worker holds the task: leased, and renewed lately."""
        return bool(task) and task.get("state") == "leased" \
            and wall - float(task.get("renewed_at") or 0.0) <= self.schedule.lease_fresh_seconds

    def measured(self, job: Job, wall: float, extra: dict[str, Any]) -> None:
        """Inside the lock: the worker's pace on a job it was seen to lease,
        lease to filed, in seconds per audio-second; the next estimates
        (`remote_wall`) use it, half the last and half before."""
        if job.leased_at is None or not job.seconds:
            return
        pace = max(0.0, wall - job.leased_at) / job.seconds
        before = self.pace.get(job.unit.extractor)
        self.pace[job.unit.extractor] = pace if before is None else (pace + before) / 2
        extra["worker_s_per_audio_s"] = round(pace, 4)

    def note_foreign(self, tasks: dict[str, dict[str, Any]]) -> None:
        """Inside the lock: say once (and when it changes) how many tasks the
        spool holds that this backfill did not put. The worker may take them
        before this backfill's; only Spool can withdraw them."""
        ours = {j.detail for j in self.jobs if j.detail}
        foreign = sorted(t for t in tasks if t not in ours)
        if len(foreign) != (self.foreign_noted or 0):
            self.foreign_noted = len(foreign)
            self.note(step="the spool holds tasks this backfill did not put", count=len(foreign),
                      leased=sum(1 for t in foreign if tasks[t].get("state") == "leased"),
                      tasks=[t[:17] for t in foreign[:20]])

    def made_elsewhere(self, job: Job) -> bool:
        """Whether the worker's entry, under the parameters it was put with,
        is in the bank although it does not count here."""
        parameters = remote_parameters(self.planner.parameters, job.unit.extractor, self.schedule)
        if parameters is self.planner.parameters:
            return False
        exact = bank.Bank(self.planner.root, self.planner.catalog, parameters, exact=True)
        return exact.entry(job.recording.digest, job.unit.extractor) is not None

    def take_here(self, job: Job, ready: list[Job], now: float) -> bool:
        """Inside the lock: whether a routed job the worker is not holding
        runs here now: with `overflow`, when it would end here before the
        worker could end it (the worker's backlog ahead of it over its tasks
        in flight, plus its own time). Unknown costs take it, as free cores
        always did."""
        if self.spool is None or now < self.put_paused_until:
            return True  # no worker, or it is away
        if not self.schedule.overflow:
            return False
        here, there = self.expected_wall(job), self.remote_wall(job)
        if here is None or there is None:
            return True
        backlog = 0.0
        for other in self.jobs:
            if other.routed and other.status in ("put", "putting"):
                backlog += max(0.0, (self.remote_wall(other) or 0.0) - (now - other.since if other.since else 0.0))
        place = job.priority()
        for other in ready:
            if other is not job and other.routed and other.priority() < place:
                backlog += self.remote_wall(other) or 0.0
        return here < backlog / max(1, self.schedule.spool_in_flight) + there

    # -- the pool --

    def background_open(self) -> bool:
        """Whether a background job may start: the fast tier is all made and
        its proposal is not outstanding."""
        if any(j.tier == "fast" and not j.requested and j.status in OPEN for j in self.jobs):
            return False
        return not self.hold_background()

    def ready(self) -> list[Job]:
        """Inside the lock: the jobs that may start, in the order they are
        offered: one queue, routed jobs the worker is not holding in their
        place by priority (whether one runs here is `take_here`'s)."""
        open_background = self.background_open()
        return sorted((j for j in self.jobs if j.status == "pending" and all(a.status == "done" for a in j.after)
                       and (j.rank < RANKS["background"] or open_background)), key=Job.priority)

    def fits(self, job: Job, cores: float, memory: float, idle: bool) -> bool:
        """Its reserved cores and memory fit in what is free; a job wider
        than the machine's memory fits only an idle machine."""
        return job.reserved <= cores and (job.memory_gb <= memory or (idle and job.memory_gb > self.memory_gb))

    def shadow(self, head: Job, now: float, cores: float, memory: float,
               chosen: list[Job]) -> tuple[float | None, float, float]:
        """Inside the lock: when the head should fit, from the expected ends
        of the running jobs and of those `chosen` to start now (None when one
        it waits for has no recorded cost), and the cores and memory it will
        leave over then; `cores` and `memory` are free after `chosen`."""
        ends = []
        for job in [j for j in self.jobs if j.status == "running"] + chosen:
            wall = self.expected_wall(job)
            since = job.since if job.status == "running" else now
            ends.append((since + wall if wall is not None else float("inf"), job))
        ends.sort(key=lambda pair: pair[0])
        for index, (end, job) in enumerate(ends):
            cores += job.reserved
            memory += job.memory_gb
            if self.fits(head, cores, memory, index == len(ends) - 1):
                when = None if end == float("inf") else max(end, now)
                return when, cores - head.reserved, max(0.0, memory - head.memory_gb)
        return None, 0.0, 0.0

    def choose(self, ready: list[Job], now: float) -> list[Job]:
        """Inside the lock, changing nothing but the head it records: which ready jobs start now, in
        order. Each that fits starts until one does not (the head); after
        it, a job that fits starts only if it ends before the head's shadow
        time or takes only what the head leaves over, and nothing does once
        the head has waited `reserve_after_seconds`."""
        cores, memory = self.free_cores, self.free_memory
        copies = dict(self.copies)
        running = any(j.status == "running" for j in self.jobs)
        chosen: list[Job] = []
        head: Job | None = None
        shadow: float | None = None
        spare_cores, spare_memory = 0.0, 0.0
        for job in ready:
            if copies.get(job.unit.extractor, 0) >= self.shape[job.unit.extractor]["copies"]:
                continue  # its extractor's copies are all busy: no resource the others need
            if job.routed and not self.take_here(job, ready, now):
                continue  # the worker should end it sooner
            if not self.fits(job, cores, memory, not running and not chosen):
                if head is None:
                    head = job
                    if self.head is not job:
                        self.head, self.head_since = job, now
                    if now - self.head_since >= self.schedule.reserve_after_seconds:
                        break  # it has waited long enough: reserved for, nothing new starts
                    shadow, spare_cores, spare_memory = self.shadow(job, now, cores, memory, chosen)
                continue
            if head is not None:
                wall = self.expected_wall(job)
                if shadow is not None and wall is not None and now + wall <= shadow:
                    pass  # it ends before the head can start
                elif job.reserved <= spare_cores and job.memory_gb <= spare_memory:
                    spare_cores -= job.reserved
                    spare_memory -= job.memory_gb
                else:
                    continue
            chosen.append(job)
            cores -= job.reserved
            memory -= job.memory_gb
            copies[job.unit.extractor] = copies.get(job.unit.extractor, 0) + 1
        if head is None:
            self.head = None
        return chosen

    def start(self, job: Job, backfilled: bool = False) -> None:
        job.status, job.since = "running", time.monotonic()
        if job.routed:
            job.routed = False  # taken here, from the worker's queue
            self.taken_here += 1
            self.note(step="routed work taken here", **job.describe())
        if backfilled:
            self.backfilled += 1
        if job.rank == RANKS["background"] and self.first_background is None:
            self.first_background = job.since
        self.free_cores -= job.reserved
        self.free_memory -= job.memory_gb
        self.copies[job.unit.extractor] = self.copies.get(job.unit.extractor, 0) + 1
        threading.Thread(target=self.work, args=(job,), daemon=True).start()

    def work(self, job: Job) -> None:
        try:
            answer, cpu = self.run_unit(job)
        except Exception as exc:  # noqa: BLE001  (the job fails; the backfill goes on)
            answer, cpu = {"status": "failed", "detail": f"{type(exc).__name__}: {exc}"}, 0.0
        state = self.planner.state(job.recording, job.unit, job.region)[0] if answer.get("status") == "ok" else None
        with self.changed:
            job.wall_s, job.cpu_s = time.monotonic() - job.since, cpu
            self.free_cores += job.reserved
            self.free_memory += job.memory_gb
            self.copies[job.unit.extractor] -= 1
            if state == DONE:
                self.end(job, "done")
            else:
                self.end(job, "failed", first_error(answer) if state is None
                         else f"ran, but the bank has no entry ({state})")
            self.note(step="ran" if job.status == "done" else "failed", **job.describe(), s=round(job.wall_s, 1),
                      cpu_s=round(cpu, 1), tier=job.tier, requested=job.requested or None,
                      error=job.detail if job.status == "failed" else None)
            self.changed.notify_all()

    def run(self) -> None:
        """Run the plan. With a spool, a collector beside the pool files what
        the worker sends every `poll_seconds`, whatever the pool or `tick`
        is doing, while any task is with the spool, also while puts are
        paused after a give-up (a given-up task may still be filed)."""
        if self.spool is None:
            self.pool()
            return
        finished = threading.Event()
        collector = threading.Thread(target=self.collector, args=(finished,), daemon=True)
        collector.start()
        try:
            self.pool()
        finally:
            finished.set()
            collector.join()

    def collector(self, finished: threading.Event) -> None:
        while not finished.wait(self.schedule.poll_seconds):
            with self.changed:
                tracking = any(j.status == "put" or (j.given_up and j.status == "pending") for j in self.jobs)
            if tracking:
                self.collect()

    def pool(self) -> None:
        last_intake = time.monotonic()
        with self.changed:
            while True:
                for job in self.jobs:
                    if job.status == "pending" and any(a.status in ("failed", "blocked") for a in job.after):
                        self.end(job, "blocked", "what it measures was not made")
                        self.note(step="blocked", **job.describe())
                stopping = self.intake is not None and self.stop()
                held = self.hold_background()
                started = 0
                if not stopping:
                    self.put_window()
                    for job in self.choose(self.ready(), time.monotonic()):
                        self.start(job, backfilled=self.head is not None and job.priority() > self.head.priority())
                        started += 1
                    head = self.head
                    if head is not None and self.head_noted is not head:
                        self.head_noted = head
                        self.note(step="waits for room; later jobs fill around it", **head.describe(),
                                  cores=head.reserved, memory_gb=head.memory_gb)
                active = any(j.status in OPEN for j in self.jobs)
                running = any(j.status in ("running", "putting") for j in self.jobs)
                waiting = any(j.status == "put" for j in self.jobs)
                self.changed.release()
                try:
                    self.tick()
                    if self.intake is not None and not stopping \
                            and time.monotonic() - last_intake >= self.schedule.poll_seconds:
                        for job in self.intake():
                            if all(job is not j for j in self.jobs):
                                self.jobs.append(job)
                        last_intake = time.monotonic()
                finally:
                    self.changed.acquire()
                if stopping and not running:
                    return
                if not active and self.intake is None:
                    return
                if active and not (running or waiting or started or held or self.intake is not None
                                   or time.monotonic() < self.put_paused_until):
                    # With nothing running every ready job fits, so a pending one waits on itself.
                    raise BackfillError("nothing can start: the plan waits on itself")
                self.changed.wait(timeout=min(1.0, self.schedule.poll_seconds) if waiting or self.intake
                                  else 0.05 if held else 5.0 if self.head is None else 1.0)


def first_error(answer: dict[str, Any]) -> str | None:
    if answer.get("detail"):
        return str(answer["detail"])[-600:]
    for name, outcome in answer.get("extractors", {}).items():
        if outcome.get("status") in ("failed", "blocked"):
            return f"{name}: {outcome.get('error') or outcome.get('reason')}"[-600:]
    return None
