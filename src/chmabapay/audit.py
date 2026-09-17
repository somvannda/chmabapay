"""Audit trail for privileged mutations.

`record()` is deliberately a plain `session.add`: the entry joins the caller's
transaction, so the change and its audit row commit together or not at all. An
audit trail with a window where a mutation exists unattributed is worse than no
audit trail, because it looks complete.

The actor is the account that performed the action — an operator on an admin
route, a merchant on their own key, webhook or store. The question the record
answers is "who did this", not "was an operator involved".
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from . import models


def record(
    session: AsyncSession,
    *,
    actor: models.Account,
    action: str,
    target_type: str,
    target_id: int,
    details: dict | None = None,
) -> models.AuditLog:
    """Stage one audit row in the caller's transaction. The caller commits."""
    entry = models.AuditLog(
        actor_account_id=actor.id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details or None,
    )
    session.add(entry)
    return entry
