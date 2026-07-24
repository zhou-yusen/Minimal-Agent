"""Deterministic context budgeting, turn selection, and rolling compression."""

from __future__ import annotations

import json
from collections.abc import Sequence

from minimal_agent.errors import (
    ContextCompressionError,
    ContextWindowExceededError,
)
from minimal_agent.models import (
    CompressionStatus,
    ConversationMessage,
    ContextBuildResult,
    MessageRole,
    SessionState,
    SummaryRequest,
    ToolDefinition,
    utc_now,
)
from minimal_agent.protocols import LLMClient


class ContextManager:
    """Build the bounded first LLM request for one user turn."""

    SUMMARY_PREFIX = "[Summary of earlier conversation]"
    CHARS_PER_TOKEN = 4

    def __init__(
        self,
        *,
        llm: LLMClient,
        context_token_limit: int,
        compression_trigger: int,
        recent_turns_to_keep: int,
        summary_max_output_tokens: int = 512,
    ) -> None:
        if context_token_limit < 1:
            raise ValueError("context_token_limit must be positive")
        if compression_trigger < 1:
            raise ValueError("compression_trigger must be positive")
        if compression_trigger > context_token_limit:
            raise ValueError(
                "compression_trigger cannot exceed context_token_limit"
            )
        if recent_turns_to_keep < 1:
            raise ValueError("recent_turns_to_keep must be at least 1")
        if summary_max_output_tokens < 1:
            raise ValueError("summary_max_output_tokens must be positive")

        self._llm = llm
        self._context_token_limit = context_token_limit
        self._compression_trigger = compression_trigger
        self._recent_turns_to_keep = recent_turns_to_keep
        self._summary_max_output_tokens = summary_max_output_tokens

    async def build(
        self,
        session: SessionState,
        *,
        system_prompt: str,
        tools: list[ToolDefinition],
        max_output_tokens: int,
    ) -> ContextBuildResult:
        """Return bounded messages plus safe compression diagnostics."""
        unsummarized = self._messages_after_boundary(session)
        turns = self._split_turns(unsummarized)
        current_turns = [turn for turn in turns if not self._is_completed(turn)]

        self._require_mandatory_content_fits(
            system_prompt=system_prompt,
            tools=tools,
            turns=current_turns,
            max_output_tokens=max_output_tokens,
        )

        summary = session.summary
        all_messages = self._compose(summary, turns)
        estimated_tokens = self.estimate_request_tokens(
            system_prompt=system_prompt,
            tools=tools,
            messages=all_messages,
            max_output_tokens=max_output_tokens,
        )

        compression_attempted = False
        compression_status = CompressionStatus.NOT_NEEDED
        summary_updated = False
        failure_kind: str | None = None

        if estimated_tokens >= self._compression_trigger:
            candidates = self._summary_candidates(turns)
            if candidates:
                compression_attempted = True
                candidate_messages = self._flatten(candidates)
                try:
                    new_summary = await self._llm.summarize(
                        SummaryRequest(
                            messages=candidate_messages,
                            previous_summary=session.summary,
                            max_output_tokens=self._summary_max_output_tokens,
                        )
                    )
                    if not new_summary.strip():
                        failure_kind = "empty_summary"
                        raise ContextCompressionError(
                            "summary response must contain visible text"
                        )
                except Exception:
                    compression_status = CompressionStatus.FALLBACK
                    if failure_kind is None:
                        failure_kind = "summary_exception"
                else:
                    compression_status = CompressionStatus.SUCCEEDED
                    summary_updated = True
                    summary = new_summary.strip()
                    session.summary = summary
                    session.summary_up_to_message_id = candidate_messages[-1].id
                    session.updated_at = max(utc_now(), session.created_at)
                    turns = turns[len(candidates) :]

        messages = self._fit_to_limit(
            system_prompt=system_prompt,
            tools=tools,
            summary=summary,
            turns=turns,
            max_output_tokens=max_output_tokens,
        )
        final_estimate = self.estimate_request_tokens(
            system_prompt=system_prompt,
            tools=tools,
            messages=messages,
            max_output_tokens=max_output_tokens,
        )
        return ContextBuildResult(
            messages=messages,
            estimated_tokens=final_estimate,
            compression_attempted=compression_attempted,
            compression_status=compression_status,
            summary_updated=summary_updated,
            failure_kind=failure_kind,
        )

    @classmethod
    def estimate_request_tokens(
        cls,
        *,
        system_prompt: str,
        tools: Sequence[ToolDefinition],
        messages: Sequence[ConversationMessage],
        max_output_tokens: int,
    ) -> int:
        """Estimate input JSON at four characters per token plus output reserve."""
        payload = {
            "system_prompt": system_prompt,
            "tools": [tool.model_dump(mode="json") for tool in tools],
            "messages": [
                message.model_dump(
                    mode="json",
                    exclude={"id", "created_at"},
                    exclude_none=True,
                )
                for message in messages
            ],
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        input_tokens = (len(serialized) + cls.CHARS_PER_TOKEN - 1) // (
            cls.CHARS_PER_TOKEN
        )
        return input_tokens + max_output_tokens

    def _messages_after_boundary(
        self,
        session: SessionState,
    ) -> list[ConversationMessage]:
        boundary = session.summary_up_to_message_id
        if boundary is None:
            return list(session.history)

        for index, message in enumerate(session.history):
            if message.id == boundary:
                return list(session.history[index + 1 :])
        raise ContextCompressionError(
            "summary boundary does not exist in session history"
        )

    @staticmethod
    def _split_turns(
        messages: Sequence[ConversationMessage],
    ) -> list[list[ConversationMessage]]:
        turns: list[list[ConversationMessage]] = []
        current: list[ConversationMessage] = []

        for message in messages:
            if message.role is MessageRole.USER and current:
                turns.append(current)
                current = []
            current.append(message)

        if current:
            turns.append(current)
        return turns

    @staticmethod
    def _is_completed(turn: Sequence[ConversationMessage]) -> bool:
        if not turn:
            return False
        terminal = turn[-1]
        return (
            terminal.role is MessageRole.ASSISTANT
            and bool(terminal.content and terminal.content.strip())
            and not terminal.tool_calls
        )

    def _summary_candidates(
        self,
        turns: Sequence[list[ConversationMessage]],
    ) -> list[list[ConversationMessage]]:
        completed_count = sum(self._is_completed(turn) for turn in turns)
        candidate_count = max(
            0,
            completed_count - self._recent_turns_to_keep,
        )
        candidates: list[list[ConversationMessage]] = []

        for turn in turns:
            if len(candidates) >= candidate_count or not self._is_completed(turn):
                break
            candidates.append(turn)
        return candidates

    def _fit_to_limit(
        self,
        *,
        system_prompt: str,
        tools: list[ToolDefinition],
        summary: str | None,
        turns: Sequence[list[ConversationMessage]],
        max_output_tokens: int,
    ) -> list[ConversationMessage]:
        kept_turns = [list(turn) for turn in turns]
        active_summary = summary

        while self._estimate(
            system_prompt,
            tools,
            active_summary,
            kept_turns,
            max_output_tokens,
        ) > self._context_token_limit:
            oldest_completed = next(
                (
                    index
                    for index, turn in enumerate(kept_turns)
                    if self._is_completed(turn)
                ),
                None,
            )
            if oldest_completed is None:
                break
            del kept_turns[oldest_completed]

        if self._estimate(
            system_prompt,
            tools,
            active_summary,
            kept_turns,
            max_output_tokens,
        ) > self._context_token_limit:
            active_summary = None

        messages = self._compose(active_summary, kept_turns)
        if self.estimate_request_tokens(
            system_prompt=system_prompt,
            tools=tools,
            messages=messages,
            max_output_tokens=max_output_tokens,
        ) > self._context_token_limit:
            raise ContextWindowExceededError(
                "mandatory request content exceeds the context token limit"
            )
        return messages

    def _require_mandatory_content_fits(
        self,
        *,
        system_prompt: str,
        tools: list[ToolDefinition],
        turns: Sequence[list[ConversationMessage]],
        max_output_tokens: int,
    ) -> None:
        mandatory = self._flatten(turns)
        if self.estimate_request_tokens(
            system_prompt=system_prompt,
            tools=tools,
            messages=mandatory,
            max_output_tokens=max_output_tokens,
        ) > self._context_token_limit:
            raise ContextWindowExceededError(
                "system prompt, tools, current turn, and output reserve "
                "exceed the context token limit"
            )

    def _estimate(
        self,
        system_prompt: str,
        tools: list[ToolDefinition],
        summary: str | None,
        turns: Sequence[list[ConversationMessage]],
        max_output_tokens: int,
    ) -> int:
        return self.estimate_request_tokens(
            system_prompt=system_prompt,
            tools=tools,
            messages=self._compose(summary, turns),
            max_output_tokens=max_output_tokens,
        )

    @classmethod
    def _compose(
        cls,
        summary: str | None,
        turns: Sequence[list[ConversationMessage]],
    ) -> list[ConversationMessage]:
        messages: list[ConversationMessage] = []
        if summary:
            messages.append(
                ConversationMessage(
                    role=MessageRole.ASSISTANT,
                    content=f"{cls.SUMMARY_PREFIX}\n{summary}",
                )
            )
        messages.extend(cls._flatten(turns))
        return messages

    @staticmethod
    def _flatten(
        turns: Sequence[Sequence[ConversationMessage]],
    ) -> list[ConversationMessage]:
        return [message for turn in turns for message in turn]
