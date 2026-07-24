# Minimal Agent Runtime — Project Instructions

## Goal

Build a small, explainable, testable Agent Runtime for a technical take-home. The project demonstrates the complete loop:

`User -> LLM -> Tool Call -> Tool Result -> LLM -> Final Answer`

The implementation must use a real LLM API in normal operation. Tests may inject a fake LLM.

## Non-negotiable constraints

- Do not use LangGraph, OpenHands, OpenClaw, or any other Agent Framework for the runtime loop.
- Do not select tools with hard-coded user-keyword rules. The LLM chooses from JSON tool schemas.
- Always cap the number of LLM decision rounds.
- A tool failure must become a structured tool result and be returned to the LLM; it must not crash the runtime.
- Session identity is the composite key `(user_id, session_id)`. Never load a session by `session_id` alone.
- Conversation state, todo state, summary, and timestamps must be persisted through `SessionStore`; do not use Python globals as durable state.
- Do not persist private chain-of-thought. Persist visible assistant content, tool calls, tool results, and an optional model-produced conversation summary only.
- Keep the project a single Python application. Do not add microservices, queues, Redis, vector databases, RAG, multi-agent behavior, or background workers.
- Do not add a dependency unless it removes more complexity than it introduces.

## Frozen architecture

The canonical architecture is `docs/architecture.md`. Unless a concrete test or provider limitation proves it inadequate, preserve these boundaries:

- `AgentRuntime`: owns the bounded LLM/tool loop.
- `LLMClient`: adapts the official provider SDK to provider-neutral runtime objects.
- `ToolRegistry`: exposes schemas and dispatches validated calls.
- `SessionStore`: loads and atomically saves `SessionState`; SQLite is the first implementation.
- `ContextManager`: selects the system prompt, summary, and recent complete turns; it triggers compression.
- `TraceSink`: receives structured `TraceEvent` records.
- Thin API/CLI adapters call the same runtime and contain no agent logic.

The first provider adapter will use the official OpenAI Python SDK. Provider-specific response parsing stays inside that adapter.

## Working method

Work in the order defined under `docs/tasks/`. Complete one phase before expanding the next one.

For every phase:

1. Restate the problem the phase solves.
2. Confirm the design and important trade-offs.
3. Make only phase-scoped changes.
4. Run the phase's specified verification.
5. Report actual results and unresolved limitations.
6. Add an entry to `docs/ai_dev_log.md` only for a technically meaningful problem or decision.

If an earlier design is wrong, explain the evidence first and make the smallest coherent change. Update `docs/architecture.md` only when the frozen architecture truly changes, and record the reason in `docs/ai_dev_log.md`.

## Implementation rules

- Target Python 3.11+ and use type hints on public interfaces.
- Prefer small Pydantic models for boundary validation and plain Python control flow for orchestration.
- Keep async boundaries end-to-end for LLM and tool execution. Synchronous SQLite operations may be isolated behind the store in v1; do not introduce a second database library solely for async syntax.
- Store timestamps as timezone-aware UTC ISO-8601 values.
- Use explicit domain errors at I/O boundaries. Do not use exceptions for ordinary tool failure results.
- Never use unrestricted `eval` for calculator expressions. Use a small AST whitelist.
- Make mock search deterministic and inject its backend.
- Todo data belongs to `SessionState.tool_state`, so it follows the composite session identity.
- Preserve complete tool-call/tool-result pairs when choosing recent context.
- Treat one `loop_step` as one LLM decision round. Multiple tool calls returned in one response share that step.
- If a response contains one or more tool calls, execute them and continue the loop. It is final only when it contains no tool calls and has visible answer text.
- On maximum-step exhaustion, return and persist a controlled terminal result with status `max_steps`; do not silently make another LLM call.

## Testing rules

- Default `pytest` must be deterministic, offline, and must not require an API key.
- Unit/runtime tests inject scripted fake LLM responses and an in-memory trace sink.
- Real-LLM tests are marked `integration` and run only when explicitly enabled and credentials exist.
- Assert behavior and trace evidence, not exact model prose.
- Use a fresh temporary SQLite database per test that touches persistence.
- Every required scenario in `docs/tasks/phase_07_tests.md` must map to a named test.

## Security and observability

- Never log API keys, authorization headers, or environment variables.
- Development traces may contain sanitized prompts, tool arguments, and tool results. Document that these can contain user data.
- Do not expose or synthesize hidden reasoning. A trace may record `reasoning_present=true` and provider-supplied safe summaries, but not private chain-of-thought.
- Tool error messages returned to the LLM must be useful but must not include stack traces.

## Definition of done

The project is done only when all phase acceptance criteria pass, the default test suite is green offline, the opt-in integration suite has a documented command, and the README demo shows session continuation, tool use, failure recovery, traces, and the maximum-step guard.
