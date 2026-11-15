"""BR-12: the reason of a refund request must be a real explanation."""

from app.domain.enums import ReasonCategory
from app.domain.errors import DomainProblem

MIN_LENGTH = 20
MAX_LENGTH = 1000
MIN_DISTINCT_WORDS = 3


class ReasonRequired(DomainProblem):
    code = "REASON_REQUIRED"
    status = 422
    default_detail = "A valid reason is required"


def validate_reason(category: str, text: str) -> tuple[ReasonCategory, str]:
    """Return the normalized `(category, text)` or raise `ReasonRequired`."""
    try:
        parsed = ReasonCategory(category)
    except ValueError as exc:
        raise ReasonRequired("Unknown reason category") from exc
    cleaned = text.strip()
    if not MIN_LENGTH <= len(cleaned) <= MAX_LENGTH:
        raise ReasonRequired(
            f"The reason must have between {MIN_LENGTH} and {MAX_LENGTH} characters"
        )
    words = {word.casefold() for word in cleaned.split()}
    if len(words) < MIN_DISTINCT_WORDS:
        raise ReasonRequired(
            f"The reason must have at least {MIN_DISTINCT_WORDS} different words"
        )
    return parsed, cleaned
