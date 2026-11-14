from app.core.permissions import Perm
from app.core.security import Principal
from app.services.errors import ForbiddenError


def require_permission(principal: Principal, permission: Perm) -> None:
    """Defense in depth: services re-check what the route already enforces."""
    if not principal.has(permission):
        raise ForbiddenError(f"Missing permission: {permission}")


def require_any_permission(principal: Principal, *permissions: Perm) -> None:
    if not any(principal.has(permission) for permission in permissions):
        raise ForbiddenError(
            "Missing permission: " + " or ".join(str(p) for p in permissions)
        )
