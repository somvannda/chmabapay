"""Auditing of privileged mutations (P1-2).

Two questions are asked throughout: *was this action recorded*, and *does the row
name the account that performed it*. The first is checked mechanically — every
mutating route on the credential and money surface has to be classified — so that
adding a route without auditing it fails here rather than being discovered during
an incident.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from conftest import make_account, make_key, make_store
from sqlalchemy import select, update

from chmabapay import models
from chmabapay.db import session_factory
from chmabapay.main import app
from chmabapay.security import hash_password
from chmabapay.services.payments import expire_due_payments

# The admin routes authenticate by cookie, and a Secure cookie is not sent back
# over plain http — the same reason test_admin_plans.py does this.
BASE_URL = "http://localhost"
PASSWORD = "correct horse battery"

# The surface where a mutation changes a credential, where money is sent, whether
# money can be taken, or an account's standing. Each entry names the audit action
# the route writes, which the behavioural tests below assert.
AUDITED: dict[tuple[str, str], tuple[str, ...]] = {
    ("post", "/v1/keys"): ("key.created",),
    ("post", "/v1/keys/{key_id}/revoke"): ("key.revoked",),
    ("post", "/v1/keys/{key_id}/rotate"): ("key.rotated",),
    ("post", "/v1/webhooks"): ("webhook.created",),
    ("patch", "/v1/webhooks/{endpoint_id}"): ("webhook.updated",),
    ("delete", "/v1/webhooks/{endpoint_id}"): ("webhook.deleted",),
    ("post", "/v1/webhooks/{endpoint_id}/rotate-secret"): ("webhook.secret_rotated",),
    ("post", "/v1/stores"): ("store.created", "store.link_set"),
    ("put", "/v1/stores/{public_id}/link"): ("store.link_set",),
    ("put", "/v1/stores/{public_id}"): ("store.updated", "store.link_set"),
    ("patch", "/v1/stores/{public_id}"): ("store.updated", "store.link_set"),
    ("post", "/v1/stores/{public_id}/disable"): ("store.disabled",),
    ("post", "/v1/stores/{public_id}/enable"): ("store.enabled",),
}

# Mutating verbs that change no state, so there is no action to attribute. Both
# send a message and nothing else.
NOT_A_MUTATION = {
    ("post", "/v1/stores/{public_id}/telegram/test"),
    ("post", "/v1/webhooks/{endpoint_id}/test"),
}


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as c:
        yield c


async def make_admin():
    account = await make_account(email="duke@chmaba.test", name="Duke")
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        row.is_platform_admin = True
        row.password_hash = hash_password(PASSWORD)
        await session.commit()
    return account


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def rows(action: str | None = None) -> list[models.AuditLog]:
    async with session_factory() as session:
        stmt = select(models.AuditLog).order_by(models.AuditLog.id)
        if action is not None:
            stmt = stmt.where(models.AuditLog.action == action)
        return list((await session.execute(stmt)).scalars().all())


# --------------------------------------------------------------------------- #
# Completeness
# --------------------------------------------------------------------------- #
def test_every_mutating_route_on_the_privileged_surface_is_classified():
    """The static half: a new route must be audited or declared a non-mutation.

    Without this, the invariant decays the first time somebody adds an endpoint
    in a hurry, and the absence is invisible until the day the trail is needed.
    """
    paths = app.openapi()["paths"]
    mutating = {
        (method, path)
        for path, operations in paths.items()
        for method in operations
        if method in ("post", "put", "patch", "delete")
        and path.startswith(("/v1/keys", "/v1/webhooks", "/v1/stores"))
    }

    unclassified = mutating - set(AUDITED) - NOT_A_MUTATION
    assert not unclassified, (
        "these mutating routes are neither audited nor declared as changing "
        f"nothing: {sorted(unclassified)}"
    )


@pytest.mark.parametrize(("method", "path"), sorted(set(AUDITED) | NOT_A_MUTATION))
def test_each_classified_route_still_exists(method: str, path: str):
    """A stale classification is worse than none: it claims coverage that is gone.

    One case per route, so a route that disappears names itself in the failure
    rather than being one item in a list of diffs.
    """
    operations = app.openapi()["paths"].get(path)
    assert operations is not None, f"{path} no longer exists"
    assert method in operations, f"{method.upper()} {path} no longer exists"


# --------------------------------------------------------------------------- #
# API keys
# --------------------------------------------------------------------------- #
async def test_key_lifecycle_is_attributed_to_the_merchant(client):
    account = await make_account()
    raw_key, _ = await make_key(account)
    headers = bearer(raw_key)

    created = await client.post("/v1/keys", json={"name": "production"}, headers=headers)
    assert created.status_code == 201
    key_id = created.json()["id"]
    issued_secret = created.json()["raw_key"]

    rotated = await client.post(f"/v1/keys/{key_id}/rotate", headers=headers)
    assert rotated.status_code == 200
    rotated_id = rotated.json()["id"]
    rotated_secret = rotated.json()["raw_key"]

    revoked = await client.post(f"/v1/keys/{rotated_id}/revoke", headers=headers)
    assert revoked.status_code == 200

    written = {entry.action: entry for entry in await rows()}
    assert {"key.created", "key.rotated", "key.revoked"} <= set(written)

    # The actor is the merchant who asked, not an operator.
    for action in ("key.created", "key.rotated", "key.revoked"):
        assert written[action].actor_account_id == account.id
        assert written[action].target_type == "ApiKey"
        assert written[action].created_at is not None

    assert written["key.created"].target_id == key_id
    # The replacement is the live credential, so the row points at it and names
    # what it superseded.
    assert written["key.rotated"].target_id == rotated_id
    assert written["key.rotated"].details["replaces_id"] == key_id

    # No credential is ever written to the trail.
    await assert_no_secret_in_details(issued_secret)
    await assert_no_secret_in_details(rotated_secret)


async def assert_no_secret_in_details(secret: str) -> None:
    async with session_factory() as session:
        entries = (
            await session.execute(select(models.AuditLog.details))
        ).scalars().all()
    assert secret not in str(entries)


async def test_revoking_an_already_revoked_key_records_nothing_new(client):
    """A no-op is not a privileged mutation, so it must not inflate the trail."""
    account = await make_account()
    raw_key, _ = await make_key(account)
    headers = bearer(raw_key)

    created = await client.post("/v1/keys", json={"name": "once"}, headers=headers)
    key_id = created.json()["id"]

    assert (await client.post(f"/v1/keys/{key_id}/revoke", headers=headers)).status_code == 200
    assert (await client.post(f"/v1/keys/{key_id}/revoke", headers=headers)).status_code == 200

    assert len(await rows("key.revoked")) == 1


# --------------------------------------------------------------------------- #
# Webhook endpoints
# --------------------------------------------------------------------------- #
async def test_webhook_lifecycle_is_attributed_to_the_merchant(client):
    account = await make_account()
    raw_key, _ = await make_key(account)
    headers = bearer(raw_key)

    created = await client.post(
        "/v1/webhooks",
        json={"url": "https://sink.example.com/hook", "events": ["payment.completed"]},
        headers=headers,
    )
    assert created.status_code == 201
    endpoint_id = created.json()["id"]

    # Pointing the URL somewhere new is how a merchant's events would be diverted,
    # so the trail keeps both ends of the change.
    moved = await client.patch(
        f"/v1/webhooks/{endpoint_id}",
        json={"url": "https://elsewhere.example.com/hook"},
        headers=headers,
    )
    assert moved.status_code == 200

    rotated = await client.post(
        f"/v1/webhooks/{endpoint_id}/rotate-secret", headers=headers
    )
    assert rotated.status_code == 200
    new_secret = rotated.json()["signing_secret"]

    deleted = await client.delete(f"/v1/webhooks/{endpoint_id}", headers=headers)
    assert deleted.status_code == 200

    written = {entry.action: entry for entry in await rows()}
    assert {
        "webhook.created",
        "webhook.updated",
        "webhook.secret_rotated",
        "webhook.deleted",
    } <= set(written)

    for entry in written.values():
        assert entry.actor_account_id == account.id
        assert entry.target_type == "WebhookEndpoint"

    assert written["webhook.updated"].details["changes"]["url"] == {
        "from": "https://sink.example.com/hook",
        "to": "https://elsewhere.example.com/hook",
    }
    # The previous secret is not kept; the new one must not be either.
    await assert_no_secret_in_details(new_secret)


async def test_a_webhook_patch_that_changes_nothing_is_not_recorded(client):
    account = await make_account()
    raw_key, _ = await make_key(account)
    headers = bearer(raw_key)

    created = await client.post(
        "/v1/webhooks", json={"url": "https://sink.example.com/hook"}, headers=headers
    )
    endpoint_id = created.json()["id"]

    same = await client.patch(
        f"/v1/webhooks/{endpoint_id}",
        json={"url": "https://sink.example.com/hook"},
        headers=headers,
    )
    assert same.status_code == 200

    assert await rows("webhook.updated") == []


# --------------------------------------------------------------------------- #
# Stores — where the money goes
# --------------------------------------------------------------------------- #
async def test_the_payout_destination_is_recorded_whenever_it_changes(client):
    """The one invariant that matters most: who pointed this store's payouts."""
    account = await make_account()
    raw_key, _ = await make_key(account)
    headers = bearer(raw_key)

    # Created with a link.
    created = await client.post(
        "/v1/stores",
        json={
            "name": "Sokha Cafe",
            "link": {
                "raw_link": "https://link.payway.com.kh/sokha",
                "merchant_account_id": "sokhapayway",
            },
        },
        headers=headers,
    )
    assert created.status_code == 201
    public_id = created.json()["id"]

    # Replaced through the dedicated route.
    replaced = await client.put(
        f"/v1/stores/{public_id}/link",
        json={
            "raw_link": "https://link.payway.com.kh/other",
            "merchant_account_id": "otherpayway",
        },
        headers=headers,
    )
    assert replaced.status_code == 200

    # And replaced again through a plain PATCH, which is the quiet path.
    patched = await client.patch(
        f"/v1/stores/{public_id}",
        json={
            "link": {
                "raw_link": "https://link.payway.com.kh/third",
                "merchant_account_id": "thirdpayway",
            }
        },
        headers=headers,
    )
    assert patched.status_code == 200

    links = await rows("store.link_set")
    assert [entry.details["merchant_account_id"] for entry in links] == [
        "sokhapayway",
        "otherpayway",
        "thirdpayway",
    ]
    assert all(entry.actor_account_id == account.id for entry in links)
    assert all(entry.target_type == "Store" for entry in links)


async def test_store_mutations_are_recorded(client):
    account = await make_account()
    raw_key, _ = await make_key(account)
    headers = bearer(raw_key)

    created = await client.post("/v1/stores", json={"name": "Draft Only"}, headers=headers)
    assert created.status_code == 201
    public_id = created.json()["id"]

    renamed = await client.patch(
        f"/v1/stores/{public_id}", json={"name": "Renamed"}, headers=headers
    )
    assert renamed.status_code == 200

    disabled = await client.post(f"/v1/stores/{public_id}/disable", headers=headers)
    assert disabled.status_code == 200

    enabled = await client.post(f"/v1/stores/{public_id}/enable", headers=headers)
    assert enabled.status_code == 200
    # This store never had a link, so it comes back as a draft rather than active:
    # `active` would advertise a store with nowhere to send money.
    assert enabled.json()["status"] == "draft"

    # Enabling a store that is not disabled changes nothing, so it records nothing —
    # the rule an empty PATCH follows.
    again = await client.post(f"/v1/stores/{public_id}/enable", headers=headers)
    assert again.status_code == 200

    written = {entry.action: entry for entry in await rows()}
    assert written["store.created"].details == {
        "name": "Draft Only",
        "external_id": None,
    }
    # A store created without a link never sets one, so there is no link row.
    assert "store.link_set" not in written
    assert written["store.updated"].details["fields"] == ["name"]
    assert written["store.disabled"].actor_account_id == account.id
    assert written["store.enabled"].details == {"name": "Renamed", "status": "draft"}
    assert len(await rows("store.enabled")) == 1


async def test_an_empty_store_patch_is_not_recorded(client):
    account = await make_account()
    raw_key, _ = await make_key(account)
    headers = bearer(raw_key)

    created = await client.post("/v1/stores", json={"name": "Untouched"}, headers=headers)
    public_id = created.json()["id"]

    assert (
        await client.patch(f"/v1/stores/{public_id}", json={}, headers=headers)
    ).status_code == 200
    assert await rows("store.updated") == []


# --------------------------------------------------------------------------- #
# Payment reissue
# --------------------------------------------------------------------------- #
async def test_reissuing_a_payment_is_recorded_with_its_lineage(client):
    account = await make_account()
    await make_store(account, name="Sokha Cafe", owner="Sokha")
    raw_key, _ = await make_key(account)
    headers = bearer(raw_key)

    created = (
        await client.post(
            "/v1/payments",
            json={"amount": 4.0, "reference_id": "order_7", "hosted_qr": False},
            headers=headers,
        )
    ).json()

    async with session_factory() as session:
        await session.execute(
            update(models.Payment)
            .where(models.Payment.public_id == created["id"])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
        await expire_due_payments(session)

    reissued = await client.post(f"/v1/payments/{created['id']}/reissue", headers=headers)
    assert reissued.status_code == 201
    successor = reissued.json()

    entries = await rows("payment.reissued")
    assert len(entries) == 1
    assert entries[0].actor_account_id == account.id
    assert entries[0].target_type == "Payment"
    assert entries[0].details == {
        "replaces": created["id"],
        "successor": successor["id"],
        "store": successor["store"],
    }

    # A replay changes nothing, so it must not add a second row.
    replay = await client.post(f"/v1/payments/{created['id']}/reissue", headers=headers)
    assert replay.status_code == 200
    assert len(await rows("payment.reissued")) == 1


# --------------------------------------------------------------------------- #
# Account standing and entitlements
# --------------------------------------------------------------------------- #
async def test_suspending_and_reactivating_an_account_is_recorded(client):
    operator = await make_admin()
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha")
    assert (
        await client.post(
            "/auth/login", json={"email": operator.email, "password": PASSWORD}
        )
    ).status_code == 200

    suspended = await client.patch(
        f"/v1/admin/accounts/{merchant.id}",
        json={"status": "suspended", "reason": "chargeback fraud"},
    )
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"

    activated = await client.patch(
        f"/v1/admin/accounts/{merchant.id}", json={"status": "active"}
    )
    assert activated.status_code == 200

    entries = {entry.action: entry for entry in await rows()}
    assert entries["account.suspended"].target_id == merchant.id
    assert entries["account.suspended"].actor_account_id == operator.id
    assert entries["account.suspended"].details == {
        "changes": {"status": {"from": "active", "to": "suspended"}},
        "reason": "chargeback fraud",
    }
    assert entries["account.activated"].actor_account_id == operator.id


async def test_a_standing_change_cannot_be_smuggled_through_as_an_entitlement(client):
    """`status` is the privileged field, and the action has to say so."""
    operator = await make_admin()
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha")
    await client.post("/auth/login", json={"email": operator.email, "password": PASSWORD})

    res = await client.patch(
        f"/v1/admin/accounts/{merchant.id}", json={"whitelabel_enabled": True}
    )
    assert res.status_code == 200

    # The operator's own sign-in is audited as well (`auth.login_succeeded`), so
    # the subject here is the *mutation* records: an entitlement change must land
    # as `account.updated` and must not be dressed up as a standing change.
    entries = [e for e in await rows() if not e.action.startswith("auth.")]
    assert [entry.action for entry in entries] == ["account.updated"]

    # An unknown standing is refused outright.
    rejected = await client.patch(
        f"/v1/admin/accounts/{merchant.id}", json={"status": "deleted"}
    )
    assert rejected.status_code == 422


# --------------------------------------------------------------------------- #
# The view
# --------------------------------------------------------------------------- #
async def test_the_audit_view_is_operator_only(client):
    """The trail spans every account, so a merchant key must not open it."""
    account = await make_account()
    raw_key, _ = await make_key(account)

    # 401 rather than 403 for the key: `get_hybrid_admin_context` resolves the
    # session dependency first and that dependency raises on a missing cookie, so
    # the `Bearer ck_` branch below it is unreachable. Either status means the
    # same thing here — a merchant key does not open the trail. The dead branch
    # is recorded in docs/production-readiness.md P1-2 rather than fixed here,
    # because making it reachable would hand platform-admin powers to API keys.
    by_key = await client.get("/v1/admin/audit-logs", headers=bearer(raw_key))
    assert by_key.status_code in (401, 403)
    assert (await client.get("/v1/admin/audit-logs")).status_code == 401


async def test_the_audit_view_lists_the_trail_newest_first_with_the_actor(client):
    operator = await make_admin()
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha")
    merchant_key, _ = await make_key(merchant)
    await client.post("/auth/login", json={"email": operator.email, "password": PASSWORD})

    await client.post("/v1/keys", json={"name": "audited"}, headers=bearer(merchant_key))
    await client.patch(
        f"/v1/admin/accounts/{merchant.id}",
        json={"status": "suspended", "reason": "review"},
    )

    res = await client.get("/v1/admin/audit-logs")
    assert res.status_code == 200
    body = res.json()
    assert body["pagination"]["total_rows"] >= 2

    actions = [row["action"] for row in body["data"]]
    assert actions[0] == "account.suspended"
    assert "key.created" in actions

    by_action = {row["action"]: row for row in body["data"]}
    assert by_action["key.created"]["actor_email"] == merchant.email
    assert by_action["account.suspended"]["actor_email"] == operator.email

    # Filters narrow the trail rather than the page.
    only_keys = await client.get("/v1/admin/audit-logs", params={"action": "key.created"})
    assert only_keys.json()["pagination"]["total_rows"] == 1
    assert {row["action"] for row in only_keys.json()["data"]} == {"key.created"}

    by_actor = await client.get(
        "/v1/admin/audit-logs", params={"actor_account_id": operator.id}
    )
    assert {row["actor_account_id"] for row in by_actor.json()["data"]} == {operator.id}

    by_type = await client.get(
        "/v1/admin/audit-logs", params={"target_type": "ApiKey"}
    )
    assert {row["target_type"] for row in by_type.json()["data"]} == {"ApiKey"}
