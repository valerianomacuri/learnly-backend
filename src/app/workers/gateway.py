"""Payment gateway port and its simulated implementation (spec 05)."""

import hashlib
import random
from collections import defaultdict
from decimal import Decimal
from typing import Protocol


class GatewayError(Exception):
    """Base class for gateway failures."""


class TransientGatewayError(GatewayError):
    """Worth retrying (timeouts, 5xx)."""


class PermanentGatewayError(GatewayError):
    """Retrying cannot help (invalid account, declined): fail the payout at once."""


class PaymentGateway(Protocol):
    async def pay(
        self, idempotency_key: str, method: str, amount: Decimal, currency: str
    ) -> str:
        """Pay once per `idempotency_key` and return the provider reference.

        Calling again with the same key returns the same reference without paying.
        """
        ...


class SimulatedGateway:
    """Deterministic gateway: succeeds unless told otherwise.

    - `failures`: the first N calls of every idempotency key raise a transient error.
    - `fail_rate`: probability (0..1) of a transient error, drawn from a seeded RNG.
    The reference derives from the key, so a re-run after a crash yields the same one.
    `payments_made` counts distinct payments actually executed (for tests).
    """

    def __init__(
        self, *, fail_rate: float = 0.0, failures: int = 0, seed: int | None = 0
    ) -> None:
        self.fail_rate = fail_rate
        self.failures = failures
        self._rng = random.Random(seed)
        self._calls: defaultdict[str, int] = defaultdict(int)
        self._paid: dict[str, str] = {}
        self.payments_made = 0

    async def pay(
        self, idempotency_key: str, method: str, amount: Decimal, currency: str
    ) -> str:
        if idempotency_key in self._paid:
            return self._paid[idempotency_key]
        self._calls[idempotency_key] += 1
        if self._calls[idempotency_key] <= self.failures:
            raise TransientGatewayError("simulated gateway failure")
        if self.fail_rate > 0 and self._rng.random() < self.fail_rate:
            raise TransientGatewayError("simulated random gateway failure")
        ref = "sim_" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
        self._paid[idempotency_key] = ref
        self.payments_made += 1
        return ref
