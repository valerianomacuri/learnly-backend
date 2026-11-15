from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Principal
from app.repositories.credit_ledger import CreditLedgerRepository
from app.repositories.user import UserRepository
from app.schemas.me import MeRead
from app.services.errors import NotFoundError


class MeService:
    def __init__(self, session: AsyncSession) -> None:
        self.users = UserRepository(session)
        self.ledger = CreditLedgerRepository(session)

    async def get(self, principal: Principal) -> MeRead:
        user = await self.users.get(principal.user_id)
        if user is None:  # pragma: no cover - the principal was just loaded
            raise NotFoundError("User")
        return MeRead(
            id=user.id,
            email=user.email,
            name=user.name,
            roles=sorted(principal.roles),
            credit_balance=await self.ledger.balance(user.id),
        )
