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
- Configure provider timeout and at most one simple retry only if adapter evidence shows a transient timeout is safe; do not add a retry framework.

## Key trade-offs

- A single optional-field event model is easier to inspect than a class hierarchy.
- Tool errors remain result data; infrastructure failures remain exceptions.
- Full development payload tracing is configurable because prompts/results can contain user data.

## Verification

- Assert event order and required correlation fields for success, tool failure, timeout, compression failure, and max steps.
- Assert secrets and private reasoning fixture text do not appear in serialized traces.
- Verify the HTTP boundary maps known domain errors consistently.

## Acceptance criteria

- The required trace fields are observable for every relevant event.
- Every listed exception scenario has a defined runtime or API outcome.
- Errors never leave a session payload partially malformed.
- Private chain-of-thought and credentials are absent from normal traces.
