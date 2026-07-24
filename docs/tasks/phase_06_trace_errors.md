# Phase 6 Task — Trace and Exception Handling

## Problem solved

Make every agent run diagnosable and ensure boundary failures have predictable behavior.

## Planned implementation

- Implement one validated `TraceEvent` model and `TraceSink.emit()` protocol.
- Add a JSON logger sink and an in-memory sink for tests.
- Emit run, LLM, tool, compression, error, and finish events with `session_id`, `turn_id`, and `loop_step` correlation.
- Measure LLM/tool/run latency with a monotonic clock.
- Sanitize LLM requests and error fields; never include API keys, headers, environment dumps, or stack traces in tool results.
- Define and map `SessionNotFoundError`, `LLMTimeoutError`, `LLMProtocolError`, `SessionStoreError`, and context errors.
- Keep provider retry disabled until Phase 7 real-API evidence justifies changing
  the one-attempt contract; do not add a retry framework.

## Phase 6A observability completed

- `AgentRuntime` optionally emits correlated `TraceEvent` records for run, context
  compression, LLM, tool, error, and finish boundaries.
- LLM request events contain only structural metadata: counts, roles, tool names,
  and output limit. They exclude prompt and message text.
- `ContextManager.build()` returns a provider-neutral `ContextBuildResult` so the
  runtime can correlate successful or fallback compression without injecting a
  trace dependency into context selection.
- Trace emission is best-effort. Sink exceptions are ignored without retry and
  cannot alter checkpoints, LLM calls, tools, or the returned run result.
- `InMemoryTraceSink` supports deterministic tests; `JsonLoggingTraceSink` writes
  one compact JSON event per stdlib logging record.

## Phase 6B provider failures and interrupted turns completed

- The OpenAI Python SDK client used for DeepSeek is constructed with
  `max_retries=0`; one adapter call is one provider attempt. No application retry
  has been added.
- SDK timeout, connection, rate-limit, status, and general API failures map to
  stable safe domain errors. Only status code and request ID may cross the adapter
  boundary; provider bodies, headers, and raw exception text do not.
- A new run seals a trailing incomplete turn with a deterministic Runtime
  assistant marker before accepting the new user message. The marker is
  checkpointed first and old tool calls are never replayed.
- Recovery emits one metadata-only event. Error traces use stable domain codes
  instead of Python class names as business classifications.
- Strict trace argument parsing now rejects NaN and infinities consistently with
  `ToolRegistry`.

Automatic retry remains disabled. A future external side-effect tool requires an
explicit idempotency or transaction strategy: V1 does not roll back a tool whose
subsequent local checkpoint fails, and does not add an outbox or distributed
transaction framework.

## Key trade-offs

- A single optional-field event model is easier to inspect than a class hierarchy.
- Tool errors remain result data; infrastructure failures remain exceptions.
- Full development payload tracing is configurable because prompts/results can contain user data.

## Verification

- Assert event order and required correlation fields for success, tool failure, timeout, compression failure, and max steps.
- Assert secrets and private reasoning fixture text do not appear in serialized traces.
- Defer HTTP status mapping until an actual API adapter is introduced.

## Acceptance criteria

- The required trace fields are observable for every relevant event.
- Every listed exception scenario has a defined runtime or API outcome.
- Errors never leave a session payload partially malformed.
- Private chain-of-thought and credentials are absent from normal traces.
