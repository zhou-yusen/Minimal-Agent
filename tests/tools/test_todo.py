import json

import pytest

from minimal_agent.models import SessionState, ToolCall, ToolContext, ToolErrorCode
from minimal_agent.tools.registry import ToolRegistry
from minimal_agent.tools.todo import TodoTool


@pytest.fixture
def registry() -> ToolRegistry:
    tool_registry = ToolRegistry()
    tool_registry.register(TodoTool())
    return tool_registry


def make_call(call_id: str, **arguments) -> ToolCall:
    return ToolCall(
        id=call_id,
        name="todo",
        arguments_json=json.dumps(arguments),
    )


def make_context(session_id: str = "session-1") -> ToolContext:
    return ToolContext(user_id="user-1", session_id=session_id)


@pytest.mark.asyncio
async def test_add_list_complete_and_delete(
    registry: ToolRegistry,
) -> None:
    context = make_context()

    added = await registry.execute(
        make_call("add", action="add", text="Review tool schemas"),
        context,
    )
    item_id = added.output["item"]["id"]

    listed = await registry.execute(make_call("list-1", action="list"), context)
    completed = await registry.execute(
        make_call("complete", action="complete", item_id=item_id),
        context,
    )
    deleted = await registry.execute(
        make_call("delete", action="delete", item_id=item_id),
        context,
    )
    final_list = await registry.execute(
        make_call("list-2", action="list"),
        context,
    )

    assert added.ok is True
    assert item_id == "todo-1"
    assert listed.output["items"][0]["text"] == "Review tool schemas"
    assert completed.output["item"]["completed"] is True
    assert deleted.output["item"]["id"] == item_id
    assert final_list.output["items"] == []

    for result in (added, listed, completed, deleted, final_list):
        json.dumps(result.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_missing_item_is_execution_error(registry: ToolRegistry) -> None:
    result = await registry.execute(
        make_call("missing", action="complete", item_id="todo-999"),
        make_context(),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.EXECUTION_ERROR
    assert "not found" in result.error.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "add"},
        {"action": "add", "text": "task", "item_id": "todo-1"},
        {"action": "list", "text": "unexpected"},
        {"action": "complete"},
        {"action": "complete", "item_id": "todo-1", "text": "unexpected"},
        {"action": "delete"},
    ],
)
async def test_action_aware_validation(
    registry: ToolRegistry, arguments: dict[str, str]
) -> None:
    result = await registry.execute(
        make_call("invalid", **arguments),
        make_context(),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.VALIDATION_ERROR


@pytest.mark.asyncio
async def test_tool_contexts_do_not_share_todo_state(registry: ToolRegistry) -> None:
    first = make_context("session-a")
    second = make_context("session-b")

    first_add = await registry.execute(
        make_call("first-add", action="add", text="Only in A"),
        first,
    )
    second_list = await registry.execute(
        make_call("second-list", action="list"),
        second,
    )
    second_add = await registry.execute(
        make_call("second-add", action="add", text="Only in B"),
        second,
    )

    assert first_add.output["item"]["id"] == "todo-1"
    assert second_list.output["items"] == []
    assert second_add.output["item"]["id"] == "todo-1"
    assert first.tool_state["todo"]["items"][0]["text"] == "Only in A"
    assert second.tool_state["todo"]["items"][0]["text"] == "Only in B"


@pytest.mark.asyncio
async def test_todo_mutates_the_exact_session_tool_state_reference(
    registry: ToolRegistry,
) -> None:
    first_session = SessionState(user_id="user-1", session_id="session-a")
    context = ToolContext(
        user_id=first_session.user_id,
        session_id=first_session.session_id,
        tool_state=first_session.tool_state,
    )

    result = await registry.execute(
        make_call("session-add", action="add", text="Persist directly"),
        context,
    )

    second_session = SessionState(user_id="user-1", session_id="session-b")

    assert result.ok is True
    assert context.tool_state is first_session.tool_state
    assert first_session.tool_state["todo"]["items"][0]["text"] == "Persist directly"
    assert second_session.tool_state == {}
