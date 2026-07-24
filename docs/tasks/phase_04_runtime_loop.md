# Phase 4 Task — Agent Runtime Loop

Phase 4 is delivered in two bounded steps: Phase 4A implements only the OpenAI Responses Adapter; Phase 4B later implements `AgentRuntime`.

## Problem solved

Implement the required LLM/tool feedback cycle with explicit termination and no Agent Framework.

## Planned implementation

- Implement one real adapter only: the OpenAI Responses API through the official Python SDK. Do not add adapters or compatibility layers for Chat Completions, Claude, Gemini, or other providers.
- Normalize Responses API function calls while preserving the exact provider `call_id` as the internal `tool_call_id`; send the same identifier in the matching function-call output on the next request.
- Phase 4A maps transient `LLMRequest.continuation_id` to `previous_response_id`, detects reasoning items without storing them, and implements text-only summarization. It does not implement the runtime loop.
- Phase 4B uses each tool-call response's `provider_response_id` only for the next call in the same active run; it starts the next user turn from local bounded Session context with no provider continuation ID.
- Implement `AgentRuntime.run()` with a `for` loop bounded by `max_steps`.
- On the first round, send the current bounded/local context with no continuation
  ID. On later rounds in the same active run, send only the newly produced tool
  result messages with the immediately previous provider response ID.
- Repeat the system prompt, registry definitions, and output-token limit on every
  request; provider continuation does not replace request-level configuration.
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
- Add an adapter protocol test proving a Responses API function call and its function-call output retain the same `call_id`.

## Acceptance criteria

- The complete required loop works without a framework.
- A tool error returns to the LLM and can be repaired on the next round.
- The runtime cannot exceed configured LLM decision rounds.
- Continuation requests do not duplicate the user message or prior local history.
- No branch selects a tool based on user text.
- The only real provider/API path is OpenAI Responses API, and tool-call/result correlation is lossless.
