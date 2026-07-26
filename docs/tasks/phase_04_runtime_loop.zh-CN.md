# Phase 4 任务——Agent Runtime Loop

Phase 4 交付 Provider Adapter 和 `AgentRuntime`。Phase 7A 使用冻结的 V1 DeepSeek Chat Completions Adapter 替换最初面向 Responses 的 Adapter，同时保留本阶段建立的 Runtime/Tool 边界。

## 解决的问题

在不使用 Agent Framework 的情况下，实现要求的 LLM/Tool 反馈循环和显式终止机制。

## 计划实现

- 只实现一个真实 Adapter：通过官方 OpenAI Python SDK 兼容客户端调用 DeepSeek Chat Completions；不增加 Provider Registry 或第二个 Provider Adapter。
- 标准化 Chat Completions Tool Call，并将 Provider 原始 `tool_call.id` 原样保留为内部 `tool_call_id` 和对应 Tool Result ID。
- 只检测是否存在 Reasoning，不读取或持久化其内容；Summary 仅返回文本、不给工具、关闭 Thinking。
- 使用受 `max_steps` 限制的 `for` 循环实现 `AgentRuntime.run()`。
- 第一轮发送当前有界本地 Context；同一活动 Run 的后续轮次重放该有界序列，加上此后产生的所有 Assistant Tool Call Message 和完整 Tool Result Batch。
- 每次请求都携带 System Prompt、Registry Definition 和输出 Token 限制。
- 持久化/转发 Assistant Tool Call，顺序执行所有调用，追加匹配结果后继续循环。
- 只有不包含 Tool Call 且有非空可见文本的 Response 才以 `completed` 返回。
- 空或不支持的 Response 转换成 `LLMProtocolError`。
- 如果最后一次允许的 LLM 轮次仍返回 Tool Call，执行并追加全部结果，不再请求 LLM，随后追加确定性终止消息并返回 `status=max_steps`。
- 本阶段只使用最小 Session/Context Fake；持久化行为属于 Phase 5。

## 关键取舍

- 一个 Step 表示一次 LLM Request，而不是一次工具调用。
- Tool Call 优先于 Response 中同时出现的文本；直到得到无 Tool Call 的 Final Response 才停止。
- 不增加自动规划或反思调用。

## 验证

- 使用 Scripted Fake 覆盖直接回答、一次工具、连续两轮工具、同轮多个工具、非法参数、执行失败和无限工具调用。
- 断言下一次 Fake LLM Request 包含精确的 Call ID 和结果。
- 添加 Adapter 协议测试，证明 Chat Completions Tool Call 与对应 Tool Result 保留相同 `tool_call_id`。

## 验收条件

- 不依赖 Framework，完整实现要求的循环。
- Tool Error 会返回 LLM，并能在下一轮修复。
- Runtime 不会超过配置的 LLM 决策轮数。
- 活动 Run 的后续请求重放同一条连贯的有界消息序列。
- 不存在根据用户文本选择工具的分支。
- 唯一真实 Provider/API 路径是 DeepSeek Chat Completions，且 Tool Call/Result 关联无损。
