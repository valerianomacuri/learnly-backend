from app.models.eligibility import EligibilityCheck
from app.repositories.base import BaseRepository


class EligibilityCheckRepository(BaseRepository[EligibilityCheck]):
    model = EligibilityCheck
