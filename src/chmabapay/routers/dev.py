"""Fake Bakong rail (Phase 1). Simulates a customer scanning + paying.

Only mounted when settings.enable_dev_gateway is true. NEVER enable in production.

Also serves the dev-only integration test runner: a self-contained page that
executes the machine-readable plan in ``chmabapay.tools.testplan`` against this
same origin. Both surfaces sit behind the same flag — if the fake rail is off,
the runner is off too.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..services import payments as svc
from ..tools.testplan import build_test_plan

router = APIRouter(prefix="/_dev", tags=["dev"], include_in_schema=False)

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"


@lru_cache(maxsize=4)
def _tool_page(name: str) -> str:
    return (_TOOLS_DIR / name).read_text(encoding="utf-8")


async def _serve_tool_page(name: str) -> HTMLResponse:
    try:
        return HTMLResponse(_tool_page(name))
    except OSError as exc:  # pragma: no cover - packaging accident only
        raise HTTPException(status_code=500, detail=f"dev_page_missing: {name}: {exc}") from exc


@lru_cache(maxsize=1)
def _test_plan() -> dict:
    return build_test_plan()


@router.get("/integration-test", response_class=HTMLResponse, include_in_schema=False)
async def integration_test_page() -> HTMLResponse:
    """Browser runner for the integration + security test plan."""
    return await _serve_tool_page("integration_test.html")


@router.get("/integration-test/manifest", include_in_schema=False)
async def integration_test_manifest() -> dict:
    """The full case catalog the browser runner executes."""
    return _test_plan()


@router.get("/live-pay", response_class=HTMLResponse, include_in_schema=False)
async def live_pay_page() -> HTMLResponse:
    """Generate a real scannable KHQR from an ABA PayWay link and watch its live
    ABA status — the A/B test for `khqr.py`'s offline EMVCo build."""
    return await _serve_tool_page("live_pay.html")


def _payment_dict(payment):
    if payment is None:
        return None
    return {
        "id": payment.public_id,
        "status": payment.status,
        "amount": f"{payment.amount_cents / 100:.2f}",
        "currency": payment.currency,
    }


@router.post("/payments/{public_id}/scan")
async def dev_scan(public_id: str, session: AsyncSession = Depends(get_session)):
    """Customer opened their banking app after scanning the QR."""
    payment = await svc.get_payment_for_store(session, public_id)
    await svc.mark_scanned(session, payment.id)
    return _payment_dict(await svc.get_payment_for_store(session, public_id))


@router.post("/payments/{public_id}/pay")
async def dev_pay(public_id: str, session: AsyncSession = Depends(get_session)):
    """Customer confirmed the payment; the rail reports the credit."""
    payment = await svc.mark_paid(
        session,
        public_id,
        bakong_ref=f"FAKEREF-{public_id[-8:]}",
        gateway_raw={"rail": "fake-bakong", "note": "Phase 1 simulation"},
    )
    if payment is None:
        raise HTTPException(status_code=404, detail="payment_not_payable")
    return _payment_dict(payment)
