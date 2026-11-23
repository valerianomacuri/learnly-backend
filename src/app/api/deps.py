import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.permissions import Perm
from app.core.rate_limit import SlidingWindowLimiter
from app.core.security import InvalidTokenError, Principal, decode_access_token
from app.domain.errors import RateLimited, RateLimitUnavailable
from app.repositories.user import UserRepository
from app.services.auth_service import AuthService
from app.services.eligibility_service import EligibilityService
from app.services.me_service import MeService
from app.services.purchase_service import PurchaseService
from app.services.refund_service import RefundService
from app.services.supervisor_refund_service import SupervisorRefundService
from app.services.user_service import UserService

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False)
_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


async def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: SessionDep,
) -> Principal:
    """Authenticate the caller (401 otherwise) and load their roles/permissions."""
    if credentials is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Not authenticated",
            headers=_UNAUTHORIZED_HEADERS,
        )
    principal = None
    try:
        user_id = decode_access_token(credentials.credentials)
        principal = await UserRepository(session).get_principal(user_id)
    except InvalidTokenError:
        pass
    if principal is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or expired token",
            headers=_UNAUTHORIZED_HEADERS,
        )
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def requires(permission: Perm) -> Callable[[Principal], Principal]:
    """Route guard: 403 unless the caller holds `permission`."""

    def dependency(principal: PrincipalDep) -> Principal:
        if not principal.has(permission):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"Missing permission: {permission}"
            )
        return principal

    return dependency


logger = logging.getLogger(__name__)


def rate_limit(
    name: str, limit: int, window_s: int, *, write: bool = True
) -> Callable[[Request, Principal], Awaitable[None]]:
    """Per-customer sliding-window limit (429 + `Retry-After`).

    No `REDIS_URL` => limiting is off. Redis configured but failing => a read
    (`write=False`) fails open, a write fails closed with 503.
    """

    async def dependency(request: Request, principal: PrincipalDep) -> None:
        redis = getattr(request.app.state, "redis", None)
        if redis is None:
            return
        try:
            decision = await SlidingWindowLimiter(redis).hit(
                f"rl:{name}:{principal.user_id}", limit=limit, window_s=window_s
            )
        except Exception:
            logger.warning("rate limiter unavailable (route=%s)", name, exc_info=True)
            if write:
                raise RateLimitUnavailable() from None
            return
        if not decision.allowed:
            raise RateLimited(decision.retry_after)

    return dependency


def get_auth_service(session: SessionDep) -> AuthService:
    return AuthService(session)


def get_user_service(session: SessionDep) -> UserService:
    return UserService(session)


def get_me_service(session: SessionDep) -> MeService:
    return MeService(session)


def get_purchase_service(session: SessionDep) -> PurchaseService:
    return PurchaseService(session)


def get_eligibility_service(session: SessionDep) -> EligibilityService:
    return EligibilityService(session)


def get_refund_service(session: SessionDep) -> RefundService:
    return RefundService(session)


def get_supervisor_refund_service(session: SessionDep) -> SupervisorRefundService:
    return SupervisorRefundService(session)


RefundServiceDep = Annotated[RefundService, Depends(get_refund_service)]
SupervisorRefundServiceDep = Annotated[
    SupervisorRefundService, Depends(get_supervisor_refund_service)
]
EligibilityServiceDep = Annotated[EligibilityService, Depends(get_eligibility_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
MeServiceDep = Annotated[MeService, Depends(get_me_service)]
PurchaseServiceDep = Annotated[PurchaseService, Depends(get_purchase_service)]
