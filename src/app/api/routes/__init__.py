from fastapi import APIRouter

from app.api.routes import auth, me, purchases, users

api_router = APIRouter()
for module in (auth, users, me, purchases):
    api_router.include_router(module.router)
