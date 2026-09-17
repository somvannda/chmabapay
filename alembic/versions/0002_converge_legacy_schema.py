"""Converge legacy schema left behind by the pre-Alembic era

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-15

Databases created by the old `create_all()` + hand-written SQL path accumulated
structures the models no longer declare. They are harmless to run against, but
they make every future `--autogenerate` noisy and hide real drift, so this
revision normalises them.

Every step is existence-checked: a database built fresh from 0001 already
matches the models, and running this against it must change nothing.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Dropped from the models when sub-merchants were merged into stores. They only
# exist on databases old enough to have been provisioned before that merge.
LEGACY_COLUMNS = (
    ("api_keys", "store_id"),
    ("webhook_endpoints", "store_id"),
)

STORES_UNIQUE_NAME = "uq_store_account_external"
STORES_EXTERNAL_INDEX = "ix_stores_external_id"


def _inspector():  # type: ignore[no-untyped-def]
    return sa.inspect(op.get_bind())


def _has_column(inspector, table: str, column: str) -> bool:  # type: ignore[no-untyped-def]
    return any(c["name"] == column for c in inspector.get_columns(table))


def _has_index(inspector, table: str, name: str) -> bool:  # type: ignore[no-untyped-def]
    return any(i.get("name") == name for i in inspector.get_indexes(table))


def _has_unique_constraint(inspector, table: str, name: str) -> bool:  # type: ignore[no-untyped-def]
    return any(c.get("name") == name for c in inspector.get_unique_constraints(table))


def upgrade() -> None:
    inspector = _inspector()

    # 1. Vestigial per-store scoping columns. Dropping the column drops its own
    #    foreign key with it on both backends, so no separate drop_constraint.
    for table, column in LEGACY_COLUMNS:
        if _has_column(inspector, table, column):
            op.drop_column(table, column)

    # 2. The models declare this uniqueness as a UniqueConstraint; older
    #    databases created it as a standalone unique index. Postgres will not
    #    accept a constraint that duplicates an existing unique index, so the
    #    index has to go first.
    if _has_index(inspector, "stores", STORES_UNIQUE_NAME) and not _has_unique_constraint(
        inspector, "stores", STORES_UNIQUE_NAME
    ):
        op.drop_index(STORES_UNIQUE_NAME, table_name="stores")
    if not _has_unique_constraint(inspector, "stores", STORES_UNIQUE_NAME):
        op.create_unique_constraint(
            STORES_UNIQUE_NAME, "stores", ["account_id", "external_id"]
        )

    # 3. `Store.external_id` is indexed in the models but older databases never
    #    got the index.
    if not _has_index(inspector, "stores", STORES_EXTERNAL_INDEX):
        op.create_index(STORES_EXTERNAL_INDEX, "stores", ["external_id"])


def downgrade() -> None:
    """Recreate the legacy shapes. Data in the dropped columns is gone."""
    inspector = _inspector()

    if _has_index(inspector, "stores", STORES_EXTERNAL_INDEX):
        op.drop_index(STORES_EXTERNAL_INDEX, table_name="stores")

    op.add_column("api_keys", sa.Column("store_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "api_keys_store_id_fkey", "api_keys", "stores", ["store_id"], ["id"]
    )
    op.add_column(
        "webhook_endpoints", sa.Column("store_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "webhook_endpoints_store_id_fkey",
        "webhook_endpoints",
        "stores",
        ["store_id"],
        ["id"],
    )
