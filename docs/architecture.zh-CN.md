# Phase 1——需求与冻结架构（中文版）

状态：**Frozen v1，Provider Revision A**

日期：2026-07-24
系统图与流程图：[`diagrams.zh-CN.md`](diagrams.zh-CN.md)

本文是后续阶段的架构基线。变更必须有具体实现或测试证据，采用最小替换方案，并记录到 `ai_dev_log.md`。

## 1. 需求拆解

### Runtime

Runtime 为已有 `(user_id, session_id)` 接收用户消息，加载 Session、构造有界 Context，让真实 LLM 直接回答或根据 Schema 发起 Tool Call。它执行所有调用并把结构化结果返回 LLM，直至 Final Answer 或最大决策轮数。终止由 Runtime 负责。

### Tool System

V1 只有 `calculator`、确定性 Mock `search` 和 Session-scoped `todo`。每个 Tool 提供名称、描述、JSON 参数 Schema 和异步 `execute()`；LLM 根据 Schema 选择，禁止关键词路由。Invalid JSON、Unknown Tool、Validation Failure、Execution Exception 均成为关联原 Call ID 的失败 `ToolResult`，由下一轮 LLM 修复或解释。

### Session 与持久状态

身份固定为 `(user_id, session_id)`。`SessionState` 保存可见 History（含 Tool Call/Result）、`tool_state`、Rolling Summary 与覆盖边界、UTC Timestamp 和 Version。SQLite 每 Session 保存一个 JSON Payload；一次 Save 是一个 Transaction。Create 与 Run 分离，Missing Session 抛出 `SessionNotFoundError`。

### Context Management

完整 History 持久保留，LLM Request 必须有界。预算包含 System Prompt、Tool Schema、Conversation/Summary 和 Response Token Reserve。只总结旧的完整 Turn，不拆 Tool Pair；Summary 不带 Tool、不允许 Tool Calling、只返回文本。失败时保留旧 Summary，选择预算内最大完整后缀并始终保留当前 Turn。

### Trace、错误与测试

`TraceEvent` 用 `session_id`、`turn_id`、`loop_step` 关联 LLM、Tool、Compression、Error 和 Finish。Tool Error 是 Result；Provider/Store/Protocol/Context Failure 是 Domain Exception。不保存 Private Chain-of-thought。默认 pytest 确定、离线、无 Key；真实测试只验证 Provider Protocol，不依赖自主 Tool Routing 的稳定性。

## 2. 最小系统架构

```text
CLI / future API
       |
 AgentService -------- SessionStore(SQLite)
       |
 AgentRuntime -------- TraceSink
   |       |
 LLMClient ContextManager
       |
 ToolRegistry -> calculator / search / todo
```

可替换边界只有 `LLMClient`、`SessionStore`、`TraceSink` 和 Search Backend，不建立 DI/Plugin/Provider Framework。V1 唯一 Adapter 通过 OpenAI SDK 调用 DeepSeek：`https://api.deepseek.com`、`deepseek-v4-flash`、Thinking Disabled、SDK Retry Disabled。

## 3. Runtime 数据流

1. Service 按复合身份加载 Session。
2. Runtime 追加 User Message 并 Checkpoint。
3. ContextManager 在完整预算内选择 Summary 和最近完整 Turn，必要时压缩。
4. Runtime 发送 System Prompt、Messages、Tool Definitions 和 Output Limit。
5. Final：追加可见 Answer、Checkpoint，返回 `completed`。
6. Tool Calls：按模型顺序保存并执行全部调用；Registry 标准化 Result。
7. 追加完整 Tool Result Batch 并 Checkpoint。
8. 同一 Run 的后续轮重放初始有界 Context 和此后全部 Tool Messages。
9. 一次 LLM Request 算一个 Step；达到上限后保存最后结果，不再调用 LLM，追加受控 Terminal Message 并返回 `max_steps`。

新 User Turn 根据本地 History 重建 Context，不依赖 Provider Continuation ID，也不持久化 Reasoning Item。

## 4. 核心数据结构

- `ConversationMessage`：Role、Visible Content、Tool Call 及其 ID/Name。
- `ToolCall`：`call_id`、`name`、原始 `arguments_json`。
- `LLMRequest`：Prompt、Provider-neutral Messages、Tool Definition、Output Limit。
- `LLMResult`：严格区分 `FINAL` 与 `TOOL_CALLS`。
- `ToolDefinition/ToolContext/ToolResult`：Schema、当前 Session State 引用、JSON-safe Result/Error。
- `SessionState`：复合身份、History、Tool State、Summary、Boundary、Timestamp、Version。
- `AgentRunResult`：Status、Final Answer、Turn ID、Loop Steps。
- `TraceEvent`：Event Type、关联 ID、Step、Metadata、Latency、安全 Error。

## 5. 目录职责

`runtime.py` 负责循环，`service.py` 负责 Session 生命周期和 Checkpoint，`context.py` 负责预算与压缩，`tracing.py` 负责可替换 Sink，`llm/` 只含 DeepSeek Adapter，`sessions/` 只含 SQLite Store，`tools/` 包含 Registry 与三个 Tool；测试按同一边界组织。

## 6. 五个最高风险点

1. Tool Call ID 与 Result ID 不一致。
2. 把持久 History 等同于 LLM Context，导致无限增长或破坏恢复。
3. 压缩切断 Tool Pair，或遗漏 Prompt/Schema/Reserve 成本。
4. Todo 使用 Global State 或只按 `session_id` 查询。
5. Tool Failure 变成 Runtime Exception，或 Max Steps 出现 Off-by-one。

## 7. 坚决不做

不实现 Agent Framework、关键词 Router、多 Agent、微服务、Queue、Redis、Vector DB、RAG、复杂长期 Memory、并行 Tool、Streaming、Retry Framework、Provider Registry、Event Sourcing 或分布式事务。

## 8. 实现顺序

架构 → 骨架 → Tool → Runtime/Provider → Session/Context/Checkpoint → Trace/Error → Offline/Real Tests → Wiring/CLI/README/Demo → Final Review。

## Architecture Freeze v1

冻结边界为：薄 Adapter → `AgentService` → `AgentRuntime`；Runtime 使用 `ContextManager`、单一 `LLMClient`、`ToolRegistry`、`TraceSink`；Service 通过 `SessionStore` 持久化复合身份状态。无充分测试证据不得改变。
