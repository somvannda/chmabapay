"""Workers package public exports."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import QueueTransport, Worker
from .inprocess import InProcessTransport
from .job import Job, JobStatus
from .redis import RedisTransport
from .w1_payment_detection import QUEUE as Q_DETECTION
from .w1_payment_detection import PaymentDetectionWorker
from .w2_webhook_sender import QUEUE as Q_WEBHOOK
from .w2_webhook_sender import WebhookSenderWorker
from .w3_billing import QUEUE as Q_BILLING
from .w3_billing import BillingInvoiceWorker
from .w4_expiry_sweeper import (
    QUEUE as Q_EXPIRY,
)
from .w4_expiry_sweeper import (
    ExpirySweeperWorker,
    expiry_heartbeat_dedup_key,
    expiry_heartbeat_job_payload,
)
from .w5_retention_sweeper import (
    QUEUE as Q_RETENTION,
)
from .w5_retention_sweeper import (
    RetentionSweeperWorker,
    retention_heartbeat_dedup_key,
    retention_heartbeat_job_payload,
)

if TYPE_CHECKING:
    pass

_GLOBAL_TRANSPORT: QueueTransport | None = None


def set_global_transport(tp: QueueTransport | None) -> None:
    """Attach the QueueTransport reference so pure-service-layer code can enqueue
    jobs without needing access to FastAPI request.app.state.

    Set in main.py lifespan after transport = InProcessTransport().
    """
    global _GLOBAL_TRANSPORT
    _GLOBAL_TRANSPORT = tp


def get_global_transport() -> QueueTransport | None:
    """Return the global QueueTransport (or None during tests)."""
    return _GLOBAL_TRANSPORT


__all__ = [
    # Abstractions
    "BillingInvoiceWorker",
    "ExpirySweeperWorker",
    "InProcessTransport",
    "Job",
    "JobStatus",
    "PaymentDetectionWorker",
    "QueueTransport",
    "RedisTransport",
    "RetentionSweeperWorker",
    "WebhookSenderWorker",
    "Worker",
    # Queue names
    "Q_BILLING",
    "Q_DETECTION",
    "Q_EXPIRY",
    "Q_RETENTION",
    "Q_WEBHOOK",
    # Helpers
    "expiry_heartbeat_dedup_key",
    "expiry_heartbeat_job_payload",
    "retention_heartbeat_dedup_key",
    "retention_heartbeat_job_payload",
    "set_global_transport",
    "get_global_transport",
]
