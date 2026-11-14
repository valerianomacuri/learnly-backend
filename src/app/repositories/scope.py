"""Ownership scoping shared by all repositories.

Every scoped query joins up to `User` (the owner). A caller holding the `*_any`
permission of the resource sees every row; anyone else only sees rows owned by
them. Out-of-scope rows look exactly like missing rows (anti-IDOR).
"""

from sqlalchemy import Select

from app.core.permissions import Perm
from app.core.security import Principal
from app.models.user import User


def restrict[RowT](
    stmt: Select[RowT], principal: Principal, any_permission: Perm
) -> Select[RowT]:
    """Filter a statement that already joins `User` (the resource owner)."""
    if principal.has(any_permission):
        return stmt
    return stmt.where(User.id == principal.user_id)
