"""W3 BillingInvoiceWorker: issue the plan invoices that have come due.

This was an M1 stub whose only job was to keep the queue registry shape stable. The
work it now does is small on purpose — find subscriptions whose period has ended, raise
one invoice per period, advance the schedule — because every part that could go wrong
twice (a retry, a restart, two replicas) is made safe by the unique key on
`(account_id, period_month)` rather than by this loop being careful. See
`services/billing.py` for the rules and `alembic/versions/0009_*` for the constraint.

What it deliberately does **not** do: charge anything. An invoice is a record that
money is owed; the merchant pays it through their own billing page, which mints a KHQR
against the platform's HQ store. Issuing and collecting are separate on purpose, so a
failure to collect never hides a failure to bill.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..db import session_factory
from ..services import billing as billing_svc
from .base import Worker
from .job import Job

log = logging.getLogger(__name__)

QUEUE = "billing.invoices"


def billing_heartbeat_dedup_key(now: datetime) -> str:
    """One sweep per hour, enforced by the transport's dedup rather than by this
    process remembering: with `inprocess` there is one process, but with `redis`
    there may be several, and a per-process timer would have each of them sweep."""
    return f"billing:{now:%Y-%m-%dT%H}"


def billing_heartbeat_job_payload() -> dict[str, str]:
    return {"type": "issue_due_invoices"}


class BillingInvoiceWorker(Worker):
    prefetch = 1
    concurrency = 1

    queue_name = QUEUE

    async def process(self, job: Job) -> dict[str, object]:
        async with session_factory() as session:
            summary = await billing_svc.issue_due_invoices(session)
        log.info("W3 billing sweep: %s", summary)
        return {"ok": True, "processed_at": job.created_at.isoformat(), **summary}
