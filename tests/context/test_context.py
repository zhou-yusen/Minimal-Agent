from collections.abc import Sequence
import pytest

from minimal_agent.context import ContextManager
from minimal_agent.errors import ContextWindowExceededError
from minimal_agent.models import (
    ConversationMessage,
    ContextBuildResult,
    CompressionStatus,
    LLMResponseType,
    LLMResult,
    MessageRole,
    SessionState,
    ToolCall,
    ToolDefinition,
)
from minimal_agent.runtime import AgentRuntime
from minimal_agent.tools.calculator import CalculatorTool
from minimal_agent.tools.registry import ToolRegistry
from tests.runtime.fakes import ScriptedFakeLLM


SYSTEM_PROMPT = "Use relevant context and tools."
OUTPUT_RESERVE = 20


def ordinary_turn(label: str, *, size: int = 0) -> list[ConversationMessage]:
    suffix = "x" * size
    return [
        ConversationMessage(
            role=MessageRole.USER,
            content=f"user-{label}-{suffix}",
        ),
        ConversationMessage(
            role=MessageRole.ASSISTANT,
            content=f"assistant-{label}-{suffix}",
        ),
    ]


def tool_turn(label: str, *, result_size: int = 0) -> list[ConversationMessage]:
    call_id = f"call-{label}"
    return [
        ConversationMessage(
            role=MessageRole.USER,
            content=f"tool-user-{label}",
        ),
        ConversationMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=[
                ToolCall(
                    id=call_id,
                    name="calculator",
                    arguments_json='{"expression":"2+2"}',
                )
            ],
        ),
        ConversationMessage(
            role=MessageRole.TOOL,
            content=(
                '{"ok":true,"output":{"result":"'
                + ("x" * result_size)
                + '"}}'
            ),
            tool_call_id=call_id,
            tool_name="calculator",
        ),
        ConversationMessage(
            role=MessageRole.ASSISTANT,
            content=f"tool-final-{label}",
        ),
    ]


def current_turn(text: str = "current-user") -> list[ConversationMessage]:
    return [ConversationMessage(role=MessageRole.USER, content=text)]


def state_with(*turns: Sequence[ConversationMessage]) -> SessionState:
    return SessionState(
        user_id="user-1",
        session_id="session-1",
        history=[message for turn in turns for message in turn],
    )


def manager(
    fake: ScriptedFakeLLM,
    *,
    limit: int = 10_000,
    trigger: int = 10_000,
    keep: int = 2,
) -> ContextManager:
    return ContextManager(
        llm=fake,
        context_token_limit=limit,
        compression_trigger=trigger,
        recent_turns_to_keep=keep,
    )


async def build(
    context_manager: ContextManager,
    state: SessionState,
    *,
    system_prompt: str = SYSTEM_PROMPT,
    tools: list[ToolDefinition] | None = None,
    max_output_tokens: int = OUTPUT_RESERVE,
) -> list[ConversationMessage]:
    result = await context_manager.build(
        state,
        system_prompt=system_prompt,
        tools=tools or [],
        max_output_tokens=max_output_tokens,
    )
    return result.messages


def contents(messages: Sequence[ConversationMessage]) -> list[str | None]:
    return [message.content for message in messages]


@pytest.mark.asyncio
async def test_small_context_keeps_all_history_without_summary_call() -> None:
    fake = ScriptedFakeLLM([])
    state = state_with(ordinary_turn("one"), current_turn())
    original_ids = [message.id for message in state.history]

    messages = await build(manager(fake), state)

    assert [message.id for message in messages] == original_ids
    assert fake.summary_requests == []


@pytest.mark.asyncio
async def test_ordinary_follow_up_sees_the_previous_turn() -> None:
    fake = ScriptedFakeLLM([])
    state = state_with(
        [
            ConversationMessage(role=MessageRole.USER, content="My name is Alice"),
            ConversationMessage(role=MessageRole.ASSISTANT, content="Hello Alice"),
        ],
        current_turn("What is my name?"),
    )

    messages = await build(manager(fake), state)

    assert contents(messages) == [
        "My name is Alice",
        "Hello Alice",
        "What is my name?",
    ]


@pytest.mark.asyncio
async def test_tool_result_follow_up_preserves_the_complete_pair() -> None:
    fake = ScriptedFakeLLM([])
    previous = tool_turn("weather")
    state = state_with(previous, current_turn("Use that result"))

    messages = await build(manager(fake), state)

    assert [message.id for message in messages[:4]] == [
        message.id for message in previous
    ]
    assert messages[1].tool_calls[0].id == messages[2].tool_call_id


@pytest.mark.asyncio
@pytest.mark.parametrize("inflated_component", ["system", "tools", "output"])
async def test_full_request_components_change_compression_trigger(
    inflated_component: str,
) -> None:
    state = state_with(
        ordinary_turn("one"),
        ordinary_turn("two"),
        ordinary_turn("three"),
        current_turn(),
    )
    base_messages = list(state.history)
    baseline_tokens = ContextManager.estimate_request_tokens(
        system_prompt="short",
        tools=[],
        messages=base_messages,
        max_output_tokens=5,
    )
    trigger = baseline_tokens + 1
    baseline_fake = ScriptedFakeLLM([])

    await build(
        manager(baseline_fake, trigger=trigger, keep=1),
        state.model_copy(deep=True),
        system_prompt="short",
        max_output_tokens=5,
    )
    assert baseline_fake.summary_requests == []

    system_prompt = "short"
    tools: list[ToolDefinition] = []
    output_tokens = 5
    if inflated_component == "system":
        system_prompt = "system-" + ("x" * 800)
    elif inflated_component == "tools":
        tools = [
            ToolDefinition(
                name="large_tool",
                description="x" * 800,
                parameters_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                },
            )
        ]
    else:
        output_tokens = 300

    inflated_fake = ScriptedFakeLLM([], summary_results=["compressed"])
    await build(
        manager(inflated_fake, trigger=trigger, keep=1),
        state.model_copy(deep=True),
        system_prompt=system_prompt,
        tools=tools,
        max_output_tokens=output_tokens,
    )

    assert len(inflated_fake.summary_requests) == 1


@pytest.mark.asyncio
async def test_successful_compression_updates_summary_and_boundary() -> None:
    fake = ScriptedFakeLLM([], summary_results=["summary-v1"])
    turns = [ordinary_turn(str(index)) for index in range(4)]
    state = state_with(*turns, current_turn())
    original_ids = [message.id for message in state.history]
    expected_candidate_ids = [message.id for turn in turns[:2] for message in turn]

    messages = await build(manager(fake, trigger=1, keep=2), state)

    request = fake.summary_requests[0]
    assert [message.id for message in request.messages] == expected_candidate_ids
    assert request.previous_summary is None
    assert "current-user" not in contents(request.messages)
    assert state.summary == "summary-v1"
    assert state.summary_up_to_message_id == turns[1][-1].id
    assert [message.id for message in state.history] == original_ids
    assert messages[0].content == (
        f"{ContextManager.SUMMARY_PREFIX}\nsummary-v1"
    )
    assert [message.id for message in messages[1:]] == [
        message.id for turn in turns[2:] for message in turn
    ] + [state.history[-1].id]


@pytest.mark.asyncio
async def test_rolling_summary_only_sends_new_messages_after_boundary() -> None:
    fake = ScriptedFakeLLM([], summary_results=["summary-v2"])
    already_summarized = ordinary_turn("old")
    new_turns = [ordinary_turn(str(index)) for index in range(3)]
    state = state_with(already_summarized, *new_turns, current_turn())
    state.summary = "summary-v1"
    state.summary_up_to_message_id = already_summarized[-1].id

    await build(manager(fake, trigger=1, keep=1), state)

    request = fake.summary_requests[0]
    request_ids = [message.id for message in request.messages]
    assert request.previous_summary == "summary-v1"
    assert request_ids == [
        message.id for turn in new_turns[:2] for message in turn
    ]
    assert not set(request_ids).intersection(
        message.id for message in already_summarized
    )
    assert state.summary == "summary-v2"
    assert state.summary_up_to_message_id == new_turns[1][-1].id


@pytest.mark.asyncio
async def test_budget_trimming_never_splits_a_tool_turn() -> None:
    fake = ScriptedFakeLLM([])
    complete_tool_turn = tool_turn("large", result_size=800)
    current = current_turn()
    current_limit = ContextManager.estimate_request_tokens(
        system_prompt=SYSTEM_PROMPT,
        tools=[],
        messages=current,
        max_output_tokens=OUTPUT_RESERVE,
    )
    state = state_with(complete_tool_turn, current)

    messages = await build(
        manager(fake, limit=current_limit, trigger=1, keep=4),
        state,
    )

    assert [message.id for message in messages] == [current[0].id]
    assert all(message.tool_call_id is None for message in messages)


@pytest.mark.asyncio
async def test_compression_exception_uses_bounded_recent_suffix() -> None:
    fake = ScriptedFakeLLM(
        [],
        summary_results=[RuntimeError("summary unavailable")],
    )
    covered = ordinary_turn("covered")
    unsummarized = [ordinary_turn(str(index), size=100) for index in range(3)]
    current = current_turn()
    state = state_with(covered, *unsummarized, current)
    state.summary = "existing summary"
    state.summary_up_to_message_id = covered[-1].id
    old_boundary = state.summary_up_to_message_id
    target_messages = [
        ConversationMessage(
            role=MessageRole.ASSISTANT,
            content=f"{ContextManager.SUMMARY_PREFIX}\nexisting summary",
        ),
        *unsummarized[-1],
        *current,
    ]
    limit = ContextManager.estimate_request_tokens(
        system_prompt=SYSTEM_PROMPT,
        tools=[],
        messages=target_messages,
        max_output_tokens=OUTPUT_RESERVE,
    )

    messages = await build(
        manager(fake, limit=limit, trigger=1, keep=1),
        state,
    )

    assert state.summary == "existing summary"
    assert state.summary_up_to_message_id == old_boundary
    assert messages[-1].id == current[0].id
    assert unsummarized[-1][0].id in [message.id for message in messages]
    assert unsummarized[0][0].id not in [message.id for message in messages]
    assert ContextManager.estimate_request_tokens(
        system_prompt=SYSTEM_PROMPT,
        tools=[],
        messages=messages,
        max_output_tokens=OUTPUT_RESERVE,
    ) <= limit


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_summary", ["", "   "])
async def test_empty_summary_is_a_non_destructive_compression_failure(
    empty_summary: str,
) -> None:
    fake = ScriptedFakeLLM([], summary_results=[empty_summary])
    turns = [ordinary_turn(str(index)) for index in range(3)]
    state = state_with(*turns, current_turn())
    original_ids = [message.id for message in state.history]

    messages = await build(manager(fake, trigger=1, keep=1), state)

    assert state.summary is None
    assert state.summary_up_to_message_id is None
    assert [message.id for message in state.history] == original_ids
    assert messages[-1].id == state.history[-1].id


@pytest.mark.asyncio
async def test_large_recent_turn_target_is_reduced_to_fit() -> None:
    fake = ScriptedFakeLLM([])
    turns = [ordinary_turn(str(index), size=500) for index in range(4)]
    current = current_turn()
    target = [*turns[-1], *current]
    limit = ContextManager.estimate_request_tokens(
        system_prompt=SYSTEM_PROMPT,
        tools=[],
        messages=target,
        max_output_tokens=OUTPUT_RESERVE,
    )
    state = state_with(*turns, current)

    messages = await build(
        manager(fake, limit=limit, trigger=1, keep=4),
        state,
    )

    assert [message.id for message in messages] == [
        message.id for message in target
    ]


@pytest.mark.asyncio
async def test_oversized_summary_is_omitted_but_remains_persisted() -> None:
    fake = ScriptedFakeLLM([])
    covered = ordinary_turn("covered")
    current = current_turn()
    state = state_with(covered, current)
    state.summary = "x" * 2_000
    state.summary_up_to_message_id = covered[-1].id
    limit = ContextManager.estimate_request_tokens(
        system_prompt=SYSTEM_PROMPT,
        tools=[],
        messages=current,
        max_output_tokens=OUTPUT_RESERVE,
    )

    messages = await build(
        manager(fake, limit=limit, trigger=1, keep=1),
        state,
    )

    assert [message.id for message in messages] == [current[0].id]
    assert state.summary == "x" * 2_000
    assert state.summary_up_to_message_id == covered[-1].id


@pytest.mark.asyncio
async def test_mandatory_current_request_that_cannot_fit_fails_clearly() -> None:
    fake = ScriptedFakeLLM([])
    state = state_with(current_turn("x" * 1_000))
    required = ContextManager.estimate_request_tokens(
        system_prompt=SYSTEM_PROMPT,
        tools=[],
        messages=state.history,
        max_output_tokens=OUTPUT_RESERVE,
    )

    with pytest.raises(ContextWindowExceededError):
        await build(
            manager(fake, limit=required - 1, trigger=1),
            state,
        )

    assert state.history[0].content == "x" * 1_000


class CountingContextManager:
    def __init__(self) -> None:
        self.calls = 0

    async def build(
        self,
        session: SessionState,
        *,
        system_prompt: str,
        tools: list[ToolDefinition],
        max_output_tokens: int,
    ) -> ContextBuildResult:
        del system_prompt, tools, max_output_tokens
        self.calls += 1
        return ContextBuildResult(
            messages=list(session.history),
            estimated_tokens=0,
            compression_attempted=False,
            compression_status=CompressionStatus.NOT_NEEDED,
            summary_updated=False,
        )


@pytest.mark.asyncio
async def test_runtime_builds_context_once_then_uses_tool_only_continuations(
) -> None:
    calls = [
        ToolCall(
            id="call-1",
            name="calculator",
            arguments_json='{"expression":"1+1"}',
        ),
        ToolCall(
            id="call-2",
            name="calculator",
            arguments_json='{"expression":"2+2"}',
        ),
    ]
    fake = ScriptedFakeLLM(
        [
            LLMResult(
                assistant_message=ConversationMessage(
                    role=MessageRole.ASSISTANT,
                    tool_calls=[calls[0]],
                ),
                response_type=LLMResponseType.TOOL_CALLS,
                provider_response_id="resp-1",
            ),
            LLMResult(
                assistant_message=ConversationMessage(
                    role=MessageRole.ASSISTANT,
                    tool_calls=[calls[1]],
                ),
                response_type=LLMResponseType.TOOL_CALLS,
                provider_response_id="resp-2",
            ),
            final_result("Done"),
        ]
    )
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    context_manager = CountingContextManager()
    runtime = AgentRuntime(
        llm=fake,
        tools=registry,
        context_manager=context_manager,  # type: ignore[arg-type]
        system_prompt=SYSTEM_PROMPT,
        max_steps=4,
        max_output_tokens=OUTPUT_RESERVE,
    )
    state = SessionState(user_id="user-1", session_id="session-1")

    await runtime.run(state, "Calculate twice")

    assert context_manager.calls == 1
    assert [request.continuation_id for request in fake.requests] == [
        None,
        "resp-1",
        "resp-2",
    ]
    assert [message.role for message in fake.requests[1].messages] == [
        MessageRole.TOOL
    ]
    assert [message.role for message in fake.requests[2].messages] == [
        MessageRole.TOOL
    ]


def final_result(text: str) -> LLMResult:
    return LLMResult(
        assistant_message=ConversationMessage(
            role=MessageRole.ASSISTANT,
            content=text,
        ),
        response_type=LLMResponseType.FINAL,
    )


@pytest.mark.asyncio
async def test_compression_never_changes_raw_history_messages() -> None:
    fake = ScriptedFakeLLM([], summary_results=["summary"])
    state = state_with(
        ordinary_turn("one"),
        ordinary_turn("two"),
        ordinary_turn("three"),
        current_turn(),
    )
    original_dump = [message.model_dump() for message in state.history]

    await build(manager(fake, trigger=1, keep=1), state)

    assert [message.model_dump() for message in state.history] == original_dump
