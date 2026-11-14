"""RBAC catalog: the roles and permissions the system knows about.

This is the single source of truth. The seed (and the tests) write it to the
`roles` / `permissions` / `role_permissions` tables; at request time the caller's
permissions are read from the database, never from the token.

Naming: `<resource>:<action>`. `*_any` grants access to every row; `*_own` only to
rows that belong to the caller (anything else answers 404, like a missing row).
Add new resources by extending `Perm` and `ROLE_PERMISSIONS`, then re-run the seed.
"""

import enum


class RoleName(enum.StrEnum):
    CUSTOMER = "customer"
    SUPERVISOR = "supervisor"


class Perm(enum.StrEnum):
    USER_CREATE = "user:create"
    USER_READ_ANY = "user:read_any"
    USER_READ_OWN = "user:read_own"
    USER_UPDATE_ANY = "user:update_any"
    USER_UPDATE_OWN = "user:update_own"


ROLE_PERMISSIONS: dict[RoleName, frozenset[Perm]] = {
    RoleName.CUSTOMER: frozenset(
        {
            Perm.USER_READ_OWN,
            Perm.USER_UPDATE_OWN,
        }
    ),
    RoleName.SUPERVISOR: frozenset(
        {
            Perm.USER_CREATE,
            Perm.USER_READ_ANY,
            Perm.USER_UPDATE_ANY,
        }
    ),
}
