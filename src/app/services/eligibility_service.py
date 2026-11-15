import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Perm
from app.core.security import Principal
from app.domain.eligibility import (
    EligibilityFacts,
    EligibilityResult,
    PurchaseFacts,
    RefundPolicyEngine,
)
from app.domain.enums import CheckSource
from app.models.eligibility import EligibilityCheck
from app.repositories.eligibility_check import EligibilityCheckRepository
from app.repositories.policy import PolicyRepository
from app.repositories.purchase import PurchaseRepository
from app.repositories.refund_request import RefundRequestRepository
from app.services.authz import require_permission

# BR-10 counts refunds of the last 12 months.
ABUSE_LOOKBACK = timedelta(days=365)


def utc_now() -> datetime:
    return datetime.now(UTC)


class EligibilityService:
    """Gathers the facts, runs the engine and always records the check (D6)."""

    def __init__(
        self, session: AsyncSession, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self.purchases = PurchaseRepository(session)
        self.refunds = RefundRequestRepository(session)
        self.checks = EligibilityCheckRepository(session)
        self.policies = PolicyRepository(session)
        self.clock = clock

    async def check(
        self, principal: Principal, purchase_id: uuid.UUID, source: CheckSource
    ) -> EligibilityResult:
        require_permission(principal, Perm.REFUND_SIMULATE)
        config = await self.policies.get_active_config()
        engine = RefundPolicyEngine(config, self.clock)

        # Someone else's purchase is not found: same result as a missing one.
        purchase = await self.purchases.get_owned(purchase_id, principal.user_id)
        if purchase is None:
            facts = EligibilityFacts(
                requesting_user_id=principal.user_id, purchase=None
            )
        else:
            progress = purchase.progress
            facts = EligibilityFacts(
                requesting_user_id=principal.user_id,
                purchase=PurchaseFacts(
                    customer_id=purchase.customer_id,
                    status=purchase.status,
                    purchase_type=purchase.purchase_type,
                    payment_method=purchase.payment_method,
                    purchased_at=purchase.purchased_at,
                    amount=purchase.amount,
                    currency=purchase.currency,
                ),
                progress_pct=progress.progress_pct if progress else 0,
                certificate_issued_at=(
                    progress.certificate_issued_at if progress else None
                ),
                existing_requests=await self.refunds.statuses_for_purchase(
                    purchase.id, principal.user_id
                ),
                completed_refunds_12m=await self.refunds.count_completed_since(
                    principal.user_id, self.clock() - ABUSE_LOOKBACK
                ),
            )

        result = engine.evaluate(facts)
        await self._record(principal, purchase_id, source, result)
        return result

    async def _record(
        self,
        principal: Principal,
        purchase_id: uuid.UUID,
        source: CheckSource,
        result: EligibilityResult,
    ) -> None:
        await self.checks.add(
            EligibilityCheck(
                customer_id=principal.user_id,
                purchase_id=purchase_id,
                source=source.value,
                eligible=result.eligible,
                reason_codes=[code.value for code in result.reason_codes],
                policy_version=result.policy_version,
                details=result.model_dump(mode="json"),
            )
        )
