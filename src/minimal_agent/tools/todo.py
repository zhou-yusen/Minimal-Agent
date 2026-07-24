"""One action-based todo tool whose state lives only in ToolContext."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from minimal_agent.errors import ToolExecutionError
from minimal_agent.models import ToolContext


TodoText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
TodoItemId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]


class TodoAction(StrEnum):
    ADD = "add"
    LIST = "list"
    COMPLETE = "complete"
    DELETE = "delete"


class TodoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: TodoAction
    text: TodoText | None = None
    item_id: TodoItemId | None = None

    @model_validator(mode="after")
    def validate_action_fields(self) -> TodoArguments:
        if self.action is TodoAction.ADD:
            if self.text is None or self.item_id is not None:
                raise ValueError("add requires text and does not accept item_id")
        elif self.action is TodoAction.LIST:
            if self.text is not None or self.item_id is not None:
                raise ValueError("list does not accept text or item_id")
        else:
            if self.item_id is None or self.text is not None:
                raise ValueError(
                    f"{self.action.value} requires item_id and does not accept text"
                )
        return self


class TodoTool:
    name = "todo"
    description = (
        "Manage session-scoped todo items with add, list, complete, or delete."
    )
    arguments_model = TodoArguments

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self.arguments_model.model_json_schema()

    async def execute(
        self,
        arguments: TodoArguments,
        context: ToolContext,
    ) -> dict[str, Any]:
        state = self._state(context)

        if arguments.action is TodoAction.ADD:
            item = {
                "id": f"todo-{state['next_id']}",
                "text": arguments.text,
                "completed": False,
            }
            state["next_id"] += 1
            state["items"].append(item)
            return {"action": "add", "item": dict(item)}

        if arguments.action is TodoAction.LIST:
            return {
                "action": "list",
                "items": [dict(item) for item in state["items"]],
            }

        index = self._find_index(state["items"], arguments.item_id)
        if index is None:
            raise ToolExecutionError(f"todo item not found: {arguments.item_id}")

        if arguments.action is TodoAction.COMPLETE:
            state["items"][index]["completed"] = True
            return {"action": "complete", "item": dict(state["items"][index])}

        removed = state["items"].pop(index)
        return {"action": "delete", "item": dict(removed)}

    @staticmethod
    def _state(context: ToolContext) -> dict[str, Any]:
        state = context.tool_state.setdefault(
            "todo",
            {"next_id": 1, "items": []},
        )
        if not isinstance(state, dict) or not isinstance(state.get("items"), list):
            raise ToolExecutionError("todo state is malformed")
        if not isinstance(state.get("next_id"), int) or state["next_id"] < 1:
            raise ToolExecutionError("todo state is malformed")
        return state

    @staticmethod
    def _find_index(items: list[dict[str, Any]], item_id: str | None) -> int | None:
        for index, item in enumerate(items):
            if item.get("id") == item_id:
                return index
        return None
