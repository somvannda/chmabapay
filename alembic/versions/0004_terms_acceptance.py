"""Record acceptance of the merchant agreement

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-15

`docs/roadmap.md` records that merchant due diligence is the operator's own legal
duty rather than a platform feature, and `supabase/migrations/6-drop-kyc.sql`
removed every identity column to match. That decision leaves exactly one
compliance fact the platform *must* hold, because only the platform can hold it:
evidence that a given merchant agreed to the agreement.

Two nullable columns. Null means "never accepted", which is the honest starting
state for every account that already exists — backfilling a timestamp would
fabricate consent that was never given, and that is the one thing this table must
not contain.

The version is stored with the timestamp because "when" without "to what" cannot
answer whether an account has accepted the *current* text.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("terms_accepted_version", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("terms_accepted_version")
        batch.drop_column("terms_accepted_at")
