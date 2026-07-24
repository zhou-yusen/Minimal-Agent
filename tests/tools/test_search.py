import json
from typing import Any

import pytest

from minimal_agent.models import ToolCall, ToolContext, ToolErrorCode
from minimal_agent.tools.registry import ToolRegistry
from minimal_agent.tools.search import InMemorySearchBackend, SearchTool


def make_call(query: str, top_k: int = 5, call_id: str = "search-call") -> ToolCall:
    return ToolCall(
        id=call_id,
        name="search",
        arguments_json=json.dumps({"query": query, "top_k": top_k}),
    )


def make_context() -> ToolContext:
    return ToolContext(user_id="user-1", session_id="session-1")


def make_registry(backend=None) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(SearchTool(backend=backend))
    return registry


@pytest.mark.asyncio
async def test_normal_search_is_deterministic_and_json_serializable() -> None:
    registry = make_registry()

    first = await registry.execute(make_call("Python testing"), make_context())
    second = await registry.execute(make_call("Python testing"), make_context())

    assert first.ok is True
    assert first.output == second.output
    assert first.output is not None
    assert first.output["results"][0]["id"] == "doc-1"
    json.dumps(first.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_top_k_limits_results() -> None:
    backend = InMemorySearchBackend(
        documents=[
            {"id": "a", "title": "Agent A", "content": "agent"},
            {"id": "b", "title": "Agent B", "content": "agent"},
            {"id": "c", "title": "Agent C", "content": "agent"},
        ]
    )
    registry = make_registry(backend)

    result = await registry.execute(make_call("agent", top_k=2), make_context())

    assert result.ok is True
    assert result.output is not None
    assert result.output["count"] == 2
    assert [item["id"] for item in result.output["results"]] == ["a", "b"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "top_k"), [("", 1), ("   ", 1), ("!!!", 1), ("ok", 0), ("ok", 11)])
async def test_invalid_search_arguments(query: str, top_k: int) -> None:
    result = await make_registry().execute(make_call(query, top_k), make_context())

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ToolErrorCode.VALIDATION_ERROR


@pytest.mark.asyncio
async def test_backend_is_injected() -> None:
    class InjectedBackend:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int]] = []

        async def search(self, query: str, top_k: int) -> list[dict[str, Any]]:
            self.calls.append((query, top_k))
            return [{"id": "injected", "title": "Injected", "snippet": query}]

    backend = InjectedBackend()
    registry = make_registry(backend)

    result = await registry.execute(make_call("stable", top_k=3), make_context())

    assert backend.calls == [("stable", 3)]
    assert result.ok is True
    assert result.output is not None
    assert result.output["results"][0]["id"] == "injected"
