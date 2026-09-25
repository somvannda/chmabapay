"""The worker process (P2-3).

Runs the same drain loops, heartbeat scheduler and alert watcher the API runs when
`WORKERS_ENABLED` is true — in a process of its own:

    WORKER_TRANSPORT=redis REDIS_URL=redis://redis:6379/0 \\
        python -m chmabapay.worker_main

The API then runs with `WORKERS_ENABLED=false` and the same `WORKER_TRANSPORT`, so it
enqueues and reports queue depth while this process drains. `docker compose` wires
exactly that (see the `worker` service).

**Why a separate process.** To scale drains independently of request handling. A
webhook fan-out job holds a slot for as long as a merchant's endpoint takes to
answer, and while the loops lived inside the API that time came out of the API's own
capacity — a slow merchant endpoint could delay the detection of incoming payments.

**Why it refuses `inprocess`.** An in-process transport here would be a private
queue in a process nothing else talks to: the API would enqueue into its own copy and
this process would drain nothing, forever, while reporting itself healthy. There is
no version of that which works, so it is an error rather than a fallback.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from types import FrameType

from . import observability
from .config import Settings, assert_runtime_matches_configuration, get_settings
from .db import ensure_schema
from .workers.runtime import build_transport, start_workers, worker_registry

log = logging.getLogger(__name__)


def _install_stop_signals(stop: asyncio.Event, loop: asyncio.AbstractEventLoop) -> None:
    """Set `stop` on SIGTERM/SIGINT, so shutdown drains rather than aborts.

    `loop.add_signal_handler` would be tidier but is Unix-only, and this also has to
    run on a developer's Windows machine. `signal.signal` works in both places; the
    event is set through `call_soon_threadsafe` because a signal handler is not
    running on the loop.
    """

    def _handle(_signum: int, _frame: FrameType | None) -> None:
        loop.call_soon_threadsafe(stop.set)

    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handle)
        except (ValueError, OSError):
            # Not the main thread, or the platform refuses it. Ctrl-C still raises
            # KeyboardInterrupt, which is handled below.
            log.debug("could not install a %s handler", name)


async def run_worker(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    observability.configure_logging()

    choice = (settings.worker_transport or "").strip().lower()
    if choice != "redis":
        log.error(
            "refusing to start: WORKER_TRANSPORT=%r. A standalone worker needs a "
            "queue that other processes can reach, so it must be 'redis'. With "
            "'inprocess' this process would drain a private queue while the API "
            "filled a different one.",
            choice,
        )
        return 2

    # Same guard the API runs, and it matters at least as much here: a worker
    # pointed at the other runtime's database drains a queue belonging to a stack
    # it cannot see, and reports healthy while doing it.
    assert_runtime_matches_configuration(settings)

    # Verify, never migrate: the schema path is Alembic's alone (P0-1), and a worker
    # booting onto a database behind the code would read columns that are not there.
    await ensure_schema()

    transport = build_transport(settings)
    await transport.ping()

    queues = list(worker_registry())
    log.info(
        "worker process up: transport=%s consumer drains %s",
        choice,
        ", ".join(queues),
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    _install_stop_signals(stop, loop)

    tasks = start_workers(transport, stop)
    try:
        await stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("worker process shutting down")
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await transport.aclose()
    return 0


def main() -> int:
    return asyncio.run(run_worker())


if __name__ == "__main__":
    raise SystemExit(main())
