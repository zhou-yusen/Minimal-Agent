"""Tool registration, argument validation, dispatch, and result normalization."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from minimal_agent.errors import ToolExecutionError
from minimal_agent.models import (
    ToolCall,
    ToolContext,
    ToolDefinition,
    ToolError,
    ToolErrorCode,
    ToolResult,
)
from minimal_agent.protocols import Tool


class ToolRegistry:
    """Provider-neutral registry for schema discovery and safe tool dispatch."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name!r}")
        self._tools[tool.name] = tool

    def definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=tool.name,
                description=tool.description,
                parameters_schema=tool.parameters_schema,
            )
            for tool in self._tools.values()
        ]

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        started = perf_counter()

        try:
            raw_arguments = json.loads(
                call.arguments_json,
                parse_constant=self._reject_nonstandard_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            details = None
            if isinstance(exc, json.JSONDecodeError):
                details = {"line": exc.lineno, "column": exc.colno}
            return self._failure(
                call,
                ToolErrorCode.INVALID_JSON,
                "tool arguments are not valid JSON",
                started,
                details=details,
            )

        tool = self._tools.get(call.name)
        if tool is None:
            return self._failure(
                call,
                ToolErrorCode.UNKNOWN_TOOL,
                f"unknown tool: {call.name}",
                started,
            )

        try:
            arguments = tool.arguments_model.model_validate(raw_arguments)
        except ValidationError as exc:
            return self._failure(
                call,
                ToolErrorCode.VALIDATION_ERROR,
                "tool arguments failed validation",
                started,
                details=self._json_safe(
                    exc.errors(include_url=False, include_input=False)
                ),
            )

        try:
            output = await tool.execute(arguments, context)
            output = self._require_json_value(output)
        except ToolExecutionError as exc:
            return self._failure(
                call,
                ToolErrorCode.EXECUTION_ERROR,
                str(exc),
                started,
            )
        except Exception:
            return self._failure(
                call,
                ToolErrorCode.EXECUTION_ERROR,
                "tool execution failed",
                started,
            )

        return ToolResult(
            tool_call_id=call.id,
            tool_name=call.name,
            ok=True,
            output=output,
            latency_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return max(0.0, (perf_counter() - started) * 1_000)

    @classmethod
    def _failure(
        cls,
        call: ToolCall,
        code: ToolErrorCode,
        message: str,
        started: float,
        *,
        details: Any | None = None,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id=call.id,
            tool_name=call.name,
            ok=False,
            error=ToolError(code=code, message=message, details=details),
            latency_ms=cls._elapsed_ms(started),
        )

    @staticmethod
    def _json_safe(value: Any) -> Any:
        return json.loads(json.dumps(value, default=str, allow_nan=False))

    @staticmethod
    def _require_json_value(value: Any) -> Any:
        try:
            serialized = json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ToolExecutionError(
                "tool returned a non-JSON-serializable result"
            ) from exc
        return json.loads(serialized)

    @staticmethod
    def _reject_nonstandard_json_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")
