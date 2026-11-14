from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.passwords import DUMMY_HASH, verify_password
from app.core.security import create_access_token
from app.models.audit_event import AuditDecision
from app.repositories.user import UserRepository
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.audit_service import AuditService


@dataclass(frozen=True, slots=True)
class LoginResult:
    """`token` is None on failure; failures are returned (not raised) so their
    audit row commits with the request."""

    token: TokenResponse | None


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.users = UserRepository(session)
        self.audit = AuditService(session)
        self.settings = get_settings()

    async def login(self, data: LoginRequest) -> LoginResult:
        user = await self.users.get_by_email(data.email)
        # Unknown email and wrong password cost the same and look the same.
        valid, new_hash = verify_password(
            data.password.get_secret_value(), user.password_hash if user else DUMMY_HASH
        )
        if user is None or not valid:
            await self.audit.record(
                actor=str(user.id) if user else "anonymous",
                action="auth.login",
                resource_type="user",
                resource_id=user.id if user else None,
                decision=AuditDecision.BLOCKED,
                reason="invalid credentials",
            )
            return LoginResult(None)

        if new_hash is not None:  # hashing parameters were upgraded
            user.password_hash = new_hash
        await self.audit.record(
            actor=str(user.id),
            action="auth.login",
            resource_type="user",
            resource_id=user.id,
        )
        return LoginResult(
            TokenResponse(
                access_token=create_access_token(user_id=user.id),
                expires_in=self.settings.access_token_ttl_minutes * 60,
            )
        )
