"""Rename audit_logs.admin_account_id to actor_account_id

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-15

The column holds whoever performed a privileged action. It was named for the
admin because the first writers were operator routes, but `billing.py` already
wrote a *tenant* account into it, and the rest of the P1-2 work records merchants
acting on their own keys, webhooks and stores. A name that says "admin" would be
read as "an operator did this", which is the one question an audit trail must
never answer wrongly.

Nothing reads the column yet — there is no audit view until this revision's
sibling work — so renaming it costs nothing.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch:
        batch.alter_column("admin_account_id", new_column_name="actor_account_id")


def downgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch:
        batch.alter_column("actor_account_id", new_column_name="admin_account_id")
