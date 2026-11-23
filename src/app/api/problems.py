"""RFC 9457 `application/problem+json` rendering for domain problems."""

import re
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

from app.domain.errors import DomainProblem

PROBLEM_MEDIA_TYPE = "application/problem+json"
TRACE_HEADER = "X-Request-ID"


def trace_id_of(request: Request) -> str:
    return str(getattr(request.state, "trace_id", None) or uuid.uuid4())


def problem_response(request: Request, exc: DomainProblem) -> JSONResponse:
    slug = re.sub(r"_", "-", exc.code).lower()
    trace_id = trace_id_of(request)
    headers = {TRACE_HEADER: trace_id}
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=exc.status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
        content={
            "type": f"/problems/{slug}",
            "code": exc.code,
            "reason_codes": exc.reason_codes,
            "detail": exc.detail,
            "trace_id": trace_id,
        },
    )
