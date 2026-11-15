from fastapi import APIRouter

from app.api.routes import (
    auth,
    chat,
    me,
    policies,
    purchases,
    refunds,
    supervisor,
    users,
)

api_router = APIRouter()
for module in (auth, users, me, policies, purchases, refunds, supervisor, chat):
    api_router.include_router(module.router)
