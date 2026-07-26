# AI Prompt 与问题解决记录（中文版）

本文件只记录有技术价值的决策；英文逐项完整记录见 [`ai_dev_log.md`](ai_dev_log.md)。

## 2026-07-24 关键决策

- **持久 History 与 LLM Context 分离**：SQLite 保留完整可见 History；请求只使用 Rolling Summary 和最近完整 Turn，且不拆 Tool Call/Result Pair。
- **不保存隐藏推理**：只持久化可见文本、Tool Call/Result，可记录 `reasoning_present`，不读取或保存 Private Chain-of-thought。
- **完整请求预算**：预算同时计入 System Prompt、Tool Schema、Conversation/Summary 和 Response Reserve；Summary 不带 Tool，只返回文本。
- **凭据不进入 Import Path**：导入 Package 和离线测试无需 Key，真实 Adapter 调用才要求凭据。
- **Calculator 与 JSON 安全边界**：使用 AST 白名单、深度/节点/数值限制，不使用 `eval`；Registry 将四类 Tool Failure 标准化为无 Stack Trace Result。
- **保留 Tool State 对象身份**：`ToolContext` 直接引用当前 `SessionState.tool_state`，确保 Todo 修改真实写回且 Session 隔离。
- **活动 Run 本地重放**：DeepSeek Chat Completions 每轮重放有界初始 Context 加活动 Run 的完整 Tool Message，不使用 Provider Continuation ID，不保存 Reasoning State。
- **Recent Turn 是软目标**：优先保留当前 Turn 和完整 Tool Pair，再在预算内尽量保留近期 Turn；Compression Failure 使用最大安全后缀。
- **Milestone Checkpoint**：Runtime 在 User Acceptance、每批 Tool Result 和 Terminal Message 后调用 Service 注入的异步 Save Callback，不依赖 SQLite。
- **结构化 Trace**：一个 `TraceEvent` 关联 Run/LLM/Tool/Compression/Error；Request Trace 只存结构 Metadata，Sink Failure 不改变 Agent 行为。
- **关闭隐藏 Retry**：SDK `max_retries=0`；Provider Failure 映射成安全 Domain Code；新 Run 先封存未完成 Turn，不重放旧 Tool Side Effect。
- **冻结 DeepSeek Provider**：V1 只支持 OpenAI SDK 兼容的 DeepSeek Chat Completions，模型 `deepseek-v4-flash`，Thinking Disabled。
- **显式真实测试 Gate**：只有启用 Marker 且存在 Key 时访问真实 API；Forced Tool、Same-ID Result Replay、Cross-turn Replay 是协议 Gate，自主 Calculator Routing 只是 Smoke。
- **单 Composition Root 与薄 CLI**：`build_service()` 只组装组件；CLI 只做 I/O、Session Create/Resume 和安全错误展示，不复制 Runtime Loop。
- **Windows Unicode 修复**：Stdout 支持时设置 `errors="replace"`，避免 GBK 无法输出某些模型字符导致 CLI 崩溃。

## 2026-07-27 关键决策

- **可读执行步骤**：新增 Opt-in `ConsoleTraceSink` 和 `--show-steps`，只展示真实结构 Event（Decision、Tool Args/Result、Recovery、Status），绝不读取或伪造隐藏推理。
- **自动加载 `.env`**：只在 `Settings.from_env()` 使用 `python-dotenv`；Process Variable 优先，显式 Mapping 绕过 `.env`，Import 仍不要求 Key。

## 统一验证方式

上述决策分别由聚焦 Regression Test、完整离线 pytest 或用户显式真实 Provider Run 验证。日志不记录 API Key、Authorization Header、Raw Provider Body、Traceback 或 Private Reasoning，也不把 Smoke Test 夸大成确定性协议保证。
