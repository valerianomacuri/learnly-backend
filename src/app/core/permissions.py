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
    PURCHASE_READ_OWN = "purchase:read_own"
    PURCHASE_READ_FOR_REVIEW = "purchase:read_for_review"
    POLICY_READ = "policy:read"
    REFUND_SIMULATE = "refund:simulate"
    REFUND_CREATE = "refund:create"
    REFUND_READ_OWN = "refund:read_own"
    REFUND_READ_ANY = "refund:read_any"
    REFUND_CANCEL_OWN = "refund:cancel_own"
    REFUND_DECIDE = "refund:decide"
    REFUND_RETRY_PAYOUT = "refund:retry_payout"
    NOTE_WRITE = "note:write"
    NOTE_READ = "note:read"
    CHAT_USE = "chat:use"


ROLE_PERMISSIONS: dict[RoleName, frozenset[Perm]] = {
    RoleName.CUSTOMER: frozenset(
        {
            Perm.USER_READ_OWN,
            Perm.USER_UPDATE_OWN,
            Perm.PURCHASE_READ_OWN,
            Perm.POLICY_READ,
            Perm.REFUND_SIMULATE,
            Perm.REFUND_CREATE,
            Perm.REFUND_READ_OWN,
            Perm.REFUND_CANCEL_OWN,
            Perm.CHAT_USE,
        }
    ),
    RoleName.SUPERVISOR: frozenset(
        {
            Perm.USER_CREATE,
            Perm.USER_READ_ANY,
            Perm.USER_UPDATE_ANY,
            Perm.PURCHASE_READ_FOR_REVIEW,
            Perm.POLICY_READ,
            Perm.REFUND_READ_ANY,
            Perm.REFUND_DECIDE,
            Perm.REFUND_RETRY_PAYOUT,
            Perm.NOTE_WRITE,
            Perm.NOTE_READ,
        }
    ),
}


# The agent acts on behalf of a customer (`Principal.acting_via == "agent"`, spec 09)
# with a subset of the customer's permissions. There is no "agent" role.
