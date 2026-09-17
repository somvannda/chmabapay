"""W3 BillingInvoiceWorker stub (Milestone 1 placeholder).

FULL IMPLEMENTATION IN M2.1 per EDD §15 M2.1:
  - T+1 midnight cron: generate invoices for all active subscriptions previous month
  - Compute PlanLedger overage (payments beyond base_payments_included)
  - Create KHQR payment via our own gateway (dog food our own HQ store)
  - Issue event plan.invoice.issued → fan-out webhook

M1 stub exists so main.py lifespan can start all 4 workers consistently from
day 1, and transport.run_workers() dict has all 4 queue_name:Worker entries —
no NPEs when we enable W3 billing later (just swap process() body).
"""

from __future__ import annotations

import logging

from .base import Worker
from .job import Job

log = logging.getLogger(__name__)

QUEUE = "billing.invoices"


class BillingInvoiceWorker(Worker):
    prefetch = 1
    concurrency = 1

    queue_name = QUEUE

    async def process(self, job: Job) -> dict[str, object]:
        """M1: No-op. Just log + return ok."""
        log.info(
            "W3 stub (full in M2.1): received invoice job payload=%s",
            list(job.payload.keys()),
        )
        return {"m1_stub": True, "ok": True, "processed_at": job.created_at.isoformat()}
