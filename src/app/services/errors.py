from app.domain.errors import DomainProblem


class NotFoundError(Exception):
    def __init__(self, entity: str) -> None:
        super().__init__(f"{entity} not found")


class ConflictError(Exception):
    pass


class InvalidInputError(Exception):
    """A well-formed request carries a value the service cannot use (e.g. a cursor)."""


class ForbiddenError(Exception):
    """The caller is authenticated but not allowed to perform the action."""


class RefundNotEligible(DomainProblem):
    code = "REFUND_NOT_ELIGIBLE"
    status = 422
    default_detail = "The purchase is not eligible for a refund"


class IdempotencyConflict(DomainProblem):
    code = "IDEMPOTENCY_CONFLICT"
    status = 409
    default_detail = "The Idempotency-Key was already used with a different body"


class SelfApprovalForbidden(DomainProblem):
    code = "SELF_APPROVAL_FORBIDDEN"
    status = 403
    default_detail = "A supervisor cannot decide their own refund request"


class RefundNotPending(DomainProblem):
    code = "REFUND_NOT_PENDING"
    status = 409
    default_detail = "The refund was already decided or is no longer pending"


class DecisionNoteRequired(DomainProblem):
    code = "DECISION_NOTE_REQUIRED"
    status = 422
    default_detail = "Rejecting a refund requires a note of at least 10 characters"


class IfMatchRequired(DomainProblem):
    code = "IF_MATCH_REQUIRED"
    status = 428
    default_detail = "The If-Match header with the refund version is required"


class InvalidIfMatch(DomainProblem):
    code = "INVALID_IF_MATCH"
    status = 400
    default_detail = "If-Match must be the refund version"


class VersionConflict(DomainProblem):
    code = "VERSION_CONFLICT"
    status = 409
    default_detail = "The refund changed since you read it (If-Match mismatch)"


class RefundNotFailed(DomainProblem):
    code = "REFUND_NOT_FAILED"
    status = 409
    default_detail = "Only a refund whose payout FAILED can be retried"
