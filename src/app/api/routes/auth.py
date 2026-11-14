from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.deps import AuthServiceDep
from app.schemas.auth import LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={401: {"description": "Invalid credentials"}},
)
async def login(
    data: LoginRequest, service: AuthServiceDep
) -> TokenResponse | JSONResponse:
    """Exchange email + password for a bearer token (public route)."""
    result = await service.login(data)
    if result.token is None:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid credentials"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return result.token
