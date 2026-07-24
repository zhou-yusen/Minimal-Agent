import json

import pytest

from minimal_agent.context import ContextManager
from minimal_agent.errors import LLMProviderError, LLMTimeoutError, SessionStoreError
from minimal_agent.models import (
    ConversationMessage,
    LLMResponseType,
    LLMResult,
    MessageRole,
    SessionState,
    ToolCall,
    TraceEventType,
)
from minimal_agent.runtime import AgentRuntime
from minimal_agent.service import AgentService
from minimal_agent.sessions.sqlite import SQLiteSessionStore
from minimal_agent.tools.registry import ToolRegistry
from minimal_agent.tools.todo import TodoTool
from minimal_agent.tracing import InMemoryTraceSink
from tests.runtime.fakes import ScriptedFakeLLM
from tests.runtime.test_checkpoints import RecordingCheckpoint


def final(text: str = "Done") -> LLMResult:
    return LLMResult(
        assistant_message=ConversationMessage(
            role=MessageRole.ASSISTANT,
            content=text,
        ),
        response_type=LLMResponseType.FINAL,
    )


def todo_call(text: str = "Durable todo") -> LLMResult:
    return LLMResult(
        assistant_message=ConversationMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=[
                ToolCall(
                    id="todo-call",
                    name="todo",
                    arguments_json=json.dumps({"action": "add", "text": text}),
                )
            ],
        ),
        response_type=LLMResponseType.TOOL_CALLS,
        provider_response_id="resp-tool",
    )


def make_runtime(
    fake: ScriptedFakeLLM,
    *,
    sink: InMemoryTraceSink | None = None,
) -> AgentRuntime:
    registry = ToolRegistry()
    registry.register(TodoTool())
    return AgentRuntime(
        llm=fake,
        tools=registry,
        context_manager=ContextManager(
            llm=fake,
            context_token_limit=100_000,
            compression_trigger=100_000,
            recent_turns_to_keep=4,
        ),
        system_prompt="Use tools when useful.",
        max_steps=4,
        max_output_tokens=200,
        trace_sink=sink,
    )


@pytest.mark.asyncio
async def test_user_only_stale_turn_is_sealed_before_new_user() -> None:
    old_text = "OLD_PRIVATE_USER_TEXT"
    session = SessionState(
        user_id="user",
        session_id="session",
        history=[ConversationMessage(role=MessageRole.USER, content=old_text)],
    )
    checkpoint = RecordingCheckpoint()
    sink = InMemoryTraceSink()

    await make_runtime(ScriptedFakeLLM([final("New answer")]), sink=sink).run(
        session,
        "New question",
        checkpoint=checkpoint,
    )

    assert [message.role for message in session.history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert session.history[1].content == AgentRuntime.INTERRUPTED_TURN_MESSAGE
    assert [len(snapshot.history) for snapshot in checkpoint.snapshots] == [2, 3, 4]
    recovery = next(
        event for event in sink.events if event.event_type is TraceEventType.RECOVERY
    )
    assert recovery.recovery_kind == "interrupted_turn"
    assert recovery.previous_terminal_role is MessageRole.USER
    assert old_text not in recovery.model_dump_json()


@pytest.mark.asyncio
async def test_tool_batch_stale_turn_is_sealed_without_replaying_tool() -> None:
    session = SessionState(
        user_id="user",
        session_id="session",
        history=[
            ConversationMessage(role=MessageRole.USER, content="Old request"),
            ConversationMessage(
                role=MessageRole.ASSISTANT,
                tool_calls=[
                    ToolCall(
                        id="old-call",
                        name="todo",
                        arguments_json='{"action":"add","text":"Existing"}',
                    )
                ],
            ),
            ConversationMessage(
                role=MessageRole.TOOL,
                content='{"ok":true,"output":{"id":"todo-1"}}',
                tool_call_id="old-call",
                tool_name="todo",
            ),
        ],
        tool_state={
            "todo": {
                "next_id": 2,
                "items": [
                    {"id": "todo-1", "text": "Existing", "completed": False}
                ],
            }
        },
    )
    fake = ScriptedFakeLLM([final("Continued")])

    await make_runtime(fake).run(session, "New request")

    assert len(fake.requests) == 1
    assert [message.role for message in fake.requests[0].messages] == [
        MessageRole.ASSISTANT,
        MessageRole.USER,
    ]
    assert all(message.tool_call_id is None for message in fake.requests[0].messages)
    assert len(session.tool_state["todo"]["items"]) == 1
    assert session.history[3].content == AgentRuntime.INTERRUPTED_TURN_MESSAGE
    assert session.history[4].content == "New request"


@pytest.mark.asyncio
async def test_recovery_checkpoint_failure_rejects_new_user_and_llm() -> None:
    session = SessionState(
        user_id="user",
        session_id="session",
        history=[ConversationMessage(role=MessageRole.USER, content="Old")],
    )
    fake = ScriptedFakeLLM([final()])
    checkpoint = RecordingCheckpoint(fail_on=1)
    sink = InMemoryTraceSink()

    with pytest.raises(SessionStoreError):
        await make_runtime(fake, sink=sink).run(
            session,
            "Must not append",
            checkpoint=checkpoint,
        )

    assert [message.content for message in session.history] == [
        "Old",
        AgentRuntime.INTERRUPTED_TURN_MESSAGE,
    ]
    assert fake.requests == []
    error = sink.events[-1]
    assert error.event_type is TraceEventType.ERROR
    assert error.error["stage"] == "recovery_checkpoint"
    assert error.error["code"] == "session_store"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_text",
    ["Normal final", AgentRuntime.MAX_STEPS_MESSAGE],
)
async def test_terminal_session_does_not_emit_recovery(terminal_text: str) -> None:
    session = SessionState(
        user_id="user",
        session_id="session",
        history=[
            ConversationMessage(role=MessageRole.USER, content="Old"),
            ConversationMessage(role=MessageRole.ASSISTANT, content=terminal_text),
        ],
    )
    sink = InMemoryTraceSink()

    await make_runtime(ScriptedFakeLLM([final()]), sink=sink).run(session, "New")

    assert TraceEventType.RECOVERY not in [event.event_type for event in sink.events]
    assert all(
        message.content != AgentRuntime.INTERRUPTED_TURN_MESSAGE
        for message in session.history
    )


@pytest.mark.asyncio
async def test_provider_timeout_stale_turn_recovers_after_sqlite_reload(
    tmp_path,
) -> None:
    database = tmp_path / "sessions.db"
    store = SQLiteSessionStore(database)
    fake = ScriptedFakeLLM([LLMTimeoutError(), final("Recovered")])
    service = AgentService(store=store, runtime=make_runtime(fake))
    await service.create_session("user", "session")

    with pytest.raises(LLMTimeoutError):
        await service.send_message("user", "session", "First request")

    stale = await SQLiteSessionStore(database).get("user", "session")
    assert [message.role for message in stale.history] == [MessageRole.USER]

    await service.send_message("user", "session", "Second request")

    recovered = await SQLiteSessionStore(database).get("user", "session")
    assert [message.role for message in recovered.history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert recovered.history[1].content == AgentRuntime.INTERRUPTED_TURN_MESSAGE


@pytest.mark.asyncio
async def test_tool_result_then_provider_failure_recovers_at_most_once(
    tmp_path,
) -> None:
    database = tmp_path / "sessions.db"
    store = SQLiteSessionStore(database)
    fake = ScriptedFakeLLM(
        [
            todo_call(),
            LLMProviderError(status_code=500, request_id="req-500"),
            final("Next turn completed"),
        ]
    )
    service = AgentService(store=store, runtime=make_runtime(fake))
    await service.create_session("user", "session")

    with pytest.raises(LLMProviderError):
        await service.send_message("user", "session", "Add once")

    stale = await SQLiteSessionStore(database).get("user", "session")
    assert len(stale.tool_state["todo"]["items"]) == 1
    assert stale.history[-1].role is MessageRole.TOOL

    await service.send_message("user", "session", "Continue")

    recovered = await SQLiteSessionStore(database).get("user", "session")
    assert len(recovered.tool_state["todo"]["items"]) == 1
    assert sum(
        message.role is MessageRole.TOOL for message in recovered.history
    ) == 1
    assert [message.role for message in fake.requests[-1].messages] == [
        MessageRole.ASSISTANT,
        MessageRole.USER,
    ]
    assert all(
        message.tool_call_id is None for message in fake.requests[-1].messages
    )
    assert AgentRuntime.INTERRUPTED_TURN_MESSAGE in [
        message.content for message in recovered.history
    ]
