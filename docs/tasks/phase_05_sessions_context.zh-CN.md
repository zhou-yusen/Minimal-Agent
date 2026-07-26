# Phase 5 任务——Session 与 Context

## 解决的问题

在把 LLM Request 保持在可预测 Context Budget 内的同时，让会话能够持久化并彼此隔离。

## 计划实现

- 实现 `SQLiteSessionStore`，初始化 Schema，并以 `(user_id, session_id)` 为复合主键。
- 将一个通过校验的 `SessionState` 序列化为 JSON Payload，连同时间戳/版本原子保存。
- 实现 create/get/save/delete 生命周期；对不存在的身份执行 get/delete 时按约定抛出 `SessionNotFoundError`。
- 接收用户消息后、每批 Tool Result 后以及终止完成时保存。
- 实现 Turn-aware Token 估算/选择器和 Summary Boundary；估算必须包括 System Prompt、序列化 Tool Schema、Summary/近期 Conversation 和 Response Token Reserve。
- 只有完整旧 Turn 且完整 Request 估算达到触发阈值时才调用 `LLMClient.summarize()`。
- Summary 是纯文本 LLM 操作：不发送 Tool Schema、禁用 Tool Calling、只返回 Summary Text。
- 压缩失败时保留旧 Summary，选择预算内最大的完整 Turn 后缀，并始终保留当前 User Turn。
- 验证 Todo State 保存在各自 Session Payload 内。

## Provider 重放决策

Phase 7A 选择显式关闭 Thinking 的 DeepSeek Chat Completions。Context 由本地 Summary 和完整的标准化消息 Turn 重建；活动 Tool Run 重放有界消息序列、Assistant Tool Call 和匹配的 Tool Result。隐藏推理既不需要也不持久化。

## Phase 5C 持久化 Checkpoint 集成

`AgentRuntime.run()` 接收可选异步 Checkpoint Callback。它在接收用户消息后、每批完整 Tool Result 后、Final/Max-step Terminal Message 后执行 Checkpoint。Runtime 不知道 `SessionStore` 或 SQLite；`AgentService` 加载复合 Session 身份后传入 `store.save`。

Checkpoint 失败时不重试、不回滚，直接传播并阻止后续 LLM/Tool Side Effect。Compression Metadata 没有独立 Checkpoint，会随下一批 Tool Result 或 Terminal Checkpoint 保存；Raw History 始终是持久恢复来源。

## 关键取舍

- 完整 Raw History 持久保存，只压缩 LLM Input。
- 基于字符的 Token 估算易解释但只是近似值。
- V1 只支持单进程、同一 Session 不并发写；分布式锁和冲突重试不在范围内。

## 验证

- 使用临时数据库测试隔离、恢复、删除、时间戳和 Round Trip。
- 捕获 Fake LLM Input，验证代词追问、Tool Result 追问和压缩行为。
- 即使 Conversation History 不变，也要断言 System Prompt、Tool Schema 或 Response Reserve 改变会影响压缩预算。
- 断言 Summary Request 不能带 Tool Schema 或产生 Tool Call。
- 强制 Summary 失败，验证 Run 仍能在降级预算内继续。

## 验收条件

- 同 User 不同 Session、不同 User 同 Session 均不共享 History 或 Todo State。
- 重新打开 Store 实例可恢复可见 History 和 Todo State。
- 追问获得足够的近期 Tool 和 Conversation Context。
- 压缩推进 Summary Boundary，但不删除持久化 Raw History。
- Completion Context 在计入固定 Prompt/Schema 成本和预留 Response Token 后仍满足限制。
- Compression 失败可观察，但不会单独导致 User Turn 失败。
- 完成 Tool Turn 后重新打开 SQLite，可恢复 History、Tool State 和成功的 Compression Metadata。
