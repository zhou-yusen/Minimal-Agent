# Phase 2 Task — Project Skeleton

## Problem solved

Create an importable, testable project foundation without implementing tool or loop behavior.

## Planned implementation

- Add `pyproject.toml` for Python 3.11+, the official LLM SDK, Pydantic, FastAPI/httpx as needed, and pytest tooling.
- Add `src/minimal_agent/` package and the minimal module files actually used in this phase.
- Implement configuration loading from environment with explicit defaults for model, timeout, loop limit, database path, and context limits.
- Define domain models and protocols for `LLMClient`, `SessionStore`, tool execution, and tracing.
- Add `.env.example` containing variable names and safe examples only.
- Add pytest configuration and a smoke test proving clean imports/config validation.

## Key trade-offs

- Use a `src/` layout to prevent accidental imports from the working directory.
- Keep configuration in one Pydantic settings-style model; do not build a DI framework.
- Interfaces exist only at the four frozen seams.

## Verification

- Install/sync dependencies in a clean environment.
- Run the import/config smoke tests.
- Inspect the dependency tree for accidental Agent Frameworks.

## Acceptance criteria

- `python -c "import minimal_agent"` succeeds in the project environment.
- Missing required real-LLM credentials fail only when constructing/calling the real adapter, not when importing or running offline tests.
- Invalid numeric limits produce clear validation errors.
- No tool behavior, runtime loop, SQLite schema, or API endpoint is implemented prematurely.
