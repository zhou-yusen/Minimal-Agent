import pytest

from minimal_agent.context import ContextManager
from minimal_agent.models import (
    ConversationMessage,
    LLMResponseType,
    LLMResult,
    MessageRole,
    ToolCall,
)
from minimal_agent.runtime import AgentRuntime
from minimal_agent.service import AgentService
from minimal_agent.sessions.sqlite import SQLiteSessionStore
from minimal_agent.tools.calculator import CalculatorTool
from minimal_agent.tools.registry import ToolRegistry
from minimal_agent.tools.todo import TodoTool
from tests.runtime.fakes import ScriptedFakeLLM


def final(text: str = "Done") -> LLMResult:
    return LLMResult(
        assistant_message=ConversationMessage(
            role=MessageRole.ASSISTANT,
            content=text,
        ),
        response_type=LLMResponseType.FINAL,
    )


def todo_add(response_id: str, call_id: str, text: str) -> LLMResult:
    return LLMResult(
        assistant_message=ConversationMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=[
                ToolCall(
                    id=call_id,
                    name="todo",
                    arguments_json=f'{{"action":"add","text":"{text}"}}',
                )
            ],
        ),
        response_type=LLMResponseType.TOOL_CALLS,
        provider_response_id=response_id,
    )


def make_service(
    store: SQLiteSessionStore,
    fake: ScriptedFakeLLM,
    *,
    compression_trigger: int = 100_000,
    recent_turns_to_keep: int = 4,
) -> AgentService:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(TodoTool())
    runtime = AgentRuntime(
        llm=fake,
        tools=registry,
        context_manager=ContextManager(
            llm=fake,
            context_token_limit=100_000,
            compression_trigger=compression_trigger,
            recent_turns_to_keep=recent_turns_to_keep,
        ),
        system_prompt="Use tools when needed.",
        max_steps=4,
        max_output_tokens=200,
    )
    return AgentService(store=store, runtime=runtime)


@pytest.mark.asyncio
async def test_service_persists_tool_turn_across_store_recreation(tmp_path) -> None:
    database = tmp_path / "sessions.db"
    store = SQLiteSessionStore(database)
    fake = ScriptedFakeLLM(
        [todo_add("resp-1", "call-1", "Write tests"), final("Added")]
    )
    service = make_service(store, fake)
    await service.create_session("user", "session")

    await service.send_message("user", "session", "Add a todo")

    reopened = SQLiteSessionStore(database)
    recovered = await reopened.get("user", "session")
    assert [message.role for message in recovered.history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert recovered.tool_state["todo"]["items"] == [
        {"id": "todo-1", "text": "Write tests", "completed": False}
    ]


@pytest.mark.asyncio
async def test_service_keeps_two_sessions_todo_state_isolated(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    fake = ScriptedFakeLLM(
        [
            todo_add("resp-a", "call-a", "Session A"),
            final("A added"),
            todo_add("resp-b", "call-b", "Session B"),
            final("B added"),
        ]
    )
    service = make_service(store, fake)
    await service.create_session("user", "a")
    await service.create_session("user", "b")

    await service.send_message("user", "a", "Add A")
    await service.send_message("user", "b", "Add B")

    a = await service.get_session("user", "a")
    b = await service.get_session("user", "b")
    assert a.tool_state["todo"]["items"][0]["text"] == "Session A"
    assert b.tool_state["todo"]["items"][0]["text"] == "Session B"


@pytest.mark.asyncio
async def test_compression_metadata_persists_with_final_checkpoint(tmp_path) -> None:
    database = tmp_path / "sessions.db"
    store = SQLiteSessionStore(database)
    fake = ScriptedFakeLLM(
        [final("Current answer")],
        summary_results=["Earlier facts summarized"],
    )
    service = make_service(
        store,
        fake,
        compression_trigger=1,
        recent_turns_to_keep=1,
    )
    state = await service.create_session("user", "session")
    state.history.extend(
        [
            ConversationMessage(role=MessageRole.USER, content="Old question 1"),
            ConversationMessage(role=MessageRole.ASSISTANT, content="Old answer 1"),
            ConversationMessage(role=MessageRole.USER, content="Old question 2"),
            ConversationMessage(role=MessageRole.ASSISTANT, content="Old answer 2"),
        ]
    )
    await store.save(state)
    original_ids = [message.id for message in state.history]

    await service.send_message("user", "session", "Current question")

    recovered = await SQLiteSessionStore(database).get("user", "session")
    assert recovered.summary == "Earlier facts summarized"
    assert recovered.summary_up_to_message_id == original_ids[1]
    assert [message.id for message in recovered.history[:4]] == original_ids
    assert len(recovered.history) == 6
    assert len(fake.summary_requests) == 1
