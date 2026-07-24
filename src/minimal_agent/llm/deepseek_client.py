"""DeepSeek Chat Completions adapter for normalized runtime objects."""

from __future__ import annotations

import json
from typing import Any

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from openai.types.chat import ChatCompletion

from minimal_agent.config import Settings
from minimal_agent.errors import (
    LLMConnectionError,
    LLMProtocolError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
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


class DeepSeekChatClient:
    """The project's only real provider adapter: DeepSeek Chat Completions."""

    _SUMMARY_INSTRUCTIONS = (
        "Summarize the supplied conversation for future context. Preserve user "
        "intent, concrete facts, tool outcomes, unresolved work, and references "
        "needed for follow-up. Return summary text only."
    )
    _THINKING_DISABLED = {"thinking": {"type": "disabled"}}

    def __init__(
        self,
        settings: Settings,
        *,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = settings.deepseek_model
        if client is not None:
            self._client = client
            return

        if settings.deepseek_api_key is None:
            raise ValueError(
                "DEEPSEEK_API_KEY is required for DeepSeekChatClient"
            )
        self._client = AsyncOpenAI(
            api_key=settings.deepseek_api_key.get_secret_value(),
            base_url=settings.deepseek_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,
        )

    async def complete(self, request: LLMRequest) -> LLMResult:
        response = await self._create_completion(
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": request.system_prompt},
                    *self._messages_to_chat(request.messages),
                ],
                "tools": self._tools_to_chat(request.tools),
                "max_tokens": request.max_output_tokens,
                "extra_body": self._THINKING_DISABLED,
            }
        )
        visible_text, tool_calls, reasoning_present = self._extract_message(
            response
        )

        if tool_calls:
            response_type = LLMResponseType.TOOL_CALLS
        elif visible_text:
            response_type = LLMResponseType.FINAL
        else:
            raise LLMProtocolError(
                "DeepSeek response contained neither visible text nor tool calls"
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
        response = await self._create_completion(
            {
                "model": self._model,
                "messages": [
                    {
                        "role": "system",
                        "content": self._SUMMARY_INSTRUCTIONS,
                    },
                    *self._messages_to_chat(request.messages),
                ],
                "max_tokens": request.max_output_tokens,
                "extra_body": self._THINKING_DISABLED,
            }
        )
        visible_text, tool_calls, _ = self._extract_message(response)
        if tool_calls:
            raise LLMProtocolError("summary response must not contain tool calls")
        if not visible_text:
            raise LLMProtocolError("summary response must contain visible text")
        return visible_text

    async def _create_completion(
        self,
        create_args: dict[str, Any],
    ) -> ChatCompletion:
        try:
            return await self._client.chat.completions.create(**create_args)
        except APITimeoutError as exc:
            raise LLMTimeoutError() from exc
        except APIConnectionError as exc:
            raise LLMConnectionError() from exc
        except RateLimitError as exc:
            raise LLMRateLimitError(
                status_code=exc.status_code,
                request_id=exc.request_id,
            ) from exc
        except APIStatusError as exc:
            raise LLMProviderError(
                status_code=exc.status_code,
                request_id=exc.request_id,
            ) from exc
        except APIError as exc:
            raise LLMProviderError() from exc

    @staticmethod
    def _tools_to_chat(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters_schema,
                },
            }
            for tool in tools
        ]

    @classmethod
    def _messages_to_chat(
        cls,
        messages: list[ConversationMessage],
    ) -> list[dict[str, Any]]:
        mapped: list[dict[str, Any]] = []
        for message in messages:
            if message.role is MessageRole.USER:
                mapped.append({"role": "user", "content": message.content})
            elif message.role is MessageRole.TOOL:
                mapped.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": cls._canonical_tool_output(message.content),
                    }
                )
            else:
                assistant: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.content,
                }
                if message.tool_calls:
                    assistant["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": call.arguments_json,
                            },
                        }
                        for call in message.tool_calls
                    ]
                mapped.append(assistant)
        return mapped

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
    def _extract_message(
        response: ChatCompletion,
    ) -> tuple[str, list[ToolCall], bool]:
        if not response.choices:
            raise LLMProtocolError("DeepSeek response contained no choices")
        message = response.choices[0].message
        visible_text = (message.content or "").strip()
        reasoning_present = bool(getattr(message, "reasoning_content", None))
        tool_calls: list[ToolCall] = []
        for call in message.tool_calls or []:
            if getattr(call, "type", None) != "function":
                continue
            function = getattr(call, "function", None)
            if function is None:
                continue
            tool_calls.append(
                ToolCall(
                    id=call.id,
                    name=function.name,
                    arguments_json=function.arguments,
                )
            )
        return visible_text, tool_calls, reasoning_present

    @staticmethod
    def _map_usage(response: ChatCompletion) -> TokenUsage | None:
        if response.usage is None:
            return None
        return TokenUsage(
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
        )
