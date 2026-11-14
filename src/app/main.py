import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import api_router
from app.core.config import get_settings
from app.core.database import get_engine
from app.services.errors import ConflictError, ForbiddenError, NotFoundError


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await get_engine().dispose()


def create_app() -> FastAPI:
    settings = get_settings()  # fail fast on invalid environment
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict_handler(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ForbiddenError)
    async def forbidden_handler(_: Request, exc: ForbiddenError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
