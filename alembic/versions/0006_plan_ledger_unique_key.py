"""Give the usage ledger a unique key, and clear the rows the old writer produced

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-17

`plan_ledger_entries` was written by two statements that could not agree with each
other: an `INSERT ... ON CONFLICT DO NOTHING` per payment, and then an `UPDATE` that
added the amount to **every** row for the account-month. The `ON CONFLICT` clause had
nothing to match — this table had no unique constraint on the columns it named, only a
primary key on `id` and three non-unique indexes — so every payment inserted a fresh
row, and each row's counters drifted to "the period so far" rather than "this payment".
N payments left N rows valued N, N-1, … 1, summing to N(N+1)/2 instead of N.

Adding the unique constraint fixes the cause: it is what makes the writer's conflict
clause do something, and it is what stops a retry double-counting. `resource_type` is
part of the key so a reversal can be recorded beside the payment it gives back.

**The existing rows are deleted, not migrated.** Their values are sums across rows that
no longer exist in a recoverable form; the per-row numbers cannot be un-mixed, and the
period totals are wrong in a way the row itself does not disclose. Reconstructing from
`payments` is possible but is a reporting decision rather than a schema one. Deleting is
safe because nothing reads this table — verified for P0-6: the only references in `src/`
are the model and the statements that wrote it, and quota is enforced from
`payments` directly via `_count_paid_payments_this_month`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM plan_ledger_entries")
    with op.batch_alter_table("plan_ledger_entries") as batch:
        batch.create_unique_constraint(
            "uq_plan_ledger_resource",
            ["period_month", "resource_type", "resource_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("plan_ledger_entries") as batch:
        batch.drop_constraint("uq_plan_ledger_resource", type_="unique")
