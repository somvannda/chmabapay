"""Rate limits and the CORS allowlist (P1-1).

Two controls, both of which have to hold for reasons that are easy to get wrong:

* A limit must actually stop a caller, and must count the right thing — the API
  key where there is one (unspoofable), the address where there is not.
* The CORS wildcard must be gone, which is only meaningful if an unknown origin
  really is refused.
"""

from __future__ import annotations

import pytest
from conftest import make_account, make_key

from chmabapay.config import Settings, get_settings
from chmabapay.main import app, cors_origins


@pytest.fixture
def tight(monkeypatch):
    """Turn the ceilings down so a test can reach one in a few requests.

    Patched on the cached `Settings` instance, which is the same object every
    call site reads through `get_settings()`, and restored by monkeypatch.
    """
    settings = get_settings()
    for name, limit in {
        "rate_limit_payment_create_per_minute": 2,
        "rate_limit_khqr_per_minute": 2,
        "rate_limit_auth_per_minute": 2,
        "rate_limit_checkout_per_minute": 2,
        "rate_limit_api_per_minute": 2,
    }.items():
        monkeypatch.setattr(settings, name, limit, raising=False)
    return settings


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #
async def test_payment_creation_is_capped_per_key(client, tight):
    """The path that mints an ABA session stops at its ceiling."""
    account = await make_account()
    await make_key(account)
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}
    body = {"amount": 1.0, "hosted_qr": False}

    first = await client.post("/v1/payments", json=body, headers=headers)
    second = await client.post("/v1/payments", json=body, headers=headers)
    assert first.status_code != 429
    assert second.status_code != 429
    # Limits are reported while there is still room, so an integrator can back off
    # before being refused.
    assert second.headers["X-RateLimit-Limit"] == "2"
    assert second.headers["X-RateLimit-Remaining"] == "0"

    refused = await client.post("/v1/payments", json=body, headers=headers)
    assert refused.status_code == 429
    assert refused.json()["detail"] == "rate_limited: payment_create"
    assert refused.json()["limit"] == 2
    assert refused.json()["window_seconds"] == 60
    assert int(refused.headers["Retry-After"]) >= 1


async def test_a_second_key_has_its_own_allowance(client, tight):
    """The counter is per caller, not a global ceiling one tenant can exhaust."""
    spent = await make_account(email="spent@chmaba.test")
    spent_key, _ = await make_key(spent)
    fresh = await make_account(email="fresh@chmaba.test")
    fresh_key, _ = await make_key(fresh)
    body = {"amount": 1.0, "hosted_qr": False}

    for _ in range(2):
        await client.post(
            "/v1/payments", json=body, headers={"Authorization": f"Bearer {spent_key}"}
        )
    exhausted = await client.post(
        "/v1/payments", json=body, headers={"Authorization": f"Bearer {spent_key}"}
    )
    assert exhausted.status_code == 429

    # A different tenant is unaffected, which is the difference between a limit
    # and an outage.
    other = await client.post(
        "/v1/payments", json=body, headers={"Authorization": f"Bearer {fresh_key}"}
    )
    assert other.status_code != 429


async def test_the_unauthenticated_khqr_surface_is_capped_per_address(
    client, tight, monkeypatch
):
    """A rotated Bearer token must not buy a fresh bucket.

    `/v1/khqr/*` needs no key and still reaches out to ABA, so it is counted by
    address. If it were counted by whatever credential the caller presented, a
    made-up token per request would be an unlimited supply of buckets.

    The merchant-name lookup is stubbed because it fetches a real PayWay page,
    and this suite has to stay green with no network.
    """
    from chmabapay.routers import khqr as khqr_router

    async def no_lookup(url: str, timeout: float = 5.0) -> str:
        return "<html></html>"

    monkeypatch.setattr(khqr_router, "fetch_link_html", no_lookup)
    body = {"link": "https://link.payway.com.kh/ABAPAYpe518710Y", "amount": 1.0}

    first = await client.post(
        "/v1/khqr/from-link", json=body, headers={"Authorization": "Bearer made-up-1"}
    )
    second = await client.post(
        "/v1/khqr/from-link", json=body, headers={"Authorization": "Bearer made-up-2"}
    )
    third = await client.post(
        "/v1/khqr/from-link", json=body, headers={"Authorization": "Bearer made-up-3"}
    )

    assert first.status_code != 429
    assert second.status_code != 429
    assert third.status_code == 429
    assert third.json()["detail"] == "rate_limited: khqr"


async def test_the_credential_surface_is_capped_per_address(client, tight):
    """`/auth/*` is the brute-force target, so it gets the tightest ceiling."""
    for _ in range(2):
        attempt = await client.post("/auth/signout")
        assert attempt.status_code != 429

    refused = await client.post("/auth/signout")
    assert refused.status_code == 429
    assert refused.json()["detail"] == "rate_limited: auth"


async def test_the_public_checkout_surface_is_capped_per_address(client, tight):
    """A payer's browser hits this; a stranger should not be able to flood it."""
    for _ in range(2):
        assert (await client.get("/pay/whatever")).status_code != 429

    refused = await client.get("/pay/whatever")
    assert refused.status_code == 429
    assert refused.json()["detail"] == "rate_limited: checkout"


async def test_health_and_the_dev_gateway_are_never_limited(client, tight):
    """A throttled health check reads as a dead service; /_dev is not production."""
    for _ in range(5):
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/_dev/nonexistent")).status_code != 429


async def test_one_bucket_does_not_spend_another(client, tight):
    """`/pay/*` is exhausted without touching the `/v1/*` allowance."""
    for _ in range(3):
        await client.get("/pay/whatever")

    assert (await client.get("/health")).status_code == 200

    account = await make_account()
    raw_key, _ = await make_key(account)
    listed = await client.get(
        "/v1/stores", headers={"Authorization": f"Bearer {raw_key}"}
    )
    assert listed.status_code != 429


async def test_limits_can_be_switched_off(monkeypatch, client, tight):
    """`RATE_LIMIT_ENABLED=false` is what a load generator needs."""
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", False, raising=False)

    for _ in range(4):
        assert (await client.get("/pay/whatever")).status_code != 429


# --------------------------------------------------------------------------- #
# CORS
# --------------------------------------------------------------------------- #
def test_cors_origins_prefers_an_operator_supplied_list():
    assert cors_origins(
        Settings(cors_allowed_origins="https://pos.example.com, https://app.example.com")
    ) == ["https://pos.example.com", "https://app.example.com"]


def test_cors_origins_falls_back_to_the_public_origin_and_localhost_in_dev():
    assert cors_origins(
        Settings(enable_dev_gateway=False, public_origin="https://pay.chmaba.com/")
    ) == ["https://pay.chmaba.com"]

    dev = cors_origins(Settings(enable_dev_gateway=True, public_origin=None))
    assert "http://localhost:3001" in dev
    assert "http://127.0.0.1:3001" in dev


def test_cors_origins_is_empty_when_nothing_is_configured():
    """No wildcard, and no accidental fallback to one."""
    assert cors_origins(Settings(enable_dev_gateway=False, public_origin=None)) == []


async def test_a_known_origin_is_allowed_and_an_unknown_one_is_not(client):
    """The plan's exit criterion: the allowlist actually refuses."""
    allowed = await client.get("/health", headers={"Origin": "http://localhost:3001"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3001"
    assert allowed.headers["access-control-allow-credentials"] == "true"

    refused = await client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in refused.headers


async def test_a_refusal_still_carries_the_cors_headers(client, tight):
    """A 429 a browser cannot read is indistinguishable from the network dying.

    This is what pins the middleware order: the limiter has to sit *inside* CORS
    so its own response passes back out through it.
    """
    for _ in range(3):
        response = await client.get(
            "/pay/whatever", headers={"Origin": "http://localhost:3001"}
        )

    assert response.status_code == 429
    assert response.headers["access-control-allow-origin"] == "http://localhost:3001"
    assert app.state.rate_limiter is not None
