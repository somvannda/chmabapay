"""Give plan invoices a natural key, so a month can only be billed once

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-18

`plan_invoices` had no constraint on `(account_id, period_month)`, only an index that
made lookups fast without making duplicates impossible. Nothing wrote the table yet —
the billing worker was an M1 stub — so the gap was invisible, but the moment that
worker ran on a schedule it would have been reachable: issue, retry, issue again, and
the merchant owes two months' fees for one period with nothing in the row to say which
was which.

The constraint is the fix, and it is the same shape as `0006` made for
`plan_ledger_entries`: the period is the natural key, so a second insert is a no-op
rather than a second charge.

**No backfill.** The table is empty in every deployment (the worker never ran), so
there is nothing to reconcile, and the `DELETE` below exists only to make that a
guarantee rather than an assumption — a manual row created by hand while testing would
otherwise break the constraint creation.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM plan_invoices")
    with op.batch_alter_table("plan_invoices") as batch:
        batch.create_unique_constraint(
            "uq_plan_invoice_period",
            ["account_id", "period_month"],
        )


def downgrade() -> None:
    with op.batch_alter_table("plan_invoices") as batch:
        batch.drop_constraint("uq_plan_invoice_period", type_="unique")
