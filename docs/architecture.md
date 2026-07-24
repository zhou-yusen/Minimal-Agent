# Phase 1 — Requirements and Frozen Architecture

Status: **Frozen v1, Provider Revision A**
Date: 2026-07-24

System and runtime flow diagrams: [`docs/diagrams.md`](diagrams.md).

This document is the architecture baseline for later phases. A change requires concrete implementation or test evidence, a minimal replacement decision, and an entry in `docs/ai_dev_log.md`.

## 1. Requirement breakdown

### 1.1 Runtime behavior

The runtime accepts one user message for an existing `(user_id, session_id)`, loads that session, builds bounded context, and asks a real LLM to either answer or emit schema-based tool calls. It executes every returned call, sends structured results back to the LLM, and repeats until a final answer or a configured maximum number of LLM decision rounds is reached.

The runtime, rather than the API layer or LLM adapter, owns loop termination.

### 1.2 Tool system

The first registry contains exactly three user-facing tools:

- `calculator`: safely evaluates a small arithmetic expression language.
- `search`: queries an injected deterministic mock backend in v1.
- `todo`: adds, lists, completes, and deletes items stored in the current session.

Every tool provides `name`, `description`, JSON-compatible parameter schema, and async `execute()`. The runtime always gives the LLM the schemas; it never checks user keywords to route a request.

Tool-call arguments pass through JSON parsing and Pydantic validation before execution. Invalid JSON, unknown tool names, validation failures, and execution exceptions all become a common failed `ToolResult` associated with the original call ID. The next LLM round receives that result and can repair the call or answer.

### 1.3 Sessions and durable state

The identity key is `(user_id, session_id)`. Two sessions owned by the same user and identical session IDs owned by different users remain isolated.

`SessionState` contains:

- visible conversation history, including tool calls and results;
- session-scoped `tool_state`, initially todo items;
- a rolling summary and the message boundary it covers;
- `created_at`, `updated_at`, and a persistence version.

SQLite stores one JSON payload per session plus indexed identity/timestamp columns. This deliberately avoids premature message normalization while keeping `SessionStore` replaceable. Saving one session is one transaction.

Creating a session and running it are separate operations. Running a missing session raises `SessionNotFoundError`; the HTTP adapter maps it to 404. A demo helper may explicitly create a session before calling the runtime, but the runtime never creates one implicitly.

### 1.4 Context management

The persisted history may remain complete, but the complete LLM request is bounded. `ContextManager` composes, in order:

1. stable system prompt;
2. existing conversation summary, clearly labeled as summary;
3. recent complete turns after the summary boundary;
4. the current user turn and any tool exchanges produced during the active run.

A turn starts at a user message and includes subsequent assistant/tool messages until the next user message. Selection never splits an assistant tool call from its tool result.

Token size uses an injectable estimator. V1 uses a documented character heuristic so the core does not depend on a provider tokenizer. Budget decisions estimate the entire request footprint, not conversation history alone:

`system prompt + serialized tool schemas + summary/recent conversation + response token reserve`

Configuration supplies `context_token_limit`, `compression_trigger`, `response_token_reserve`, and `recent_turns_to_keep`; initial defaults will be 8,000, 6,000, 1,000, and 4. Compression is considered when the estimated total above reaches the trigger. Final context selection must also fit within `context_token_limit` after reserving response tokens.

At the trigger, completed old turns are summarized through `LLMClient.summarize()`. Summarization is a dedicated text-only request: it carries no tool schemas, disables tool calling, and returns summary text only. The summary replaces the previous summary and advances `summary_up_to_message_id`; raw history remains in SQLite but is no longer sent. If compression fails, the runtime emits a trace error, retains the previous summary, and uses the largest suffix of complete recent turns that fits. The user turn is never discarded.

### 1.5 Trace and errors

Every run has a `turn_id`; every LLM decision round has a one-based `loop_step`. Structured trace events cover run start, sanitized LLM request, response classification, tool start/result, compression, error, and run finish.

Expected boundary failures use typed errors. Tool failures are data and return to the LLM. Missing sessions, persistent store failures, or an unavailable LLM cross the runtime boundary as domain exceptions. Maximum-step exhaustion is a controlled `AgentRunResult(status="max_steps")`, not an uncaught exception.

### 1.6 Testing

The runtime depends on interfaces, so scripted fake LLM responses can deterministically drive every branch, including autonomous calculator selection. The normal test suite is offline. An opt-in integration suite exercises the official SDK against a real model without asserting exact wording or depending on the model to autonomously select `calculator`; its primary purpose is verifying a complete real tool-call/tool-result protocol round trip.

## 2. Minimal system architecture

```text
HTTP / CLI adapter
       |
       v
 AgentService  ---- create/get/delete ----> SessionStore (SQLite)
       |
       v
 AgentRuntime
   |       |             |                 |
   |       |             |                 +--> TraceSink (JSON logging)
   |       |             +--> ContextManager --> LLM summary when needed
   |       +--> ToolRegistry --> calculator / search / todo
   +--> LLMClient (official SDK adapter)
```

`AgentService` is deliberately thin: session lifecycle plus delegation to `AgentRuntime`. There is no repository layer beneath SQLite and no event bus between components.

### Replaceable boundaries

| Boundary | V1 implementation | Reason it exists |
|---|---|---|
| `LLMClient` | DeepSeek Chat Completions via the OpenAI Python SDK | Deterministic tests and normalization of the one provider protocol used by this project |
| `SessionStore` | SQLite JSON store | Session isolation, recovery, and future storage replacement |
| `SearchBackend` | Deterministic mock corpus | Search is allowed to be mocked and stays testable |
| `TraceSink` | Structured Python logger | Tests capture events without scraping log text |

Everything else remains concrete until a test demonstrates a second implementation is useful.

## 3. Agent Runtime data flow

```text
1. API receives user_id, session_id, text
2. Store.get(composite key); fail clearly if absent
3. Append and save user message
4. ContextManager compresses/selects context if needed
5. For loop_step = 1..max_steps:
   a. Trace sanitized LLM request
   b. LLMClient.complete(context, tool schemas)
   c. Normalize response items; note but do not persist private reasoning
   d. If tool calls exist:
      - persist assistant tool-call message
      - for each call: parse -> validate -> execute
      - turn every outcome into ToolResult
      - append results, save state, and continue
   e. If visible final text exists:
      - persist assistant answer and save state
      - emit finish trace and return status=completed
   f. Otherwise raise an LLM protocol error
6. If all rounds requested tools:
   - persist a deterministic terminal assistant message
   - emit max_steps error/finish traces
   - return status=max_steps
```

Multiple tool calls in one LLM response execute sequentially in response order. This avoids mutation races for `todo` and keeps trace order deterministic. They share the same `loop_step`; the following LLM request receives all results.

State is saved at three durable milestones: after accepting the user message, after each tool-call batch, and after the terminal answer. This makes session continuation explainable without introducing checkpoints or event sourcing.

## 4. Core data structures

The names below are the frozen domain vocabulary; field details may receive small implementation adjustments.

### Messages and LLM decisions

```text
ConversationMessage
  id: str
  role: user | assistant | tool
  content: str | None
  tool_calls: list[ToolCall]
  tool_call_id: str | None
  tool_name: str | None
  created_at: datetime

ToolCall
  id: str
  name: str
  arguments_json: str

LLMResult
  assistant_message: ConversationMessage
  response_type: final | tool_calls
  reasoning_present: bool
  usage: TokenUsage | None
  provider_response_id: str | None
```

`LLMRequest` contains no provider continuation state. DeepSeek's OpenAI-compatible
Chat Completions protocol requires message replay: the first request uses bounded
context from `ContextManager`, and each subsequent request in the same active run
replays that context plus every normalized assistant tool-call and tool-result
message produced so far.

The only v1 adapter is `DeepSeekChatClient`; this is not a universal provider
registry. It uses `deepseek-v4-flash` at `https://api.deepseek.com`, maps the
provider-neutral output reserve to `max_tokens`, and sends thinking disabled on
completion and summary calls. `ToolCall.id` preserves the provider call ID and the
matching role=`tool` message returns the same ID. Raw DeepSeek messages and
`reasoning_content` are never persisted. Thinking remains disabled because
thinking plus tools would require replaying reasoning content, which conflicts
with the project's private-reasoning contract.

### Tools

```text
ToolDefinition
  name: str
  description: str
  parameters_schema: dict

ToolContext
  user_id: str
  session_id: str
  tool_state: dict

ToolResult
  tool_call_id: str
  tool_name: str
  ok: bool
  output: JSON value | None
  error: ToolError | None
  latency_ms: float

ToolError
  code: invalid_json | unknown_tool | validation_error | execution_error
  message: str
  details: JSON value | None
```

### Session and runtime

```text
SessionState
  user_id: str
  session_id: str
  history: list[ConversationMessage]
  tool_state: dict
  summary: str | None
  summary_up_to_message_id: str | None
  created_at: datetime
  updated_at: datetime
  version: int

AgentRunResult
  user_id: str
  session_id: str
  turn_id: str
  status: completed | max_steps
  final_answer: str
  loop_steps: int
```

### Trace

```text
TraceEvent
  timestamp: datetime
  event_type: run_start | llm_request | llm_response | tool_start |
              tool_result | compression | error | run_finish
  session_id: str
  user_id: str
  turn_id: str
  loop_step: int | None
  latency_ms: float | None
  llm_request: sanitized JSON | None
  llm_response_type: final | tool_calls | protocol_error | None
  reasoning_present: bool | None
  tool_name: str | None
  tool_args: JSON value | None
  tool_result: JSON value | None
  error: structured JSON | None
  final_answer: str | None
```

One model with optional fields is intentionally chosen over an event-class hierarchy. Validation and tests remain simple.

## 5. Recommended directory structure

```text
MVP agent/
├── AGENTS.md
├── README.md                         # Phase 8
├── pyproject.toml                    # Phase 2
├── .env.example                      # Phase 2; names only, no secret
├── src/minimal_agent/
│   ├── __init__.py
│   ├── config.py
│   ├── models.py                     # shared domain models
│   ├── errors.py
│   ├── runtime.py                    # bounded loop only
│   ├── context.py
│   ├── service.py                    # thin lifecycle facade
│   ├── llm/
│   │   ├── base.py
│   │   └── deepseek_client.py
│   ├── tools/
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── calculator.py
│   │   ├── search.py
│   │   └── todo.py
│   ├── sessions/
│   │   ├── base.py
│   │   └── sqlite.py
│   ├── tracing.py
│   └── api.py
├── tests/
│   ├── fakes.py
│   ├── unit/
│   ├── runtime/
│   ├── persistence/
│   └── integration/
└── docs/
    ├── architecture.md
    ├── ai_dev_log.md
    └── tasks/
        ├── phase_01_architecture.md
        └── phase_02_skeleton.md ... phase_09_review.md
```

Do not create empty abstraction modules pre-emptively. The tree is the target shape; each phase creates only what it uses.

## 6. Five highest-risk pitfalls

1. **Breaking provider tool-call continuity.** A tool result must reference the exact provider call ID and the next request must contain a valid assistant-call/result sequence. The adapter needs round-trip tests.
2. **Confusing history storage with context selection.** Keeping all history in SQLite does not mean sending all history to the model. Summary boundaries and complete-turn selection must be explicit.
3. **Leaking or depending on chain-of-thought.** The runtime only needs observable response type and tool calls. Hidden reasoning is not application state and must not enter normal traces.
4. **Losing session-scoped mutations.** Todo execution mutates only the loaded session's `tool_state`, and the state must be saved after the tool-result batch before another LLM call.
5. **Brittle tests that imitate an LLM poorly.** Scripted responses must model call IDs, invalid argument strings, repeated tool rounds, and response ordering. Integration tests verify provider compatibility without exact-text assertions.

## 7. Things we will not build

- Agent framework integration or a generic workflow engine.
- Multi-agent coordination, planning agents, reflection loops, or autonomous task queues.
- Redis, Kafka, distributed locks, microservices, containers as an architectural requirement, or cloud deployment automation.
- Vector search, embeddings, RAG, long-term semantic memory, or user profiling.
- Browser/web search infrastructure; the v1 search backend is deterministic and mocked.
- A universal expression language or arbitrary Python execution in calculator.
- A generic plugin marketplace, dynamic code loading, or remote tools.
- Event sourcing, unbounded full-history replay, elaborate checkpointing, or normalized message tables before a concrete need.
- Streaming, parallel tool execution, retries with complex backoff, or same-session concurrent-write guarantees in v1.
- Storage or display of private model chain-of-thought.

## 8. Implementation order

1. **Phase 1 — architecture:** freeze responsibilities, state ownership, flow, and acceptance criteria.
2. **Phase 2 — skeleton:** package, configuration, domain models/interfaces, and test harness importability.
3. **Phase 3 — tools:** registry and three tools, fully unit-tested independent of an LLM.
4. **Phase 4 — runtime loop:** fake-LLM-driven bounded loop and tool-result feedback.
5. **Phase 5 — sessions/context:** SQLite durability, isolation/recovery, summary and recent-turn selection.
6. **Phase 6 — trace/errors:** structured trace coverage and consistent boundary behavior.
7. **Phase 7 — tests:** close the required scenario matrix and add gated real-LLM integration cases.
8. **Phase 8 — README/demo:** thin API, runnable demo, setup and trace examples.
9. **Phase 9 — review:** simplify, verify constraints, and produce the final submission checklist.

## Architecture freeze v1

The frozen implementation is a single async Python application whose concrete `AgentRuntime` orchestrates normalized LLM decisions, a schema-driven `ToolRegistry`, SQLite-backed composite-key sessions, full-request-budgeted summary-plus-recent-turn context, and structured tracing. The only real provider implementation is DeepSeek V4 Flash through OpenAI-compatible Chat Completions; active tool runs use full normalized message replay with thinking disabled. Fake implementations exist only at explicit test seams. State is saved as one JSON session document, tools execute sequentially, one loop step equals one LLM decision, and maximum-step exhaustion returns a controlled terminal result. No additional architectural layer should be introduced without evidence that one of the required acceptance tests cannot be met cleanly.
