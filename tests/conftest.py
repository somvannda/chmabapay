"""Shared test fixtures. Must set env vars BEFORE importing chmabapay modules."""

from __future__ import annotations

import os

TEST_DB = "chmabapay_test.db"

# Tests deliberately do NOT inherit the app's DATABASE_URL. An ambient one — a
# shell export, a developer's .env — would aim the destructive fixtures below at
# a real database. CI selects Postgres with the dedicated variable instead.
TEST_DATABASE_URL = os.getenv(
    "CHMABAPAY_TEST_DATABASE_URL", f"sqlite+aiosqlite:///{TEST_DB}"
)
if (
    not TEST_DATABASE_URL.startswith("sqlite")
    and "test" not in TEST_DATABASE_URL.rsplit("/", 1)[-1].lower()
):
    raise RuntimeError(
        f"refusing to run destructive test fixtures against {TEST_DATABASE_URL!r}: "
        "the database name must contain 'test'"
    )

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["WORKERS_ENABLED"] = "false"
os.environ["ENABLE_DEV_GATEWAY"] = "true"
# Tests build the schema straight from the models (see _fresh_db below), so the
# app's Alembic head check does not apply to them.
os.environ["SCHEMA_CHECK"] = "false"

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from chmabapay import models  # noqa: E402
from chmabapay.db import engine, session_factory  # noqa: E402
from chmabapay.main import app  # noqa: E402
from chmabapay.schemas import LinkIn, StoreCreate  # noqa: E402
from chmabapay.security import hash_key  # noqa: E402
from chmabapay.services import stores as store_svc  # noqa: E402


@pytest_asyncio.fixture
async def client():
    """The app under test, driven in-process over ASGI.

    `http://localhost`, not a made-up host: `_set_session_cookie` marks the cookie
    Secure for any non-localhost host, and a Secure cookie is not sent back over
    plain http — so a session-authenticated test would get a 401 from a sign-in
    that had actually succeeded. `test_admin_plans.py` and `test_audit.py` already
    use this base URL for exactly that reason; this makes the shared fixture stop
    being the one that silently breaks it.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
        yield c


def remove_test_db() -> None:
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


@pytest.fixture(autouse=True)
def _no_live_aba(request, monkeypatch):
    """Keep the suite off the ABA PayWay rail.

    Creating a payment for a PayWay link defaults to a *hosted* session, which is
    a real network call to ABA. Without this the suite failed whenever ABA was
    unreachable, and every green run was spending live checkout sessions.

    Only the one function that reaches the network is replaced, so the hosted
    path stays exercised end to end and gets a realistic session back — including
    ABA's own ~180s expiry, which is what makes the TTL assertions meaningful.
    Tests that want our offline encoder still ask for it with `hosted_qr=False`.

    `live`-marked tests are deliberately exempt: talking to ABA for real is the
    entire reason they exist. See tests/test_live_aba.py.
    """
    if request.node.get_closest_marker("live"):
        return

    from chmabapay.services import payments as payments_svc
    from chmabapay.services.payway_parser import HostedCheckout

    async def fake_create_hosted_checkout(slug_or_url, amount, *, timeout=25.0):
        return HostedCheckout(
            # A real PayWay payload captured during the KHQR work, used verbatim so
            # anything that parses, hashes or re-renders the TLV sees genuine
            # structure: the PAYWAY@ABA private template at 62.50 (which itself
            # carries sub-tag 02, the bill number), Tag 30's switch id and merchant
            # account, and a trailing CRC. The same capture, with assertions over its
            # structure and CRC, is `CAPTURED_PAYWAY_PAYLOAD` in tests/test_khqr.py.
            qr_string="00020101021230390016abaakhppxxx@abaa0115126071610243081520489995303840540115802KH5914SOMVANNDA KONG6003N/A629950950010PAYWAY@ABA01232364634-518710-262481770216TAG50_VERIFY_0030616TAG50_VERIFY_00305101789021696630451AA",
            client_id="test-client-id",
            token="test-session-token-0123456789abcdef",
            request_time="1700000000000",
            tran_id=None,
            expires_in_seconds=180,
            raw={"step": "request_qr", "status": {"message": "test double"}},
        )

    monkeypatch.setattr(
        payments_svc, "create_hosted_checkout", fake_create_hosted_checkout
    )


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Every test starts with empty request counters.

    Limits are counted per caller, and the whole suite is one caller: one
    process, one client address. Without this, a test that deliberately exhausts
    a bucket would leak its count into whichever test ran next.
    """
    limiter = getattr(app.state, "rate_limiter", None)
    if limiter is not None:
        limiter.reset()


@pytest.fixture(autouse=True)
async def _fresh_db():
    # pytest-asyncio gives every test its own event loop, but the engine is
    # module-level because the app owns it. Any connection it pools would then be
    # handed to the next test while still bound to the previous test's loop. On
    # SQLite that is invisible; on Postgres it fails every test at setup with
    # "another operation is in progress", which is why this suite is only
    # trustworthy once it has run against Postgres (see docs/production-readiness.md
    # P0-4). Dropping the pool per test keeps connections on the loop that made them.
    await engine.dispose()
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.drop_all)
        await conn.run_sync(models.Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.drop_all)
        # `drop_all` only knows the models, so the Alembic stamp would survive and
        # leave the database claiming to be at head while holding no tables — the
        # precise "boots happily onto a schema it would crash on" state that
        # ensure_schema() exists to refuse. Wipe the stamp with the schema.
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await engine.dispose()


async def make_account(email: str = "pos@chmaba.test", name: str = "Chmaba POS"):
    async with session_factory() as session:
        account = models.Account(email=email, name=name)
        session.add(account)
        await session.commit()
        await session.refresh(account)
        return account


async def make_store(
    account,
    name: str = "Sokha Cafe",
    owner: str = "Sokha",
    external_id: str | None = None,
) -> models.Store:
    async with session_factory() as session:
        store = await store_svc.create_store(
            session,
            account,
            StoreCreate(
                name=name,
                external_id=external_id,
                link=LinkIn(
                    raw_link=f"https://link.payway.com.kh/{owner.lower()}",
                    merchant_account_id=f"{owner.lower()}payway",
                    merchant_name=name,
                ),
            ),
        )
        return store


async def make_key(
    account,
    *,
    mode: str = "live",
) -> tuple[str, models.ApiKey]:
    raw = f"ck_{mode}_testkey-{os.urandom(8).hex()}"
    api_key = models.ApiKey(
        account_id=account.id,
        scope=models.KEY_ACCOUNT_SCOPE,
        key_prefix=raw[:18],
        key_hash=hash_key(raw),
        mode=mode,
    )
    async with session_factory() as session:
        session.add(api_key)
        await session.commit()
    return raw, api_key


async def make_webhook(
    account,
    *,
    url: str = "https://sink.example.com/hook",
    secret: str = "whsec_testsecret",
):
    endpoint = models.WebhookEndpoint(
        account_id=account.id,
        url=url,
        secret_key=secret,
    )
    async with session_factory() as session:
        session.add(endpoint)
        await session.commit()
        await session.refresh(endpoint)
    return endpoint


async def active_store_count(account) -> int:
    async with session_factory() as session:
        from sqlalchemy import func

        return (
            await session.execute(
                select(func.count(models.Store.id)).where(
                    models.Store.account_id == account.id,
                    models.Store.status == models.STORE_ACTIVE,
                )
            )
        ).scalar_one()
