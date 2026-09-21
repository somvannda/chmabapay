"""Readiness probe for the worker process.

`docker compose` healthchecks the API with an HTTP request. The worker has no HTTP
server — giving it one purely to answer a healthcheck would be an extra listening
socket whose only job is to say "still here" — so this reads the wall-clock
heartbeats the drain loops publish to Redis instead.

What it catches that nothing else does: a worker that is **alive but not draining**.
A crashed process is restarted by compose; a loop wedged on a Redis call sits there
looking healthy indefinitely while the queues fill up behind it. The alert watcher
inside the worker cannot report that condition either, because it shares the event
loop that has stopped making progress.

Usage, from the container:

    python -m chmabapay.healthcheck

Exits 0 when every expected queue has a recent heartbeat, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Any

import redis.asyncio as redis_asyncio

from .config import get_settings
from .workers.redis import shared_heartbeat_key
from .workers.runtime import worker_registry

log = logging.getLogger(__name__)

# The drain loop refreshes each queue's stamp about every five seconds, so a minute
# is a dozen missed writes — long enough not to trip on a slow Redis, short enough
# that a wedged worker is caught while it still matters.
DEFAULT_MAX_AGE_SECONDS = 60.0


async def queue_heartbeat_ages(
    *,
    queues: list[str],
    url: str | None = None,
    client: Any | None = None,
) -> dict[str, float | None]:
    """Seconds since each queue was last drained, `None` where there is no stamp.

    The admin console wants the number, not just the verdict: an age that is rising
    towards the threshold is the difference between "the API is about to stop
    detecting payments" and "the API stopped detecting payments an hour ago". The
    probe below is the same read with a threshold applied.
    """
    if client is None and not url:
        raise ValueError("queue_heartbeat_ages needs either a url or a client")
    own_client = client is None
    redis = client or redis_asyncio.from_url(url, decode_responses=True)
    try:
        now = time.time()
        stamps = await redis.mget([shared_heartbeat_key(queue) for queue in queues])
        ages: dict[str, float | None] = {}
        for queue, raw in zip(queues, stamps, strict=True):
            try:
                ages[queue] = now - float(raw) if raw is not None else None
            except (TypeError, ValueError):
                # An unreadable stamp is not evidence of a working drain.
                ages[queue] = None
        return ages
    finally:
        if own_client:
            await redis.aclose()


async def stale_queues(
    *,
    queues: list[str],
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    url: str | None = None,
    client: Any | None = None,
) -> list[str]:
    """Queues whose last drain is missing or older than `max_age_seconds`."""
    ages = await queue_heartbeat_ages(queues=queues, url=url, client=client)
    return [
        queue
        for queue, age in ages.items()
        if age is None or age > max_age_seconds
    ]


async def check() -> list[str]:
    settings = get_settings()
    return await stale_queues(
        queues=list(worker_registry()),
        max_age_seconds=DEFAULT_MAX_AGE_SECONDS,
        url=settings.redis_url,
    )


def main() -> int:
    try:
        stale = asyncio.run(check())
    except Exception as exc:  # noqa: BLE001 - the exit code is the report
        print(f"unhealthy: cannot read worker heartbeats: {exc}", file=sys.stderr)
        return 1
    if stale:
        print(
            f"unhealthy: no drain on {', '.join(stale)} in the last "
            f"{DEFAULT_MAX_AGE_SECONDS:.0f}s",
            file=sys.stderr,
        )
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
