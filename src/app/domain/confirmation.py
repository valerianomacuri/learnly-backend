"""Signed confirmation tokens (BR-13): preview proves what the customer agreed to."""

import base64
import binascii
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.domain.errors import DomainProblem


class ConfirmationRequired(DomainProblem):
    code = "CONFIRMATION_REQUIRED"
    status = 422
    default_detail = "Preview the refund and confirm it before creating the request"


class ConfirmationInvalid(DomainProblem):
    code = "CONFIRMATION_INVALID"
    status = 422
    default_detail = "The confirmation token is expired or does not match the request"


def _canonical(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_hash(
    *,
    purchase_id: uuid.UUID,
    reason_category: str,
    reason_text: str,
    amount: Decimal,
    refund_method: str,
) -> str:
    """sha256 over the canonical JSON of what the customer confirms."""
    canonical = _canonical(
        {
            "purchase_id": str(purchase_id),
            "reason_category": str(reason_category),
            "reason_text": reason_text,
            "amount": f"{amount:.2f}",
            "refund_method": str(refund_method),
        }
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: bytes, body: str) -> str:
    return _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())


def issue_token(
    *,
    secret: str,
    customer_id: uuid.UUID,
    hash: str,
    now: datetime,
    ttl_min: int,
) -> tuple[str, datetime]:
    """Return `(token, expires_at)`."""
    expires_at = now + timedelta(minutes=ttl_min)
    claims = {"cid": str(customer_id), "h": hash, "exp": int(expires_at.timestamp())}
    body = _b64(_canonical(claims).encode())
    return f"{body}.{_sign(secret.encode(), body)}", expires_at


def verify(
    token: str | None,
    *,
    secret: str,
    payload_hash: str,
    customer_id: uuid.UUID,
    now: datetime,
) -> None:
    """Raise `ConfirmationRequired` (absent) or `ConfirmationInvalid` (otherwise)."""
    if not token:
        raise ConfirmationRequired
    body, _, signature = token.partition(".")
    if not hmac.compare_digest(_sign(secret.encode(), body), signature):
        raise ConfirmationInvalid
    try:
        claims = json.loads(_unb64(body))
        token_customer, token_hash, exp = claims["cid"], claims["h"], int(claims["exp"])
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        raise ConfirmationInvalid from exc
    if (
        token_customer != str(customer_id)
        or not hmac.compare_digest(str(token_hash), payload_hash)
        or now.timestamp() >= exp
    ):
        raise ConfirmationInvalid
