# Phase 8 任务——应用入口与 Demo

## Phase 8A 解决的问题

通过小型交互式 CLI 让已完成的 Runtime 可直接使用，同时保留现有应用边界。FastAPI 推迟到独立审核阶段。

## 应用组装

`minimal_agent.app.build_service()` 是唯一 Composition Root，负责组装经过校验的 Settings、DeepSeek Adapter、三个生产 Tool、SQLite Store、ContextManager、JSON Trace Sink、Runtime 和 AgentService；不包含 Agent 或 Session Lifecycle Logic。

CLI 把所有消息委托给 AgentService，不重复实现 Agent Loop、Tool Dispatch、Persistence 或 Context Selection。

## CLI 用法

在项目根目录 `.env` 中配置 Key（或导出到当前 Shell）：

```dotenv
DEEPSEEK_API_KEY=...
```

```powershell
.\.venv\Scripts\python.exe -m minimal_agent.cli --user demo --session demo1
```

省略 `--session` 时生成八字符 UUID Session ID。使用 `--debug` 启用 INFO 级结构化 Trace；普通模式使用 WARNING，避免 Trace 遮挡对话。使用 `/exit` 或 `/quit` 退出。

默认数据库为 `data/minimal_agent.db`。不存在的复合 Session 会创建；已有 `(user_id, session_id)` 会恢复且不被覆盖。

## 手动真实 LLM Demo

启动 CLI 后依次输入：

```text
你好
请使用计算器计算 123456 * 789
帮我添加 todo：准备 Agent 面试
查看我的 todo
/exit
```

使用相同 `--user demo --session demo1` 重启，再输入：

```text
查看我的 todo
```

最后一次请求必须仍能看到已保存 Todo，证明 Conversation 与 Tool State 都通过 SQLite 跨进程重启保存。

## 验证

- 离线 CLI 测试使用 Scripted Fake LLM 和临时 SQLite Database。
- 覆盖 Create、Resume、Send、Exit、安全 Domain Error 展示、生成 Session ID、缺 API Key 和生产 Tool Wiring。
- 五个 DeepSeek Marker Test 默认 Skip，并已在用户显式真实 Provider Run 中通过。

## Phase 8A 验收条件

- 从 `.env` 或 Process Environment 获得 `DEEPSEEK_API_KEY` 时，`python -m minimal_agent.cli` 启动真实交互 Client。
- Calculator、确定性 Mock Search、Todo 是 CLI 唯一工具。
- 复用复合身份会恢复 SQLite History 和 Todo State。
- 默认输出可读；`--debug` 启用结构化 INFO Trace。
- 预期 Domain Error 不展示 Traceback 或 Raw Provider Data。
- 不引入 FastAPI、HTTP Endpoint、UI、Streaming 或新 Framework。
