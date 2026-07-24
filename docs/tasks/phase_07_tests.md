# Phase 7 Task — Required and Integration Tests

## Problem solved

Turn the assignment requirements into a reproducible behavior contract and
verify the real DeepSeek boundary separately from deterministic tests.

## Required offline matrix

| # | Scenario | Primary assertion |
|---|---|---|
| 1 | Ordinary chat | final answer with zero tool events |
| 2 | Calculator | schema-selected call and correct result returned to LLM |
| 3 | Search | deterministic result returned to LLM |
| 4 | Todo | mutation persists in session tool state |
| 5 | Two tool calls | two LLM tool rounds then a final answer |
| 6 | Bad tool arguments | structured failure reaches LLM |
| 7 | Tool exception | `execution_error` reaches LLM; run continues |
| 8 | Maximum rounds | exact configured call count and `max_steps` result |
| 9 | Session isolation | no history/todo crossover |
| 10 | Session recovery | new store/runtime instance resumes state |
| 11 | Ordinary follow-up | prior user/assistant turn is in LLM context |
| 12 | Tool-result follow-up | prior call/result pair remains in context |
| 13 | Context compression | summary plus recent complete turns; raw history retained |
| 14 | LLM API mock | timeout/protocol/provider normalization without network |

Additional offline checks cover missing tools and sessions, compression failure,
calculator safety, trace redaction, multiple calls in one response, and durable
recovery.

## Real DeepSeek integration set

All five tests live in `tests/integration/test_deepseek_real.py`, carry the
`integration` marker, access the real DeepSeek API, and may incur a small API
cost. They run only when both `RUN_LLM_INTEGRATION=1` and `DEEPSEEK_API_KEY` are
present in the process environment. The project does not load `.env` implicitly.

1. **Direct Final** — empty tools, short prompt, visible final text.
2. **Forced Tool Call** — only the test-local `integration_echo` schema is
   supplied and `tool_choice="required"` verifies a real call and usable ID.
3. **Tool Result Round Trip** — returns canonical JSON using the exact real
   `tool_call_id`, sets `tool_choice="none"`, and requires a visible final.
4. **AgentRuntime Calculator Smoke** — exercises the real client, Runtime,
   ContextManager, ToolRegistry, Calculator, normalized history, and traces with
   normal provider routing (`tool_choice=None`).
5. **Cross-Turn Local History Replay** — reconstructs the previous normalized
   user/call/result/final messages, adds a new user turn, and verifies DeepSeek
   accepts the request without provider continuation or reasoning state.

Tests 2 and 3 are deterministic provider protocol gates. Test 5 is the local
history replay protocol gate. Test 4 is a real routing smoke and is not the sole
evidence for protocol correctness because autonomous model routing can drift.
No test deliberately submits a wrong call ID.

## Execution

Offline default, which must never access the network:

```powershell
Remove-Item Env:RUN_LLM_INTEGRATION -ErrorAction SilentlyContinue
Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest
```

Explicit real integration run after the user exports the key into the current
PowerShell process:

```powershell
$env:RUN_LLM_INTEGRATION="1"
.\.venv\Scripts\python.exe -m pytest tests\integration -m integration -v
```

Provider settings remain frozen at `deepseek-v4-flash`,
`https://api.deepseek.com`, thinking disabled, and SDK `max_retries=0`.

## Acceptance criteria

- All required offline scenarios pass without credentials or network access.
- Five real tests are discoverable and safely skipped by default.
- Direct, forced-call, same-ID result replay, and cross-turn replay are verified
  against the real API before Phase 8 is declared ready.
- Integration assertions cover protocol structure, not exact model prose or JSON
  whitespace/property order.
- No key, authorization header, raw provider body, or reasoning content is
  persisted, traced, documented, or printed.
