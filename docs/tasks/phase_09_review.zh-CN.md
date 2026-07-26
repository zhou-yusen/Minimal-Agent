# Phase 9 任务——最终代码审查

## 解决的问题

确保最终提交比各阶段增量之和更小、更清晰、更经得起解释。

## 计划实现

- 对照每项笔试要求和冻结架构审查真实 Diff。
- 搜索禁止的 Framework、Keyword Routing、Global Durable State、Unrestricted `eval`、Secret Logging、Hidden Reasoning Persistence 和 Unbounded Loop。
- 逐行追踪一条 Direct Answer、Two-tool、Tool Failure 和 Recovered Session 路径。
- 删除未使用的抽象、依赖、配置和 Dead Code。
- 运行 Phase 2 选定的 Format/Lint/Type Check、默认 pytest，以及凭据可用时的显式 Integration Test。
- 生成简洁的提交 Checklist 和 Known Limitations。

## 关键取舍

- 与其润色未使用路径，不如删除推测性灵活性。
- 将 Integration Behavior 视为证据，而不是让确定性测试依赖模型的理由。
- 本阶段架构修改必须有已记录的具体缺陷和聚焦 Regression Test。

## 验证

- 在干净环境运行全部自动检查。
- 手动检查 Demo 产生的 SQLite State 和脱敏 Trace。
- 将每个编号必需测试对应到具体 Test Name 和 Result。

## 验收条件

- 所有强制要求和异常场景均已实现并记录。
- 离线 Suite 通过；真实 LLM Suite 结果按 Model/Date 单独报告。
- 不存在禁止或不必要的架构。
- `docs/ai_dev_log.md` 只包含有价值的问题解决记录，不虚构问题。
- 最终 README 与代码表达的架构和 `docs/architecture.md` 一致。
