"""Parse `policies/*.md`: YAML front-matter (validated) + checksum of the file."""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.domain.policy_config import (
    REASON_CODE_MAP_KEY,
    PolicyConfig,
    PolicyConfigError,
)

_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


class PolicyDocumentMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    doc_key: str
    title: str
    version: str
    effective_date: date
    language: str
    tags: list[str] = []
    supersedes: str | None = None
    params: dict[str, Any] = {}
    reason_code_map: dict[str, str] = {}

    @field_validator("version", mode="before")
    @classmethod
    def _version_as_text(cls, value: object) -> object:
        # `version: 2026-10-01` unquoted is parsed by YAML as a date.
        return value.isoformat() if isinstance(value, date) else value


@dataclass(frozen=True, slots=True)
class PolicyDocumentFile:
    path: Path
    meta: PolicyDocumentMeta
    checksum: str

    @property
    def stored_params(self) -> dict[str, Any]:
        """`params` as persisted: the front-matter params plus the code map."""
        params = dict(self.meta.params)
        if self.meta.reason_code_map:
            params[REASON_CODE_MAP_KEY] = dict(self.meta.reason_code_map)
        return params


def parse_policy_file(path: Path) -> PolicyDocumentFile:
    raw = path.read_bytes()
    match = _FRONT_MATTER.match(raw.decode("utf-8"))
    if match is None:
        raise PolicyConfigError(f"{path.name}: missing YAML front-matter")
    try:
        data = yaml.safe_load(match.group(1))
        if not isinstance(data, dict):
            raise PolicyConfigError(f"{path.name}: front-matter must be a mapping")
        meta = PolicyDocumentMeta.model_validate(data)
    except yaml.YAMLError as exc:
        raise PolicyConfigError(f"{path.name}: invalid YAML: {exc}") from exc
    except ValidationError as exc:
        raise PolicyConfigError(f"{path.name}: invalid front-matter: {exc}") from exc
    return PolicyDocumentFile(path, meta, hashlib.sha256(raw).hexdigest())


def load_policy_directory(directory: Path) -> list[PolicyDocumentFile]:
    paths = sorted(directory.glob("*.md"))
    if not paths:
        raise PolicyConfigError(f"No policy documents found in {directory}")
    files = [parse_policy_file(p) for p in paths]
    keys = [f.meta.doc_key for f in files]
    if len(set(keys)) != len(keys):
        raise PolicyConfigError("Duplicate doc_key among the policy documents")
    return files


def config_from_files(files: list[PolicyDocumentFile]) -> PolicyConfig:
    """The config these files would publish; raises `PolicyConfigError` if invalid."""
    return PolicyConfig.from_documents(
        (f.meta.doc_key, f.meta.version, f.stored_params) for f in files
    )
