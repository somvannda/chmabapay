"""The schema pipeline itself: migrations must reproduce the models.

This is the acceptance test for the Alembic adoption. It runs in a subprocess
against its own throwaway SQLite file so it never touches the suite's database
and never races the `_fresh_db` fixture.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _alembic(*args: str, url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )


def test_migrations_reproduce_the_models(tmp_path: Path) -> None:
    """`upgrade head` on an empty database must land exactly on the models.

    `alembic check` is the assertion: it diffs the migrated schema against the
    model metadata and exits non-zero when they disagree, so this fails the
    moment a model changes without a matching revision.
    """
    url = f"sqlite+aiosqlite:///{(tmp_path / 'migrations.db').as_posix()}"

    upgrade = _alembic("upgrade", "head", url=url)
    assert upgrade.returncode == 0, f"upgrade failed:\n{upgrade.stdout}\n{upgrade.stderr}"

    check = _alembic("check", url=url)
    assert check.returncode == 0, (
        "migrations do not match the models — a revision is missing:\n"
        f"{check.stdout}\n{check.stderr}"
    )


def test_migrations_are_idempotent_and_tolerate_legacy_shapes(tmp_path: Path) -> None:
    """Re-running is a no-op, and 0002 converges a pre-Alembic database.

    The legacy shapes (a vestigial `store_id` on api_keys, uniqueness stored as
    an index rather than a constraint) are built on top of revision 0001 to
    mimic a database provisioned by the old `create_all()` path.
    """
    db = tmp_path / "legacy.db"
    url = f"sqlite+aiosqlite:///{db.as_posix()}"

    assert _alembic("upgrade", "0001", url=url).returncode == 0

    # Rebuild the legacy shapes 0002 is expected to clean up.
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE api_keys ADD COLUMN store_id INTEGER")
        conn.execute("ALTER TABLE webhook_endpoints ADD COLUMN store_id INTEGER")
        conn.execute("DROP INDEX ix_stores_external_id")

    legacy_check = _alembic("check", url=url)
    assert legacy_check.returncode != 0, "the legacy drift should be detectable"

    converge = _alembic("upgrade", "head", url=url)
    assert converge.returncode == 0, f"0002 failed:\n{converge.stdout}\n{converge.stderr}"

    converged = _alembic("check", url=url)
    assert converged.returncode == 0, (
        "0002 did not converge the legacy schema:\n"
        f"{converged.stdout}\n{converged.stderr}"
    )

    # Running again must change nothing.
    again = _alembic("upgrade", "head", url=url)
    assert again.returncode == 0
    still_clean = _alembic("check", url=url)
    assert still_clean.returncode == 0


def test_the_billing_migration_retires_open_invoices_without_touching_the_schedule(
    tmp_path: Path,
) -> None:
    """A-15, the deploy that ambushes nobody.

    `0012` voids every invoice that was open when it ran. They predate the reminder machinery,
    so no tier rows exist for them and none can honestly be invented — leaving one open would
    let the first enforcement sweep freeze an account that was never warned. What the migration
    must *not* do is change what any account is entitled to, and that is `next_billing_at`.

    The rows are written as raw SQL against revision `0011`, because that is the shape a
    production database has when this migration runs: the ORM models describe the schema
    *after* it, and inserting through them would test a state that never occurs.
    """
    db = tmp_path / "legacy-billing.db"
    url = f"sqlite+aiosqlite:///{db.as_posix()}"

    assert _alembic("upgrade", "0011", url=url).returncode == 0

    created = "2026-06-15 08:00:00.000000"
    renewal = "2026-07-15 08:00:00.000000"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO accounts (id, email, name, status, created_at, updated_at,"
            " whitelabel_enabled, is_platform_admin)"
            " VALUES (1, 'legacy@billing.test', 'Legacy', 'active', ?, ?, 0, 0)",
            (created, created),
        )
        conn.execute(
            "INSERT INTO plans (id, name, code, monthly_fee_cents, base_payments_included,"
            " currency, max_keys_per_account, max_webhooks_per_account, priority_support,"
            " is_public, is_active, is_featured, created_at, updated_at)"
            " VALUES (1, 'Starter', 'starter', 999, 15000, 'USD', 3, 10, 0, 1, 1, 0, ?, ?)",
            (created, created),
        )
        conn.execute(
            "INSERT INTO plan_subscriptions (id, account_id, plan_id, status, next_billing_at,"
            " created_at, updated_at) VALUES (1, 1, 1, 'active', ?, ?, ?)",
            (renewal, created, created),
        )
        conn.execute(
            "INSERT INTO plan_invoices (id, account_id, subscription_id, period_month, status,"
            " base_fee_cents, usage_payments_count, overage_payments_count, overage_fee_cents,"
            " total_due_cents, created_at, updated_at)"
            " VALUES (1, 1, 1, '2026-06', 'open', 999, 0, 0, 0, 999, ?, ?)",
            (created, created),
        )

    upgraded = _alembic("upgrade", "head", url=url)
    assert upgraded.returncode == 0, f"0012 failed:\n{upgraded.stdout}\n{upgraded.stderr}"

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT status, void_reason, voided_at, period_start, period_end, due_at"
            " FROM plan_invoices WHERE id = 1"
        ).fetchone()
        (entitled,) = conn.execute(
            "SELECT next_billing_at FROM plan_subscriptions WHERE id = 1"
        ).fetchone()

    status, reason, voided_at, period_start, period_end, due_at = row
    assert status == "void"
    assert reason == "pre_lifecycle"
    assert voided_at is not None
    # `created_at` is a real column, so the window start is portable SQL rather than a guess.
    assert period_start == created
    # And the two dates nobody can honestly compute are left NULL instead of invented: step 3
    # retires the row a statement later, so a guess would only be a lie in the audit trail.
    assert period_end is None
    assert due_at is None
    assert entitled == renewal, "what the account is entitled to must not move"

    # The debt is retired rather than reserved: the sweep can claim the same window again,
    # which is the whole reason the new index is partial.
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO plan_invoices (id, account_id, subscription_id, period_month, status,"
            " period_start, base_fee_cents, usage_payments_count, overage_payments_count,"
            " overage_fee_cents, total_due_cents, created_at, updated_at)"
            " VALUES (2, 1, 1, '2026-07', 'open', ?, 999, 0, 0, 0, 999, ?, ?)",
            (renewal, renewal, renewal),
        )

    # And the way down has to survive the rows it just voided — it cannot know which of them
    # were open, so it leaves them retired rather than guessing.
    assert _alembic("downgrade", "0011", url=url).returncode == 0
