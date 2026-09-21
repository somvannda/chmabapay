import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings

logger = logging.getLogger(__name__)

# src/chmabapay/db.py -> src/chmabapay -> src -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]

_settings = get_settings()
_W1_CONCURRENCY = int(getattr(_settings, "worker_w1_concurrency", 20) or 20)

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    # W1 runs `worker_w1_concurrency` reconciliations at once and holds a session
    # for each of them across its multi-second ABA status call. The async default
    # (5 + 10 overflow) sits below that, so the workers drained the pool and every
    # other caller queued behind them — W4's expiry sweep logged "QueuePool limit
    # of size 5 overflow 10 reached" on every tick. Sizing the pool to the number
    # of workers is the relationship the concurrency setting already implies.
    pool_size=max(5, _W1_CONCURRENCY),
    max_overflow=max(10, _W1_CONCURRENCY),
)
session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a scoped async session."""
    async with session_factory() as session:
        yield session


async def seed_default_plans(session: AsyncSession) -> None:
    """Create the default pricing plans, and backfill their presentation copy.

    Simplified CutLuy-style model: single account type, three public tiers,
    quota = paid payments/month. The plans differ only in price, quota, store
    count, API-key count and (Pro only) support — every other capability is
    identical across tiers. `max_keys_per_account` / `max_webhooks_per_account`
    are account-wide because the workspace holds one account, not one per store.

    Plans are edited in the admin console, so this **never overwrites an existing
    plan**: it only creates missing ones and fills `tagline`/`features` while they
    are still empty.
    """
    from . import models

    now = datetime.now(UTC)
    plans = [
        {
            "code": "free",
            "name": "Free",
            "monthly_fee_cents": 0,
            "base_payments_included": 3000,
            "max_stores": 1,
            "max_keys_per_account": 1,
            "max_webhooks_per_account": 10,
            "priority_support": False,
            "is_public": True,
            "is_active": True,
            "tagline": "For finding your rhythm.",
            "is_featured": False,
            "features": [
                "1 store",
                "1 API key",
                "3,000 payments / month",
                "Webhook signing",
                "CSV reports export",
            ],
        },
        {
            "code": "starter",
            "name": "Starter",
            "monthly_fee_cents": 999,
            "base_payments_included": 15000,
            "max_stores": 5,
            "max_keys_per_account": 3,
            "max_webhooks_per_account": 10,
            "priority_support": False,
            "is_public": True,
            "is_active": True,
            "tagline": "For teams making moves.",
            "is_featured": True,
            "features": [
                "Up to 5 stores",
                "3 API keys",
                "15,000 payments / month",
                "Webhook signing",
                "CSV reports export",
            ],
        },
        {
            "code": "pro",
            "name": "Pro",
            "monthly_fee_cents": 5999,
            "base_payments_included": 1000000,
            "max_stores": 50,
            "max_keys_per_account": 10,
            "max_webhooks_per_account": 10,
            "priority_support": True,
            "is_public": True,
            "is_active": True,
            "tagline": "For teams ready to scale.",
            "is_featured": False,
            "features": [
                "Up to 50 stores",
                "10 API keys",
                "1,000,000 payments / month",
                "Webhook signing",
                "CSV reports export",
                "Priority support",
            ],
        },
    ]

    # Deactivate legacy tiers that are no longer offered.
    legacy_codes = {"growth", "scale", "enterprise"}
    for code in legacy_codes:
        res = await session.execute(
            select(models.Plan).where(models.Plan.code == code)
        )
        legacy = res.scalar_one_or_none()
        if legacy is not None and legacy.is_active:
            legacy.is_active = False
            legacy.is_public = False
            legacy.updated_at = now

    for data in plans:
        res = await session.execute(
            select(models.Plan).where(models.Plan.code == data["code"])
        )
        existing = res.scalar_one_or_none()
        if existing is None:
            session.add(models.Plan(**data))
            continue
        changed = False
        if not existing.tagline and data["tagline"]:
            existing.tagline = data["tagline"]
            changed = True
        if not existing.features and data["features"]:
            existing.features = data["features"]
            changed = True
        if changed:
            existing.updated_at = now
    await session.commit()


def _alembic_config():
    """The Alembic config, or None when the repo layout is not present."""
    from alembic.config import Config

    ini = _REPO_ROOT / "alembic.ini"
    return Config(str(ini)) if ini.exists() else None


async def run_migrations() -> None:
    """Bring the database up to head. The only path that changes schema."""
    cfg = _alembic_config()
    if cfg is None:
        raise RuntimeError(f"alembic.ini not found under {_REPO_ROOT}; cannot migrate")

    from alembic import command

    # alembic/env.py drives its own event loop, so it must not run inside ours.
    await asyncio.to_thread(command.upgrade, cfg, "head")


async def ensure_schema() -> None:
    """Refuse to start against a database that does not match this code.

    `create_all()` could only ever create missing *tables*; it never added a
    missing *column*. So a deployment could boot happily onto a schema it would
    crash on at the first query — which is exactly what happened when `payments`
    was missing `reissued_from_id` while every other table was present. Today
    migrations are the only path, and being stale is a startup error that names
    the command to fix it.
    """
    settings = get_settings()
    if not settings.schema_check:
        logger.warning("Schema check disabled (SCHEMA_CHECK=false); assuming the DB is current")
        return

    cfg = _alembic_config()
    if cfg is None:
        logger.warning("alembic.ini not found under %s; skipping the schema check", _REPO_ROOT)
        return

    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(cfg).get_current_head()

    async with engine.connect() as conn:
        has_version = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).has_table("alembic_version")
        )
        current: str | None = None
        if has_version:
            current = (
                await conn.execute(text("select version_num from alembic_version"))
            ).scalar()

    if current is None:
        raise RuntimeError(
            "This database is not managed by Alembic. Run `alembic upgrade head` for a "
            f"new database, or `alembic stamp {head}` if it was created by the old "
            "create_all() path and already matches the models. Starting anyway would "
            "risk queries against columns that do not exist."
        )
    if current != head:
        raise RuntimeError(
            f"Database is at revision {current!r} but this code expects {head!r}. "
            "Run `alembic upgrade head` before starting the app."
        )


async def seed_plans_if_needed() -> None:
    from . import models

    async with session_factory() as session:
        res = await session.execute(select(func.count(models.Plan.id)))
        plan_count = res.scalar_one()
        if get_settings().enable_dev_gateway or plan_count == 0:
            await seed_default_plans(session)
