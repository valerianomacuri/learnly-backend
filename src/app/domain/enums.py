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
