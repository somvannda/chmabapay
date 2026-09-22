"""The two W1 sweeps must cover different populations, not the same one faster.

W1 is the only thing that asks ABA whether a pending payment has settled, so its
cadence *is* the latency a customer sees — both on the checkout page and in the
merchant's Telegram alert, which are emitted by the same settlement. Measured on the
live rail 2026-09-22, a settled payment's own attempt log showed polls at 18.4s, 48.4s
and 78.4s after its code was issued: exactly 30.0s apart, the settlement landing on
the third. The customer had paid before that poll.

Tightening that one interval would work and would be wrong: the sweep covers every
unsettled payment in the hour-wide detection window, so a faster cadence would poll
abandoned codes twelve times a minute instead of twice to serve the one code somebody
is watching. Hence a second sweep, scoped by age. These tests pin the scoping, because
a `max_age_seconds` that silently stops filtering would look like nothing at all —
just a larger ABA bill.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_account, make_store

from chmabapay import models, workers
from chmabapay.db import session_factory
from chmabapay.services import stores as store_svc
from chmabapay.workers.job import Job
from chmabapay.workers.w1_payment_detection import QUEUE, PaymentDetectionWorker


class _RecordingTransport:
    """Captures the ids a sweep decides to poll."""

    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, queue: str, *, payload: dict, dedup_key=None) -> None:
        assert queue == QUEUE
        self.enqueued.append(payload["payment_public_id"])


@pytest.fixture
def sweep(monkeypatch):
    """A worker wired to a recording transport instead of the real queue."""
    transport = _RecordingTransport()
    monkeypatch.setattr(workers, "_GLOBAL_TRANSPORT", transport)
    return PaymentDetectionWorker(), transport


async def _payment(public_id: str, *, age_seconds: float) -> None:
    """A pending payment that was created `age_seconds` ago."""
    account = await make_account(email=f"{public_id}@sweep.test", name="Sweep")
    store = await make_store(account, name=f"Store {public_id}", external_id=public_id)
    async with session_factory() as session:
        link = await store_svc.load_link(session, store.id)
        assert link is not None
        session.add(
            models.Payment(
                public_id=public_id,
                store_id=store.id,
                payment_link_id=link.id,
                amount_cents=100,
                status=models.PAYMENT_PENDING,
                qr_string="00020101021229",
                bill_number=f"bill-{public_id}",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
                created_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
            )
        )
        await session.commit()


def _job(payload: dict) -> Job:
    return Job(queue_name=QUEUE, payload=payload)


async def test_the_young_sweep_skips_a_code_whose_window_has_closed(sweep):
    """The whole point: a faster cadence that covers everything is just more calls."""
    worker, transport = sweep
    await _payment("pay_young", age_seconds=30)
    await _payment("pay_old", age_seconds=30 * 60)

    await worker._orphan_scan(
        _job(
            {
                "type": "orphan_scan_pending",
                "max_age_seconds": 180,
                "batch_size": 25,
            }
        )
    )

    assert transport.enqueued == ["pay_young"]


async def test_the_general_sweep_still_covers_the_whole_window(sweep):
    """The old code stays under watch — it is where a late settlement lands."""
    worker, transport = sweep
    # Created in age order, so id order and "oldest first" agree and the assertion
    # below reads as the query's own intent rather than as an accident of insertion.
    await _payment("pay_old", age_seconds=30 * 60)
    await _payment("pay_young", age_seconds=30)

    await worker._orphan_scan(_job({"type": "orphan_scan_pending"}))

    # Oldest first: those are closest to leaving the detection window.
    assert transport.enqueued == ["pay_old", "pay_young"]


async def test_the_batch_size_bounds_one_sweep(sweep):
    """A burst of new payments must not become an unbounded run of ABA calls."""
    worker, transport = sweep
    for index in range(4):
        await _payment(f"pay_burst_{index}", age_seconds=30)

    await worker._orphan_scan(
        _job(
            {
                "type": "orphan_scan_pending",
                "max_age_seconds": 180,
                "batch_size": 2,
            }
        )
    )

    assert len(transport.enqueued) == 2
