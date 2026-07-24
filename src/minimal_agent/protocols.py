"""Small replaceable seams required by persistence, provider I/O, and tools."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from minimal_agent.models import (
    LLMRequest,
    LLMResult,
    SessionState,
    SummaryRequest,
    TraceEvent,
    ToolContext,
)


class LLMClient(Protocol):
    """Replaceable so offline tests do not call the real Responses API."""

    async def complete(self, request: LLMRequest) -> LLMResult: ...

    async def summarize(self, request: SummaryRequest) -> str:
        """Return summary text only; SummaryRequest cannot carry tools."""
        ...


class SessionStore(Protocol):
    """Replaceable because tests and v1 SQLite use different storage."""

    async def create(self, user_id: str, session_id: str) -> SessionState: ...

    async def get(self, user_id: str, session_id: str) -> SessionState: ...

    async def save(self, state: SessionState) -> None: ...

    async def delete(self, user_id: str, session_id: str) -> None: ...


class TraceSink(Protocol):
    """Replaceable destination for structured runtime diagnostics."""

    async def emit(self, event: TraceEvent) -> None: ...


class Tool(Protocol):
    """Common schema and execution contract implemented by each concrete tool."""

    name: str
    description: str
    arguments_model: type[BaseModel]

    @property
    def parameters_schema(self) -> dict[str, Any]: ...

    async def execute(self, arguments: BaseModel, context: ToolContext) -> Any: ...
