import json
import logging

import httpx
import pytest
from openai import InternalServerError

from minimal_agent.config import Settings
from minimal_agent.context import ContextManager
from minimal_agent.errors import (
    LLMConnectionError,
    LLMProtocolError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
    SessionStoreError,
)
from minimal_agent.models import (
    AgentRunStatus,
    ConversationMessage,
    LLMResponseType,
    LLMResult,
    MessageRole,
    SessionState,
    ToolCall,
    TraceEvent,
    TraceEventType,
)
from minimal_agent.llm.deepseek_client import DeepSeekChatClient
from minimal_agent.runtime import AgentRuntime
from minimal_agent.tools.calculator import CalculatorTool
from minimal_agent.tools.registry import ToolRegistry
from minimal_agent.tools.todo import TodoTool
from minimal_agent.tracing import InMemoryTraceSink, JsonLoggingTraceSink
from tests.runtime.fakes import ScriptedFakeLLM


def final(text: str = "Done", *, reasoning: bool = False) -> LLMResult:
    return LLMResult(
        assistant_message=ConversationMessage(
            role=MessageRole.ASSISTANT,
            content=text,
        ),
        response_type=LLMResponseType.FINAL,
        reasoning_present=reasoning,
    )


def tool_calls(response_id: str | None, *calls: ToolCall) -> LLMResult:
    return LLMResult(
        assistant_message=ConversationMessage(
            role=MessageRole.ASSISTANT,
            tool_calls=list(calls),
        ),
        response_type=LLMResponseType.TOOL_CALLS,
        provider_response_id=response_id,
    )


def call(call_id: str, name: str, arguments: str) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments_json=arguments)


def make_runtime(
    fake: ScriptedFakeLLM,
    sink: object,
    *,
    max_steps: int = 4,
    system_prompt: str = "Use tools when useful.",
    compression_trigger: int = 100_000,
    recent_turns_to_keep: int = 4,
) -> AgentRuntime:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    registry.register(TodoTool())
    return AgentRuntime(
        llm=fake,
        tools=registry,
        context_manager=ContextManager(
            llm=fake,
            context_token_limit=100_000,
            compression_trigger=compression_trigger,
            recent_turns_to_keep=recent_turns_to_keep,
        ),
        system_prompt=system_prompt,
        max_steps=max_steps,
        max_output_tokens=200,
        trace_sink=sink,  # type: ignore[arg-type]
    )


def state() -> SessionState:
    return SessionState(user_id="user", session_id="session")


def event_types(sink: InMemoryTraceSink) -> list[TraceEventType]:
    return [event.event_type for event in sink.events]


@pytest.mark.asyncio
async def test_direct_final_trace_order_correlation_and_latency() -> None:
    sink = InMemoryTraceSink()

    result = await make_runtime(ScriptedFakeLLM([final("Hello")]), sink).run(
        state(), "Hi"
    )

    assert event_types(sink) == [
        TraceEventType.RUN_START,
        TraceEventType.LLM_REQUEST,
        TraceEventType.LLM_RESPONSE,
        TraceEventType.RUN_FINISH,
    ]
    assert {event.turn_id for event in sink.events} == {result.turn_id}
    assert sink.events[2].latency_ms is not None
    assert sink.events[2].latency_ms >= 0
    assert sink.events[-1].latency_ms is not None
    assert sink.events[-1].latency_ms >= 0


@pytest.mark.asyncio
async def test_one_tool_trace_order_and_latency() -> None:
    sink = InMemoryTraceSink()
    fake = ScriptedFakeLLM(
        [
            tool_calls(
                "resp-1",
                call("call-1", "calculator", '{"expression":"2+2"}'),
            ),
            final("Four"),
        ]
    )

    await make_runtime(fake, sink).run(state(), "Calculate")

    assert event_types(sink) == [
        TraceEventType.RUN_START,
        TraceEventType.LLM_REQUEST,
        TraceEventType.LLM_RESPONSE,
        TraceEventType.TOOL_START,
        TraceEventType.TOOL_RESULT,
        TraceEventType.LLM_REQUEST,
        TraceEventType.LLM_RESPONSE,
        TraceEventType.RUN_FINISH,
    ]
    tool_result = sink.events[4]
    assert tool_result.tool_ok is True
    assert tool_result.latency_ms is not None
    assert tool_result.latency_ms >= 0


@pytest.mark.asyncio
async def test_multiple_tools_emit_start_result_pairs_in_model_order() -> None:
    sink = InMemoryTraceSink()
    fake = ScriptedFakeLLM(
        [
            tool_calls(
                "resp-1",
                call("calc", "calculator", '{"expression":"3*3"}'),
                call("todo", "todo", '{"action":"add","text":"Keep nine"}'),
            ),
            final(),
        ]
    )

    await make_runtime(fake, sink).run(state(), "Do both")

    tool_events = [
        (event.event_type, event.tool_call_id)
        for event in sink.events
        if event.event_type in {TraceEventType.TOOL_START, TraceEventType.TOOL_RESULT}
    ]
    assert tool_events == [
        (TraceEventType.TOOL_START, "calc"),
        (TraceEventType.TOOL_RESULT, "calc"),
        (TraceEventType.TOOL_START, "todo"),
        (TraceEventType.TOOL_RESULT, "todo"),
    ]


@pytest.mark.asyncio
async def test_multiple_llm_rounds_use_correct_loop_steps() -> None:
    sink = InMemoryTraceSink()
    fake = ScriptedFakeLLM(
        [
            tool_calls(
                "resp-1",
                call("c1", "calculator", '{"expression":"1+1"}'),
            ),
            tool_calls(
                "resp-2",
                call("c2", "calculator", '{"expression":"2+2"}'),
            ),
            final(),
        ]
    )

    await make_runtime(fake, sink).run(state(), "Twice")

    requests = [
        event for event in sink.events if event.event_type is TraceEventType.LLM_REQUEST
    ]
    responses = [
        event for event in sink.events if event.event_type is TraceEventType.LLM_RESPONSE
    ]
    assert [event.loop_step for event in requests] == [1, 2, 3]
    assert [event.loop_step for event in responses] == [1, 2, 3]


@pytest.mark.asyncio
async def test_max_steps_emits_durable_run_finish() -> None:
    sink = InMemoryTraceSink()
    fake = ScriptedFakeLLM(
        [
            tool_calls(
                "resp-1",
                call("c1", "calculator", '{"expression":"1+1"}'),
            ),
            tool_calls(
                "resp-2",
                call("c2", "calculator", '{"expression":"2+2"}'),
            ),
        ]
    )

    result = await make_runtime(fake, sink, max_steps=2).run(state(), "Continue")

    finish = sink.events[-1]
    assert result.status is AgentRunStatus.MAX_STEPS
    assert finish.event_type is TraceEventType.RUN_FINISH
    assert finish.status is AgentRunStatus.MAX_STEPS
    assert finish.loop_steps == 2


@pytest.mark.asyncio
async def test_tool_failure_is_a_tool_result_not_runtime_error() -> None:
    sink = InMemoryTraceSink()
    fake = ScriptedFakeLLM(
        [
            tool_calls("resp-1", call("bad", "calculator", "{")),
            final("Arguments failed"),
        ]
    )

    await make_runtime(fake, sink).run(state(), "Calculate")

    result_event = next(
        event for event in sink.events if event.event_type is TraceEventType.TOOL_RESULT
    )
    assert result_event.tool_ok is False
    assert result_event.tool_result["error"]["code"] == "invalid_json"
    assert TraceEventType.ERROR not in event_types(sink)


@pytest.mark.asyncio
async def test_llm_trace_is_sanitized_and_never_contains_private_reasoning() -> None:
    api_secret = "sk-API_KEY_MUST_NOT_LEAK"
    private_reasoning = "PRIVATE_REASONING_MUST_NOT_LEAK"
    user_secret = "FULL_USER_MESSAGE_MUST_NOT_LEAK"
    fake = ScriptedFakeLLM([final("Safe answer", reasoning=True)])
    fake.private_reasoning_fixture = private_reasoning
    sink = InMemoryTraceSink()

    await make_runtime(
        fake,
        sink,
        system_prompt=f"System credential fixture: {api_secret}",
    ).run(state(), user_secret)

    serialized = json.dumps(
        [event.model_dump(mode="json", exclude_none=True) for event in sink.events]
    )
    response = next(
        event for event in sink.events if event.event_type is TraceEventType.LLM_RESPONSE
    )
    request = next(
        event for event in sink.events if event.event_type is TraceEventType.LLM_REQUEST
    )
    assert response.reasoning_present is True
    assert request.message_roles == [MessageRole.USER]
    assert api_secret not in serialized
    assert private_reasoning not in serialized
    assert user_secret not in serialized


def completed_turn(label: str) -> list[ConversationMessage]:
    return [
        ConversationMessage(role=MessageRole.USER, content=f"question-{label}"),
        ConversationMessage(role=MessageRole.ASSISTANT, content=f"answer-{label}"),
    ]


@pytest.mark.asyncio
async def test_successful_compression_emits_report_before_llm_request() -> None:
    sink = InMemoryTraceSink()
    fake = ScriptedFakeLLM([final()], summary_results=["Earlier summary"])
    session = state()
    session.history.extend(completed_turn("one") + completed_turn("two"))

    await make_runtime(
        fake,
        sink,
        compression_trigger=1,
        recent_turns_to_keep=1,
    ).run(session, "Current")

    assert event_types(sink)[:3] == [
        TraceEventType.RUN_START,
        TraceEventType.COMPRESSION,
        TraceEventType.LLM_REQUEST,
    ]
    event = sink.events[1]
    assert event.compression_status == "succeeded"
    assert event.summary_updated is True


@pytest.mark.asyncio
async def test_compression_exception_emits_fallback_and_run_continues() -> None:
    sink = InMemoryTraceSink()
    fake = ScriptedFakeLLM(
        [final()],
        summary_results=[RuntimeError("provider summary details")],
    )
    session = state()
    session.history.extend(completed_turn("one") + completed_turn("two"))

    result = await make_runtime(
        fake,
        sink,
        compression_trigger=1,
        recent_turns_to_keep=1,
    ).run(session, "Current")

    event = sink.events[1]
    assert result.status is AgentRunStatus.COMPLETED
    assert event.event_type is TraceEventType.COMPRESSION
    assert event.compression_status == "fallback"
    assert event.failure_kind == "summary_exception"
    assert TraceEventType.ERROR not in event_types(sink)


@pytest.mark.asyncio
async def test_empty_summary_emits_specific_fallback_kind() -> None:
    sink = InMemoryTraceSink()
    fake = ScriptedFakeLLM([final()], summary_results=["   "])
    session = state()
    session.history.extend(completed_turn("one") + completed_turn("two"))

    await make_runtime(
        fake,
        sink,
        compression_trigger=1,
        recent_turns_to_keep=1,
    ).run(session, "Current")

    event = sink.events[1]
    assert event.compression_status == "fallback"
    assert event.failure_kind == "empty_summary"


class FailingCheckpoint:
    def __init__(self, fail_on: int) -> None:
        self.fail_on = fail_on
        self.calls = 0

    async def __call__(self, session: SessionState) -> None:
        del session
        self.calls += 1
        if self.calls == self.fail_on:
            raise SessionStoreError("database details must not leak")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("results", "fail_on", "expected_stage"),
    [
        ([final()], 1, "user_checkpoint"),
        ([final()], 2, "final_checkpoint"),
        (
            [
                tool_calls(
                    "resp-1",
                    call("c1", "calculator", '{"expression":"1+1"}'),
                ),
                final(),
            ],
            2,
            "tool_checkpoint",
        ),
    ],
)
async def test_checkpoint_failures_emit_safe_error_event(
    results: list[LLMResult],
    fail_on: int,
    expected_stage: str,
) -> None:
    sink = InMemoryTraceSink()

    with pytest.raises(SessionStoreError):
        await make_runtime(ScriptedFakeLLM(results), sink).run(
            state(),
            "Run",
            checkpoint=FailingCheckpoint(fail_on),
        )

    error = sink.events[-1]
    assert error.event_type is TraceEventType.ERROR
    assert error.error == {
        "type": "SessionStoreError",
        "code": "session_store",
        "stage": expected_stage,
        "message": "agent operation failed",
    }


@pytest.mark.asyncio
async def test_adapter_protocol_error_is_traced_then_propagated() -> None:
    sink = InMemoryTraceSink()
    fake = ScriptedFakeLLM([LLMProtocolError("malformed provider response")])

    with pytest.raises(LLMProtocolError):
        await make_runtime(fake, sink).run(state(), "Calculate")

    assert event_types(sink)[-2:] == [TraceEventType.LLM_REQUEST, TraceEventType.ERROR]
    assert sink.events[-1].error["type"] == "LLMProtocolError"
    assert sink.events[-1].error["stage"] == "llm_complete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("domain_error", "expected_code"),
    [
        (LLMTimeoutError(), "llm_timeout"),
        (LLMConnectionError(), "llm_connection"),
        (
            LLMRateLimitError(status_code=429, request_id="req-rate"),
            "llm_rate_limit",
        ),
        (
            LLMProviderError(status_code=500, request_id="req-provider"),
            "llm_provider",
        ),
    ],
)
async def test_provider_errors_use_stable_safe_trace_codes(
    domain_error: LLMProviderError,
    expected_code: str,
) -> None:
    sink = InMemoryTraceSink()

    with pytest.raises(type(domain_error)):
        await make_runtime(ScriptedFakeLLM([domain_error]), sink).run(
            state(), "Run"
        )

    error = sink.events[-1].error
    assert error["code"] == expected_code
    assert error.get("status_code") == domain_error.status_code
    assert error.get("request_id") == domain_error.request_id


@pytest.mark.asyncio
async def test_sdk_provider_body_never_reaches_serialized_trace() -> None:
    private = "PROVIDER_PRIVATE_BODY_MUST_NOT_LEAK"
    request = httpx.Request(
        "POST", "https://api.deepseek.com/chat/completions"
    )
    response = httpx.Response(
        500,
        request=request,
        headers={"x-request-id": "req-safe"},
        json={"error": {"message": private}},
    )
    sdk_error = InternalServerError(
        private,
        response=response,
        body={"private": private},
    )

    class ErrorCompletions:
        async def create(self, **kwargs):
            del kwargs
            raise sdk_error

    class ErrorChat:
        completions = ErrorCompletions()

    class ErrorSDK:
        chat = ErrorChat()

    adapter = DeepSeekChatClient(
        Settings(deepseek_model="deepseek-v4-flash"),
        client=ErrorSDK(),  # type: ignore[arg-type]
    )
    sink = InMemoryTraceSink()

    with pytest.raises(LLMProviderError):
        await make_runtime(adapter, sink).run(  # type: ignore[arg-type]
            state(), "Run"
        )

    serialized = json.dumps(
        [event.model_dump(mode="json") for event in sink.events]
    )
    assert private not in serialized
    assert sink.events[-1].error["code"] == "llm_provider"
    assert sink.events[-1].error["status_code"] == 500
    assert sink.events[-1].error["request_id"] == "req-safe"


@pytest.mark.asyncio
@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
async def test_nonstandard_json_tool_args_trace_as_invalid(
    constant: str,
) -> None:
    sink = InMemoryTraceSink()
    fake = ScriptedFakeLLM(
        [
            tool_calls(
                "resp-1",
                call("c1", "calculator", f'{{"expression":{constant}}}'),
            ),
            final(),
        ]
    )

    await make_runtime(fake, sink).run(state(), "Calculate")

    start = next(
        event for event in sink.events if event.event_type is TraceEventType.TOOL_START
    )
    result = next(
        event for event in sink.events if event.event_type is TraceEventType.TOOL_RESULT
    )
    assert start.tool_args == {"parse_status": "invalid_json"}
    assert result.tool_result["error"]["code"] == "invalid_json"


class ExplodingTraceSink:
    async def emit(self, event: TraceEvent) -> None:
        del event
        raise RuntimeError("trace destination unavailable")


@pytest.mark.asyncio
async def test_trace_sink_failure_never_changes_agent_result() -> None:
    result = await make_runtime(
        ScriptedFakeLLM([final("Still completed")]),
        ExplodingTraceSink(),
    ).run(state(), "Hi")

    assert result.status is AgentRunStatus.COMPLETED
    assert result.final_answer == "Still completed"


@pytest.mark.asyncio
async def test_json_logging_sink_emits_one_json_record(caplog) -> None:
    logger = logging.getLogger("test.minimal-agent.trace")
    sink = JsonLoggingTraceSink(logger)
    event = TraceEvent(
        event_type=TraceEventType.RUN_START,
        user_id="user",
        session_id="session",
        turn_id="turn",
    )

    with caplog.at_level(logging.INFO, logger=logger.name):
        await sink.emit(event)

    payload = json.loads(caplog.records[-1].message)
    assert payload["event_type"] == "run_start"
    assert payload["turn_id"] == "turn"
