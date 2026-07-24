# Real CLI Demo Report

Status: **PASS after one CLI portability fix**

Date: 2026-07-24

## Verified path

- Real DeepSeek greeting response
- Real Calculator tool call for `125 * 37`
- Real Todo add through the production registry
- Process exit and restart with the same `(user_id, session_id)`
- Real Todo list showing the previously persisted item from SQLite

The demo used the production CLI, AgentService, AgentRuntime, DeepSeek adapter,
ToolRegistry, and `data/minimal_agent.db`. No integration-only tool was used.

## Problem observed

On the first persistence-list restart, the model response contained a Unicode
symbol unsupported by the active Windows GBK stdout. `print()` raised
`UnicodeEncodeError`, even though the provider request, Runtime, and SQLite state
had succeeded.

The CLI now configures stdout with `errors="replace"` when the stream supports
`reconfigure()`. A focused offline test covers this behavior. Reopening the same
session after the fix listed the persisted Todo successfully.

The automated PowerShell input pipeline could not preserve Chinese characters in
the active code page, so the persisted demo text appeared with replacement
characters. This is a harness encoding limitation; composite-session persistence
and Todo state recovery were still verified. No API key, raw provider body,
authorization header, or private conversation transcript is recorded here.
