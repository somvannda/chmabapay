"""The compliance baseline P1-4 can actually enforce (P1-4).

Two things live here, and they are different in kind:

* **Acceptance of the merchant agreement.** The roadmap records that merchant due
  diligence is the operator's own legal duty rather than a platform feature, and
  migration 6 deleted every identity column to match. What is left for the
  platform to hold is the *evidence* of consent — which only the platform can
  hold, and which is worthless if it cannot name the text agreed to.
* **Retention of raw gateway payloads.** `payments.gateway_status_raw` carries the
  ABA session token verbatim. "Defined and enforced" is the standard, and this is
  the enforced half.

Deliberately absent: a test that a restricted business cannot be activated. There
is no business-category field to test, by decision — see P1-4 in
docs/production-readiness.md. Writing a test that appeared to cover it would be
worse than not having one.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from conftest import make_account, make_store
from sqlalchemy import select

from chmabapay import models
from chmabapay.config import get_settings
from chmabapay.db import session_factory
from chmabapay.security import hash_password
from chmabapay.services.retention import purge_gateway_payloads
from chmabapay.workers import Q_RETENTION, RetentionSweeperWorker
from chmabapay.workers.job import Job
from chmabapay.workers.w5_retention_sweeper import (
    retention_heartbeat_dedup_key,
    retention_heartbeat_job_payload,
)

PAYLOAD = {"payway_hosted": {"token": "session-token-abc", "client_id": "cid-1"}}

PASSWORD = "correct horse battery"


async def _signed_in(client, email: str = "sokha@example.com"):
    """Sign in for real and return the account's /v1/me profile.

    A password sign-in rather than a hand-minted cookie, so the acceptance below
    is reached the way a merchant reaches it — through the session dependency,
    with the cookie doing a real round trip.
    """
    account = await make_account(email=email, name="Sokha", terms_accepted=False)
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        row.password_hash = hash_password(PASSWORD)
        await session.commit()

    sign_in = await client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert sign_in.status_code == 200, sign_in.text
    return (await client.get("/v1/me")).json()


async def _audit_rows(action: str) -> list[models.AuditLog]:
    async with session_factory() as session:
        res = await session.execute(
            select(models.AuditLog).where(models.AuditLog.action == action)
        )
        return list(res.scalars().all())


async def _payment(*, days_old: int, payload: dict | None = PAYLOAD) -> int:
    """A terminal payment aged into (or out of) the retention window.

    Built directly rather than through the API: the subject here is the purge, and
    routing it through a hosted checkout would couple these tests to ABA's
    response shape for no benefit. `created_at` is what the window is measured
    against, so it is the field that gets backdated.
    """
    account = await make_account(email=f"ret-{os.urandom(4).hex()}@chmaba.test")
    await make_store(account, name="Sokha Cafe", owner="Sokha")
    created = datetime.now(UTC) - timedelta(days=days_old)

    async with session_factory() as session:
        store = (
            await session.execute(
                select(models.Store).where(models.Store.account_id == account.id)
            )
        ).scalar_one()
        link = (
            await session.execute(
                select(models.PaymentLink).where(
                    models.PaymentLink.store_id == store.id
                )
            )
        ).scalar_one()
        payment = models.Payment(
            public_id=f"pay_{os.urandom(8).hex()}",
            store_id=store.id,
            payment_link_id=link.id,
            amount_cents=2500,
            status=models.PAYMENT_EXPIRED,
            qr_string="00020101021229",
            bill_number="bill-0001",
            expires_at=created + timedelta(minutes=5),
            created_at=created,
            bakong_ref="ref-keep-me",
            gateway_status_raw=payload,
            attempt_history=[{"type": "poll", "note": "pending"}],
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        return payment.id


async def _reload(payment_id: int) -> models.Payment:
    async with session_factory() as session:
        payment = await session.get(models.Payment, payment_id)
        assert payment is not None
        session.expunge(payment)
        return payment


# --------------------------------------------------------------------------- #
# Merchant agreement acceptance
# --------------------------------------------------------------------------- #
async def test_a_new_account_has_agreed_to_nothing(client):
    """Null, not a backfilled timestamp. Inventing consent is the one thing an
    acceptance record must never do — it is a record of something that happened."""
    profile = await _signed_in(client)

    assert profile["terms_accepted_at"] is None
    assert profile["terms_accepted_version"] is None
    assert profile["terms_required_version"] == get_settings().terms_version


async def test_accepting_the_terms_records_the_version_and_the_moment(client):
    await _signed_in(client)

    response = await client.post(
        "/v1/me/terms", json={"version": get_settings().terms_version}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["terms_accepted_version"] == get_settings().terms_version
    assert body["terms_accepted_at"] is not None

    # And it persisted, rather than only being echoed back.
    profile = (await client.get("/v1/me")).json()
    assert profile["terms_accepted_version"] == get_settings().terms_version
    assert profile["terms_accepted_at"] is not None


async def test_the_acceptance_is_attributable_to_the_merchant(client):
    profile = await _signed_in(client)
    await client.post("/v1/me/terms", json={"version": get_settings().terms_version})

    rows = await _audit_rows("account.terms_accepted")
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_account_id == profile["id"]
    assert row.target_type == "Account"
    assert row.target_id == profile["id"]
    assert row.details == {"version": get_settings().terms_version}


async def test_a_superseded_version_is_refused_rather_than_recorded(client):
    """The reason the client sends a version at all.

    Recording whichever version the server is publishing would attribute a stale
    page's acceptance to text the merchant never saw. A 409 makes the client
    reload instead of the platform fabricating consent.
    """
    profile = await _signed_in(client)

    response = await client.post("/v1/me/terms", json={"version": "0"})
    assert response.status_code == 409
    assert response.json()["detail"] == "terms_version_superseded"

    assert (await client.get("/v1/me")).json()["terms_accepted_version"] is None
    assert await _audit_rows("account.terms_accepted") == []
    assert profile["terms_required_version"] != "0"


async def test_accepting_the_same_version_twice_writes_one_audit_row(client):
    """A reload is not a second event. Duplicate consents make the trail longer,
    not more complete."""
    await _signed_in(client)
    version = get_settings().terms_version

    assert (await client.post("/v1/me/terms", json={"version": version})).status_code == 200
    assert (await client.post("/v1/me/terms", json={"version": version})).status_code == 200

    assert len(await _audit_rows("account.terms_accepted")) == 1


async def test_a_new_published_version_makes_the_old_acceptance_visible_as_stale(
    client, monkeypatch
):
    """`terms_accepted_version` earns its column here.

    A timestamp alone cannot tell an account that agreed to the current text from
    one that agreed to a draft we later replaced. With the version stored, the
    required re-acceptance is a comparison rather than a guess.
    """
    await _signed_in(client)
    monkeypatch.setattr(get_settings(), "terms_version", "1", raising=False)
    await client.post("/v1/me/terms", json={"version": "1"})

    monkeypatch.setattr(get_settings(), "terms_version", "2", raising=False)

    profile = (await client.get("/v1/me")).json()
    assert profile["terms_accepted_version"] == "1"
    assert profile["terms_required_version"] == "2"

    # Old text is refused; the new one is accepted and recorded as a second event.
    assert (await client.post("/v1/me/terms", json={"version": "1"})).status_code == 409
    accepted = await client.post("/v1/me/terms", json={"version": "2"})
    assert accepted.status_code == 200
    assert accepted.json()["terms_accepted_version"] == "2"
    actions = await _audit_rows("account.terms_accepted")
    assert sorted(r.details["version"] for r in actions) == ["1", "2"]


async def test_a_key_cannot_be_minted_before_the_terms_are_accepted(client):
    """The gate has to be on the server, or it is only a suggestion.

    An account can sign up, then integrate with the API without ever opening the
    dashboard. If acceptance were enforced only by the dashboard, that merchant
    would never see the agreement — so an API key, which is the credential the
    integration needs, is refused until they have.
    """
    profile = await _signed_in(client)
    assert profile["terms_accepted_version"] is None

    refused = await client.post("/v1/keys", json={"name": "first key"})
    assert refused.status_code == 403
    assert refused.json()["detail"] == "terms_not_accepted"

    # Accepting is the only thing that changes the answer.
    accepted = await client.post(
        "/v1/me/terms", json={"version": get_settings().terms_version}
    )
    assert accepted.status_code == 200
    assert (await client.post("/v1/keys", json={"name": "first key"})).status_code == 201


async def test_republishing_the_agreement_asks_for_consent_again(client, monkeypatch):
    """A merchant who agreed to version 1 must not be treated as having agreed to
    version 2 — the reason `terms_accepted_version` is compared and not just
    `terms_accepted_at` checked for null."""
    await _signed_in(client)
    monkeypatch.setattr(get_settings(), "terms_version", "1", raising=False)
    assert (await client.post("/v1/me/terms", json={"version": "1"})).status_code == 200

    monkeypatch.setattr(get_settings(), "terms_version", "2", raising=False)
    blocked = await client.post("/v1/keys", json={"name": "after republish"})
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "terms_not_accepted"

    assert (await client.post("/v1/me/terms", json={"version": "2"})).status_code == 200
    assert (
        await client.post("/v1/keys", json={"name": "after republish"})
    ).status_code == 201


async def test_terms_cannot_be_accepted_without_a_session(client):
    """An anonymous caller accepting the merchant agreement would be meaningless:
    there is no merchant to attribute it to."""
    assert (await client.post("/v1/me/terms", json={"version": "1"})).status_code == 401


async def test_a_malformed_version_is_rejected_before_it_reaches_the_record(client):
    await _signed_in(client)

    assert (await client.post("/v1/me/terms", json={"version": ""})).status_code == 422
    assert (await client.post("/v1/me/terms", json={})).status_code == 422


# --------------------------------------------------------------------------- #
# Retention of raw gateway payloads
# --------------------------------------------------------------------------- #
async def test_a_payload_older_than_the_window_is_purged():
    payment_id = await _payment(days_old=91)

    async with session_factory() as session:
        assert await purge_gateway_payloads(session, older_than_days=90) == 1

    assert (await _reload(payment_id)).gateway_status_raw is None


async def test_a_payload_inside_the_window_is_kept():
    """The other side of the boundary. A purge that took everything would pass a
    test that only ever checked the old row disappeared."""
    payment_id = await _payment(days_old=89)

    async with session_factory() as session:
        assert await purge_gateway_payloads(session, older_than_days=90) == 0

    assert (await _reload(payment_id)).gateway_status_raw == PAYLOAD


async def test_the_purge_leaves_the_accounting_record_alone():
    """Only the credential column is cleared. Amounts, references and the QR are
    the record of a real payment and are not ours to delete."""
    payment_id = await _payment(days_old=200)

    async with session_factory() as session:
        await purge_gateway_payloads(session, older_than_days=90)

    payment = await _reload(payment_id)
    assert payment.gateway_status_raw is None
    assert payment.amount_cents == 2500
    assert payment.bakong_ref == "ref-keep-me"
    assert payment.qr_string == "00020101021229"
    assert payment.status == models.PAYMENT_EXPIRED
    # Derived detection signals, not raw gateway responses — there is no
    # credential in here, so it is kept.
    assert payment.attempt_history == [{"type": "poll", "note": "pending"}]


async def test_a_disabled_window_refuses_rather_than_purging_everything():
    """`0` must not read as "delete all history". A misconfiguration that wipes
    every stored payload is the one outcome this function cannot allow."""
    payment_id = await _payment(days_old=5000)

    async with session_factory() as session:
        assert await purge_gateway_payloads(session, older_than_days=0) == 0

    assert (await _reload(payment_id)).gateway_status_raw == PAYLOAD


async def test_the_sweep_uses_the_configured_window(monkeypatch):
    """The window is a policy number in settings, not a constant buried in the
    worker — otherwise changing the policy would not change the behaviour."""
    await _payment(days_old=40)

    monkeypatch.setattr(get_settings(), "retention_gateway_raw_days", 30)
    result = await RetentionSweeperWorker().process(
        Job(queue_name=Q_RETENTION, payload=retention_heartbeat_job_payload())
    )

    assert result["purged"] == 1
    assert result["older_than_days"] == 30


async def test_the_sweep_is_idempotent(monkeypatch):
    """It runs on every boot as well as daily, so a second run in the same window
    must find nothing left to do rather than re-reporting the same rows."""
    await _payment(days_old=200)

    monkeypatch.setattr(get_settings(), "retention_gateway_raw_days", 90)
    worker = RetentionSweeperWorker()
    job = Job(queue_name=Q_RETENTION, payload=retention_heartbeat_job_payload())

    assert (await worker.process(job))["purged"] == 1
    assert (await worker.process(job))["purged"] == 0


async def test_the_sweep_does_not_race_itself_across_replicas():
    """A day-granularity dedup key is what stops two replicas purging the same
    day's batch twice once P2-3 scales the workers out."""
    same_day = datetime(2026, 9, 15, 1, 0, tzinfo=UTC)
    later_same_day = datetime(2026, 9, 15, 23, 59, tzinfo=UTC)
    next_day = datetime(2026, 9, 16, 0, 1, tzinfo=UTC)

    assert retention_heartbeat_dedup_key(same_day) == retention_heartbeat_dedup_key(
        later_same_day
    )
    assert retention_heartbeat_dedup_key(same_day) != retention_heartbeat_dedup_key(
        next_day
    )
