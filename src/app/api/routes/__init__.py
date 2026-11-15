from fastapi import APIRouter

from app.api.routes import auth, me, purchases, refunds, supervisor, users

api_router = APIRouter()
for module in (auth, users, me, purchases, refunds, supervisor):
    api_router.include_router(module.router)
