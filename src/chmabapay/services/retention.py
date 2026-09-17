"""Retention of stored gateway payloads (P1-4).

`docs/roadmap.md` requires that per-merchant credentials be secured and that a
retention policy be *defined and enforced*. Defined is a document; enforced is
this module.

The one column that needs a clock on it is `payments.gateway_status_raw`. It
holds the ABA PayWay response verbatim, and for a hosted checkout that response
contains the session token minted for that payment. Those sessions live about a
minute. Keeping them forever buys nothing — nothing reads the field once the
payment is terminal — while quietly accumulating a credential per payment, which
is exactly the kind of thing that turns a database dump into a much worse
database dump.

Only that column is cleared. Status, amounts, timestamps, `bakong_ref` and the QR
survive, because they are the accounting record, and `attempt_history` survives
because it holds derived detection signals ("matched_amount_cents", "signals")
rather than raw gateway responses — there is no credential in it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import null, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


async def purge_gateway_payloads(
    session: AsyncSession,
    *,
    older_than_days: int,
    now: datetime | None = None,
) -> int:
    """Clear raw gateway payloads on payments older than the window.

    Returns the number of rows cleared, which is what a test can assert on and
    what an operator can sanity-check the first time it runs.

    A non-positive window means **disabled**, not "purge everything". A `0` that
    silently reads as "delete all history" is the one misconfiguration this
    function must not turn into an outage, so it is refused by name.
    """
    if older_than_days <= 0:
        log.warning(
            "gateway-payload retention is disabled (older_than_days=%s); "
            "nothing was purged",
            older_than_days,
        )
        return 0

    cutoff = (now or _now()) - timedelta(days=older_than_days)
    result = await session.execute(
        update(models.Payment)
        .where(
            models.Payment.created_at < cutoff,
            models.Payment.gateway_status_raw.is_not(None),
        )
        # `null()` rather than `None`: this must write SQL NULL, not the JSON
        # literal `null`. The JSON type stores Python None as the literal unless
        # told otherwise, and a purge that leaves the literal behind reports the
        # same rows as purged on every run.
        .values(gateway_status_raw=null())
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    purged = int(result.rowcount or 0)
    if purged:
        log.info(
            "purged raw gateway payloads from %d payment(s) older than %s days",
            purged,
            older_than_days,
        )
    return purged
