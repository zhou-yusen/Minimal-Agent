import json
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from minimal_agent.context import ContextManager
from minimal_agent.errors import LLMProtocolError
from minimal_agent.models import (
    AgentRunStatus,
    ConversationMessage,
    LLMResponseType,
    LLMResult,
    MessageRole,
    SessionState,
    ToolCall,
    ToolContext,
    ToolErrorCode,
)
from minimal_agent.runtime import AgentRuntime
from minimal_agent.tools.calculator import CalculatorTool
from minimal_agent.tools.registry import ToolRegistry
from minimal_agent.tools.search import SearchTool
from minimal_agent.tools.todo import TodoTool
from tests.runtime.fakes import ScriptedFakeLLM


SYSTEM_PROMPT = "Use tools when needed and answer with visible text."
MAX_OUTPUT_TOKENS = 400


class ValueArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int


class ExplodingTool:
    name = "explode"
    description = "Raise an exception for a runtime boundary test."
    arguments_model = ValueArguments

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self.arguments_model.model_json_schema()

    async def execute(
        self,
        arguments: ValueArguments,
        context: ToolContext,
    ) -> dict[str, int]:
        del arguments, context
        raise RuntimeError("private implementation detail")


def final_result(text: str = "Done") -> LLMResult:
    return LLMResult(
        assistant_message=ConversationMessage(
            role=MessageRole.ASSISTANT,
            content=text,
        ),
        response_type=LLMResponseType.FINAL,
    )


def tool_result(
    *calls: ToolCall,
    response_id: str | None,
) -> LLMResult:
    return LLMResult(
        assistant_message=ConversationMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=list(calls),
        ),
        response_type=LLMResponseType.TOOL_CALLS,
        provider_response_id=response_id,
    )


def call(
    name: str,
    arguments_json: str,
    call_id: str,
) -> ToolCall:
    return ToolCall(
        id=call_id,
        name=name,
        arguments_json=arguments_json,
    )


def standard_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(SearchTool())
    registry.register(TodoTool())
    return registry


def session() -> SessionState:
    return SessionState(user_id="user-1", session_id="session-1")


def runtime(
    fake: ScriptedFakeLLM,
    *,
    registry: ToolRegistry | None = None,
    max_steps: int = 6,
) -> AgentRuntime:
    return AgentRuntime(
        llm=fake,
        tools=registry or standard_registry(),
        context_manager=ContextManager(
            llm=fake,
            context_token_limit=100_000,
            compression_trigger=100_000,
            recent_turns_to_keep=4,
        ),
        system_prompt=SYSTEM_PROMPT,
        max_steps=max_steps,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


def decoded_tool_message(message: ConversationMessage) -> dict[str, Any]:
    assert message.role is MessageRole.TOOL
    assert message.content is not None
    return json.loads(message.content)


@pytest.mark.asyncio
async def test_direct_answer_completes_in_one_step_without_tools() -> None:
    fake = ScriptedFakeLLM([final_result("Hello")])
    state = session()

    result = await runtime(fake).run(state, "Hi")

    assert result.status is AgentRunStatus.COMPLETED
    assert result.final_answer == "Hello"
    assert result.loop_steps == 1
    assert len(fake.requests) == 1
    assert [message.role for message in state.history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]


@pytest.mark.asyncio
async def test_one_tool_round_travels_llm_tool_llm() -> None:
    fake = ScriptedFakeLLM(
        [
            tool_result(
                call("calculator", '{"expression":"2+2"}', "call-calc"),
                response_id="resp-1",
            ),
            final_result("Four"),
        ]
    )
    state = session()

    result = await runtime(fake).run(state, "Calculate two plus two")

    assert result.status is AgentRunStatus.COMPLETED
    assert result.loop_steps == 2
    assert [message.role for message in state.history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    tool_message = state.history[2]
    assert tool_message.tool_call_id == "call-calc"
    assert tool_message.tool_name == "calculator"
    assert decoded_tool_message(tool_message) == {
        "ok": True,
        "output": {"expression": "2+2", "result": 4},
    }
    assert "latency_ms" not in tool_message.content


@pytest.mark.asyncio
async def test_two_sequential_tool_rounds_are_three_llm_steps() -> None:
    fake = ScriptedFakeLLM(
        [
            tool_result(
                call("calculator", '{"expression":"6*7"}', "call-1"),
                response_id="resp-1",
            ),
            tool_result(
                call("search", '{"query":"agent","top_k":1}', "call-2"),
                response_id="resp-2",
            ),
            final_result("Finished both tasks"),
        ]
    )
    state = session()

    result = await runtime(fake).run(state, "Do both")

    assert result.loop_steps == 3
    assert len(fake.requests) == 3
    assert [[message.role for message in request.messages] for request in fake.requests] == [
        [MessageRole.USER],
        [MessageRole.USER, MessageRole.ASSISTANT, MessageRole.TOOL],
        [
            MessageRole.USER,
            MessageRole.ASSISTANT,
            MessageRole.TOOL,
            MessageRole.ASSISTANT,
            MessageRole.TOOL,
        ],
    ]
    tool_names = [
        message.tool_name
        for message in state.history
        if message.role is MessageRole.TOOL
    ]
    assert tool_names == [
        "calculator",
        "search",
    ]


@pytest.mark.asyncio
async def test_multiple_tool_calls_execute_in_order_before_one_next_llm_call() -> None:
    fake = ScriptedFakeLLM(
        [
            tool_result(
                call("calculator", '{"expression":"1+2"}', "call-a"),
                call("search", '{"query":"Python","top_k":1}', "call-b"),
                response_id="resp-multi",
            ),
            final_result("Both complete"),
        ]
    )

    result = await runtime(fake).run(session(), "Use both tools")

    assert result.loop_steps == 2
    assert len(fake.requests) == 2
    next_messages = fake.requests[1].messages
    tool_messages = [
        message for message in next_messages if message.role is MessageRole.TOOL
    ]
    assert [message.tool_call_id for message in tool_messages] == [
        "call-a",
        "call-b",
    ]
    assert [message.tool_name for message in tool_messages] == [
        "calculator",
        "search",
    ]


@pytest.mark.asyncio
async def test_invalid_tool_json_is_returned_to_llm() -> None:
    fake = ScriptedFakeLLM(
        [
            tool_result(
                call("calculator", "{", "call-invalid"),
                response_id="resp-invalid",
            ),
            final_result("The tool arguments were invalid"),
        ]
    )

    result = await runtime(fake).run(session(), "Calculate")

    assert result.status is AgentRunStatus.COMPLETED
    payload = decoded_tool_message(fake.requests[1].messages[-1])
    assert payload["ok"] is False
    assert payload["error"]["code"] == ToolErrorCode.INVALID_JSON
    assert "latency_ms" not in payload


@pytest.mark.asyncio
async def test_unknown_tool_error_is_returned_to_llm() -> None:
    fake = ScriptedFakeLLM(
        [
            tool_result(
                call("missing", "{}", "call-missing"),
                response_id="resp-missing",
            ),
            final_result("That tool is unavailable"),
        ]
    )

    await runtime(fake).run(session(), "Use a missing tool")

    payload = decoded_tool_message(fake.requests[1].messages[-1])
    assert payload["error"]["code"] == ToolErrorCode.UNKNOWN_TOOL


@pytest.mark.asyncio
async def test_tool_execution_exception_is_redacted_and_returned_to_llm() -> None:
    registry = ToolRegistry()
    registry.register(ExplodingTool())
    fake = ScriptedFakeLLM(
        [
            tool_result(
                call("explode", '{"value":1}', "call-explode"),
                response_id="resp-explode",
            ),
            final_result("The tool failed safely"),
        ]
    )

    await runtime(fake, registry=registry).run(session(), "Run it")

    payload = decoded_tool_message(fake.requests[1].messages[-1])
    assert payload["error"] == {
        "code": ToolErrorCode.EXECUTION_ERROR,
        "message": "tool execution failed",
    }
    assert "private implementation detail" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_llm_can_repair_invalid_arguments_on_next_round() -> None:
    fake = ScriptedFakeLLM(
        [
            tool_result(
                call("calculator", '{"expression":[]}', "call-bad"),
                response_id="resp-1",
            ),
            tool_result(
                call("calculator", '{"expression":"8/2"}', "call-fixed"),
                response_id="resp-2",
            ),
            final_result("The answer is 4"),
        ]
    )

    result = await runtime(fake).run(session(), "Calculate eight divided by two")

    assert result.loop_steps == 3
    first_error = decoded_tool_message(fake.requests[1].messages[-1])
    repaired = decoded_tool_message(fake.requests[2].messages[-1])
    assert first_error["error"]["code"] == ToolErrorCode.VALIDATION_ERROR
    assert repaired == {
        "ok": True,
        "output": {"expression": "8/2", "result": 4.0},
    }


@pytest.mark.asyncio
async def test_max_steps_executes_last_calls_then_stops_without_extra_llm_call(
) -> None:
    fake = ScriptedFakeLLM(
        [
            tool_result(
                call("calculator", f'{{"expression":"{number}+1"}}', f"call-{number}"),
                response_id=f"resp-{number}",
            )
            for number in range(1, 4)
        ]
    )
    state = session()

    result = await runtime(fake, max_steps=3).run(state, "Keep calculating")

    assert result.status is AgentRunStatus.MAX_STEPS
    assert result.loop_steps == 3
    assert len(fake.requests) == 3
    assert state.history[-2].tool_call_id == "call-3"
    assert decoded_tool_message(state.history[-2])["ok"] is True
    assert state.history[-1].role is MessageRole.ASSISTANT
    assert state.history[-1].content == AgentRuntime.MAX_STEPS_MESSAGE
    assert result.final_answer == AgentRuntime.MAX_STEPS_MESSAGE


@pytest.mark.asyncio
async def test_tool_calls_do_not_require_provider_response_id() -> None:
    fake = ScriptedFakeLLM(
        [
            tool_result(
                call("todo", '{"action":"add","text":"Must run"}', "call-1"),
                response_id=None,
            ),
            final_result("Added"),
        ]
    )
    state = session()

    result = await runtime(fake).run(state, "Add a todo")

    assert result.status is AgentRunStatus.COMPLETED
    assert state.tool_state["todo"]["items"][0]["text"] == "Must run"


@pytest.mark.asyncio
async def test_active_run_replays_bounded_context_call_and_result() -> None:
    fake = ScriptedFakeLLM(
        [
            tool_result(
                call("calculator", '{"expression":"3+4"}', "call-1"),
                response_id="resp-1",
            ),
            final_result("Seven"),
        ]
    )
    state = session()
    state.history.extend(
        [
            ConversationMessage(role=MessageRole.USER, content="Earlier question"),
            ConversationMessage(role=MessageRole.ASSISTANT, content="Earlier answer"),
        ]
    )
    expected_definitions = standard_registry().definitions()

    await runtime(fake).run(state, "Current question")

    first, second = fake.requests
    assert [message.content for message in first.messages] == [
        "Earlier question",
        "Earlier answer",
        "Current question",
    ]
    assert [message.role for message in second.messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]
    assert second.messages[-2].tool_calls[0].id == "call-1"
    assert second.messages[-1].tool_call_id == "call-1"
    assert first.system_prompt == second.system_prompt == SYSTEM_PROMPT
    assert first.tools == second.tools == expected_definitions
    assert first.max_output_tokens == second.max_output_tokens == MAX_OUTPUT_TOKENS


@pytest.mark.asyncio
async def test_two_tool_rounds_replay_the_complete_active_sequence() -> None:
    fake = ScriptedFakeLLM(
        [
            tool_result(
                call("calculator", '{"expression":"1+1"}', "call-1"),
                response_id="resp-1",
            ),
            tool_result(
                call("calculator", '{"expression":"2+2"}', "call-2"),
                response_id="resp-2",
            ),
            final_result("Done"),
        ]
    )

    await runtime(fake).run(session(), "Two calculations")

    assert [message.role for message in fake.requests[2].messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]


@pytest.mark.asyncio
async def test_todo_tool_mutates_the_passed_session_state_immediately() -> None:
    fake = ScriptedFakeLLM(
        [
            tool_result(
                call(
                    "todo",
                    '{"action":"add","text":"Write Phase 4B"}',
                    "call-todo",
                ),
                response_id="resp-todo",
            ),
            final_result("Todo added"),
        ]
    )
    state = session()

    await runtime(fake).run(state, "Add the todo")

    assert state.tool_state["todo"]["items"] == [
        {
            "id": "todo-1",
            "text": "Write Phase 4B",
            "completed": False,
        }
    ]
