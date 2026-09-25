"""ABA PayWay SSR page parser — extracts fixed merchant fields from a bare
ABA PayWay link (like https://link.payway.com.kh/ABAPAYpe518710Y) without
requiring PayWay API credentials or a logged-in session.

How ABA PayWay link pages work (REVERSE-ENGINEERED):
  - link.payway.com.kh/<slug> is a Nuxt 3 SSR page rendered server-side.
  - The page <body> ships a <script> with window.__NUXT__ whose payload is a
    JavaScript object literal (not strict JSON — it uses unquoted keys, NaN,
    Infinity, undefined, and newlines inside strings).
  - Inside window.__NUXT__ we care about two things:
      1. aba_data      — encrypted blob (opaque to us; the client decrypts it)
      2. transaction_summary — PLAINTEXT { merchant, order, ... } fields that
         identify the merchant name.
  - After user enters an amount and clicks Continue, the SPA populates
    checkoutData.qr_string with a real KHQR (EMVCo TLV), checkoutData.client_id
    with the PayWay client id (e.g. 2364634-518710-26248177), and
    checkoutData.* with Bakong account id, rules (min/max amount, currency,
    list of allowed banks etc).
  - So we actually need to run the client-side JS AND do the amount+Continue
    step to get the KHQR. We emulate this by:
      a. SSR fetch → extract __NUXT__ for merchant summary.
      b. ALSO ship a script that extracts the real checkoutData.qr_string and
         parses Tag 30.01 (bakong_id) out of it for the same slug/amount.
  - Because a full headless browser isn't always available, we also have a
    "known fields cache" mode — callers (the /api/v1/khqr/from-link API) that
    know the bakong_id/payway_client_id from DB can still use them as
    fallbacks; this parser is for bootstrapping that DB.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import string
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from ..config import get_settings

PAYWAY_BASE = "https://link.payway.com.kh"

# A browser-shaped request. ABA's edge answers 403 Forbidden to some client
# fingerprints while serving a byte-identical request from a browser, so this header
# set is part of *reaching the page at all* rather than politeness. It mirrors what
# the PayWay SPA itself sends; `Accept-Encoding` is left to httpx, which handles
# gzip/br and adds it per request.
BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9,km;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Chromium";v="128", "Not(A:Brand";v="24", "Google Chrome";v="128"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def _client(timeout: float) -> httpx.AsyncClient:
    """An ABA-facing HTTP client: browser headers are passed per call, egress here.

    Proxying is deliberately narrow. ABA refuses some datacenter ranges outright, so a
    deployment that is otherwise healthy can be unable to mint a QR or verify a link.
    Pointing *only* these requests at an allowed egress is the fix, and keeping it on
    this one helper stops it silently becoming a platform-wide proxy. See
    ``Settings.payway_proxy_url``.
    """
    proxy = (get_settings().payway_proxy_url or "").strip() or None
    kwargs: dict[str, Any] = {"timeout": timeout, "follow_redirects": True}
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)


def _refusal_message(step: str, status: int) -> str:
    """Explain an ABA HTTP refusal in terms of what the operator can do about it.

    A 403 on this fetch is the one failure this module cannot fix by itself: ABA is
    refusing *the platform host's* request while the same URL answers 200 from an
    ordinary connection. Naming that, and the escape hatch, is the difference between
    a five-minute fix and an afternoon spent re-reading the QR code path. It is also
    what stops the refusal arriving as a bare `Client error '403 Forbidden'` — an
    `httpx.HTTPStatusError` used to escape this module unhandled, so the merchant saw
    a 500 and the operator got paged about a traceback instead of a configuration gap.
    """
    if status in (401, 403, 406, 429):
        return (
            f"{step}_http_{status}: ABA refused this request from the platform's own "
            "network, so no QR can be issued against it. Set PAYWAY_PROXY_URL to route "
            "ABA requests through an allowed address, or ask ABA to allowlist this "
            "host's IP."
        )
    return f"{step}_http_{status}: ABA answered {status} for the link page."


# --------------------------------------------------------------------------- #
# ABA hosted checkout — the API the PayWay link page itself calls.            #
#                                                                             #
# REVERSE-ENGINEERED 2026-09-15 by watching the page's own XHRs and reading    #
# its bundle (_nuxt/62c7803.js holds the endpoint map, _nuxt/2f784c0.js the    #
# 3s poll loop). This is the ONLY source that can both mint a payable QR and   #
# report whether it was paid, and it needs no credentials:                    #
#                                                                             #
#   list-payment-options  hash = sha512(request_time + aba_data + additional)  #
#   check-payment-status  hash = sha512(client_id + device_id + request_time)  #
#                                                                             #
# Both hashes are plain SHA-512 over values we already hold — there is no      #
# secret key — which is why this is reproducible from a server with no        #
# browser session and no ABA merchant contract.                               #
# --------------------------------------------------------------------------- #
HOSTED_API_BASE = "https://pwapp.ababank.com/api"
HOSTED_LIST_PAYMENT_OPTIONS = "/pw-app/v1/payment/gateway/list-payment-options"
HOSTED_CHECK_PAYMENT_STATUS = "/pw-app/v1/payment-link/check-payment-status"

# The page's own poll cadence (setInterval(..., 3e3) in _nuxt/2f784c0.js).
HOSTED_POLL_INTERVAL_SECONDS = 3.0

# data.action values the page's UI switches on. Only APPROVED means paid.
HOSTED_ACTION_REQUEST_QR = "request_qr"
HOSTED_ACTION_SCANNED = "scanned"
HOSTED_ACTION_RQPAY = "rqpay"
HOSTED_ACTION_APPROVED = "approved"
HOSTED_ACTION_PROCESSING = "processing-payment"


class PayWayHostedError(RuntimeError):
    """ABA's hosted checkout refused the request or returned an unusable body."""


@dataclass
class HostedCheckout:
    """A checkout session ABA created, carrying ABA's own KHQR.

    Letting ABA host the QR is what makes it payable *and* trackable: the
    payload contains a PayWay token block we cannot reproduce offline, and
    ``client_id`` is the key the status endpoint answers on.
    """

    qr_string: str
    client_id: str
    token: str
    request_time: str
    tran_id: str | None = None
    expires_in_seconds: int | None = None
    download_qr_url: str | None = None
    status_message: str | None = None
    raw: dict[str, Any] | None = None

    @property
    def qr_md5(self) -> str:
        return hashlib.md5(self.qr_string.encode()).hexdigest()


@dataclass
class HostedStatus:
    action: str
    paid: bool
    receipt_url: str | None
    tran_id: str | None
    raw: dict[str, Any]


def _sha512_hex(value: str) -> str:
    return hashlib.sha512(value.encode()).hexdigest()


def _unescape_nuxt(raw: str) -> str:
    """Undo Nuxt's SSR escaping.

    The payload is inlined into HTML, so ``/`` is written as ``\\u002F``.
    JSON-decoding the literal reverses exactly that and nothing else.
    """
    try:
        return json.loads('"' + raw + '"')
    except json.JSONDecodeError:
        return raw


def extract_link_state(html: str) -> tuple[str | None, str | None]:
    """Return ``(aba_data, request_time)`` from a PayWay link page payload.

    Note the key is written as an assignment (``aba_data="…"``) in the minified
    payload, not a JSON property, so both separators are accepted.
    """
    m_aba = re.search(r'aba_data\s*[=:]\s*"((?:[^"\\]|\\.)*)"', html)
    m_time = re.search(r'request_time\s*[=:]\s*"?(\d{10,20})"?', html)
    return (
        _unescape_nuxt(m_aba.group(1)) if m_aba else None,
        m_time.group(1) if m_time else None,
    )


async def create_hosted_checkout(
    slug_or_url: str,
    amount: str,
    *,
    timeout: float = 25.0,
) -> HostedCheckout:
    """Mint a real ABA-issued KHQR for this link and amount.

    Mirrors the page's ``handleListPaymentOption`` action. ``amount`` is the
    string ABA expects in ``additional_fields`` (e.g. ``"1.00"``) — it is the
    amount the *page* would have submitted.
    """
    try:
        html = await fetch_link_html(
            slug_or_url
            if slug_or_url.startswith("http")
            else f"{PAYWAY_BASE}/{slug_or_url}",
            timeout=timeout,
        )
    except httpx.HTTPStatusError as exc:
        # Converted here rather than inside `fetch_link_html`, because `verify_link`
        # needs the raw status to tell "ABA has no such link" (404) from "ABA refused
        # us" (403). Callers of *this* function only ever want a mintable QR, and an
        # unwrapped HTTPStatusError used to escape all the way to the API's exception
        # handler as a 500.
        raise PayWayHostedError(
            _refusal_message("link_page", exc.response.status_code)
        ) from exc
    except httpx.HTTPError as exc:
        raise PayWayHostedError(f"link_page_unreachable: {exc}") from exc
    aba_data, request_time = extract_link_state(html)
    if not aba_data or not request_time:
        raise PayWayHostedError(
            "link_page_missing_aba_data: the PayWay page payload had no aba_data/request_time. "
            "The page markup may have changed."
        )

    additional_fields = json.dumps({"amount": amount}, separators=(",", ":"))
    body = {
        "additional_fields": additional_fields,
        "request_time": request_time,
        "aba_data": aba_data,
        "hash": _sha512_hex(request_time + aba_data + additional_fields),
    }
    async with _client(timeout) as client:
        res = await client.post(
            HOSTED_API_BASE + HOSTED_LIST_PAYMENT_OPTIONS,
            json=body,
            # The page's own XHR sends these; ABA's edge treats a bare POST to this
            # endpoint as not-from-the-page.
            headers={
                "language": "en",
                "Origin": PAYWAY_BASE,
                "Referer": f"{PAYWAY_BASE}/",
            },
        )
    payload = _hosted_json(res, "list_payment_options")
    qr_string = payload.get("qr_string") or ""
    client_id = payload.get("client_id") or ""
    token = payload.get("token") or ""
    if not qr_string or not client_id or not token:
        raise PayWayHostedError(
            "hosted_checkout_incomplete: ABA returned no qr_string/client_id/token. "
            f"step={payload.get('step')!r} status={payload.get('status')!r}"
        )
    expires_in = payload.get("expire_in_sec")
    return HostedCheckout(
        qr_string=qr_string,
        client_id=client_id,
        token=token,
        request_time=request_time,
        tran_id=(payload.get("status") or {}).get("tran_id"),
        expires_in_seconds=int(expires_in) if str(expires_in or "").isdigit() else None,
        download_qr_url=payload.get("download_qr"),
        status_message=(payload.get("status") or {}).get("message"),
        raw=payload,
    )


async def fetch_hosted_status(
    *,
    client_id: str,
    request_time: str,
    token: str,
    device_id: str | None = None,
    timeout: float = 15.0,
) -> HostedStatus:
    """Ask ABA whether the checkout identified by ``client_id`` has been paid.

    Mirrors the page's ``actRequestStatus`` action. ``device_id`` is a fresh
    random string on every call by design — the page itself generates a new one
    per poll (``$_getDeviceId(10)``), so it is not a device fingerprint.
    """
    device = device_id or "".join(
        secrets.choice(string.ascii_letters + string.digits) for _ in range(10)
    )
    body = {
        "device_id": device,
        "request_time": request_time,
        "client_id": client_id,
        "hash": _sha512_hex(client_id + device + request_time),
    }
    async with _client(timeout) as client:
        res = await client.post(
            HOSTED_API_BASE + HOSTED_CHECK_PAYMENT_STATUS,
            json=body,
            headers={
                "language": "en",
                "token": token,
                "Origin": PAYWAY_BASE,
                "Referer": f"{PAYWAY_BASE}/",
            },
        )
    payload = _hosted_json(res, "check_payment_status")
    data = payload.get("data") or {}
    action = str(data.get("action") or "")
    return HostedStatus(
        action=action,
        paid=action == HOSTED_ACTION_APPROVED,
        receipt_url=data.get("download_receipt") or data.get("download_receipt_url"),
        tran_id=((payload.get("status") or {}).get("tran_id")) or (data.get("message") or {}).get("tran_id"),
        raw=payload,
    )


def _hosted_json(res: httpx.Response, label: str) -> dict[str, Any]:
    if res.status_code >= 400:
        # A 4xx here is an edge refusal, not ABA's JSON protocol — its body is usually
        # an HTML error page, and parsing it would report a confusing `not_json`.
        raise PayWayHostedError(
            f"{label}_http_{res.status_code}: {res.text[:200]}"
        )
    try:
        payload = res.json()
    except ValueError as exc:
        raise PayWayHostedError(f"{label}_not_json: {res.text[:200]}") from exc
    if not isinstance(payload, dict):
        raise PayWayHostedError(f"{label}_unexpected_shape: {type(payload).__name__}")
    status = payload.get("status")
    if isinstance(status, dict) and str(status.get("code")) not in ("00", "0", ""):
        raise PayWayHostedError(
            f"{label}_rejected: code={status.get('code')} message={status.get('message')!r}"
        )
    return payload



def _extract_slug(slug_or_url: str) -> str:
    s = (slug_or_url or "").strip()
    if "://" in s:
        # URL form: https://link.payway.com.kh/ABAPAYpe518710Y?x=y
        try:
            from urllib.parse import urlparse

            parsed = urlparse(s)
            path = parsed.path.rstrip("/")
            if path:
                return path.split("/")[-1]
        except Exception:
            pass
    if s.startswith(PAYWAY_BASE):
        tail = s[len(PAYWAY_BASE):].strip("/")
        if "?" in tail:
            tail = tail.split("?", 1)[0]
        if "#" in tail:
            tail = tail.split("#", 1)[0]
        return tail or s
    if "?" in s:
        s = s.split("?", 1)[0]
    if "#" in s:
        s = s.split("#", 1)[0]
    return s


@dataclass
class PayWayLinkInfo:
    slug: str
    url: str
    merchant_name: str | None
    merchant_bakong_id: str | None
    payway_client_id: str | None
    currency: str | None
    min_amount_usd: float | None
    max_amount_usd: float | None
    raw_nuxt_snippet: str | None = None

    @property
    def is_ready(self) -> bool:
        return bool(self.merchant_bakong_id)


NUXT_RE = re.compile(
    r"window\.__NUXT__\s*=\s*(\{.*?\})\s*;?\s*</script>",
    re.DOTALL,
)

NUXT_IIFE_RE = re.compile(
    r"window\.__NUXT__\s*=\s*\(function\s*\(([^)]*)\)\s*\{(.*?)\}\)\s*\(([^)]*)\)\s*;?\s*</script>",
    re.DOTALL,
)

ABA_DATA_RE = re.compile(r'aba_data\s*:\s*"([^"]+)"')
TRANSACTION_SUMMARY_RE = re.compile(
    r'transaction_summary\s*:\s*(\{[^}]*\})',
    re.DOTALL,
)

REQUIRED_FIELDS_RE = re.compile(
    r'required_fields\s*:\s*(\{(?:[^{}]|\{[^{}]*\})*\})',
    re.DOTALL,
)

ORDER_DETAILS_RE = re.compile(
    r'order_details\s*:\s*(\{(?:[^{}]|\{[^{}]*\})*\})',
    re.DOTALL,
)

BAKONG_ID_HINT_RE = re.compile(r'\b12[0-9]{13,14}\b')

UNQUOTED_KEY_RE = re.compile(r'([{,])\s*([a-zA-Z_$][a-zA-Z0-9_$]*)\s*:')
SINGLE_Q_STRING_RE = re.compile(r"'([^'\\]*(?:\\.[^'\\]*)*)'")
JS_UNDEF_RE = re.compile(r'\bundefined\b')
JS_NAN_RE = re.compile(r'\bNaN\b')
JS_INF_RE = re.compile(r'\bInfinity\b')


def _js_obj_to_json_like(src: str) -> str:
    """Convert a loose JS object literal ({foo: 'bar'}) into something we can
    pass to json.loads. Best-effort; does not handle arbitrary JS and is only
    designed for the subset Nuxt SSR emits for __NUXT__."""
    s = src
    # Replace undefined, NaN, Infinity with valid JSON placeholders
    s = JS_UNDEF_RE.sub('null', s)
    s = JS_NAN_RE.sub('null', s)
    s = JS_INF_RE.sub('null', s)
    # Convert single-quoted strings to double-quoted
    def _sq_to_dq(m: re.Match[str]) -> str:
        inner = m.group(1).replace('\\"', '"').replace("\\'", "'")
        return '"' + inner.replace('"', '\\"') + '"'
    s = SINGLE_Q_STRING_RE.sub(_sq_to_dq, s)
    # Unquoted keys → quoted keys
    s = UNQUOTED_KEY_RE.sub(r'\1"\2":', s)
    return s


def _extract_loose_dict(body: str, inner_re: re.Pattern[str]) -> dict[str, Any] | None:
    m = inner_re.search(body)
    if not m:
        return None
    try:
        js_like = _js_obj_to_json_like(m.group(1))
        return json.loads(js_like)
    except Exception:
        return None


def _split_top_level_commas(s: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    dq = False
    sq = False
    depth_paren = 0
    depth_brace = 0
    depth_bracket = 0
    i = 0
    while i < len(s):
        ch = s[i]
        emitted = False
        if not sq and ch == '"' and (i == 0 or s[i - 1] != "\\"):
            dq = not dq
            buf.append(ch)
            emitted = True
        elif not dq and ch == "'" and (i == 0 or s[i - 1] != "\\"):
            sq = not sq
            buf.append(ch)
            emitted = True
        elif not sq and not dq:
            if ch == "(":
                depth_paren += 1
            elif ch == ")":
                depth_paren -= 1
            elif ch == "{":
                depth_brace += 1
            elif ch == "}":
                depth_brace -= 1
            elif ch == "[":
                depth_bracket += 1
            elif ch == "]":
                depth_bracket -= 1
            elif (
                ch == ","
                and depth_paren == 0
                and depth_brace == 0
                and depth_bracket == 0
            ):
                out.append("".join(buf).strip())
                buf = []
                i += 1
                continue
        if not emitted:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _parse_js_arg(arg: str) -> Any:
    if arg == "null" or arg == "undefined":
        return None
    if arg == "true":
        return True
    if arg == "false":
        return False
    if not arg:
        return None
    if (arg[0] == '"' and arg[-1] == '"') or (arg[0] == "'" and arg[-1] == "'"):
        inner = arg[1:-1]
        inner = inner.replace('\\"', '"').replace("\\'", "'")
        inner = inner.encode("utf-8").decode("unicode_escape")
        return inner
    if arg[0] == "{":
        try:
            return json.loads(_js_obj_to_json_like(arg))
        except Exception:
            return None
    if arg[0] == "[":
        try:
            return json.loads(_js_obj_to_json_like(arg))
        except Exception:
            return None
    if arg[0] in "-.0123456789":
        try:
            if "." in arg:
                return float(arg)
            return int(arg)
        except Exception:
            return None
    return None


def _parse_iife(html: str) -> dict[str, Any] | None:
    """If window.__NUXT__ is an IIFE (payway.com.kh style), build the resolved
    variable substitution map and extract transaction_summary + aba_data with
    identifier references replaced by actual argument values. Returns None if
    the payload is the plain object-literal form (old Nuxt style)."""
    # Quick heuristic: does window.__NUXT__ begin with `(function`?
    start = html.find("window.__NUXT__")
    if start < 0:
        return None
    prefix = html[start : start + 40]
    if "(function" not in prefix:
        return None

    # IIFE regex can fail on huge payloads, so do manual extraction:
    #   window.__NUXT__=(function(params){BODY})(ARGS);</script>
    after_eq = html.find("=", start)
    if after_eq < 0:
        return None
    func_open = html.find("(function", after_eq)
    if func_open < 0:
        return None
    params_open = html.find("(", func_open)
    params_close = html.find(")", params_open)
    if params_close < 0:
        return None
    params_raw = html[params_open + 1 : params_close]
    body_open = html.find("{", params_close)
    if body_open < 0:
        return None
    # Match balanced braces to find body close
    depth = 0
    i = body_open
    while i < len(html):
        ch = html[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                body_close = i
                break
        i += 1
    else:
        return None
    body = html[body_open + 1 : body_close]
    # After body_close should be `})(ARGS);`
    args_open = html.find("(", body_close)
    args_close = html.find(")", args_open) if args_open > 0 else -1
    if args_open < 0 or args_close < 0:
        return None
    args_raw = html[args_open + 1 : args_close]

    param_names = [p.strip() for p in params_raw.split(",") if p.strip()]
    arg_vals = _split_top_level_commas(args_raw)
    var_map: dict[str, Any] = {}
    for name, raw in zip(param_names, arg_vals):
        var_map[name] = _parse_js_arg(raw)

    # Now: find the interesting pieces inside body and substitute identifiers
    def _subst_in_text(text: str) -> str:
        out: list[str] = []
        i = 0
        in_dq = False
        in_sq = False
        while i < len(text):
            ch = text[i]
            # String detection
            if not in_sq and ch == '"' and (i == 0 or text[i - 1] != "\\"):
                in_dq = not in_dq
                out.append(ch)
                i += 1
                continue
            if not in_dq and ch == "'" and (i == 0 or text[i - 1] != "\\"):
                in_sq = not in_sq
                out.append(ch)
                i += 1
                continue
            if in_dq or in_sq:
                out.append(ch)
                i += 1
                continue
            # Identifier detection
            if ch.isalpha() or ch == "_" or ch == "$":
                j = i
                while j < len(text) and (text[j].isalnum() or text[j] == "_" or text[j] == "$"):
                    j += 1
                ident = text[i:j]
                # Only substitute if identifier is followed by , } ) : space or end
                followed_by = text[j] if j < len(text) else ""
                preceded_by = text[i - 1] if i > 0 else ""
                is_bare = (
                    preceded_by in ("", " ", "\t", "\n", "\r", "{", ",", "(", ":")
                    and followed_by in ("", " ", "\t", "\n", "\r", "}", ")", ",", ":", ";", "[", ".")
                )
                if is_bare and ident in var_map:
                    val = var_map[ident]
                    out.append(json.dumps(val, ensure_ascii=False))
                else:
                    out.append(ident)
                i = j
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    def _extract_braced_block(
        body_snippet: str, start_idx_open_brace: int
    ) -> str | None:
        depth = 0
        i = start_idx_open_brace
        while i < len(body_snippet):
            ch = body_snippet[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return body_snippet[start_idx_open_brace : i + 1]
            i += 1
        return None

    result: dict[str, Any] = {"_iife_var_map_keys": list(var_map.keys())}
    aba = ABA_DATA_RE.search(body)
    if aba:
        result["aba_data"] = aba.group(1)
    bakong_match = BAKONG_ID_HINT_RE.search(body)
    if bakong_match:
        result["bakong_id_hint"] = bakong_match.group(0)

    # Extract the assignments p.transaction_summary = {...}, p.required_fields = {...}
    # (NOT the return-object literal form checkout.transaction_summary:{}, which is empty)
    for key, re_pat in [
        (
            "transaction_summary",
            re.compile(r"\bp\.transaction_summary\s*=\s*(\{)"),
        ),
        (
            "order_details",
            re.compile(r"\bp\.transaction_summary[^{]*\{(?:[^{}]|\{[^{}]*\})*order_details\s*:\s*(\{)"),
        ),
        (
            "required_fields",
            re.compile(r"\bp\.required_fields\s*=\s*(\{)"),
        ),
    ]:
        m = re_pat.search(body)
        if not m:
            continue
        brace_open = m.end(1) - 1
        block = _extract_braced_block(body, brace_open)
        if block is None:
            continue
        # Substitute identifiers in just the braced block (no key prefix)
        subbed_block = _subst_in_text(block)
        # Strip unresolved single-letter identifiers (they're typically "", null,
        # or placeholders). Replace ':<bare_ident>' with ':null' so JSON parses.
        def _strip_bare_idents(text: str) -> str:
            out: list[str] = []
            i = 0
            in_dq = False
            in_sq = False
            prev_ch = ""
            while i < len(text):
                ch = text[i]
                if not in_sq and ch == '"' and (i == 0 or text[i - 1] != "\\"):
                    in_dq = not in_dq
                    out.append(ch)
                    i += 1
                    prev_ch = ch
                    continue
                if not in_dq and ch == "'" and (i == 0 or text[i - 1] != "\\"):
                    in_sq = not in_sq
                    out.append(ch)
                    i += 1
                    prev_ch = ch
                    continue
                if in_dq or in_sq:
                    out.append(ch)
                    i += 1
                    prev_ch = ch
                    continue
                if (prev_ch == ":" or prev_ch == "," or prev_ch == "[" or prev_ch == "{" or prev_ch == "") and (
                    ch.isalpha() or ch == "_" or ch == "$"
                ):
                    j = i
                    while j < len(text) and (text[j].isalnum() or text[j] == "_" or text[j] == "$"):
                        j += 1
                    ident = text[i:j]
                    next_ch = text[j] if j < len(text) else ""
                    if next_ch in ("", ",", "}", "]", ")", ":") and ident in var_map:
                        val = var_map.get(ident)
                        out.append(json.dumps(val, ensure_ascii=False, default=str))
                        prev_ch = ""
                        if j < len(text):
                            prev_ch = next_ch
                        i = j
                        continue
                    elif next_ch in ("", ",", "}", "]", ")"):
                        out.append("null")
                        prev_ch = ""
                        if j < len(text):
                            prev_ch = next_ch
                        i = j
                        continue
                out.append(ch)
                prev_ch = ch
                i += 1
            return "".join(out)

        cleaned_block = _strip_bare_idents(subbed_block)
        parsed = _extract_loose_dict(
            f"X:{cleaned_block}",
            re.compile(r"X:\s*(\{.*\})", re.DOTALL),
        )
        if parsed:
            result[key] = parsed

    # order_details is also a nested child of transaction_summary — if we
    # didn't match it standalone, pull it from tx if present.
    if "transaction_summary" in result and "order_details" not in result:
        txn = result["transaction_summary"]
        if isinstance(txn, dict) and isinstance(txn.get("order_details"), dict):
            result["order_details"] = txn["order_details"]

    return result



async def fetch_link_html(slug_or_url: str, *, timeout: float = 8.0) -> str:
    if slug_or_url.startswith("http://") or slug_or_url.startswith("https://"):
        url = slug_or_url
    else:
        url = f"{PAYWAY_BASE}/{slug_or_url.lstrip('/')}"
    async with _client(timeout) as client:
        resp = await client.get(url, headers=BROWSER_HEADERS)
        resp.raise_for_status()
        return resp.text


def parse_nuxt_fields(html: str) -> dict[str, Any]:
    """Extract window.__NUXT__ data from SSR HTML. Returns a best-effort dict.

    PayWay link pages ship Nuxt SSR payloads in TWO formats:
      (a) IIFE form (newer): `window.__NUXT__=(function(params){...})(args);`
          This form uses short identifiers (a, b, c, r, s, t, ...) inside the
          function body that resolve to actual values via the IIFE call args.
          The IIFE parser extracts these by substituting identifiers with
          their argument values.
      (b) Plain object-literal form (older Nuxt): `window.__NUXT__={...}`
          This form is handled by the tolerant JS-obj → JSON converter below.
    """
    # Try IIFE parser first — this is what payway.com.kh actually uses.
    iife = _parse_iife(html)
    if iife:
        return iife

    result: dict[str, Any] = {}
    m = NUXT_RE.search(html)
    if not m:
        return result
    snippet = m.group(1)
    result["_snippet_len"] = len(snippet)
    result["aba_data"] = ABA_DATA_RE.search(snippet).group(1) if ABA_DATA_RE.search(snippet) else None
    txn = _extract_loose_dict(snippet, TRANSACTION_SUMMARY_RE)
    if txn:
        result["transaction_summary"] = txn
    od = _extract_loose_dict(snippet, ORDER_DETAILS_RE)
    if od:
        result["order_details"] = od
    rf = _extract_loose_dict(snippet, REQUIRED_FIELDS_RE)
    if rf:
        result["required_fields"] = rf
    return result


def _first_non_none(*vals: Any) -> Any:
    for v in vals:
        if v is not None:
            return v
    return None


def extract_merchant_fields(
    nuxt: dict[str, Any],
    *,
    bakong_id_hint: str | None = None,
    client_id_hint: str | None = None,
) -> PayWayLinkInfo:
    """Turn raw window.__NUXT__ data into a PayWayLinkInfo.

    Because SSR only ships the encrypted aba_data (not the checkoutData that
    only appears AFTER the SPA runs amount+Continue), we only guarantee
    merchant_name here — bakong_id and payway_client_id typically come from
    the real checkoutData.qr_string / checkoutData.client_id (which is the
    KHQR we'd get by clicking Continue in a browser, or by knowing them
    from DB).
    """
    txn = nuxt.get("transaction_summary") or {}
    merchant_sub = txn.get("merchant") if isinstance(txn.get("merchant"), dict) else {}
    name = _first_non_none(
        merchant_sub.get("company") if isinstance(merchant_sub, dict) else None,
        merchant_sub.get("outlet_name") if isinstance(merchant_sub, dict) else None,
        txn.get("merchant"),
        txn.get("merchant_name"),
        txn.get("company"),
    )
    if isinstance(name, dict):
        name = name.get("company") or name.get("outlet_name")
    if isinstance(name, str) and not name:
        name = None

    # Read required_fields rules FIRST — these are the actual user-facing
    # amount validation (PayWay shows a "min_value / max_value" rule set that
    # matches what the browser enforces on input, unlike
    # order_details.max_amount_per_trxn which is typically the global ceiling.)
    min_amt: float | None = None
    max_amt: float | None = None
    req_fields = nuxt.get("required_fields") if isinstance(nuxt.get("required_fields"), dict) else {}
    amt_rules: list[Any] = []
    amount_field = req_fields.get("amount") if isinstance(req_fields.get("amount"), dict) else {}
    rules_val = amount_field.get("rules") if isinstance(amount_field, dict) else None
    if isinstance(rules_val, list):
        amt_rules = rules_val
    for rule in amt_rules:
        if not isinstance(rule, dict):
            continue
        if min_amt is None:
            v = rule.get("min_value")
            if isinstance(v, (int, float)):
                min_amt = float(v)
            elif isinstance(v, str):
                try:
                    min_amt = float(v)
                except ValueError:
                    pass
        if max_amt is None:
            v = rule.get("max_value")
            if isinstance(v, (int, float)):
                max_amt = float(v)
            elif isinstance(v, str):
                try:
                    max_amt = float(v)
                except ValueError:
                    pass
        if min_amt is not None and max_amt is not None:
            break

    order = nuxt.get("order_details") if isinstance(nuxt.get("order_details"), dict) else {}
    currency: str | None = order.get("currency") or None
    if max_amt is None:
        for key in ("max_amount_per_trxn", "max_total", "max_value"):
            v = order.get(key)
            if isinstance(v, (int, float)) and v > 0:
                max_amt = float(v)
                break
            if isinstance(v, str):
                try:
                    max_amt = float(v)
                    break
                except ValueError:
                    continue

    bakong_id = bakong_id_hint
    if not bakong_id and isinstance(nuxt.get("bakong_id_hint"), str):
        bakong_id = nuxt["bakong_id_hint"]

    return PayWayLinkInfo(
        slug="",
        url="",
        merchant_name=name,
        merchant_bakong_id=bakong_id,
        payway_client_id=client_id_hint,
        currency=currency,
        min_amount_usd=min_amt,
        max_amount_usd=max_amt,
        raw_nuxt_snippet=(
            json.dumps({k: v for k, v in nuxt.items() if k != "_snippet_len"}, default=str)[:2000]
        ),
    )


@dataclass
class PayWayLinkCheck:
    """What PayWay said about a link, before it becomes a store's destination.

    Three outcomes, and the difference matters because only one of them is a
    reason to refuse the merchant's input:

      - ``ok``            — the page resolved and named its merchant. The link is
                            real. Only this outcome marks a link verified.
      - ``not_found``     — PayWay itself answered 4xx for the slug. The link does
                            not exist, which is the typo we want to catch before it
                            becomes an "active" store that fails at the first sale.
      - ``inconclusive``  — we could not reach PayWay, or could not read the page.
                            Not the merchant's fault and not evidence of a typo, so
                            it must never reject the input.
    """

    outcome: Literal["ok", "not_found", "inconclusive"]
    slug: str
    merchant_name: str | None = None
    detail: str | None = None

    @property
    def verified(self) -> bool:
        return self.outcome == "ok"


async def verify_link(raw_link: str, *, timeout: float = 6.0) -> PayWayLinkCheck:
    """Ask PayWay whether a share link exists, and read its merchant name.

    Why this is a network call and not a regex: the failure it exists to prevent is
    a *typo in the slug*, and a mistyped slug is still a perfectly well-formed
    string. `ABAPAYpe518710Y` and `ABAPAYpe518710X` are indistinguishable by shape;
    only PayWay can say that one of them is not a link. Before this, the platform
    stored whatever it was given, hardcoded ``verification=verified``, and left the
    merchant to discover the mistake as a 502 on their first customer.

    The cost is an outbound fetch on the store-create/link-set path, which is why
    the ``inconclusive`` outcome exists: if PayWay is slow or down we record the
    link as *unverified* rather than refusing it. Turning an ABA outage into a
    blocked signup would be a worse bug than the one being fixed.
    """
    slug = _extract_slug(raw_link)
    if not slug:
        return PayWayLinkCheck("not_found", "", None, "no link reference found")

    try:
        html = await fetch_link_html(raw_link, timeout=timeout)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (404, 410):
            # PayWay's own verdict on the slug: this link is not there.
            return PayWayLinkCheck("not_found", slug, None, f"payway answered {status}")
        # Every other status is *our* problem, not the merchant's typo. A 403 is ABA
        # refusing this host's egress (observed from datacenter ranges) and a 429 is
        # rate limiting — neither says anything about whether the link exists, and
        # treating them as `not_found` told a merchant their correct link was wrong.
        return PayWayLinkCheck("inconclusive", slug, None, f"payway answered {status}")
    except Exception as exc:  # noqa: BLE001 - DNS, TLS, timeout, a parser crash
        return PayWayLinkCheck("inconclusive", slug, None, str(exc)[:200] or "unreachable")

    info = extract_merchant_fields(parse_nuxt_fields(html), client_id_hint=slug)
    if not info.merchant_name:
        # The page came back but carried no merchant. That is a limit of reading the
        # SSR payload, not proof the link is bogus, so it is not a rejection.
        return PayWayLinkCheck(
            "inconclusive", slug, None, "link page carried no merchant name"
        )
    return PayWayLinkCheck("ok", slug, info.merchant_name)


def md5hex(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()

# --------------------------------------------------------------------------- #
# PayWay payment status polling                                               #
#                                                                             #
# How it works (reverse-engineered from the ABAPAYpe518710Y flow after the   #
# user reported "paid status update on the page"):                            #
#   1. After a user pays, the ABA PayWay SPA transitions the page to a       #
#      "Payment Successful" state. We cannot drive the SPA directly, but     #
#      TWO independent signals are available server-side without browser     #
#      automation:                                                            #
#         (a) The SSR payload's `transaction_summary.order_details.amount`   #
#             is initially `0.00` on the unpaid page but flips to the paid   #
#             amount when ABA replays the successful transaction back into   #
#             SSR (this is what the user observed as "paid status update").  #
#         (b) The `checkoutData` / `checkout_status` text inside the         #
#             rendered HTML contains literal tokens like "paid", "Payment    #
#             Successful", "Success", "Completed", or amounts that match     #
#             the expected bill/reference.                                    #
#   2. Callers provide the bill_number (= instruction_ref = Tag 62.01) and   #
#      optionally the amount they expect. We re-fetch the SSR page and look  #
#      for these signals in the HTML + parsed NUXT payload.                   #
# --------------------------------------------------------------------------- #

ABA_PAID_TOKENS: tuple[str, ...] = (
    "payment successful",
    "payment success",
    "paid successfully",
    "has been paid",
    "transaction successful",
    "transaction completed",
    "completed successfully",
    "your payment is successful",
    "payment successful",
    "status: paid",
    '"status":"paid"',
    '"status": "paid"',
    '"payment_status":"paid"',
    '"payment_status": "paid"',
    '"paid":true',
    '"is_paid":true',
    '"order_status":"success"',
    '"order_status": "success"',
    '"status":"success"',
    '"status": "success"',
    'success-payment',
    'page-success',
    'ico-success',
)

ABA_FAILED_TOKENS: tuple[str, ...] = (
    "payment failed",
    "payment cancelled",
    "payment was cancelled",
    "transaction failed",
    "transaction cancelled",
    '"status":"failed"',
    '"status": "failed"',
    '"payment_status":"failed"',
    '"order_status":"failed"',
    '"status":"cancelled"',
    '"status":"canceled"',
)


@dataclass
class PayWayPaymentStatus:
    slug: str
    status: Literal["PAID", "PENDING", "FAILED", "UNKNOWN"]
    signals: list[str]
    matched_amount: float | None
    matched_bill: str | None
    html_tail: str | None
    nuxt_snippet: str | None


async def fetch_payment_status(
    slug_or_url: str,
    *,
    bill_number: str | None = None,
    reference_id: str | None = None,
    expected_amount_usd: float | None = None,
    timeout: float = 10.0,
) -> PayWayPaymentStatus:
    """Best-effort server-side status check for an ABA PayWay payment page.

    This is the #1 priority status checkpoint for payments created from an
    ABA Payment Link (the user explicitly asked for this priority). It does
    NOT require any ABA / PayWay API credentials — it just re-fetches the
    public SSR page and looks for paid/failed indicators that the user
    themselves confirmed are present after paying.
    """
    slug = _extract_slug(slug_or_url)
    url = f"{PAYWAY_BASE}/{slug}"
    try:
        async with _client(timeout) as c:
            resp = await c.get(url, headers=BROWSER_HEADERS)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        return PayWayPaymentStatus(
            slug=slug,
            status="UNKNOWN",
            signals=[f"fetch_error:{exc}"],
            matched_amount=None,
            matched_bill=None,
            html_tail=None,
            nuxt_snippet=None,
        )

    html_lc = html.lower()
    signals: list[str] = []
    matched_amount: float | None = None
    matched_bill: str | None = None

    # (1) Scan HTML for literal success/failure tokens
    for tok in ABA_PAID_TOKENS:
        if tok.lower() in html_lc:
            signals.append(f"html_token:paid:{tok[:40]}")
            break
    for tok in ABA_FAILED_TOKENS:
        if tok.lower() in html_lc:
            signals.append(f"html_token:failed:{tok[:40]}")
            break

    # (2) Re-parse __NUXT__ and look for status / non-zero amount in order_details
    nuxt = parse_nuxt_fields(html)
    txn = nuxt.get("transaction_summary") or {}
    od = txn.get("order_details") if isinstance(txn.get("order_details"), dict) else nuxt.get("order_details") or {}
    if isinstance(od, dict):
        amt_raw = od.get("amount")
        try:
            if amt_raw is not None and str(amt_raw).strip() not in ("", "0", "0.00", "0.0"):
                amt = float(amt_raw)
                if amt > 0:
                    signals.append(f"nuxt_order_details.amount={amt}")
                    if expected_amount_usd is None or abs(amt - expected_amount_usd) < 0.005:
                        matched_amount = amt
                        signals.append(f"nuxt_amount_matches_expected:{amt}")
        except (TypeError, ValueError):
            pass
        for key in ("status", "payment_status", "order_status", "transaction_status"):
            val = od.get(key)
            if isinstance(val, str) and val:
                signals.append(f"nuxt_od.{key}={val}")
                vl = val.lower()
                if vl in ("paid", "success", "successful", "completed"):
                    signals.append(f"nuxt_od_status_paid:{vl}")
                elif vl in ("failed", "canceled", "cancelled", "error", "expired"):
                    signals.append(f"nuxt_od_status_failed:{vl}")

    # (3) Checkout block inside nuxt
    for top_key in ("checkout", "payment", "checkoutData", "checkout_data"):
        blk = nuxt.get(top_key)
        if not isinstance(blk, dict):
            continue
        for key in ("status", "paymentStatus", "payment_status", "orderStatus", "order_status"):
            val = blk.get(key)
            if isinstance(val, str) and val:
                signals.append(f"nuxt_{top_key}.{key}={val}")
                vl = val.lower()
                if vl in ("paid", "success", "successful", "completed"):
                    signals.append(f"nuxt_{top_key}_paid:{vl}")
                elif vl in ("failed", "canceled", "cancelled", "error", "expired"):
                    signals.append(f"nuxt_{top_key}_failed:{vl}")

    # (4) Bill/reference ID hint: if the page now renders the bill/reference as
    #     "Paid for bill_number XYZ" it's a soft signal.
    if bill_number:
        needle = bill_number.lower()
        if needle and needle in html_lc:
            matched_bill = bill_number
            signals.append("bill_present_in_html")
    if reference_id and not matched_bill:
        needle = reference_id.lower()
        if needle and needle in html_lc:
            matched_bill = reference_id
            signals.append("reference_present_in_html")

    paid_signals = sum(1 for s in signals if "paid" in s.lower() or "success" in s.lower() or "completed" in s.lower())
    failed_signals = sum(1 for s in signals if "failed" in s.lower() or "cancel" in s.lower() or "error" in s.lower())

    if matched_amount is not None and paid_signals:
        status: Literal["PAID", "PENDING", "FAILED", "UNKNOWN"] = "PAID"
    elif paid_signals and matched_bill and expected_amount_usd is None:
        status = "PAID"
    elif paid_signals >= 2:
        status = "PAID"
    elif failed_signals and paid_signals == 0:
        status = "FAILED"
    else:
        status = "PENDING"

    return PayWayPaymentStatus(
        slug=slug,
        status=status,
        signals=signals,
        matched_amount=matched_amount,
        matched_bill=matched_bill,
        html_tail=html[-200:] if len(html) > 200 else html,
        nuxt_snippet=(
            json.dumps({k: v for k, v in nuxt.items() if k != "_snippet_len"}, default=str)[:2000]
            if nuxt
            else None
        ),
    )
