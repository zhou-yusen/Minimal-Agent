"""Normalized domain data used by the runtime boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    """A normalized function call; `id` preserves the provider call ID."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments_json: str


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    role: MessageRole
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_role_fields(self) -> ConversationMessage:
        has_visible_content = bool(self.content and self.content.strip())
        if self.role is MessageRole.TOOL:
            if not self.tool_call_id or not self.tool_name or self.content is None:
                raise ValueError(
                    "tool messages require tool_call_id, tool_name, and content"
                )
        elif self.tool_call_id is not None or self.tool_name is not None:
            raise ValueError("only tool messages may set tool_call_id or tool_name")

        if self.role is MessageRole.USER and not has_visible_content:
            raise ValueError("user messages require non-empty content")
        if (
            self.role is MessageRole.ASSISTANT
            and not has_visible_content
            and not self.tool_calls
        ):
            raise ValueError(
                "assistant messages require visible content or at least one tool call"
            )
        if self.role is not MessageRole.ASSISTANT and self.tool_calls:
            raise ValueError("only assistant messages may contain tool_calls")
        return self


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parameters_schema: dict[str, Any]


class LLMRequest(BaseModel):
    """A provider-neutral completion request."""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str = Field(min_length=1)
    messages: list[ConversationMessage]
    tools: list[ToolDefinition]
    max_output_tokens: int = Field(ge=1)


class SummaryRequest(BaseModel):
    """A text-only summary request; intentionally has no tools field."""

    model_config = ConfigDict(extra="forbid")

    messages: list[ConversationMessage]
    previous_summary: str | None = None
    max_output_tokens: int = Field(default=512, ge=1)


class LLMResponseType(StrEnum):
    FINAL = "final"
    TOOL_CALLS = "tool_calls"


class CompressionStatus(StrEnum):
    NOT_NEEDED = "not_needed"
    SUCCEEDED = "succeeded"
    FALLBACK = "fallback"


class ContextBuildResult(BaseModel):
    """Provider-neutral context output plus safe compression diagnostics."""

    model_config = ConfigDict(extra="forbid")

    messages: list[ConversationMessage]
    estimated_tokens: int = Field(ge=0)
    compression_attempted: bool
    compression_status: CompressionStatus
    summary_updated: bool
    failure_kind: str | None = None


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class LLMResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_message: ConversationMessage
    response_type: LLMResponseType
    reasoning_present: bool = False
    usage: TokenUsage | None = None
    provider_response_id: str | None = None

    @model_validator(mode="after")
    def validate_response_shape(self) -> LLMResult:
        message = self.assistant_message
        if message.role is not MessageRole.ASSISTANT:
            raise ValueError("LLMResult requires an assistant message")
        if self.response_type is LLMResponseType.TOOL_CALLS and not message.tool_calls:
            raise ValueError("tool_calls response requires at least one tool call")
        if self.response_type is LLMResponseType.FINAL:
            if message.tool_calls or not message.content or not message.content.strip():
                raise ValueError(
                    "final response requires visible text and no tool calls"
                )
        return self


@dataclass(slots=True)
class ToolContext:
    """Internal mutable context that preserves the session tool-state reference."""

    user_id: str
    session_id: str
    tool_state: dict[str, Any] = dataclass_field(default_factory=dict)


class ToolErrorCode(StrEnum):
    INVALID_JSON = "invalid_json"
    UNKNOWN_TOOL = "unknown_tool"
    VALIDATION_ERROR = "validation_error"
    EXECUTION_ERROR = "execution_error"


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ToolErrorCode
    message: str = Field(min_length=1)
    details: Any | None = None


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    ok: bool
    output: Any | None = None
    error: ToolError | None = None
    latency_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_outcome(self) -> ToolResult:
        if self.ok and self.error is not None:
            raise ValueError("successful tool results cannot contain an error")
        if not self.ok and self.error is None:
            raise ValueError("failed tool results require an error")
        return self


class SessionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    history: list[ConversationMessage] = Field(default_factory=list)
    tool_state: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None
    summary_up_to_message_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    version: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_timestamps(self) -> SessionState:
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("session timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self


class AgentRunStatus(StrEnum):
    COMPLETED = "completed"
    MAX_STEPS = "max_steps"


class AgentRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    status: AgentRunStatus
    final_answer: str = Field(min_length=1)
    loop_steps: int = Field(ge=0)


class TraceEventType(StrEnum):
    RUN_START = "run_start"
    RECOVERY = "recovery"
    COMPRESSION = "compression"
    LLM_REQUEST = "llm_request"
    LLM_RESPONSE = "llm_response"
    TOOL_START = "tool_start"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    RUN_FINISH = "run_finish"


class TraceEvent(BaseModel):
    """One correlated, JSON-serializable development trace event."""

    model_config = ConfigDict(extra="forbid")

    event_type: TraceEventType
    timestamp: datetime = Field(default_factory=utc_now)
    user_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    loop_step: int | None = Field(default=None, ge=1)
    latency_ms: float | None = Field(default=None, ge=0)
    llm_response_type: LLMResponseType | None = None
    reasoning_present: bool | None = None
    provider_response_id_present: bool | None = None
    message_count: int | None = Field(default=None, ge=0)
    message_roles: list[MessageRole] | None = None
    tool_names: list[str] | None = None
    max_output_tokens: int | None = Field(default=None, ge=1)
    tool_call_count: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_args: Any | None = None
    tool_ok: bool | None = None
    tool_result: Any | None = None
    error: dict[str, Any] | None = None
    estimated_tokens: int | None = Field(default=None, ge=0)
    compression_attempted: bool | None = None
    compression_status: CompressionStatus | None = None
    summary_updated: bool | None = None
    failure_kind: str | None = None
    final_answer: str | None = None
    status: AgentRunStatus | None = None
    loop_steps: int | None = Field(default=None, ge=0)
    recovery_kind: str | None = None
    previous_terminal_role: MessageRole | None = None
    previous_terminal_state: str | None = None
