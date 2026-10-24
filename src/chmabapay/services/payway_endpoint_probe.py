"""Is ABA's hosted-checkout API still where we think it is?

On 2026-10-24 ABA moved it without notice: ``pwapp.ababank.com/api`` started
answering every call with nginx's plain-text 403, and the link page's own build
had moved to ``link.payway.com.kh/api``. Both the mint and the status call share
that base, so the move broke QR generation *and* left every issued session
unpollable - and the first report came from a merchant, not from the platform.

This module asks the question on a timer instead, and answers it without minting
anything or touching a session: the status endpoint is called with an empty body,
so the worst it can do is collect a 400 saying the fields are missing - which is
the proof that the endpoint still exists. When the configured base stops
answering, it re-derives the current one from the PayWay page's own bundle (the
``API_BASE_PUBLIC_URL`` constant that gave away the last move) and names it in
the alert, so the next move is a one-line config change rather than an afternoon
of hunting.

The verdict is deliberately conservative: a 4xx is "the endpoint exists and
rejects this probe", never "the API is down". Only a missing path, a refusal, a
5xx or no answer at all is a condition worth paging about.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urljoin, urlsplit

import httpx

from ..config import get_settings
from .payway_parser import (
    BROWSER_HEADERS,
    HOSTED_CHECK_PAYMENT_STATUS,
    PAYWAY_BASE,
    hosted_api_base,
)

if TYPE_CHECKING:  # the import is deferred to avoid a cycle: alerts reaches this module
    from ..alerts import Condition

log = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 8.0
DEFAULT_INTERVAL_SECONDS = 1800.0
MAX_DISCOVERY_SCRIPTS = 8

# The bundle constant that gave away the 2026-10-24 move. Both the quoted
# ("API_BASE_PUBLIC_URL":"https://...") and the assignment
# (API_BASE_PUBLIC_URL="https://...") shapes appear in Nuxt builds, so accept
# either rather than pinning one spelling.
_API_BASE_PATTERN = re.compile(
    r"""API_BASE_PUBLIC_URL["']?\s*[:=]\s*["'](https?://[^"']+)["']"""
)
_SCRIPT_PATTERN = re.compile(r"""<script[^>]+src=["']([^"']+)["']""", re.IGNORECASE)

Outcome = Literal["answers", "refused", "missing", "unreachable"]
VerdictStatus = Literal["ok", "moved", "refused", "down"]


@dataclass(frozen=True)
class EndpointProbe:
    """What one request to a candidate base concluded."""

    base_url: str
    outcome: Outcome
    status_code: int | None = None
    detail: str = ""
    latency_ms: int | None = None
    redirected_to: str | None = None

    @property
    def healthy(self) -> bool:
        return self.outcome == "answers"


@dataclass(frozen=True)
class EndpointVerdict:
    """The configured base, plus where the page says it should be."""

    status: VerdictStatus
    configured: EndpointProbe
    discovered: list[str] = field(default_factory=list)
    working: str | None = None

    @property
    def healthy(self) -> bool:
        return self.status == "ok"


def _base_of(url: str) -> str:
    """``https://host/api/path`` -> ``https://host/api``: the part we configure."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    # The probe URL ends with HOSTED_CHECK_PAYMENT_STATUS; strip it back to the
    # base the code appends paths to.
    if path.endswith(HOSTED_CHECK_PAYMENT_STATUS):
        path = path[: -len(HOSTED_CHECK_PAYMENT_STATUS)]
    return f"{parts.scheme}://{parts.netloc}{path}".rstrip("/")


def _classify(response: httpx.Response) -> Outcome:
    """What an HTTP answer says about the *endpoint*, not about our credentials.

    A 401/403 is ambiguous, and the difference matters: ABA's edge refusing this
    host returns nginx's HTML (the 2026-10-24 symptom), while the application
    answering with JSON means the route exists and only the probe's credentials
    are missing. Paging "ABA refuses this network" for the second case would teach
    the reader to ignore the alert.
    """
    status_code = response.status_code
    if 200 <= status_code < 300:
        return "answers"
    if status_code in (400, 405, 409, 415, 422):
        # A validation refusal proves the route exists and is executing.
        return "answers"
    if status_code in (401, 403, 406, 429):
        content_type = (response.headers.get("content-type") or "").lower()
        return "answers" if "json" in content_type else "refused"
    if status_code == 404:
        return "missing"
    return "unreachable"


def _probe_client(timeout: float, *, follow_redirects: bool = False) -> httpx.AsyncClient:
    """An ABA-facing client, routed through the same egress as the QR fetches.

    ABA refuses some datacenter ranges outright (see ``payway_parser._client``),
    so sharing ``payway_proxy_url`` is what makes this probe meaningful behind a
    host that needs it. Redirects are off by default: a redirect *is* the finding.
    """
    proxy = (get_settings().payway_proxy_url or "").strip() or None
    kwargs: dict[str, Any] = {"timeout": timeout, "follow_redirects": follow_redirects}
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)


def _probe_headers() -> dict[str, str]:
    headers = {
        **BROWSER_HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": PAYWAY_BASE,
        "Referer": f"{PAYWAY_BASE}/",
    }
    return headers


async def probe_endpoint(
    base_url: str,
    *,
    path: str = HOSTED_CHECK_PAYMENT_STATUS,
    timeout: float = PROBE_TIMEOUT_SECONDS,
    client: httpx.AsyncClient | None = None,
) -> EndpointProbe:
    """Ask one candidate base to answer the status endpoint.

    The body is empty on purpose: a 400/422 is a success for this check, because
    it means ABA routed and executed the request. No session is created, no token
    is used, and nothing is minted.
    """
    url = base_url.rstrip("/") + path
    own_client = client is None
    probe_client = client or _probe_client(timeout)
    started = time.monotonic()
    try:
        response = await probe_client.post(url, json={}, headers=_probe_headers())
    except httpx.HTTPError as exc:
        return EndpointProbe(
            base_url=base_url,
            outcome="unreachable",
            detail=f"{exc.__class__.__name__}: {exc}",
        )
    finally:
        if own_client:
            await probe_client.aclose()
    latency = int((time.monotonic() - started) * 1000)

    location = response.headers.get("location")
    if response.is_redirect and location:
        target = urljoin(url, location)
        return EndpointProbe(
            base_url=base_url,
            outcome="missing",
            status_code=response.status_code,
            detail=f"redirected to {target}",
            latency_ms=latency,
            redirected_to=target,
        )

    outcome = _classify(response)
    return EndpointProbe(
        base_url=base_url,
        outcome=outcome,
        status_code=response.status_code,
        detail=f"answered {response.status_code}",
        latency_ms=latency,
    )


def extract_api_bases(text: str) -> list[str]:
    """Absolute API bases named in a page or bundle, in first-seen order."""
    seen: dict[str, None] = {}
    for match in _API_BASE_PATTERN.finditer(text):
        base = match.group(1).rstrip("/")
        seen.setdefault(base, None)
    return list(seen)


def _script_sources(html: str) -> list[str]:
    return [src for src in _SCRIPT_PATTERN.findall(html) if "_nuxt" in src or src.endswith(".js")]


async def discover_hosted_api_bases(
    *,
    timeout: float = PROBE_TIMEOUT_SECONDS,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Re-derive the hosted API base from the PayWay page's current build.

    Fetches ``https://link.payway.com.kh`` and then its bundles, looking for the
    ``API_BASE_PUBLIC_URL`` constant the SPA uses. That constant is what revealed
    the 2026-10-24 move, and it is the only source we trust more than a guess:
    it is the address ABA's own page posts to.

    Never raises. An unreachable page simply yields no candidates.
    """
    own_client = client is None
    probe_client = client or _probe_client(timeout, follow_redirects=True)
    try:
        page = await probe_client.get(PAYWAY_BASE, headers=BROWSER_HEADERS)
        if page.status_code != 200:
            log.warning("payway discovery: link page answered %s", page.status_code)
            return []
        candidates = extract_api_bases(page.text)
        if candidates:
            return candidates
        for src in _script_sources(page.text)[:MAX_DISCOVERY_SCRIPTS]:
            try:
                bundle = await probe_client.get(urljoin(f"{PAYWAY_BASE}/", src), headers=BROWSER_HEADERS)
            except httpx.HTTPError:
                continue
            if bundle.status_code != 200:
                continue
            candidates = extract_api_bases(bundle.text)
            if candidates:
                return candidates
        return []
    except httpx.HTTPError as exc:
        log.warning("payway discovery failed: %s", exc)
        return []
    finally:
        if own_client:
            await probe_client.aclose()


async def check_payway_endpoint(
    *,
    configured_base: str,
    timeout: float = PROBE_TIMEOUT_SECONDS,
    client: httpx.AsyncClient | None = None,
) -> EndpointVerdict:
    """Probe the configured base and, when it fails, the page's current one."""
    # An empty setting is what `PAYWAY_HOSTED_API_BASE=` in a compose file produces,
    # not an instruction to probe "": that URL has no scheme, so every check would
    # report `unreachable` and then "moved" as soon as discovery found the base the
    # code already defaults to — a false page every interval. Resolve at the
    # boundary so no caller can hand this module an empty base.
    configured_base = (configured_base or "").strip() or hosted_api_base()
    configured = await probe_endpoint(configured_base, timeout=timeout, client=client)
    if configured.healthy:
        return EndpointVerdict(status="ok", configured=configured)

    # A redirect is the cheapest possible answer: follow it before crawling the page.
    candidates: list[str] = []
    if configured.redirected_to:
        candidates.append(_base_of(configured.redirected_to))

    discovered: list[str] = []
    # A move and a network refusal both arrive as a bare 403 — the 2026-10-24 move
    # was exactly that. The page's own bundle is what tells them apart: if it names
    # a base that answers, this is a move; if it names the configured base (or
    # nothing), the refusal really is this host's egress and the proxy advice stands.
    # Without `refused` here the monitor would have paged "set PAYWAY_PROXY_URL" for
    # the one incident it was built for.
    if not candidates and configured.outcome in ("missing", "unreachable", "refused"):
        discovered = await discover_hosted_api_bases(timeout=timeout, client=client)
        candidates.extend(discovered)

    seen: set[str] = set()
    for candidate in candidates:
        base = candidate.rstrip("/")
        if not base or base in seen or base == configured_base.rstrip("/"):
            continue
        seen.add(base)
        probe = await probe_endpoint(base, timeout=timeout, client=client)
        if probe.healthy:
            return EndpointVerdict(
                status="moved",
                configured=configured,
                discovered=discovered,
                working=base,
            )
        if probe.redirected_to:
            redirected = _base_of(probe.redirected_to)
            if redirected not in seen:
                redirect_probe = await probe_endpoint(redirected, timeout=timeout, client=client)
                seen.add(redirected)
                if redirect_probe.healthy:
                    return EndpointVerdict(
                        status="moved",
                        configured=configured,
                        discovered=discovered,
                        working=redirected,
                    )

    if configured.outcome == "refused":
        return EndpointVerdict(status="refused", configured=configured, discovered=discovered)
    return EndpointVerdict(status="down", configured=configured, discovered=discovered)


def condition_for(verdict: EndpointVerdict, *, configured_base: str):
    """The operator-facing condition for a bad verdict, or ``None`` when healthy.

    Imported lazily: ``alerts`` pulls in the worker package, and this module is
    itself reached from there.
    """
    if verdict.healthy:
        return None
    from ..alerts import Condition

    configured = verdict.configured
    if verdict.status == "moved":
        return Condition(
            key="payway_hosted_endpoint",
            summary="ABA moved the hosted checkout API",
            detail=(
                f"{configured_base} no longer answers ({configured.detail}). "
                f"The PayWay page's own bundle points at {verdict.working}, "
                "which accepted the same request. Set PAYWAY_HOSTED_API_BASE to "
                "it and restart. Both the mint and the status call read this base, "
                "so QR generation and every open session are broken until it is "
                "updated."
            ),
            impact=(
                "no QR can be minted and payments already in flight cannot be "
                "confirmed"
            ),
        )
    if verdict.status == "refused":
        return Condition(
            key="payway_hosted_endpoint",
            summary="ABA refuses the hosted checkout from this network",
            detail=(
                f"{configured_base} answered {configured.status_code} "
                f"({configured.detail}). ABA serves the same request from other "
                "networks, so this is the host's egress, not a broken endpoint. "
                "Set PAYWAY_PROXY_URL to route the ABA hop through an allowed "
                "address, or ask ABA to allowlist this host."
            ),
            impact="no QR can be minted and payments in flight cannot be confirmed",
        )
    candidates = ", ".join(verdict.discovered[:5]) or "none found"
    return Condition(
        key="payway_hosted_endpoint",
        summary="ABA hosted checkout unreachable",
        detail=(
            f"{configured_base} did not answer the status endpoint "
            f"({configured.detail}). Candidates from the PayWay page: {candidates}. "
            "If a candidate answers, set PAYWAY_HOSTED_API_BASE to it."
        ),
        impact="no QR can be minted and payments in flight cannot be confirmed",
    )


class PaywayEndpointProbe:
    """The probe clamped to an interval, for the alert watcher's 30s tick.

    The watcher evaluates conditions every ``alert_interval_seconds``; asking ABA
    that often would be rude and would turn one outage into a request storm. The
    cached verdict is returned between checks, so a real move keeps producing the
    same condition and the watcher still reports one edge, not one per tick.
    """

    def __init__(
        self,
        *,
        base_url: str,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        timeout: float = PROBE_TIMEOUT_SECONDS,
    ) -> None:
        # Same guard as `check_payway_endpoint`: an empty env value means "use the
        # built-in base", and `condition_for` must quote the base actually probed.
        self.base_url = (base_url or "").strip().rstrip("/") or hosted_api_base()
        self.interval_seconds = interval_seconds
        self.timeout = timeout
        self._last_checked: float | None = None
        self._last: EndpointVerdict | None = None

    async def verdict(self, *, force: bool = False) -> EndpointVerdict:
        now = time.monotonic()
        if (
            not force
            and self._last is not None
            and self._last_checked is not None
            and now - self._last_checked < self.interval_seconds
        ):
            return self._last
        self._last = await check_payway_endpoint(
            configured_base=self.base_url, timeout=self.timeout
        )
        self._last_checked = now
        return self._last

    async def conditions(self) -> list[Condition]:
        verdict = await self.verdict()
        condition = condition_for(verdict, configured_base=self.base_url)
        return [condition] if condition else []


def main() -> int:
    """`python -m chmabapay.services.payway_endpoint_probe` — run the check once.

    Prints the verdict, every candidate found in the page bundle, and exits
    non-zero when the configured base is not answering. Useful for verifying the
    proxy and for finding the new base the moment an alert says ABA moved it.
    """
    settings = get_settings()
    # Resolved, not the raw setting: an empty env value must print the base that is
    # actually probed, or the CLI reports "moved" against nothing at all.
    base = hosted_api_base()

    async def run() -> int:
        verdict = await check_payway_endpoint(
            configured_base=base,
            timeout=settings.alert_payway_endpoint_timeout_seconds,
        )
        print(f"configured: {base}")
        print(f"status: {verdict.status}")
        print(f"detail: {verdict.configured.detail}")
        if verdict.working:
            print(f"working: {verdict.working}")
        for candidate in verdict.discovered:
            print(f"candidate: {candidate}")
        return 0 if verdict.healthy else 1

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
