# DeepSeek Integration Test Report

Status: **PASSED**

The user executed the opt-in real DeepSeek suite locally and reported:

```text
5 passed in 12.52s
```

## Environment

- Date recorded: 2026-07-24
- Model: `deepseek-v4-flash`
- Base URL: `https://api.deepseek.com`
- Thinking: disabled
- SDK: `openai 2.47.0`
- Integration tests: 5
- Result: PASS

## Scenarios

| Scenario | Result |
|---|---|
| Direct Final | PASS |
| Forced Tool Call | PASS |
| Same `tool_call_id` Tool Result Round Trip | PASS |
| AgentRuntime Calculator Smoke | PASS |
| Cross-Turn Local History Replay | PASS |

The deterministic protocol gates and normal Runtime routing smoke all passed.
Local normalized history was accepted on a new user turn without provider
conversation state or persisted reasoning content.

## Offline verification

Before the real run, the offline suite reported `168 passed, 5 skipped` with both
integration environment variables removed from the pytest process. The skipped
tests made no network requests.

No API key, authorization header, raw provider body, or reasoning content is
recorded in this report.
