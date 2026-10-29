"""The backfill's archive-side steps: `enrich` and World in processes of
their own, when to propose, and `--follow` (ADR 0112).

**Usable, recording by recording** (ADR 0112, round 3 amendment). A
recording is proposed as soon as its fast tier has settled, while the pool
goes on with the others: `enrich propose --artifact` for one recording, or
`--all` when several are waiting (one export instead of one each). Proposals
run one at a time, beside the pool. The `usable` event comes when the fast
tier is settled everywhere and every recording's proposal has run (then
World is refreshed, with `--world-store`); until then no background job
starts. So the usable proposal no longer waits for the whole fast pass, and
time-to-usable is the fast pass plus the last recording's proposal.

A usable proposal that fails makes nothing usable: the background goes on,
and the next proposal that succeeds (a batch, at most every
`propose_every_seconds`, or the last) is the usable one. Each proposal knows
its own outcome (`Steps.call`), so a `--follow` rescan's failure running
beside it is never taken for the proposal's.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import bank
from backfill_queue import OPEN, BackfillError, Job, Runner

if TYPE_CHECKING:
    from backfill import Planning, Recording

HERE = Path(__file__).resolve().parent
# More recordings than this waiting for their usable proposal are proposed
# with one `--all` (one export of the archive) rather than one by one.
BATCH_ABOVE = 2


class Steps:
    """The archive-side steps, each `enrich.py` or World in a process of its
    own as the 0.0.10 backfill ran them. `failures` counts every step that
    failed, for the exit status; `call` says whether this one did."""

    def __init__(self, args: argparse.Namespace, note: Callable[..., None], env: dict[str, str]):
        self.args, self.note, self.failures = args, note, 0
        self.env = dict(env)
        self.lock = threading.Lock()

    def failed(self) -> None:
        with self.lock:
            self.failures += 1

    def common(self) -> list[str]:
        a = self.args
        return ["--archive", a.archive, "--door", a.door, "--archive-command", a.archive_command,
                "--ffmpeg", a.ffmpeg, "--ffprobe", a.ffprobe, *(["--staging", a.staging] if a.staging else [])]

    def call(self, label: str, command: list[str], ok: tuple[int, ...] = (0,)) -> tuple[Any, bool]:
        """Run one step: its answer, and whether it succeeded."""
        started = time.monotonic()
        done = subprocess.run(command, capture_output=True, text=True, env=self.env)
        try:
            answer = json.loads(done.stdout) if done.stdout.strip() else None
        except ValueError:
            answer = done.stdout[-1500:]
        good = done.returncode in ok
        if not good:
            self.failed()
        brief = answer if not isinstance(answer, dict) or len(json.dumps(answer)) < 3000 else {
            k: answer.get(k) for k in ("status", "detail", "code")}
        self.note(step=label, exit=done.returncode, ok=good, s=round(time.monotonic() - started, 1), answer=brief,
                  stderr=done.stderr[-600:] if not good else None)
        return answer, good

    def run(self, label: str, command: list[str], ok: tuple[int, ...] = (0,)) -> Any:
        return self.call(label, command, ok)[0]

    def enrich_command(self, *words: str) -> list[str]:
        return [sys.executable, str(HERE / "enrich.py"), *words, *self.common()]

    def enrich(self, label: str, *words: str, ok: tuple[int, ...] = (0,)) -> Any:
        return self.run(label, self.enrich_command(*words), ok)

    def refresh_world(self) -> bool:
        """Refresh the disposable projection for recording-by-recording progress."""
        a = self.args
        return self.call("World refresh", [a.world_python, str(HERE.parent / "world" / "world.py"), "refresh",
                                                a.archive, "--store", a.world_store, "--archive-command",
                                                a.archive_command])[1]

    def world(self) -> bool:
        """Final World refresh and full conformance; whether both succeeded."""
        a = self.args
        refreshed = self.refresh_world()
        conforms = self.call("conformance", [a.conformance_python or a.world_python,
                                             str(HERE.parent / "world" / "conformance.py"), "--store",
                                             a.world_store])[1]
        return refreshed and conforms

    def propose_words(self, target: list[str], extra: tuple[str, ...], proposers: str | None) -> list[str]:
        a = self.args
        return ["propose", *target, "--proposers", a.proposers if proposers is None else proposers, "--bank", a.bank,
                *(["--config", a.config] if a.config else []), *extra]

    def propose(self, label: str, *extra: str, proposers: str | None = None) -> Any:
        """`enrich propose --all`: its answer."""
        return self.run(label, self.enrich_command(*self.propose_words(["--all"], extra, proposers)), (0, 3))

    def proposed(self, label: str, artifacts: list[str] | None) -> bool:
        """`enrich propose` of these Artifacts (each in turn), or `--all`
        when None: whether every one succeeded (nothing to propose counts)."""
        good = True
        for target in ([["--all"]] if artifacts is None else [["--artifact", a] for a in artifacts]):
            good = self.call(label, self.enrich_command(*self.propose_words(target, (), None)), (0, 3))[1] and good
        return good


class Proposals:
    """When to propose while the backfill runs (see the module's
    description): each recording once its fast tier settles, then in
    batches as background units land, at most every
    `propose_every_seconds`; one at a time, each in a thread of its own so
    the pool goes on. With no proposers, a proposal is nothing, and usable
    is when the fast tier is made."""

    def __init__(self, steps: Steps, args: argparse.Namespace, note: Callable[..., None], started: float,
                 every: float, enabled: bool):
        self.steps, self.args, self.note, self.started, self.every = steps, args, note, started, every
        self.enabled = enabled
        self.runner: Runner | None = None
        self.thread: threading.Thread | None = None
        self.kind: str | None = None
        self.waiting: dict[str, "Recording"] = {}  # settled fast tier, not yet proposed, in arrival order
        self.requested_waiting: dict[str, "Recording"] = {}
        self.outstanding = True  # a usable event is still to come
        self.round_failed = False  # a usable proposal of this round failed
        self.background_made = False
        self.last = time.monotonic()
        self.usable_s: float | None = None
        self.usable_pending = False  # the usable proposal failed: the next good one is usable
        self.proposed_s: list[float] = []  # seconds of each usable proposal, for the report
        self.lock = threading.Lock()

    # -- what the pool tells it --

    def start(self, recordings: list["Recording"], jobs: list[Job]) -> None:
        """Before the pool: every recording with no fast job left to make
        waits for its proposal now (its fast tier is in the bank already)."""
        busy = {j.recording.object_id for j in jobs if self.counts(j)}
        with self.lock:
            for recording in recordings:
                if recording.object_id not in busy:
                    self.waiting[recording.object_id] = recording

    @staticmethod
    def counts(job: Job) -> bool:
        return job.tier == "fast" and not job.requested and job.status in OPEN

    def request_ready(self, recording: "Recording") -> None:
        """An on-demand recording is ready for its own proposal/index round."""
        with self.lock:
            self.requested_waiting[recording.object_id] = recording

    def settled(self, job: Job) -> None:
        """Inside the pool's lock: a job ended. A recording whose fast tier
        has settled (made, failed or blocked) waits for its proposal."""
        if job.requested and job.status == "done":
            self.request_ready(job.recording)
        elif job.tier == "fast" and not job.requested:
            assert self.runner is not None
            if not any(self.counts(j) for j in self.runner.jobs if j.recording is job.recording):
                with self.lock:
                    self.waiting[job.recording.object_id] = job.recording
                    self.outstanding = True
        elif job.status == "done":
            with self.lock:
                self.background_made = True

    def fast_left(self) -> bool:
        assert self.runner is not None
        with self.runner.changed:
            return any(self.counts(j) for j in self.runner.jobs)

    def hold_background(self) -> bool:
        """Whether a usable proposal is still to come or under way."""
        with self.lock:
            return self.outstanding or bool(self.waiting)

    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    # -- the proposals --

    def tick(self) -> None:
        if self.busy():
            return
        with self.lock:
            requested = next(iter(self.requested_waiting.values()), None)
            if requested is not None:
                del self.requested_waiting[requested.object_id]
            waiting = list(self.waiting.values()) if requested is None else []
            if requested is None:
                self.waiting.clear()
        if requested is not None:
            self.begin("requested", [requested.object_id])
        elif waiting:
            artifacts = None if len(waiting) > BATCH_ABOVE else [r.object_id for r in waiting]
            self.begin("usable", artifacts)
        elif self.outstanding and not self.fast_left():
            self.begin("settle", None)
        elif (self.background_made or self.usable_pending) and time.monotonic() - self.last >= self.every:
            self.begin("background", None)

    def begin(self, kind: str, artifacts: list[str] | None) -> None:
        with self.lock:
            self.kind = kind
            if kind == "background":
                self.background_made = False
        self.thread = threading.Thread(target=self.propose, args=(kind, artifacts), daemon=True)
        self.thread.start()

    def propose(self, kind: str, artifacts: list[str] | None) -> None:
        started = time.monotonic()
        good = True
        try:
            if kind == "settle":
                good = not self.round_failed
                if good and self.enabled and self.args.world_store:
                    good = self.steps.refresh_world()
            elif self.enabled:
                label = {"usable": "propose (usable" + (", all" if artifacts is None else "") + ")",
                         "background": "propose --all (background batch)",
                         "requested": "propose (requested recording)"}[kind]
                good = self.steps.proposed(label, artifacts)
                if kind == "requested" and good and self.args.world_store:
                    good = self.steps.refresh_world()
        except Exception as exc:  # noqa: BLE001  (a proposal that could not run is not usable; the pool goes on)
            good = False
            self.steps.failed()
            self.note(step="proposal could not run", kind=kind, error=f"{type(exc).__name__}: {exc}"[-600:])
        finally:
            with self.lock:
                self.last = time.monotonic()
                self.kind = None
                if kind == "usable":
                    self.proposed_s.append(round(time.monotonic() - started, 1))
                    self.round_failed = self.round_failed or not good
                elif kind == "settle":
                    self.declare(good)
                elif kind == "background" and self.usable_pending and good:
                    self.declare(True)
            if self.runner is not None:
                with self.runner.changed:
                    self.runner.changed.notify_all()

    def declare(self, good: bool) -> None:
        """Inside the lock: this round of usable proposals is over."""
        self.outstanding, self.round_failed = False, False
        at = round(time.monotonic() - self.started, 1)
        if not good:
            self.usable_pending = True
            self.note(step="usable proposal failed", s=at)
            return
        self.usable_pending = False
        first = self.usable_s is None
        if first:
            self.usable_s = at
        self.note(step="usable", s=at, first=first, proposals=len(self.proposed_s),
                  proposal_s=round(sum(self.proposed_s), 1))

    def finish(self) -> None:
        """Wait for the proposal under way, and for the usable round to end
        when the pool has stopped first (a plan with no background)."""
        if self.thread is not None:
            self.thread.join()
        while self.runner is not None and (self.waiting or self.requested_waiting or self.outstanding):
            before = self.thread
            self.tick()
            if self.thread is before:  # nothing began (a fast job is still open): no round to end
                break
            self.thread.join()


class Follow:
    """`--follow`: every `poll_seconds`, the bank's new requests, first in
    the queue; every `rescan_seconds`, new admissions (probed, then planned);
    until `--follow-seconds` pass or the unit is stopped (SIGTERM, SIGINT).
    `discover` and `report` are the backfill's (`backfill.discover`,
    `backfill.report`)."""

    def __init__(self, planning: "Planning", args: argparse.Namespace, env: dict[str, str] | None, steps: Steps,
                 note: Callable[..., None], root: Path, by_digest: dict[str, "Recording"],
                 present: list["Recording"], discover: Callable[..., tuple[list, list]],
                 prioritise: Callable[[list], None], report: Callable[..., None],
                 request_ready: Callable[["Recording"], None] | None = None):
        self.planning, self.args, self.env, self.steps, self.note = planning, args, env, steps, note
        self.root, self.by_digest, self.present = root, by_digest, present
        self.discover, self.prioritise, self.report = discover, prioritise, report
        self.until = time.monotonic() + args.follow_seconds if args.follow_seconds is not None else None
        self.request_ready = request_ready
        self.stopped = False
        self.last_scan = time.monotonic()
        self.reported: set[str] = set()

    def stop(self) -> bool:
        return self.stopped or (self.until is not None and time.monotonic() >= self.until)

    def intake(self) -> list[Job]:
        added: list[Job] = []
        if time.monotonic() - self.last_scan >= self.planning.schedule.rescan_seconds:
            self.last_scan = time.monotonic()
            try:
                _, present = self.discover(self.args, self.env, self.steps, self.planning.planner, self.note, True)
            except (BackfillError, OSError) as exc:
                self.note(step="rescan failed", error=str(exc)[-600:])
                present = []
            known = {r.object_id for r in self.present}
            new = [r for r in present if r.object_id not in known]
            if new:
                self.present.extend(new)
                self.prioritise(self.present)
                for recording in new:
                    self.by_digest[recording.digest] = recording
                    added += self.planning.recording(recording)
                self.note(step="new recordings", count=len(new), jobs=len(added))
        try:
            asked = bank.requests(self.root)
        except (bank.BankError, OSError) as exc:
            self.note(step="requests unreadable", error=str(exc)[-600:])
            return added
        for request in asked:
            if request["key"] in self.planning.asked:
                continue
            self.planning.result.answered[:] = [key for key in self.planning.result.answered
                                                 if key != request["key"]]
            self.reported.discard(request["key"])
            jobs = self.planning.request(request, self.by_digest)
            added += jobs
            if not jobs and request["key"] in self.planning.result.answered and self.request_ready is not None:
                recording = self.by_digest.get(request["content"])
                if recording is not None:
                    self.request_ready(recording)
            if jobs:
                self.note(step="request planned", key=request["key"], jobs=[j.unit.name for j in jobs])
        self.report(self.planning.result, self.root, self.note, self.reported, True)
        for key in self.planning.result.answered:
            if not bank.request_path(self.root, key).exists():
                self.planning.asked.discard(key)
        return added
