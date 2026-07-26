# Phase 2 任务——项目骨架

## 解决的问题

创建可导入、可测试的项目基础，但不实现工具或循环行为。

## 计划实现

- 添加面向 Python 3.11+ 的 `pyproject.toml`，包含官方 LLM SDK、Pydantic、按需使用的 FastAPI/httpx 和 pytest 工具。
- 添加 `src/minimal_agent/` 包，以及本阶段实际需要的最小模块集合。
- 实现集中式配置加载，为模型、超时、循环上限、数据库路径和上下文限制提供明确默认值。
- 定义 `LLMClient`、`SessionStore`、工具执行和 Trace 的领域模型与协议。
- 添加 `.env.example`，只包含变量名和安全示例。
- 添加 pytest 配置，以及证明干净导入和配置校验的冒烟测试。

## 关键取舍

- 使用 `src/` 布局，防止意外从工作目录导入代码。
- 将配置集中在一个 Pydantic Settings 风格模型中，不构建 DI 框架。
- 接口只存在于四个冻结的可替换边界。

## 验证

- 在干净环境中安装/同步依赖。
- 运行导入和配置冒烟测试。
- 检查依赖树，确认未意外引入 Agent Framework。

## 验收条件

- 项目环境中执行 `python -c "import minimal_agent"` 成功。
- 缺少真实 LLM 凭据时，只在构造或调用真实 Adapter 时失败，不影响导入和离线测试。
- 非法数值限制产生清晰的校验错误。
- 不提前实现工具行为、Runtime Loop、SQLite Schema 或 API Endpoint。
