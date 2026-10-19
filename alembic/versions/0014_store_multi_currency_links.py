"""Let one store hold a payment link per currency

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-19

A store could hold exactly one payment link (`payment_links.store_id` was unique), and
a link settles in exactly one currency. That made "one shop, dollars and riel"
impossible without a second store, which costs a plan slot and splits the merchant's
reporting.

ABA cannot be asked to change a link's currency — the currency is in the link's own
payload and a `currency` field in the mint request is silently dropped, with the QR
still coming back in the link's currency. So the only honest model is one link *per
currency*: this migration replaces `unique(store_id)` with `unique(store_id, currency)`.

No data changes. Every existing link keeps its store and its currency; the new
constraint simply allows a second row per store in a different currency.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sqlite_link_table(*constraints: sa.Constraint) -> sa.Table:
    """`payment_links` as of this revision, for SQLite's copy-and-move rebuild.

    SQLite cannot drop a column-level UNIQUE — it lives on as an internal auto-index,
    reflected as an index rather than a constraint — so `drop_constraint` cannot find it
    and the only route is rebuilding the table. Batch mode rebuilds from the *reflected*
    schema by default, which would carry the old uniqueness across, so it is handed the
    desired definition instead. A migration is a snapshot, which is why spelling the
    columns out here is correct rather than duplication.
    """
    return sa.Table(
        "payment_links",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("link_type", sa.String(24), nullable=False),
        sa.Column("raw_link", sa.Text(), nullable=False),
        sa.Column("merchant_account_id", sa.String(120), nullable=False),
        sa.Column("merchant_name", sa.String(120), nullable=True),
        sa.Column("payway_client_id", sa.String(64), nullable=True),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("verification", sa.String(16), nullable=False),
        sa.Column("is_sandbox", sa.Boolean(), nullable=False),
        sa.Column("min_amount_cents", sa.Integer(), nullable=False),
        sa.Column("max_amount_cents", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *constraints,
        sa.Index("ix_payment_links_store_id", "store_id"),
    )


# The old constraint was created by `unique=True` on the column, so its name depends on
# how the schema happened to be created. Rather than guess (`payment_links_store_id_key`
# is the usual result, not a guarantee), find the unique constraint whose key is exactly
# `store_id` and drop that one. A wrong guess here would leave the old uniqueness in
# place and make the first KHR link fail with a constraint error nobody could read.
_DROP_SINGLE_COLUMN_UNIQUE = """
DO $$
DECLARE con_name text;
BEGIN
    FOR con_name IN
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = 'payment_links'
          AND c.contype = 'u'
          AND c.conkey = ARRAY[
              (SELECT a.attnum FROM pg_attribute a
               WHERE a.attrelid = t.oid AND a.attname = 'store_id')
          ]
    LOOP
        EXECUTE format('ALTER TABLE payment_links DROP CONSTRAINT %I', con_name);
    END LOOP;
END $$;
"""


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    """Replace `unique(store_id)` with `unique(store_id, currency)`."""
    if _is_sqlite():
        with op.batch_alter_table(
            "payment_links",
            copy_from=_sqlite_link_table(
                sa.UniqueConstraint(
                    "store_id", "currency", name="uq_store_link_currency"
                )
            ),
            recreate="always",
        ):
            pass
        return

    op.execute(_DROP_SINGLE_COLUMN_UNIQUE)
    op.create_unique_constraint(
        "uq_store_link_currency", "payment_links", ["store_id", "currency"]
    )


def downgrade() -> None:
    if _is_sqlite():
        # Fails loudly on a store that holds two links, at the constraint rather than by
        # deleting one: a downgrade is not a licence to discard a payout destination.
        with op.batch_alter_table(
            "payment_links",
            copy_from=_sqlite_link_table(
                sa.UniqueConstraint("store_id", name="payment_links_store_id_key")
            ),
            recreate="always",
        ):
            pass
        return

    op.drop_constraint("uq_store_link_currency", "payment_links", type_="unique")
    # Deliberately lets this fail on a store that holds two links instead of deleting one.
    op.create_unique_constraint(
        "payment_links_store_id_key", "payment_links", ["store_id"]
    )
