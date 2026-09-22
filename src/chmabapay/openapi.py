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

    `detail` is a machine code. The three refusals a merchant has to tell apart are
    `store_disabled` (they switched the store off), `store_billing_suspended` (the platform is
    holding it because the account is over its plan's store allowance) and `account_restricted`
    (the whole account is frozen for an unpaid plan invoice) — each has a different fix, and a
    merchant who cannot tell them apart calls support instead of paying. Others include
    `quota_exceeded` and `whitelabel_not_enabled`. In a few older places `detail` is a human
    sentence; both are strings, and FastAPI's own validation errors are a list of objects —
    which is why this is a union rather than a `str`.
    """

    detail: str | list[dict[str, Any]] | None = None


API_KEY_SCHEME = HTTPBearer(
    scheme_name="ApiKey",
    description=(
        "`Authorization: Bearer ck_live_…` — an API key created in the dashboard."
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

# What every authenticated route can answer, whatever else it does. The two 403 codes are
# different states with different ways out: `account_suspended` is an operator's lockout and
# nothing the caller can undo, `account_restricted` is a billing hold and the billing routes
# are exactly what lifts it.
AUTH_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorOut, "description": "Missing or invalid credentials."},
    403: {
        "model": ErrorOut,
        "description": (
            "The account is suspended (`account_suspended`), frozen for an unpaid plan "
            "invoice (`account_restricted` — only the billing routes are served while it "
            "holds), or this credential may not do this (`whitelabel_not_enabled`)."
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

# A precondition on the request itself failed: a store that is switched off
# (`store_disabled`) or held by the platform for billing (`store_billing_suspended`), a link
# with no destination, an amount outside the link's bounds, an offline QR nothing can confirm.
# Only on the routes that create or transition a payment.
BAD_REQUEST_ERROR = {
    400: {
        "model": ErrorOut,
        "description": (
            "A precondition the request must meet was not met. On the payment routes this "
            "includes `store_disabled` and `store_billing_suspended`, which are different "
            "states: one is the merchant's own switch, the other is the platform holding the "
            "store until the plan is settled."
        ),
    }
}

# The resource named by the path is not in the caller's account (`store_not_found`,
# `merchant_not_found`, `payment_not_found`). Only on the routes that look one up.
NOT_FOUND_ERROR = {
    404: {
        "model": ErrorOut,
        "description": "The named resource is not in this account.",
    }
}


def merged(*maps: dict[int | str, dict[str, Any]]) -> dict[int | str, dict[str, Any]]:
    """Combine response maps for a router or a single decorator."""
    out: dict[int | str, dict[str, Any]] = {}
    for mapping in maps:
        out.update(mapping)
    return out
