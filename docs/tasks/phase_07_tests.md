# Phase 7 Task — Required and Integration Tests

## Problem solved

Turn the assignment requirements into a reproducible behavior contract and verify the real provider boundary separately from deterministic tests.

## Required offline matrix

| # | Scenario | Primary assertion |
|---|---|---|
| 1 | Ordinary chat | final answer with zero tool events |
| 2 | Calculator | schema-selected call and correct result returned to LLM |
| 3 | Search | deterministic result returned to LLM |
| 4 | Todo | mutation persists in session tool state |
| 5 | Two tool calls | two LLM tool rounds then a final answer |
| 6 | Bad tool arguments | `validation_error` or `invalid_json` result reaches LLM |
| 7 | Tool exception | `execution_error` result reaches LLM; run continues |
| 8 | Maximum rounds | exact configured call count and `max_steps` result |
| 9 | Session isolation | no history/todo crossover |
| 10 | Session recovery | new store/runtime instance resumes state |
| 11 | Ordinary follow-up | prior user/assistant turn is in LLM context |
| 12 | Tool-result follow-up | prior call and result pair remain in context |
| 13 | Context compression | summary plus recent complete turns, raw history retained |
| 14 | LLM API mock | timeout/protocol/provider normalization without network |

Additional required checks cover missing tool, missing session, compression failure, calculator safety, trace redaction, and multiple calls in one response.

## Real LLM integration set (3–5 tests)

1. A direct text request verifies that the real SDK response is normalized into visible final text.
2. A provider-forced call to a tiny deterministic test tool verifies that the Responses API returns a function call with a usable `call_id`.
3. Returning that tool's output with the exact same `call_id` verifies the real SDK accepts the result and produces the next response.
4. An invalid tool-result correlation test verifies a clear provider/adapter failure rather than silent misassociation, when safe and deterministic to run.
5. A multi-round protocol smoke test may be included, but it must assert protocol continuity rather than require autonomous selection of `calculator`, `search`, or `todo`.

One of the selected 3-5 tests must cover the deferred cross-user-turn question:
start a new request without `previous_response_id`, rebuild context from local
Session history containing a prior function-call/output pair, and determine
whether the provider also requires replayed reasoning items. This validation test
is not prior authorization to persist hidden reasoning or add provider-state
abstractions.

## Planned implementation

- Centralize scripted LLM fixtures and trace capture in `tests/fakes.py`.
- Mark network tests `integration` and skip unless both a credentials variable and explicit opt-in flag are present.
- Use a low-cost configurable model and conservative timeouts.
- Assert tool names/results, session state, statuses, and trace types rather than exact prose.
- Keep strict autonomous tool-selection and full Tool Loop assertions in the offline scripted-LLM suite. Real integration tests may force a specific test tool through the one supported provider API.

## Verification

- Run default pytest with network credentials absent.
- Run coverage focused on runtime branches, registry failures, context fallback, and persistence.
- Run integration tests explicitly when credentials are available and report model/date because model behavior can drift.

## Acceptance criteria

- All 14 numbered scenarios have clearly named passing offline tests.
- Default tests never access the network.
- Three to five real-LLM tests are discoverable, safely skipped by default, and documented.
- No flaky exact-text assertion or hidden ordering assumption remains.
- Integration success does not depend on the model autonomously deciding to use `calculator`.
