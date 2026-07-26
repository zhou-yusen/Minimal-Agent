# Phase 6 任务——Trace 与异常处理

## 解决的问题

使每次 Agent Run 都可诊断，并让边界失败具有可预测行为。

## 计划实现

- 实现一个经过校验的 `TraceEvent` 模型和 `TraceSink.emit()` 协议。
- 添加 JSON Logger Sink 和测试用 In-memory Sink。
- 发出 Run、LLM、Tool、Compression、Error 和 Finish Event，并通过 `session_id`、`turn_id`、`loop_step` 关联。
- 使用单调时钟测量 LLM/Tool/Run 延迟。
- 脱敏 LLM Request 和 Error Field；Tool Result 中绝不包含 API Key、Header、环境变量 Dump 或 Stack Trace。
- 定义并映射 `SessionNotFoundError`、`LLMTimeoutError`、`LLMProtocolError`、`SessionStoreError` 和 Context Error。
- Phase 7 真实 API 证据证明需要改变“一次尝试”契约前保持 Provider Retry 关闭，不引入 Retry Framework。

## Phase 6A 可观察性（已完成）

- `AgentRuntime` 可选发出关联的 Run、Context Compression、LLM、Tool、Error、Finish `TraceEvent`。
- LLM Request Event 只包含结构元数据：数量、Role、Tool Name 和 Output Limit；不含 Prompt/Message Text。
- `ContextManager.build()` 返回 Provider-neutral `ContextBuildResult`，使 Runtime 能关联成功或降级 Compression，而不把 Trace 依赖注入 Context Selection。
- Trace Emit 采用 Best-effort；Sink 异常不重试并被忽略，不得改变 Checkpoint、LLM Call、Tool 或最终 Run Result。
- `InMemoryTraceSink` 支持确定性测试；`JsonLoggingTraceSink` 每条 stdlib Logging Record 写入一个紧凑 JSON Event。

## Phase 6B Provider Failure 与中断 Turn（已完成）

- DeepSeek 使用的 OpenAI Python SDK Client 设置 `max_retries=0`；一次 Adapter Call 就是一次 Provider Attempt，不含应用层 Retry。
- SDK Timeout、Connection、Rate Limit、Status 和一般 API Failure 映射为稳定安全的 Domain Error；只有 Status Code 和 Request ID 可越过 Adapter Boundary，Provider Body/Header/Raw Exception Text 不可越界。
- 新 Run 在接收新 User Message 前，使用确定性 Runtime Assistant Marker 封存尾部未完成 Turn；先 Checkpoint Marker，绝不重放旧 Tool Call。
- Recovery 发出一条仅 Metadata Event；Error Trace 使用稳定 Domain Code，而不是 Python Class Name 作为业务分类。
- 严格 Trace Argument Parsing 与 `ToolRegistry` 一致地拒绝 NaN 和 Infinity。

自动 Retry 继续关闭。未来外部副作用工具需要明确的 Idempotency 或 Transaction Strategy：V1 不回滚“本地 Checkpoint 随后失败”的已执行工具，也不增加 Outbox 或分布式事务框架。

## 关键取舍

- 单一 Optional-field Event Model 比 Class Hierarchy 更容易检查。
- Tool Error 保持为 Result Data；Infrastructure Failure 保持为 Exception。
- 开发环境 Full Payload Trace 可配置，因为 Prompt/Result 可能包含用户数据。

## 验证

- 对成功、Tool Failure、Timeout、Compression Failure 和 Max Steps 断言 Event 顺序和必要关联字段。
- 断言序列化 Trace 中没有 Secret 和 Private Reasoning Fixture Text。
- HTTP Status Mapping 推迟到实际引入 API Adapter 时处理。

## 验收条件

- 每类相关 Event 都可观察到要求的 Trace Field。
- 每个列出的异常场景都有确定的 Runtime 或 API 结果。
- Error 不会留下结构损坏的 Session Payload。
- 普通 Trace 不包含 Private Chain-of-thought 和凭据。
