"""Domain problems that the API renders as `application/problem+json`."""


class DomainProblem(Exception):  # noqa: N818 - rendered as a problem, not an error
    """A business rule rejected the request.

    `code` is the stable machine-readable identifier, `status` the HTTP status the
    API maps it to and `reason_codes` the (optional) policy reasons behind it.
    """

    code: str = "DOMAIN_ERROR"
    status: int = 422
    default_detail: str = "The request violates a business rule"

    def __init__(
        self, detail: str | None = None, *, reason_codes: list[str] | None = None
    ) -> None:
        self.detail = detail or self.default_detail
        self.reason_codes = list(reason_codes or [])
        super().__init__(self.detail)


class RateLimited(DomainProblem):
    code = "RATE_LIMITED"
    status = 429
    default_detail = "Too many requests, retry later"

    def __init__(self, retry_after: int, detail: str | None = None) -> None:
        super().__init__(detail)
        self.retry_after = max(1, int(retry_after))


class RateLimitUnavailable(DomainProblem):
    """Redis is down and the route is a write: fail closed."""

    code = "RATE_LIMIT_UNAVAILABLE"
    status = 503
    default_detail = "The service is temporarily unavailable"


class RunInProgress(DomainProblem):
    code = "RUN_IN_PROGRESS"
    status = 409
    default_detail = "Another run is already in progress for this thread"
