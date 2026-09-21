"""Password sign-in, the admin plan CRUD surface, and the console's read-only views."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest_asyncio
from conftest import make_account, make_key, make_store, make_webhook
from sqlalchemy import func, select

from chmabapay import models
from chmabapay.config import get_settings
from chmabapay.db import seed_default_plans, session_factory
from chmabapay.main import app
from chmabapay.routers import admin as admin_router
from chmabapay.routers.auth import SESSION_COOKIE, _make_session_jwt
from chmabapay.security import hash_password
from chmabapay.services.status_reconciler import ReconcileResult

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


@pytest_asyncio.fixture
async def unconfigured_hq_store(monkeypatch):
    """Start with no plan-fee collection configured, whatever the environment says.

    A deployment that has set `CHMABAPAY_HQ_PAYWAY_LINK` (or the store id) seeds an HQ
    store the first time a platform admin signs in, so "nothing is configured yet" is
    not a property of the code — it is a property of the machine the test runs on. The
    tests below assert that starting point, so they have to establish it.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "chmabapay_hq_store_id", None, raising=False)
    monkeypatch.setattr(settings, "chmabapay_hq_payway_link", None, raising=False)
    return settings


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


async def test_an_sso_session_cannot_reach_the_admin_api(client):
    """The console's password-only rule is enforced by the API, not just by React.

    `/v1/me` reported `auth_method` and the admin shell refused anything that was
    not "password" — but the shell is not a security boundary. An admin's Google
    session could call every `/v1/admin/*` route directly with curl, which is the
    whole privilege the console claims to withhold. This asserts the refusal now
    happens server-side, which is what `session_auth_method` was written for.
    """
    account = await make_admin()

    # The console's own path still works.
    await sign_in(client, account.email, PASSWORD)
    assert (await client.get("/v1/admin/accounts")).status_code == 200

    # The same admin, authenticated by Google instead, is refused.
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        google_token = _make_session_jwt(row, "google")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=BASE_URL,
        cookies={SESSION_COOKIE: google_token},
    ) as sso_client:
        refused = await sso_client.get("/v1/admin/accounts")

    assert refused.status_code == 403
    assert refused.json()["detail"] == "password_session_required"


async def test_every_sign_in_attempt_is_audited(client):
    """A sign-in leaves a row whether it worked or not.

    Every privileged *mutation* was already audited, but the front door was not:
    nothing recorded who had been trying passwords, or from where. The failure
    path is the interesting one — it has to commit before it raises, because
    raising discards the request's transaction and with it the audit row.
    """
    account = await make_admin()
    assert (await sign_in(client, account.email, "wrong password")).status_code == 401
    assert (await sign_in(client, account.email, "wrong password")).status_code == 401
    assert (await sign_in(client, account.email, PASSWORD)).status_code == 200
    # An address with no account has no actor to attribute the attempt to; the
    # attempt is still worth keeping, so the row is written with a null actor.
    assert (
        await sign_in(client, "nobody@chmaba.test", "whatever")
    ).status_code == 401

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(models.AuditLog)
                    .where(models.AuditLog.action.like("auth.login%"))
                    .order_by(models.AuditLog.id)
                )
            )
            .scalars()
            .all()
        )

    assert [row.action for row in rows] == [
        "auth.login_failed",
        "auth.login_failed",
        "auth.login_succeeded",
        "auth.login_failed",
    ]
    # The known account is the actor on its own failures...
    assert rows[0].actor_account_id == account.id
    assert rows[0].details["reason"] == "invalid_credentials"
    assert rows[0].details["email"] == account.email
    # ...and the unknown address is not, but the address it tried is recorded.
    assert rows[3].actor_account_id is None
    assert rows[3].target_type == "Email"
    assert rows[3].details["email"] == "nobody@chmaba.test"
    # Neither the right password nor the wrong one is anywhere in the trail.
    for row in rows:
        assert PASSWORD not in str(row.details)
        assert "wrong password" not in str(row.details)


async def test_repeated_failures_lock_the_email_out(client):
    """The per-address limiter cannot bound guessing spread across addresses.

    This is the per-identity half: five failures on one email start an
    exponentially growing window, and the refusal is a distinct 429 so a real
    operator can tell "wait" apart from "you typed it wrong".
    """
    account = await make_admin()

    for _ in range(4):
        assert (await sign_in(client, account.email, "wrong")).status_code == 401

    # The fifth failure trips the lock, but it is still answered as a bad password:
    # the attempt that crossed the line is not treated differently from the others.
    assert (await sign_in(client, account.email, "wrong")).status_code == 401

    # Even the *correct* password is refused while the window is open.
    locked = await sign_in(client, account.email, PASSWORD)
    assert locked.status_code == 429
    assert locked.json()["detail"] == "too_many_attempts"
    assert int(locked.headers["Retry-After"]) > 0

    # A different email is untouched: this is a lockout, not an outage.
    await make_account(email="someone.else@chmaba.test", name="Someone")
    assert (
        await sign_in(client, "someone.else@chmaba.test", "wrong")
    ).status_code == 401


async def test_a_successful_sign_in_clears_the_failure_record(client):
    """Otherwise three typos accumulated over months would eventually lock a user out."""
    account = await make_admin()

    for _ in range(3):
        assert (await sign_in(client, account.email, "wrong")).status_code == 401
    assert (await sign_in(client, account.email, PASSWORD)).status_code == 200

    # The record is cleared, so this next run of four failures is still under the
    # threshold of five — and the correct password therefore still works.
    for _ in range(4):
        assert (await sign_in(client, account.email, "wrong")).status_code == 401
    assert (await sign_in(client, account.email, PASSWORD)).status_code == 200


async def test_admin_payment_feed_spans_the_platform(client):
    """The console could count a merchant's payments but never show one.

    That gap is the whole reason this endpoint exists: a merchant reporting that a
    payment "never settled" had no in-product answer platform-side.
    """
    admin = await make_admin()
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha Cafe")
    store = await make_store(merchant, name="Sokha Cafe", owner="Sokha")
    raw_key, _ = await make_key(merchant)

    created = (
        await client.post(
            "/v1/payments",
            json={
                "amount": 4.5,
                "reference_id": "order_7",
                "store": store.public_id,
                "hosted_qr": False,
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
    ).json()
    assert created["status"] == "pending"

    await sign_in(client, admin.email, PASSWORD)

    body = (await client.get("/v1/admin/payments")).json()
    row = next(p for p in body["data"] if p["id"] == created["id"])
    assert row["account_id"] == merchant.id
    assert row["account_email"] == merchant.email
    assert row["store_public_id"] == store.public_id
    assert row["store_name"] == "Sokha Cafe"
    assert row["amount_cents"] == 450
    assert row["currency"] == "USD"
    assert row["reference_id"] == "order_7"
    # A pending code we are still reconciling, which is what distinguishes it from
    # one the detection window has already closed on.
    assert row["paid_at"] is None
    assert row["detection_closed_at"] is None

    # `q` is the string a support thread actually contains: our public id, or the
    # merchant's own reference.
    by_reference = (await client.get("/v1/admin/payments?q=order_7")).json()
    assert [p["id"] for p in by_reference["data"]] == [created["id"]]
    by_public_id = (await client.get(f"/v1/admin/payments?q={created['id']}")).json()
    assert [p["id"] for p in by_public_id["data"]] == [created["id"]]

    # ...and a partial match still finds it, because an operator pastes whatever
    # the merchant sent them rather than a tidied-up identifier.
    partial = (await client.get("/v1/admin/payments?q=order")).json()
    assert created["id"] in [p["id"] for p in partial["data"]]

    narrowed = (await client.get(f"/v1/admin/payments?account_id={merchant.id}")).json()
    assert [p["id"] for p in narrowed["data"]] == [created["id"]]
    # The admin's own account owns no stores, so it has taken no payments.
    assert (await client.get(f"/v1/admin/payments?account_id={admin.id}")).json()["data"] == []

    assert (await client.get("/v1/admin/payments?status=paid")).json()["data"] == []
    assert [
        p["id"] for p in (await client.get("/v1/admin/payments?status=pending")).json()["data"]
    ] == [created["id"]]

    # Settling it moves the timestamp an operator reads to answer "did the money
    # arrive", rather than only changing the status pill.
    assert (await client.post(f"/_dev/payments/{created['id']}/pay")).status_code == 200
    settled = (await client.get("/v1/admin/payments?status=paid")).json()["data"]
    assert [p["id"] for p in settled] == [created["id"]]
    assert settled[0]["paid_at"] is not None


# --------------------------------------------------------------------------- #
# Plan fee collection — where ChmabaPay's own money lands
# --------------------------------------------------------------------------- #
async def test_an_operator_can_point_plan_fees_at_a_payway_link(
    client, unconfigured_hq_store
):
    """The setting used to be an environment variable plus a shell session, which is
    most of why self-serve billing was never switched on. It is a field now, and this
    is that field working end to end.
    """
    admin = await make_admin()
    await sign_in(client, admin.email, PASSWORD)

    before = (await client.get("/v1/admin/hq-store")).json()
    assert before["configured"] is False
    assert before["source"] == "none"

    saved = await client.put(
        "/v1/admin/hq-store/link",
        json={"raw_link": "https://link.payway.com.kh/ABAPAYpe518710Y"},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["configured"] is True
    assert body["source"] == "console"
    # Derived from the link, so an operator only has to paste the link itself.
    assert body["merchant_account_id"] == "ABAPAYpe518710Y"
    assert body["store_status"] == "active"

    # It persisted, and it is the store the billing route will resolve.
    again = (await client.get("/v1/admin/hq-store")).json()
    assert again["merchant_account_id"] == "ABAPAYpe518710Y"
    assert again["store_public_id"] == body["store_public_id"]

    async with session_factory() as session:
        actions = [
            row.action
            for row in (
                await session.execute(
                    select(models.AuditLog)
                    .where(models.AuditLog.action.like("hq_store%"))
                    .order_by(models.AuditLog.id)
                )
            )
            .scalars()
            .all()
        ]
    assert actions == ["hq_store.created", "hq_store.link_set"]


async def test_saving_a_link_twice_replaces_it_rather_than_adding_a_store(client):
    """A second save is a correction, not a second collection point."""
    admin = await make_admin()
    await sign_in(client, admin.email, PASSWORD)

    first = (
        await client.put(
            "/v1/admin/hq-store/link",
            json={"raw_link": "https://link.payway.com.kh/ABAPAYfirst001"},
        )
    ).json()
    second = (
        await client.put(
            "/v1/admin/hq-store/link",
            json={"raw_link": "https://link.payway.com.kh/ABAPAYsecond02"},
        )
    ).json()

    assert second["store_public_id"] == first["store_public_id"]
    assert second["merchant_account_id"] == "ABAPAYsecond02"

    async with session_factory() as session:
        stores = list(
            (
                await session.execute(
                    # The name is the marker `resolve_hq_store` keys on, so it is
                    # spelled out here rather than imported: if the constant is ever
                    # renamed, the lookup and the console marker must move together,
                    # and this failing is how that gets noticed.
                    select(models.Store).where(models.Store.name == "ChmabaPay HQ")
                )
            )
            .scalars()
            .all()
        )
    assert len(stores) == 1


async def test_a_link_that_is_not_a_payway_link_is_refused(
    client, unconfigured_hq_store
):
    """This field decides where the platform's own revenue lands, so a pasted URL from
    somewhere else has to be refused rather than stored.

    A bare slug is accepted on purpose — that is the other form a PayWay share link
    comes in — which is why an opaque token cannot be told apart from an account
    number here. The host check is what carries the weight.
    """
    admin = await make_admin()
    await sign_in(client, admin.email, PASSWORD)

    for bad in (
        "https://example.com/pay/abc",  # some other host
        "not a link at all",  # not a slug either
    ):
        res = await client.put("/v1/admin/hq-store/link", json={"raw_link": bad})
        assert res.status_code == 400, bad
        assert res.json()["detail"].startswith("invalid_payway_link")

    # Nothing was written on the way through.
    assert (await client.get("/v1/admin/hq-store")).json()["configured"] is False


async def test_a_merchant_cannot_set_where_plan_fees_are_collected(client):
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha")
    async with session_factory() as session:
        row = await session.get(models.Account, merchant.id)
        assert row is not None
        row.password_hash = hash_password(PASSWORD)
        await session.commit()
    await sign_in(client, merchant.email, PASSWORD)

    res = await client.put(
        "/v1/admin/hq-store/link",
        json={"raw_link": "https://link.payway.com.kh/ABAPAYpe518710Y"},
    )
    assert res.status_code == 403


async def test_admin_delivery_feed_shows_a_stalled_sender(client):
    """`GET /v1/admin/deliveries` answers "is the webhook rail delivering at all".

    The merchant-facing route answers "did *my* endpoint receive it" and only for
    an endpoint the caller owns, so three merchants reporting silence at once each
    had to suspect their own integration.
    """
    admin = await make_admin()
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha Cafe")
    store = await make_store(merchant, name="Sokha Cafe", owner="Sokha")
    endpoint = await make_webhook(merchant, url="https://hooks.example.com/sokha")
    raw_key, _ = await make_key(merchant)

    created = (
        await client.post(
            "/v1/payments",
            json={
                "amount": 2.0,
                "store": store.public_id,
                "hosted_qr": False,
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
    ).json()
    # Settling a payment is what fans the event out to the account's endpoints, so
    # this goes through the real enqueue path rather than inserting rows by hand.
    assert (await client.post(f"/_dev/payments/{created['id']}/pay")).status_code == 200

    async with session_factory() as session:
        delivery = (
            (
                await session.execute(
                    select(models.EventDelivery).order_by(models.EventDelivery.id.desc())
                )
            )
            .scalars()
            .first()
        )
        assert delivery is not None
        delivery.status = models.DELIVERY_RETRYING
        delivery.attempts = 3
        delivery.last_response_status = 502
        delivery.last_error = "ConnectError: connection refused"
        # Two hours in the past: the sender should have tried again long ago, which
        # is the signature of a stalled worker rather than an endpoint refusing.
        delivery.next_attempt_at = datetime.now(UTC) - timedelta(hours=2)
        await session.commit()

    await sign_in(client, admin.email, PASSWORD)
    body = (await client.get("/v1/admin/deliveries")).json()
    row = next(d for d in body["data"] if d["endpoint_id"] == endpoint.id)
    assert row["account_id"] == merchant.id
    assert row["account_email"] == merchant.email
    assert row["endpoint_url"] == "https://hooks.example.com/sokha"
    assert row["event_type"] == models.EVENT_COMPLETED
    assert row["status"] == "retrying"
    assert row["attempts"] == 3
    assert row["last_response_status"] == 502
    assert row["last_error"] == "ConnectError: connection refused"

    # The filters an operator reaches for when deciding whether this is one endpoint
    # or the whole rail.
    retrying = (await client.get("/v1/admin/deliveries?status=retrying")).json()
    assert [d["id"] for d in retrying["data"]] == [row["id"]]
    assert (await client.get("/v1/admin/deliveries?status=success")).json()["data"] == []

    both = await client.get(f"/v1/admin/deliveries?status=retrying&account_id={merchant.id}")
    assert [d["id"] for d in both.json()["data"]] == [row["id"]]
    assert (await client.get(f"/v1/admin/deliveries?account_id={admin.id}")).json()["data"] == []

    by_endpoint = await client.get(f"/v1/admin/deliveries?endpoint_id={endpoint.id}")
    assert [d["id"] for d in by_endpoint.json()["data"]] == [row["id"]]


async def test_admin_read_views_are_closed_to_everyone_else(client):
    """Both feeds expose every account's money and integration health."""
    for path in ("/v1/admin/payments", "/v1/admin/deliveries"):
        assert (await client.get(path)).status_code == 401

    # A signed-in merchant who is not a platform admin. The gate is on
    # `is_platform_admin`, not on holding a session, so signing in is not enough.
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha Cafe")
    async with session_factory() as session:
        row = await session.get(models.Account, merchant.id)
        row.password_hash = hash_password(PASSWORD)
        await session.commit()

    assert (await sign_in(client, merchant.email, PASSWORD)).status_code == 200
    for path in ("/v1/admin/payments", "/v1/admin/deliveries"):
        assert (await client.get(path)).status_code == 403


async def seed_default_plans_via_session() -> None:
    async with session_factory() as session:
        await seed_default_plans(session)


# --------------------------------------------------------------------------- #
# Payment disputes — seeing the problem is not the same as fixing it
# --------------------------------------------------------------------------- #
# Before these routes, a merchant on the line saying "my customer paid and it still
# says pending" had no platform-side remedy at all: the reconciler runs on its own
# schedule, ABA's session could not be re-queried by hand, and a webhook the
# merchant's endpoint refused sat failed forever.


async def make_disputed_payment(
    client, *, settle: bool = False, reverse: bool = False
) -> tuple[models.Account, models.Account, models.Store, dict]:
    """A merchant, a store, and one payment — the shape of a dispute."""
    admin = await make_admin()
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha Cafe")
    store = await make_store(merchant, name="Sokha Cafe", owner="Sokha")
    raw_key, _ = await make_key(merchant)

    created = (
        await client.post(
            "/v1/payments",
            json={
                "amount": 12.5,
                "reference_id": "order_99",
                "store": store.public_id,
                "hosted_qr": False,
            },
            headers={"Authorization": f"Bearer {raw_key}"},
        )
    ).json()
    if settle:
        assert (await client.post(f"/_dev/payments/{created['id']}/pay")).status_code == 200
    if reverse:
        # Reversed as the merchant, because that route is scoped to the owner.
        reverted = await client.post(
            f"/v1/payments/{created['id']}/reverse",
            json={"reason": "customer refund"},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert reverted.status_code == 200, reverted.text

    assert (await sign_in(client, admin.email, PASSWORD)).status_code == 200
    return admin, merchant, store, created


async def _payment_row(public_id: str) -> models.Payment:
    async with session_factory() as session:
        return (
            await session.execute(
                select(models.Payment).where(models.Payment.public_id == public_id)
            )
        ).scalar_one()


async def _audit_row(action: str) -> models.AuditLog:
    async with session_factory() as session:
        return (
            await session.execute(
                select(models.AuditLog).where(models.AuditLog.action == action)
            )
        ).scalars().one()


async def test_payment_detail_exposes_the_evidence_a_dispute_needs(client):
    """The feed answers "what is wrong"; the detail answers "why".

    A status string cannot settle an argument with a customer holding a receipt, so
    this carries the rail's transaction id, the raw gateway payload and the webhook
    attempts alongside the usual fields.
    """
    admin, merchant, store, created = await make_disputed_payment(client)

    res = await client.get(f"/v1/admin/payments/{created['id']}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == created["id"]
    assert body["status"] == "pending"
    assert body["amount_cents"] == 1250
    assert body["currency"] == "USD"
    assert body["reference_id"] == "order_99"
    assert body["account_id"] == merchant.id
    assert body["account_email"] == merchant.email
    assert body["store_public_id"] == store.public_id
    assert body["store_name"] == "Sokha Cafe"
    # The QR is here on purpose: a dispute sometimes ends by comparing it to the
    # customer's screenshot.
    assert body["qr_string"]
    assert body["bill_number"]
    # No rail has answered yet, no code has been reissued, and we are still watching.
    assert body["bakong_ref"] is None
    assert body["gateway_status_raw"] is None
    assert body["attempt_history"] is None
    assert body["reissued_from"] is None
    assert body["superseded_by"] is None
    assert body["detection_closed_at"] is None
    assert body["deliveries"] == []

    assert (await client.get("/v1/admin/payments/ps_nope")).status_code == 404


async def test_payment_dispute_routes_are_closed_to_everyone_else(client):
    """Each of the four routes exposes or moves another account's money."""
    await make_admin()
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha Cafe")
    store = await make_store(merchant, name="Sokha Cafe", owner="Sokha")
    raw_key, _ = await make_key(merchant)
    created = (
        await client.post(
            "/v1/payments",
            json={"amount": 3.0, "store": store.public_id, "hosted_qr": False},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
    ).json()
    pid = created["id"]
    body = {"reason": "attempting a dispute"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as anonymous:
        assert (await anonymous.get(f"/v1/admin/payments/{pid}")).status_code == 401
        for action in ("reconcile", "mark-paid", "redeliver"):
            res = await anonymous.post(f"/v1/admin/payments/{pid}/{action}", json=body)
            assert res.status_code == 401, action

    # A signed-in merchant is not an operator: the gate is `is_platform_admin`, not
    # the mere presence of a session.
    async with session_factory() as session:
        row = await session.get(models.Account, merchant.id)
        row.password_hash = hash_password(PASSWORD)
        await session.commit()
    assert (await sign_in(client, merchant.email, PASSWORD)).status_code == 200

    assert (await client.get(f"/v1/admin/payments/{pid}")).status_code == 403
    for action in ("reconcile", "mark-paid", "redeliver"):
        res = await client.post(f"/v1/admin/payments/{pid}/{action}", json=body)
        assert res.status_code == 403, action


async def test_reconciling_a_paid_payment_is_a_no_op(client):
    """Nothing to re-ask, so nothing is claimed to have been asked."""
    admin, merchant, store, created = await make_disputed_payment(client, settle=True)

    async with session_factory() as session:
        before = (await session.execute(select(func.count(models.AuditLog.id)))).scalar_one()

    res = await client.post(f"/v1/admin/payments/{created['id']}/reconcile")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "paid"
    assert body["rail_status"] == "PAID"
    assert body["signals"] == ["already_paid"]
    assert body["transitioned_to_paid"] is False

    async with session_factory() as session:
        after = (await session.execute(select(func.count(models.AuditLog.id)))).scalar_one()
    assert after == before


async def test_a_reconcile_attempt_records_what_the_rail_said(client, monkeypatch):
    """The rail's answer travels with the attempt, including "it could not answer"."""
    admin, merchant, store, created = await make_disputed_payment(client)

    async def fake(payment, *, bakong_client, session=None, **kwargs):
        return ReconcileResult(
            payment_id=payment.id,
            payment_public_id=payment.public_id,
            status="PENDING",
            source="payway_hosted_checkout",
            matched_amount=None,
            signals=["hosted_action:OPEN"],
            error="payway_hosted_unavailable: ABA did not answer for this session; retry",
        )

    monkeypatch.setattr(admin_router, "reconcile_payment", fake)

    res = await client.post(f"/v1/admin/payments/{created['id']}/reconcile")
    assert res.status_code == 200, res.text
    body = res.json()
    # The rail said pending and so do we — but they are reported separately, because
    # "the rail said pending" and "we are pending" are different claims.
    assert body["rail_status"] == "PENDING"
    assert body["status"] == "pending"
    assert body["transitioned_to_paid"] is False
    assert body["source"] == "payway_hosted_checkout"
    assert body["signals"] == ["hosted_action:OPEN"]
    assert "payway_hosted_unavailable" in body["error"]

    entry = await _audit_row("admin.payment_reconciled")
    assert entry.actor_account_id == admin.id
    assert entry.target_type == "Payment"
    assert entry.details["rail_status"] == "PENDING"
    assert entry.details["store_id"] == store.id

    # The attempt is recorded, not the outcome it hoped for.
    assert (await _payment_row(created["id"])).status == "pending"


async def test_marking_paid_manually_credits_the_sale_and_names_the_operator(client):
    """The most dangerous button in the console, so it demands a reason and an audit."""
    admin, merchant, store, created = await make_disputed_payment(client)

    # The ABA session handle is the only thing a later reconciliation can use to ask
    # ABA about this payment, and `mark_paid` writes that column wholesale.
    async with session_factory() as session:
        payment = (
            await session.execute(
                select(models.Payment).where(models.Payment.public_id == created["id"])
            )
        ).scalar_one()
        payment.gateway_status_raw = {
            "payway_hosted": {
                "client_id": "abc123",
                "request_time": "20260918120000",
                "token": "tok",
            }
        }
        await session.commit()

    # A missing or too-short reason is refused: that string is the only record of why
    # money was credited with nothing from the rail to back it.
    for bad in ({}, {"reason": ""}, {"reason": "  "}, {"reason": "no"}):
        res = await client.post(f"/v1/admin/payments/{created['id']}/mark-paid", json=bad)
        assert res.status_code == 422, bad

    reason = "Customer receipt ABA ref 998877 confirms payment; hosted status unreachable"
    res = await client.post(
        f"/v1/admin/payments/{created['id']}/mark-paid", json={"reason": reason}
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "paid"
    assert res.json()["paid_at"] is not None

    async with session_factory() as session:
        payment = (
            await session.execute(
                select(models.Payment).where(models.Payment.public_id == created["id"])
            )
        ).scalar_one()
        assert payment.status == "paid"
        # The hosted session survived, so a later reconciliation can still ask ABA
        # about this payment and agree with the decision.
        assert payment.gateway_status_raw["payway_hosted"]["client_id"] == "abc123"
        note = payment.gateway_status_raw["manual_mark_paid"]
        assert note["by"] == admin.email
        assert "998877" in note["reason"]

    entry = await _audit_row("admin.payment_mark_paid")
    assert entry.actor_account_id == admin.id
    assert entry.target_type == "Payment"
    assert entry.details["reason"] == reason
    assert entry.details["amount_cents"] == 1250

    # A second attempt is refused. Crediting it twice would double-count the sale in
    # every report built on it.
    again = await client.post(
        f"/v1/admin/payments/{created['id']}/mark-paid", json={"reason": "trying again"}
    )
    assert again.status_code == 409
    assert again.json()["detail"] == "payment_already_paid"


async def test_marking_a_refunded_payment_paid_is_refused(client):
    """A refunded sale cannot be un-refunded by a status edit."""
    admin, merchant, store, created = await make_disputed_payment(
        client, settle=True, reverse=True
    )

    res = await client.post(
        f"/v1/admin/payments/{created['id']}/mark-paid", json={"reason": "restoring it"}
    )
    assert res.status_code == 409
    assert res.json()["detail"] == "payment_is_reversed"


async def test_redelivering_requeues_a_payments_webhooks(client):
    """A merchant who fixes their endpoint should not have to wait for the next sale."""
    admin = await make_admin()
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha Cafe")
    store = await make_store(merchant, name="Sokha Cafe", owner="Sokha")
    await make_webhook(merchant, url="https://hooks.example.com/sokha")
    raw_key, _ = await make_key(merchant)
    created = (
        await client.post(
            "/v1/payments",
            json={"amount": 2.0, "store": store.public_id, "hosted_qr": False},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
    ).json()
    assert (await client.post(f"/_dev/payments/{created['id']}/pay")).status_code == 200

    async with session_factory() as session:
        delivery = (await session.execute(select(models.EventDelivery))).scalars().one()
        delivery.status = models.DELIVERY_FAILED
        delivery.attempts = 8
        delivery.next_attempt_at = None
        delivery.last_error = "ConnectError: connection refused"
        await session.commit()
        delivery_id = delivery.id

    assert (await sign_in(client, admin.email, PASSWORD)).status_code == 200

    # The detail view shows the operator the delivery that is stuck.
    body = (await client.get(f"/v1/admin/payments/{created['id']}")).json()
    row = next(d for d in body["deliveries"] if d["id"] == delivery_id)
    assert row["status"] == "failed"
    assert row["attempts"] == 8
    assert row["event_type"] == models.EVENT_COMPLETED
    assert row["last_error"] == "ConnectError: connection refused"

    res = await client.post(f"/v1/admin/payments/{created['id']}/redeliver")
    assert res.status_code == 200, res.text
    assert res.json()["redelivered"] == 1
    assert res.json()["delivery_ids"] == [delivery_id]

    async with session_factory() as session:
        delivery = await session.get(models.EventDelivery, delivery_id)
        # Due now, with a fresh budget: a new, deliberate send rather than the tail of
        # the one that already exhausted itself.
        assert delivery.status == models.DELIVERY_RETRYING
        assert delivery.attempts == 0
        assert delivery.next_attempt_at is not None
        assert delivery.last_error is None

    entry = await _audit_row("admin.payment_redelivered")
    assert entry.actor_account_id == admin.id
    assert entry.target_type == "Payment"
    assert entry.details["deliveries"] == 1
    assert entry.details["delivery_ids"] == [delivery_id]


async def test_redelivering_a_payment_with_no_webhook_is_a_404(client):
    """Saying "sent" when nothing exists would be worse than saying nothing."""
    admin, merchant, store, created = await make_disputed_payment(client)

    res = await client.post(f"/v1/admin/payments/{created['id']}/redeliver")
    assert res.status_code == 404
    assert res.json()["detail"] == "no_deliveries_for_payment"
