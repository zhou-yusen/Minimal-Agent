"""Deterministic in-memory search with an injectable backend."""

from __future__ import annotations

import re
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from minimal_agent.models import ToolContext


Query = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Query
    top_k: int = Field(default=5, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def require_searchable_character(cls, value: str) -> str:
        if not any(character.isalnum() for character in value):
            raise ValueError("query must contain at least one letter or number")
        return value


class SearchBackend(Protocol):
    async def search(self, query: str, top_k: int) -> list[dict[str, Any]]: ...


class InMemorySearchBackend:
    """Tiny lexical backend intended for deterministic demos and tests."""

    DEFAULT_DOCUMENTS = (
        {
            "id": "doc-1",
            "title": "Python testing basics",
            "content": "pytest supports small deterministic Python tests.",
        },
        {
            "id": "doc-2",
            "title": "Agent tool calling",
            "content": "An agent can call a tool and return its result to an LLM.",
        },
        {
            "id": "doc-3",
            "title": "Session state",
            "content": "Separate session identifiers keep conversation state isolated.",
        },
        {
            "id": "doc-4",
            "title": "Python async code",
            "content": "Async Python can coordinate network and tool operations.",
        },
    )

    def __init__(self, documents: list[dict[str, str]] | None = None) -> None:
        source = self.DEFAULT_DOCUMENTS if documents is None else documents
        self._documents = tuple(dict(document) for document in source)

    async def search(self, query: str, top_k: int) -> list[dict[str, Any]]:
        tokens = tuple(dict.fromkeys(re.findall(r"\w+", query.casefold())))
        ranked: list[tuple[int, str, dict[str, str]]] = []

        for document in self._documents:
            haystack = f"{document['title']} {document['content']}".casefold()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                ranked.append((score, document["id"], document))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [
            {
                "id": document["id"],
                "title": document["title"],
                "snippet": document["content"],
                "score": score,
            }
            for score, _, document in ranked[:top_k]
        ]


class SearchTool:
    name = "search"
    description = "Search a deterministic local document corpus for relevant text."
    arguments_model = SearchArguments

    def __init__(self, backend: SearchBackend | None = None) -> None:
        self._backend = backend or InMemorySearchBackend()

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self.arguments_model.model_json_schema()

    async def execute(
        self,
        arguments: SearchArguments,
        context: ToolContext,
    ) -> dict[str, Any]:
        del context
        results = await self._backend.search(arguments.query, arguments.top_k)
        return {
            "query": arguments.query,
            "count": len(results),
            "results": results,
        }
