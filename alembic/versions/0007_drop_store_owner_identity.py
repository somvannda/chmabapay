"""Drop the store owner identity columns

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-17

`stores.owner_name`, `owner_phone` and `owner_email` were collected when a store
was created and then read by almost nothing. What they were *for* — merchant due
diligence — is explicitly not a platform feature: migration `6-drop-kyc.sql`
deleted every identity column from `accounts`, and `docs/roadmap.md` records that
KYB is the operator's own legal duty rather than something the platform captures.
So the platform carried a merchant's name, phone and email with no decision
hanging off any of them.

What did read them, and what replaces it:

- `khqr.py` used `store.owner_name` as the merchant name printed on a KHQR,
  falling back to `store.name`. It now falls back directly to `store.name`; the
  link's own `merchant_name` (from the ABA PayWay share link) still takes
  precedence, so the name a payer sees is unchanged for any store whose link
  carries one.
- `webhooks.py` echoed `owner_email` inside `data.store`. That field is removed
  from the event payload — a webhook consumer reading it will now see it absent.

Dropping a column discards data, and this is the one destructive step in the
change. It is deliberate: the values are contact metadata with no reader, and
keeping the columns to preserve them would leave exactly the trap this removes —
a column that looks like it gates something and does not.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("owner_name", sa.String(length=120)),
    ("owner_phone", sa.String(length=40)),
    ("owner_email", sa.String(length=255)),
)


def upgrade() -> None:
    with op.batch_alter_table("stores") as batch:
        for name, _type in _COLUMNS:
            batch.drop_column(name)


def downgrade() -> None:
    with op.batch_alter_table("stores") as batch:
        for name, type_ in _COLUMNS:
            batch.add_column(sa.Column(name, type_, nullable=True))
