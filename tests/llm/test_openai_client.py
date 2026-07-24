import json
from collections.abc import Iterable
from typing import Any

import pytest
from openai.types.responses import (
    Response,
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseReasoningItem,
    ResponseUsage,
)

from minimal_agent.config import Settings
from minimal_agent.errors import LLMProtocolError
from minimal_agent.llm.openai_client import OpenAIResponsesClient
from minimal_agent.models import (
    ConversationMessage,
    LLMRequest,
    LLMResponseType,
    MessageRole,
    SummaryRequest,
    ToolCall,
    ToolDefinition,
    ToolResult,
)


class FakeResponsesBoundary:
    def __init__(self, responses: Iterable[Response]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Response:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("no fake SDK response remains")
        return self._responses.pop(0)


class FakeAsyncOpenAI:
    def __init__(self, responses: Iterable[Response]) -> None:
        self.responses = FakeResponsesBoundary(responses)


def output_text(text: str) -> ResponseOutputText:
    return ResponseOutputText(
        annotations=[],
        text=text,
        type="output_text",
    )


def output_message(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="msg-1",
        content=[output_text(text)],
        role="assistant",
        status="completed",
        type="message",
    )


def function_call(
    call_id: str = "call-1",
    name: str = "calculator",
    arguments: str = '{"expression":"2+2"}',
) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        arguments=arguments,
        call_id=call_id,
        name=name,
        type="function_call",
        status="completed",
    )


def reasoning_item(private_text: str) -> ResponseReasoningItem:
    return ResponseReasoningItem.model_construct(
        id="reasoning-1",
        summary=[{"type": "summary_text", "text": private_text}],
        type="reasoning",
        content=[{"type": "reasoning_text", "text": private_text}],
        status="completed",
    )


def sdk_response(
    output: list[Any],
    *,
    response_id: str = "resp-1",
    with_usage: bool = True,
) -> Response:
    usage = None
    if with_usage:
        usage = ResponseUsage(
            input_tokens=11,
            input_tokens_details={"cached_tokens": 0, "cache_write_tokens": 0},
            output_tokens=7,
            output_tokens_details={"reasoning_tokens": 2},
            total_tokens=18,
        )
    return Response.model_construct(
        id=response_id,
        created_at=0.0,
        model="gpt-5-mini",
        object="response",
        output=output,
        parallel_tool_calls=True,
        tool_choice="auto",
        tools=[],
        usage=usage,
    )


def make_client(*responses: Response) -> tuple[OpenAIResponsesClient, FakeAsyncOpenAI]:
    sdk = FakeAsyncOpenAI(responses)
    client = OpenAIResponsesClient(
        Settings(openai_model="gpt-5-mini"),
        client=sdk,  # type: ignore[arg-type]
    )
    return client, sdk


def completion_request(
    *,
    messages: list[ConversationMessage] | None = None,
    tools: list[ToolDefinition] | None = None,
    continuation_id: str | None = None,
) -> LLMRequest:
    return LLMRequest(
        system_prompt="Use the available tools when necessary.",
        messages=messages
        or [ConversationMessage(role=MessageRole.USER, content="Hello")],
        tools=tools or [],
        max_output_tokens=500,
        continuation_id=continuation_id,
    )


@pytest.mark.asyncio
async def test_tool_definition_converts_to_responses_function_schema() -> None:
    client, sdk = make_client(sdk_response([output_message("Done")]))
    definition = ToolDefinition(
        name="calculator",
        description="Evaluate arithmetic.",
        parameters_schema={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    )

    await client.complete(completion_request(tools=[definition]))

    assert sdk.responses.calls[0]["tools"] == [
        {
            "type": "function",
            "name": "calculator",
            "description": "Evaluate arithmetic.",
            "parameters": definition.parameters_schema,
        }
    ]


@pytest.mark.asyncio
async def test_final_response_maps_text_id_and_usage() -> None:
    client, sdk = make_client(
        sdk_response([output_message("Visible answer")], response_id="resp-final")
    )

    result = await client.complete(completion_request())

    assert result.response_type is LLMResponseType.FINAL
    assert result.assistant_message.content == "Visible answer"
    assert result.provider_response_id == "resp-final"
    assert result.usage is not None
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 7
    assert sdk.responses.calls[0]["input"] == [
        {"role": "user", "content": "Hello"}
    ]
    assert sdk.responses.calls[0]["store"] is True


@pytest.mark.asyncio
async def test_function_call_maps_call_id_without_leaking_reasoning() -> None:
    private = "PRIVATE_REASONING_MUST_NOT_LEAK"
    client, _ = make_client(
        sdk_response(
            [
                reasoning_item(private),
                function_call(
                    call_id="provider-call-9",
                    name="search",
                    arguments='{"query":"agent"}',
                ),
            ],
            response_id="resp-tool",
        )
    )

    result = await client.complete(completion_request())

    assert result.response_type is LLMResponseType.TOOL_CALLS
    assert result.reasoning_present is True
    assert result.provider_response_id == "resp-tool"
    assert result.assistant_message.content is None
    assert len(result.assistant_message.tool_calls) == 1
    call = result.assistant_message.tool_calls[0]
    assert call.id == "provider-call-9"
    assert call.name == "search"
    assert call.arguments_json == '{"query":"agent"}'
    assert private not in result.model_dump_json()


@pytest.mark.asyncio
async def test_tool_output_continuation_uses_previous_response_and_json() -> None:
    client, sdk = make_client(sdk_response([output_message("The answer is 4")]))
    tool_result = ToolResult(
        tool_call_id="provider-call-1",
        tool_name="calculator",
        ok=True,
        output={"result": 4},
        latency_ms=1.5,
    )
    tool_message = ConversationMessage(
        role=MessageRole.TOOL,
        content=tool_result.model_dump_json(),
        tool_call_id=tool_result.tool_call_id,
        tool_name=tool_result.tool_name,
    )

    await client.complete(
        completion_request(
            messages=[tool_message],
            continuation_id="resp-123",
        )
    )

    request = sdk.responses.calls[0]
    assert request["previous_response_id"] == "resp-123"
    assert request["store"] is True
    assert len(request["input"]) == 1
    output_item = request["input"][0]
    assert output_item["type"] == "function_call_output"
    assert output_item["call_id"] == "provider-call-1"
    assert json.loads(output_item["output"]) == tool_result.model_dump(mode="json")
    assert output_item["output"].startswith("{")


@pytest.mark.asyncio
async def test_full_history_maps_assistant_calls_and_tool_outputs() -> None:
    client, sdk = make_client(sdk_response([output_message("Done")]))
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
        content='{"ok":true,"output":{"items":[]}}',
        tool_call_id="call-history",
        tool_name="todo",
    )

    await client.complete(completion_request(messages=[assistant, tool]))

    assert sdk.responses.calls[0]["input"] == [
        {
            "type": "function_call",
            "call_id": "call-history",
            "name": "todo",
            "arguments": '{"action":"list"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call-history",
            "output": '{"ok":true,"output":{"items":[]}}',
        },
    ]


@pytest.mark.asyncio
async def test_multiple_function_calls_preserve_order() -> None:
    client, _ = make_client(
        sdk_response(
            [
                function_call("call-a", "search", '{"query":"A"}'),
                function_call("call-b", "todo", '{"action":"list"}'),
            ]
        )
    )

    result = await client.complete(completion_request())

    assert [call.id for call in result.assistant_message.tool_calls] == [
        "call-a",
        "call-b",
    ]
    assert [call.name for call in result.assistant_message.tool_calls] == [
        "search",
        "todo",
    ]


@pytest.mark.asyncio
async def test_empty_or_unsupported_response_is_protocol_error() -> None:
    client, _ = make_client(sdk_response([]))

    with pytest.raises(LLMProtocolError, match="neither visible text"):
        await client.complete(completion_request())


@pytest.mark.asyncio
async def test_summary_omits_tools_and_returns_only_visible_text() -> None:
    client, sdk = make_client(sdk_response([output_message("Compact summary")]))
    request = SummaryRequest(
        messages=[ConversationMessage(role=MessageRole.USER, content="Old turn")]
    )

    summary = await client.summarize(request)

    assert summary == "Compact summary"
    sdk_request = sdk.responses.calls[0]
    assert "tools" not in sdk_request
    assert "tool_choice" not in sdk_request
    assert sdk_request["store"] is False


@pytest.mark.asyncio
async def test_summary_rejects_function_call() -> None:
    client, _ = make_client(sdk_response([function_call()]))
    request = SummaryRequest(
        messages=[ConversationMessage(role=MessageRole.USER, content="Old turn")]
    )

    with pytest.raises(LLMProtocolError, match="must not contain function calls"):
        await client.summarize(request)


@pytest.mark.asyncio
async def test_summary_rejects_empty_visible_text() -> None:
    client, _ = make_client(sdk_response([reasoning_item("private")]))
    request = SummaryRequest(
        messages=[ConversationMessage(role=MessageRole.USER, content="Old turn")]
    )

    with pytest.raises(LLMProtocolError, match="must contain visible text"):
        await client.summarize(request)
