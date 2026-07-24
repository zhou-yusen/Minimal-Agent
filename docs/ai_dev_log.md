# AI Prompt and Problem-Solving Log

This file records only decisions or problems with technical value. It is not a chronological activity log.

## 2026-07-24 — Separate durable history from bounded LLM context

### Problem

The system must resume old sessions while also respecting a context threshold. Treating conversation history and LLM context as the same list either loses recoverability or grows requests without bound.

### Analysis

Session recovery needs durable visible history and tool state. Model inference only needs the stable prompt, a summary of older completed turns, and a recent raw suffix. Tool-call/result pairs cannot be split during compression.

### Options

1. Delete old messages after summarization.
2. Keep all raw messages and select only summary plus recent complete turns for each LLM request.
3. Normalize every message and summary revision into an event-sourced database.

### Decision

Keep complete raw history in the SQLite session JSON, store a rolling summary plus its message boundary, and bound only request construction. This is the smallest design that supports recovery, inspection, and compression without event sourcing.

### AI Assistance

AI compared storage and context responsibilities, identified the tool-pair boundary requirement, and proposed the summary cursor model.

### Verification

Phase 5 tests will reopen a session, verify old raw history remains stored, and assert that the fake LLM receives only the summary plus configured recent complete turns after compression.

## 2026-07-24 — Normalize LLM responses without storing hidden reasoning

### Problem

The runtime must distinguish final answers, tool calls, and reasoning/internal decision information, but it must not depend on or permanently store private chain-of-thought.

### Analysis

Provider response formats are not stable domain models. The runtime only requires visible answer text, function calls, call IDs, and whether reasoning metadata was present. Persisting provider payloads would couple sessions to one SDK and may expose sensitive reasoning.

### Options

1. Store raw provider responses directly in session history.
2. Ask the model to emit a custom JSON envelope containing reasoning.
3. Normalize visible text and tool calls, record only `reasoning_present` and any explicitly safe provider summary.

### Decision

Use option 3 inside `LLMClient`. Hidden reasoning text is transient and excluded from session state and normal trace payloads.

### AI Assistance

AI separated provider parsing from runtime control flow and defined the minimum normalized `LLMResult` fields.

### Verification

Adapter tests will provide responses containing text, function calls, and reasoning metadata, then assert correct classification and absence of private reasoning text from persisted messages and trace events.

## 2026-07-24 — Architecture review: request budgeting and one provider protocol

### Problem

The frozen v1 text treated conversation length as the main compression signal, left summarization tool behavior implicit, and proposed integration tests that could fail when a real model reasonably answered arithmetic without calling `calculator`. It also named an official SDK adapter without fixing one API protocol.

### Analysis

The actual request consumes tokens from the system prompt and serialized tool schemas as well as conversation, while output needs reserved capacity. Summarization should never enter the agent tool loop. Real-model routing is probabilistic, but correct `call_id` linkage in the Responses API is a deterministic integration boundary the project must prove.

### Options

1. Keep approximate history-only budgeting and accept model-dependent integration tests.
2. Add a universal provider abstraction and provider-specific budget/token implementations.
3. Preserve the architecture while making the full request estimate explicit, restricting summary calls to text only, pinning the real adapter to OpenAI Responses API, and separating offline routing tests from forced-tool protocol integration tests.

### Decision

Use option 3. The four review corrections are contract clarifications inside the existing `LLMClient`, `ContextManager`, and test boundaries; they do not add a new component or change state ownership.

### AI Assistance

AI translated the review comments into testable request-budget, summarization, integration-test, and tool-call correlation rules and checked the Responses API `call_id` linkage against official OpenAI guidance.

### Verification

Phase 2 exposes separate completion and text-only summary request contracts plus response-reserve configuration. Phases 4, 5, and 7 will test exact `call_id` round trips, full-request budget accounting, summary calls without tools, and real SDK protocol continuity without autonomous calculator selection.

## 2026-07-24 — Phase 2: keep credentials out of the import path

### Problem

The project needs a real OpenAI SDK dependency and centralized configuration, but offline tests and `import minimal_agent` must work without `OPENAI_API_KEY`.

### Analysis

Constructing an SDK client or a required settings singleton during module import would couple package importability to process credentials. It would also make tests order-dependent because importing a module would capture environment state.

### Options

1. Require `OPENAI_API_KEY` in the settings model and load a global settings instance at import time.
2. Load `.env` implicitly and inject a placeholder key for tests.
3. Keep the key optional in validated settings, load settings explicitly with `Settings.from_env()`, and defer credential enforcement and SDK construction to the Phase 4 real adapter.

### Decision

Use option 3. `SecretStr` prevents accidental plain-text representation, package import has no environment side effect, and the real adapter will own the clear missing-credential error at construction or first use.

### AI Assistance

AI identified the import-time credential coupling, kept environment parsing restricted to documented names, and added validation for the context-limit relationships without constructing later-phase services.

### Verification

With `OPENAI_API_KEY` absent, the installed package imported successfully and the offline suite passed. Configuration tests also verified environment conversion and rejected invalid loop, timeout, compression, and response-reserve limits.

## 2026-07-24 — Phase 3: bound calculator work and enforce JSON at the tool boundary

### Problem

Rejecting unsafe Python syntax is not sufficient for a calculator tool: a syntactically allowed expression can still consume excessive work or create an unusably large number. Separately, a tool or Pydantic validation detail can contain a Python object that cannot be returned as JSON to an LLM API.

### Analysis

The calculator needs both a syntax whitelist and resource ceilings. The Registry is the common boundary for every tool, so it is the correct place to guarantee that successful outputs and failed validation details are JSON-safe before a future Runtime sees them.

### Options

1. Use a restricted expression helper library and trust each tool to return JSON.
2. Validate AST node types only and serialize results later in the provider adapter.
3. Evaluate a small AST recursively with length, node-count, depth, literal, and result limits, while making `ToolRegistry` reject non-JSON tool outputs and sanitize validation details.

### Decision

Use option 3. Calculator accepts only numeric constants, `+`, `-`, `*`, `/`, parentheses, and unary signs. Expressions are limited to 256 characters, 64 AST nodes, depth 12, literals up to `1e12`, and results up to `1e15`. Registry preserves ordinary failures as structured `ToolResult` values and never includes tracebacks.

### AI Assistance

AI identified the difference between syntax safety and computational bounds, defined explainable ceilings, and centralized JSON normalization without introducing a tool base class or plugin system.

### Verification

Tests covered arithmetic, calls, names, attributes, comprehensions, unsupported operators, invalid syntax, division by zero, excessive AST size, oversized results, non-JSON tool output, validation details, and JSON serialization of successful and failed results. The full suite passed, and a source scan found no `eval(` or `exec(` usage.

## 2026-07-24 — Phase 3 hotfix: preserve the Session tool-state object

### Problem

`ToolContext` was a Pydantic model. Constructing it with `ToolContext(tool_state=session.tool_state)` could copy the top-level dictionary, so Todo's first `setdefault()` mutation was not guaranteed to appear in `SessionState.tool_state` for later persistence.

### Analysis

`ToolContext` is an internal execution carrier, not an external DTO. Its central requirement is reference identity, while validation and serialization belong to the Session and Tool argument/result models.

### Options

1. Copy `context.tool_state` back into the Session after every tool call.
2. Configure Pydantic copy behavior and depend on model internals.
3. Use a small stdlib dataclass that stores the exact dictionary reference.

### Decision

Use option 3. Runtime will not need a compensating assignment, and Todo mutations directly affect the state object that Phase 5 will persist.

### AI Assistance

AI reproduced the ownership issue from the model semantics, changed only the internal context carrier, and added an identity-based regression test without modifying `SessionState`.

### Verification

A SessionState-backed ToolContext satisfies `context.tool_state is session.tool_state`; Todo add appears immediately in the Session state, while a second SessionState remains empty. The hotfix suite passed all 60 tests.

## 2026-07-24 — Phase 4A: active-run continuation without reasoning persistence

### Problem

Reasoning-model function calls require provider continuity across the tool-result request, but storing or replaying hidden reasoning would violate the project's privacy and Session ownership rules.

### Analysis

The Responses API can chain the next request through `previous_response_id`. The provider can then recover its prior response context while the application submits only new function-call outputs with the original `call_id`. Local Session history still stores only visible messages, normalized tool calls, and tool results.

### Options

1. Persist raw response and reasoning items in Session history.
2. Manually replay provider reasoning items without storing them long term.
3. Carry the prior response ID only inside the active Agent run and map it to `previous_response_id`.

### Decision

Use option 3. `LLMRequest.continuation_id` is transient and absent from `SessionState`; `LLMResult.provider_response_id` supplies the next active-run continuation value. The adapter records only `reasoning_present` and never reads reasoning text into domain output.

Completion requests explicitly use `store=True` so active-run tool continuations
can resolve `previous_response_id`; text-only summarization remains `store=False`.
Cross-turn local replay requirements for reasoning items remain deferred to a real
API integration test and do not justify reasoning persistence in Phase 4.

### AI Assistance

AI verified the official Responses API continuation and `call_id` contracts, inspected the installed SDK's concrete response types, implemented the single adapter, and kept the network seam injectable for offline tests.

### Verification

Adapter tests use actual OpenAI SDK Response, message, output-text, reasoning, function-call, and usage model classes while faking only `responses.create`. The first run exposed a real SDK 2.47.0 fixture mismatch: `InputTokensDetails.cache_write_tokens` is required. After updating the fixture to the installed SDK structure, all 10 adapter tests and all 71 project tests passed. A privacy fixture confirmed that `PRIVATE_REASONING_MUST_NOT_LEAK` is absent from serialized domain results.

## 2026-07-24 - Phase 4B: separate local history from active-run continuation input

### Problem

After the first tool-call response, sending both `previous_response_id` and the
full local history would duplicate the user message and prior function-call items
inside the provider's active response chain.

### Analysis

The first request needs local Session context because a new run has no provider
continuation. Later requests have a different role: they supply only the newly
executed function outputs while the response ID links prior provider state.
Request-level instructions, tool definitions, and the output limit remain explicit
on every call. A missing response ID makes safe continuation impossible.

### Options

1. Resend full history on every request and also set `previous_response_id`.
2. Fall back to full-history replay when a tool-call response has no provider ID.
3. Use full local context only on the first request, then require a provider ID and
   send only newly produced tool-result messages during the active run.

### Decision

Use option 3. The continuation ID remains a local variable in `AgentRuntime.run()`
and is never stored in `SessionState`. Invalid tool-call responses fail before tool
execution, avoiding side effects whose results cannot be returned to the model.

### AI Assistance

AI isolated provider continuation from durable Session history, defined the exact
request shape for each loop round, and identified the missing-ID side-effect guard.

### Verification

Runtime tests assert first-round full context, continuation rounds containing only
new tool messages, repeated request configuration, immediate response-ID chaining,
and no Todo mutation when a tool-call response lacks a provider ID.

## 2026-07-24 - Phase 5B: treat recent-turn retention as a soft target

### Problem

Keeping a configured number of recent turns does not guarantee that the complete
request fits: the system prompt, tool schemas, summary, individual turns, and
reserved output tokens can each consume the remaining context window.

### Analysis

A compression trigger decides when summarization is worth attempting, while the
hard context limit is an independent safety boundary. Summary generation may fail
or return text that is still too large. Cropping individual messages would break
tool-call/result pairs and could silently truncate the current user request.

### Options

1. Always retain `recent_turns_to_keep`, even when the request exceeds the limit.
2. Truncate strings or individual messages until the estimate fits.
3. Estimate the full request, summarize only older completed turns, then remove
   oldest complete raw turns as needed while always retaining the incomplete turn.

### Decision

Use option 3. Recent-turn retention is a priority target rather than a hard
guarantee. A temporary labeled Assistant message represents the persisted summary;
raw history remains unchanged. If mandatory system, schema, current-turn, and
output-reserve content cannot fit, fail with `ContextWindowExceededError` instead
of truncating user input.

### AI Assistance

AI separated trigger and hard-limit semantics, defined completed-turn boundaries,
and designed non-destructive fallback behavior for exceptions and empty summaries.

### Verification

Context tests cover full-request budget components, rolling boundaries, intact
tool-call/result turns, failed and oversized summaries, reduced recent suffixes,
immutable raw history, and an explicit impossible-request error.
