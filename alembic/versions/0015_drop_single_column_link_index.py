"""Drop the single-column unique index that 0014 could not see

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-19

`PaymentLink.store_id` was declared `unique=True, index=True`, and SQLAlchemy expresses
that pair as one **unique index** named `ix_payment_links_store_id` — not as a table
constraint. `0014` looked for unique *constraints* whose key was exactly `store_id`, found
none, and added the composite constraint beside the old index. The composite allows two
links per store; the old index still refuses them, so saving a second link failed with a
duplicate-key error.

This revision finds a unique, non-primary index whose only column is `store_id`, drops it,
and recreates the plain index the model declares (`index=True`, not unique). It also makes
sure the composite constraint exists, so a database that skipped straight from 0013 lands
in the same state.

Idempotent on purpose: production was repaired by hand while this was written, and running
this there must be a no-op rather than an error.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Any unique, non-primary index on `payment_links` whose only column is `store_id` is the
# old single-column uniqueness, whatever it ended up being called.
_DROP_SINGLE_COLUMN_UNIQUE_INDEX = """
DO $$
DECLARE idx_name text;
BEGIN
    FOR idx_name IN
        SELECT i.relname
        FROM pg_index x
        JOIN pg_class i ON i.oid = x.indexrelid
        JOIN pg_class t ON t.oid = x.indrelid
        WHERE t.relname = 'payment_links'
          AND x.indisunique
          AND NOT x.indisprimary
          AND x.indnatts = 1
          AND x.indkey[0] = (
              SELECT a.attnum FROM pg_attribute a
              WHERE a.attrelid = t.oid AND a.attname = 'store_id'
          )
    LOOP
        EXECUTE format('DROP INDEX IF EXISTS %I', idx_name);
    END LOOP;
END $$;
"""

_ENSURE_COMPOSITE = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'payment_links'::regclass
          AND conname = 'uq_store_link_currency'
    ) THEN
        ALTER TABLE payment_links
            ADD CONSTRAINT uq_store_link_currency UNIQUE (store_id, currency);
    END IF;
END $$;
"""


def upgrade() -> None:
    # SQLite already landed on the right shape in 0014, whose rebuild declares the plain
    # index and the composite constraint explicitly.
    if op.get_bind().dialect.name == "sqlite":
        return

    op.execute(_DROP_SINGLE_COLUMN_UNIQUE_INDEX)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_payment_links_store_id "
        "ON payment_links (store_id)"
    )
    op.execute(_ENSURE_COMPOSITE)


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        return

    op.execute("DROP INDEX IF EXISTS ix_payment_links_store_id")
    # Fails loudly on a store that holds two links rather than deleting one.
    op.execute(
        "CREATE UNIQUE INDEX ix_payment_links_store_id ON payment_links (store_id)"
    )
