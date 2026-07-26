# Phase 3 任务——工具系统

## 解决的问题

提供由 Schema 描述的安全能力，使 LLM 能自主选择，并由 Runtime 统一分发。

## 计划实现

- 实现小型 `BaseTool` 契约，包含 `name`、`description`、Pydantic 参数模型/Schema 和异步 `execute()`。
- 实现带重复名称保护的 `ToolRegistry.register()`、`definitions()` 和 `execute(call, context)`。
- 使用 AST 白名单构建 `calculator`，仅允许数值常量、括号、一元运算符和基础算术；拒绝名称、属性、函数调用和不安全复杂度。
- 构建 `search`，通过注入的确定性内存语料/Backend 工作，参数为 `query` 和受限的 `top_k`。
- 构建一个 `todo` 工具，通过 action 枚举（`add`、`list`、`complete`、`delete`）区分操作并执行 action-aware 参数校验；ID 和状态保存在 `ToolContext.tool_state`。
- 将非法 JSON、未知工具、Pydantic 校验失败和执行异常统一转换成失败的 `ToolResult`。

## 关键取舍

- 一个带 action 的 Todo 工具使公开工具数量保持为要求的三个。
- 顺序修改状态保证 Todo 行为确定。
- Registry 负责失败标准化，Runtime 不出现工具专用分支。

## 验证

- 对每条成功路径和每类 Registry 错误进行单元测试。
- 断言生成的 JSON Schema 可被 Provider Adapter 格式接受。
- 断言恶意 Calculator 表达式无法执行 Python 代码。

## 验收条件

- 三个工具的 Registry Definition 均包含名称、描述和参数 Schema。
- 源代码中不存在关键词工具路由器。
- Calculator 安全、Search 确定、Todo 状态按传入 Context 隔离。
- 每个工具失败都返回带稳定错误码且无堆栈信息的 `ok=false`。
