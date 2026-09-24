"""Support tickets: a thread header and its messages

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-23

Decision D-4. The Pro plan has advertised "priority support" since the plan matrix was
written, and nothing implemented it: a merchant who paid for it had no way to open a
ticket, no way to read a reply, and no target anyone was measuring against. This is the
storage half of making that claim true.

Two tables, and the split between them is the design decision worth naming.

* **The header carries what the platform routes on** — status, priority, assignment,
  first-response and resolution instants. Everything the operator queue filters, sorts or
  highlights lives here, so answering "which requests are past target?" is a query over
  one table rather than an aggregate over message rows.

* **The conversation lives in `support_messages`, including the opening message.** The
  tempting shortcut is a `body` column on the header for the opening text, which reads
  fine until something has to render a thread: then the first message is in one place and
  the rest in another, and every reader has to special-case it. Message #1 as a row gives
  the thread one order and one shape, at the cost of one insert.

`priority` is copied from the plan at open time rather than derived on read. That is
deliberate and is the whole of what makes the promise mean something: an account that
downgrades next month keeps the queue position it was given on the request it already
opened, and one that upgrades does not overtake requests that arrived before it paid.

Nothing is backfilled. There is no support data to migrate — the tables are new, and an
empty table is the truthful state.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "support_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(40), nullable=False),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column(
            "assigned_admin_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id"),
            nullable=True,
        ),
        sa.Column("first_response_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # `unique=True` with `index=True` on the model produces a unique *index*, not a
    # constraint — the same shape `stores` and `payments` already use, and the shape
    # `alembic check` compares against.
    op.create_index(
        "ix_support_requests_public_id", "support_requests", ["public_id"], unique=True
    )
    op.create_index(
        "ix_support_requests_account_id", "support_requests", ["account_id"]
    )
    op.create_index("ix_support_requests_category", "support_requests", ["category"])
    op.create_index("ix_support_requests_status", "support_requests", ["status"])
    op.create_index("ix_support_requests_priority", "support_requests", ["priority"])
    op.create_index(
        "ix_support_requests_assigned_admin_account_id",
        "support_requests",
        ["assigned_admin_account_id"],
    )

    op.create_table(
        "support_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "request_id",
            sa.Integer(),
            sa.ForeignKey("support_requests.id"),
            nullable=False,
        ),
        sa.Column(
            "author_account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column("author_kind", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_support_messages_request_id", "support_messages", ["request_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_support_messages_request_id", "support_messages")
    op.drop_table("support_messages")

    op.drop_index(
        "ix_support_requests_assigned_admin_account_id", "support_requests"
    )
    op.drop_index("ix_support_requests_priority", "support_requests")
    op.drop_index("ix_support_requests_status", "support_requests")
    op.drop_index("ix_support_requests_category", "support_requests")
    op.drop_index("ix_support_requests_account_id", "support_requests")
    op.drop_index("ix_support_requests_public_id", "support_requests")
    op.drop_table("support_requests")
