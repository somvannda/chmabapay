"""The schema pipeline itself: migrations must reproduce the models.

This is the acceptance test for the Alembic adoption. It runs in a subprocess
against its own throwaway SQLite file so it never touches the suite's database
and never races the `_fresh_db` fixture.
"""

from __future__ import annotations

import os
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
