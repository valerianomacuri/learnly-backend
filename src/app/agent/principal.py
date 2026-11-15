"""Identity of the agent: the authenticated customer, with fewer permissions.

The identity is built only from `config["configurable"]` (filled by the API from the
JWT), never from anything the model produced (challenge §2, rule 1).
"""

import uuid
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig

from app.core.permissions import Perm
from app.core.security import Principal

# Subset of the customer's permissions: no create/cancel (spec 10, behind HITL).
AGENT_PERMISSIONS: frozenset[Perm] = frozenset(
    {
        Perm.PURCHASE_READ_OWN,
        Perm.REFUND_READ_OWN,
        Perm.POLICY_READ,
        Perm.REFUND_SIMULATE,
    }
)


class MissingIdentityError(RuntimeError):
    """The run was started without the identity the API must inject."""


@dataclass(frozen=True, slots=True)
class AgentPrincipal:
    user_id: uuid.UUID
    role: str
    acting_via: Literal["agent"] = "agent"
    thread_id: str = ""
    request_id: str = ""

    @classmethod
    def from_config(cls, config: RunnableConfig) -> "AgentPrincipal":
        configurable: dict[str, Any] = config.get("configurable", {})
        try:
            return cls(
                user_id=uuid.UUID(str(configurable["user_id"])),
                role=str(configurable["role"]),
                thread_id=str(configurable.get("thread_id", "")),
                request_id=str(configurable.get("request_id", "")),
            )
        except (KeyError, ValueError) as exc:
            raise MissingIdentityError("agent run without a valid identity") from exc

    def to_service_principal(self) -> Principal:
        """What the services see: the customer, limited to `AGENT_PERMISSIONS`."""
        return Principal(
            user_id=self.user_id,
            roles=frozenset({self.role}),
            permissions=frozenset(str(p) for p in AGENT_PERMISSIONS),
        )
