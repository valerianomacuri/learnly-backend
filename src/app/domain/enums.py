"""Domain enums (pure: no SQLAlchemy, FastAPI or services).

Persisted by the models as VARCHAR + CHECK through `models.base.str_enum`, so
every value must stay within 20 characters.
"""

import enum


class RefundStatus(enum.StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# A purchase can have at most one request in these states (BR-04).
ACTIVE_REFUND_STATUSES = (
    RefundStatus.PENDING_APPROVAL,
    RefundStatus.APPROVED,
    RefundStatus.PROCESSING,
)


class ReasonCategory(enum.StrEnum):
    NOT_AS_DESCRIBED = "NOT_AS_DESCRIBED"
    ACCIDENTAL_PURCHASE = "ACCIDENTAL_PURCHASE"
    TECHNICAL_ISSUES = "TECHNICAL_ISSUES"
    CONTENT_OUTDATED = "CONTENT_OUTDATED"
    FOUND_ALTERNATIVE = "FOUND_ALTERNATIVE"
    NO_LONGER_NEEDED = "NO_LONGER_NEEDED"
    OTHER = "OTHER"


class RefundMethod(enum.StrEnum):
    ORIGINAL_PAYMENT = "ORIGINAL_PAYMENT"
    PLATFORM_CREDITS = "PLATFORM_CREDITS"


class Channel(enum.StrEnum):
    CHAT = "CHAT"
    MANUAL_FORM = "MANUAL_FORM"


class PaymentMethod(enum.StrEnum):
    CARD = "CARD"
    PAYPAL = "PAYPAL"
    APPLE_IAP = "APPLE_IAP"
    GOOGLE_IAP = "GOOGLE_IAP"
    CREDITS = "CREDITS"


class PurchaseType(enum.StrEnum):
    INDIVIDUAL = "INDIVIDUAL"
    GIFT = "GIFT"
    SUBSCRIPTION = "SUBSCRIPTION"
    THIRD_PARTY = "THIRD_PARTY"


class PurchaseStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    REFUNDED = "REFUNDED"


class ActorType(enum.StrEnum):
    CUSTOMER = "CUSTOMER"
    SUPERVISOR = "SUPERVISOR"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class PayoutStatus(enum.StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class OutboxStatus(enum.StrEnum):
    PENDING = "PENDING"
    DONE = "DONE"
    DEAD = "DEAD"


class ThreadStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    CLOSED = "CLOSED"


class PolicyStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class ReasonCode(enum.StrEnum):
    """Why a refund is not eligible (challenge §3)."""

    OUTSIDE_REFUND_WINDOW = "OUTSIDE_REFUND_WINDOW"
    ALREADY_REFUNDED = "ALREADY_REFUNDED"
    ACTIVE_REFUND_EXISTS = "ACTIVE_REFUND_EXISTS"
    PREVIOUSLY_REJECTED = "PREVIOUSLY_REJECTED"
    CONSUMPTION_LIMIT_EXCEEDED = "CONSUMPTION_LIMIT_EXCEEDED"
    CERTIFICATE_ISSUED = "CERTIFICATE_ISSUED"
    SUBSCRIPTION_NOT_ELIGIBLE = "SUBSCRIPTION_NOT_ELIGIBLE"
    THIRD_PARTY_PURCHASE = "THIRD_PARTY_PURCHASE"
    REFUND_ABUSE_LIMIT = "REFUND_ABUSE_LIMIT"
    PURCHASE_NOT_FOUND = "PURCHASE_NOT_FOUND"


class EligibilityWarning(enum.StrEnum):
    """Non-blocking flags, for the supervisor only (never shown to the customer)."""

    HIGH_REFUND_FREQUENCY = "HIGH_REFUND_FREQUENCY"


class QueueSort(enum.StrEnum):
    """Sort keys of the supervisor queue (`sla` = oldest first = most overdue first)."""

    CREATED_AT = "created_at"
    AMOUNT = "amount"
    SLA = "sla"


class SortOrder(enum.StrEnum):
    ASC = "asc"
    DESC = "desc"


class CheckSource(enum.StrEnum):
    """Where an eligibility check came from (`eligibility_checks.source`)."""

    SIMULATION = "SIMULATION"
    PREVIEW = "PREVIEW"
    CREATE_ATTEMPT = "CREATE_ATTEMPT"
    AGENT = "AGENT"
