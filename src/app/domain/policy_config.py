"""Executable parameters of the active refund policy (single source of truth, D2).

They come from the YAML front-matter of `policies/*.md`; nothing here is hardcoded.
`PolicyConfig` is assembled from the `params` of the ACTIVE policy documents.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, PositiveInt, ValidationError

# The document whose `version` identifies the policy set (`policy_version`).
ELIGIBILITY_DOC_KEY = "refund-eligibility"
# Reserved key under which a document's `reason_code_map` is stored in `params`.
REASON_CODE_MAP_KEY = "reason_code_map"


class PolicyConfigError(Exception):
    """The policy parameters are missing, mistyped or contradictory."""


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    refund_window_days: PositiveInt
    max_progress_pct: int
    abuse_flag_12m: PositiveInt
    abuse_block_12m: PositiveInt
    confirmation_ttl_min: PositiveInt
    sla_hours: PositiveInt
    one_active_request_per_purchase: bool
    rejected_is_final: bool
    policy_version: str
    reason_code_map: dict[str, str]

    @classmethod
    def from_documents(
        cls, documents: Iterable[tuple[str, str, Mapping[str, Any]]]
    ) -> "PolicyConfig":
        """Merge `(doc_key, version, params)` of the active documents.

        The same key in two documents must carry the same value. Unknown keys are
        ignored (other documents carry parameters for other consumers).
        """
        merged: dict[str, Any] = {}
        version: str | None = None
        for doc_key, doc_version, params in documents:
            if doc_key == ELIGIBILITY_DOC_KEY:
                version = doc_version
            for key, value in params.items():
                if key in merged and merged[key] != value:
                    raise PolicyConfigError(
                        f"Conflicting values for policy parameter '{key}'"
                    )
                merged[key] = value
        if version is None:
            raise PolicyConfigError(f"Missing policy document '{ELIGIBILITY_DOC_KEY}'")

        fields = set(cls.model_fields) - {"policy_version"}
        values = {k: v for k, v in merged.items() if k in fields}
        try:
            config = cls(policy_version=version, **values)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()
            )
            raise PolicyConfigError(f"Invalid policy parameters: {problems}") from exc
        if not 0 <= config.max_progress_pct <= 100:
            raise PolicyConfigError("max_progress_pct must be between 0 and 100")
        if config.abuse_flag_12m > config.abuse_block_12m:
            raise PolicyConfigError("abuse_flag_12m must not exceed abuse_block_12m")
        return config
