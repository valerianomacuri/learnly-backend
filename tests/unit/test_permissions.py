from app.core.permissions import ROLE_PERMISSIONS, Perm, RoleName

CUSTOMER_NEW = {
    "purchase:read_own",
    "policy:read",
    "refund:simulate",
    "refund:create",
    "refund:read_own",
    "refund:cancel_own",
    "chat:use",
}
SUPERVISOR_NEW = {
    "purchase:read_for_review",
    "policy:read",
    "refund:read_any",
    "refund:decide",
    "refund:retry_payout",
    "note:write",
    "note:read",
}
USER_CUSTOMER = {"user:read_own", "user:update_own"}
USER_SUPERVISOR = {"user:create", "user:read_any", "user:update_any"}


def test_each_role_gets_exactly_the_matrix() -> None:
    assert {str(p) for p in ROLE_PERMISSIONS[RoleName.CUSTOMER]} == (
        CUSTOMER_NEW | USER_CUSTOMER
    )
    assert {str(p) for p in ROLE_PERMISSIONS[RoleName.SUPERVISOR]} == (
        SUPERVISOR_NEW | USER_SUPERVISOR
    )


def test_supervisor_cannot_create_refunds_or_chat() -> None:  # BR-11
    sup = ROLE_PERMISSIONS[RoleName.SUPERVISOR]
    assert Perm.REFUND_CREATE not in sup
    assert Perm.CHAT_USE not in sup
