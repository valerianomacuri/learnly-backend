from fastapi import APIRouter

from app.api.routes import auth, users

api_router = APIRouter()
for module in (auth, users):
    api_router.include_router(module.router)
