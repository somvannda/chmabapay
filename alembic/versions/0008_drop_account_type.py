"""Drop the account type columns

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-18

`accounts.account_type` held an enum of exactly two values, `individual` and
`business`, and `account_type_explicitly_set` existed to tell "chose individual"
apart from "never chose". Both are removed here because **nothing read either
one**. The concept was retired by the product, not by this migration:

- The onboarding step that collected the choice is gone. `web/landing`'s
  `/onboarding/account-type` route is now a redirect stub whose own comment reads
  "single account type".
- KYC — the other half of the same concept, and the thing that would have
  justified a verified Business tier — was deleted outright in
  `supabase/migrations/6-drop-kyc.sql`.
- The one privilege the enum was supposed to gate, white-label checkout, is
  gated by `accounts.whitelabel_enabled` instead: a per-account boolean the admin
  console toggles, read by `services/stores.py` and `routers/checkout.py`.
- The other, sub-merchants, was never built: there is no `SubMerchant` model and
  no `routers/platform.py`.

Across the backend there was no `account_type == "business"` comparison anywhere,
only writes and echoes, so the column was write-only. `account_type_switched` on
the change-plan response was likewise hardcoded to `False`.

What the drop removes, concretely: the field on `PATCH /v1/me`, the two fields on
`GET /v1/me` and the admin account rows, the two session-payload entries, and
`account_type` plus `account_type_switched` on the change-plan response. A client
reading any of them will now see the field absent rather than a value that
implied a gate.

Dropping a column discards data, which is the one destructive step here. It is
deliberate and it costs nothing: every row held `individual`, because the only
code path that ever wrote `business` (the Starter-to-Growth auto-switch) was
disabled before this. Retaining the columns to preserve those values would leave
exactly the trap this removes — a field the console displays, an operator can
edit, and no code obeys.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("account_type")
        batch.drop_column("account_type_explicitly_set")


def downgrade() -> None:
    # Both columns were NOT NULL, so re-adding them needs a server default to
    # backfill existing rows: `individual` is the value every row held, and
    # `false` is what `account_type_explicitly_set` meant before the drop.
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(
            sa.Column(
                "account_type",
                sa.String(length=16),
                nullable=False,
                server_default="individual",
            )
        )
        batch.add_column(
            sa.Column(
                "account_type_explicitly_set",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )
