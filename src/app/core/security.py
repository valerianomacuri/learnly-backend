import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings
from app.core.permissions import ROLE_PERMISSIONS, Perm, RoleName


class InvalidTokenError(Exception):
    """The bearer token is missing claims, expired, or has a bad signature."""


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated caller, loaded from the database by the token's `sub`.

    The token only identifies the user; roles and permissions come from the
    `user_roles` / `role_permissions` tables, so they can be changed or revoked
    without waiting for a token to expire.
    """

    user_id: uuid.UUID
    roles: frozenset[str]
    permissions: frozenset[str]

    @property
    def sub(self) -> str:
        """Stable string identity used for audit `actor`."""
        return str(self.user_id)

    def has(self, permission: Perm | str) -> bool:
        return str(permission) in self.permissions

    @classmethod
    def for_roles(cls, user_id: uuid.UUID, roles: Iterable[RoleName]) -> "Principal":
        """Principal as the catalog defines it (seed and tests; no database)."""
        role_set = frozenset(roles)
        return cls(
            user_id=user_id,
            roles=frozenset(str(role) for role in role_set),
            permissions=frozenset(
                str(perm) for role in role_set for perm in ROLE_PERMISSIONS[role]
            ),
        )


def create_access_token(
    *,
    user_id: uuid.UUID,
    expires_in: timedelta | None = None,
) -> str:
    """Issue a token (`POST /auth/login`, the seed and the tests)."""
    settings = get_settings()
    now = datetime.now(UTC)
    lifetime = expires_in or timedelta(minutes=settings.access_token_ttl_minutes)
    claims = {"sub": str(user_id), "iat": now, "exp": now + lifetime}
    return jwt.encode(
        claims,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> uuid.UUID:
    """Validate the token and return the user id (`sub`)."""
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
        return uuid.UUID(str(claims["sub"]))
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidTokenError(str(exc)) from exc
