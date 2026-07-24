import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from minimal_agent.errors import SessionNotFoundError, SessionStoreError
from minimal_agent.models import (
    ConversationMessage,
    LLMResponseType,
    LLMResult,
    MessageRole,
    SessionState,
    ToolCall,
)
from minimal_agent.runtime import AgentRuntime
from minimal_agent.sessions.sqlite import SQLiteSessionStore
from minimal_agent.tools.registry import ToolRegistry
from minimal_agent.tools.todo import TodoTool
from tests.runtime.fakes import ScriptedFakeLLM


def final_result(text: str) -> LLMResult:
    return LLMResult(
        assistant_message=ConversationMessage(
            role=MessageRole.ASSISTANT,
            content=text,
        ),
        response_type=LLMResponseType.FINAL,
    )


def todo_call_result() -> LLMResult:
    return LLMResult(
        assistant_message=ConversationMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=[
                ToolCall(
                    id="call-todo-1",
                    name="todo",
                    arguments_json=(
                        '{"action":"add","text":"Persist this item"}'
                    ),
                )
            ],
        ),
        response_type=LLMResponseType.TOOL_CALLS,
        provider_response_id="resp-todo-1",
    )


def runtime(
    fake: ScriptedFakeLLM,
    registry: ToolRegistry | None = None,
) -> AgentRuntime:
    return AgentRuntime(
        llm=fake,
        tools=registry or ToolRegistry(),
        system_prompt="Use tools when needed.",
        max_steps=4,
        max_output_tokens=300,
    )


@pytest.mark.asyncio
async def test_create_then_get_returns_the_empty_session(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "agent.db")

    created = await store.create("user-a", "session-1")
    restored = await store.get("user-a", "session-1")

    assert restored == created
    assert restored.history == []
    assert restored.tool_state == {}
    assert restored.version == 0


@pytest.mark.asyncio
async def test_duplicate_create_is_rejected_without_overwrite(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "agent.db")
    created = await store.create("user-a", "session-1")

    with pytest.raises(SessionStoreError, match="already exists"):
        await store.create("user-a", "session-1")

    assert await store.get("user-a", "session-1") == created


@pytest.mark.asyncio
async def test_missing_get_save_and_delete_raise_not_found(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "agent.db")

    with pytest.raises(SessionNotFoundError):
        await store.get("missing-user", "missing-session")
    with pytest.raises(SessionNotFoundError):
        await store.save(
            SessionState(user_id="missing-user", session_id="missing-session")
        )
    with pytest.raises(SessionNotFoundError):
        await store.delete("missing-user", "missing-session")


@pytest.mark.asyncio
async def test_same_user_different_sessions_are_isolated(tmp_path: Path) -> None:
    store = SQLiteSessionStore(tmp_path / "agent.db")
    first = await store.create("user-a", "session-1")
    second = await store.create("user-a", "session-2")
    first.history.append(
        ConversationMessage(role=MessageRole.USER, content="Only session one")
    )
    first.tool_state["marker"] = "first"
    await store.save(first)
    await store.save(second)

    restored_first = await store.get("user-a", "session-1")
    restored_second = await store.get("user-a", "session-2")

    assert restored_first.history[0].content == "Only session one"
    assert restored_first.tool_state == {"marker": "first"}
    assert restored_second.history == []
    assert restored_second.tool_state == {}


@pytest.mark.asyncio
async def test_different_users_with_same_session_id_are_isolated(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionStore(tmp_path / "agent.db")
    user_a = await store.create("user-a", "session-1")
    user_b = await store.create("user-b", "session-1")
    user_a.tool_state["owner"] = "a"
    user_b.tool_state["owner"] = "b"
    await store.save(user_a)
    await store.save(user_b)

    assert (await store.get("user-a", "session-1")).tool_state == {
        "owner": "a"
    }
    assert (await store.get("user-b", "session-1")).tool_state == {
        "owner": "b"
    }

    with pytest.raises(SessionNotFoundError):
        await store.get("user-c", "session-1")


@pytest.mark.asyncio
async def test_runtime_history_is_saved_and_recovered(tmp_path: Path) -> None:
    database_path = tmp_path / "agent.db"
    store = SQLiteSessionStore(database_path)
    state = await store.create("user-a", "session-1")
    fake = ScriptedFakeLLM([final_result("Persisted final answer")])

    await runtime(fake).run(state, "Persist this turn")
    await store.save(state)
    restored = await SQLiteSessionStore(database_path).get(
        "user-a",
        "session-1",
    )

    assert [message.role for message in restored.history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert [message.content for message in restored.history] == [
        "Persist this turn",
        "Persisted final answer",
    ]


@pytest.mark.asyncio
async def test_todo_state_from_runtime_survives_store_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "agent.db"
    store = SQLiteSessionStore(database_path)
    state = await store.create("user-a", "session-1")
    registry = ToolRegistry()
    registry.register(TodoTool())
    fake = ScriptedFakeLLM(
        [todo_call_result(), final_result("Todo persisted")]
    )

    await runtime(fake, registry).run(state, "Add a todo")
    await store.save(state)
    restored = await SQLiteSessionStore(database_path).get(
        "user-a",
        "session-1",
    )

    assert restored.tool_state["todo"]["items"] == [
        {
            "id": "todo-1",
            "text": "Persist this item",
            "completed": False,
        }
    ]


@pytest.mark.asyncio
async def test_complete_tool_history_and_summary_round_trip(tmp_path: Path) -> None:
    database_path = tmp_path / "agent.db"
    store = SQLiteSessionStore(database_path)
    state = await store.create("user-a", "session-1")
    user = ConversationMessage(role=MessageRole.USER, content="Calculate")
    assistant_call = ConversationMessage(
        role=MessageRole.ASSISTANT,
        tool_calls=[
            ToolCall(
                id="provider-call-7",
                name="calculator",
                arguments_json='{"expression":"2+2"}',
            )
        ],
    )
    tool_message = ConversationMessage(
        role=MessageRole.TOOL,
        content='{"ok":true,"output":{"result":4}}',
        tool_call_id="provider-call-7",
        tool_name="calculator",
    )
    final = ConversationMessage(
        role=MessageRole.ASSISTANT,
        content="The answer is 4.",
    )
    state.history.extend([user, assistant_call, tool_message, final])
    state.tool_state = {"custom": {"value": 4}}
    state.summary = "The user requested a calculation."
    state.summary_up_to_message_id = final.id
    state.version = 2

    await store.save(state)
    restored = await SQLiteSessionStore(database_path).get(
        "user-a",
        "session-1",
    )

    assert restored == state
    assert restored.history[1].tool_calls[0].id == "provider-call-7"
    assert restored.history[2].tool_call_id == "provider-call-7"
    assert restored.history[2].tool_name == "calculator"
    assert restored.history[2].content == tool_message.content


@pytest.mark.asyncio
async def test_second_store_instance_recovers_first_store_state(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "agent.db"
    store_a = SQLiteSessionStore(database_path)
    state = await store_a.create("user-a", "session-1")
    state.tool_state["restart"] = {"recovered": True}
    await store_a.save(state)

    store_b = SQLiteSessionStore(database_path)
    restored = await store_b.get("user-a", "session-1")

    assert restored.tool_state == {"restart": {"recovered": True}}


@pytest.mark.asyncio
async def test_delete_removes_only_the_exact_composite_identity(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionStore(tmp_path / "agent.db")
    await store.create("user-a", "session-1")
    await store.create("user-a", "session-2")
    await store.create("user-b", "session-1")

    await store.delete("user-a", "session-1")

    with pytest.raises(SessionNotFoundError):
        await store.get("user-a", "session-1")
    assert (await store.get("user-a", "session-2")).user_id == "user-a"
    assert (await store.get("user-b", "session-1")).user_id == "user-b"


@pytest.mark.asyncio
async def test_save_preserves_created_at_and_round_trips_utc_timestamps(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionStore(tmp_path / "agent.db")
    state = await store.create("user-a", "session-1")
    original_created_at = state.created_at

    await store.save(state)
    restored = await store.get("user-a", "session-1")

    assert restored.created_at == original_created_at
    assert restored.updated_at >= original_created_at
    assert restored.created_at.utcoffset() == timedelta(0)
    assert restored.updated_at.utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_corrupted_state_json_becomes_session_store_error(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "agent.db"
    store = SQLiteSessionStore(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO sessions (
                user_id,
                session_id,
                state_json,
                created_at,
                updated_at,
                version
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "user-a",
                "session-1",
                "not-json",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                0,
            ),
        )

    with pytest.raises(SessionStoreError, match="stored session state is invalid"):
        await store.get("user-a", "session-1")
