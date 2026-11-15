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
