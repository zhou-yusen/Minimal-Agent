# Phase 4 Task — Agent Runtime Loop

Phase 4 delivered the provider adapter and `AgentRuntime`. Phase 7A replaces the
original Responses-specific adapter with the frozen V1 DeepSeek Chat Completions
adapter while preserving the runtime/tool boundaries established here.

## Problem solved

Implement the required LLM/tool feedback cycle with explicit termination and no Agent Framework.

## Planned implementation

- Implement one real adapter only: DeepSeek Chat Completions through the official
  OpenAI Python SDK's compatible client. Do not add a provider registry or a
  second provider adapter.
- Normalize Chat Completions tool calls while preserving each exact provider
  `tool_call.id` as the internal `tool_call_id` and matching tool-result ID.
- Detect reasoning presence without reading or persisting reasoning content, and
  implement text-only summarization with tools omitted and thinking disabled.
- Implement `AgentRuntime.run()` with a `for` loop bounded by `max_steps`.
- On the first round, send the current bounded local context. On later rounds in
  the same active run, replay that bounded sequence plus every newly produced
  assistant tool-call message and complete tool-result batch.
- Repeat the system prompt, registry definitions, and output-token limit on every
  request.
- Persist/forward assistant tool calls, execute all calls sequentially, append matching tool results, and continue.
- Return only a response with no tool calls and non-empty visible text as `completed`.
- Detect empty/unsupported responses as `LLMProtocolError`.
- If the final allowed LLM round returns tool calls, execute and append every call
  result, make no additional LLM request, then append a deterministic terminal
  message and return `status=max_steps`.
- Keep session/context implementations minimal fakes here; durable behavior belongs to Phase 5.

## Key trade-offs

- One step is one LLM request, not one individual tool call.
- Tool calls take precedence over accompanying text; the loop continues until a call-free final response.
- No automatic planning/reflection calls are added.

## Verification

- Script fake responses for direct answer, one tool, two sequential tool rounds, multiple calls in one round, invalid arguments, execution failure, and endless tool calls.
- Assert exact call IDs and results appear in the next fake-LLM request.
- Add an adapter protocol test proving a Chat Completions tool call and matching
  tool result retain the same `tool_call_id`.

## Acceptance criteria

- The complete required loop works without a framework.
- A tool error returns to the LLM and can be repaired on the next round.
- The runtime cannot exceed configured LLM decision rounds.
- Each subsequent active-run request replays one coherent bounded message sequence.
- No branch selects a tool based on user text.
- The only real provider/API path is DeepSeek Chat Completions, and tool-call/result correlation is lossless.
