"""Support-ticketing rules, shared by the merchant API and the operator console.

The split between this module and the two routers is the one `services/billing.py`
makes against `routers/billing.py`, for the same reason: `priority_for_account`,
`first_response_at` and the queue ordering *are* the feature, and a copy of any of
them inside a router is a copy that can disagree with the console. The routers parse
HTTP and shape responses; every decision about a ticket's state is here.

Three invariants are worth stating before the code:

* **`priority` is copied from the plan at open time, not derived on read.** A queue
  that re-derived it would move a request the moment its account downgraded, taking
  away a position the platform had already promised — see `models.SupportRequest`.
* **`first_response_at` is written once, by the first operator reply.** It is the
  instant the Pro target is measured against, so a merchant's own reply must not
  start the clock and a later operator reply must not move it.
* **Notifications are courtesy, never the record.** The thread is what the platform
  owes the merchant; an email or a Telegram line that could not be sent is logged and
  that is the whole cost — the same posture `services/billing.py` takes for a dunning
  mail.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from . import notifications, resend
from .billing import as_utc, portal_url
from .payments import gen_public_id

log = logging.getLogger(__name__)

# The Pro first-response target, in hours. One number, here, so `/contact`, the portal
# and the console cannot state three different ones: `response_target_hours` is the only
# place the value is produced, and both routers read it rather than repeating it.
PRIORITY_RESPONSE_TARGET_HOURS = 24


async def _active_plan(
    session: AsyncSession, account_id: int
) -> models.Plan | None:
    """The account's active plan, or None.

    The same query `routers/keys.py::_get_active_plan` makes: a subscription in
    `trial`/`active` joined to its plan. Duplicated rather than imported because the
    dependency runs the other way — a service must not read a router's private helper —
    and the two are small enough that a shared home would be a module for one query.
    """
    res = await session.execute(
        select(models.Plan)
        .join(
            models.PlanSubscription,
            models.PlanSubscription.plan_id == models.Plan.id,
        )
        .where(
            models.PlanSubscription.account_id == account_id,
            models.PlanSubscription.status.in_(["trial", "active"]),
        )
    )
    return res.scalar_one_or_none()


async def priority_for_account(
    session: AsyncSession, account: models.Account
) -> str:
    """The queue class a request opened *now* for this account deserves.

    `priority` when the account's active plan carries `priority_support` (Pro today),
    `standard` otherwise — including when the account has no plan at all, because an
    account that never subscribed is a best-effort account, not an error case.
    """
    plan = await _active_plan(session, account.id)
    if plan is not None and plan.priority_support:
        return models.SUPPORT_PRIORITY_HIGH
    return models.SUPPORT_PRIORITY_STANDARD


def response_target_hours(plan: models.Plan | None) -> int | None:
    """The first-response target a plan promises, or None for best-effort.

    A function rather than a lookup table so the target and the plan flag that grants
    it cannot drift: the number is only ever produced by reading the same
    `priority_support` bit the queue class derives from.
    """
    if plan is not None and plan.priority_support:
        return PRIORITY_RESPONSE_TARGET_HOURS
    return None


async def target_hours_by_account(
    session: AsyncSession, account_ids: list[int]
) -> dict[int, int | None]:
    """First-response target per account, in one query for a whole page.

    The queue shows a breach per row, so resolving each row's plan separately would be
    an N+1 behind a list. An account absent from the result has no target (`None`).
    """
    if not account_ids:
        return {}
    rows = (
        await session.execute(
            select(models.PlanSubscription.account_id, models.Plan)
            .join(models.Plan, models.Plan.id == models.PlanSubscription.plan_id)
            .where(
                models.PlanSubscription.account_id.in_(account_ids),
                models.PlanSubscription.status.in_(["trial", "active"]),
            )
        )
    ).all()
    return {account_id: response_target_hours(plan) for account_id, plan in rows}


async def target_hours_for_account(
    session: AsyncSession, account_id: int
) -> int | None:
    return (await target_hours_by_account(session, [account_id])).get(account_id)


def target_breached(
    request: models.SupportRequest, hours: int | None, now: datetime
) -> bool:
    """Whether an unanswered request is past its account's target.

    An answered request can never be breached: `first_response_at` is the answer to
    "did we make it", so once it exists the clock has stopped, however late it landed
    — a separate `late` flag would be a second way to say the same thing.
    """
    if hours is None or request.first_response_at is not None:
        return False
    created = as_utc(request.created_at)
    return now - created > timedelta(hours=hours)


async def open_request(
    session: AsyncSession,
    account: models.Account,
    subject: str,
    category: str,
    body: str,
) -> models.SupportRequest:
    """Create a request and store the opening text as message #1.

    The caller commits. The message is written in the same transaction as the header so
    a request can never exist headerless: "the first thing the merchant wrote" is not a
    field that can be missing.
    """
    request = models.SupportRequest(
        public_id=gen_public_id("sup_"),
        account_id=account.id,
        subject=subject,
        category=category,
        status=models.SUPPORT_OPEN,
        priority=await priority_for_account(session, account),
    )
    session.add(request)
    # Flush to mint `request.id` for the message's foreign key; the transaction is still
    # open, so the header and its first message commit together or not at all.
    await session.flush()
    session.add(
        models.SupportMessage(
            request_id=request.id,
            author_account_id=account.id,
            author_kind=models.SUPPORT_AUTHOR_MERCHANT,
            body=body,
        )
    )
    await session.flush()
    return request


async def record_operator_reply(
    session: AsyncSession,
    request: models.SupportRequest,
    admin_account: models.Account,
    body: str,
) -> models.SupportMessage:
    """Append an operator reply, start the response clock, and acknowledge the thread.

    The caller commits. `first_response_at` is set **only if it is still None**: this is
    the first time a human answered, and a second reply is not a second first response.
    Status moves `open → pending` — the platform has answered and is waiting on the
    merchant — but a resolved thread is left as it is, since nothing in this feature
    reopens one.
    """
    now = datetime.now(UTC)
    message = models.SupportMessage(
        request_id=request.id,
        author_account_id=admin_account.id,
        author_kind=models.SUPPORT_AUTHOR_OPERATOR,
        body=body,
    )
    session.add(message)
    if request.first_response_at is None:
        request.first_response_at = now
    if request.status == models.SUPPORT_OPEN:
        request.status = models.SUPPORT_PENDING
    request.updated_at = now
    await session.flush()
    return message


async def record_merchant_reply(
    session: AsyncSession,
    request: models.SupportRequest,
    account: models.Account,
    body: str,
) -> models.SupportMessage:
    """Append a merchant reply.

    `first_response_at` is deliberately untouched: the target is a promise about how
    long the *platform* takes to answer, so a merchant's own message must not satisfy
    it. A reply on a `pending` thread returns it to `open` — the operator's answer has
    been answered in turn, so the queue must show the ball is back with the platform.
    """
    message = models.SupportMessage(
        request_id=request.id,
        author_account_id=account.id,
        author_kind=models.SUPPORT_AUTHOR_MERCHANT,
        body=body,
    )
    session.add(message)
    if request.status == models.SUPPORT_PENDING:
        request.status = models.SUPPORT_OPEN
    request.updated_at = datetime.now(UTC)
    await session.flush()
    return message


def resolve_request(
    session: AsyncSession,
    request: models.SupportRequest,
    resolved_at: datetime,
) -> None:
    """Mark a thread resolved and stamp when. The caller commits."""
    request.status = models.SUPPORT_RESOLVED
    request.resolved_at = resolved_at
    request.updated_at = resolved_at


def queue_stmt():
    """The operator queue's ordering, as a statement a caller can narrow and page.

    Priority first — a priority request outranks a standard one however long it has
    waited, which is the whole of what a Pro account buys — then oldest-unanswered:
    threads no operator has touched come first, oldest first, and answered ones follow
    in the same order. The rank is a `case` rather than a sort on the literal string,
    because "priority" sorting above "standard" is an accident of the alphabet that a
    future rename would silently reverse.
    """
    priority_rank = case(
        (models.SupportRequest.priority == models.SUPPORT_PRIORITY_HIGH, 1),
        else_=0,
    )
    return (
        select(models.SupportRequest)
        .order_by(
            priority_rank.desc(),
            # `False` (no first response) sorts before `True`, so unanswered first.
            models.SupportRequest.first_response_at.is_not(None),
            models.SupportRequest.created_at.asc(),
        )
    )


async def load_messages(
    session: AsyncSession, request_id: int
) -> list[models.SupportMessage]:
    """One thread, in order. The opening message is the lowest id, so id order is
    thread order even for messages written in the same transaction."""
    res = await session.execute(
        select(models.SupportMessage)
        .where(models.SupportMessage.request_id == request_id)
        .order_by(models.SupportMessage.id.asc())
    )
    return list(res.scalars().all())


async def messages_by_request(
    session: AsyncSession, request_ids: list[int]
) -> dict[int, list[models.SupportMessage]]:
    """Threads for a whole page, in one query, so a list route is not an N+1."""
    if not request_ids:
        return {}
    res = await session.execute(
        select(models.SupportMessage)
        .where(models.SupportMessage.request_id.in_(request_ids))
        .order_by(models.SupportMessage.id.asc())
    )
    threads: dict[int, list[models.SupportMessage]] = {}
    for message in res.scalars().all():
        threads.setdefault(message.request_id, []).append(message)
    return threads


async def alert_priority_opened(
    request: models.SupportRequest, account: models.Account
) -> None:
    """Tell the operator channel a priority request was just opened.

    Reuses `services.notifications`, the group the platform already pushes settled
    payments and raised invoices to — a second Telegram client or a second chat id
    would be a second place for "where does the team look" to be wrong. That helper
    never raises and treats an unset chat id as silence, so this can run after the
    request's own commit and cannot undo it.
    """
    if request.priority != models.SUPPORT_PRIORITY_HIGH:
        return
    await notifications.notify_activity(
        "\n".join(
            [
                "🟣 Priority support request",
                f"Account: {account.name} <{account.email}>",
                f"Subject: {request.subject}",
                f"Request: {request.public_id}",
            ]
        )
    )


async def email_operator_reply(
    *,
    to: str,
    request: models.SupportRequest,
    reply_body: str,
    message_id: int,
) -> None:
    """Email the merchant that an operator answered, and never raise to the caller.

    Does nothing when the deployment has no email channel — an unset key is silence,
    not failure, which is the state every deployment was in before Resend existed.
    The provider idempotency key is the message id, so a retried HTTP request that
    re-attempts the send collapses into one delivery rather than two.
    """
    if not resend.is_configured():
        return
    text = "\n".join(
        [
            f"An operator has replied to your support request “{request.subject}”.",
            "",
            reply_body,
            "",
            f"Reply in the portal: {portal_url('/dashboard/support')}",
            f"Reference: {request.public_id}",
            "",
            "— ChmabaPay Support",
        ]
    )
    try:
        await resend.send_email(
            to=to,
            subject=f"Re: {request.subject} [{request.public_id}]",
            text=text,
            idempotency_key=f"support-reply-{message_id}",
        )
    except resend.ResendError as exc:
        # Reported, not retried: the reply itself is already committed and the merchant
        # can read it in the portal, so a provider outage costs a courtesy, not an
        # answer. Same choice `billing.record_due_reminders` makes for a dunning mail.
        log.error(
            "support reply email not delivered (request=%s to=%s): %s",
            request.public_id,
            to,
            exc,
        )
