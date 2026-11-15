from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import SessionDep, requires
from app.core.permissions import Perm
from app.core.security import Principal
from app.repositories.policy import PolicyRepository
from app.schemas.policy import PolicyRead, PolicySummary
from app.services.errors import NotFoundError

router = APIRouter(prefix="/policies", tags=["policies"])

ReaderDep = Annotated[Principal, Depends(requires(Perm.POLICY_READ))]


@router.get("", response_model=list[PolicySummary])
async def list_policies(session: SessionDep, _: ReaderDep) -> Sequence[PolicySummary]:
    """ACTIVE policy documents (version and date, no body)."""
    documents = await PolicyRepository(session).list_active()
    return [PolicySummary.model_validate(d) for d in documents]


@router.get("/{doc_key}", response_model=PolicyRead)
async def get_policy(doc_key: str, session: SessionDep, _: ReaderDep) -> PolicyRead:
    """The ACTIVE version of one document with its Markdown."""
    for document in await PolicyRepository(session).list_active():
        if document.doc_key == doc_key:
            return PolicyRead(
                doc_key=document.doc_key,
                title=document.title,
                version=document.version,
                effective_date=document.effective_date,
                content=document.content or "",
            )
    raise NotFoundError("Policy")
