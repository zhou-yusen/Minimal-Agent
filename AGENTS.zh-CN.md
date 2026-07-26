# Minimal Agent Runtime——项目指令

## 目标

为技术笔试构建一个小型、可解释、可测试的 Agent Runtime，展示完整循环：

`User -> LLM -> Tool Call -> Tool Result -> LLM -> Final Answer`

正常运行必须使用真实 LLM API；测试可注入 Fake LLM。

## 不可违反的约束

- Runtime Loop 不使用 LangGraph、OpenHands、OpenClaw 或其他 Agent Framework。
- 不用硬编码用户关键词选择工具；LLM 根据 JSON Tool Schema 自主选择。
- 始终限制 LLM 决策轮数。
- Tool Failure 必须成为结构化 Tool Result 并返回 LLM，不能使 Runtime 崩溃。
- Session 身份是复合键 `(user_id, session_id)`，禁止只按 `session_id` 加载。
- Conversation、Todo、Summary、Timestamp 通过 `SessionStore` 持久化，禁止用 Python Global 保存持久状态。
- 不持久化 Private Chain-of-thought；只保存可见 Assistant Content、Tool Call/Result 和可选的模型会话 Summary。
- 保持单体 Python 应用，不增加微服务、队列、Redis、向量数据库、RAG、多 Agent 或后台 Worker。
- 只有依赖消除的复杂度大于自身引入的复杂度时才增加依赖。

## 冻结架构

规范架构见 `docs/architecture.md`。除非具体测试或 Provider 限制证明不足，否则保留以下边界：

- `AgentRuntime`：负责有界 LLM/Tool Loop。
- `LLMClient`：把 DeepSeek Chat Completions 映射到 Provider-neutral Runtime Object。
- `ToolRegistry`：暴露 Schema 并分发已校验调用。
- `SessionStore`：加载并原子保存 `SessionState`；首版为 SQLite。
- `ContextManager`：选择 System Prompt、Summary 和最近完整 Turn，并触发 Compression。
- `TraceSink`：接收结构化 `TraceEvent`。
- 薄 API/CLI Adapter 调用同一 Runtime，不包含 Agent Logic。

V1 唯一 Provider Adapter 通过 OpenAI Python SDK 调用 `https://api.deepseek.com`，模型为 `deepseek-v4-flash`，关闭 Thinking 和 SDK Retry；Provider-specific Mapping 只存在于该 Adapter。

## 工作方式

按 `docs/tasks/` 顺序完成阶段。每阶段先说明问题和取舍，只修改阶段范围内代码，执行规定验证，报告真实结果与限制；只有具备技术价值的问题/决策才写入 `docs/ai_dev_log.md`。早期设计有误时先给证据，再做最小一致修改；只有冻结架构确实变化时更新架构文档并记录原因。

## 实现规则

- Python 3.11+，公开接口使用 Type Hint。
- 边界校验优先使用小型 Pydantic Model，编排使用普通 Python Control Flow。
- LLM/Tool 异步边界端到端保持 Async；V1 可在 Store 内隔离同步 SQLite，不仅为 Async 语法引入第二个数据库库。
- Timestamp 使用带时区 UTC ISO-8601。
- I/O Boundary 使用显式 Domain Error；普通 Tool Failure 不使用 Exception 表达。
- Calculator 禁止 Unrestricted `eval`，使用小型 AST 白名单。
- Mock Search 必须确定且 Backend 可注入。
- Todo 属于 `SessionState.tool_state`，随复合 Session 身份隔离。
- 选择近期 Context 时保留完整 Tool Call/Result Pair。
- 一个 `loop_step` 是一轮 LLM Decision；同 Response 多 Tool Call 共享 Step。
- Response 有 Tool Call 时执行并继续；无 Tool Call 且有可见文本时才是 Final。
- 活动 Run 每轮重放有界初始 Context 加本轮产生的全部 Assistant Tool Call/Result，不使用 Provider Continuation ID，也不只发送最新 Result。
- Max-step 耗尽时返回并持久化受控 `status=max_steps`，不偷偷再调用 LLM。

## 测试规则

- 默认 pytest 必须确定、离线、不需要 API Key。
- Unit/Runtime Test 注入 Scripted Fake LLM 和 In-memory Trace Sink。
- 真实 LLM Test 标记为 `integration`，只有显式启用且凭据存在才运行。
- 断言行为与 Trace 证据，不断言精确模型措辞。
- 持久化测试使用新的临时 SQLite Database。
- `docs/tasks/phase_07_tests.md` 每个要求场景都对应命名测试。

## 安全与可观察性

- 不记录 API Key、Authorization Header 或环境变量。
- 开发 Trace 可含脱敏 Prompt、Tool Argument/Result，需说明仍可能含用户数据。
- 不暴露或合成隐藏推理；Trace 可记录 `reasoning_present=true` 和 Provider 提供的安全 Summary，但不记录 Private Chain-of-thought。
- 返回 LLM 的 Tool Error 应有用，但不能含 Stack Trace。

## 完成定义

全部阶段验收条件通过、默认离线 Suite 全绿、Opt-in Integration 命令有文档、README Demo 展示 Session Continuation、Tool Use、Failure Recovery、Trace 和 Max-step Guard 后，项目才算完成。
