"""Account security self-service: changing the email, rotating the password, closing
the account.

The theme is proof. A session cookie is enough to *read* an account, and it used to be
enough to move its email address — which is how an account is taken over rather than
merely read. Everything here either verifies the caller with something only the owner
has, or refuses and says why.
"""

from __future__ import annotations

import re

import httpx
import pytest_asyncio
from conftest import make_account, make_key, make_store, make_webhook
from fastapi.routing import APIRoute
from sqlalchemy import select

from chmabapay import models
from chmabapay.auth import get_current_auth_context, get_hybrid_context
from chmabapay.db import seed_default_plans, session_factory
from chmabapay.main import app
from chmabapay.routers.auth import (
    RESTRICTED_ALLOWED_WRITES,
    RESTRICTED_REFUSED_READS,
    SESSION_COOKIE,
    _make_session_jwt,
    get_current_session_account,
)
from chmabapay.security import hash_password, verify_password

BASE_URL = "http://localhost"
PASSWORD = "correct horse battery"
NEW_PASSWORD = "a much longer passphrase"


async def seed_plans() -> None:
    async with session_factory() as session:
        await seed_default_plans(session)


@pytest_asyncio.fixture
async def plain_client():
    """A client with no cookies, for requests that carry their own credential."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as c:
        yield c


async def make_account_row(
    *, with_password: bool = True, admin: bool = False, email: str = "sokha@chmaba.test"
) -> models.Account:
    account = await make_account(email=email, name="Sokha Cafe")
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        if with_password:
            row.password_hash = hash_password(PASSWORD)
        row.is_platform_admin = admin
        await session.commit()
        await session.refresh(row)
        return row


def session_client(row: models.Account, amr: str = "password") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=BASE_URL,
        cookies={SESSION_COOKIE: _make_session_jwt(row, amr)},
    )


async def reload_account(account_id: int) -> models.Account:
    async with session_factory() as session:
        row = await session.get(models.Account, account_id)
        assert row is not None
        return row


# --------------------------------------------------------------------------- #
# Email
# --------------------------------------------------------------------------- #
async def test_an_email_change_needs_the_current_password():
    row = await make_account_row()
    async with session_client(row) as client:
        # No password at all: refused, and the address is untouched.
        missing = await client.post("/v1/me/email", json={"email": "new@chmaba.test"})
        assert missing.status_code == 401
        assert missing.json()["detail"] == "invalid_password"

        wrong = await client.post(
            "/v1/me/email",
            json={"email": "new@chmaba.test", "current_password": "not it"},
        )
        assert wrong.status_code == 401

        assert (await reload_account(row.id)).email == "sokha@chmaba.test"

        ok = await client.post(
            "/v1/me/email",
            json={"email": "New@Chmaba.test", "current_password": PASSWORD},
        )
        assert ok.status_code == 200, ok.text
        # Normalised, because the address is the sign-in identity and two casings of
        # it must not be two accounts.
        assert ok.json()["email"] == "new@chmaba.test"

    saved = await reload_account(row.id)
    assert saved.email == "new@chmaba.test"

    async with session_factory() as session:
        entry = (
            await session.execute(
                select(models.AuditLog).where(
                    models.AuditLog.action == "account.email_changed"
                )
            )
        ).scalars().one()
    assert entry.actor_account_id == row.id
    # Domains only: the previous address must not outlive an erasure request in a log.
    assert entry.details["domain"] == "chmaba.test"
    assert entry.details["previous_domain"] == "chmaba.test"


async def test_an_email_change_cannot_land_on_a_taken_address():
    row = await make_account_row()
    await make_account(email="already@chmaba.test", name="Other")

    async with session_client(row) as client:
        res = await client.post(
            "/v1/me/email",
            json={"email": "already@chmaba.test", "current_password": PASSWORD},
        )
    assert res.status_code == 400
    assert res.json()["detail"] == "email_already_taken"


async def test_a_passwordless_account_cannot_move_its_email():
    """No password means no proof, and we have no mail provider to fall back on."""
    row = await make_account_row(with_password=False)

    async with session_client(row, amr="google") as client:
        res = await client.post(
            "/v1/me/email", json={"email": "new@chmaba.test", "current_password": None}
        )
    assert res.status_code == 409
    assert res.json()["detail"].startswith("email_change_requires_password:")
    assert (await reload_account(row.id)).email == "sokha@chmaba.test"


async def test_patching_the_email_is_refused_rather_than_ignored():
    """A silently dropped field is worse than a rejected one — the caller believes it worked."""
    row = await make_account_row()

    async with session_client(row) as client:
        res = await client.patch("/v1/me", json={"email": "sneaky@chmaba.test"})
        assert res.status_code == 422

        # The field it does own still works.
        renamed = await client.patch("/v1/me", json={"name": "Sokha Cafe II"})
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "Sokha Cafe II"


# --------------------------------------------------------------------------- #
# Password
# --------------------------------------------------------------------------- #
async def test_a_password_change_checks_the_old_one_and_takes_effect():
    row = await make_account_row()

    async with session_client(row) as client:
        wrong = await client.post(
            "/v1/me/password",
            json={"current_password": "not it", "new_password": NEW_PASSWORD},
        )
        assert wrong.status_code == 401
        assert wrong.json()["detail"] == "invalid_password"

        same = await client.post(
            "/v1/me/password",
            json={"current_password": PASSWORD, "new_password": PASSWORD},
        )
        assert same.status_code == 400
        assert same.json()["detail"] == "password_unchanged"

        ok = await client.post(
            "/v1/me/password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
        assert ok.status_code == 200, ok.text

    saved = await reload_account(row.id)
    assert verify_password(NEW_PASSWORD, saved.password_hash)
    assert not verify_password(PASSWORD, saved.password_hash)

    async with session_factory() as session:
        entry = (
            await session.execute(
                select(models.AuditLog).where(
                    models.AuditLog.action == "account.password_changed"
                )
            )
        ).scalars().one()
    assert entry.actor_account_id == row.id


async def test_a_google_only_account_has_no_password_to_change():
    row = await make_account_row(with_password=False)

    async with session_client(row, amr="google") as client:
        res = await client.post(
            "/v1/me/password",
            json={"current_password": "anything", "new_password": NEW_PASSWORD},
        )
    assert res.status_code == 409
    assert res.json()["detail"] == "no_password_set"
    # Still password-less: a session must not be able to mint a credential from nothing.
    assert (await reload_account(row.id)).password_hash is None


# --------------------------------------------------------------------------- #
# Erasure
# --------------------------------------------------------------------------- #
async def test_erasing_an_account_anonymises_it_and_switches_every_credential_off(
    plain_client,
):
    row = await make_account_row()
    store = await make_store(row, name="Sokha Cafe")
    raw_key, api_key = await make_key(row)
    endpoint = await make_webhook(row, url="https://hooks.example.com/sokha")

    # A settled payment, so there is an accounting record to protect.
    created = await plain_client.post(
        "/v1/payments",
        json={"amount": 5.0, "store": store.public_id, "hosted_qr": False},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert created.status_code == 201, created.text
    payment_public_id = created.json()["id"]

    async with session_client(row) as client:
        wrong_confirm = await client.request(
            "DELETE",
            "/v1/me",
            json={"confirm_email": "someone@else.test", "current_password": PASSWORD},
        )
        assert wrong_confirm.status_code == 400
        assert wrong_confirm.json()["detail"] == "confirm_email_does_not_match"

        wrong_password = await client.request(
            "DELETE",
            "/v1/me",
            json={"confirm_email": row.email, "current_password": "not it"},
        )
        assert wrong_password.status_code == 401

        # Neither refusal did anything.
        still_there = await reload_account(row.id)
        assert still_there.email == "sokha@chmaba.test"
        assert still_there.status == models.ACCOUNT_ACTIVE

        erased = await client.request(
            "DELETE",
            "/v1/me",
            json={"confirm_email": row.email, "current_password": PASSWORD},
        )
        assert erased.status_code == 200, erased.text

        # The session died with the account: `get_current_session_account` refuses a
        # suspended one.
        assert (await client.get("/v1/me")).status_code == 401

    saved = await reload_account(row.id)
    assert saved.email == f"erased-{row.id}@chmabapay.invalid"
    assert saved.name == "Erased account"
    assert saved.google_sub is None
    assert saved.password_hash is None
    assert saved.status == models.ACCOUNT_SUSPENDED

    async with session_factory() as session:
        store_row = await session.get(models.Store, store.id)
        assert store_row is not None
        assert store_row.status == models.STORE_DISABLED

        key_row = await session.get(models.ApiKey, api_key.id)
        assert key_row is not None
        assert key_row.revoked_at is not None

        endpoint_row = await session.get(models.WebhookEndpoint, endpoint.id)
        assert endpoint_row is not None
        assert endpoint_row.status == "disabled"

        # The money trail survives: amounts, statuses and references are the
        # accounting record of a sale that really happened.
        payment_row = (
            await session.execute(
                select(models.Payment).where(
                    models.Payment.public_id == payment_public_id
                )
            )
        ).scalar_one()
        assert payment_row.amount_cents == 500

        entry = (
            await session.execute(
                select(models.AuditLog).where(
                    models.AuditLog.action == "account.erased"
                )
            )
        ).scalars().one()
    assert entry.target_id == row.id
    assert entry.details == {
        "stores_disabled": 1,
        "keys_revoked": 1,
        "webhooks_disabled": 1,
    }


async def test_a_platform_admin_cannot_erase_itself():
    row = await make_account_row(admin=True)

    async with session_client(row) as client:
        res = await client.request(
            "DELETE",
            "/v1/me",
            json={"confirm_email": row.email, "current_password": PASSWORD},
        )
    assert res.status_code == 409
    assert res.json()["detail"].startswith("platform_admin_cannot_self_delete:")
    assert (await reload_account(row.id)).status == models.ACCOUNT_ACTIVE


def test_the_sign_in_redirect_survives_the_oauth_round_trip():
    """A deep link must still be the destination after signing in.

    The client that hits a 401 sends `next=<path + query>` to `/user/google/auth/
    login`; the path has to survive the trip out to Google and back, or every
    deep link lands on the dashboard and the user has to navigate again. It
    travels inside the signed `state` parameter, which is the only part of the
    handshake we control on both ends.
    """
    from chmabapay.routers.auth import _decode_google_state, _encode_google_state

    deep_link = "/dashboard/reports?from=2026-08-01&statuses=paid,reversed"
    state = _encode_google_state(deep_link)
    assert _decode_google_state(state)["next"] == deep_link


def test_the_sign_in_redirect_refuses_to_leave_the_site():
    """`next` is attacker-controlled input: it arrives on a URL anyone can send.

    A protocol-relative value (`//evil.example`) looks relative to a naive
    `startswith("/")` check but is an absolute URL to the browser, which is how an
    open redirect turns a sign-in link into a phishing page. An unsafe value is
    dropped rather than honoured, so the sign-in still succeeds — just at the
    default destination.
    """
    from chmabapay.routers.auth import _decode_google_state, _encode_google_state, _is_safe_next

    for hostile in ("//evil.example/x", "https://evil.example", "evil.example", "", None):
        assert not _is_safe_next(hostile), hostile
        assert "next" not in _decode_google_state(_encode_google_state(hostile))

    for safe in ("/dashboard", "/dashboard/reports?x=1", "/#plans"):
        assert _is_safe_next(safe), safe


# --------------------------------------------------------------------------- #
# A frozen (restricted) account
# --------------------------------------------------------------------------- #
async def set_status(account_id: int, status: str) -> None:
    async with session_factory() as session:
        row = await session.get(models.Account, account_id)
        assert row is not None
        row.status = status
        await session.commit()


def merchant_gated_routes() -> list[tuple[str, str]]:
    """Every route that authenticates a merchant, as (method, path).

    Read out of the app rather than listed by hand, because the thing this exists to catch is a
    route nobody thought about. A route reaches the gate through one of the choke points —
    `get_current_auth_context` for the API surface, `get_current_session_account` for the portal —
    so a route that uses either is covered, and one that uses neither is not a merchant route.

    The walk recurses because `include_router` in this FastAPI version mounts a wrapper rather
    than copying routes into `app.routes`; without that, the walk finds almost nothing and the
    test would pass by looking at an empty list.
    """
    gates = {get_current_auth_context, get_current_session_account, get_hybrid_context}
    found: list[tuple[str, str]] = []
    for route in _api_routes(app.routes):
        if not (_dependency_calls(route.dependant) & gates):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            found.append((method, route.path))
    return found


def _api_routes(routes):
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _api_routes(inner.routes)


def _dependency_calls(dependant) -> set:
    calls = set()
    for sub in dependant.dependencies:
        if sub.call is not None:
            calls.add(sub.call)
        calls |= _dependency_calls(sub)
    return calls


def concrete(path: str) -> str:
    """A request path for a templated route: `/v1/stores/{public_id}` -> `/v1/stores/x`."""
    return re.sub(r"\{[^}]+\}", "x", path)


async def test_a_frozen_account_reads_and_pays_but_cannot_write():
    """§7.4: the freeze takes capability, never the ability to pay.

    Reads stay open, because the portal has to render and the merchant has to see what they owe.
    `POST /v1/billing/change-plan` stays open because it is two of the three ways out (§7.5) —
    pay, or move to a plan you can afford. Everything that would run the business is refused with
    a code an integrator can act on rather than a bare 403.
    """
    row = await make_account_row(email="frozen@chmaba.test")
    await seed_plans()
    await set_status(row.id, models.ACCOUNT_RESTRICTED)

    async with session_client(row) as frozen:
        for path in (
            "/v1/me",
            "/v1/billing/subscription",
            "/v1/billing/invoices",
            "/v1/stores",
            "/v1/payments",
            "/v1/keys",
            "/v1/webhooks",
        ):
            assert (await frozen.get(path)).status_code == 200, path

        for method, path in (
            ("POST", "/v1/stores"),
            ("POST", "/v1/keys"),
            ("POST", "/v1/webhooks"),
            ("PATCH", "/v1/me"),
            ("DELETE", "/v1/me"),
        ):
            res = await frozen.request(method, path, json={})
            assert res.status_code == 403, f"{method} {path}"
            assert res.json()["detail"] == "account_restricted", f"{method} {path}"

        # A read that settles what it polls is not a read. Detection (W1) settles the payment
        # anyway, on its own sweep, so refusing this costs the merchant nothing.
        polled = await frozen.get("/v1/transactions/check-status/pay_anything")
        assert polled.status_code == 403
        assert polled.json()["detail"] == "account_restricted"

        # The escape hatch: choosing Free resolves the debt and lifts the freeze in one click.
        moved = await frozen.post("/v1/billing/change-plan", json={"plan_code": "free"})
        assert moved.status_code == 200, moved.text

    assert (await reload_account(row.id)).status == models.ACCOUNT_ACTIVE


async def test_every_mutating_route_refuses_a_frozen_account():
    """The gate is a rule, so it is proved against the app's own route table.

    A hand-written list of calls would only ever test the routes someone remembered. This walks
    `app.routes`, finds everything a merchant can reach, and requires every mutating method to
    answer `403 account_restricted` — unless it is the billing escape hatch. A route added later
    that forgets the rule fails here instead of quietly working for a frozen merchant.
    """
    row = await make_account_row(email="frozen-gate@chmaba.test")
    await seed_plans()
    await set_status(row.id, models.ACCOUNT_RESTRICTED)

    routes = merchant_gated_routes()
    assert len(routes) > 20, "the walk found almost nothing, so it is not testing the app"

    # Both halves of the rule are real paths: a typo in either constant would otherwise leave a
    # hole that no assertion could see.
    paths = {path for _method, path in routes}
    for _method, allowed in RESTRICTED_ALLOWED_WRITES:
        assert allowed in paths, allowed
    for refused in RESTRICTED_REFUSED_READS:
        assert any(path.startswith(refused) for path in paths), refused

    async with session_client(row) as frozen:
        for method, path in routes:
            if method in ("GET", "HEAD"):
                continue
            res = await frozen.request(method, concrete(path), json={})
            if (method, path) in RESTRICTED_ALLOWED_WRITES:
                assert res.status_code != 403, f"{method} {path} is the escape hatch"
                continue
            assert res.status_code == 403, f"{method} {path} -> {res.status_code}"
            assert res.json()["detail"] == "account_restricted", f"{method} {path}"


async def test_a_frozen_account_can_sign_in_but_a_suspended_one_cannot(plain_client):
    """The difference between the two states, stated as behaviour.

    A frozen merchant has to be able to sign in, or they cannot reach the one page that clears
    the debt — which is the entire reason `restricted` exists instead of reusing `suspended`.
    Suspension keeps its lockout, both at sign-in and on any session minted earlier.
    """
    row = await make_account_row(email="signin@chmaba.test")
    await set_status(row.id, models.ACCOUNT_RESTRICTED)

    signed_in = await plain_client.post(
        "/auth/login", json={"email": row.email, "password": PASSWORD}
    )
    assert signed_in.status_code == 200, signed_in.text
    # And out again: a merchant who cannot end their own session has been trapped, not frozen.
    assert (await plain_client.post("/auth/signout")).status_code == 200

    await set_status(row.id, models.ACCOUNT_SUSPENDED)
    refused = await plain_client.post(
        "/auth/login", json={"email": row.email, "password": PASSWORD}
    )
    assert refused.status_code == 403
    assert refused.json()["detail"] == "account_suspended"

    async with session_client(row) as suspended:
        assert (await suspended.get("/v1/me")).status_code == 401


async def test_an_api_key_is_refused_for_a_frozen_account(plain_client):
    """D12: the API is refused wholesale. A key is not a read-only instrument — it is the
    integration that mints payment codes — so there is no allowlist to apply to it, and a frozen
    merchant's own systems get one clear answer instead of a half-working API."""
    row = await make_account_row(email="frozen-key@chmaba.test")
    raw_key, _api_key = await make_key(row)
    await set_status(row.id, models.ACCOUNT_RESTRICTED)

    res = await plain_client.get(
        "/v1/stores", headers={"Authorization": f"Bearer {raw_key}"}
    )
    assert res.status_code == 403
    assert res.json()["detail"] == "account_restricted"

