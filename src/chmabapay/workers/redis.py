"""Redis Streams QueueTransport.

The drop-in replacement for `InProcessTransport` once more than one process has to
share the queue:

    WORKER_TRANSPORT=redis
    REDIS_URL=redis://redis:6379/0

Keys, per queue `q` (queue names are dotted, e.g. `payments.detection`):

    chmabapay:queue:{q}             stream of jobs, written with XADD
    chmabapay:queue:{q}:delayed     sorted set, score = unix time a retry comes due
    chmabapay:queue:{q}:counters    hash of lifetime counters
    chmabapay:dedup:{dedup_key}     SET NX EX — the cross-process dedup table
    consumer group                  cg-{q}

**Why a stream and not a list.** A consumer group hands each entry to exactly one
consumer, and the entry stays in the group's pending list until it is acknowledged.
A worker that dies mid-job therefore leaves its work recoverable (`XAUTOCLAIM`)
rather than lost, which a bare `BRPOP` cannot do: with a list, a crash between pop
and completion drops the job silently, and for this queue that means a payment that
was taken and never detected.

**Delivery is at-least-once, not exactly-once.** The group guarantees that two live
consumers never hold the same entry, so the concurrent-duplicate problem is solved.
But an entry idle past `claim_idle_seconds` is taken over on the assumption that its
worker died, and a worker that was merely slow will then be running the same job
alongside its rescuer. `Worker.process` must therefore be safe to run twice. The
threshold is the knob: it must exceed the slowest legitimate job, and webhook
delivery is bounded by `webhook_timeout_seconds` (5s), so the 60s default has a
wide margin.

**Heartbeats come in two forms, for two consumers.** `heartbeats()` reports this
process's own drain loops from a monotonic clock and is what the alert watcher reads
— it can only be meaningful where the loops run, which is why the watcher starts
alongside the workers rather than beside the API. Separately, each dequeue also
publishes a throttled *wall-clock* stamp to Redis under `chmabapay:hb:{q}`, because a
container healthcheck and the API's `/metrics` cannot see another process's monotonic
clock. The stamp is seconds since the epoch, so it compares across hosts; the alert
decision deliberately does not depend on it.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
import uuid
from typing import Any

import redis.asyncio as redis_asyncio
from redis.exceptions import RedisError, ResponseError

from .base import QueueTransport
from .job import Job, JobStatus

log = logging.getLogger(__name__)

_KEY_ROOT = "chmabapay:queue:"
_DEDUP_ROOT = "chmabapay:dedup:"
_SHARED_HEARTBEAT_ROOT = "chmabapay:hb:"

# How many delayed retries to promote per poll. Bounded so one poll cannot stall a
# drain loop for as long as a large backlog would take.
_PROMOTE_BATCH = 100

# One shared-heartbeat write per queue per this many seconds. The drain loop polls
# every 50ms, so writing on every dequeue would be twenty Redis commands a second per
# queue to say nothing had changed.
_SHARED_HEARTBEAT_INTERVAL = 5.0

# Long enough to outlive a couple of missed writes, short enough that a stale key
# disappears rather than lingering as a false all-clear if the process is removed.
_SHARED_HEARTBEAT_TTL = 300


def shared_heartbeat_key(queue_name: str) -> str:
    """Key holding the wall-clock time a worker last asked this queue for work."""
    return f"{_SHARED_HEARTBEAT_ROOT}{queue_name}"


def _default_consumer() -> str:
    """A consumer name unique per process.

    Per *process*, not per host: two workers sharing a consumer name would share a
    pending list, and `XAUTOCLAIM`'s idle check measures idleness per consumer — so
    one of them dying would be invisible.
    """
    return f"{socket.gethostname()}-{os.getpid()}"


class RedisTransport(QueueTransport):
    def __init__(
        self,
        url: str | None = None,
        *,
        client: Any | None = None,
        queues: list[str] | None = None,
        consumer: str | None = None,
        dedup_ttl_sec: int = 3600,
        claim_idle_seconds: float = 60.0,
        claim_interval_seconds: float = 10.0,
        max_stream_len: int = 1_000_000,
    ) -> None:
        """`client` exists for tests, which pass a fakeredis instance. One of
        `url` or `client` is required.
        """
        if client is None and not url:
            raise ValueError("RedisTransport needs either a url or a client")
        self._redis: Any = client or redis_asyncio.from_url(url, decode_responses=True)
        self._owns_client = client is None
        self._consumer = consumer or _default_consumer()
        self._dedup_ttl = dedup_ttl_sec
        self._claim_idle_ms = int(claim_idle_seconds * 1000)
        self._claim_interval = claim_interval_seconds
        self._max_stream_len = max_stream_len
        # Queues this transport should report on even when idle. Supplied from the
        # worker registry, because a process that only enqueues would otherwise
        # never learn the name of a queue it has not posted to.
        self._known_queues: set[str] = set(queues or ())
        self._groups_ready: set[str] = set()
        # entry id per job id, so mark_done can acknowledge the right stream entry.
        # A side table rather than a Job field: the dequeued entry is a transport
        # concern, and every other transport ignores it.
        self._entry_ids: dict[str, str] = {}
        self._last_dequeue: dict[str, float] = {}
        self._last_claim: dict[str, float] = {}
        self._last_shared_beat: dict[str, float] = {}

    # ------------------------------------------------------------------ keys --
    def _stream(self, queue_name: str) -> str:
        return f"{_KEY_ROOT}{queue_name}"

    def _delayed(self, queue_name: str) -> str:
        return f"{_KEY_ROOT}{queue_name}:delayed"

    def _counters_key(self, queue_name: str) -> str:
        return f"{_KEY_ROOT}{queue_name}:counters"

    def _group(self, queue_name: str) -> str:
        return f"cg-{queue_name}"

    def _dedup(self, dedup_key: str) -> str:
        return f"{_DEDUP_ROOT}{dedup_key}"

    # --------------------------------------------------------------- startup --
    async def ping(self) -> None:
        """Raise if Redis is unreachable.

        Called at startup so a misconfigured URL is a refusal to boot rather than a
        platform whose enqueues all fail quietly — every enqueue site swallows
        exceptions, so nothing else would surface it. The visible symptom would be
        payments that are taken and never detected.
        """
        await self._redis.ping()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._redis.aclose()

    # -------------------------------------------------------------- contract --
    async def enqueue(
        self,
        queue_name: str,
        payload: dict[str, Any],
        dedup_key: str | None = None,
    ) -> Job:
        self._known_queues.add(queue_name)

        if dedup_key is not None:
            # SET NX EX is the whole cross-process dedup mechanism: the first caller
            # wins, everyone else is told nothing happened. The key outlives the job
            # on purpose (parity with InProcessTransport), so a job that has just
            # finished still suppresses an immediate duplicate.
            acquired = await self._redis.set(
                self._dedup(dedup_key), "1", nx=True, ex=self._dedup_ttl
            )
            if not acquired:
                # Same shape as InProcessTransport's skip: the caller receives a Job
                # that simply was not queued. Every existing caller ignores it.
                return Job(queue_name=queue_name, payload=payload, dedup_key=dedup_key)

        job = Job(queue_name=queue_name, payload=payload, dedup_key=dedup_key)
        await self._add(queue_name, self._record(job))
        await self._redis.hincrby(self._counters_key(queue_name), "enqueued", 1)
        return job

    async def dequeue(self, queue_name: str, n: int = 1, timeout: float = 0.5) -> list[Job]:
        self._known_queues.add(queue_name)
        # Stamped on entry, before any waiting: this is the drain loop saying it is
        # alive, which is what the alert watcher reads.
        self._last_dequeue[queue_name] = time.monotonic()
        await self._publish_heartbeat(queue_name)

        await self._ensure_group(queue_name)
        await self._promote_due(queue_name)

        jobs = await self._read_new(queue_name, n, timeout)
        if len(jobs) < n:
            jobs.extend(await self._claim_stale(queue_name, n - len(jobs)))
        return jobs

    async def mark_done(
        self,
        job: Job,
        success: bool,
        result: dict[str, Any] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        job.result = result or {}
        queue_name = job.queue_name
        entry_id = self._entry_ids.pop(str(job.job_id), None)
        counters = self._counters_key(queue_name)

        if success:
            await self._redis.hincrby(counters, JobStatus.SUCCESS, 1)
            await self._ack(queue_name, entry_id)
            return

        if retry_after_seconds is not None and job.attempts < job.max_attempts:
            # Ack the entry that failed and schedule a *new* one behind the delay.
            # Leaving the failed entry unacked would make it look like a dead
            # worker's work and be claimed again immediately, defeating the backoff.
            await self._ack(queue_name, entry_id)
            await self._redis.zadd(
                self._delayed(queue_name),
                {
                    self._record(job): time.time()
                    + max(1, int(retry_after_seconds))
                },
            )
            await self._redis.hincrby(counters, "retry", 1)
            return

        await self._redis.hincrby(counters, JobStatus.DEAD, 1)
        await self._ack(queue_name, entry_id)

    async def metrics(self) -> dict[str, Any]:
        """Counters per queue, plus a backlog depth.

        `pending` is entries never handed to a consumer (`XINFO GROUPS` lag) plus
        retries waiting on their timer — the same quantity InProcessTransport
        reports as `qsize()`. Entries currently being processed are excluded by
        both, which is what makes the number comparable across transports.
        """
        out: dict[str, Any] = {}
        for queue_name in sorted(self._known_queues):
            counters = await self._redis.hgetall(self._counters_key(queue_name))
            out[queue_name] = {
                **{key: int(value) for key, value in counters.items()},
                "enqueued": int(counters.get("enqueued", 0)),
                JobStatus.PENDING: await self._backlog(queue_name),
            }
        return out

    async def heartbeats(self) -> dict[str, float]:
        """Monotonic timestamps of this process's own drains. See the module docstring."""
        return dict(self._last_dequeue)

    async def worker_age_seconds(self) -> dict[str, float]:
        """Ages from the wall-clock stamps, so another process can report them.

        Only queues that have been drained at least once appear: a queue with no stamp
        has no age, and inventing zero would turn "never started" into "just checked
        in", which is the one thing this signal exists to distinguish.
        """
        if not self._known_queues:
            return {}
        queues = sorted(self._known_queues)
        try:
            stamps = await self._redis.mget([shared_heartbeat_key(q) for q in queues])
        except RedisError as exc:
            log.debug("could not read shared heartbeats: %s", exc)
            return {}

        now = time.time()
        ages: dict[str, float] = {}
        for queue, raw in zip(queues, stamps, strict=True):
            if raw is None:
                continue
            try:
                ages[queue] = max(0.0, now - float(raw))
            except (TypeError, ValueError):
                # An unreadable stamp is not evidence of a working drain, so it is
                # left out rather than reported as fresh.
                continue
        return ages

    # --------------------------------------------------------------- internals --
    @staticmethod
    def _record(job: Job) -> str:
        return json.dumps(
            {
                "id": str(job.job_id),
                "queue": job.queue_name,
                "dedup_key": job.dedup_key,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
                "payload": job.payload,
            },
            separators=(",", ":"),
        )

    def _decode(self, record: str) -> Job:
        data = json.loads(record)
        job = Job(
            queue_name=data["queue"],
            payload=data.get("payload") or {},
            dedup_key=data.get("dedup_key"),
            job_id=uuid.UUID(data["id"]),
            attempts=int(data.get("attempts") or 0),
            max_attempts=int(data.get("max_attempts") or 10),
        )
        return job

    async def _add(self, queue_name: str, record: str) -> None:
        # `maxlen` is a safety valve against an unbounded stream, not a retention
        # policy: trimming is approximate, so at the 1M-entry default it can only
        # discard the oldest entries once the queue is already an incident.
        await self._redis.xadd(
            self._stream(queue_name),
            {"job": record},
            maxlen=self._max_stream_len,
            approximate=True,
        )

    async def _ack(self, queue_name: str, entry_id: str | None) -> None:
        if entry_id:
            await self._redis.xack(self._stream(queue_name), self._group(queue_name), entry_id)

    async def _publish_heartbeat(self, queue_name: str) -> None:
        """Publish a wall-clock heartbeat for consumers outside this process.

        Throttled, and a failure is not fatal on purpose: this is an observability
        signal, and a Redis hiccup that took down a drain loop because it could not
        write a metric would be a worse bug than the one the metric reports.
        """
        now = time.monotonic()
        if now - self._last_shared_beat.get(queue_name, 0.0) < _SHARED_HEARTBEAT_INTERVAL:
            return
        self._last_shared_beat[queue_name] = now
        try:
            await self._redis.set(
                shared_heartbeat_key(queue_name),
                str(time.time()),
                ex=_SHARED_HEARTBEAT_TTL,
            )
        except RedisError as exc:
            log.debug("could not publish a shared heartbeat for %s: %s", queue_name, exc)

    async def _ensure_group(self, queue_name: str) -> None:
        if queue_name in self._groups_ready:
            return
        try:
            await self._redis.xgroup_create(
                self._stream(queue_name), self._group(queue_name), id="0", mkstream=True
            )
        except ResponseError as exc:
            # Already there is the expected case on every boot after the first.
            if "BUSYGROUP" not in str(exc):
                raise
        self._groups_ready.add(queue_name)

    async def _read_new(self, queue_name: str, n: int, timeout: float) -> list[Job]:
        block_ms = int(timeout * 1000) if timeout and timeout > 0 else None
        try:
            response = await self._redis.xreadgroup(
                self._group(queue_name),
                self._consumer,
                {self._stream(queue_name): ">"},
                count=n,
                block=block_ms,
            )
        except ResponseError as exc:
            if "NOGROUP" not in str(exc):
                raise
            # The group or stream vanished underneath us — a flush, or a failover to
            # a replica that never had it. Recreate and read once more rather than
            # crashing a drain loop, which would take the queue down with it.
            self._groups_ready.discard(queue_name)
            await self._ensure_group(queue_name)
            response = await self._redis.xreadgroup(
                self._group(queue_name),
                self._consumer,
                {self._stream(queue_name): ">"},
                count=n,
                block=block_ms,
            )

        jobs: list[Job] = []
        for _stream, entries in response or []:
            for entry_id, fields in entries:
                try:
                    job = self._decode(fields.get("job") or "")
                except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                    # Undecodable. Dropping it is the only option, and it must be
                    # acknowledged while being dropped: left in the pending list it
                    # would be retried forever and grow the list with each pass.
                    log.error(
                        "discarding undecodable queue entry %s on %s: %s",
                        entry_id,
                        queue_name,
                        exc,
                    )
                    await self._ack(queue_name, entry_id)
                    continue
                self._entry_ids[str(job.job_id)] = entry_id
                jobs.append(job)
        return jobs

    async def _promote_due(self, queue_name: str) -> int:
        """Move retries whose delay has elapsed back onto the stream."""
        due = await self._redis.zrangebyscore(
            self._delayed(queue_name), "-inf", time.time(), start=0, num=_PROMOTE_BATCH
        )
        promoted = 0
        for record in due:
            # ZREM first and XADD only if it returned 1. Two workers polling at once
            # would otherwise both promote the same retry, and the job would run
            # twice — the delay would be enforcing backoff on a duplicate.
            if await self._redis.zrem(self._delayed(queue_name), record):
                await self._add(queue_name, record)
                promoted += 1
        return promoted

    async def _claim_stale(self, queue_name: str, n: int) -> list[Job]:
        """Take over entries whose worker most likely died before acking them.

        Throttled. On a healthy system there is nothing to claim, and paying a round
        trip per 50ms poll to confirm that is waste; on an unhealthy one, ten
        seconds of extra latency on recovery costs nothing next to the retry.
        """
        if n <= 0:
            return []
        now = time.monotonic()
        if now - self._last_claim.get(queue_name, 0.0) < self._claim_interval:
            return []
        self._last_claim[queue_name] = now

        try:
            _cursor, entries, _deleted = await self._redis.xautoclaim(
                self._stream(queue_name),
                self._group(queue_name),
                self._consumer,
                min_idle_time=self._claim_idle_ms,
                start_id="0-0",
                count=n,
            )
        except ResponseError as exc:
            if "NOGROUP" in str(exc):
                self._groups_ready.discard(queue_name)
                return []
            raise
        except RedisError as exc:
            # Recovery is best effort: a transient failure here must not take down
            # the drain loop that is successfully reading new work.
            log.warning("claim sweep skipped on %s: %s", queue_name, exc)
            return []

        jobs: list[Job] = []
        for entry_id, fields in entries or []:
            raw = (fields or {}).get("job")
            if not raw:
                continue
            try:
                job = self._decode(raw)
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                await self._ack(queue_name, entry_id)
                continue
            log.warning(
                "recovered an unacknowledged job on %s (entry %s, attempt %s); "
                "its original worker did not finish it",
                queue_name,
                entry_id,
                job.attempts,
            )
            self._entry_ids[str(job.job_id)] = entry_id
            jobs.append(job)
        return jobs

    async def _backlog(self, queue_name: str) -> int:
        delayed = await self._redis.zcard(self._delayed(queue_name))
        try:
            groups = await self._redis.xinfo_groups(self._stream(queue_name))
        except ResponseError:
            # No stream, so no group, so nothing is waiting.
            return delayed
        for group in groups or []:
            if group.get("name") == self._group(queue_name):
                # `lag` is entries written but never delivered to any consumer.
                # Older servers omit it; XPENDING's count is then the closest
                # available proxy, though it also counts in-flight entries.
                if "lag" in group:
                    return int(group["lag"]) + delayed
                summary = await self._redis.xpending(self._stream(queue_name), self._group(queue_name))
                return int((summary or {}).get("pending", 0)) + delayed
        return delayed
