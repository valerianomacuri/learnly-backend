"""Operational endpoints (spec 13): readiness and Prometheus metrics.

Mounted at the root, outside `/api/v1`, and hidden from OpenAPI: they are for the
orchestrator and the scraper, not for API clients. `/healthz` (liveness) lives in
`main.py`.
"""

import asyncio
import hmac
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.metrics import OUTBOX_DEAD, OUTBOX_PENDING, SLA_BREACHED
from app.domain.enums import OutboxStatus
from app.models.outbox import OutboxEvent
from app.repositories.supervisor_refund import SupervisorRefundRepository

logger = logging.getLogger(__name__)
router = APIRouter(include_in_schema=False)

CHECK_TIMEOUT_S = 2.0
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _check(name: str, probe: Callable[[], Awaitable[object]]) -> dict[str, str]:
    """Run one dependency probe with a short timeout; never raises."""
    try:
        await asyncio.wait_for(probe(), CHECK_TIMEOUT_S)
    except TimeoutError:
        return {name: "timeout"}
    except Exception as exc:
        logger.warning("readiness: %s check failed: %s", name, type(exc).__name__)
        return {name: f"down ({type(exc).__name__})"}
    return {name: "ok"}


@router.get("/readyz")
async def readyz(request: Request, session: SessionDep) -> Response:
    """200 when DB, Redis (if configured) and the vector store answer; else 503."""
    redis = getattr(request.app.state, "redis", None)

    async def db() -> object:
        return await session.execute(text("SELECT 1"))

    async def vector_store() -> object:
        # The chunk table (pgvector) must be queryable; it may be empty.
        return await session.execute(text("SELECT 1 FROM policy_chunks LIMIT 1"))

    checks = [await _check("database", db)]
    if redis is not None:
        checks.append(await _check("redis", redis.ping))
    # A failed statement aborts the transaction: only probe the store if the DB is up.
    if checks[0]["database"] == "ok":
        checks.append(await _check("vector_store", vector_store))
    else:
        checks.append({"vector_store": "skipped"})
    detail = {k: v for check in checks for k, v in check.items()}
    ok = all(v in {"ok", "skipped"} for v in detail.values()) and (
        detail["vector_store"] != "skipped"
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if ok else "unavailable", "checks": detail},
    )


async def _refresh_gauges(session: AsyncSession) -> None:
    """Mirror database state into the gauges at scrape time."""
    cutoff = datetime.now(UTC) - timedelta(hours=get_settings().sla_hours)
    SLA_BREACHED.set(
        await SupervisorRefundRepository(session).count_sla_breached(cutoff)
    )
    for status_, gauge in (
        (OutboxStatus.PENDING, OUTBOX_PENDING),
        (OutboxStatus.DEAD, OUTBOX_DEAD),
    ):
        gauge.set(
            await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.status == status_)
            )
            or 0
        )


@router.get("/metrics")
async def metrics(
    session: SessionDep, authorization: Annotated[str | None, Header()] = None
) -> Response:
    """Prometheus exposition. Internal: bearer `METRICS_TOKEN` when configured."""
    token = get_settings().metrics_token
    if token is not None:
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied, token.get_secret_value()):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Invalid metrics token",
                headers={"WWW-Authenticate": "Bearer"},
            )
    try:
        await _refresh_gauges(session)
    except Exception:  # the counters are still worth serving
        logger.exception("could not refresh the database gauges")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
