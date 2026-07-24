# Phase 5 Task — Sessions and Context

## Problem solved

Make conversations durable and isolated while keeping LLM requests within a predictable context budget.

## Planned implementation

- Implement `SQLiteSessionStore` with schema initialization and composite primary key `(user_id, session_id)`.
- Serialize one validated `SessionState` JSON payload and save it atomically with timestamps/version.
- Implement create/get/save/delete lifecycle; `get` and `delete` on absent identity raise `SessionNotFoundError` where appropriate.
- Save after user acceptance, after each tool-result batch, and at terminal completion.
- Implement a turn-aware token estimator/selector and summary boundary. The estimate must include the system prompt, serialized tool schemas, summary/recent conversation, and response token reserve.
- Call `LLMClient.summarize()` only for completed old turns when the full request estimate reaches the trigger.
- Make summarization a text-only LLM operation that sends no tool schemas, disables tool calling, and returns only summary text.
- On compression failure, keep the prior summary and construct the largest safe complete-turn suffix, always retaining the current user turn.
- Verify todo state is stored inside each session payload.

## Deferred provider validation

Phase 5 must not assume that locally replaying historical `function_call` and
`function_call_output` items is sufficient for a new user turn with reasoning
models. Whether matching reasoning items must also be replayed when no
`previous_response_id` is used remains an explicit Phase 5/7 real-API integration
test question. Do not add reasoning persistence or a provider-state store before
that test provides evidence.

## Key trade-offs

- Full raw history remains durable; only LLM input is compressed.
- Character-based token estimation is explainable but approximate.
- V1 documents single-process, non-concurrent writes to one session; distributed locking and conflict retries are out of scope.

## Verification

- Use temporary databases for isolation, recovery, delete, timestamp, and round-trip tests.
- Capture fake-LLM inputs for pronoun follow-up, tool-result follow-up, and compression behavior.
- Assert compression budgeting changes when system-prompt size, tool-schema size, or response reserve changes even if conversation history is unchanged.
- Assert the summarization request cannot carry tool schemas or produce tool calls.
- Force summarizer failure and verify the run can continue within the fallback budget.

## Acceptance criteria

- Same user/different session and different user/same session never share history or todo state.
- Reopening a store instance resumes visible history and todo state.
- Follow-ups receive sufficient recent tool and conversation context.
- Compression advances a summary boundary without deleting stored raw history.
- Context sent for completion fits the configured limit after accounting for fixed prompt/schema costs and reserved response tokens.
- A compression failure is observable but does not by itself fail the user turn.
