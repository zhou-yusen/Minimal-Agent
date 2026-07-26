# DeepSeek 集成测试报告

状态：**已通过**

用户在本机执行 Opt-in 真实 DeepSeek Suite，报告结果：

```text
5 passed in 12.52s
```

## 环境

- 记录日期：2026-07-24
- 模型：`deepseek-v4-flash`
- Base URL：`https://api.deepseek.com`
- Thinking：关闭
- SDK：`openai 2.47.0`
- Integration Test：5
- 结果：PASS

## 场景

| 场景 | 结果 |
|---|---|
| Direct Final | PASS |
| Forced Tool Call | PASS |
| 相同 `tool_call_id` 的 Tool Result Round Trip | PASS |
| AgentRuntime Calculator Smoke | PASS |
| Cross-Turn Local History Replay | PASS |

确定性 Protocol Gate 和普通 Runtime Routing Smoke 均通过。DeepSeek 接受新 User Turn 中重放的本地标准化 History，无需 Provider Conversation State 或持久化 Reasoning Content。

## 离线验证

真实运行前，在 pytest Process 中移除两个 Integration Environment Variable，离线 Suite 报告 `168 passed, 5 skipped`；Skip Test 未发起网络请求。

本报告未记录 API Key、Authorization Header、Raw Provider Body 或 Reasoning Content。
