"""OpenAPI metadata the routers share: the two credentials, and the error bodies.

FastAPI builds `components.securitySchemes` out of the security *dependencies* in a
route's chain, and marks an operation as secured when one is present. So the two
schemes are declared once here and attached to the routers whose routes are genuinely
behind them. A padlock in `/docs` then reflects the code; a hand-annotated schema
would stop being true the first time a route moved between routers.

`auto_error=False` on both is deliberate. These declarations *document* and *extract*
— they do not enforce. Enforcement is already `auth.get_current_auth_context` /
`auth.get_current_session_account`, which run on the same routes. A second, weaker
copy of the rule would also answer the wrong status: a missing credential is 401 from
the real dependency, and 403 from `HTTPBearer`'s default.

The error bodies are shared for the same reason. `responses={}` on the router is the
one place that can say "every route here can answer 401 and 403" without repeating it
forty times, and it cannot drift from the routes because it is attached to them.
"""

from __future__ import annotations

from typing import Any

from fastapi import Security
from fastapi.security import APIKeyCookie, HTTPBearer
from pydantic import BaseModel

from .routers.auth import SESSION_COOKIE


class ErrorOut(BaseModel):
    """An error body is `{"detail": …}`.

    `detail` is a machine code (`quota_exceeded`, `store_disabled`) on the API paths
    and, in a few older places, a human sentence; both are strings, and FastAPI's own
    validation errors are a list of objects — which is why this is a union rather
    than a `str`.
    """

    detail: str | list[dict[str, Any]] | None = None


API_KEY_SCHEME = HTTPBearer(
    scheme_name="ApiKey",
    description=(
        "`Authorization: Bearer ck_live_…` — an API key created in the dashboard. "
        "`ck_test_…` keys are for test stores."
    ),
    auto_error=False,
)
SESSION_COOKIE_SCHEME = APIKeyCookie(
    name=SESSION_COOKIE,
    scheme_name="SessionCookie",
    description="The signed-in dashboard session cookie.",
    auto_error=False,
)

# Either credential. Attach to `APIRouter(dependencies=…)` when every route in the
# router is behind auth, or to a single decorator when only some are.
AUTH_SECURITY = [Security(API_KEY_SCHEME), Security(SESSION_COOKIE_SCHEME)]
SESSION_SECURITY = [Security(SESSION_COOKIE_SCHEME)]

# What every authenticated route can answer, whatever else it does.
AUTH_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorOut, "description": "Missing or invalid credentials."},
    403: {
        "model": ErrorOut,
        "description": (
            "The account is suspended, or this credential may not do this "
            "(`whitelabel_not_enabled`)."
        ),
    },
}

# The plan quota, refused before the store is touched. Only on the routes that create
# something billable.
QUOTA_ERROR = {
    402: {
        "model": ErrorOut,
        "description": "Plan quota reached (`quota_exceeded`).",
    }
}

# A state transition that the payment's current state forbids (`payment_already_paid`,
# `payment_reversed`, `payment_not_expired`). Only on the routes that transition.
CONFLICT_ERROR = {
    409: {
        "model": ErrorOut,
        "description": "The resource's current state forbids this transition.",
    }
}

# The rail did not answer, or answered with something we cannot use
# (`payway_hosted_error`). Only on the routes that call ABA directly.
UPSTREAM_ERROR = {
    502: {
        "model": ErrorOut,
        "description": "The upstream rail failed (`payway_hosted_error`).",
    }
}


def merged(*maps: dict[int | str, dict[str, Any]]) -> dict[int | str, dict[str, Any]]:
    """Combine response maps for a router or a single decorator."""
    out: dict[int | str, dict[str, Any]] = {}
    for mapping in maps:
        out.update(mapping)
    return out
