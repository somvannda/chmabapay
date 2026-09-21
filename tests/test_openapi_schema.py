"""The published schema has to describe the API that actually exists.

`/openapi.json` is what a merchant reads before writing a line of code, and what
`/docs` renders. Two things about it are easy to get wrong and expensive to get
wrong: a route that needs a credential but is advertised without one (a reader
writes an unauthenticated client and gets a 401 they cannot explain), and an
internal route that is advertised at all (the operator console's surface is not
the API's public surface).

The assertions below are the contract: the credentials are declared, the routes
that are really behind auth say so, the public ones say so by saying nothing, and
the admin and dev surfaces are absent.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_PAGE = REPO_ROOT / "web" / "landing" / "app" / "api" / "docs" / "page.tsx"

# Routers whose every route sits behind a credential. Router-level
# `dependencies=AUTH_SECURITY` in the source is what makes this true; if a route
# is ever moved out from under it, this list is the thing that notices.
CREDENTIALED_PREFIXES = (
    "/v1/payments",
    "/v1/stores",
    "/v1/keys",
    "/v1/webhooks",
    "/v1/reports",
    "/v1/transactions",
)

# Reached without a credential, by design: the customer loading the page, or the
# merchant pasting an <img src>. Listed explicitly so adding a public route here
# is a decision rather than an oversight.
PUBLIC_PATHS = (
    "/pay/{public_id}",
    "/pay/{public_id}/qr.svg",
    "/pay/{public_id}/status",
    "/v1/khqr/render.svg",
    "/health",
)

# The four KHQR routes that drive an outbound ABA fetch. The router itself has no
# security because render.svg lives in it, so each of these carries it by hand —
# which is exactly the kind of hand-application this test exists to pin down.
OUTBOUND_KHQR_ROUTES = (
    "/v1/khqr/from-link",
    "/v1/khqr/probe-aba-status",
    "/v1/khqr/payway/checkout",
    "/v1/khqr/payway/status",
)

# In the live schema but deliberately not on the public API page: the browser
# sign-in and Google OAuth redirects the dashboard drives, the health probe, and
# the dev-rail sign-in. Listing them here makes silence a decision rather than an
# oversight — a new published path must be either documented or named here.
INTERNAL_PATHS = (
    "/auth/login",
    "/auth/signout",
    "/auth/google/login",
    "/auth/google/callback",
    "/auth/api/v1/auth/google/login",
    "/auth/api/v1/auth/google/callback",
    "/api/v1/auth/google/login",
    "/api/v1/auth/google/callback",
    "/user/google/auth/login",
    "/user/google/auth/callback",
    "/auth/_dev/login",
    "/health",
)


def _normalize_path(path: str) -> str:
    """`/v1/billing/invoices/{invoice_id}/khqr` and `{id}` are the same route."""
    return re.sub(r"\{[^}]+\}", "{}", path)


def _docs_page_text() -> str:
    """The published docs page, or a skip.

    The docs page is a frontend file and the API image contains no `web/`, so a
    test that reads it fails inside the container for a reason that has nothing to
    do with the API. A full checkout — CI, or a local run that mounts the frontend
    — has the file and the assertions below really run. Skipping is the honest
    answer for an API-only checkout, and the message names the mount so the
    difference is visible rather than silent.
    """
    if not DOCS_PAGE.exists():
        pytest.skip(
            "docs page not in this checkout; to run this assertion mount the "
            "frontend, e.g. -v <repo>/web:/app/web:ro "
            f"(looked in {DOCS_PAGE})"
        )
    return DOCS_PAGE.read_text(encoding="utf-8")


def _documented_operations() -> set[tuple[str, str]]:
    """(METHOD, path) for every endpoint row on the public docs page."""
    text = _docs_page_text()
    return {
        (method, _normalize_path(path))
        for method, path in re.findall(
            r'method:\s*"([A-Z]+)",\s*path:\s*"([^"]+)"', text
        )
    }


@pytest.fixture
async def schema(client) -> dict[str, Any]:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()


def _operations(schema: dict[str, Any]):
    """Yield (path, method, operation) for every documented operation."""
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if method in {"get", "post", "put", "patch", "delete", "head", "options"}:
                yield path, method, operation


def test_both_credentials_are_declared(schema: dict[str, Any]) -> None:
    schemes = schema["components"]["securitySchemes"]
    assert schemes["ApiKey"]["type"] == "http"
    assert schemes["ApiKey"]["scheme"] == "bearer"
    assert schemes["SessionCookie"]["type"] == "apiKey"
    assert schemes["SessionCookie"]["in"] == "cookie"
    assert schemes["SessionCookie"]["name"] == "chmabapay_session"


def test_the_routes_behind_a_credential_say_so(schema: dict[str, Any]) -> None:
    offenders = []
    checked = 0
    for path, method, operation in _operations(schema):
        if path.startswith(CREDENTIALED_PREFIXES):
            checked += 1
            if not operation.get("security"):
                offenders.append(f"{method.upper()} {path}")
    assert checked > 20, f"expected to check the API surface, only saw {checked} routes"
    assert offenders == [], f"documented without security: {offenders}"


def test_the_public_routes_are_documented_without_security(schema: dict[str, Any]) -> None:
    for path in PUBLIC_PATHS:
        operations = [
            operation
            for probe_path, _, operation in _operations(schema)
            if probe_path == path
        ]
        assert operations, f"{path} is missing from the schema"
        for operation in operations:
            assert not operation.get("security"), f"{path} should not require a credential"


def test_the_outbound_khqr_routes_are_secured(schema: dict[str, Any]) -> None:
    for path in OUTBOUND_KHQR_ROUTES:
        operation = schema["paths"][path]["post"]
        security = operation.get("security")
        assert security, f"POST {path} is missing its security declaration"
        schemes = {name for requirement in security for name in requirement}
        assert schemes == {"ApiKey", "SessionCookie"}, f"POST {path} allows {schemes}"
        assert "401" in operation["responses"], f"POST {path} does not document 401"


def test_a_quota_refusal_is_documented_where_it_can_happen(schema: dict[str, Any]) -> None:
    """`POST /v1/payments` refuses with 402 once the plan's quota is spent."""
    responses = schema["paths"]["/v1/payments"]["post"]["responses"]
    assert "402" in responses
    assert responses["402"]["content"]["application/json"]["schema"] is not None


def test_the_internal_surfaces_are_not_published(schema: dict[str, Any]) -> None:
    """The operator console and the dev tools are reachable, just not advertised."""
    published = set(schema["paths"])
    leaked = sorted(p for p in published if p.startswith("/v1/admin") or p.startswith("/_dev"))
    assert leaked == []


async def test_the_hidden_routers_still_serve(client) -> None:
    """Hiding a router from the schema must not unmount it.

    `include_in_schema=False` is easy to misread as "disabled". It only omits the
    routes from the generated document; they keep dispatching, and keep enforcing
    their own auth — a refusal, never a 404 from a missing route.
    """
    assert (await client.get("/v1/admin/overview")).status_code in {401, 403}
    assert (await client.get("/_dev/integration-test")).status_code == 200


def test_every_published_path_is_documented_or_declared_internal(
    schema: dict[str, Any],
) -> None:
    """`/openapi.json` is the whole API; the docs page is what a merchant reads.

    A path cannot quietly exist in one and not the other: either it is on the page,
    or it is named in `INTERNAL_PATHS` so the omission is a decision. `{invoice_id}`
    and `{id}` are the same route, so parameter names are normalized away.
    """
    documented = {path for _, path in _documented_operations()}
    internal = {_normalize_path(path) for path in INTERNAL_PATHS}
    published = {_normalize_path(path) for path in schema["paths"]}

    undocumented = sorted(published - documented - internal)
    assert undocumented == [], (
        f"published but neither documented nor declared internal: {undocumented}"
    )


def test_the_previously_omitted_endpoints_are_on_the_docs_page() -> None:
    """The five routes that existed in the schema and in neither document."""
    operations = _documented_operations()
    required = (
        ("PUT", "/v1/stores/{public_id}"),
        ("PATCH", "/v1/account"),
        ("POST", "/v1/me/password"),
        ("DELETE", "/v1/me"),
        ("POST", "/v1/transactions/token/renew"),
    )
    missing = [
        f"{method} {path}"
        for method, path in required
        if (method, _normalize_path(path)) not in operations
    ]
    assert missing == [], f"missing from the docs page: {missing}"


def test_payment_create_and_reissue_declare_their_reachable_statuses(
    schema: dict[str, Any],
) -> None:
    """The decorators were silent, so the schema advertised 200 for routes that mint
    201 and omitted the 400/404/502 the handlers can return.

    These are the status sets the handlers can actually answer (the router-level
    401/403 ride on `AUTH_ERRORS`); the published schema has to include them all.
    """
    create = set(schema["paths"]["/v1/payments"]["post"]["responses"])
    assert {"200", "201", "400", "402", "404", "502"} <= create

    reissue = set(
        schema["paths"]["/v1/payments/{public_id}/reissue"]["post"]["responses"]
    )
    assert {"200", "201", "400", "404", "409", "502"} <= reissue
