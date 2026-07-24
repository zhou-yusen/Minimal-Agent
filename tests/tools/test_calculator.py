import json

import pytest

from minimal_agent.models import ToolCall, ToolContext, ToolErrorCode
from minimal_agent.tools.calculator import CalculatorTool
from minimal_agent.tools.registry import ToolRegistry


@pytest.fixture
def registry() -> ToolRegistry:
    tool_registry = ToolRegistry()
    tool_registry.register(CalculatorTool())
    return tool_registry


def make_call(expression: str) -> ToolCall:
    return ToolCall(
        id="calculator-call",
        name="calculator",
        arguments_json=json.dumps({"expression": expression}),
    )


def make_context() -> ToolContext:
    return ToolContext(user_id="user-1", session_id="session-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("8 + 2 - 3", 7),
        ("6 * 7 / 2", 21),
        ("(2 + 3) * 4", 20),
        ("-5 + +2", -3),
    ],
)
async def test_basic_arithmetic(
    registry: ToolRegistry, expression: str, expected: int | float
) -> None:
    result = await registry.execute(make_call(expression), make_context())

    assert result.ok is True
    assert result.output is not None
    assert result.output["result"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_division_by_zero_is_execution_error(registry: ToolRegistry) -> None:
    result = await registry.execute(make_call("1 / 0"), make_context())

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.EXECUTION_ERROR
    assert result.error.message == "division by zero"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "abs(1)",
        "(1).__class__",
        "[x for x in [1]]",
        "2 & 3",
        "2 + @",
    ],
)
async def test_malicious_or_unsupported_syntax_is_rejected(
    registry: ToolRegistry, expression: str
) -> None:
    result = await registry.execute(make_call(expression), make_context())

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.EXECUTION_ERROR


@pytest.mark.asyncio
async def test_overly_complex_expression_is_rejected(registry: ToolRegistry) -> None:
    expression = "+".join(["1"] * 40)

    result = await registry.execute(make_call(expression), make_context())

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.EXECUTION_ERROR
    assert "complex" in result.error.message


@pytest.mark.asyncio
async def test_large_numeric_result_is_rejected(registry: ToolRegistry) -> None:
    result = await registry.execute(
        make_call("1000000000000 * 1000000000000"),
        make_context(),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.EXECUTION_ERROR
