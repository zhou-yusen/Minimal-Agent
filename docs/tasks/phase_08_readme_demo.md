# Phase 8 Task — README, API, and Demo

## Problem solved

Make the runtime easy for a reviewer to install, run, inspect, and explain without adding another architecture.

## Planned implementation

- Add a thin FastAPI adapter for session create/get/delete and message execution.
- Add a small CLI or script that explicitly creates a session and demonstrates direct answer, calculator/search/todo use, follow-up, and session reopening.
- Write README sections for scope, architecture, data flow, setup, environment variables, API examples, tests, integration opt-in, traces, and limitations.
- Include one short sanitized trace example showing two tool rounds.
- Document SQLite location, mock search semantics, context defaults, and the max-step outcome.
- Add an interview explanation: why each boundary exists and why excluded features are unnecessary.

## Key trade-offs

- API and CLI are adapters over the same service/runtime.
- Demo data is deterministic except for model wording.
- No UI, authentication system, Docker orchestration, or deployment platform is required.

## Verification

- Follow README setup from a clean shell.
- Exercise endpoints with an HTTP client and restart the process to prove recovery.
- Run demo and compare emitted trace correlations with the documented example.

## Acceptance criteria

- A reviewer can reach a real-LLM answer from documented commands.
- Session continuation and todo persistence survive process restart.
- README commands are copyable and match actual configuration/paths.
- Limitations and non-goals are explicit rather than hidden.
