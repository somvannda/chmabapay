"""The KHQR encoder's own tests.

There were none. Nothing in the suite referenced `crc16_ccitt_false`, `parse_tlv`
or the payload builder, so the one function that decides whether a customer's QR is
scannable at all was covered only indirectly, through whatever the payment tests
happened to touch.

`CAPTURED_PAYWAY_PAYLOAD` is a real payload sampled from a live ABA PayWay link page
during the KHQR reverse-engineering work. It used to live in `scripts/_crc_qr.txt`,
read by a script (`scripts/crc_verify.py`) that nothing ran — and that could no
longer run, because it imported a `KHQR` class that has since been removed from the
module. The capture's value is the assertions it makes possible, not the file, so it
lives here now, inlined so this test depends on no scratch artifact.

Provenance note: the payload embeds a real merchant name and account identifier.
`tests/conftest.py` holds the same string for its hosted-checkout fixture, so the
repository already carries it. If that ever needs to stop, replace both copies
together with a synthetic payload that preserves this TLV shape — the assertions
below are about structure, not about whose account it is.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from chmabapay.khqr import build_khqr_payload, crc16_ccitt_false, parse_tlv
from chmabapay.models import PaymentLink, Store

# A real ABA PayWay KHQR, captured verbatim. Top-level tags:
#   00 payload format, 01 dynamic, 30 merchant account, 52 merchant category,
#   53 currency (840 = USD), 54 amount, 58 country, 59 merchant name, 60 city,
#   62.50 the PAYWAY@ABA private template, 63 the CRC.
CAPTURED_PAYWAY_PAYLOAD = (
    "00020101021230390016abaakhppxxx@abaa0115126071610243081520489995303840540115802KH"
    "5914SOMVANNDA KONG6003N/A629950950010PAYWAY@ABA01232364634-518710-262481770216TAG50"
    "_VERIFY_0030616TAG50_VERIFY_00305101789021696630451AA"
)


def _crc_of(payload: str) -> str:
    """The tag-63 value our implementation computes for a payload's body.

    EMVCo defines the CRC over everything up to and including the literal "6304"
    that introduces tag 63, so it can only be checked by recomputing it.
    """
    return "6304" + f"{crc16_ccitt_false((payload[:-8] + '6304').encode()):04X}"


def test_the_captured_payload_crc_matches_our_implementation() -> None:
    """Our CRC-16 is ABA's CRC-16, checked against a payload they produced.

    This is the highest-consequence assertion in the file. Get the CRC wrong and
    nothing errors: the QR simply never scans, and every wallet rejects it in
    silence. Comparing against a real captured payload is the only way to know the
    implementation is the right algorithm rather than merely self-consistent.
    """
    assert _crc_of(CAPTURED_PAYWAY_PAYLOAD) == CAPTURED_PAYWAY_PAYLOAD[-8:]


def test_the_captured_payload_parses_to_its_real_structure() -> None:
    """The parser reads genuine ABA output, not just output we wrote ourselves."""
    top = dict(parse_tlv(CAPTURED_PAYWAY_PAYLOAD))

    assert list(top) == ["00", "01", "30", "52", "53", "54", "58", "59", "60", "62", "63"]

    merchant_account = dict(parse_tlv(top["30"]))
    assert merchant_account["00"] == "abaakhppxxx@abaa"  # the ABA switch GI
    assert merchant_account["01"] == "126071610243081"  # the merchant's Bakong id

    private_template = dict(parse_tlv(dict(parse_tlv(top["62"]))["50"]))
    assert private_template["00"] == "PAYWAY@ABA"

    assert top["53"] == "840"  # USD
    assert top["54"] == "1"


def test_the_captured_payload_has_no_tag_99() -> None:
    """The dynamic window is ours, not ABA's — and tag 99 is why that matters.

    ABA's own payload stops at tag 63. We add a tag 99 validity window, and the
    comment on it says Bakong wallets treat a payload without one as already
    expired. So our encoder's output is deliberately a superset of what ABA emits;
    a future change that "simplifies" by dropping tag 99 would be undetectable
    here unless this difference is pinned.
    """
    assert "99" not in dict(parse_tlv(CAPTURED_PAYWAY_PAYLOAD))


def test_our_encoder_emits_a_payload_whose_crc_verifies() -> None:
    """The payload we actually ship carries a CRC that recomputes.

    Synthetic merchant values on purpose: this asserts the encoder's behaviour, not
    anyone's account details.
    """
    store = Store(public_id="st_khqr_test", account_id=1, name="Test Store")
    link = PaymentLink(
        store_id=1,
        raw_link="https://link.payway.com.kh/ABAPAYpe000000Y",
        merchant_account_id="000000000000000",
        merchant_name="TEST MERCHANT",
        payway_client_id="1111111-111111-11111111",
        currency="USD",
    )

    payload = build_khqr_payload(
        store,
        link,
        amount_cents=100,
        bill_number="CHMABA0000000001",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    assert _crc_of(payload) == payload[-8:]
    # Same shape as the capture, plus our tag 99.
    top = dict(parse_tlv(payload))
    assert top["00"] == "01"
    assert top["01"] == "12"  # dynamic, not static
    assert top["53"] == "840"
    assert "99" in top
    assert "63" in top


def test_our_encoder_formats_a_round_amount_without_trailing_zeros() -> None:
    """100 cents is "1", not "1.00" — ABA's own payload writes it that way.

    Pinned because it is invisible in the happy path: a wallet that shows "$1.00"
    looks fine, so nothing surfaces the difference until a payload is compared
    against ABA's byte for byte.
    """
    store = Store(public_id="st_khqr_test", account_id=1, name="Test Store")
    link = PaymentLink(
        store_id=1,
        raw_link="https://link.payway.com.kh/ABAPAYpe000000Y",
        merchant_account_id="000000000000000",
        currency="USD",
    )

    payload = build_khqr_payload(
        store,
        link,
        amount_cents=100,
        bill_number="CHMABA0000000001",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    assert dict(parse_tlv(payload))["54"] == "1"


def test_the_parser_drops_a_truncated_tail_instead_of_raising() -> None:
    """A cut-off payload costs one element, not an exception.

    The parser reads a declared length and then slices that many characters. When
    the value runs past the end of the string the honest answer is "this element is
    not all here", and the elements before it are still usable. Raising instead
    would turn a truncated `gateway_raw` blob into a 500 on a page that only wanted
    to render a payment.
    """
    whole = dict(parse_tlv(CAPTURED_PAYWAY_PAYLOAD))
    truncated = parse_tlv(CAPTURED_PAYWAY_PAYLOAD[:-3])

    assert [tag for tag, _ in truncated] == list(whole)[: len(truncated)]
    assert len(truncated) < len(whole)
    assert "63" not in [tag for tag, _ in truncated]


# --------------------------------------------------------------------------- #
# Auth on the generator routes                                                #
# --------------------------------------------------------------------------- #
# These four handlers reach out to ABA / PayWay on every call. For a long time
# they carried no auth dependency at all, so an anonymous caller could drive our
# outbound traffic without bound — the rate limiter was the only thing standing
# in front of them, and it caps per address rather than per credential. The
# internal testplan asserted the 401s these tests now hold up (see the AUTH-*
# cases in `src/chmabapay/tools/testplan.py`).


async def test_the_khqr_generator_routes_require_a_credential(client) -> None:
    """No credential means 401, and it means it *before* body validation.

    An empty body would otherwise be a 422, so a 422 here would prove the request
    was being parsed by an unauthenticated handler.
    """
    unauthenticated = [
        ("POST", "/v1/khqr/from-link", {}),
        ("POST", "/v1/khqr/probe-aba-status?slug_or_url=ABAPAYpe518710Y", None),
        ("POST", "/v1/khqr/payway/checkout", {}),
        ("POST", "/v1/khqr/payway/status", {}),
    ]
    for method, path, body in unauthenticated:
        resp = await client.request(method, path, json=body)
        assert resp.status_code == 401, f"{method} {path} answered {resp.status_code}"

    # An unknown key is refused too, rather than being treated as anonymous.
    resp = await client.post(
        "/v1/khqr/from-link",
        json={"link": "https://link.payway.com.kh/ABAPAYpe518710Y", "amount": 1.0},
        headers={"Authorization": "Bearer ck_test_0000000000000000000"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "unauthorized"


async def test_the_qr_renderer_stays_public(client) -> None:
    """`render.svg` is deliberately unauthenticated: it is loaded as an `<img src>`
    on the hosted checkout page and by `web/shared/components/KHQR.tsx`, where no
    Authorization header can be attached. Locking it down would blank every
    customer-facing QR, so this asserts the exemption survives future edits.
    """
    resp = await client.get("/v1/khqr/render.svg", params={"payload": "not-a-payload"})
    assert resp.status_code != 401
