"""W6 BillingLifecycleWorker — the dunning clock, and the one irreversible step on it.

Two job types on one queue, because they are one clock read at two points in an hour:

* `remind` — record the tier each unpaid invoice has reached (§5.2). Bookkeeping only: nothing
  is delivered, and the banner is derived from `due_at` rather than from the rows this writes.
* `enforce` — freeze the accounts whose grace has run out, and retire the parked purchases
  nobody paid for (§5.4). Gated by `billing_enforce_enabled`, so the sequence can be observed in
  production for a full cycle before it is allowed to cost anyone anything.

Separate job types rather than one combined sweep, because the flag only belongs on one of them:
`remind` and the abandoned-purchase cleanup are safe at any time, and a deployment that wants to
watch the sequence without freezing anybody must still get its reminders.

Nothing here collects money or changes a plan. An invoice is a record that money is owed, the
merchant pays it through their billing page, and the two are separate so a failure to collect
can never hide a failure to bill.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ..db import session_factory
from ..services import billing as billing_svc
from .base import Worker
from .job import Job

log = logging.getLogger(__name__)

QUEUE = "billing.lifecycle"

JOB_REMIND = "remind"
JOB_ENFORCE = "enforce"


def heartbeat_dedup_key(job_type: str, now: datetime) -> str:
    """One sweep per hour per job type, deduped by the transport rather than by this process.

    Same shape as `w3_billing.billing_heartbeat_dedup_key`: with `redis` there may be several
    replicas, and a per-process timer would have each of them sweep — which for this job would
    mean several workers racing to record the same tier, and for `enforce` several workers racing
    to freeze the same account. The transport's dedup is what makes those two races impossible
    rather than merely unlikely.

    The key carries the job type, so `remind` and `enforce` are deduped independently and either
    can be enqueued without the other.
    """
    return f"billing-{job_type}:{now:%Y-%m-%dT%H}"


def reminder_heartbeat_job_payload() -> dict[str, str]:
    return {"type": JOB_REMIND}


def enforce_heartbeat_job_payload() -> dict[str, str]:
    return {"type": JOB_ENFORCE}


class BillingLifecycleWorker(Worker):
    prefetch = 1
    concurrency = 1

    queue_name = QUEUE

    async def process(self, job: Job) -> dict[str, Any]:
        job_type = job.payload.get("type") or JOB_REMIND
        if job_type == JOB_REMIND:
            async with session_factory() as session:
                summary = await billing_svc.record_due_reminders(session)
            log.info("W6 reminder sweep: %s", summary)
            return {"ok": True, "job": job_type, "processed_at": job.created_at.isoformat(), **summary}
        if job_type == JOB_ENFORCE:
            async with session_factory() as session:
                summary = await billing_svc.enforce_grace(session)
            log.info("W6 enforce sweep: %s", summary)
            return {"ok": True, "job": job_type, "processed_at": job.created_at.isoformat(), **summary}
        # An unknown type is skipped rather than raised on: during a rolling deploy an older
        # worker can receive a type a newer one enqueued, and failing the job would retry it
        # against the same old code until it landed in the dead-letter queue.
        return {"skipped": True, "reason": "unknown_job_type", "type": job_type}
