"""Give plan invoices a real period window, and hold stores when a plan shrinks

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-22

`plan_invoices` was keyed on `(account_id, period_month)` — a calendar month — while the
credit a subscription buys is a rolling 30 days. The two drift, and the drift is not
cosmetic: `2026-01-31 + 30d = 2026-03-02` skips February entirely, and later a 30-day step
can land twice in one calendar month, where the second attempt collides with the unique key
and the period is silently never billed.

So the period becomes what it always should have been: an explicit window of instants.
`period_start`/`period_end` say what the invoice is a claim for, `due_at` says when it was
expected, and the key moves to `(subscription_id, period_start)` — which cannot drift
because it is the same number the schedule is built from.

Four decisions worth naming:

* **The unique index is partial, excluding `void`.** A voided invoice is a historical record
  rather than a claim, so it must not reserve its window forever. With a plain unique key,
  voiding an invoice for a period still in force would make that period permanently
  unbillable — the operator has no way back.

* **The window columns are nullable, with no `server_default`.** The value cannot be
  computed for a row that never had one, and `NULL` is the truthful answer. Both SQLite and
  Postgres treat `NULL` keys as distinct, so pre-lifecycle rows simply do not participate in
  the new index.

* **Every currently-open invoice is voided.** They predate the reminder machinery: no tier
  rows exist for them and none can honestly be invented, so leaving one open would let the
  first enforcement run freeze an account that was never warned. The debt is retired and the
  sweep re-raises for the same window with a real `due_at`. Nothing about what an account is
  entitled to changes here — no `next_billing_at` is touched.

* **`stores.billing_suspended_at` is a flag, not a status value.** `disable_store`
  overwrites `status` and `enable_store` then has to infer what to restore, so a status
  cannot carry the "why" — and a billing hold would overwrite an operator's deliberate
  disable. A separate column keeps the two independent.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The statuses that mean "not settled", spelled out rather than imported: a migration has
# to keep meaning what it meant when it ran, and `services.billing.UNPAID_STATUSES` is free
# to change later. Matches it today.
_UNPAID = ("open", "draft", "issued")
_UNPAID_SQL = ", ".join(f"'{status}'" for status in _UNPAID)

# The predicate both dialects need so the index is reproducible from the model. Spelled the
# same in `models.PlanInvoice.__table_args__`; `alembic check` compares the two.
_LIVE_PERIOD = sa.text("status != 'void'")


def upgrade() -> None:
    # 1. The window.
    with op.batch_alter_table("plan_invoices") as batch:
        batch.add_column(sa.Column("period_start", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("period_end", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("due_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("voided_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("void_reason", sa.String(32)))

    # 2. Give historical rows a window start. `created_at` is a real column, so this is
    #    portable SQL. `period_end` and `due_at` stay NULL: we do not know when those rows
    #    were due, and computing a guess would need dialect-specific date arithmetic for
    #    rows that step 3 retires a statement later.
    op.execute(
        "UPDATE plan_invoices SET period_start = created_at WHERE period_start IS NULL"
    )

    # 3. Retire every open claim that predates the reminders.
    op.execute(
        sa.text(
            "UPDATE plan_invoices SET status = 'void', void_reason = 'pre_lifecycle', "
            f"voided_at = :now WHERE status IN ({_UNPAID_SQL})"
        ).bindparams(now=datetime.now(UTC))
    )

    # 4. The key moves from the drifting calendar month to the window.
    with op.batch_alter_table("plan_invoices") as batch:
        batch.drop_constraint("uq_plan_invoice_period", type_="unique")

    op.create_index(
        "uq_plan_invoice_live_period",
        "plan_invoices",
        ["subscription_id", "period_start"],
        unique=True,
        sqlite_where=_LIVE_PERIOD,
        postgresql_where=_LIVE_PERIOD,
    )

    # 5. The reminder record. Empty at this point: the worker that writes it ships later,
    #    and an empty table is the truthful state until then.
    op.create_table(
        "plan_invoice_reminders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "invoice_id",
            sa.Integer(),
            sa.ForeignKey("plan_invoices.id"),
            nullable=False,
        ),
        sa.Column("tier", sa.String(24), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.JSON()),
        sa.UniqueConstraint(
            "invoice_id", "tier", "channel", name="uq_plan_invoice_reminder"
        ),
    )
    op.create_index(
        "ix_plan_invoice_reminders_invoice_id",
        "plan_invoice_reminders",
        ["invoice_id"],
    )

    # 6. The store hold. Nullable with no backfill, so no existing store changes behaviour.
    with op.batch_alter_table("stores") as batch:
        batch.add_column(sa.Column("billing_suspended_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    with op.batch_alter_table("stores") as batch:
        batch.drop_column("billing_suspended_at")

    op.drop_index("ix_plan_invoice_reminders_invoice_id", "plan_invoice_reminders")
    op.drop_table("plan_invoice_reminders")

    op.drop_index("uq_plan_invoice_live_period", "plan_invoices")
    with op.batch_alter_table("plan_invoices") as batch:
        batch.create_unique_constraint(
            "uq_plan_invoice_period",
            ["account_id", "period_month"],
        )

    # The new columns go last: the unique constraint above needs `period_month`, and the
    # rows that step 3 voided stay voided — there is no way to know which of them were open
    # before, and guessing on the way down is worse than leaving them retired.
    with op.batch_alter_table("plan_invoices") as batch:
        batch.drop_column("void_reason")
        batch.drop_column("voided_at")
        batch.drop_column("due_at")
        batch.drop_column("period_end")
        batch.drop_column("period_start")
