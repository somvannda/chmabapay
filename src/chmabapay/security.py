"""API key handling, account passwords, and webhook HMAC signing."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import bcrypt


def new_api_key() -> tuple[str, str]:
    """Return (display_prefix, raw_key).

    The raw key is shown once; only its hash is stored. The prefix keeps the
    `ck_live_` tag plus the first 4 characters so the key is recognisable in the
    dashboard without storing a usable slice of the secret.
    """
    raw = f"ck_live_{secrets.token_urlsafe(28)}"
    return raw[:12], raw


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


# --------------------------------------------------------------------------- #
# Account passwords — bcrypt, used by the platform admin console sign-in.
# --------------------------------------------------------------------------- #
# bcrypt only hashes the first 72 bytes and raises on anything longer, so the
# limit is enforced explicitly rather than silently truncating.
MAX_PASSWORD_BYTES = 72


def hash_password(raw: str) -> str:
    encoded = raw.encode("utf-8")
    if not encoded:
        raise ValueError("empty_password")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password_too_long: max {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("ascii")


def verify_password(raw: str, password_hash: str | None) -> bool:
    """Return False for a missing hash, an over-long candidate, or a corrupt hash —
    callers treat every failure the same way, so nothing leaks about the account."""
    if not password_hash:
        return False
    encoded = raw.encode("utf-8")
    if not encoded or len(encoded) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(encoded, password_hash.encode("ascii"))
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Webhook signing:  X-ChmabaPay-Signature: t=<unix>,v1=<hex>
#   hmac_sha256(secret, f"{t}.{raw_body}")
# --------------------------------------------------------------------------- #
def sign_payload(payload: bytes, secret: str, t: int | None = None) -> tuple[int, str]:
    if t is None:
        t = int(time.time())
    digest = hmac.new(secret.encode("utf-8"), f"{t}.".encode() + payload, hashlib.sha256)
    return t, digest.hexdigest()


def verify_signature(payload: bytes, secret: str, header: str, max_age_seconds: int = 300) -> bool:
    parts = {}
    for chunk in header.split(","):
        if "=" in chunk:
            key, _, value = chunk.partition("=")
            parts[key.strip()] = value.strip()
    t_raw = parts.get("t")
    v1 = parts.get("v1")
    if not t_raw or not v1:
        return False
    try:
        t = int(t_raw)
    except ValueError:
        return False
    if abs(time.time() - t) > max_age_seconds:
        return False
    _, expected = sign_payload(payload, secret, t)
    return hmac.compare_digest(expected, v1)
