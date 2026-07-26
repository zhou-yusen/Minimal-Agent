# 真实 CLI Demo 报告

状态：**修复一项 CLI 兼容性问题后通过**
日期：2026-07-24

## 已验证路径

- 真实 DeepSeek 问候回复
- 使用真实 Calculator Tool Call 计算 `125 * 37`
- 通过生产 Registry 新增 Todo
- 使用相同 `(user_id, session_id)` 退出并重启进程
- 从 SQLite 恢复并列出之前持久化的 Todo

Demo 使用生产 CLI、AgentService、AgentRuntime、DeepSeek Adapter、ToolRegistry 和 `data/minimal_agent.db`，未使用 Integration-only Tool。

## 发现的问题

第一次重启并列出持久化数据时，模型回复包含当前 Windows GBK Stdout 不支持的 Unicode Symbol。虽然 Provider Request、Runtime 和 SQLite State 均已成功，`print()` 仍抛出 `UnicodeEncodeError` 并终止 CLI。

CLI 现会在 Stream 支持 `reconfigure()` 时配置 `stdout errors="replace"`，并有聚焦离线测试覆盖。修复后重新打开相同 Session，成功列出持久化 Todo。

自动 PowerShell Input Pipeline 无法在活动 Code Page 中保留中文，因此 Demo 的持久化文本出现替换字符。这是 Harness Encoding 限制；Composite Session Persistence 和 Todo State Recovery 仍已验证。报告未记录 API Key、Raw Provider Body、Authorization Header 或 Private Conversation Transcript。
