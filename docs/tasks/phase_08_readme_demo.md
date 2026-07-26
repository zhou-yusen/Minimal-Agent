# Phase 8 Task — Application Entry Points and Demo

## Phase 8A problem solved

Make the completed runtime directly usable through a small interactive CLI while
preserving the existing application boundaries. FastAPI remains deferred until a
separate reviewed phase.

## Application wiring

`minimal_agent.app.build_service()` is the single composition root. It wires the
validated Settings, DeepSeek adapter, three production tools, SQLite store,
ContextManager, JSON trace sink, Runtime, and AgentService. It contains no agent
or session lifecycle logic.

The CLI delegates all messages to AgentService. It does not reproduce the agent
loop, tool dispatch, persistence, or context selection.

## CLI usage

Put the key in the project-root `.env` (or export it in the current shell):

```dotenv
DEEPSEEK_API_KEY=...
```

```powershell
.\.venv\Scripts\python.exe -m minimal_agent.cli --user demo --session demo1
```

Omit `--session` to generate an eight-character UUID session ID. Add `--debug`
to enable INFO-level structured trace records. Normal mode uses WARNING so traces
do not obscure the conversation. Use `/exit` or `/quit` to stop.

The default database is `data/minimal_agent.db`. A missing composite session is
created; an existing `(user_id, session_id)` is resumed without replacement.

## Manual real-LLM demo

Start the CLI with the command above and enter, in order:

```text
你好
请使用计算器计算 123456 * 789
帮我添加 todo：准备 Agent 面试
查看我的 todo
/exit
```

Restart with the same `--user demo --session demo1`, then enter:

```text
查看我的 todo
```

The final request must still see the saved Todo item, demonstrating that both
conversation and tool state survive process restart through SQLite.

## Verification

- Offline CLI tests use a scripted fake LLM and temporary SQLite database.
- Tests cover create, resume, send, exit, safe domain-error display, generated
  session IDs, missing API-key behavior, and production tool wiring.
- The five marked DeepSeek tests remain skipped by default and passed in the
  user's explicit real-provider run.

## Phase 8A acceptance criteria

- `python -m minimal_agent.cli` starts a real interactive client when
  `DEEPSEEK_API_KEY` is available from `.env` or the process environment.
- Calculator, deterministic mock Search, and Todo are the only CLI tools.
- Reusing the composite identity resumes SQLite history and Todo state.
- Default output is readable; `--debug` enables structured INFO traces.
- Expected domain errors are displayed without tracebacks or raw provider data.
- No FastAPI, HTTP endpoint, UI, streaming, or new framework is introduced.
