"""OpenAI Responses API adapter for normalized runtime domain objects."""

from __future__ import annotations

import json
from typing import Any

from openai import APITimeoutError, AsyncOpenAI
from openai.types.responses import Response

from minimal_agent.config import Settings
from minimal_agent.errors import LLMProtocolError, LLMTimeoutError
from minimal_agent.models import (
    ConversationMessage,
    LLMRequest,
    LLMResponseType,
    LLMResult,
    MessageRole,
    SummaryRequest,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)


class OpenAIResponsesClient:
    """The project's only real provider adapter: OpenAI Responses API."""

    _SUMMARY_INSTRUCTIONS = (
        "Summarize the supplied conversation for future context. Preserve user "
        "intent, concrete facts, tool outcomes, unresolved work, and references "
        "needed for follow-up. Return summary text only."
    )

    def __init__(
        self,
        settings: Settings,
        *,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = settings.openai_model
        if client is not None:
            self._client = client
            return

        if settings.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required for OpenAIResponsesClient")
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
        )

    async def complete(self, request: LLMRequest) -> LLMResult:
        create_args: dict[str, Any] = {
            "model": self._model,
            "instructions": request.system_prompt,
            "input": self._messages_to_input(request.messages),
            "tools": self._tools_to_openai(request.tools),
            "max_output_tokens": request.max_output_tokens,
            "store": True,
        }
        if request.continuation_id is not None:
            create_args["previous_response_id"] = request.continuation_id

        response = await self._create_response(create_args)
        visible_text, tool_calls, reasoning_present = self._extract_output(response)

        if tool_calls:
            response_type = LLMResponseType.TOOL_CALLS
        elif visible_text:
            response_type = LLMResponseType.FINAL
        else:
            raise LLMProtocolError(
                "OpenAI response contained neither visible text nor function calls"
            )

        return LLMResult(
            assistant_message=ConversationMessage(
                role=MessageRole.ASSISTANT,
                content=visible_text or None,
                tool_calls=tool_calls,
            ),
            response_type=response_type,
            reasoning_present=reasoning_present,
            usage=self._map_usage(response),
            provider_response_id=response.id,
        )

    async def summarize(self, request: SummaryRequest) -> str:
        create_args: dict[str, Any] = {
            "model": self._model,
            "instructions": self._SUMMARY_INSTRUCTIONS,
            "input": self._messages_to_input(request.messages),
            "max_output_tokens": request.max_output_tokens,
            "store": False,
        }
        response = await self._create_response(create_args)
        visible_text, tool_calls, _ = self._extract_output(response)

        if tool_calls:
            raise LLMProtocolError("summary response must not contain function calls")
        if not visible_text:
            raise LLMProtocolError("summary response must contain visible text")
        return visible_text

    async def _create_response(self, create_args: dict[str, Any]) -> Response:
        try:
            return await self._client.responses.create(**create_args)
        except APITimeoutError as exc:
            raise LLMTimeoutError("OpenAI Responses API request timed out") from exc

    @staticmethod
    def _tools_to_openai(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters_schema,
            }
            for tool in tools
        ]

    @classmethod
    def _messages_to_input(
        cls,
        messages: list[ConversationMessage],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []

        for message in messages:
            if message.role is MessageRole.USER:
                items.append({"role": "user", "content": message.content})
                continue

            if message.role is MessageRole.TOOL:
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": cls._canonical_tool_output(message.content),
                    }
                )
                continue

            if message.content and message.content.strip():
                items.append({"role": "assistant", "content": message.content})
            items.extend(
                {
                    "type": "function_call",
                    "call_id": tool_call.id,
                    "name": tool_call.name,
                    "arguments": tool_call.arguments_json,
                }
                for tool_call in message.tool_calls
            )

        return items

    @staticmethod
    def _canonical_tool_output(content: str | None) -> str:
        if content is None:
            raise LLMProtocolError("tool message content is missing")

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-standard JSON constant: {value}")

        try:
            value = json.loads(content, parse_constant=reject_constant)
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMProtocolError(
                "tool message content must be valid JSON"
            ) from exc
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _extract_output(
        response: Response,
    ) -> tuple[str, list[ToolCall], bool]:
        visible_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        reasoning_present = False

        for item in response.output:
            item_type = getattr(item, "type", None)
            if item_type == "reasoning":
                reasoning_present = True
                continue
            if item_type == "function_call":
                tool_calls.append(
                    ToolCall(
                        id=item.call_id,
                        name=item.name,
                        arguments_json=item.arguments,
                    )
                )
                continue
            if item_type != "message":
                continue

            for content_item in item.content:
                if getattr(content_item, "type", None) != "output_text":
                    continue
                text = content_item.text
                if text and text.strip():
                    visible_parts.append(text.strip())

        return "\n".join(visible_parts), tool_calls, reasoning_present

    @staticmethod
    def _map_usage(response: Response) -> TokenUsage | None:
        if response.usage is None:
            return None
        return TokenUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
