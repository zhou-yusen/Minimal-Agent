"""Bounded orchestration for one user turn of the minimal agent."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from uuid import uuid4

from minimal_agent.context import ContextManager
from minimal_agent.errors import LLMProtocolError
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
    utc_now,
)
from minimal_agent.protocols import LLMClient
from minimal_agent.tools.registry import ToolRegistry

Checkpoint = Callable[[SessionState], Awaitable[None]]


class AgentRuntime:
    """Run the LLM/tool feedback loop for one already-loaded SessionState."""

    MAX_STEPS_MESSAGE = (
        "The agent stopped because it reached the maximum number of decision steps."
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

    async def run(
        self,
        session: SessionState,
        user_message: str,
        *,
        checkpoint: Checkpoint | None = None,
    ) -> AgentRunResult:
        """Mutate ``session`` with one bounded user turn and return its outcome."""
        turn_id = str(uuid4())
        self._append(
            session,
            ConversationMessage(role=MessageRole.USER, content=user_message),
        )
        await self._checkpoint(session, checkpoint)

        tool_definitions = self._tools.definitions()
        request_messages = await self._context_manager.build(
            session,
            system_prompt=self._system_prompt,
            tools=tool_definitions,
            max_output_tokens=self._max_output_tokens,
        )
        continuation_id: str | None = None
        tool_context = ToolContext(
            user_id=session.user_id,
            session_id=session.session_id,
            tool_state=session.tool_state,
        )

        for loop_step in range(1, self._max_steps + 1):
            result = await self._llm.complete(
                LLMRequest(
                    system_prompt=self._system_prompt,
                    messages=request_messages,
                    tools=tool_definitions,
                    max_output_tokens=self._max_output_tokens,
                    continuation_id=continuation_id,
                )
            )

            if result.response_type is LLMResponseType.FINAL:
                self._append(session, result.assistant_message)
                await self._checkpoint(session, checkpoint)
                return AgentRunResult(
                    user_id=session.user_id,
                    session_id=session.session_id,
                    turn_id=turn_id,
                    status=AgentRunStatus.COMPLETED,
                    final_answer=result.assistant_message.content or "",
                    loop_steps=loop_step,
                )

            if result.response_type is not LLMResponseType.TOOL_CALLS:
                raise LLMProtocolError(
                    f"unsupported normalized LLM response: {result.response_type}"
                )
            if (
                not result.provider_response_id
                or not result.provider_response_id.strip()
            ):
                raise LLMProtocolError(
                    "tool-call response requires a non-empty provider_response_id"
                )

            self._append(session, result.assistant_message)
            tool_messages: list[ConversationMessage] = []
            for call in result.assistant_message.tool_calls:
                tool_result = await self._tools.execute(call, tool_context)
                tool_message = self._tool_message(tool_result)
                self._append(session, tool_message)
                tool_messages.append(tool_message)

            await self._checkpoint(session, checkpoint)
            continuation_id = result.provider_response_id
            request_messages = tool_messages

        terminal_message = ConversationMessage(
            role=MessageRole.ASSISTANT,
            content=self.MAX_STEPS_MESSAGE,
        )
        self._append(session, terminal_message)
        await self._checkpoint(session, checkpoint)
        return AgentRunResult(
            user_id=session.user_id,
            session_id=session.session_id,
            turn_id=turn_id,
            status=AgentRunStatus.MAX_STEPS,
            final_answer=self.MAX_STEPS_MESSAGE,
            loop_steps=self._max_steps,
        )

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
