from datetime import datetime

import pytest
from pydantic import ValidationError

from minimal_agent.models import (
    ConversationMessage,
    LLMResponseType,
    LLMResult,
    LLMRequest,
    MessageRole,
    SessionState,
    SummaryRequest,
    ToolCall,
    ToolDefinition,
    ToolError,
    ToolErrorCode,
    ToolResult,
)


def test_summary_request_has_no_tool_schema_surface() -> None:
    request = SummaryRequest(
        messages=[ConversationMessage(role=MessageRole.USER, content="summarize me")]
    )

    assert "tools" not in request.model_dump()
    assert "tools" not in SummaryRequest.model_json_schema()["properties"]


def test_completion_request_carries_tools_and_response_reserve() -> None:
    request = LLMRequest(
        system_prompt="Use tools when appropriate.",
        messages=[ConversationMessage(role=MessageRole.USER, content="2 + 2")],
        tools=[
            ToolDefinition(
                name="calculator",
                description="Evaluate arithmetic.",
                parameters_schema={"type": "object", "properties": {}},
            )
        ],
        max_output_tokens=1_000,
    )

    assert request.tools[0].name == "calculator"
    assert request.max_output_tokens == 1_000


def test_provider_state_is_not_part_of_request_or_session() -> None:
    request = LLMRequest(
        system_prompt="Continue the active run.",
        messages=[ConversationMessage(role=MessageRole.USER, content="Continue")],
        tools=[],
        max_output_tokens=100,
    )
    session = SessionState(user_id="user-1", session_id="session-1")

    assert "continuation_id" not in request.model_dump()
    assert "continuation_id" not in session.model_dump()
    assert "provider_response_id" not in session.model_dump()


def test_llm_request_rejects_unknown_tool_choice() -> None:
    with pytest.raises(ValidationError):
        LLMRequest(
            system_prompt="system",
            messages=[],
            tools=[],
            max_output_tokens=100,
            tool_choice="sometimes",  # type: ignore[arg-type]
        )


def test_tool_message_requires_call_correlation() -> None:
    with pytest.raises(ValidationError):
        ConversationMessage(role=MessageRole.TOOL, content="4")

    message = ConversationMessage(
        role=MessageRole.TOOL,
        content="4",
        tool_call_id="call_123",
        tool_name="calculator",
    )

    assert message.tool_call_id == "call_123"


def test_tool_call_preserves_provider_call_id() -> None:
    call = ToolCall(id="call_provider_456", name="search", arguments_json="{}")

    assert call.id == "call_provider_456"


def test_session_defaults_are_isolated_and_timezone_aware() -> None:
    first = SessionState(user_id="user-a", session_id="one")
    second = SessionState(user_id="user-a", session_id="two")

    first.tool_state["value"] = 1

    assert second.tool_state == {}
    assert isinstance(first.created_at, datetime)
    assert first.created_at.tzinfo is not None


def test_empty_assistant_message_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ConversationMessage(role=MessageRole.ASSISTANT)

    with pytest.raises(ValidationError):
        ConversationMessage(role=MessageRole.ASSISTANT, content="   ")


def test_final_llm_result_requires_text_and_forbids_tool_calls() -> None:
    final = LLMResult(
        assistant_message=ConversationMessage(
            role=MessageRole.ASSISTANT,
            content="Done.",
        ),
        response_type=LLMResponseType.FINAL,
    )
    assert final.assistant_message.content == "Done."

    with pytest.raises(ValidationError):
        LLMResult(
            assistant_message=ConversationMessage(
                role=MessageRole.ASSISTANT,
                tool_calls=[ToolCall(id="call-1", name="todo", arguments_json="{}")],
            ),
            response_type=LLMResponseType.FINAL,
        )


def test_tool_calls_llm_result_requires_a_tool_call() -> None:
    with pytest.raises(ValidationError):
        LLMResult(
            assistant_message=ConversationMessage(
                role=MessageRole.ASSISTANT,
                content="I should use a tool.",
            ),
            response_type=LLMResponseType.TOOL_CALLS,
        )


def test_successful_tool_result_cannot_contain_error() -> None:
    with pytest.raises(ValidationError):
        ToolResult(
            tool_call_id="call-1",
            tool_name="test",
            ok=True,
            output={"value": 1},
            error=ToolError(
                code=ToolErrorCode.EXECUTION_ERROR,
                message="unexpected",
            ),
            latency_ms=0,
        )


def test_failed_tool_result_requires_error() -> None:
    with pytest.raises(ValidationError):
        ToolResult(
            tool_call_id="call-1",
            tool_name="test",
            ok=False,
            latency_ms=0,
        )
