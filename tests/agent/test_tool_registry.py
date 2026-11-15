"""The agent's security frontier: identity is injected, the allowlist is explicit."""

from app.agent.tools.registry import (
    ALL_TOOLS,
    FORBIDDEN_PARAMS,
    FORBIDDEN_TOOL_WORDS,
    LLM_BOUND_TOOLS,
    bound_specs,
)


def _properties(schema: dict[str, object]) -> set[str]:
    params = schema["function"]["parameters"]  # type: ignore[index]
    return set(params.get("properties", {}))  # type: ignore[attr-defined]


def test_no_tool_accepts_customer_id_or_role() -> None:
    for spec in ALL_TOOLS.values():
        assert not (_properties(spec.openai_schema()) & FORBIDDEN_PARAMS), spec.name


def test_bound_tools_are_known_and_read_only() -> None:
    assert set(LLM_BOUND_TOOLS) <= set(ALL_TOOLS)
    assert "escalate_to_human" not in LLM_BOUND_TOOLS
    assert len(bound_specs()) == len(LLM_BOUND_TOOLS)


def test_registry_has_no_approval_payment_or_notes_tools() -> None:
    for name in ALL_TOOLS:
        assert not any(word in name for word in FORBIDDEN_TOOL_WORDS), name
    assert "create_refund_request" not in ALL_TOOLS
    assert "cancel_refund_request" not in ALL_TOOLS


def test_argument_schemas_forbid_extra_fields() -> None:
    for spec in ALL_TOOLS.values():
        assert spec.args.model_config.get("extra") == "forbid", spec.name
        assert (
            spec.openai_schema()["function"]["parameters"].get("additionalProperties")
            is False
        )
