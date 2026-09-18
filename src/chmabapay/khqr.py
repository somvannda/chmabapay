"""KHQR (EMVCo QR) payload builder for ABA PayWay.

Tag format: Tag(2 DEC digits) + Length(2 DEC digits) + Value(N chars).
Max value length per tag = 99 chars (2-digit decimal limit).

CRC-16 CCITT-FALSE: init 0xFFFF, poly 0x1021, over (body + "6304").
Result stored as 4 uppercase hex chars in Tag 63 (final).

PayWay private template lives inside Tag 62 -> SubTag 50 -> SubSubTags:
  00 = network (PAYWAY@ABA)
  01 = client_id   (from PayWay, e.g. 2364634-518710-26248177)
  02 = bill / invoice  (inside 50 so ABA sees it internally)
  03 = terminal id     (inside 50 so ABA sees it internally)
  04 = timestamp  (epoch seconds at build time)
  05 = expiry     (epoch seconds, now + ttl)
  06 = reference  (our payment public_id, for later search by external_ref)
  07 = signature  (optional, reserved)

Tag 62 also duplicates bill as 62.01 (instruction_ref) and term as 62.03 so
standard Bakong search strategies (instruction_ref, md5, short_hash) all work
whether the parser reads Tag 62 directly or unwraps the ABA private 62.50 nest.
"""

from __future__ import annotations

import io
import re
import time
from datetime import UTC, datetime

from .models import PaymentLink, Store

# Tag 99 is the validity window Bakong wallets enforce, in milliseconds. ABA's own
# dynamic QR sets 99.01 exactly 30 days after 99.00 (measured from a captured
# checkoutData.qr_string), so we mirror their convention rather than our own
# short-lived checkout TTL. Our internal Payment row keeps its own `expires_at`;
# a wallet that pays a QR after we marked it expired still flips expired -> paid
# because mark_paid() only refuses already-paid / failed rows.
KHQR_VALIDITY_WINDOW_MS = 30 * 24 * 60 * 60 * 1000


def crc16_ccitt_false(data: bytes, init: int = 0xFFFF) -> int:
    crc = init
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def tlv(tag: str, value: str) -> str:
    n = len(value)
    if n > 99:
        raise ValueError(f"Tag {tag} value len={n} > 99 (EMVCo 2-digit decimal max)")
    return f"{tag}{n:02d}{value}"


def parse_tlv(s: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    i = 0
    n = len(s)
    while i + 4 <= n:
        t = s[i:i+2]
        if not t.isdigit():
            break
        try:
            ln = int(s[i+2:i+4])
        except ValueError:
            break
        if ln < 0:
            break
        end = i + 4 + ln
        if end > n:
            break
        out.append((t, s[i+4:end]))
        i = end
    return out


_CURRENCY_ISO_NUMERIC = {
    "USD": "840",
    "KHR": "116",
}


def _format_amount(amount_cents: int) -> str:
    a = f"{(amount_cents / 100.0):.2f}"
    if a.endswith(".00"):
        a = a[:-3]
    return a


def build_khqr_payload(
    store: Store,
    link: PaymentLink,
    amount_cents: int,
    bill_number: str,
    expires_at: datetime,
) -> str:

    currency_iso = _CURRENCY_ISO_NUMERIC.get(link.currency, link.currency)
    try:
        int(currency_iso)
    except ValueError:
        currency_iso = "840"

    merchant_name = link.merchant_name or store.name

    now_dt = datetime.now(UTC)
    ttl_sec = max(1, int((expires_at.replace(tzinfo=expires_at.tzinfo or UTC) - now_dt).total_seconds()))

    now_epoch = int(time.time())

    amount_str = _format_amount(amount_cents)

    # --- build Tag 30 (merchant account template for the ABA Bakong switch) --
    # ABA's own QR carries three sub-tags here: 00 = the ABA switch GI,
    # 01 = the merchant's Bakong account id, 02 = the bank display label.
    # Verified against a captured checkoutData.qr_string from the PayWay page.
    t30 = (
        tlv("00", "abaakhppxxx@abaa")
        + tlv("01", link.merchant_account_id)
        + tlv("02", "ABA Bank")
    )

    # --- build Tag 62.50 (PAYWAY@ABA private template) --------------------
    # The inner block has a hard 99-char budget (EMVCo 2-digit length), so the
    # build order doubles as the priority order: the bill/ref/expiry must be
    # packed before the optional client id, or they are silently dropped.
    payway_client_id = getattr(link, "payway_client_id", None) or ""

    pw50_parts: list[tuple[str, str]] = []
    if payway_client_id:
        pw50_parts.append(("00", "PAYWAY@ABA"))
    if bill_number:
        pw50_parts.append(("02", bill_number))
        pw50_parts.append(("06", bill_number))  # external_ref search key
    pw50_parts.append(("05", str(now_epoch + ttl_sec)))  # expiry
    term_id = f"{now_epoch % 1000000000:09d}"
    if payway_client_id:
        pw50_parts.append(("01", payway_client_id))
    pw50_parts.append(("03", term_id))
    pw50_parts.append(("04", str(now_epoch)))

    pw50_inner = ""
    for k, v in pw50_parts:
        cand = pw50_inner + tlv(k, v)
        if len(cand) <= 99:
            pw50_inner = cand

    # --- assemble Tag 62 ---------------------------------------------------
    t62_value = ""
    def fits_62(part: str) -> bool:
        return len(t62_value + part) <= 99

    if pw50_inner and fits_62(tlv("50", pw50_inner)):
        t62_value += tlv("50", pw50_inner)
    if bill_number and fits_62(tlv("01", bill_number)):
        t62_value += tlv("01", bill_number)
    if fits_62(tlv("03", term_id)):
        t62_value += tlv("03", term_id)

    # --- final EMVCo body --------------------------------------------------
    body = (
        tlv("00", "01")
        + tlv("01", "12")
        + tlv("30", t30)
        + tlv("52", "8999")
        + tlv("53", currency_iso)
        + tlv("54", amount_str)
        + tlv("58", "KH")
        + tlv("59", merchant_name[:99])
        + tlv("60", "N/A")
    )
    if t62_value:
        body += tlv("62", t62_value)

    # --- Tag 99: the validity window wallets actually read ------------------
    # Milliseconds since epoch: issue time, then expiry. ABA's own dynamic QR
    # always carries this, and Bakong wallets treat a payload without it as
    # already expired regardless of what the 62.50.05 expiry says.
    issued_ms = now_epoch * 1000
    body += tlv(
        "99",
        tlv("00", str(issued_ms)) + tlv("01", str(issued_ms + KHQR_VALIDITY_WINDOW_MS)),
    )

    crc = crc16_ccitt_false((body + "6304").encode())
    body += tlv("63", f"{crc:04X}")
    return body


# --------------------------------------------------------------------------- #
# Rendering the payload as something a phone can point at                     #
# --------------------------------------------------------------------------- #

_SVG_ROOT_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)


def ensure_scalable(svg: str) -> str:
    """Give the root ``<svg>`` a viewBox derived from its width/height.

    segno emits only width/height. Without a viewBox an SVG has no internal
    coordinate system to scale into, so anything that resizes it crops instead
    of scaling — a scan box rendering it at 190px against a natural 558px. For a
    QR that means an unscannable, cut-off code.
    """
    match = _SVG_ROOT_RE.search(svg)
    if match is None or "viewBox" in match.group(0):
        return svg
    tag = match.group(0)
    width = re.search(r'width="([0-9.]+)"', tag)
    height = re.search(r'height="([0-9.]+)"', tag)
    if not (width and height):
        return svg
    patched = f'{tag[:-1]} viewBox="0 0 {width.group(1)} {height.group(1)}">'
    return svg[: match.start()] + patched + svg[match.end() :]


def render_qr_svg(payload: str, *, scale: int = 8, ecc: str = "h") -> str:
    """Encode a KHQR payload as a scannable SVG.

    ECC level H by default: the KHQR spec recommends it so a centre medallion
    (bank logo) can sit on top without breaking the decode.
    """
    import segno

    qr = segno.make(payload, error=ecc, micro=False, boost_error=False)
    buf = io.BytesIO()
    qr.save(
        buf,
        kind="svg",
        scale=scale,
        border=4,  # quiet zone: 4 modules is the spec minimum for reliable scans
        dark="#000000",
        light="#ffffff",
        xmldecl=False,
        nl=False,
    )
    return ensure_scalable(buf.getvalue().decode("utf-8"))
