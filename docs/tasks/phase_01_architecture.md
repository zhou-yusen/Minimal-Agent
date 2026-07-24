# Phase 1 Task — Requirements and Architecture

## Problem solved

Turn a broad take-home prompt into a small set of explicit responsibilities and stable contracts before implementation begins.

## Implementation/design work

- Inventory functional requirements, failure cases, state, context, trace, and test obligations.
- Define one owner for the agent loop (`AgentRuntime`).
- Define only the replaceable boundaries needed for deterministic tests or required persistence.
- Freeze session identity, loop-step semantics, save points, context compression behavior, and reasoning-data policy.
- Map Phases 2–9 to focused deliverables.

## Key trade-offs

- SQLite JSON session documents over normalized/event-sourced storage.
- Sequential tools over parallel execution.
- Full durable visible history but bounded inference context.
- Provider-neutral domain objects with one official SDK adapter.
- Thin HTTP/CLI adapters after the core runtime works.

## Acceptance criteria

- `AGENTS.md` states constraints and working rules.
- `docs/architecture.md` contains all eight requested sections and a clearly labeled frozen version.
- Every later phase has a scoped task document with implementation details, verification, and acceptance criteria.
- The design covers every required exception and test scenario.
- No runtime source code or dependency file is created in this phase.
