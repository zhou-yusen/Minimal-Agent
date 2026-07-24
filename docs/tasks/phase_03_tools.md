# Phase 3 Task — Tool System

## Problem solved

Expose safe, schema-described capabilities that the LLM can choose and the runtime can dispatch uniformly.

## Planned implementation

- Implement a small `BaseTool` contract with `name`, `description`, a Pydantic argument model/schema, and async `execute()`.
- Implement `ToolRegistry.register()`, `definitions()`, and `execute(call, context)` with duplicate-name protection.
- Build `calculator` with an AST whitelist for numeric constants, parentheses, unary operators, and basic arithmetic; reject names, attributes, calls, and unsafe complexity.
- Build `search` over an injected deterministic in-memory corpus/backend with `query` and bounded `top_k`.
- Build one `todo` tool with an `action` enum (`add`, `list`, `complete`, `delete`) and action-aware argument validation. Store generated item IDs and status in `ToolContext.tool_state`.
- Normalize invalid JSON, unknown tool, Pydantic validation failure, and execution exception as failed `ToolResult` values.

## Key trade-offs

- One todo tool with actions keeps the required public tool set at three.
- Sequential state mutation makes todo behavior deterministic.
- The registry owns failure normalization so the runtime does not contain tool-specific branches.

## Verification

- Unit-test each happy path and each registry error category.
- Assert generated JSON schemas are accepted by the provider adapter format.
- Assert malicious calculator expressions cannot execute Python code.

## Acceptance criteria

- Registry definitions contain name, description, and parameter schema for all three tools.
- No source-code keyword router exists.
- Calculator is safe, search is deterministic, and todo state is isolated by the supplied context.
- Every tool failure returns `ok=false` with a stable error code and no stack trace.
