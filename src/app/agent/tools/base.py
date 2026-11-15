"""Tool contract: `ToolResult`, the runtime context and the dispatcher (challenge §7).

Domain errors come back as data so the model can explain them; infrastructure
failures are flagged `retryable`. The model never provides identity: it lives in
`ToolContext`, built from `config["configurable"]`.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.principal import AgentPrincipal
from app.domain.errors import DomainProblem
from app.rag.retriever import PolicyRetriever
from app.services.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)

logger = logging.getLogger("app.agent.tools")


class ToolError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ToolResult(BaseModel):
    ok: bool
    data: Any = None
    error: ToolError | None = None

    @classmethod
    def success(cls, data: Any) -> "ToolResult":
        return cls(ok=True, data=jsonable(data))

    @classmethod
    def failure(
        cls, code: str, message: str, *, retryable: bool = False
    ) -> "ToolResult":
        return cls(
            ok=False, error=ToolError(code=code, message=message, retryable=retryable)
        )


class ToolArgs(BaseModel):
    """Base of every argument schema the model fills in."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True, slots=True)
class ToolContext:
    principal: AgentPrincipal
    session_maker: async_sessionmaker[AsyncSession]
    retriever_factory: Callable[[AsyncSession], PolicyRetriever] | None = None


Handler = Callable[[ToolContext, Any], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    args: type[ToolArgs]
    handler: Handler
    not_found_code: str = "NOT_FOUND"

    def openai_schema(self) -> dict[str, Any]:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }


def jsonable(value: Any) -> Any:
    """Plain JSON types, so results go into messages and checkpoints unchanged."""
    if isinstance(value, BaseModel):
        return jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


async def run_tool(
    spec: ToolSpec, ctx: ToolContext, raw_args: dict[str, Any]
) -> ToolResult:
    """Validate the model's arguments and run the handler; never raises."""
    try:
        args = spec.args.model_validate(raw_args)
    except ValidationError as exc:
        return ToolResult.failure("INVALID_ARGUMENTS", _short(exc))
    try:
        return ToolResult.success(await spec.handler(ctx, args))
    except NotFoundError:
        return ToolResult.failure(spec.not_found_code, "No encontrado.")
    except ForbiddenError:
        return ToolResult.failure("FORBIDDEN", "Acción no permitida.")
    except (InvalidInputError, ConflictError) as exc:
        return ToolResult.failure("INVALID_INPUT", str(exc) or "Entrada inválida.")
    except DomainProblem as exc:
        return ToolResult.failure(exc.code, exc.detail)
    except Exception:
        logger.exception("tool %s failed", spec.name)
        return ToolResult.failure(
            "INFRASTRUCTURE_ERROR", "Error temporal del sistema.", retryable=True
        )


def _short(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
    )
