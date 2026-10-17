# ChmabaPay Architecture & Engineering Design Document (EDD)

> **Version:** 1.0  
> **Date:** 2026-09-10  
> **Companion Doc:** [BUSINESS_REQUIREMENTS.md](file:///e:/Development/chmabapay/BUSINESS_REQUIREMENTS.md) — "What we build"  
> **This Document:** "How we build it — modular monolith first, seamless microservices scalability later"

---

## Table of Contents

1. [Executive Design Principles](#1-executive-design-principles)
2. [Deployable Topology — 3 Apps, 4 Worker Domains](#2-deployable-topology--3-apps-4-worker-domains)
3. [Phase Evolution — Monolith → Queue Workers → Per-Domain Microservices](#3-phase-evolution--monolith--queue-workers--per-domain-microservices)
4. [Worker Domains — Detailed Specification (W1-W4)](#4-worker-domains--detailed-specification-w1-w4)
5. [Queue Transport Abstraction — Pluggable Interface](#5-queue-transport-abstraction--pluggable-interface)
6. [Enqueue-on-Write Pattern — Replace DB Polling for Low Latency + Efficiency](#6-enqueue-on-write-pattern--replace-db-polling-for-low-latency--efficiency)
7. [Reverse Engineering Libraries — NOT Microservices; Scale via Import + Caching](#7-reverse-engineering-libraries--not-microservices-scale-via-import--caching)
8. [DB Schema Additions vs. Existing (M1-M3)](#8-db-schema-additions-vs-existing-m1-m3)
9. [Auth Matrix — Two Modes, One FastAPI](#9-auth-matrix--two-modes-one-fastapi)
10. [API Router Map (Final Topology)](#10-api-router-map-final-topology)
11. [Frontend Component Sharing (2 Apps, 1 Framework)](#11-frontend-component-sharing-2-apps-1-framework)
12. [Scalability Levers per Phase (Perf Tuning Knobs)](#12-scalability-levers-per-phase-perf-tuning-knobs)
13. [Migration Steps — Phase 1 → 2 → 3 (Code Diffs, Not Rewrites)](#13-migration-steps--phase-1--2--3-code-diffs-not-rewrites)
14. [Monitoring & Observability Hooks](#14-monitoring--observability-hooks)
15. [Implementation Order Annotated with Scalability Hooks](#15-implementation-order-annotated-with-scalability-hooks)
16. [Risks & Mitigations](#16-risks--mitigations)

---

## 1. Executive Design Principles

Four non-negotiable rules every engineer on the project follows:

| # | Principle | Why It Matters |
|---|---|---|
| **P1** | **Modular Monolith First, Microservices Never (Unless Forced)** | Microservices add 2x-10x ops complexity for 0 user-visible benefit at 100K txns/day. We split ONLY when a single deployment domain can't be right-sized (e.g., detector needs 64 cores, expiry needs 0.25 core — same container can't optimize both). |
| **P2** | **All Background Work Runs Through `Worker.process()` Interface** | 4 worker domains (W1-W4). Zero ad-hoc `while True:` loops. This is the single seam that lets us split to microservices later WITHOUT CHANGING business logic. |
| **P3** | **All Cross-Worker Communication Runs Through `QueueTransport` Interface** | One queue abstraction. Two built-in implementations: `InProcessTransport` (Phase 1, 0 infra) + `RedisTransport` (Phase 2, 1 env var). Phase 3 RabbitMQ/AWS SQS — same interface. Zero rewrite. |
| **P4** | **Reverse-Engineering Logic = Python Libraries (NOT Services)** | SSR parser, KHQR builder, Bakong Open API client are pure `.py` modules. Scale them by importing in N worker replicas — no inter-service network hop, no latency, no tracing pain. Extract only if non-Python services need them (not on our roadmap). |

---

## 2. Deployable Topology — 3 Apps, 4 Worker Domains

### 2.1 Logical Diagram

```
                                       ┌────────────────────────────────────────────┐
                                       │         CHMABAPAY = 3 DEPLOYABLE APPS       │
                                       └────────────────────────────────────────────┘
              ┌────────────────────────────────┐              ┌──────────────────────────────┐
              │  DNS:  app.chmabapay.com       │              │  DNS: admin.chmabapay.com    │
              │  App 2: USER PORTAL FRONTEND   │              │  App 3: ADMIN PORTAL FRONTEND│
              │  (Next.js 13+ App Router)      │              │  (Same framework as App 2!)  │
              │  Pages: dashboard/stores/      │              │  Pages: overview/accounts/   │
              │  payments/keys/webhooks/       │              │  plans/invoices/             │
              │  billing/sub-m/settings        │              │  Shared 80% of UI components │
              └───────────────┬────────────────┘              └────────────┬─────────────────┘
                              │ HTTPS (CORS allow-listed)                 │ HTTPS
                              ▼                                            ▼
              ┌──────────────────────────────────────────────────────────────────────────────┐
              │  DNS:  api.chmabapay.com         App 1: BACKEND API MONOLITH (FastAPI)       │
              │  ┌────────────────────────────────────────────────────────────────────────┐  │
              │  │  FastAPI create_app()                                                  │  │
              │  │    1. HTTP Endpoints (/api/v1/* + /auth + /pay/{id} public)               │  │
              │  │    2. (Phase 1 ONLY) bg tasks: W1-W4 workers run IN-PROCESS           │  │
              │  │    3. (Phase 2/3 ONLY) NO BG workers in API proc → offloaded          │  │
              │  └────────────────────────────────────────────────────────────────────────┘  │
              │                                       │                                     │
              │                                       ▼                                     │
              │  ┌────────────────────────────────────────────────────────────────────────┐  │
              │  │  QUEUE TRANSPORT LAYER (plugabble, Section 5)                         │  │
              │  │    Phase 1: asyncio.Queue (in-process, no infra)                      │  │
              │  │    Phase 2: Redis Streams (1 env var, 1 CLI runner)                   │  │
              │  │    Phase 3: Per-domain queues routed to dedicated microsvc runners    │  │
              │  └────────────────────────────────────────────────────────────────────────┘  │
              │                                       │                                     │
              │                                       ▼                                     │
              │  ┌────────────────────────────────────────────────────────────────────────┐  │
              │  │  4 WORKER DOMAINS  (Section 4 detailed spec)                           │  │
              │  │    W1 PaymentDetectionWorker (Bakong + SSR status)                     │  │
              │  │    W2 WebhookSenderWorker (outbox + HMAC + POST + backoff)            │  │
              │  │    W3 BillingInvoiceWorker (T+1 midnight batch)                        │  │
              │  │    W4 ExpirySweeperWorker (pending past TTL)                           │  │
              │  └────────────────────────────────────────────────────────────────────────┘  │
              │                                       │                                     │
              │                                       ▼                                     │
              │  ┌────────────────────────────────────────────────────────────────────────┐  │
              │  │  INTERNAL LIBRARIES (imported, NOT deployable, Section 7)             │  │
              │  │    khqr.py                       services/payway_parser.py             │  │
              │  │    services/bakong.py             services/status_reconciler.py        │  │
              │  │    services/payments.py          services/stores.py                    │  │
              │  │    services/billing.py (NEW)                                           │
              │  │    services/emails.py (NEW)      auth.py / security.py                 │
              │  │    models.py / db.py / config.py  webhooks.py (old loop → new worker)  │
              │  └────────────────────────────────────────────────────────────────────────┘  │
              └──────────────────────────────────────────────────────────────────────────────┘
                                           │
                                           ▼
                ┌──────────────────────────────────────────────────────────────────────┐
                │  EXTERNAL INFRASTRUCTURE (shared by all phases, pluggable)            │
                │    • Primary DB: Supabase Postgres (or local SQLite dev, asyncpg)    │
                │    • Cache + Queue: Redis (Phase 2+. Dev: in-memory LRU, no Redis)   │
                │    │      → Hot key: SSR status cache, Bakong token, rate limits      │
                │    │      → Queue: Phase 2+ Redis Streams                             │
                │    • Object storage: S3 / Supabase Storage                            │
                │    │      → receipt PDFs                                                    │
                │    • Outbound calls: Bakong Open API v1, link.payway.com.kh SSR       │
                │    • Email provider: Resend / Mailgun / SMTP (services/emails.py)     │
                └──────────────────────────────────────────────────────────────────────┘
```

### 2.2 Physical Recommendations Per Phase

| Phase | Deployment | # Containers | Notes |
|---|---|---|---|
| **1: Dev/Pilot** | Single uvicorn + Next.js dev server | 1 (API+workers) + 2 (FE) | No Redis, no S3, local SQLite + file uploads |
| **2: Growth (1K-100K/day)** | API proc (HTTP only, NO workers) + `N` worker containers + Redis | 1 (API) + 4 (workers) + 2 (FE) + 1 Redis | HPA on queue depth for detector/webhook |
| **3: Scale (100K+/day)** | Per-worker microservices | 1 API + 1 detector-svc + 1 webhook-svc + 1 billing-cronjob + 1 expiry-svc + 2 FE + Redis + Postgres | Right-size each svc (detector=CPU-heavy, expiry=idle, billing=cron) |

---

## 3. Phase Evolution — Monolith → Queue Workers → Per-Domain Microservices

### 3.1 Phase 1: Modular Monolith In-Process (Pilot / <1K txns/day)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  uvicorn  (Single Python process)                                            │
│    FastAPI lifespan:                                                          │
│    ├── HTTP Server (FastAPI app)                                              │
│    └── asyncio.create_task for W1+W2+W3+W4 Workers                            │
│        (all share one inprocess QueueTransport — no IPC, no network)         │
│                                                                               │
│  DB: SQLite dev / Postgres pilot                                              │
│  Redis: NOT USED                                                              │
│  Uploads: ./local_uploads/ folder (dev)                                       │
└──────────────────────────────────────────────────────────────────────────────┘
```

✅ Pros: Zero infra overhead, perfect debugger experience, no queue-at-least-once bugs to debug in pilot.
⚠️ Cons: HTTP + workers compete for event loop; 1 process can't scale workers beyond N CPU. Fine for 1K txns/day.

### 3.2 Phase 2: Split API vs Workers (Redis Queue, 1 env var switch)

```
┌────────────────────────────────────┐    ┌────────────────────────────────────────┐
│   uvicorn API                      │    │   N × Worker Processes (same package)  │
│   (HTTP only, lifespan BG: OFF)    │    │   CLI:  uv run python -m chmabapay.    │
│                                    │    │         workers run W1 W2 W3 W4        │
│   - All /api/v1/* + /auth + /pay/*     │    │                                        │
│   - On write: enqueue() → Redis    │    │   - Each worker dequeues via           │
│                                    │    │     RedisTransport + process()         │
│   - 0 BG workers in this proc      │    │   - Can run 4 workers × N replicas     │
└───────────────┬────────────────────┘    └──────────────┬─────────────────────────┘
                │                                        │
                ▼                                        ▼
           ┌─────────────────────────────────────────────────────────┐
           │                    REDIS CLUSTER                         │
           │   Streams: jobs.payment.detect / jobs.webhook.send / …  │
           │   Caches: Bakong token TTL, SSR status LRU, rate limit  │
           └─────────────────────────────────────────────────────────┘
```

✅ Pros: HTTP latency not impacted by slow webhook endpoints; horizontal worker replicas; per-replica concurrency tuning.
⚠️ Cons: Needs Redis. 1 deployable → 3 (API + workers + Redis). Still no microservices.

### 3.3 Phase 3: Per-Domain Microservices (Strangler Fig, NO business logic changes)

**When to trigger**: Per-worker metrics show imbalance (e.g., W1 detector uses 90% of CPU, W4 expiry is idle). Fix: run each worker type in its own container with resource rightsizing.

```
┌─────────────────────────────┐     ┌──────────────────────────────────────────────┐
│  uvicorn API (HTTP only)    │     │  Microservice Containers (same package!)     │
└─────────────┬───────────────┘     │                                              │
              │                     │  Container detector-svc:  8 replicas         │
              │                     │   → runs W1 PaymentDetectionWorker ONLY      │
              │                     │   → HPA on jobs.payment.detect stream len    │
              │                     │                                              │
              │                     │  Container webhook-svc:   4 replicas         │
              │                     │   → runs W2 WebhookSenderWorker ONLY         │
              │                     │   → per-endpoint queue sharding + circuits   │
              │                     │                                              │
              │                     │  K8s CronJob billing-svc (T+1 00:10): 1 run  │
              │                     │   → runs W3 BillingInvoiceWorker ONCE/month  │
              ▼                     │                                              │
         ┌───────────────────┐      │  Container expiry-svc:    1 replica         │
         │  REDIS STREAMS    │◄─────┤   → runs W4 ExpirySweeperWorker ONLY         │
         └───────────────────┘      └──────────────────────────────────────────────┘
```

✅ Pros: Per-domain right-sizing + HPA; detector can scale to 100 replicas without paying for 100 idle webhook replicas.
⚠️ Cons: 1 deployment + CI-CD per service. Still NO business logic changes. `Worker.process()` code identical in every container. Only `cli` args differ.

---

## 4. Worker Domains — Detailed Specification (W1-W4)

### W1. Payment Detection Worker (MOST CRITICAL, SCALES FIRST)

| Field | Value |
|---|---|
| **Purpose** | Monitor pending/scanned payments → detect Bakong settlement → mark PAID. SSR poll for ABA-linked payments first (POINT #1) → Bakong cascade fallback (POINT #2). |
| **Queue name** | `jobs.payment.detect` |
| **Concurrency (per replica)** | 50 (I/O bound, httpx pool of 100) |
| **Max attempts per job** | 4,320 (approx 12h at 10s exponential cap; adjust via config) |
| **Backoff** | Exponential: `min(2 ** attempts, 10)` seconds |
| **Input job payload** | `{payment_public_id: str, mode: "full" or "retry", source_signal: "aba_or_bakong_or_customer"}` |
| **Flow inside `process(job)`** | <ol><li>Load payment by public_id + store + payment_link</li><li>`_is_aba_payway_link(payment)` → True → run `payway_parser.fetch_payment_status()` on slug → if PAID + session → mark_paid → DONE</li><li>Run `bakong_client.verify_receipt()` with md5+short_hash+instruction_ref+external_ref cascade</li><li>Success + amount match → mark_paid → DONE</li><li>Else: job.retry_later(backoff)</li><li>After 4000 attempts → Payment.status = FAILED (manual review)</li></ol> |
| **Phase 1 legacy adapter** | Today's `bakong_verify_loop()` + `reconcile_payment()` become W1.process(). Wrap in worker scaffold, DONE. |
| **Hot caching (Section 7)** | `@lru_cache(maxsize=1000)` on `fetch_payment_status(slug, ttl=30s)` so 100 detectors on same slug = 1 SSR fetch. |
| **HPA trigger (Phase 3)** | `redis_stream_len(jobs.payment.detect) > 2 * concurrency_per_replica` → +1 replica |

### W2. Webhook Sender Worker (SECOND MOST CRITICAL, PER-ENDPOINT CIRCUIT)

| Field | Value |
|---|---|
| **Purpose** | Fan-out signed webhook POSTs. Per-endpoint exponential backoff. No merchant endpoint slowdown can hurt others. |
| **Queue name** | `jobs.webhook.send` |
| **Concurrency (per replica)** | 100. *Per-endpoint token bucket*: endpoint with 50 timeouts gets its own 20% cap. |
| **Max attempts** | 8. Total max schedule: 1 + 2 + 4 + 8 + 16 + 32 + 64 + 128 → ~5 min cumulative. |
| **Backoff** | `min(2 ** attempts, 3600)` seconds. Jitter ±20%. |
| **Input job payload** | `{event_id: str, endpoint_id: str}`. **NOT payload.** Load event+endpoint to avoid queue duplication. |
| **Flow inside `process(job)`** | <ol><li>Load endpoint + event + payload snapshot (immutable, outbox)</li><li>`sign_payload(raw_bytes, endpoint.secret_key)` → header `X-ChmabaPay-Signature: t=<ts>,v1=<hex>`</li><li>httpx POST endpoint.url, timeout=5s, headers+json</li><li>HTTP 200-299 → delivery.status = SUCCESS → ACK</li><li>Timeout/5xx → attempts++. retry_later(backoff). last_error=traceback. last_response_status = code</li><li>Attempts == max → FAILED delivery. Dashboard shows with retry button.</li></ol> |
| **Phase 1 legacy adapter** | Replace `webhooks.webhook_loop()` + `process_due_deliveries()` with W2.process() + enqueue-on-write pattern. |
| **Circuit breaker (Phase 2+)** | Endpoint with >10% 5xx/timeout in 5 min window → half-open: drop new jobs to 1/min + alert merchant via email. |
| **Fan-out rule** | Section 4 of BRD. Event written → for each endpoint matching `(event.store_id==endpoint.store_id) OR (endpoint.account_id==store.account_id AND endpoint.store_id is NULL)` → enqueue 1 job. Unique `uq_event_endpoint` prevents duplicates. |

### W3. Billing Invoice Worker (BATCH, NOT REAL-TIME)

| Field | Value |
|---|---|
| **Purpose** | T+1 00:10 → generate PlanInvoices for previous month across all accounts. Dog-food our gateway by creating a KHQR payment to ChmabaPay HQ store. |
| **Queue name** | `jobs.billing.run_month_end` (single producer: admin daily T+1 scheduler) |
| **Concurrency** | 20 per replica. Per-account parallel. Single global job → emits N per-account child jobs (fan-out). |
| **Max attempts** | 1. Human fixes on failure. |
| **Input job** | `{period_month: "2026-09", account_id: int\|None, only_overdue: bool}`. None = all accounts. |
| **Flow** | <ol><li>For each active subscription: compute usage via `PlanLedgerEntry WHERE period_month=?` aggregate</li><li>Invoice.base = plan.monthly_fee_cents; overage = 0 (plans have no per-payment overage price)</li><li>Invoice.status = ISSUED; POST /api/v1/payments → KHQR via our own store (st_chmabapay_hq)</li><li>Email: "Your ChmabaPay Sep invoice is ready → [Pay Now KHQR]"</li></ol> |
| **Run pattern (Phase 2+)** | Kubernetes CronJob or scheduled admin CLI. Not a long-running process. |

### W4. Payment Expiry Sweeper (LEAST CRITICAL, BATCH SQL)

| Field | Value |
|---|---|
| **Purpose** | Find pending/scanned payments past `expires_at` → status = EXPIRED → enqueue payment.expired webhook events. |
| **Queue name** | `jobs.payment.expire_sweep` |
| **Concurrency** | 5. SQL is the bottleneck, not Python. |
| **Max attempts** | 1. |
| **Schedule** | Every 60 seconds via cron heartbeat that enqueues 1 job. |
| **Flow** | <ol><li>`UPDATE payments SET status='expired' WHERE status IN ('pending','scanned') AND expires_at < NOW() RETURNING id, store_id, public_id`</li><li>For each RETURNING row: Event(type=payment.expired) OUTBOX inserted</li><li>Fan-out enqueue W2 jobs per endpoint (same rule)</li></ol> |
| **Legacy adapter** | Replace `webhooks.expiry_loop()` with W4.process(). Today's hardcoded 5s interval → new configurable 60s. |
| **Scaling note** | Always single replica. 1 SQL UPDATE batch handles 10K rows in <100ms. Never need more than 1. |

---

## 5. Queue Transport Abstraction — Pluggable Interface

### 5.1 Interface Code (Single Source of Truth — Phase 1)

```python
# src/chmabapay/workers/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from asyncio import Event
from dataclasses import dataclass, field
from typing import Generic, TypeVar
import asyncio
import time

PayloadT = TypeVar("PayloadT")

@dataclass
class Job(Generic[PayloadT]):
    job_id: str
    name: str
    payload: PayloadT
    attempts: int = 0
    max_attempts: int = 8
    created_at: float = field(default_factory=time.time)
    next_attempt_at: float = field(default_factory=time.time)  # for delay queues

class QueueTransport(ABC, Generic[PayloadT]):
    """
    Single queue abstraction. Two implementations today. Many tomorrow.

    Implementations:
      - InProcessTransport: asyncio.PriorityQueue. No infra. Dev/Phase 1.
      - RedisTransport: Redis Streams + Consumer Groups. Phase 2+ prod.
      - RabbitMQTransport, SQSTransport, KafkaTransport: future, same interface.
    """
    @abstractmethod
    async def enqueue(
        self,
        queue_name: str,
        payload: PayloadT,
        *,
        delay_seconds: float = 0.0,
        max_attempts: int = 8,
        dedup_key: str | None = None,
    ) -> str:
        """Enqueue a job. Returns unique job_id.
        dedup_key: if set + job with same dedup_key pending/processing → NOOP; return existing id.
        """

    @abstractmethod
    async def dequeue(self, queue_name: str, worker_id: str) -> Job[PayloadT] | None:
        """Blocking: pop next job whose next_attempt_at <= now(). Claim ownership for worker_id."""

    @abstractmethod
    async def ack(self, queue_name: str, job_id: str) -> None:
        """Mark success. Delete from pending + move to history TTL 30d."""

    @abstractmethod
    async def retry_later(self, queue_name: str, job_id: str, backoff_seconds: float) -> None:
        """Increment attempts + set next_attempt_at = now + backoff. Return to pending."""

    @abstractmethod
    async def dead_letter(self, queue_name: str, job_id: str, reason: str) -> None:
        """Max attempts exceeded. Move to dead-letter queue + trigger alert."""

    async def metrics(self, queue_name: str) -> dict:
        """Optional: queue_len, in_flight, dead, oldest_age. Used by HPA in Phase 2+."""
        return {}

# --------------------------------------------------------------------------- #
# Worker base (consumes the transport)
# --------------------------------------------------------------------------- #
class Worker(ABC, Generic[PayloadT]):
    name: str
    concurrency: int = 20

    def __init__(self, transport: QueueTransport[PayloadT]) -> None:
        self.transport = transport

    @abstractmethod
    async def process(self, job: Job[PayloadT]) -> None:
        """Implement per W1-W4. Raise Exception = auto retry_later with backoff."""

    async def run(self, stop: Event, worker_id: str = "local-0") -> None:
        """Generic run loop. Same for every worker, never rewritten."""
        sem = asyncio.Semaphore(self.concurrency)
        in_flight: set[asyncio.Task] = set()
        while not stop.is_set():
            job = await self.transport.dequeue(self.name, worker_id)
            if job is None:
                await asyncio.sleep(0.1)
                continue

            async def _handle(j: Job[PayloadT]) -> None:
                async with sem:
                    try:
                        await self.process(j)
                        await self.transport.ack(self.name, j.job_id)
                    except Exception as exc:  # noqa: BLE001
                        j.attempts += 1
                        if j.attempts < j.max_attempts:
                            backoff = min(2 ** j.attempts, 3600)
                            await self.transport.retry_later(self.name, j.job_id, backoff)
                        else:
                            await self.transport.dead_letter(self.name, j.job_id, str(exc))

            task = asyncio.create_task(_handle(job))
            in_flight.add(task)
            task.add_done_callback(in_flight.discard)

        # Drain in-flight on shutdown (graceful 30s)
        if in_flight:
            await asyncio.wait_for(asyncio.gather(*in_flight, return_exceptions=True), timeout=30)
```

### 5.2 InProcessTransport (Phase 1, Zero Infra)

```python
# src/chmabapay/workers/inprocess.py
"""
asyncio-based QueueTransport. No Redis, no RabbitMQ.
Perfect for pilot + local dev. Single process only.
"""
from __future__ import annotations
import asyncio
import heapq
import time
import uuid
from collections import defaultdict
from .base import Job, QueueTransport, PayloadT

class InProcessTransport(QueueTransport[PayloadT]):
    def __init__(self) -> None:
        self._queues: dict[str, list[tuple[float, str, Job[PayloadT]]]] = defaultdict(list)
        self._dedup: dict[tuple[str, str], str] = {}  # (queue,dedup_key) → job_id
        self._history: dict[str, dict] = {}

    async def enqueue(
        self, queue_name, payload, *, delay_seconds=0, max_attempts=8, dedup_key=None
    ) -> str:
        if dedup_key and (existing := self._dedup.get((queue_name, dedup_key))):
            return existing
        job_id = uuid.uuid4().hex
        job: Job[PayloadT] = Job(
            job_id=job_id, name=queue_name, payload=payload,
            max_attempts=max_attempts,
            next_attempt_at=time.time() + delay_seconds,
        )
        heapq.heappush(self._queues[queue_name], (job.next_attempt_at, job_id, job))
        if dedup_key:
            self._dedup[(queue_name, dedup_key)] = job_id
        return job_id

    async def dequeue(self, queue_name, worker_id: str) -> Job[PayloadT] | None:
        q = self._queues[queue_name]
        if not q:
            return None
        next_attempt_at, _, _ = q[0]
        if next_attempt_at > time.time():
            return None
        _, _, job = heapq.heappop(q)
        return job

    async def ack(self, queue_name, job_id) -> None:
        self._history[job_id] = {"status": "acked", "finished_at": time.time()}

    async def retry_later(self, queue_name, job_id, backoff_seconds: float) -> None:
        # Find job by id in heap (O(n) OK for pilot; swap to hash-indexed heap in Phase 2)
        q = self._queues[queue_name]
        for i, (_, _, j) in enumerate(q):
            if j.job_id == job_id:
                j.next_attempt_at = time.time() + backoff_seconds
                q[i] = (j.next_attempt_at, j.job_id, j)
                heapq.heapify(q)
                return
        # Not in pending → was popped → re-enqueue
        # (handled in run loop already, this is fallback)

    async def dead_letter(self, queue_name, job_id, reason) -> None:
        self._history[job_id] = {"status": "dead", "reason": reason, "at": time.time()}
```

### 5.3 RedisTransport (Phase 2 — Skeleton Now, Implement Then)

```python
# src/chmabapay/workers/redis.py  (SKELETON ONLY in M1, IMPLEMENT in M2)
"""
Redis Streams based QueueTransport with consumer groups for at-least-once + HPA.
- Stream per queue name: `chmabapay:stream:{queue_name}`
- Consumer group: `chmabapay:group:{queue_name}`
- Dedup: SET `chmabapay:dedup:{queue}:{key}` EX 86400
- Dead-letter: STREAM `chmabapay:dlq:{queue_name}`
- History: STREAM capped 100MB per queue
"""
# Implementation: Phase 2. Same QueueTransport interface. ~120 lines of redis-py asyncio code.
```

---

## 6. Enqueue-on-Write Pattern — Replace DB Polling for Low Latency + Efficiency

**OLD Pattern (today's loops, wasteful):**
```
Every 2s → SELECT 50 pending payments → run detection on each
  → Avg latency: 0-2s. 500 pending payments → 250 pointless SELECTs/hour.
Every 1s → SELECT due webhook deliveries → POST → update
  → Same.
```

**NEW Pattern (Section 5 + workers):**
```
At the MOMENT data is written → enqueue job INSTANTLY.
No repeated SELECTS. Zero idle polling.
First attempt at detection happens in < 100 ms (not avg 1s).
```

### 6.1 W1 Payment Detection — Enqueue Triggers

| Where to enqueue | What to pass | Dedup key |
|---|---|---|
| **After `create_payment()` commit** (services/payments) | `enqueue(jobs.payment.detect, {payment_public_id})` | `(payment_public_id, detect)` → ensures only 1 pending detection job per payment at any time |
| **After dev `/payments/{id}/pay`** (same pattern on Phase 1 rail) | Same. | Same. |
| **Customer `/pay/{id}` page loads JS poll for status** → optional enqueue priority | `delay=0` if not pending. | Same dedup. |
| Optional: Bakong webhook listener (future NBC feature) → enqueue | Instant detection. Customer pays → detection under 1s. | Same. |

### 6.2 W2 Webhook — Enqueue Triggers

| After Event OUTBOX insert | For each matching endpoint (scope rule) → enqueue | Dedup: `(event_id, endpoint_id)` matches DB unique |
|---|---|---|

### 6.3 W4 Expiry — Enqueue Trigger

Daily heartbeat (cron): admin scheduler calls `enqueue(jobs.payment.expire_sweep, {})` every 60s.

### 6.4 W3 Billing — Enqueue Trigger

Platform scheduler: `enqueue(jobs.billing.run_month_end, {period_month: "2026-09"})` at T+1 00:10 UTC+7.

---

## 7. Reverse Engineering Libraries — NOT Microservices; Scale via Import + Caching

### 7.1 Library Modules Summary

| Module | Used In | Scale Technique |
|---|---|---|
| **khqr.py** (EMVCo build/parse, derive_bakong_id, BankProfile registry) | HTTP: payment create, khqr/*, sub-merchant create<br>Worker W1: detection (parse to md5) | Pure CPU, no I/O. Import in N workers → scales linearly. BankProfile dict is process-level cache, 0 cost. |
| **services/payway_parser.py** (SSR IIFE parser, fetch_payment_status) | HTTP: khqr/from-link, probe-aba-status<br>Worker W1: POINT #1 priority status poll | I/O bound (httpx fetch link.payway.com.kh). **Hot cache:** `@lru_cache(maxsize=2000)` on `fetch_payment_status(slug, expected_amount)` with TTL=30s via `cachetools.TTLCache`. 100 workers on same slug → 1 HTTP call. **Rate limit:** per-worker `limiter=2 req/s per origin` using `aiometer` or sliding window to avoid upstream ABA cloudflare ban. |
| **services/bakong.py** (Open API v1 client, 5 searches, auto-renew token) | HTTP: transactions/* endpoints<br>Worker W1: cascade verify_receipt | **Shared Bakong token cache (Phase 2 Redis):** write once on renew, TTL 58 min, read by all replicas → single mint per hour cluster-wide. **Per-worker connection pool:** `httpx.AsyncClient(limits=Limits(max_connections=100, max_keepalive_connections=20))`. **Per-developer-email rate limit:** leaky bucket via Redis across replicas (Bakong will 429 otherwise). |
| **services/status_reconciler.py** (ABA#1+Bakong#2 orchestrator) | HTTP: check-status, verify-payment<br>Worker W1: main process body | Pure orchestrator, no state. Imports 2 libraries. Import everywhere → no scaling concern. |

### 7.2 Rule: When To Actually Extract As Microservice? (Answer: NEVER Unless...)

Extract a reverse-engineering module to its own service ONLY if:
1. **Non-Python services need it.** e.g. Node.js admin needs SSR parser. Not our case.
2. **Specialized hardware.** Not in scope.
3. **Library needs >16GB RAM.** KHQR build is nanobytes. Bakong client is KB-scale. Not applicable.

If none apply → KEEP AS LIBRARY. 0 deployment cost, 0 network hop, 0 tracing cost.

### 7.3 Caching Hierarchy

```
Level 1 (Process L1, every process has its own):
  • @lru_cache BankProfile lookup (infinite TTL, process boot only)
  • cachetools.TTLCache payway_parser.fetch_payment_status — 30s TTL, 2000 keys
  • Bakong token in-memory copy + check time < 58 min → skip re-read

Level 2 (Redis L2, Phase 2+, shared cluster-wide):
  • SET chmabapay:bakong_token — value=JWT, EX 3480 (58 min)
  • SET chmabapay:ssr_status:{slug_md5} — JSON status, EX 30s
  • ZSET chmabapay:rate_limit:{developer_email}:{minute_bucket} — leaky counter
  • SESSIONS cookie cache (optional, DB fallback always)

Level 3 (DB L3, always final source of truth):
  • Payment.qr_md5, PaymentLink, Account, all tables
```

Cache invalidation: W1 write `mark_paid()` → delete Level 1 + 2 SSR cache keys for slug = explicit stale on settlement success.

---

## 8. DB Schema Additions vs. Existing (M1-M3)

Companion to BRD §8 — **annotated with Phase in which introduced:**

| New Table / Column | M1 | M2 | M3 | Notes |
|---|---|---|---|---|
| Account: account_type enum (individual / business) | ✅ | | | DEFAULT individual |
| Account: feature gates (saas_sub_merchants_enabled, whitelabel_enabled) | ✅ | | | Plan sets; admin override |
| Account: is_platform_admin | ✅ | | | 1-2 initial rows seeded |
| **NEW SubMerchant table** (shadow store pattern) | | | ✅ | M3; M1/M2 Business only uses account keys, no SaaS |
| **NEW Plan table** (Starter/Growth/Scale/Enterprise 4 seed) | ✅ | | | |
| **NEW PlanSubscription table** | ✅ | | | |
| **NEW PlanInvoice** | | ✅ | | Billing worker W3 = M2 |
| **NEW PlanLedgerEntry** | ✅ | | | Counter in mark_paid(); M1 hook; M2 aggregator runs |
| **NEW AuditLog** (admin actions + impersonation) | ✅ | | | Every admin POST writes 1 row |
| Event.type adds: `sub_merchant.created/updated`, `plan.invoice.issued/paid` | | ✅ | ✅ | |
| **NEW** ApiKey.mode=test bypass → fake delay mark_paid | ✅ | | | |

---

## 9. Auth Matrix — Two Modes, One FastAPI

No separate "integration API" vs "portal API" deployables. One FastAPI, two auth modes via different `Depends()`.

| Mode | Header / Transport | Used For | Router Prefix | Permissions Model |
|---|---|---|---|---|
| **Bearer API Key** (Integration) | `Authorization: Bearer <ck_… or st_…>` | Merchant HTTP integration. cURL, PHP/JS SDKs. | `/api/v1/payments/*`, `/api/v1/khqr/*`, `/api/v1/platform/*`, `/api/v1/stores/*` (mutations that also work via API key), `/api/v1/transactions/*` | `resolve_key_context()` → scope rule. Sub-merchant gate. Plan max limits. |
| **Session Cookie JWT** (Portal) | httpOnly cookie `chmabapay_session` + optional CSRF header | User/admin browser UI (Next.js frontends). Session JWT signed with rotating secret. | `/auth/*`, `/api/v1/me/*`, `/api/v1/keys/*` (session CRUD), `/api/v1/webhooks/*` (session CRUD), `/api/v1/billing/*`, `/api/v1/reports/*`, all `/api/v1/admin/*` | `require_dashboard_session()` → Account + plan. `require_platform_admin()` for admin routes. |
| **Public** | No auth | Hosted checkout page, health | `/pay/*`, `/health`, `/.well-known/*` | Read-only; no mutations. |

No conflicts. Two auth Depends are orthogonal. One FastAPI serves all three modes.

---

## 10. API Router Map (Final Topology)

Mounted in [main.py create_app()](file:///e:/Development/chmabapay/src/chmabapay/main.py#L17-L62):

| Router File | URL Prefix | Auth Mode | Milestone Added |
|---|---|---|---|
| routers/**auth.py** | `/auth` | Public (Google OAuth) | M1 |
| routers/**account.py** | `/api/v1/me` | Session | M1 |
| routers/**payments.py** | `/api/v1/payments` | Bearer key (primary) + Session (view only) | Existing + M1 extend |
| routers/**stores.py** | `/api/v1/stores` | Bearer + Session | Existing + M1 extend |
| routers/**khqr.py** | `/api/v1/khqr` | Bearer + Session | Existing + M1 tweaks |
| routers/**transactions.py** | `/api/v1/transactions` | Bearer + Session | Existing |
| routers/**keys.py** (NEW, split from services) | `/api/v1/keys` | Bearer (rotate/revoke) + Session (CRUD list) | M1 |
| routers/**webhooks.py** (NEW, split) | `/api/v1/webhooks` | Bearer + Session + `send-test` helper | M1 |
| routers/**billing.py** | `/api/v1/billing` | Session | M1 backend, M2 invoicing full |
| routers/**reports.py** | `/api/v1/reports` | Session | M2 (Starter-gated export) |
| routers/**platform.py** | `/api/v1/platform` | Bearer account-scope + plan SaaS gate | M3 (Scale feature) |
| routers/**admin.py** | `/api/v1/admin` | Session + `is_platform_admin` | M2 (plans) |
| routers/**checkout.py** | `/pay` | Public | Existing |
| routers/**dev.py** | `/_dev` | Public if `enable_dev_gateway` (Phase 1) | Existing (dev only) |

---

## 11. Frontend Component Sharing (2 Apps, 1 Framework)

```
web/
├── shared/                          ← UI LIBRARY (shared components)
│   ├── package.json                 ← pnpm/npm workspace package
│   ├── src/
│   │   ├── components/
│   │   │   ├── ChmabaLayout.tsx     ← sidebar + top nav + i18n EN/KH toggle
│   │   │   ├── DataTable.tsx        ← sortable, paginated, CSV export, filters drawer
│   │   │   ├── KHQRDisplay.tsx      ← SVG render + qr md5 tooltip + Tag dump expandable
│   │   │   ├── CopyField.tsx        ← copy-on-click with green check
│   │   │   ├── StatusBadge.tsx      ← paid/pending/scanned/expired/failed variants
│   │   │   ├── PlanPricingTable.tsx ← BRD §3.1 matrix as React component
│   │   │   └── ModalSystem.tsx      ← confirm modals, form modal layout
│   │   ├── hooks/
│   │   │   ├── useApi.ts            ← axios/fetch wrapper, supports:
│   │   │   │                         │     - API key (passed from env in SDK mode)
│   │   │   │                         │     - Session cookie (browser)
│   │   │   │                         │     - 401 auto → /login
│   │   │   │                         │     - Request idempotency key auto-inject
│   │   │   └── useI18n.ts           ← EN/KH dict, toggle, persistent user pref
│   │   ├── i18n/
│   │   │   ├── en.json
│   │   │   └── km.json
│   │   └── styles/
│   │       └── theme.ts             ← Chmaba brand colors, typography, spacing tokens
│   │
├── user/                            ← App 2: USER PORTAL (Next.js 13 App Router)
│   └── app/
│       ├── dashboard/               ├── payments/  ├── keys/  ├── sub-merchants/ (if plan)
│       ├── stores/                  ├── webhooks/  ├── billing/
│       ├── settings/                ├── help/      ├── onboarding/
│       └── login/page.tsx
│
└── admin/                           ← App 3: ADMIN CONSOLE (Next.js, port 3002)
    └── app/
        ├── page.tsx                 ← platform overview
        ├── accounts/                ← all accounts list with search
        ├── accounts/[account_id]/   ← plan & usage, profile, stores, invoices
        ├── plans/                   ← all plans incl. retired/hidden, inline edit
        └── invoices/                ← all plan invoices
```

Framework recommendation: **Next.js 13 App Router + React + Tailwind + shadcn/ui**. Why:
- 1 file = page (aligns with BRD §7 page-by-page spec)
- shadcn/ui = DataTable, Modal, StatusBadge, CopyField, DropdownMenu copy-paste implementations; no heavy MUI bundle
- Tailwind theme tokens from `web/shared/styles/theme.ts` = one source
- Server components for list pages = lower client bundle JS

---

## 12. Scalability Levers per Phase (Perf Tuning Knobs)

| Lever | Default (Phase 1) | Phase 2 Tuning | Phase 3 |
|---|---|---|---|
| API uvicorn workers | 1 process, 200 req timeout | 8 processes, `--workers 8 --limit-max-requests 10000` | Same or k8s HPA on CPU 70% |
| W1 detector concurrency | 20 (in-process) | 50/replica, Redis Streams 4 replicas → 200 parallel | 100/replica × 16 replicas → 1600 parallel (Bakong will rate-limit before this) |
| W2 webhook concurrency | 30 (in-process) | 100/replica, 4 replicas → 400 parallel | Per-endpoint sharding + circuit breaker |
| W1 retry backoff cap | 10 s | 15 s | 30 s (higher for 1M/day flows) |
| SSR cache size/TTL | LRU 2000 keys / 30s process-local | Redis shared 200k keys / 30s cluster-wide | Same |
| Bakong token mint | In-process memory | Redis shared TTL 58 min | Same |
| Postgres connection pool | 10 conns per process | PgBouncer in front, pool 200 per API+worker node | Same + read replica for /payments list page |

---

## 13. Migration Steps — Phase 1 → 2 → 3 (Code Diffs, Not Rewrites)

This is the payoff of the interface-first approach. Migration = config change + tiny code, NOT rewrite.

### Phase 1 → 2: Install Redis + Split Workers from API

```
DIFF SIZE: ~200 lines NEW, 0 lines CHANGED in business logic
─────────────────────────────────────────────────────────────

1. pyproject add redis + hiredis-py
   → `uv add "redis[hiredis]"`

2. Add RedisTransport impl (workers/redis.py) — ~120 lines
   → Uses redis.asyncio Streams + consumer groups

3. Add 2 env vars + 2 config fields:
   CHMABAPAY_QUEUE_TRANSPORT = inprocess | redis
   CHMABAPAY_REDIS_URL = redis://localhost:6379/0
   workers_enabled = True (in fastapi) + run_workers_in_api = True/False

4. New CLI command: src/chmabapay/cli.py → `workers run`
   @click.command()
   @click.argument("worker_names", nargs=-1)
   def workers_run(worker_names):
       transport = RedisTransport(url=REDIS_URL)
       workers = {
           "W1": PaymentDetectionWorker(transport),
           "W2": WebhookSenderWorker(transport),
           "W3": BillingInvoiceWorker(transport),
           "W4": ExpirySweeperWorker(transport),
       }[name] for name in worker_names
       → run each worker.run(stop_event) with graceful shutdown
   Example: `uv run python -m chmabapay.workers run W1 W2 W3 W4`
            → 4 workers in 1 process (or split across multiple processes as desired)

5. FastAPI lifespan conditional:
   if settings.run_workers_in_api:   # Phase 1 = True, Phase 2 = False
       start W1-W4 in-process
   else:
       NO workers in API proc. Only HTTP.

6. All business (W1.process / W2.process / khqr.py / bakong.py / ...):
   → 0 LINES CHANGED. Works.
```

### Phase 2 → 3: Per-Domain Microservices

```
DIFF SIZE: Dockerfile × 4 + k8s YAML × 4. 0 PYTHON CODE CHANGES.
─────────────────────────────────────────────────────────────

App 1 (API):        same container as Phase 2.
                    `run_workers_in_api = False` always.

Detector container: dockerfile → cmd = python -m chmabapay.workers run W1
                    → replicas=8, HPA on stream len jobs.payment.detect.

Webhook container:  dockerfile → cmd = python -m chmabapay.workers run W2
                    → replicas=4, HPA on jobs.webhook.send.

Billing cronjob:    k8s CronJob schedule T+1 00:10 → cmd = python -m chmabapay.workers run W3 once
                    → replicas=1 per run.

Expiry container:   dockerfile → cmd = python -m chmabapay.workers run W4 + heartbeat scheduler that
                    enqueues W4 job every 60s.
                    → replicas=1 always.

BUSINESS LOGIC:         0 lines changed.
WORKER.PROCESS():       0 lines changed.
QUEUE TRANSPORT:        RedisTransport (same).
```

Only deployment config changes. Zero regression risk.

---

## 14. Monitoring & Observability Hooks

Instrumented in Milestone 1 — not bolted on after:

| Hook | Implementation | Destination |
|---|---|---|
| **Trace ID every request** | Middleware `X-ChmabaPay-Trace` UUID → propagated to downstream Bakong/PayWay calls as header | Response header + structured logs |
| **Structured JSON logs** | loguru `serialize=True` | stdout → ELK / Grafana Loki |
| **Worker attempt metrics** | `transport.metrics()` endpoint + push every 10s | Prometheus via prometheus-fastapi-instrumentator |
| **W1 detection attempt timeline** | Every W1.process attempt → append Payment.attempt_history JSON list | Payment detail dashboard page |
| **W2 delivery status counts** | Endpoint-level dashboard: success/4xx/5xx/timeout (7d window) | Billing/support dashboard |
| **Admin AuditLog table** | Every admin mutation → write 1 row + `impersonation=true` flag if applicable | Admin logs page (M2) |
| **SLO latency histogram** | `mark_paid()` ← payment.created_at → histogram of "time to paid" | Grafana dashboard (95p should be < 60 s once Bakong indexes) |
| **Health checks** | `/health` → 200, `/health/details` → per-component: DB up / Bakong reachable / Redis up (Phase 2) | Uptime monitoring |

---

## 15. Implementation Order Annotated with Scalability Hooks

**Aligned to BRD §13 (Milestones)**, with Section references of THIS document embedded so engineers know which scalable hook to build WITH each feature.

### Milestone 1 (Weeks 1-2) — Individual Story + Dashboard Skeleton

| # | Task | Scalable Hook Built Alongside |
|---|---|---|
| M1.1 | DB: Account new columns (type, feature gates) | None (schema) |
| M1.2 | DB: Plan + PlanSubscription + PlanLedgerEntry + AuditLog 4 seed plans | Add PlanFeature.max_concurrency_per_worker fields (for tuning) |
| M1.3 | **`workers/` scaffold (THIS EDD Section 5)** | ✅ QueueTransport ABC + InProcessTransport (P1) + RedisTransport skeleton placeholder comment + Worker base — all 4 generic run loops |
| M1.4 | **W1 PaymentDetectionWorker** → Wrap today's bakong_verify_loop + reconciler in `process(job)` + enqueue-on-write after create_payment() (Section 6) | ✅ Dedup key = payment_public_id so only 1 pending detection/payment. ✅ SSR TTLCache 30s 2000 keys. ✅ Run loop uses Semaphore concurrency 20 |
| M1.5 | **W2 WebhookSenderWorker** → Wrap today's webhook_loop process_due + sign_payload in `process(job)` | ✅ Fan-out enqueue on Event insert (Section 6.2). ✅ uq_event_endpoint dedup OK |
| M1.6 | **W4 ExpirySweeperWorker** → Wrap today's expiry_loop in `process(job)` | ✅ 60s heartbeat enqueues |
| M1.7 | W3 BillingInvoiceWorker stub (skeleton only) | ✅ Placeholder; M2 implements |
| M1.8 | Auth router (Google OAuth + session + /auth/signout) + /api/v1/me profile endpoints | |
| M1.9 | Keys + Webhooks session CRUD endpoints (routers/keys.py + webhooks.py NEW) | |
| M1.10 | Billing backend: `/api/v1/billing/plans` matrix + change-plan (Individual → Growth auto-type switch) | |
| M1.11 | Test-mode ApiKey.mode=test → fake delay mark_paid after 5s + enqueue W1 | ✅ Enqueue pattern works |
| M1.12 | Dashboard pages (Next.js): dashboard overview + stores CRUD + payments list/detail + keys + webhooks + settings (profile/billing) | ✅ Shared component library `web/shared/` (Section 11) |

### Milestone 2 (Weeks 3-4) — Business + Admin + Billing Full

| # | Task | Scalability Hooks |
|---|---|---|
| M2.1 | Implement W3 BillingInvoiceWorker (full) + PlanInvoice table + T+1 scheduler emit job | ✅ Fan-out → per-account child jobs for parallelism |
| M2.2 | Router admin.py + admin dashboard pages: accounts/impersonate, plans CRUD, invoices, platform settings | ✅ Every action writes AuditLog |
| M2.3 | Reports router CSV export (plan gated; Starter hidden) | |
| M2.4 | Khmer i18n string files + EN/KH toggle shared layout component | |
| M2.5 | Emails service (fastapi-mail) — plan limit 80%/100%, invoice due 3d, invoice created | |
| M2.6 | Middleware: rate limit (12.6 gap) + X-ChmabaPay-Trace ID (Section 14) | Structured JSON logs every request |
| M2.7 | **RedisTransport IMPLEMENTATION (Phase 2 ready, not wired)** | ✅ Section 5.3. Now Phase 2 requires ONLY env var change + CLI run. |
| M2.8 | Health details endpoint + basic Prometheus metrics (Section 14) | |

### Milestone 3 (Weeks 5-6) — Business Scale → SaaS Sub-Merchants

| # | Task | Scalability Hooks |
|---|---|---|
| M3.1 | SubMerchant table + shadow-store auto-create service | ✅ Payment create resolver: sub_merchant_id → shadow store id (Section 4.2 BRD STEP 2) |
| M3.2 | mark_paid → atomic SubMerchant counter UPDATE | ✅ SQL +=1 (no race conditions) |
| M3.3 | Router platform.py (SaaS plan gate) Sub-Merchant CRUD + CSV import | ✅ 1000-row CSV batch = per row → enqueue W1 detection |
| M3.4 | Checkout page: SubMerchant whitelabel CSS override + redirect priority | ✅ Imported in layout (single hook) |
| M3.5 | Sub-Merchants dashboard page + import CSV + detail tabs | |
| M3.6 | Billing upgrade Growth→Scale → magically show Sub-Merchants tab | ✅ Plan gate only |
| M3.7 | Signature playground (SDK snippets 5 languages) | |
| M3.8 | Fraud duplicate payment detection (same bill_number + store + 24h window → warning banner) | |
| M3.9 | PDF receipts/invoices download buttons | |
| M3.10 | **Optional: Phase 2 infra deploy verification** (Redis + worker CLI) | ✅ End-to-end with 2 worker containers. Verify de-dup works across replicas. |

---

## 16. Risks & Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| **R1** | ABA PayWay SSR IIFE parser breaks after upstream UI revamp | High (quarterly-ish) | High (POINT #1 detection down for ABA) | 1. SSR parser has 4 HTML signals + window.__NUXT__ scan — if 1 changes, 3 others still fire.<br>2. Monitor: structured log every fetch_payment_status → alert on `UNKNOWN` rate > 10% for 10 min → human updates regex.<br>3. Fallback always works: Bakong cascade POINT #2 still detects. |
| **R2** | Bakong rate-limits our developer email at scale | Medium (over 1000 req/h) | Medium | 1. Per-developer-email Redis leaky bucket (Phase 2) → cap before hitting Bakong 429.<br>2. Rotate across multiple developer emails (pool of 3-5) → shard Bakong API calls. |
| **R3** | Queue at-least-once → duplicate webhook POST. Duplicate mark_paid. | Low (dedup everywhere) | Critical | 1. DB UniqueConstraint uq_event_endpoint (already there).<br>2. mark_paid: idempotency via payment.status already PAID = return existing. Payment.paid_at set once only.<br>3. Webhook docs: SDKs always check `event.id` for dedup on merchant side. |
| **R4** | Webhook merchant endpoint is 5s-30s slow → blocks worker pool. | High (at scale) | Medium | 1. Phase 2: per-endpoint concurrency cap 20% of global pool → 1 slow merchant can use at most 20% of capacity.<br>2. Circuit breaker: 10% 5xx/timeout in 5 min → drop to 1 attempt/min per endpoint + alert. |
| **R6** | Admin impersonation abused (platform insider risk). | Very low | Critical | 1. Every impersonation writes AuditLog with impersonator_account_id + target_account_id.<br>2. Impersonation session TTL 30 min max, auto-logs admin back to own.<br>3. Impersonation can't change admin-only records. |
| **R7** | Phase 1 → Phase 2 Redis migration downtime / regressions. | Low (this EDD designed to eliminate it) | Medium | 1. Can run in HYBRID mode for 1 week: API proc still runs workers AND enqueues to Redis. Parallel run of external workers consumes Redis jobs → verify same outcome. Then flip the flag.<br>2. Zero business logic changes. |

---

**END OF ARCHITECTURE + ENGINEERING DESIGN DOCUMENT**

Companion document to BUSINESS_REQUIREMENTS.md:
- BRD answers: "What do we ship for users?"
- THIS DOC answers: "How do we ship it scalable, with zero rewrites at each growth inflection?"

Implementation begins with Milestone M1.3 (workers scaffold) → M1.4/M1.5/M1.6 → wrap existing loops into Workers → enqueue-on-write. Then add schema/auth/dashboard pages. No engineer should write a new `while True:` loop again. Everything is a `Worker.process(Job)`.
