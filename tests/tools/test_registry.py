import json
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from minimal_agent.models import ToolCall, ToolContext, ToolErrorCode
from minimal_agent.tools.registry import ToolRegistry


class EchoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int


class EchoTool:
    name = "echo"
    description = "Return a validated integer."
    arguments_model = EchoArguments

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self.arguments_model.model_json_schema()

    async def execute(
        self, arguments: EchoArguments, context: ToolContext
    ) -> dict[str, int | str]:
        return {"value": arguments.value, "session_id": context.session_id}


class ExplodingTool(EchoTool):
    name = "explode"

    async def execute(
        self, arguments: EchoArguments, context: ToolContext
    ) -> dict[str, int]:
        del arguments, context
        raise RuntimeError("SECRET_PATH=C:/internal/private.txt")


class NonJsonTool(EchoTool):
    name = "non_json"

    async def execute(
        self, arguments: EchoArguments, context: ToolContext
    ) -> object:
        del arguments, context
        return object()


def make_call(name: str, arguments_json: str, call_id: str = "call-1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments_json=arguments_json)


def make_context() -> ToolContext:
    return ToolContext(user_id="user-1", session_id="session-1")


def test_register_and_definitions() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    definitions = registry.definitions()

    assert len(definitions) == 1
    assert definitions[0].name == "echo"
    assert definitions[0].description == "Return a validated integer."
    assert definitions[0].parameters_schema == EchoArguments.model_json_schema()


def test_duplicate_registration_is_rejected() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoTool())


@pytest.mark.asyncio
async def test_invalid_json_becomes_tool_result() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    result = await registry.execute(make_call("echo", "{"), make_context())

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_JSON


@pytest.mark.asyncio
@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
async def test_nonstandard_json_constants_are_rejected_before_execution(
    constant: str,
) -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    result = await registry.execute(
        make_call("echo", f'{{"x": {constant}}}'),
        make_context(),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_JSON


@pytest.mark.asyncio
async def test_unknown_tool_becomes_tool_result() -> None:
    result = await ToolRegistry().execute(
        make_call("missing", "{}"),
        make_context(),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.UNKNOWN_TOOL


@pytest.mark.asyncio
async def test_validation_error_becomes_json_serializable_tool_result() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    result = await registry.execute(
        make_call("echo", '{"value": "not-an-integer"}'),
        make_context(),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.VALIDATION_ERROR
    json.dumps(result.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_unknown_execution_exception_is_redacted() -> None:
    registry = ToolRegistry()
    registry.register(ExplodingTool())

    result = await registry.execute(
        make_call("explode", '{"value": 1}'),
        make_context(),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.EXECUTION_ERROR
    assert result.error.message == "tool execution failed"
    assert "SECRET_PATH" not in result.error.message
    assert "private.txt" not in result.error.message
    assert "Traceback" not in result.error.message


@pytest.mark.asyncio
async def test_successful_execution_preserves_call_id() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    result = await registry.execute(
        make_call("echo", '{"value": 7}', call_id="provider-call-42"),
        make_context(),
    )

    assert result.ok is True
    assert result.tool_call_id == "provider-call-42"
    assert result.output == {"value": 7, "session_id": "session-1"}
    json.dumps(result.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_non_json_tool_output_becomes_execution_error() -> None:
    registry = ToolRegistry()
    registry.register(NonJsonTool())

    result = await registry.execute(
        make_call("non_json", '{"value": 1}'),
        make_context(),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.EXECUTION_ERROR
    json.dumps(result.model_dump(mode="json"))
