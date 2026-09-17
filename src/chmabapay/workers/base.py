"""QueueTransport abstract base class + Worker base.

NFR-2: The ONLY while True loop allowed in the entire codebase lives in this
file inside QueueTransport.run_workers(). Any attempt to add a new while True
in routers/services/webhooks.py WILL FAIL spec AC-2. Refactor into Worker.process().
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from .job import Job


class QueueTransport(ABC):
    """Abstract queue interface.

    Milestone 1: InProcessTransport (in-memory asyncio queues).
    Milestone 2.7: RedisTransport (Redis Streams, same methods).
    Milestone 3: Per-worker microservices — same process() code, deploy YAML only.

    Signature contract: implement ALL 6 methods below. Do not add/remove
    parameters — Phase 2 RedisTransport swap depends on stable interface.
    """

    @abstractmethod
    async def enqueue(
        self,
        queue_name: str,
        payload: dict[str, Any],
        dedup_key: str | None = None,
    ) -> Job:
        """Create and enqueue a Job. Skip silently if dedup_key already pending."""

    @abstractmethod
    async def dequeue(self, queue_name: str, n: int = 1, timeout: float = 0.5) -> list[Job]:
        """Atomically pop up to `n` ready jobs from `queue_name`."""

    @abstractmethod
    async def mark_done(
        self,
        job: Job,
        success: bool,
        result: dict[str, Any] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        """Mark job end. If failure + retry_after_seconds set, re-enqueue at T+retry."""

    @abstractmethod
    async def metrics(self) -> dict[str, Any]:
        """Return counters per queue: {queue_name: {pending, success, failed, dead}}."""

    async def ping(self) -> None:
        """Confirm the queue's backing store is reachable, or raise.

        A transport with nothing external to reach has nothing to check, which is
        why this is not abstract. A networked one must check: every enqueue site
        swallows its exceptions by design, so an unreachable queue is otherwise
        invisible until someone notices that payments are not being detected.
        """
        return None

    async def worker_age_seconds(self) -> dict[str, float]:
        """Seconds since each queue was last drained, from any process.

        Not the same question as `heartbeats()`. That one is this process's own
        monotonic view and drives the *alert decision*, which must be made where the
        loops run. This one is for *metrics*, whose reader may be a different process —
        in the split topology it always is, and a process that never drains would
        otherwise publish no heartbeat age at all, silently dropping the signal that
        says detection has stopped.

        Transports with no cross-process view return nothing, and the metrics reader
        falls back to their local heartbeats.
        """
        return {}

    async def heartbeats(self) -> dict[str, float]:
        """Monotonic timestamp of the last dequeue per queue, if the transport knows.

        Liveness, not throughput: a drain loop asks for work on every tick whether
        or not there is any, so a timestamp that stops advancing means the loop is
        gone. Transports that cannot report it return nothing, and the alert
        watcher treats a missing heartbeat for a queue that should exist as a
        stalled worker rather than as silence to ignore.
        """
        return {}

    async def run_workers(
        self,
        workers: dict[str, Worker],
        stop: asyncio.Event,
        tick: float = 0.05,
    ) -> None:
        """Canonical worker run loop. **ONLY allowed while True in src/.**

        One drain loop per queue, run concurrently. They must not share a loop:
        this used to dequeue a batch across every queue and then await all of it
        before asking any queue for more work, so a single queue with slow jobs
        stalled the others. Webhook fan-out jobs each POST to a merchant URL and
        can take seconds apiece, which was enough to stop payment detection from
        ever being dequeued — a merchant with a slow endpoint could freeze the
        platform's ability to notice money arriving.

        Parameters
        ----------
        workers : dict
            {queue_name: Worker_instance}. One Worker per queue.
        stop : asyncio.Event
            Graceful shutdown signal.
        """
        await asyncio.gather(
            *[
                self._drain_queue(qname, worker, stop, tick)
                for qname, worker in workers.items()
            ]
        )

    async def _drain_queue(
        self,
        queue_name: str,
        worker: Worker,
        stop: asyncio.Event,
        tick: float,
    ) -> None:
        """Consume one queue until shutdown. Independent of every other queue."""
        while not stop.is_set():  # NFR-2: ONLY occurrence allowed in src/
            jobs = await self.dequeue(queue_name, n=worker.prefetch, timeout=tick)
            if not jobs:
                await asyncio.sleep(tick)
                continue
            await asyncio.gather(
                *[self._run_one(worker, job, queue_name) for job in jobs],
                return_exceptions=True,
            )

    async def _run_one(self, worker: Worker, job: Job, queue_name: str) -> None:
        """Execute Worker.process() for one job; handle retries + mark_done."""
        try:
            job.attempts += 1
            result = await worker.process(job)
            success = True
            retry_after = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - retries own policy
            success = False
            if job.attempts >= job.max_attempts:
                result = {"error": f"dead:{type(exc).__name__}:{exc}"}
                retry_after = None
            else:
                backoff_sec = min(600, 2 ** job.attempts)
                result = {"error": f"{type(exc).__name__}:{exc}", "backoff": backoff_sec}
                retry_after = backoff_sec
        await self.mark_done(job, success=success, result=result, retry_after_seconds=retry_after)
        _ = queue_name  # reserved for future queue-specific logging


class Worker(ABC):
    """Base worker. Subclass & implement process(job)."""

    prefetch: int = 1
    concurrency: int = 1

    @abstractmethod
    async def process(self, job: Job) -> dict[str, Any]:
        """Implement domain work. Return serializable dict on success.

        Raise exception to trigger backoff retry (2^n capped 600s, max_attempts 10).
        """
