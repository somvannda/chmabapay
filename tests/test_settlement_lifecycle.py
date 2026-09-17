"""What happens around a settlement: late money, refunds, double charges, and the
moment we stop looking.

Every case here came out of one real payment on 2026-09-17 that settled nine
minutes after ABA's own 180s window had closed on it. That single event exposed a
sequence of assumptions that only hold if a customer pays promptly:

  - the replacement code a merchant minted was still payable alongside the one that
    settled, so one sale had two live codes;
  - a refund had nowhere to be recorded, so revenue would have been overstated
    forever;
  - detection stopped at a hardcoded 900s and wrote down nothing about it stopping,
    so money accepted after that point would have existed in the merchant's ABA
    account and nowhere in ours.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import make_account, make_key, make_store
from sqlalchemy import select, update

from chmabapay import alerts, models
from chmabapay.db import session_factory
from chmabapay.services.status_reconciler import ReconcileResult
from chmabapay.workers import Q_DETECTION
from chmabapay.workers.job import Job
from chmabapay.workers.w1_payment_detection import PaymentDetectionWorker


async def _settle(client, public_id: str) -> None:
    response = await client.post(f"/_dev/payments/{public_id}/pay")
    assert response.status_code == 200, response.text


async def _rows(model, **filters):
    async with session_factory() as session:
        statement = select(model)
        for column, value in filters.items():
            statement = statement.where(getattr(model, column) == value)
        return list((await session.execute(statement)).scalars().all())


async def _row(public_id: str) -> models.Payment:
    async with session_factory() as session:
        return (
            await session.execute(
                select(models.Payment).where(models.Payment.public_id == public_id)
            )
        ).scalar_one()


async def _expire(public_id: str) -> None:
    async with session_factory() as session:
        await session.execute(
            update(models.Payment)
            .where(models.Payment.public_id == public_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
        from chmabapay.services.payments import expire_due_payments

        await expire_due_payments(session)


class RecordingTransport:
    """Captures enqueues instead of running them."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict]] = []

    async def enqueue(self, queue_name, *, payload, dedup_key=None):  # noqa: ARG002
        self.jobs.append((queue_name, payload))


# --------------------------------------------------------------------------- #
# A late settlement must not leave a second payable code behind
# --------------------------------------------------------------------------- #
async def test_a_late_settlement_retires_the_replacement_code(client):
    """The customer paid the original after the merchant had already reissued.

    That leaves two live ABA sessions for one sale. Settling the original has to
    retire the replacement, or a customer who pays the second one too has been
    charged twice for one purchase.
    """
    account = await make_account()
    await make_store(account, name="Late Store", owner="Late")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    original = (
        await client.post(
            "/v1/payments",
            json={"amount": 2.0, "reference_id": "order-late"},
            headers=headers,
        )
    ).json()
    await _expire(original["id"])

    successor = (
        await client.post(f"/v1/payments/{original['id']}/reissue", headers=headers)
    ).json()
    assert successor["id"] != original["id"]
    assert (await _row(successor["id"])).status == models.PAYMENT_PENDING

    # The customer pays the code the merchant had already given up on.
    await _settle(client, original["id"])

    assert (await _row(original["id"])).status == models.PAYMENT_PAID
    assert (await _row(successor["id"])).status == models.PAYMENT_SUPERSEDED

    # And the retired code stops being served, so it cannot be scanned again.
    withdrawn = await client.get(f"/pay/{successor['id']}/qr.svg")
    assert withdrawn.status_code == 410
    assert withdrawn.json()["detail"] == "payment_superseded"

    # The merchant is told, so the POS stops offering a code that is now dead.
    superseded = await _rows(models.Event, type=models.EVENT_SUPERSEDED)
    assert len(superseded) == 1
    assert superseded[0].payload["financial"] is False


# --------------------------------------------------------------------------- #
# Double charge
# --------------------------------------------------------------------------- #
async def test_two_settled_payments_for_one_order_are_reported(client, monkeypatch):
    """Two settled payments sharing a `reference_id` is one sale charged twice.

    Nothing can undo the charge, so the value is in saying so — loudly, with both
    payment ids, while someone can still refund the customer.
    """
    raised: list[tuple[str, str]] = []

    async def fake_alert(summary: str, detail: str) -> None:
        raised.append((summary, detail))

    monkeypatch.setattr(alerts, "alert_discrete", fake_alert)

    account = await make_account()
    await make_store(account, name="Double Store", owner="Double")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    first = (
        await client.post(
            "/v1/payments",
            json={"amount": 3.0, "reference_id": "order-dup"},
            headers=headers,
        )
    ).json()
    second = (
        await client.post(
            "/v1/payments",
            json={"amount": 3.0, "reference_id": "order-dup"},
            headers=headers,
        )
    ).json()

    await _settle(client, first["id"])
    # One settled payment for this order is a sale, not a problem.
    assert raised == []

    await _settle(client, second["id"])

    assert len(raised) == 1
    summary, detail = raised[0]
    assert summary == "double charge suspected"
    assert first["id"] in detail and second["id"] in detail
    assert "order-dup" in detail


async def test_the_double_charge_check_needs_an_order_identifier(client, monkeypatch):
    """Without a `reference_id` there is no basis for the claim.

    Matching on amount and time instead would fire on every two customers who buy
    the same thing at the same price — an alert nobody would keep reading.
    """
    raised: list[tuple[str, str]] = []

    async def fake_alert(summary: str, detail: str) -> None:
        raised.append((summary, detail))

    monkeypatch.setattr(alerts, "alert_discrete", fake_alert)

    account = await make_account()
    await make_store(account, name="Anon Store", owner="Anon")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    for _ in range(2):
        created = (
            await client.post("/v1/payments", json={"amount": 4.0}, headers=headers)
        ).json()
        await _settle(client, created["id"])

    assert raised == []


# --------------------------------------------------------------------------- #
# Reversal
# --------------------------------------------------------------------------- #
async def test_a_settled_payment_can_be_reversed(client):
    """A refund has to be recordable, or revenue is overstated permanently.

    ABA provides no callback and no reversal signal, so this is the only way the
    platform can learn that money went back.
    """
    account = await make_account()
    await make_store(account, name="Refund Store", owner="Refund")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    created = (
        await client.post(
            "/v1/payments",
            json={"amount": 9.5, "reference_id": "order-refund"},
            headers=headers,
        )
    ).json()
    await _settle(client, created["id"])

    reversed_response = await client.post(
        f"/v1/payments/{created['id']}/reverse",
        json={"reason": "customer returned the item"},
        headers=headers,
    )
    assert reversed_response.status_code == 200, reversed_response.text
    body = reversed_response.json()
    assert body["status"] == "reversed"
    assert body["reversed_at"] is not None
    # `paid_at` survives: the money did move, and then moved back. A report needs
    # both or it cannot tell a reversal from a sale that never happened.
    assert body["paid_at"] is not None

    row = await _row(created["id"])
    assert row.reversal_reason == "customer returned the item"

    events = await _rows(models.Event, type=models.EVENT_REVERSED)
    assert len(events) == 1
    # Financial in the opposite direction — this is the negative of a completion,
    # not an operational notice.
    assert events[0].payload["financial"] is True
    assert events[0].payload["data"]["payment"]["reversed_at"] is not None

    # And it is on the audit trail, in the same transaction as the reversal itself.
    audited = await _rows(models.AuditLog, action="payment.reversed")
    assert len(audited) == 1
    assert str(audited[0].target_id) == str(row.id)


async def test_reversing_is_refused_for_money_that_never_arrived(client):
    account = await make_account()
    await make_store(account, name="Unpaid Store", owner="Unpaid")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    pending = (
        await client.post("/v1/payments", json={"amount": 1.0}, headers=headers)
    ).json()

    response = await client.post(
        f"/v1/payments/{pending['id']}/reverse", json={}, headers=headers
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "payment_not_paid"


async def test_a_reversal_is_idempotent_and_scoped_to_the_owner(client):
    account = await make_account()
    await make_store(account, name="Once Store", owner="Once")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    created = (
        await client.post("/v1/payments", json={"amount": 2.5}, headers=headers)
    ).json()
    await _settle(client, created["id"])

    first = await client.post(f"/v1/payments/{created['id']}/reverse", json={}, headers=headers)
    assert first.status_code == 200
    again = await client.post(f"/v1/payments/{created['id']}/reverse", json={}, headers=headers)
    assert again.status_code == 409
    assert again.json()["detail"] == "payment_already_reversed"

    # One event, not two: a second reversal does not move money a second time.
    assert len(await _rows(models.Event, type=models.EVENT_REVERSED)) == 1

    other = await make_account(email="other-refund@chmaba.test", name="Other")
    other_key, _ = await make_key(other)
    cross = await client.post(
        f"/v1/payments/{created['id']}/reverse",
        json={},
        headers={"Authorization": f"Bearer {other_key}"},
    )
    assert cross.status_code == 404


async def test_a_reversed_payment_is_not_resurrected_by_a_later_poll(client):
    """ABA's session knows nothing about the refund and still answers `approved`.

    Without a terminal guard a later poll would quietly flip a refunded payment back
    to paid, and the reversal would vanish from every report that had already been
    built on it.
    """
    account = await make_account()
    await make_store(account, name="Resurrect Store", owner="Resurrect")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    created = (
        await client.post("/v1/payments", json={"amount": 5.0}, headers=headers)
    ).json()
    await _settle(client, created["id"])
    await client.post(f"/v1/payments/{created['id']}/reverse", json={}, headers=headers)

    from chmabapay.services.payments import mark_paid

    async with session_factory() as session:
        result = await mark_paid(session, created["id"], bakong_ref="late-arrival")

    assert result is None
    assert (await _row(created["id"])).status == models.PAYMENT_REVERSED


async def test_a_reversed_payment_cannot_be_reissued(client):
    account = await make_account()
    await make_store(account, name="No Reissue Store", owner="NoReissue")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    created = (
        await client.post("/v1/payments", json={"amount": 1.5}, headers=headers)
    ).json()
    await _settle(client, created["id"])
    await client.post(f"/v1/payments/{created['id']}/reverse", json={}, headers=headers)

    response = await client.post(
        f"/v1/payments/{created['id']}/reissue", headers=headers
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "payment_reversed"


# --------------------------------------------------------------------------- #
# Reporting contract
# --------------------------------------------------------------------------- #
async def test_expiry_is_not_a_financial_event_but_completion_is(client):
    """`payment.expired` is widely read as "sale lost". It is nothing of the sort.

    The same payment settled nine minutes after its expiry event fired, so a
    consumer booking revenue from this stream has to branch on `financial` rather
    than on the event name.
    """
    account = await make_account()
    await make_store(account, name="Report Store", owner="Report")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    created = (
        await client.post(
            "/v1/payments",
            json={"amount": 1.0, "reference_id": "order-report"},
            headers=headers,
        )
    ).json()
    await _expire(created["id"])

    expired = await _rows(models.Event, type=models.EVENT_EXPIRED)
    assert len(expired) == 1
    payload = expired[0].payload
    assert payload["financial"] is False
    # Not settled, so nothing claims a settlement date.
    assert payload["data"]["payment"]["settled_late"] is False
    assert payload["data"]["payment"]["paid_at"] is None

    # The money turns up anyway.
    await _settle(client, created["id"])

    completed = await _rows(models.Event, type=models.EVENT_COMPLETED)
    assert len(completed) == 1
    payload = completed[0].payload
    assert payload["financial"] is True
    payment = payload["data"]["payment"]
    # Both ends of the journey, so a report can attribute the sale and the cash
    # separately — for a late settlement those are different moments.
    assert payment["paid_at"] is not None
    assert payment["created_at"] is not None
    assert payment["expires_at"] is not None
    assert payment["settled_late"] is True


# --------------------------------------------------------------------------- #
# Usage ledger
# --------------------------------------------------------------------------- #
async def test_settling_appends_this_payments_own_amount_to_the_ledger(client):
    """One row per movement, carrying that movement's amount.

    The old writer inserted a blank row and then added the amount to *every* row for
    the account-month, so N payments left rows valued N, N-1, … 1 and a total of
    N(N+1)/2. A row names one payment in `resource_id`, so its numbers have to be
    that payment's.
    """
    account = await make_account()
    await make_store(account, name="Ledger Store", owner="Ledger")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    first = (
        await client.post(
            "/v1/payments", json={"amount": 1.0, "reference_id": "led-1"}, headers=headers
        )
    ).json()
    second = (
        await client.post(
            "/v1/payments", json={"amount": 2.0, "reference_id": "led-2"}, headers=headers
        )
    ).json()
    await _settle(client, first["id"])
    await _settle(client, second["id"])

    entries = await _rows(models.PlanLedgerEntry)
    # Two payments, two rows — not two rows plus a shared running total.
    assert len(entries) == 2
    assert sorted(e.amount_cents_delta for e in entries) == [100, 200]
    # Each row counts itself, never the period.
    assert {e.total_payments_count for e in entries} == {1}
    assert {e.total_volume_cents for e in entries} == {100, 200}
    assert {e.resource_type for e in entries} == {"payment"}


async def test_a_reversal_appends_a_negative_row_rather_than_editing(client):
    """The give-back is recorded, not merged away.

    Append-only is the point: a report can show both the credit and the refund, and a
    refunded payment stops counting toward the month's volume.
    """
    account = await make_account()
    await make_store(account, name="Ledger Refund Store", owner="LedgerRefund")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    created = (
        await client.post("/v1/payments", json={"amount": 7.5}, headers=headers)
    ).json()
    await _settle(client, created["id"])
    await client.post(f"/v1/payments/{created['id']}/reverse", json={}, headers=headers)

    entries = sorted(await _rows(models.PlanLedgerEntry), key=lambda e: e.id)
    assert [e.resource_type for e in entries] == ["payment", "payment_reversal"]
    assert [e.amount_cents_delta for e in entries] == [750, -750]
    # Net zero: the sale and its refund cancel out for the period.
    assert sum(e.amount_cents_delta for e in entries) == 0
    assert sum(e.total_payments_count for e in entries) == 0


async def test_the_same_movement_cannot_be_counted_twice():
    """The unique key is what made the writer's `ON CONFLICT DO NOTHING` meaningless.

    It could never fire, because the table had no constraint on the columns the clause
    named — the root cause of the inflation rather than a side detail. A retry now
    lands on the conflict and is dropped.
    """
    from chmabapay.services.payments import _record_plan_ledger_entry

    account = await make_account()
    store = await make_store(account, name="Idempotent Store", owner="Idempotent")
    payment = await _row(await _payment_at(store, age_seconds=0))

    for _ in range(3):
        async with session_factory() as session:
            await _record_plan_ledger_entry(session, account.id, payment)
            await session.commit()

    entries = await _rows(models.PlanLedgerEntry)
    assert len(entries) == 1
    assert entries[0].amount_cents_delta == 100


# --------------------------------------------------------------------------- #
# The detection window
# --------------------------------------------------------------------------- #
def _reconcile(status: str, error: str | None = None) -> ReconcileResult:
    return ReconcileResult(
        payment_id=1,
        payment_public_id="p",
        status=status,  # type: ignore[arg-type]
        source=None,
        matched_amount=None,
        error=error,
    )


async def test_a_sweep_queues_one_last_look_for_payments_leaving_the_window():
    """The boundary is a policy decision; failing to observe it is a bug.

    Every payment that crosses out of the window unsettled gets exactly one `final`
    job, and the gate is `detection_closed_at IS NULL` — so a job lost to a restart
    is retried by the next sweep rather than silently dropped.
    """
    account = await make_account()
    store = await make_store(account, name="Window Store", owner="Window")

    window = 3600
    crossing = await _payment_at(store, age_seconds=window + 60)
    inside = await _payment_at(store, age_seconds=window - 600)
    ancient = await _payment_at(store, age_seconds=window * 5)

    transport = RecordingTransport()
    queued = await PaymentDetectionWorker()._close_crossed_window(transport, _window(window))

    assert queued == 1
    assert len(transport.jobs) == 1
    queue_name, payload = transport.jobs[0]
    assert queue_name == Q_DETECTION
    assert payload == {"payment_public_id": crossing, "final": True}
    # Still under watch, too old to bother with.
    assert inside not in [p["payment_public_id"] for _, p in transport.jobs]
    assert ancient not in [p["payment_public_id"] for _, p in transport.jobs]

    # And the same row is not queued again until that job actually runs.
    transport2 = RecordingTransport()
    assert await PaymentDetectionWorker()._close_crossed_window(transport2, _window(window)) == 1
    async with session_factory() as session:
        row = (
            await session.execute(
                select(models.Payment).where(models.Payment.public_id == crossing)
            )
        ).scalar_one()
        row.detection_closed_at = datetime.now(UTC)
        await session.commit()
    transport3 = RecordingTransport()
    assert await PaymentDetectionWorker()._close_crossed_window(transport3, _window(window)) == 0


async def test_closing_the_window_escalates_only_the_case_we_cannot_answer():
    """Three outcomes, and only one of them is worth waking someone for.

    Settled needs nothing said. Answered-but-unpaid is the ordinary fate of an
    abandoned QR and alerting on it would make the channel useless. No answer is
    different in kind: we are about to stop looking without knowing whether money
    moved, and the merchant has no way to discover that either.
    """
    account = await make_account()
    store = await make_store(account, name="Close Store", owner="Close")
    public_id = await _payment_at(store, age_seconds=60)
    payment = await _row(public_id)
    worker = PaymentDetectionWorker()

    attempt: dict = {}
    assert worker._close_detection_window(payment, _reconcile("PAID"), attempt) is None
    assert payment.detection_closed_at is not None
    assert attempt["final"] is True
    assert "settled" in attempt["note"]

    payment.detection_closed_at = None
    attempt = {}
    assert worker._close_detection_window(payment, _reconcile("PENDING"), attempt) is None
    assert "not paid" in attempt["note"]

    payment.detection_closed_at = None
    attempt = {}
    escalated = worker._close_detection_window(
        payment, _reconcile("PENDING", error="payway_hosted_unavailable"), attempt
    )
    assert escalated is not None
    summary, detail = escalated
    assert summary == "payment outcome unknown"
    assert public_id in detail
    assert "may have paid" in detail


async def test_a_final_job_that_cannot_get_an_answer_records_and_alerts(client, monkeypatch):
    """The wiring, not just the decision.

    A `final` job must stamp the closure and escalate in one pass, and the stamp
    must survive the transaction — otherwise the next sweep re-queues the same row
    forever, or worse, never revisits it.
    """
    raised: list[tuple[str, str]] = []

    async def fake_alert(summary: str, detail: str) -> None:
        raised.append((summary, detail))

    monkeypatch.setattr(alerts, "alert_discrete", fake_alert)

    from chmabapay.workers import w1_payment_detection as w1

    async def stays_unanswered(payment, **kwargs):  # noqa: ARG001
        return _reconcile("PENDING", error="payway_hosted_unavailable: no answer")

    monkeypatch.setattr(w1, "reconcile_payment", stays_unanswered)

    account = await make_account()
    store = await make_store(account, name="Alert Store", owner="Alert")
    public_id = await _payment_at(store, age_seconds=7200)

    result = await PaymentDetectionWorker().process(
        Job(
            queue_name=Q_DETECTION,
            payload={"payment_public_id": public_id, "final": True},
        )
    )

    assert result["final"] is True
    assert len(raised) == 1
    assert raised[0][0] == "payment outcome unknown"
    assert public_id in raised[0][1]

    row = await _row(public_id)
    # Stamped, so the sweep stops considering it closed-but-unclosed.
    assert row.detection_closed_at is not None
    # And the closure is on the payment's own record, with the reason.
    final_entry = row.attempt_history[-1]
    assert final_entry["final"] is True
    assert "closed unresolved" in final_entry["note"]


def _window(seconds: int) -> timedelta:
    return timedelta(seconds=seconds)


async def _payment_at(store, *, age_seconds: int) -> str:
    """A pending, hosted payment that is `age_seconds` old.

    Hosted on purpose: `_process_one` refuses to reconcile anything without either a
    hosted session or Bakong credentials, and this suite runs with neither.
    """
    from chmabapay.services import payments as svc

    async with session_factory() as session:
        link = await svc.load_active_link(session, store.id)
        public_id = svc.gen_public_id()
        session.add(
            models.Payment(
                public_id=public_id,
                store_id=store.id,
                payment_link_id=link.id,
                amount_cents=100,
                currency="USD",
                status=models.PAYMENT_PENDING,
                qr_string="00020101021229",
                bill_number=public_id,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
                created_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
                gateway_status_raw={
                    "payway_hosted": {
                        "client_id": "test-client",
                        "token": "test-token",
                        "request_time": "1700000000000",
                    }
                },
            )
        )
        await session.commit()
        return public_id
