"""In-process QueueTransport for Phase 1.

Fully async with per-queue asyncio.Queue; TTLCache dedup table.

Drop-in swap at deployment: `transport = InProcessTransport()` in Phase 1,
`transport = RedisTransport(url=REDIS_URL)` in Phase 2. Same code paths for
Worker.process() — NFR-1 zero-rewrite scalability.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any

from cachetools import TTLCache

from .base import QueueTransport
from .job import Job, JobStatus


class InProcessTransport(QueueTransport):
    def __init__(self, dedup_ttl_sec: int = 3600, dedup_max_size: int = 200_000) -> None:
        self._queues: dict[str, asyncio.Queue[Job]] = defaultdict(asyncio.Queue)
        self._pending: dict[str, Job] = {}
        self._dedup: TTLCache[str, None] = TTLCache(maxsize=dedup_max_size, ttl=dedup_ttl_sec)
        self._counters: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._last_dequeue: dict[str, float] = {}

    async def enqueue(
        self,
        queue_name: str,
        payload: dict[str, Any],
        dedup_key: str | None = None,
    ) -> Job:
        if dedup_key is not None:
            if dedup_key in self._dedup:
                return Job(queue_name=queue_name, payload=payload, dedup_key=dedup_key)
            self._dedup[dedup_key] = None
        job = Job(queue_name=queue_name, payload=payload, dedup_key=dedup_key)
        await self._queues[queue_name].put(job)
        self._pending[str(job.job_id)] = job
        self._counters[queue_name][JobStatus.PENDING] += 1
        return job

    async def dequeue(self, queue_name: str, n: int = 1, timeout: float = 0.5) -> list[Job]:
        # Stamped on entry, before any waiting: this is the drain loop saying it is
        # alive, which is what the alert watcher reads.
        self._last_dequeue[queue_name] = time.monotonic()
        jobs: list[Job] = []
        deadline = time.monotonic() + timeout
        q = self._queues[queue_name]
        while len(jobs) < n and time.monotonic() < deadline:
            try:
                job = await asyncio.wait_for(q.get(), timeout=max(0.0, deadline - time.monotonic()))
            except TimeoutError:
                break
            jobs.append(job)
        return jobs

    async def mark_done(
        self,
        job: Job,
        success: bool,
        result: dict[str, Any] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        job.result = result or {}
        if success:
            self._counters[job.queue_name][JobStatus.SUCCESS] += 1
            self._pending.pop(str(job.job_id), None)
            if job.dedup_key is not None:
                pass  # keep dedup in TTLCache until natural expiry to avoid double-runs
            return
        if retry_after_seconds is not None and job.attempts < job.max_attempts:
            loop = asyncio.get_running_loop()
            loop.call_later(
                max(1, int(retry_after_seconds)),
                lambda: asyncio.create_task(
                    self._queues[job.queue_name].put(job),
                    name=f"retry-{job.job_id}",
                ),
            )
            self._counters[job.queue_name]["retry"] += 1
            return
        self._counters[job.queue_name][JobStatus.DEAD] += 1
        self._pending.pop(str(job.job_id), None)

    async def metrics(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        # Every queue the transport knows about, not just the ones that have had a
        # job: a queue sitting at depth zero is a fact worth reporting, and its
        # absence would read as "no such queue" to a scrape.
        for qname in set(self._counters) | set(self._queues):
            cnt = self._counters.get(qname, {})
            # `pending` is the live queue depth, and it has to be assigned *after*
            # the spread: `cnt` also carries JobStatus.PENDING, counted once per
            # enqueue, so spreading it last silently reported lifetime enqueues as
            # the current backlog. A backlog alert built on that would fire harder
            # the better the workers were keeping up.
            out[qname] = {
                **cnt,
                "enqueued": cnt.get(JobStatus.PENDING, 0),
                JobStatus.PENDING: self._queues[qname].qsize(),
            }
        return out

    async def heartbeats(self) -> dict[str, float]:
        return dict(self._last_dequeue)
