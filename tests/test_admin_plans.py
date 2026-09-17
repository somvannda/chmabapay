"""Password sign-in and the admin plan CRUD surface."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest_asyncio
from conftest import make_account
from sqlalchemy import select

from chmabapay import models
from chmabapay.db import seed_default_plans, session_factory
from chmabapay.main import app
from chmabapay.routers.auth import SESSION_COOKIE, _make_session_jwt
from chmabapay.security import hash_password

# localhost over http keeps the session cookie non-Secure, so the httpx jar sends it
# back on the next request (the same thing a browser does in local dev).
BASE_URL = "http://localhost"

PASSWORD = "correct horse battery"


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


async def sign_in(client, email: str, password: str) -> httpx.Response:
    return await client.post("/auth/login", json={"email": email, "password": password})


async def test_password_login_creates_a_usable_session(client):
    account = await make_admin()

    res = await sign_in(client, account.email, PASSWORD)
    assert res.status_code == 200
    assert res.json()["is_platform_admin"] is True

    me = await client.get("/v1/me")
    assert me.status_code == 200
    assert me.json()["email"] == account.email

    # and the session is an admin session
    assert (await client.get("/v1/admin/overview")).status_code == 200


async def test_password_login_is_case_insensitive_on_email(client):
    account = await make_admin()
    assert (await sign_in(client, account.email.upper(), PASSWORD)).status_code == 200


async def test_password_login_failures_are_indistinguishable(client):
    account = await make_admin()

    wrong = await sign_in(client, account.email, "not the password")
    unknown = await sign_in(client, "nobody@chmaba.test", PASSWORD)
    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "invalid_credentials"}


async def test_account_without_a_password_cannot_use_password_login(client):
    account = await make_account(email="sokha@chmaba.test")
    assert (await sign_in(client, account.email, PASSWORD)).status_code == 401


async def test_suspended_account_is_rejected_after_correct_password(client):
    account = await make_admin()
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        row.status = models.ACCOUNT_SUSPENDED
        await session.commit()

    res = await sign_in(client, account.email, PASSWORD)
    assert res.status_code == 403
    assert res.json()["detail"] == "account_suspended"


async def test_admin_plan_crud(client):
    await make_admin()
    await seed_default_plans_via_session()
    assert (await sign_in(client, "duke@chmaba.test", PASSWORD)).status_code == 200

    created = await client.post(
        "/v1/admin/plans",
        json={
            "code": "growth",
            "name": "Growth",
            "monthly_fee_cents": 2900,
            "base_payments_included": 10000,
            "max_stores": 25,
            "tagline": "For teams making moves.",
            "features": ["Up to 25 stores", "Webhook signing"],
            "is_featured": True,
        },
    )
    assert created.status_code == 201, created.text
    plan = created.json()
    assert plan["code"] == "growth"
    assert plan["features"] == ["Up to 25 stores", "Webhook signing"]
    assert plan["subscriptions_count"] == 0

    duplicate = await client.post(
        "/v1/admin/plans", json={"code": "growth", "name": "Another"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "plan_code_exists"

    # a new public plan shows up in the catalogue the website and user portal read
    catalogue = await client.get("/v1/billing/plans")
    assert "growth" in [p["code"] for p in catalogue.json()]

    patched = await client.patch(
        f"/v1/admin/plans/{plan['id']}",
        json={"tagline": "For scaling teams.", "features": ["One", "Two", "Three"]},
    )
    assert patched.status_code == 200
    assert patched.json()["features"] == ["One", "Two", "Three"]

    too_many = await client.patch(
        f"/v1/admin/plans/{plan['id']}",
        json={"features": [f"bullet {i}" for i in range(20)]},
    )
    assert too_many.status_code == 422

    deleted = await client.delete(f"/v1/admin/plans/{plan['id']}")
    assert deleted.status_code == 200
    assert deleted.json() == {"retired": False, "deleted": True, "plan": None}

    assert (await client.delete(f"/v1/admin/plans/{plan['id']}")).status_code == 404


async def test_deleting_a_plan_in_use_retires_it_instead(client):
    account = await make_admin()
    await seed_default_plans_via_session()
    await sign_in(client, "duke@chmaba.test", PASSWORD)

    created = await client.post(
        "/v1/admin/plans", json={"code": "in_use", "name": "In Use"}
    )
    plan_id = created.json()["id"]

    async with session_factory() as session:
        now = datetime.now(UTC)
        session.add(
            models.PlanSubscription(
                account_id=account.id,
                plan_id=plan_id,
                status="active",
                next_billing_at=now + timedelta(days=30),
            )
        )
        await session.commit()

    res = await client.delete(f"/v1/admin/plans/{plan_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["retired"] is True
    assert body["deleted"] is False
    assert body["plan"]["is_active"] is False
    assert body["plan"]["is_public"] is False
    assert body["plan"]["subscriptions_count"] == 1

    # gone from the public catalogue, still visible to the operator
    catalogue = await client.get("/v1/billing/plans")
    assert "in_use" not in [p["code"] for p in catalogue.json()]
    admin_list = await client.get("/v1/admin/plans")
    assert "in_use" in [p["code"] for p in admin_list.json()]


async def test_seeding_never_overwrites_operator_edits(client):
    """The admin console owns plan values; the seeder only creates and backfills."""
    await seed_default_plans_via_session()

    async with session_factory() as session:
        plan = (
            await session.execute(
                select(models.Plan).where(models.Plan.code == "starter")
            )
        ).scalar_one()
        plan.monthly_fee_cents = 1234
        plan.name = "Starter (edited)"
        plan.tagline = None
        plan.features = None
        await session.commit()

    await seed_default_plans_via_session()

    async with session_factory() as session:
        plan = (
            await session.execute(
                select(models.Plan).where(models.Plan.code == "starter")
            )
        ).scalar_one()
        assert plan.monthly_fee_cents == 1234
        assert plan.name == "Starter (edited)"
        # ...but empty presentation copy is still filled in
        assert plan.tagline == "For teams making moves."
        assert plan.features


async def test_me_reports_how_the_session_was_created(client):
    """The console refuses SSO sessions, so /v1/me must expose the auth method."""
    account = await make_admin()

    await sign_in(client, account.email, PASSWORD)
    assert (await client.get("/v1/me")).json()["auth_method"] == "password"

    # A Google session for the same admin carries a different label — this is what the
    # console gate keys off to keep the SSO flow out of platform controls.
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        google_token = _make_session_jwt(row, "google")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=BASE_URL,
        cookies={SESSION_COOKIE: google_token},
    ) as google_client:
        res = await google_client.get("/v1/me")

    assert res.status_code == 200
    assert res.json()["auth_method"] == "google"


async def seed_default_plans_via_session() -> None:
    async with session_factory() as session:
        await seed_default_plans(session)