"""Payment reversal, and the end of the detection window

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-17

Two facts the platform could not previously hold, both about money that has
already moved.

`detection_closed_at` — when we stopped reconciling a payment. ABA's hosted
checkout keeps accepting a payment after its own 180s QR window closes: a real
payment on 2026-09-17 settled 9.5 minutes after our mirrored expiry. Detection
therefore has to outlast the rail's willingness to accept, and it used to stop at a
hardcoded 900 seconds with nothing recording that it had stopped. A merchant whose
customer says the money left their account deserves a better answer than "we
stopped looking, and did not write down when".

`reversed_at` / `reversal_reason` — a refund. ABA offers us no callback and the
hosted status endpoint reports no reversal, so nothing in the platform could
express "this was paid and then given back". Without it a refunded payment reads
`paid` forever and revenue is overstated permanently, with no way to correct it.

All three are nullable with no backfill: null means "not closed" / "not reversed",
which is the true state of every existing row. The new `superseded` and `reversed`
*statuses* need no schema change — `payments.status` is already a string column,
and constraining it to a set of values would have to be undone the next time the
lifecycle grows a state.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("payments") as batch:
        batch.add_column(
            sa.Column("detection_closed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("reversal_reason", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("payments") as batch:
        batch.drop_column("reversal_reason")
        batch.drop_column("reversed_at")
        batch.drop_column("detection_closed_at")
