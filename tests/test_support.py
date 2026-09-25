"""Support ticketing: the merchant API, the operator queue, and the notifications (F-03…F-09).

The rules these assert are the feature's contract: `priority` is derived from the plan at
open time, `first_response_at` is written once by the first *operator* reply, the queue
puts priority above age, and every operator write lands in the audit trail. The tenancy
case is the one a support thread makes dangerous — it carries whatever a merchant typed,
so a cross-account read by public id must be a 404 like every other merchant route.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from conftest import BASE_URL, make_account, make_key, session_client
from sqlalchemy import select

from chmabapay import models
from chmabapay.db import seed_default_plans, session_factory
from chmabapay.main import app
from chmabapay.security import hash_password
from chmabapay.services import resend as resend_svc
from chmabapay.services import support as support_svc

PASSWORD = "correct horse battery"


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def seed_plans() -> None:
    async with session_factory() as session:
        await seed_default_plans(session)
        await session.commit()


async def subscribe(account: models.Account, plan_code: str) -> None:
    async with session_factory() as session:
        plan = (
            await session.execute(
                select(models.Plan).where(models.Plan.code == plan_code)
            )
        ).scalar_one()
        session.add(
            models.PlanSubscription(
                account_id=account.id,
                plan_id=plan.id,
                status="active",
                next_billing_at=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await session.commit()


async def make_admin(email: str = "duke@chmaba.test") -> models.Account:
    account = await make_account(email=email, name="Duke")
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        row.is_platform_admin = True
        row.password_hash = hash_password(PASSWORD)
        await session.commit()
    return account


async def sign_in(client: httpx.AsyncClient, account: models.Account) -> None:
    res = await client.post(
        "/auth/login", json={"email": account.email, "password": PASSWORD}
    )
    assert res.status_code == 200


async def open_ticket(
    client: httpx.AsyncClient, key: str, *, subject: str = "QR will not scan"
) -> dict:
    res = await client.post(
        "/api/v1/support/requests",
        json={"subject": subject, "category": "payment", "body": "It will not scan."},
        headers=bearer(key),
    )
    assert res.status_code == 201, res.text
    return res.json()


async def audit_rows(action: str) -> list[models.AuditLog]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(models.AuditLog)
                    .where(models.AuditLog.action == action)
                    .order_by(models.AuditLog.id)
                )
            )
            .scalars()
            .all()
        )


# --------------------------------------------------------------------------- #
# Merchant API
# --------------------------------------------------------------------------- #
async def test_a_request_opens_with_its_first_message_and_reads_back(client):
    account = await make_account()
    raw_key, _ = await make_key(account)

    created = await open_ticket(client, raw_key)
    assert created["status"] == "open"
    assert created["priority"] == "standard"
    # No plan subscription → best effort, no target.
    assert created["response_target_hours"] is None
    assert created["messages"][0]["author_kind"] == "merchant"
    assert created["messages"][0]["body"] == "It will not scan."

    listed = await client.get("/api/v1/support/requests", headers=bearer(raw_key))
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["data"]] == [created["id"]]

    detail = await client.get(
        f"/api/v1/support/requests/{created['id']}", headers=bearer(raw_key)
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["subject"] == "QR will not scan"
    assert body["messages"][0]["body"] == "It will not scan."


async def test_an_unknown_category_is_refused(client):
    account = await make_account()
    raw_key, _ = await make_key(account)

    res = await client.post(
        "/api/v1/support/requests",
        json={"subject": "Hi", "category": "not_a_category", "body": "hello"},
        headers=bearer(raw_key),
    )
    assert res.status_code == 422


async def test_a_cross_account_read_and_reply_are_404(client):
    """A support thread carries what the merchant typed, so it is scoped like any other."""
    owner = await make_account(email="owner@chmaba.test", name="Owner")
    other = await make_account(email="other@chmaba.test", name="Other")
    owner_key, _ = await make_key(owner)
    other_key, _ = await make_key(other)

    created = await open_ticket(client, owner_key)

    read = await client.get(
        f"/api/v1/support/requests/{created['id']}", headers=bearer(other_key)
    )
    assert read.status_code == 404

    reply = await client.post(
        f"/api/v1/support/requests/{created['id']}/reply",
        json={"body": "let me in"},
        headers=bearer(other_key),
    )
    assert reply.status_code == 404


async def test_a_restricted_account_can_read_but_not_open(client):
    account = await make_account()
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        row.status = models.ACCOUNT_RESTRICTED
        await session.commit()

    async with session_client(account) as api:
        listed = await api.get("/api/v1/support/requests")
        assert listed.status_code == 200

        opened = await api.post(
            "/api/v1/support/requests",
            json={"subject": "Please help", "category": "billing", "body": "Hi"},
        )
        assert opened.status_code == 403
        assert opened.json()["detail"] == "account_restricted"


# --------------------------------------------------------------------------- #
# Priority and first response
# --------------------------------------------------------------------------- #
async def test_a_pro_request_is_priority_and_sorts_above_an_older_free_request(client):
    await seed_plans()
    free = await make_account(email="free@chmaba.test", name="Free Co")
    free_key, _ = await make_key(free)
    pro = await make_account(email="pro@chmaba.test", name="Pro Co")
    await subscribe(pro, "pro")
    pro_key, _ = await make_key(pro)

    # Free opens first, so age alone would put it at the top.
    older = await open_ticket(client, free_key, subject="Old free question")
    newer = await open_ticket(client, pro_key, subject="New pro question")

    assert older["priority"] == "standard"
    assert newer["priority"] == "priority"
    assert newer["response_target_hours"] == 24

    admin = await make_admin()
    await sign_in(client, admin)
    queue = await client.get("/api/v1/admin/support/requests")
    assert queue.status_code == 200
    assert [row["id"] for row in queue.json()["data"]] == [newer["id"], older["id"]]


async def test_the_queue_puts_unanswered_before_answered(client):
    """The second half of the ordering: within a priority class, no-op-yet rows win."""
    merchant = await make_account()
    merchant_key, _ = await make_key(merchant)
    admin = await make_admin()

    answered_first = await open_ticket(client, merchant_key, subject="Older, answered")
    still_open = await open_ticket(client, merchant_key, subject="Newer, unanswered")

    await sign_in(client, admin)
    assert (
        await client.post(
            f"/api/v1/admin/support/requests/{answered_first['id']}/reply",
            json={"body": "Done."},
        )
    ).status_code == 200

    queue = await client.get("/api/v1/admin/support/requests")
    assert [row["id"] for row in queue.json()["data"]] == [
        still_open["id"],
        answered_first["id"],
    ]


async def test_first_response_is_set_once_by_the_first_operator_reply(client):
    merchant = await make_account()
    merchant_key, _ = await make_key(merchant)
    admin = await make_admin()

    created = await open_ticket(client, merchant_key)
    public_id = created["id"]

    # A merchant reply is not a response from the platform, so it must not start the clock.
    await sign_in(client, admin)
    merchant_reply_early = await client.post(
        f"/api/v1/support/requests/{public_id}/reply",
        json={"body": "extra detail"},
        headers=bearer(merchant_key),
    )
    assert merchant_reply_early.status_code == 200
    assert merchant_reply_early.json()["first_response_at"] is None

    first_reply = await client.post(
        f"/api/v1/admin/support/requests/{public_id}/reply",
        json={"body": "We are looking into it."},
    )
    assert first_reply.status_code == 200
    first_response_at = first_reply.json()["first_response_at"]
    assert first_response_at is not None
    # The operator's answer moves the ball back to the merchant.
    assert first_reply.json()["status"] == "pending"

    # A merchant reply reopens the thread and still does not touch the clock.
    merchant_reply = await client.post(
        f"/api/v1/support/requests/{public_id}/reply",
        json={"body": "still broken"},
        headers=bearer(merchant_key),
    )
    assert merchant_reply.status_code == 200
    assert merchant_reply.json()["status"] == "open"
    assert merchant_reply.json()["first_response_at"] == first_response_at

    # A second operator reply is not a second first response.
    second_reply = await client.post(
        f"/api/v1/admin/support/requests/{public_id}/reply",
        json={"body": "Fixed now."},
    )
    assert second_reply.status_code == 200
    assert second_reply.json()["first_response_at"] == first_response_at


# --------------------------------------------------------------------------- #
# Operator queue
# --------------------------------------------------------------------------- #
async def test_the_queue_filters_by_status_priority_and_account_and_clamps_paging(client):
    await seed_plans()
    free = await make_account(email="free@chmaba.test", name="Free Co")
    free_key, _ = await make_key(free)
    pro = await make_account(email="pro@chmaba.test", name="Pro Co")
    await subscribe(pro, "pro")
    pro_key, _ = await make_key(pro)

    free_ticket = await open_ticket(client, free_key, subject="Free question")
    pro_ticket = await open_ticket(client, pro_key, subject="Pro question")

    admin = await make_admin()
    await sign_in(client, admin)
    resolved = await client.patch(
        f"/api/v1/admin/support/requests/{free_ticket['id']}",
        json={"status": "resolved"},
    )
    assert resolved.status_code == 200

    by_priority = await client.get(
        "/api/v1/admin/support/requests", params={"priority": "priority"}
    )
    assert [row["id"] for row in by_priority.json()["data"]] == [pro_ticket["id"]]

    by_status = await client.get(
        "/api/v1/admin/support/requests", params={"status": "resolved"}
    )
    assert [row["id"] for row in by_status.json()["data"]] == [free_ticket["id"]]

    by_account = await client.get(
        "/api/v1/admin/support/requests", params={"account_id": free.id}
    )
    assert [row["id"] for row in by_account.json()["data"]] == [free_ticket["id"]]

    clamped = await client.get("/api/v1/admin/support/requests", params={"per_page": 500})
    assert clamped.json()["pagination"]["per_page"] == 100


async def test_an_unauthenticated_admin_call_is_401():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as anonymous:
        assert (
            await anonymous.get("/api/v1/admin/support/requests")
        ).status_code == 401


async def test_every_operator_write_writes_an_audit_row_naming_the_admin(client):
    merchant = await make_account()
    merchant_key, _ = await make_key(merchant)
    admin = await make_admin()

    created = await open_ticket(client, merchant_key)
    public_id = created["id"]

    await sign_in(client, admin)
    assert (
        await client.post(
            f"/api/v1/admin/support/requests/{public_id}/reply",
            json={"body": "On it."},
        )
    ).status_code == 200
    assert (
        await client.patch(
            f"/api/v1/admin/support/requests/{public_id}",
            json={"assigned_admin_account_id": admin.id},
        )
    ).status_code == 200
    assert (
        await client.patch(
            f"/api/v1/admin/support/requests/{public_id}", json={"status": "resolved"}
        )
    ).status_code == 200

    replied = await audit_rows("support.replied")
    assigned = await audit_rows("support.assigned")
    status_changed = await audit_rows("support.status_changed")

    assert len(replied) == 1
    assert len(assigned) == 1
    assert len(status_changed) == 1
    for entry in (replied[0], assigned[0], status_changed[0]):
        assert entry.actor_account_id == admin.id
        assert entry.target_type == "SupportRequest"
    assert assigned[0].details == {"from": None, "to": admin.id}
    assert status_changed[0].details["to"] == "resolved"


async def test_assigning_a_non_admin_is_refused(client):
    merchant = await make_account()
    merchant_key, _ = await make_key(merchant)
    other = await make_account(email="other@chmaba.test", name="Other")
    admin = await make_admin()

    created = await open_ticket(client, merchant_key)
    await sign_in(client, admin)
    res = await client.patch(
        f"/api/v1/admin/support/requests/{created['id']}",
        json={"assigned_admin_account_id": other.id},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "invalid_assignee"


# --------------------------------------------------------------------------- #
# Notifications (F-09 / PA-42)
# --------------------------------------------------------------------------- #
async def test_an_operator_reply_emails_the_merchant(client, monkeypatch):
    merchant = await make_account()
    merchant_key, _ = await make_key(merchant)
    admin = await make_admin()
    created = await open_ticket(client, merchant_key)

    sent: list[dict] = []

    async def fake_send_email(**kwargs):
        sent.append(kwargs)
        return "re_123"

    monkeypatch.setattr(resend_svc, "is_configured", lambda: True)
    monkeypatch.setattr(resend_svc, "send_email", fake_send_email)

    await sign_in(client, admin)
    res = await client.post(
        f"/api/v1/admin/support/requests/{created['id']}/reply",
        json={"body": "Here is your answer."},
    )
    assert res.status_code == 200
    assert len(sent) == 1
    assert sent[0]["to"] == merchant.email
    assert "QR will not scan" in sent[0]["subject"]


async def test_a_priority_open_alerts_the_operator_channel(client, monkeypatch):
    await seed_plans()
    free = await make_account(email="free@chmaba.test", name="Free Co")
    free_key, _ = await make_key(free)
    pro = await make_account(email="pro@chmaba.test", name="Pro Co")
    await subscribe(pro, "pro")
    pro_key, _ = await make_key(pro)

    alerts: list[str] = []

    async def fake_notify_activity(text: str) -> bool:
        alerts.append(text)
        return True

    monkeypatch.setattr(
        support_svc.notifications, "notify_activity", fake_notify_activity
    )

    await open_ticket(client, free_key, subject="Free question")
    assert alerts == []

    await open_ticket(client, pro_key, subject="Pro question")
    assert len(alerts) == 1
    assert "Pro question" in alerts[0]


async def test_a_mailer_failure_leaves_the_reply_committed(client, monkeypatch):
    """The thread is the record; the email is a courtesy."""
    merchant = await make_account()
    merchant_key, _ = await make_key(merchant)
    admin = await make_admin()
    created = await open_ticket(client, merchant_key)

    async def failing_send_email(**kwargs):
        raise resend_svc.ResendError("provider is down")

    monkeypatch.setattr(resend_svc, "is_configured", lambda: True)
    monkeypatch.setattr(resend_svc, "send_email", failing_send_email)

    await sign_in(client, admin)
    res = await client.post(
        f"/api/v1/admin/support/requests/{created['id']}/reply",
        json={"body": "Answer despite the outage."},
    )
    assert res.status_code == 200

    detail = await client.get(f"/api/v1/admin/support/requests/{created['id']}")
    body = detail.json()
    assert body["first_response_at"] is not None
    assert [m["body"] for m in body["messages"]] == [
        "It will not scan.",
        "Answer despite the outage.",
    ]


async def test_the_target_breach_is_visible_before_a_reply(client):
    """A target-breached, unanswered ticket is the console's most urgent row."""
    await seed_plans()
    pro = await make_account(email="pro@chmaba.test", name="Pro Co")
    await subscribe(pro, "pro")
    pro_key, _ = await make_key(pro)
    created = await open_ticket(client, pro_key)

    # Backdate the request past its 24-hour target.
    async with session_factory() as session:
        row = (
            await session.execute(
                select(models.SupportRequest).where(
                    models.SupportRequest.public_id == created["id"]
                )
            )
        ).scalar_one()
        row.created_at = datetime.now(UTC) - timedelta(hours=25)
        await session.commit()

    admin = await make_admin()
    await sign_in(client, admin)
    detail = await client.get(f"/api/v1/admin/support/requests/{created['id']}")
    assert detail.json()["target_breached"] is True
