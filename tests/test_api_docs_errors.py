"""The API reference has to describe refusals the API can actually return.

`/api/docs` is what an integrator codes against: the error tables there are the list of
things a client is expected to branch on. Written by hand, that list drifts in two
directions, and both are expensive. A code that is documented but no longer produced sends
a caller down a path that cannot happen; a code that is produced but not documented
surfaces as an unexplained failure, which is exactly what happened with
`open_invoice_unpaid` — the reference mapped a similar-sounding code that nothing raised.

The catalogue lives in `web/landing/app/api/docs/reference.ts` as data, so it can be read
here. As in `test_openapi_schema.py`, the frontend is absent from the API image and these
assertions skip rather than fail in an API-only checkout.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from chmabapay.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE = REPO_ROOT / "web" / "landing" / "app" / "api" / "docs" / "reference.ts"
SOURCE_ROOT = REPO_ROOT / "src" / "chmabapay"

# The modules that raise a refusal on the documented surface. Listed rather than globbed
# so the scope is visible: adding a module means deciding whether its codes are part of
# the reference.
SCANNED_MODULES = (
    "routers/account.py",
    "routers/auth.py",
    "routers/billing.py",
    "routers/checkout.py",
    "routers/keys.py",
    "routers/khqr.py",
    "routers/payments.py",
    "routers/reports.py",
    "routers/stores.py",
    "routers/transactions.py",
    "routers/webhooks.py",
    "services/billing.py",
    "services/payments.py",
    "services/stores.py",
    "auth.py",
)

# Codes the API can emit that are deliberately absent from the reference, each for a
# stated reason. Anything not in this set has to be documented.
NOT_IN_REFERENCE = frozenset(
    {
        # Reachable only from the routes the page withholds (decision D-3): the Bakong
        # ledger lookups and the two non-payable KHQR helpers.
        "bakong_error",
        "hash_must_be_64_chars",
        "md5_must_be_32_chars",
        "short_hash_must_be_8_chars",
        "at_least_one_identifier_required",
        "email_required",
        # The browser sign-in routes are internal (`INTERNAL_PATHS` in
        # test_openapi_schema.py), not API surface a client integrates against.
        "invalid_credentials",
        "google_oauth_not_configured",
        "google_token_exchange_failed",
        "google_no_id_token",
        "google_invalid_id_token",
        "google_no_email",
        "too_many_attempts",
        # Answered by middleware with its own body shape, documented as the 429 in the
        # rate-limits section rather than as a `detail` code.
        "rate_limited",
        "invalid_metrics_token",
        # The dashboard's own surface (decision D-7): key management, billing and the
        # account profile are session-only and are not integration surface, so they are
        # described in `docs/api.md` rather than on the public page. Listed here for the
        # same reason their paths are in `DASHBOARD_PATHS` in test_openapi_schema.py —
        # the omission is a decision, not an oversight.
        "invalid_session",
        "terms_not_accepted",
        "key_not_found",
        "invalid_password",
        "password_unchanged",
        "password_too_long",
        "no_password_set",
        "email_already_taken",
        "email_change_requires_password",
        "confirm_email_does_not_match",
        "platform_admin_cannot_self_delete",
        "terms_version_superseded",
        "plan_not_found",
        "plan_not_available",
        "plan_unchanged",
        "open_invoice_unpaid",
        "invoice_not_found",
        "invoice_already_paid",
        "billing_not_open",
        "invoice_not_raised",
        # Internal invariants raised by the console, not the merchant API.
        "invoice_already_void",
    }
)

# `detail="token"` and the `detail=f"token: …"` / `detail="token; …"` shapes. The `\(?`
# and `\s*` are load-bearing: several of the longer refusals are written as a parenthesised
# multi-line f-string (`detail=(\n  "billing_not_open: …"`), and without them those codes
# looked like they were never raised at all.
#
# The lookahead is what keeps prose out: `detail="Max stores (5) reached…"` starts with a
# capital and `detail=f"payment_{row.status}"` is followed by `{`, so neither is a stable
# token.
_DETAIL_TOKEN = re.compile(r'detail[=:]\s*\(?\s*f?["\']([a-z0-9_]+)(?=[:;"\'])')
_DOCUMENTED_CODE = re.compile(r'code:\s*"([a-z0-9_]+)"')
_DOCUMENTED_LIMIT = re.compile(
    r'rule:\s*"([a-z_]+)",\s*scope:\s*"[^"]*",\s*limit:\s*"(\d+) / minute"'
)


def _reference_text() -> str:
    if not REFERENCE.exists():
        pytest.skip(
            "API reference not in this checkout; to run this assertion mount the "
            f"frontend, e.g. -v <repo>/web:/app/web:ro (looked in {REFERENCE})"
        )
    return REFERENCE.read_text(encoding="utf-8")


def _documented_codes() -> set[str]:
    return set(_DOCUMENTED_CODE.findall(_reference_text()))


def _source_codes() -> dict[str, str]:
    """Every code the scanned modules raise, mapped to where it was found."""
    found: dict[str, str] = {}
    for relative in SCANNED_MODULES:
        path = SOURCE_ROOT / relative
        if not path.exists():
            pytest.skip(f"{relative} is not in this checkout")
        for code in _DETAIL_TOKEN.findall(path.read_text(encoding="utf-8")):
            found.setdefault(code, relative)
    return found


def test_every_refusal_the_api_returns_is_in_the_error_reference() -> None:
    """A caller cannot handle a refusal they have not been told about."""
    documented = _documented_codes()
    source = _source_codes()

    assert len(source) > 30, f"expected the API surface, only found {sorted(source)}"

    missing = sorted(
        f"{code} ({source[code]})"
        for code in source
        if code not in documented and code not in NOT_IN_REFERENCE
    )
    assert missing == [], f"raised but not documented: {missing}"


def test_the_reference_documents_no_code_the_api_does_not_raise() -> None:
    """The other direction, and the one that actually bit.

    `period_already_invoiced` was documented in the portal's copy map for months while
    nothing raised it; the code that *was* raised went undocument. A code listed but never
    produced is worse than a gap, because it looks like an answer.
    """
    documented = _documented_codes()
    source = set(_source_codes())

    invented = sorted(documented - source)
    assert invented == [], f"documented but never raised: {invented}"


def test_the_documented_error_codes_are_not_withheld_surface() -> None:
    """The two sets must not overlap, or a withheld route's codes are advertised."""
    overlap = sorted(_documented_codes() & NOT_IN_REFERENCE)
    assert overlap == [], (
        "documented and declared out of scope at the same time: " f"{overlap}"
    )


def test_the_documented_rate_limits_match_the_configuration() -> None:
    """A limit changed in `config.py` and not on the page is a support ticket.

    The reference states the numbers plainly, so this compares them to the settings the
    middleware actually reads rather than to a second hand-written copy.
    """
    documented = {
        rule: int(limit) for rule, limit in _DOCUMENTED_LIMIT.findall(_reference_text())
    }
    assert documented, "no rate limits parsed out of the reference"

    settings = get_settings()
    configured = {
        field[len("rate_limit_") : -len("_per_minute")]: value
        for field, value in settings.model_dump().items()
        if field.startswith("rate_limit_") and field.endswith("_per_minute")
    }

    assert documented == configured, (
        "the reference and the configuration disagree: "
        f"documented={documented} configured={configured}"
    )


def test_the_error_reference_covers_every_group_a_caller_meets() -> None:
    """A smoke check that the catalogue was not emptied to make the tests pass."""
    text = _reference_text()
    for title in (
        "Access",
        "Stores",
        "Payments",
        "KHQR codes",
        "Webhooks",
        "Reconciliation",
        "Hosted checkout",
        "Reports",
    ):
        assert f'title: "{title}"' in text, f"the {title} group is missing"
