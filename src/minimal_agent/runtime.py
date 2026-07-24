"""Bounded orchestration for one user turn of the minimal agent."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any
from uuid import uuid4

from minimal_agent.context import ContextManager
from minimal_agent.errors import (
    ContextCompressionError,
    ContextWindowExceededError,
    LLMConnectionError,
    LLMProtocolError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
    MinimalAgentError,
    SessionNotFoundError,
    SessionStoreError,
)
from minimal_agent.models import (
    AgentRunResult,
    AgentRunStatus,
    ConversationMessage,
    LLMRequest,
    LLMResponseType,
    MessageRole,
    SessionState,
    ToolContext,
    ToolResult,
    TraceEvent,
    TraceEventType,
    utc_now,
)
from minimal_agent.protocols import LLMClient, TraceSink
from minimal_agent.tools.registry import ToolRegistry

Checkpoint = Callable[[SessionState], Awaitable[None]]


class AgentRuntime:
    """Run the LLM/tool feedback loop for one already-loaded SessionState."""

    MAX_STEPS_MESSAGE = (
        "The agent stopped because it reached the maximum number of decision steps."
    )
    INTERRUPTED_TURN_MESSAGE = (
        "The previous agent turn was interrupted before a final answer."
    )

    def __init__(
        self,
        *,
        llm: LLMClient,
        tools: ToolRegistry,
        context_manager: ContextManager,
        system_prompt: str,
        max_steps: int,
        max_output_tokens: int,
        trace_sink: TraceSink | None = None,
    ) -> None:
        if not system_prompt.strip():
            raise ValueError("system_prompt must not be empty")
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be at least 1")

        self._llm = llm
        self._tools = tools
        self._context_manager = context_manager
        self._system_prompt = system_prompt
        self._max_steps = max_steps
        self._max_output_tokens = max_output_tokens
        self._trace_sink = trace_sink

    async def run(
        self,
        session: SessionState,
        user_message: str,
        *,
        checkpoint: Checkpoint | None = None,
    ) -> AgentRunResult:
        """Mutate ``session`` with one bounded user turn and return its outcome."""
        run_started = perf_counter()
        turn_id = str(uuid4())
        loop_step: int | None = None
        stage = "run_start"
        await self._emit_trace(
            TraceEvent(
                event_type=TraceEventType.RUN_START,
                user_id=session.user_id,
                session_id=session.session_id,
                turn_id=turn_id,
            )
        )

        try:
            interrupted_start: int | None = None
            if self._has_trailing_incomplete_turn(session):
                interrupted_start = self._trailing_turn_start(session)
                previous_role = session.history[-1].role
                self._append(
                    session,
                    ConversationMessage(
                        role=MessageRole.ASSISTANT,
                        content=self.INTERRUPTED_TURN_MESSAGE,
                    ),
                )
                await self._emit_trace(
                    TraceEvent(
                        event_type=TraceEventType.RECOVERY,
                        user_id=session.user_id,
                        session_id=session.session_id,
                        turn_id=turn_id,
                        recovery_kind="interrupted_turn",
                        previous_terminal_role=previous_role,
                        previous_terminal_state="non_terminal",
                    )
                )
                stage = "recovery_checkpoint"
                await self._checkpoint(session, checkpoint)

            self._append(
                session,
                ConversationMessage(role=MessageRole.USER, content=user_message),
            )
            stage = "user_checkpoint"
            await self._checkpoint(session, checkpoint)

            tool_definitions = self._tools.definitions()
            stage = "context_build"
            context_session = self._context_session(
                session,
                interrupted_start=interrupted_start,
            )
            context_result = await self._context_manager.build(
                context_session,
                system_prompt=self._system_prompt,
                tools=tool_definitions,
                max_output_tokens=self._max_output_tokens,
            )
            if context_session is not session:
                session.summary = context_session.summary
                session.summary_up_to_message_id = (
                    context_session.summary_up_to_message_id
                )
                session.updated_at = max(
                    session.updated_at,
                    context_session.updated_at,
                )
            if context_result.compression_attempted:
                await self._emit_trace(
                    TraceEvent(
                        event_type=TraceEventType.COMPRESSION,
                        user_id=session.user_id,
                        session_id=session.session_id,
                        turn_id=turn_id,
                        estimated_tokens=context_result.estimated_tokens,
                        compression_attempted=True,
                        compression_status=context_result.compression_status,
                        summary_updated=context_result.summary_updated,
                        failure_kind=context_result.failure_kind,
                    )
                )
            request_messages = context_result.messages
            continuation_id: str | None = None
            tool_context = ToolContext(
                user_id=session.user_id,
                session_id=session.session_id,
                tool_state=session.tool_state,
            )

            for loop_step in range(1, self._max_steps + 1):
                request = LLMRequest(
                    system_prompt=self._system_prompt,
                    messages=request_messages,
                    tools=tool_definitions,
                    max_output_tokens=self._max_output_tokens,
                    continuation_id=continuation_id,
                )
                await self._emit_trace(
                    TraceEvent(
                        event_type=TraceEventType.LLM_REQUEST,
                        user_id=session.user_id,
                        session_id=session.session_id,
                        turn_id=turn_id,
                        loop_step=loop_step,
                        continuation_present=continuation_id is not None,
                        message_count=len(request.messages),
                        message_roles=[message.role for message in request.messages],
                        tool_names=[tool.name for tool in request.tools],
                        max_output_tokens=request.max_output_tokens,
                    )
                )
                stage = "llm_complete"
                llm_started = perf_counter()
                result = await self._llm.complete(request)
                llm_latency = self._elapsed_ms(llm_started)
                await self._emit_trace(
                    TraceEvent(
                        event_type=TraceEventType.LLM_RESPONSE,
                        user_id=session.user_id,
                        session_id=session.session_id,
                        turn_id=turn_id,
                        loop_step=loop_step,
                        latency_ms=llm_latency,
                        llm_response_type=result.response_type,
                        reasoning_present=result.reasoning_present,
                        provider_response_id_present=bool(
                            result.provider_response_id
                        ),
                        tool_call_count=len(result.assistant_message.tool_calls),
                        input_tokens=(
                            result.usage.input_tokens if result.usage else None
                        ),
                        output_tokens=(
                            result.usage.output_tokens if result.usage else None
                        ),
                    )
                )

                if result.response_type is LLMResponseType.FINAL:
                    self._append(session, result.assistant_message)
                    stage = "final_checkpoint"
                    await self._checkpoint(session, checkpoint)
                    answer = result.assistant_message.content or ""
                    await self._emit_finish(
                        session=session,
                        turn_id=turn_id,
                        status=AgentRunStatus.COMPLETED,
                        final_answer=answer,
                        loop_steps=loop_step,
                        run_started=run_started,
                    )
                    return AgentRunResult(
                        user_id=session.user_id,
                        session_id=session.session_id,
                        turn_id=turn_id,
                        status=AgentRunStatus.COMPLETED,
                        final_answer=answer,
                        loop_steps=loop_step,
                    )

                stage = "llm_protocol"
                if result.response_type is not LLMResponseType.TOOL_CALLS:
                    raise LLMProtocolError(
                        "unsupported normalized LLM response: "
                        f"{result.response_type}"
                    )
                if (
                    not result.provider_response_id
                    or not result.provider_response_id.strip()
                ):
                    raise LLMProtocolError(
                        "tool-call response requires a non-empty "
                        "provider_response_id"
                    )

                self._append(session, result.assistant_message)
                tool_messages: list[ConversationMessage] = []
                for call in result.assistant_message.tool_calls:
                    await self._emit_trace(
                        TraceEvent(
                            event_type=TraceEventType.TOOL_START,
                            user_id=session.user_id,
                            session_id=session.session_id,
                            turn_id=turn_id,
                            loop_step=loop_step,
                            tool_name=call.name,
                            tool_call_id=call.id,
                            tool_args=self._safe_tool_args(call.arguments_json),
                        )
                    )
                    stage = "tool_execute"
                    tool_result = await self._tools.execute(call, tool_context)
                    await self._emit_trace(
                        TraceEvent(
                            event_type=TraceEventType.TOOL_RESULT,
                            user_id=session.user_id,
                            session_id=session.session_id,
                            turn_id=turn_id,
                            loop_step=loop_step,
                            latency_ms=tool_result.latency_ms,
                            tool_name=tool_result.tool_name,
                            tool_call_id=tool_result.tool_call_id,
                            tool_ok=tool_result.ok,
                            tool_result=self._trace_tool_result(tool_result),
                        )
                    )
                    tool_message = self._tool_message(tool_result)
                    self._append(session, tool_message)
                    tool_messages.append(tool_message)

                stage = "tool_checkpoint"
                await self._checkpoint(session, checkpoint)
                continuation_id = result.provider_response_id
                request_messages = tool_messages

            terminal_message = ConversationMessage(
                role=MessageRole.ASSISTANT,
                content=self.MAX_STEPS_MESSAGE,
            )
            self._append(session, terminal_message)
            stage = "final_checkpoint"
            await self._checkpoint(session, checkpoint)
            await self._emit_finish(
                session=session,
                turn_id=turn_id,
                status=AgentRunStatus.MAX_STEPS,
                final_answer=self.MAX_STEPS_MESSAGE,
                loop_steps=self._max_steps,
                run_started=run_started,
            )
            return AgentRunResult(
                user_id=session.user_id,
                session_id=session.session_id,
                turn_id=turn_id,
                status=AgentRunStatus.MAX_STEPS,
                final_answer=self.MAX_STEPS_MESSAGE,
                loop_steps=self._max_steps,
            )
        except Exception as exc:
            await self._emit_trace(
                TraceEvent(
                    event_type=TraceEventType.ERROR,
                    user_id=session.user_id,
                    session_id=session.session_id,
                    turn_id=turn_id,
                    loop_step=loop_step,
                    error=self._safe_error(exc, stage),
                )
            )
            raise

    @staticmethod
    def _append(session: SessionState, message: ConversationMessage) -> None:
        session.history.append(message)
        session.updated_at = utc_now()

    @staticmethod
    async def _checkpoint(
        session: SessionState,
        checkpoint: Checkpoint | None,
    ) -> None:
        if checkpoint is not None:
            await checkpoint(session)

    async def _emit_trace(self, event: TraceEvent) -> None:
        if self._trace_sink is None:
            return
        try:
            await self._trace_sink.emit(event)
        except Exception:
            pass

    async def _emit_finish(
        self,
        *,
        session: SessionState,
        turn_id: str,
        status: AgentRunStatus,
        final_answer: str,
        loop_steps: int,
        run_started: float,
    ) -> None:
        await self._emit_trace(
            TraceEvent(
                event_type=TraceEventType.RUN_FINISH,
                user_id=session.user_id,
                session_id=session.session_id,
                turn_id=turn_id,
                latency_ms=self._elapsed_ms(run_started),
                final_answer=final_answer,
                status=status,
                loop_steps=loop_steps,
            )
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return max(0.0, (perf_counter() - started) * 1_000)

    @staticmethod
    def _safe_tool_args(arguments_json: str) -> Any:
        def reject_constant(value: str) -> None:
            raise ValueError(f"non-standard JSON constant: {value}")

        try:
            return json.loads(arguments_json, parse_constant=reject_constant)
        except (json.JSONDecodeError, ValueError):
            return {"parse_status": "invalid_json"}

    @staticmethod
    def _has_trailing_incomplete_turn(session: SessionState) -> bool:
        if not session.history:
            return False
        last = session.history[-1]
        return not (
            last.role is MessageRole.ASSISTANT
            and bool(last.content and last.content.strip())
            and not last.tool_calls
        )

    @staticmethod
    def _trailing_turn_start(session: SessionState) -> int:
        for index in range(len(session.history) - 1, -1, -1):
            if session.history[index].role is MessageRole.USER:
                return index
        return 0

    @staticmethod
    def _context_session(
        session: SessionState,
        *,
        interrupted_start: int | None,
    ) -> SessionState:
        if interrupted_start is None:
            return session
        return session.model_copy(
            update={
                "history": [
                    *session.history[:interrupted_start],
                    *session.history[-2:],
                ]
            }
        )

    @staticmethod
    def _safe_error(exc: Exception, stage: str) -> dict[str, Any]:
        if isinstance(exc, LLMTimeoutError):
            code = "llm_timeout"
        elif isinstance(exc, LLMConnectionError):
            code = "llm_connection"
        elif isinstance(exc, LLMRateLimitError):
            code = "llm_rate_limit"
        elif isinstance(exc, LLMProviderError):
            code = "llm_provider"
        elif isinstance(exc, LLMProtocolError):
            code = "llm_protocol"
        elif isinstance(exc, ContextWindowExceededError):
            code = "context_window_exceeded"
        elif isinstance(exc, ContextCompressionError):
            code = "context_compression"
        elif isinstance(exc, SessionNotFoundError):
            code = "session_not_found"
        elif isinstance(exc, SessionStoreError):
            code = "session_store"
        elif isinstance(exc, MinimalAgentError):
            code = "domain_error"
        else:
            code = "internal_error"

        payload: dict[str, Any] = {
            "type": type(exc).__name__,
            "code": code,
            "stage": stage,
            "message": "agent operation failed",
        }
        if isinstance(exc, LLMProviderError):
            if exc.status_code is not None:
                payload["status_code"] = exc.status_code
            if exc.request_id is not None:
                payload["request_id"] = exc.request_id
        return payload

    @staticmethod
    def _trace_tool_result(result: ToolResult) -> dict[str, Any]:
        if result.ok:
            return {"ok": True, "output": result.output}
        error = result.error
        return {
            "ok": False,
            "error": error.model_dump(mode="json") if error else None,
        }

    @staticmethod
    def _tool_message(result: ToolResult) -> ConversationMessage:
        if result.ok:
            payload = {"ok": True, "output": result.output}
        else:
            error = result.error
            if error is None:
                raise LLMProtocolError("failed ToolResult is missing its error")
            error_payload = {
                "code": error.code.value,
                "message": error.message,
            }
            if error.details is not None:
                error_payload["details"] = error.details
            payload = {"ok": False, "error": error_payload}

        content = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return ConversationMessage(
            role=MessageRole.TOOL,
            content=content,
            tool_call_id=result.tool_call_id,
            tool_name=result.tool_name,
        )
