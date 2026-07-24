"""Real DeepSeek protocol tests.

These tests incur a small API cost and run only when RUN_LLM_INTEGRATION=1 and
DEEPSEEK_API_KEY are both present in the process environment.
"""

from __future__ import annotations

import json
import os

import pytest

from minimal_agent.config import Settings
from minimal_agent.context import ContextManager
from minimal_agent.llm.deepseek_client import DeepSeekChatClient
from minimal_agent.models import (
    AgentRunStatus,
    ConversationMessage,
    LLMRequest,
    LLMResponseType,
    MessageRole,
    SessionState,
    ToolCall,
    ToolDefinition,
    TraceEventType,
)
from minimal_agent.runtime import AgentRuntime
from minimal_agent.tools.calculator import CalculatorTool
from minimal_agent.tools.registry import ToolRegistry
from minimal_agent.tracing import InMemoryTraceSink


pytestmark = pytest.mark.integration

SYSTEM_PROMPT = "Use the supplied tools when requested, then answer concisely."
ECHO_TOOL = ToolDefinition(
    name="integration_echo",
    description="Return the supplied text for a provider protocol test.",
    parameters_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
)


def require_real_deepseek() -> Settings:
    """Gate every real network test without reading a local dotenv file."""
    if os.getenv("RUN_LLM_INTEGRATION") != "1":
        pytest.skip("set RUN_LLM_INTEGRATION=1 to enable real DeepSeek tests")
    if not os.getenv("DEEPSEEK_API_KEY"):
        pytest.skip("DEEPSEEK_API_KEY is unavailable in the process environment")
    return Settings.from_env()


def request(
    messages: list[ConversationMessage],
    *,
    tools: list[ToolDefinition],
    tool_choice: str | None,
    max_output_tokens: int = 128,
) -> LLMRequest:
    return LLMRequest(
        system_prompt=SYSTEM_PROMPT,
        messages=messages,
        tools=tools,
        max_output_tokens=max_output_tokens,
        tool_choice=tool_choice,
    )


async def forced_echo_call(
    client: DeepSeekChatClient,
) -> tuple[ConversationMessage, ToolCall]:
    user = ConversationMessage(
        role=MessageRole.USER,
        content='Use the available tool with the text "protocol-check".',
    )
    result = await client.complete(
        request([user], tools=[ECHO_TOOL], tool_choice="required")
    )
    assert result.response_type is LLMResponseType.TOOL_CALLS
    assert result.assistant_message.tool_calls
    call = result.assistant_message.tool_calls[0]
    assert call.name == "integration_echo"
    assert call.id.strip()
    arguments = json.loads(call.arguments_json)
    assert isinstance(arguments.get("text"), str)
    return user, call


def echo_result_message(call: ToolCall) -> ConversationMessage:
    return ConversationMessage(
        role=MessageRole.TOOL,
        content=json.dumps(
            {"ok": True, "output": {"echo": "protocol-check"}},
            separators=(",", ":"),
        ),
        tool_call_id=call.id,
        tool_name=call.name,
    )


@pytest.mark.asyncio
async def test_real_direct_final() -> None:
    settings = require_real_deepseek()
    client = DeepSeekChatClient(settings)

    result = await client.complete(
        request(
            [
                ConversationMessage(
                    role=MessageRole.USER,
                    content="Reply with one short greeting.",
                )
            ],
            tools=[],
            tool_choice=None,
        )
    )

    assert result.response_type is LLMResponseType.FINAL
    assert result.assistant_message.content
    if result.provider_response_id is not None:
        assert result.provider_response_id.strip()
    assert result.reasoning_present is False
    if result.usage is not None:
        assert result.usage.input_tokens >= 0
        assert result.usage.output_tokens >= 0


@pytest.mark.asyncio
async def test_real_forced_tool_call() -> None:
    client = DeepSeekChatClient(require_real_deepseek())

    _, call = await forced_echo_call(client)

    assert call.name == "integration_echo"
    assert call.id.strip()


@pytest.mark.asyncio
async def test_real_tool_result_round_trip_uses_same_call_id() -> None:
    client = DeepSeekChatClient(require_real_deepseek())
    user, call = await forced_echo_call(client)
    assistant = ConversationMessage(
        role=MessageRole.ASSISTANT,
        tool_calls=[call],
    )
    tool_result = echo_result_message(call)

    result = await client.complete(
        request(
            [user, assistant, tool_result],
            tools=[ECHO_TOOL],
            tool_choice="none",
        )
    )

    assert tool_result.tool_call_id == call.id
    assert result.response_type is LLMResponseType.FINAL
    assert result.assistant_message.content


@pytest.mark.asyncio
async def test_real_agent_runtime_calculator_smoke() -> None:
    client = DeepSeekChatClient(require_real_deepseek())
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    trace = InMemoryTraceSink()
    runtime = AgentRuntime(
        llm=client,
        tools=registry,
        context_manager=ContextManager(
            llm=client,
            context_token_limit=8_000,
            compression_trigger=6_000,
            recent_turns_to_keep=4,
        ),
        system_prompt=SYSTEM_PROMPT,
        max_steps=3,
        max_output_tokens=256,
        trace_sink=trace,
    )
    session = SessionState(user_id="integration-user", session_id="calculator")

    result = await runtime.run(
        session,
        "Use the calculator tool to calculate 1234567 * 89123, then give me "
        "the result.",
    )

    assert result.status is AgentRunStatus.COMPLETED
    assert session.history[0].role is MessageRole.USER
    call_messages = [message for message in session.history if message.tool_calls]
    assert call_messages
    calculator_calls = [
        call
        for message in call_messages
        for call in message.tool_calls
        if call.name == "calculator"
    ]
    assert calculator_calls
    tool_messages = [
        message
        for message in session.history
        if message.role is MessageRole.TOOL and message.tool_name == "calculator"
    ]
    assert tool_messages
    assert tool_messages[0].tool_call_id == calculator_calls[0].id
    assert session.history[-1].role is MessageRole.ASSISTANT
    assert session.history[-1].content
    event_types = {event.event_type for event in trace.events}
    assert {
        TraceEventType.LLM_REQUEST,
        TraceEventType.LLM_RESPONSE,
        TraceEventType.TOOL_START,
        TraceEventType.TOOL_RESULT,
        TraceEventType.RUN_FINISH,
    } <= event_types


@pytest.mark.asyncio
async def test_real_cross_turn_local_history_replay() -> None:
    client = DeepSeekChatClient(require_real_deepseek())
    first_user, call = await forced_echo_call(client)
    first_assistant_call = ConversationMessage(
        role=MessageRole.ASSISTANT,
        tool_calls=[call],
    )
    first_tool_result = echo_result_message(call)
    first_final_result = await client.complete(
        request(
            [first_user, first_assistant_call, first_tool_result],
            tools=[ECHO_TOOL],
            tool_choice="none",
        )
    )
    assert first_final_result.response_type is LLMResponseType.FINAL

    local_history = [
        first_user,
        first_assistant_call,
        first_tool_result,
        first_final_result.assistant_message,
        ConversationMessage(
            role=MessageRole.USER,
            content="What tool was used in the previous turn?",
        ),
    ]
    second_turn = await client.complete(
        request(local_history, tools=[ECHO_TOOL], tool_choice="none")
    )

    assert second_turn.response_type is LLMResponseType.FINAL
    assert second_turn.assistant_message.content
    assert second_turn.reasoning_present is False
