"""Living permission table (spec 12): who may call every route of the API.

`test_route_inventory.py` fails when a route is missing here (or listed here but
gone), so a new endpoint cannot ship without an explicit access decision.
"""

from enum import StrEnum


class Access(StrEnum):
    PUBLIC = "public"  # no token
    ANY = "any"  # any authenticated user (the service scopes the data)
    CUSTOMER = "customer"  # customers only; a supervisor gets 403
    SUPERVISOR = "supervisor"  # supervisors only; a customer gets 403


P = "/api/v1"

PERMISSIONS: dict[tuple[str, str], Access] = {
    ("GET", "/health"): Access.PUBLIC,
    ("GET", "/healthz"): Access.PUBLIC,
    ("POST", f"{P}/auth/login"): Access.PUBLIC,
    ("GET", f"{P}/me"): Access.ANY,
    # users: supervisors manage; everyone reads/updates only themselves
    ("POST", f"{P}/users"): Access.SUPERVISOR,
    ("GET", f"{P}/users"): Access.SUPERVISOR,
    ("GET", f"{P}/users/{{user_id}}"): Access.ANY,
    ("PATCH", f"{P}/users/{{user_id}}"): Access.ANY,
    # policies are public knowledge for any signed-in user
    ("GET", f"{P}/policies"): Access.ANY,
    ("GET", f"{P}/policies/{{doc_key}}"): Access.ANY,
    # purchases and refunds belong to the customer
    ("GET", f"{P}/purchases"): Access.CUSTOMER,
    ("GET", f"{P}/purchases/{{purchase_id}}"): Access.CUSTOMER,
    ("GET", f"{P}/purchases/{{purchase_id}}/refund-eligibility"): Access.CUSTOMER,
    ("POST", f"{P}/refunds/preview"): Access.CUSTOMER,
    ("POST", f"{P}/refunds"): Access.CUSTOMER,
    ("GET", f"{P}/refunds"): Access.CUSTOMER,
    ("GET", f"{P}/refunds/{{refund_id}}"): Access.CUSTOMER,
    ("GET", f"{P}/refunds/{{refund_id}}/events"): Access.CUSTOMER,
    ("POST", f"{P}/refunds/{{refund_id}}/cancel"): Access.CUSTOMER,
    # supervisor console
    ("GET", f"{P}/supervisor/refunds/stream"): Access.SUPERVISOR,
    ("GET", f"{P}/supervisor/refunds"): Access.SUPERVISOR,
    ("GET", f"{P}/supervisor/stats"): Access.SUPERVISOR,
    ("GET", f"{P}/supervisor/refunds/{{refund_id}}"): Access.SUPERVISOR,
    ("POST", f"{P}/supervisor/refunds/{{refund_id}}/approve"): Access.SUPERVISOR,
    ("POST", f"{P}/supervisor/refunds/{{refund_id}}/reject"): Access.SUPERVISOR,
    ("POST", f"{P}/supervisor/refunds/{{refund_id}}/retry-payout"): Access.SUPERVISOR,
    ("GET", f"{P}/supervisor/refunds/{{refund_id}}/notes"): Access.SUPERVISOR,
    ("POST", f"{P}/supervisor/refunds/{{refund_id}}/notes"): Access.SUPERVISOR,
    ("GET", f"{P}/supervisor/refunds/{{refund_id}}/events"): Access.SUPERVISOR,
    # chat is the customer's assistant
    ("POST", f"{P}/chat/threads"): Access.CUSTOMER,
    ("GET", f"{P}/chat/threads"): Access.CUSTOMER,
    ("GET", f"{P}/chat/threads/{{thread_id}}/messages"): Access.CUSTOMER,
    ("POST", f"{P}/chat/threads/{{thread_id}}/messages"): Access.CUSTOMER,
    ("GET", f"{P}/chat/threads/{{thread_id}}/pending-interrupt"): Access.CUSTOMER,
    ("POST", f"{P}/chat/threads/{{thread_id}}/resume"): Access.CUSTOMER,
}
