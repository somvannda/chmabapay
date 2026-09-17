"""RedisTransport: the shared-queue semantics P2-3 exists to get.

These run against `fakeredis`, so the suite stays offline. Two clients bound to one
`FakeServer` are two *processes* as far as the transport is concerned — separate
consumer names, separate pending lists, one Redis — which is what makes the
concurrency properties testable without standing up a server.

Set `CHMABAPAY_TEST_REDIS_URL` to run the same tests against a real Redis; CI does,
so the command semantics are never proved only against a simulator.
"""

from __future__ import annotations

import asyncio
import os
import time

import fakeredis
import fakeredis.aioredis as fakeredis_async
import pytest
import redis.asyncio as redis_asyncio

QUEUE = "payments.detection"
OTHER_QUEUE = "webhooks.send"

_REAL_REDIS_URL = os.getenv("CHMABAPAY_TEST_REDIS_URL")


@pytest.fixture
async def redis_server():
    """One Redis, shared by every client in a test.

    A real server when `CHMABAPAY_TEST_REDIS_URL` is set, otherwise an in-process
    fake. Real Redis needs flushing between tests, because unlike the fake it
    outlives them.
    """
    if _REAL_REDIS_URL:
        probe = redis_asyncio.from_url(_REAL_REDIS_URL, decode_responses=True)
        await probe.flushdb()
        await probe.aclose()
        yield _REAL_REDIS_URL
    else:
        yield fakeredis.FakeServer()


async def _client(redis_server):
    if isinstance(redis_server, str):
        return redis_asyncio.from_url(redis_server, decode_responses=True)
    return fakeredis_async.FakeRedis(server=redis_server, decode_responses=True)


async def _transport(redis_server, *, claim_idle=60.0, claim_interval=10.0, consumer=None):
    from chmabapay.workers.redis import RedisTransport

    client = await _client(redis_server)
    return RedisTransport(
        client=client,
        queues=[QUEUE, OTHER_QUEUE],
        consumer=consumer,
        claim_idle_seconds=claim_idle,
        claim_interval_seconds=claim_interval,
    )


async def test_enqueue_then_dequeue_round_trips_the_job(redis_server) -> None:
    transport = await _transport(redis_server)
    sent = await transport.enqueue(QUEUE, {"type": "check", "payment_id": "p_1"}, "dedup-1")

    [received] = await transport.dequeue(QUEUE, n=1, timeout=0.01)

    assert received.job_id == sent.job_id
    assert received.payload == {"type": "check", "payment_id": "p_1"}
    assert received.dedup_key == "dedup-1"
    assert received.queue_name == QUEUE


async def test_a_duplicate_dedup_key_is_not_enqueued(redis_server) -> None:
    """Parity with InProcessTransport: the caller gets a Job, the queue gets one entry."""
    transport = await _transport(redis_server)
    first = await transport.enqueue(QUEUE, {"n": 1}, "same-key")
    second = await transport.enqueue(QUEUE, {"n": 2}, "same-key")

    first_batch = await transport.dequeue(QUEUE, n=5, timeout=0.01)
    assert [job.job_id for job in first_batch] == [first.job_id]

    rest = await transport.dequeue(QUEUE, n=5, timeout=0.01)
    assert rest == []
    assert second.job_id != first.job_id  # a Job was still returned, just not queued


async def test_dedup_is_shared_across_transports(redis_server) -> None:
    """The reason dedup moved to Redis at all.

    Two processes each hold their own in-memory dedup table. With one queue shared
    between them, a per-process table suppresses nothing: both would enqueue, and
    the duplicate work would be done twice. `SET NX` makes the first caller win
    across every process.
    """
    writer_a = await _transport(redis_server, consumer="a")
    writer_b = await _transport(redis_server, consumer="b")

    await writer_a.enqueue(QUEUE, {"n": 1}, "shared-dedup")
    await writer_b.enqueue(QUEUE, {"n": 2}, "shared-dedup")

    reader = await _transport(redis_server, consumer="reader")
    batch = await reader.dequeue(QUEUE, n=5, timeout=0.01)

    assert len(batch) == 1
    assert batch[0].payload == {"n": 1}


async def test_two_workers_never_receive_the_same_job(redis_server) -> None:
    """No double-processing: a consumer group hands each entry to one consumer.

    This is the "must not double-expire or double-send" property. Reader A is given
    the change to drain everything first, so B picking up nothing is the interesting
    half: entries already delivered are not handed out again while they are being
    worked on.
    """
    writer = await _transport(redis_server)
    for index in range(20):
        await writer.enqueue(QUEUE, {"n": index})

    reader_a = await _transport(redis_server, consumer="a")
    reader_b = await _transport(redis_server, consumer="b")

    from_a = await reader_a.dequeue(QUEUE, n=20, timeout=0.01)
    from_b = await reader_b.dequeue(QUEUE, n=20, timeout=0.01)

    ids = [job.job_id for job in from_a + from_b]
    assert len(ids) == len(set(ids)), "the same entry was delivered to two consumers"
    assert len(ids) == 20
    assert {job.payload["n"] for job in from_a + from_b} == set(range(20))


async def test_work_is_split_when_both_workers_compete(redis_server) -> None:
    """Both readers get work when they poll concurrently, and none is duplicated."""
    writer = await _transport(redis_server)
    for index in range(10):
        await writer.enqueue(QUEUE, {"n": index})

    reader_a = await _transport(redis_server, consumer="a")
    reader_b = await _transport(redis_server, consumer="b")

    batch_a, batch_b = await asyncio.gather(
        reader_a.dequeue(QUEUE, n=5, timeout=0.01),
        reader_b.dequeue(QUEUE, n=5, timeout=0.01),
    )

    seen = [job.job_id for job in batch_a + batch_b]
    assert len(seen) == len(set(seen))
    assert batch_a, "one reader got everything, so the group is not distributing"
    assert batch_b, "one reader got everything, so the group is not distributing"
    assert len(seen) == 10


async def test_a_failed_job_is_retried_only_after_its_delay(redis_server) -> None:
    """Backoff is real, and the attempt count survives the round trip through Redis."""
    transport = await _transport(redis_server, claim_interval=9999.0)
    await transport.enqueue(QUEUE, {"n": 1})

    [job] = await transport.dequeue(QUEUE, n=1, timeout=0.01)
    job.attempts += 1
    await transport.mark_done(job, success=False, result={"error": "boom"}, retry_after_seconds=1)

    assert await transport.dequeue(QUEUE, n=1, timeout=0.01) == [], "the delay was skipped"

    await asyncio.sleep(1.1)
    [retried] = await transport.dequeue(QUEUE, n=1, timeout=0.01)

    assert retried.job_id == job.job_id
    assert retried.attempts == job.attempts, "attempts did not survive the delayed set"


async def test_an_exhausted_job_is_dead_and_not_retried(redis_server) -> None:
    transport = await _transport(redis_server)
    await transport.enqueue(QUEUE, {"n": 1})

    [job] = await transport.dequeue(QUEUE, n=1, timeout=0.01)
    job.attempts = job.max_attempts
    await transport.mark_done(job, success=False, result={"error": "boom"}, retry_after_seconds=5)

    await asyncio.sleep(0.05)
    assert await transport.dequeue(QUEUE, n=1, timeout=0.01) == []
    counts = (await transport.metrics())[QUEUE]
    assert counts["dead"] == 1
    # Absent rather than zero, matching InProcessTransport: counters appear when
    # they first move. The metrics reader defaults them, so both shapes work.
    assert counts.get("retry", 0) == 0


async def test_a_successful_job_is_acknowledged(redis_server) -> None:
    """Acked, so a later claim sweep cannot resurrect finished work."""
    transport = await _transport(redis_server, claim_idle=0.0, claim_interval=0.0)
    await transport.enqueue(QUEUE, {"n": 1})

    [job] = await transport.dequeue(QUEUE, n=1, timeout=0.01)
    await transport.mark_done(job, success=True, result={"ok": True})

    assert await transport.dequeue(QUEUE, n=1, timeout=0.01) == []
    assert (await transport.metrics())[QUEUE]["success"] == 1


async def test_an_unacknowledged_job_is_recovered_by_another_worker(redis_server) -> None:
    """The crash path, and the reason this is a stream rather than a list.

    Worker A takes the entry and dies before acknowledging — no mark_done, no ack.
    With a list, that job is gone. Here the entry is still in the group's pending
    list, and an idle threshold is all that separates it from a second worker
    picking it up.
    """
    dead_worker = await _transport(redis_server, claim_idle=60.0, consumer="dead")
    await dead_worker.enqueue(QUEUE, {"n": 1})
    [taken] = await dead_worker.dequeue(QUEUE, n=1, timeout=0.01)
    # No mark_done: the worker is gone.

    rescuer = await _transport(redis_server, claim_idle=0.0, claim_interval=0.0, consumer="rescuer")
    [recovered] = await rescuer.dequeue(QUEUE, n=1, timeout=0.01)

    assert recovered.job_id == taken.job_id
    assert recovered.payload == taken.payload


async def test_metrics_report_backlog_and_outcomes(redis_server) -> None:
    transport = await _transport(redis_server)
    await transport.enqueue(QUEUE, {"n": 1})
    await transport.enqueue(QUEUE, {"n": 2})

    [first] = await transport.dequeue(QUEUE, n=1, timeout=0.01)
    await transport.mark_done(first, success=True, result={})

    counts = (await transport.metrics())[QUEUE]

    assert counts["success"] == 1
    assert counts["enqueued"] == 2
    # One entry left undelivered. The one being processed is excluded, as in
    # InProcessTransport, so the two numbers mean the same thing.
    assert counts["pending"] == 1


async def test_an_idle_queue_is_still_reported(redis_server) -> None:
    """A queue at depth zero is a fact; its absence would read as "no such queue"."""
    transport = await _transport(redis_server, consumer="idle")
    counts = (await transport.metrics())[QUEUE]
    assert counts["pending"] == 0


async def test_an_undecodable_entry_is_dropped_and_acknowledged(redis_server) -> None:
    """A malformed entry must not poison the queue permanently.

    Left in the pending list, every claim pass would hand it back and log it again,
    forever. Dropping it is the only option, so it has to be acknowledged while
    being dropped.
    """
    transport = await _transport(redis_server)
    client = await _client(redis_server)
    await client.xadd(f"chmabapay:queue:{QUEUE}", {"job": "{not json"})
    await client.xadd(f"chmabapay:queue:{QUEUE}", {"job": '{"missing": "id"}'})
    await transport.enqueue(QUEUE, {"n": "good"})

    batch = await transport.dequeue(QUEUE, n=5, timeout=0.01)

    assert [job.payload for job in batch] == [{"n": "good"}]
    groups = await client.xinfo_groups(f"chmabapay:queue:{QUEUE}")
    assert groups[0]["pending"] == 1, "a bad entry was left pending and will be retried forever"


async def test_heartbeats_are_reported_per_queue(redis_server) -> None:
    transport = await _transport(redis_server)
    assert await transport.heartbeats() == {}

    await transport.dequeue(QUEUE, n=1, timeout=0.01)

    heartbeats = await transport.heartbeats()
    assert QUEUE in heartbeats
    assert OTHER_QUEUE not in heartbeats


async def test_a_queue_that_never_started_reports_no_heartbeat(redis_server) -> None:
    """Which the watcher reads as "worker never started", so it must stay absent."""
    transport = await _transport(redis_server)
    await transport.enqueue(OTHER_QUEUE, {"n": 1})
    await transport.dequeue(QUEUE, n=1, timeout=0.01)

    assert set(await transport.heartbeats()) == {QUEUE}


async def test_the_transport_honours_the_base_class_contract(redis_server) -> None:
    """Drop-in means replaceable, so both transports must expose the same surface."""
    from chmabapay.workers.base import QueueTransport
    from chmabapay.workers.inprocess import InProcessTransport

    transport = await _transport(redis_server)

    assert isinstance(transport, QueueTransport)
    for method in ("enqueue", "dequeue", "mark_done", "metrics", "heartbeats"):
        assert hasattr(transport, method)
    assert set((await transport.metrics())[QUEUE]) >= {"pending", "enqueued"}
    assert set((await InProcessTransport().metrics()).get(QUEUE, {"pending": 0})) >= {"pending"}


async def test_enqueue_without_a_dedup_key_always_queues(redis_server) -> None:
    """The dedup table must not be consulted when the caller does not ask for it."""
    transport = await _transport(redis_server)
    await transport.enqueue(QUEUE, {"n": 1})
    await transport.enqueue(QUEUE, {"n": 1})

    assert len(await transport.dequeue(QUEUE, n=5, timeout=0.01)) == 2


async def test_dequeue_publishes_a_shared_heartbeat(redis_server) -> None:
    """In Redis, because the consumers of it are other processes.

    `heartbeats()` is monotonic and process-local and drives the alert decision; this
    stamp is wall-clock and is what the worker container's healthcheck and the API's
    `/metrics` can actually read.
    """
    from chmabapay.workers.redis import shared_heartbeat_key

    transport = await _transport(redis_server)
    client = await _client(redis_server)
    assert await client.get(shared_heartbeat_key(QUEUE)) is None

    await transport.dequeue(QUEUE, n=1, timeout=0.01)

    raw = await client.get(shared_heartbeat_key(QUEUE))
    assert raw is not None, "no shared heartbeat was published"
    assert abs(time.time() - float(raw)) < 30


async def test_the_healthcheck_passes_when_every_queue_is_draining(redis_server) -> None:
    from chmabapay.healthcheck import stale_queues

    transport = await _transport(redis_server)
    client = await _client(redis_server)
    await transport.dequeue(QUEUE, n=1, timeout=0.01)
    await transport.dequeue(OTHER_QUEUE, n=1, timeout=0.01)

    stale = await stale_queues(
        queues=[QUEUE, OTHER_QUEUE], max_age_seconds=60, client=client
    )

    assert stale == []


async def test_the_healthcheck_reports_a_queue_nobody_drained(redis_server) -> None:
    """The condition a compose `depends_on` cannot see: process up, queue ignored."""
    from chmabapay.healthcheck import stale_queues

    transport = await _transport(redis_server)
    client = await _client(redis_server)
    await transport.dequeue(QUEUE, n=1, timeout=0.01)

    stale = await stale_queues(
        queues=[QUEUE, OTHER_QUEUE], max_age_seconds=60, client=client
    )

    assert stale == [OTHER_QUEUE]


async def test_the_healthcheck_reports_a_stalled_drain(redis_server) -> None:
    """A heartbeat that has stopped advancing is the failure worth catching."""
    from chmabapay.healthcheck import stale_queues
    from chmabapay.workers.redis import shared_heartbeat_key

    client = await _client(redis_server)
    await client.set(shared_heartbeat_key(QUEUE), str(time.time() - 600))

    stale = await stale_queues(queues=[QUEUE], max_age_seconds=60, client=client)

    assert stale == [QUEUE]


async def test_a_worker_age_is_readable_from_another_process(redis_server) -> None:
    """How the API reports stalls when it is not the process draining.

    The API has no drain loops in the split topology, so it has no heartbeats of its
    own and would publish no heartbeat age at all — dropping the one metric that says
    detection has stopped. This is the shared reading that replaces it.
    """
    draining = await _transport(redis_server, consumer="drainer")
    observer = await _transport(redis_server, consumer="observer")
    await draining.dequeue(QUEUE, n=1, timeout=0.01)

    ages = await observer.worker_age_seconds()

    assert QUEUE in ages
    assert 0.0 <= ages[QUEUE] < 30
    assert OTHER_QUEUE not in ages, "a queue nobody drained must not report an age"


async def test_a_queue_never_drained_reports_no_age(redis_server) -> None:
    """Absent, not zero: zero would read as "checked in a moment ago"."""
    transport = await _transport(redis_server)

    assert await transport.worker_age_seconds() == {}


async def test_an_in_process_transport_has_no_shared_ages() -> None:
    """Which is what makes the metrics reader fall back to local heartbeats."""
    from chmabapay.workers.inprocess import InProcessTransport

    assert await InProcessTransport().worker_age_seconds() == {}
