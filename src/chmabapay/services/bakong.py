"""Bakong Open API client for transaction verification and account lookup.

Reverse-engineered from https://api-bakong.nbc.gov.kh/ - provides 5 search modes
for verifying transaction status on the Bakong payment network operated by the
National Bank of Cambodia (NBC).

Endpoints (all POST https://api-bakong.nbc.gov.kh/v1/…):
  - check_transaction_by_hash          (64-char SHA-256 full hash)
  - check_transaction_by_md5           (32-char MD5 of QR string)
  - check_transaction_by_short_hash    (truncated short hash)
  - check_transaction_by_instruction_ref (instruction / reference ID)
  - check_transaction_by_external_ref  (external merchant reference)
  - check_bakong_account               (validate a Bakong ID like user@bank)
  - renew_token                        (mint a short-lived JWT via reg'd email)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

import httpx

from ..config import get_settings

SearchType = Literal["hash", "md5", "short_hash", "instruction_ref", "external_ref"]

BAKONG_SUCCESS_CODE = 0
BAKONG_ERROR_UNAUTHORIZED = 6
BAKONG_ERROR_NOT_FOUND = 7


@dataclass
class BakongTransaction:
    hash: str | None = None
    short_hash: str | None = None
    md5: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    from_account_name: str | None = None
    to_account_name: str | None = None
    currency: str | None = None
    amount: float | None = None
    description: str | None = None
    created_date_ms: float | None = None
    acknowledged_date_ms: float | None = None
    instruction_ref: str | None = None
    external_ref: str | None = None
    status: str | None = None
    raw: dict | None = None

    @classmethod
    def from_raw(cls, data: dict) -> BakongTransaction:
        return cls(
            hash=data.get("hash"),
            short_hash=data.get("shortHash") or data.get("short_hash"),
            md5=data.get("md5"),
            from_account_id=data.get("fromAccountId") or data.get("from_account_id"),
            to_account_id=data.get("toAccountId") or data.get("to_account_id"),
            from_account_name=data.get("fromAccountName") or data.get("from_account_name"),
            to_account_name=data.get("toAccountName") or data.get("to_account_name"),
            currency=data.get("currency"),
            amount=data.get("amount"),
            description=data.get("description"),
            created_date_ms=data.get("createdDateMs") or data.get("created_date_ms"),
            acknowledged_date_ms=data.get("acknowledgedDateMs") or data.get("acknowledged_date_ms"),
            instruction_ref=data.get("instructionRef") or data.get("instruction_ref"),
            external_ref=data.get("externalRef") or data.get("external_ref"),
            status=data.get("status"),
            raw=data,
        )


class BakongApiError(Exception):
    def __init__(self, message: str, error_code: int | None = None, response_code: int | None = None):
        super().__init__(message)
        self.error_code = error_code
        self.response_code = response_code


class BakongApiClient:
    """Async HTTP client for the Bakong Open API.

    Token usage:
      - The token is short-lived (hours). Provide it directly via settings or
        pass a registered developer email to auto-mint on first use / 401.
      - Public unauthenticated search works on the NBC website but the API
        returns 401 for raw API calls; a token is therefore mandatory for
        programmatic use.
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        developer_email: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ):
        settings = get_settings()
        self.base_url = (base_url or settings.bakong_base_url).rstrip("/")
        self.token = token or settings.bakong_api_token
        self.developer_email = developer_email or settings.bakong_developer_email
        self.timeout = timeout or settings.bakong_timeout_seconds
        self.max_retries = max_retries or settings.bakong_max_retries
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> BakongApiClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if extra:
            h.update(extra)
        return h

    async def _post(self, path: str, body: dict) -> dict:
        client = self._get_client()
        url = f"{self.base_url}{path}"

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                res = await client.post(url, json=body, headers=self._headers())
                payload: dict = res.json()
                response_code = payload.get("responseCode")
                error_code = payload.get("errorCode")

                if response_code == BAKONG_SUCCESS_CODE:
                    return payload

                if error_code == BAKONG_ERROR_UNAUTHORIZED and attempt == 0 and self.developer_email:
                    await self.renew_token(self.developer_email)
                    continue

                msg = payload.get("responseMessage") or f"Bakong API error on {path}"
                raise BakongApiError(msg, error_code=error_code, response_code=response_code)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise
        if last_exc:
            raise last_exc
        raise BakongApiError("Unknown error")

    # ------------------------------------------------------------------ #
    # Token
    # ------------------------------------------------------------------ #
    async def renew_token(self, email: str | None = None) -> str:
        use_email = email or self.developer_email
        if not use_email:
            raise BakongApiError("developer_email required to renew Bakong API token")
        payload = await self._post("/v1/renew_token", {"email": use_email})
        data = payload.get("data") or {}
        token = data.get("token")
        if token:
            self.token = token
        return token

    # ------------------------------------------------------------------ #
    # Transaction lookups
    # ------------------------------------------------------------------ #
    async def check_by_hash(
        self,
        hash_value: str,
        *,
        amount: float | None = None,
        currency: str | None = None,
    ) -> BakongTransaction | None:
        body: dict = {"hash": hash_value}
        if amount is not None:
            body["amount"] = float(amount)
        if currency:
            body["currency"] = currency
        payload = await self._post("/v1/check_transaction_by_hash", body)
        data = payload.get("data")
        return BakongTransaction.from_raw(data) if data else None

    async def check_by_md5(
        self,
        md5: str,
        *,
        amount: float | None = None,
        currency: str | None = None,
    ) -> BakongTransaction | None:
        body: dict = {"md5": md5}
        if amount is not None:
            body["amount"] = float(amount)
        if currency:
            body["currency"] = currency
        payload = await self._post("/v1/check_transaction_by_md5", body)
        data = payload.get("data")
        return BakongTransaction.from_raw(data) if data else None

    async def check_by_short_hash(
        self,
        short_hash: str,
        *,
        amount: float | None = None,
        currency: str | None = None,
    ) -> BakongTransaction | None:
        body: dict = {"short_hash": short_hash, "shortHash": short_hash}
        if amount is not None:
            body["amount"] = float(amount)
        if currency:
            body["currency"] = currency
        payload = await self._post("/v1/check_transaction_by_short_hash", body)
        data = payload.get("data")
        return BakongTransaction.from_raw(data) if data else None

    async def check_by_instruction_ref(
        self,
        instruction_ref: str,
        *,
        amount: float | None = None,
        currency: str | None = None,
    ) -> BakongTransaction | None:
        body: dict = {
            "instruction_ref": instruction_ref,
            "instructionRef": instruction_ref,
        }
        if amount is not None:
            body["amount"] = float(amount)
        if currency:
            body["currency"] = currency
        payload = await self._post("/v1/check_transaction_by_instruction_ref", body)
        data = payload.get("data")
        return BakongTransaction.from_raw(data) if data else None

    async def check_by_external_ref(
        self,
        external_ref: str,
        *,
        amount: float | None = None,
        currency: str | None = None,
    ) -> BakongTransaction | None:
        body: dict = {"external_ref": external_ref, "externalRef": external_ref}
        if amount is not None:
            body["amount"] = float(amount)
        if currency:
            body["currency"] = currency
        payload = await self._post("/v1/check_transaction_by_external_ref", body)
        data = payload.get("data")
        return BakongTransaction.from_raw(data) if data else None

    # ------------------------------------------------------------------ #
    # Unified dispatcher
    # ------------------------------------------------------------------ #
    async def search(
        self,
        search_type: SearchType,
        value: str,
        *,
        amount: float | None = None,
        currency: str | None = None,
    ) -> BakongTransaction | None:
        fn = {
            "hash": self.check_by_hash,
            "md5": self.check_by_md5,
            "short_hash": self.check_by_short_hash,
            "instruction_ref": self.check_by_instruction_ref,
            "external_ref": self.check_by_external_ref,
        }[search_type]
        return await fn(value, amount=amount, currency=currency)

    # ------------------------------------------------------------------ #
    # Polling helper: wait until a transaction is found (and optionally paid)
    # ------------------------------------------------------------------ #
    async def poll_until_found(
        self,
        search_type: SearchType,
        value: str,
        *,
        amount: float | None = None,
        currency: str | None = None,
        require_status: str | None = "Success",
        interval_seconds: float = 2.0,
        max_attempts: int = 60,
    ) -> BakongTransaction | None:
        for _ in range(max_attempts):
            tx = await self.search(search_type, value, amount=amount, currency=currency)
            if tx is not None and (require_status is None or tx.status == require_status):
                return tx
            await asyncio.sleep(interval_seconds)
        return None

    # ------------------------------------------------------------------ #
    # Cascading receipt lookup: try every identifier on a KHQR / ABA / ACLB
    #   payment receipt in priority order until we find the Bakong record.
    #
    #   Priority (md5 first for KHQR; the rest discovered empirically 2026-09-09
    #   on the NBC portal):
    #   0. md5 of the KHQR string -> the QR-payment index key, no amount filter
    #   1. short_hash + amount + currency     (highest confidence, always set)
    #   2. purchase_number        -> instruction_ref (merchant internal order)
    #   3. reference_number       -> external_ref    (bank/Bakong tracking id)
    #   4. transaction_id (trx_id) -> BOTH in turn (instruction, then external)
    #   5. apv_number             -> BOTH in turn
    #   6. remark_number          -> NOT an index key; it's Description suffix
    # ------------------------------------------------------------------ #
    async def verify_receipt(
        self,
        *,
        short_hash: str | None = None,
        amount: float | None = None,
        currency: str | None = "USD",
        purchase_number: str | None = None,
        reference_number: str | None = None,
        transaction_id: str | None = None,
        apv_number: str | None = None,
        remark: str | None = None,
        md5: str | None = None,
        full_hash: str | None = None,
        require_status: str | None = "Success",
    ) -> ReceiptVerifyResult:
        attempts: list[dict] = []

        async def _try(
            kind: SearchType, value: str, amt: float | None = None, cur: str | None = None
        ) -> BakongTransaction | None:
            attempts.append({"type": kind, "value": value})
            try:
                tx = await self.search(kind, value, amount=amt, currency=cur)
                if tx is not None and (
                    require_status is None or tx.status == require_status
                ):
                    return tx
            except BakongApiError as exc:
                # A credential failure means we could not ask the question at
                # all. Swallowing it would turn "unable to look" into "not
                # found", which every caller downstream reads as "not paid yet"
                # — a silent false negative on a payment. Every other error is
                # genuinely just a miss for this one key.
                if exc.error_code == BAKONG_ERROR_UNAUTHORIZED:
                    raise
                return None
            return None

        # 1. MD5 of the KHQR string. This is the index key Bakong uses for QR
        #    payments, and the only identifier we control end to end (our own
        #    builder computes it, so it is exactly what the wallet sent). Tried
        #    first and WITHOUT amount/currency: the md5 already identifies one
        #    transaction, so a filter can only turn a hit into a miss — ABA
        #    legitimately settles a USD QR from a KHR wallet at its own rate,
        #    and the recorded amount then differs from what we stored. The
        #    caller verifies the amount after the fact.
        if md5:
            tx = await _try("md5", md5)
            if tx:
                return ReceiptVerifyResult(found=True, via="md5", attempts=attempts, transaction=tx)

        # 2. short_hash + amount + currency  (HIGH confidence: only 1 correct tx)
        if short_hash and amount is not None:
            tx = await _try("short_hash", short_hash, amount, currency or "USD")
            if tx:
                return ReceiptVerifyResult(found=True, via="short_hash", attempts=attempts, transaction=tx)

        # 2b. full 64-char hash (if provided)
        if full_hash:
            tx = await _try("hash", full_hash, amount, currency)
            if tx:
                return ReceiptVerifyResult(found=True, via="full_hash", attempts=attempts, transaction=tx)

        # 2. purchase # -> instruction_ref (merchant internal order id)
        if purchase_number:
            tx = await _try("instruction_ref", purchase_number, amount, currency)
            if tx:
                return ReceiptVerifyResult(found=True, via="purchase_as_instruction_ref", attempts=attempts, transaction=tx)

        # 3. reference # -> external_ref (bank tracking id)
        if reference_number:
            tx = await _try("external_ref", reference_number, amount, currency)
            if tx:
                return ReceiptVerifyResult(found=True, via="reference_as_external_ref", attempts=attempts, transaction=tx)

        # 4. trx_id -> try instruction first, then external
        if transaction_id:
            tx = await _try("instruction_ref", transaction_id, amount, currency)
            if tx:
                return ReceiptVerifyResult(found=True, via="trxid_as_instruction_ref", attempts=attempts, transaction=tx)
            tx = await _try("external_ref", transaction_id, amount, currency)
            if tx:
                return ReceiptVerifyResult(found=True, via="trxid_as_external_ref", attempts=attempts, transaction=tx)

        # 5. apv -> try both
        if apv_number:
            tx = await _try("instruction_ref", apv_number, amount, currency)
            if tx:
                return ReceiptVerifyResult(found=True, via="apv_as_instruction_ref", attempts=attempts, transaction=tx)
            tx = await _try("external_ref", apv_number, amount, currency)
            if tx:
                return ReceiptVerifyResult(found=True, via="apv_as_external_ref", attempts=attempts, transaction=tx)

        # 6. fallback: short_hash alone (without amount) — low-confidence matches
        if short_hash:
            tx = await _try("short_hash", short_hash, None, None)
            if tx:
                return ReceiptVerifyResult(found=True, via="short_hash_no_amount", attempts=attempts, transaction=tx)

        # 7. remark -> NOT an index key. Do NOT attempt API calls for it.
        if remark:
            attempts.append({"type": "remark", "value": remark, "note": "remark_is_not_index_key_only_description_field"})

        return ReceiptVerifyResult(found=False, via=None, attempts=attempts, transaction=None)

    # ------------------------------------------------------------------ #
    # Bulk convenience
    # ------------------------------------------------------------------ #
    async def check_by_md5_list(self, md5_list: list[str]) -> dict[str, BakongTransaction | None]:
        results: dict[str, BakongTransaction | None] = {}
        for md5 in md5_list:
            results[md5] = await self.check_by_md5(md5)
        return results

    async def check_by_hash_list(self, hash_list: list[str]) -> dict[str, BakongTransaction | None]:
        results: dict[str, BakongTransaction | None] = {}
        for h in hash_list:
            results[h] = await self.check_by_hash(h)
        return results


@dataclass
class ReceiptVerifyResult:
    found: bool
    via: str | None
    attempts: list
    transaction: BakongTransaction | None


_client_singleton: BakongApiClient | None = None


def get_bakong_client() -> BakongApiClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = BakongApiClient()
    return _client_singleton
