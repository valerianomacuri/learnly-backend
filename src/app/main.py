import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runtime import start_runtime
from app.api.problems import TRACE_HEADER, problem_response
from app.api.routes import api_router
from app.core.config import get_settings
from app.core.database import get_engine, get_sessionmaker
from app.core.privacy import install_log_privacy
from app.core.redis import open_redis
from app.domain.errors import DomainProblem
from app.repositories.policy import PolicyRepository
from app.services.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Fail fast: the app needs a complete ACTIVE policy (`uv run policies-load`).
    async with AsyncSession(get_engine()) as session:
        await PolicyRepository(session).get_active_config()
    app.state.redis = open_redis(get_settings().redis_url)
    async with AsyncExitStack() as stack:
        # `None` without OPENAI_API_KEY: the chat answers 503, the rest works.
        app.state.agent_runtime = await start_runtime(
            get_sessionmaker(), stack, app.state.redis
        )
        yield
    if app.state.redis is not None:
        await app.state.redis.aclose()
    await get_engine().dispose()


def create_app() -> FastAPI:
    settings = get_settings()  # fail fast on invalid environment
    logging.basicConfig(level=settings.log_level)
    install_log_privacy()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "If-Match",
            TRACE_HEADER,
        ],
        expose_headers=[TRACE_HEADER, "ETag", "Retry-After"],
    )

    @app.middleware("http")
    async def trace_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Propagate the caller's X-Request-ID (bounded) or mint one: it is the
        # `trace_id` of problem+json bodies.
        incoming = request.headers.get(TRACE_HEADER, "")
        trace_id = incoming if 0 < len(incoming) <= 128 else str(uuid.uuid4())
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers[TRACE_HEADER] = trace_id
        return response

    @app.exception_handler(DomainProblem)
    async def domain_problem_handler(request: Request, exc: DomainProblem) -> Response:
        return problem_response(request, exc)

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict_handler(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ForbiddenError)
    async def forbidden_handler(_: Request, exc: ForbiddenError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(InvalidInputError)
    async def invalid_input_handler(_: Request, exc: InvalidInputError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health", tags=["health"])
    @app.get("/healthz", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
