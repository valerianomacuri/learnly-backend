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


class VersionConflict(DomainProblem):
    code = "VERSION_CONFLICT"
    status = 409
    default_detail = "The refund changed since you read it (If-Match mismatch)"
