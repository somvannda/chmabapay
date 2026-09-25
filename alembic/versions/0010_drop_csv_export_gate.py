"""Drop the per-plan CSV export gate

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-21

`plans.csv_export_enabled` was a per-plan switch that **no longer gated anything**,
and the products of that were worse than having no switch at all:

- Every seeded plan carried `true`, Free included, so `GET /v1/reports/payments.csv`
  could only ever return 200. The 403 branch in `routers/reports.py` was unreachable.
- Three surfaces nevertheless described it as a paid feature: the 403 message
  ("Upgrade to Starter to unlock"), `docs/api.md`'s error table, and the admin
  console's plan editor, where an operator could switch it off and watch CSV keep
  working.
- The store-catalog export on the same page was assembled in the browser from
  `GET /api/v1/stores`, so it never touched the gate at all.

Decision D6 of the launch-gap-closure spec settled it as "CSV is available on every
plan", which is what the code has always actually done. The gate is deleted from
`routers/reports.py` and the flag is removed from the plan matrix
(`routers/billing.py`), the admin plan CRUD (`routers/admin.py`), both front-ends and
the docs, so nothing is left claiming a limit that is not enforced.

Retaining the column while nothing reads it is the specific trap 0008 removed for
`account_type`: a field the console displays, an operator can edit, and no code
obeys. Dropping it discards only values that were never consulted.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("plans") as batch:
        batch.drop_column("csv_export_enabled")


def downgrade() -> None:
    # NOT NULL, so re-adding needs a server default to backfill: `true` is what every
    # row held, and what the column meant before the drop.
    with op.batch_alter_table("plans") as batch:
        batch.add_column(
            sa.Column(
                "csv_export_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            )
        )
