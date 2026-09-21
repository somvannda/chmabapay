"""Give a store a first-class "internal" flag

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-21

The platform runs its own collection store ("ChmabaPay HQ") so it can receive plan
fees. That store hangs off the platform-admin account, and every rule that keys off
"this account's stores" therefore treated the platform as one of its own merchants:

* `check_plan_quota` counted plan fees paid BY merchants toward the platform's own
  monthly quota, so the platform could exhaust its own paid-transaction allowance
  collecting money.
* `mark_paid` wrote a usage/volume ledger row against the platform's account for every
  plan fee.
* The console summed every paid payment platform-wide, so platform revenue read as
  merchant GMV.

Deciding this by *who owns the store* would put an identity branch in the money path.
Instead a store is marked `is_internal` when it belongs to the platform, and the rules
key off that property of the store. A merchant store is `false`; the default backfills
that, because nothing existing was the platform's own store under this definition.

`nullable=False` with `server_default=sa.text("false")` for the same reason `0010`'s
downgrade used one: the column is added to a populated table, so a real default is
required, and `false` is the only truthful value for every existing row.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("stores") as batch:
        batch.add_column(
            sa.Column(
                "is_internal",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("stores") as batch:
        batch.drop_column("is_internal")
