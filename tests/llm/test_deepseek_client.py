import json
from collections.abc import Iterable
from typing import Any

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    Function,
)
from openai.types.completion_usage import CompletionUsage

from minimal_agent.config import Settings
from minimal_agent.errors import (
    LLMConnectionError,
    LLMProtocolError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from minimal_agent.llm.deepseek_client import DeepSeekChatClient
from minimal_agent.models import (
    ConversationMessage,
    LLMRequest,
    LLMResponseType,
    MessageRole,
    SummaryRequest,
    ToolCall,
    ToolDefinition,
)


class FakeCompletionsBoundary:
    def __init__(self, results: Iterable[ChatCompletion | Exception]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> ChatCompletion:
        self.calls.append(kwargs)
        if not self._results:
            raise AssertionError("no fake SDK result remains")
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeChatBoundary:
    def __init__(self, results: Iterable[ChatCompletion | Exception]) -> None:
        self.completions = FakeCompletionsBoundary(results)


class FakeAsyncOpenAI:
    def __init__(self, results: Iterable[ChatCompletion | Exception]) -> None:
        self.chat = FakeChatBoundary(results)


def sdk_tool_call(
    call_id: str = "call-1",
    name: str = "calculator",
    arguments: str = '{"expression":"2+2"}',
) -> ChatCompletionMessageToolCall:
    return ChatCompletionMessageToolCall(
        id=call_id,
        type="function",
        function=Function(name=name, arguments=arguments),
    )


def sdk_response(
    *,
    content: str | None = None,
    tool_calls: list[ChatCompletionMessageToolCall] | None = None,
    reasoning_content: str | None = None,
    response_id: str = "chatcmpl-1",
    with_usage: bool = True,
) -> ChatCompletion:
    message = ChatCompletionMessage.model_construct(
        role="assistant",
        content=content,
        refusal=None,
        tool_calls=tool_calls,
        reasoning_content=reasoning_content,
    )
    usage = None
    if with_usage:
        usage = CompletionUsage(
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
        )
    return ChatCompletion(
        id=response_id,
        choices=[
            Choice(
                finish_reason="tool_calls" if tool_calls else "stop",
                index=0,
                logprobs=None,
                message=message,
            )
        ],
        created=0,
        model="deepseek-v4-flash",
        object="chat.completion",
        usage=usage,
    )


def make_client(
    *results: ChatCompletion | Exception,
) -> tuple[DeepSeekChatClient, FakeAsyncOpenAI]:
    sdk = FakeAsyncOpenAI(results)
    client = DeepSeekChatClient(
        Settings(),
        client=sdk,  # type: ignore[arg-type]
    )
    return client, sdk


def completion_request(
    *,
    messages: list[ConversationMessage] | None = None,
    tools: list[ToolDefinition] | None = None,
) -> LLMRequest:
    return LLMRequest(
        system_prompt="Use the available tools when necessary.",
        messages=messages
        or [ConversationMessage(role=MessageRole.USER, content="Hello")],
        tools=tools or [],
        max_output_tokens=500,
    )


def test_sdk_client_uses_deepseek_configuration_and_disables_retries(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    class CapturingAsyncOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "minimal_agent.llm.deepseek_client.AsyncOpenAI",
        CapturingAsyncOpenAI,
    )
    DeepSeekChatClient(Settings(deepseek_api_key="test-key"))

    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "https://api.deepseek.com"
    assert captured["max_retries"] == 0


@pytest.mark.asyncio
async def test_request_maps_model_system_tokens_and_disabled_thinking() -> None:
    client, sdk = make_client(sdk_response(content="Done"))

    await client.complete(completion_request())

    request = sdk.chat.completions.calls[0]
    assert request["model"] == "deepseek-v4-flash"
    assert request["max_tokens"] == 500
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert request["messages"][0] == {
        "role": "system",
        "content": "Use the available tools when necessary.",
    }
    assert request["messages"][1] == {"role": "user", "content": "Hello"}


@pytest.mark.asyncio
async def test_tool_definition_uses_nested_chat_function_schema() -> None:
    client, sdk = make_client(sdk_response(content="Done"))
    definition = ToolDefinition(
        name="calculator",
        description="Evaluate arithmetic.",
        parameters_schema={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
        },
    )

    await client.complete(completion_request(tools=[definition]))

    assert sdk.chat.completions.calls[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "calculator",
                "description": "Evaluate arithmetic.",
                "parameters": definition.parameters_schema,
            },
        }
    ]


@pytest.mark.asyncio
async def test_final_response_maps_text_id_and_usage() -> None:
    client, _ = make_client(
        sdk_response(content="Visible answer", response_id="chat-final")
    )

    result = await client.complete(completion_request())

    assert result.response_type is LLMResponseType.FINAL
    assert result.assistant_message.content == "Visible answer"
    assert result.provider_response_id == "chat-final"
    assert result.usage is not None
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 7


@pytest.mark.asyncio
async def test_single_tool_call_preserves_id_name_and_arguments() -> None:
    client, _ = make_client(
        sdk_response(
            tool_calls=[sdk_tool_call("provider-call", "search", '{"query":"A"}')]
        )
    )

    result = await client.complete(completion_request())

    assert result.response_type is LLMResponseType.TOOL_CALLS
    assert result.assistant_message.tool_calls == [
        ToolCall(
            id="provider-call",
            name="search",
            arguments_json='{"query":"A"}',
        )
    ]


@pytest.mark.asyncio
async def test_multiple_tool_calls_preserve_provider_order() -> None:
    client, _ = make_client(
        sdk_response(
            tool_calls=[
                sdk_tool_call("call-a", "search", '{"query":"A"}'),
                sdk_tool_call("call-b", "todo", '{"action":"list"}'),
            ]
        )
    )

    result = await client.complete(completion_request())

    assert [call.id for call in result.assistant_message.tool_calls] == [
        "call-a",
        "call-b",
    ]


@pytest.mark.asyncio
async def test_assistant_call_and_tool_result_replay_round_trip() -> None:
    client, sdk = make_client(sdk_response(content="Done"))
    assistant = ConversationMessage(
        role=MessageRole.ASSISTANT,
        tool_calls=[
            ToolCall(
                id="call-history",
                name="todo",
                arguments_json='{"action":"list"}',
            )
        ],
    )
    tool = ConversationMessage(
        role=MessageRole.TOOL,
        content='{"output":{"items":[]},"ok":true}',
        tool_call_id="call-history",
        tool_name="todo",
    )

    await client.complete(completion_request(messages=[assistant, tool]))

    assert sdk.chat.completions.calls[0]["messages"][1:] == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-history",
                    "type": "function",
                    "function": {
                        "name": "todo",
                        "arguments": '{"action":"list"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-history",
            "content": '{"ok":true,"output":{"items":[]}}',
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        ChatCompletion(
            id="empty",
            choices=[],
            created=0,
            model="deepseek-v4-flash",
            object="chat.completion",
        ),
        sdk_response(),
    ],
)
async def test_empty_or_malformed_response_is_protocol_error(
    response: ChatCompletion,
) -> None:
    client, _ = make_client(response)

    with pytest.raises(LLMProtocolError):
        await client.complete(completion_request())


@pytest.mark.asyncio
async def test_reasoning_presence_is_boolean_and_text_never_leaks() -> None:
    private = "PRIVATE_REASONING_MUST_NOT_LEAK"
    client, _ = make_client(
        sdk_response(content="Safe answer", reasoning_content=private)
    )

    result = await client.complete(completion_request())

    assert result.reasoning_present is True
    assert private not in result.model_dump_json()


@pytest.mark.asyncio
async def test_summary_disables_thinking_and_omits_tools() -> None:
    client, sdk = make_client(sdk_response(content="Compact summary"))

    summary = await client.summarize(
        SummaryRequest(
            messages=[ConversationMessage(role=MessageRole.USER, content="Old")]
        )
    )

    assert summary == "Compact summary"
    request = sdk.chat.completions.calls[0]
    assert "tools" not in request
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert request["messages"][0]["role"] == "system"


@pytest.mark.asyncio
async def test_summary_rejects_tool_calls() -> None:
    client, _ = make_client(sdk_response(tool_calls=[sdk_tool_call()]))

    with pytest.raises(LLMProtocolError, match="must not contain tool calls"):
        await client.summarize(
            SummaryRequest(
                messages=[ConversationMessage(role=MessageRole.USER, content="Old")]
            )
        )


@pytest.mark.asyncio
async def test_summary_requires_visible_text() -> None:
    client, _ = make_client(sdk_response())

    with pytest.raises(LLMProtocolError, match="must contain visible text"):
        await client.summarize(
            SummaryRequest(
                messages=[ConversationMessage(role=MessageRole.USER, content="Old")]
            )
        )


def sdk_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.deepseek.com/chat/completions")


def status_response(status_code: int, request_id: str) -> httpx.Response:
    return httpx.Response(
        status_code,
        request=sdk_request(),
        headers={"x-request-id": request_id},
        json={"error": {"message": "PROVIDER_PRIVATE_BODY_MUST_NOT_LEAK"}},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sdk_error", "domain_type", "safe_message", "status_code", "request_id"),
    [
        (
            APITimeoutError(sdk_request()),
            LLMTimeoutError,
            "LLM provider request timed out",
            None,
            None,
        ),
        (
            APIConnectionError(
                message="PROVIDER_PRIVATE_BODY_MUST_NOT_LEAK",
                request=sdk_request(),
            ),
            LLMConnectionError,
            "LLM provider connection failed",
            None,
            None,
        ),
        (
            RateLimitError(
                "PROVIDER_PRIVATE_BODY_MUST_NOT_LEAK",
                response=status_response(429, "req-rate"),
                body={"private": "PROVIDER_PRIVATE_BODY_MUST_NOT_LEAK"},
            ),
            LLMRateLimitError,
            "LLM provider rate limit exceeded",
            429,
            "req-rate",
        ),
        (
            InternalServerError(
                "PROVIDER_PRIVATE_BODY_MUST_NOT_LEAK",
                response=status_response(500, "req-500"),
                body={"private": "PROVIDER_PRIVATE_BODY_MUST_NOT_LEAK"},
            ),
            LLMProviderError,
            "LLM provider request failed",
            500,
            "req-500",
        ),
        (
            BadRequestError(
                "PROVIDER_PRIVATE_BODY_MUST_NOT_LEAK",
                response=status_response(400, "req-400"),
                body={"private": "PROVIDER_PRIVATE_BODY_MUST_NOT_LEAK"},
            ),
            LLMProviderError,
            "LLM provider request failed",
            400,
            "req-400",
        ),
        (
            APIError(
                "PROVIDER_PRIVATE_BODY_MUST_NOT_LEAK",
                sdk_request(),
                body={"private": "PROVIDER_PRIVATE_BODY_MUST_NOT_LEAK"},
            ),
            LLMProviderError,
            "LLM provider request failed",
            None,
            None,
        ),
    ],
)
async def test_sdk_errors_map_to_safe_domain_errors(
    sdk_error: Exception,
    domain_type: type[LLMProviderError],
    safe_message: str,
    status_code: int | None,
    request_id: str | None,
) -> None:
    client, _ = make_client(sdk_error)

    with pytest.raises(domain_type) as captured:
        await client.complete(completion_request())

    error = captured.value
    assert str(error) == safe_message
    assert error.status_code == status_code
    assert error.request_id == request_id
    assert "PROVIDER_PRIVATE_BODY_MUST_NOT_LEAK" not in str(error)
