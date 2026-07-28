# Minimal Agent Runtime 构建过程与设计理由

本文根据当前仓库中的实际代码编写，目标是回答两个问题：

1. 这个项目按什么顺序构建，每一步具体落到哪些文件和方法？
2. 为什么采用这些边界、数据结构和失败语义，而没有使用更复杂的 Agent Framework？

项目最终实现的核心闭环是：

```text
User
  -> AgentService
  -> AgentRuntime
  -> LLMClient
  -> ToolRegistry
  -> ToolResult
  -> LLMClient
  -> Final Answer
```

## 一、构建原则

### 1. 先固定行为契约，再实现控制循环

Agent 的复杂度主要不在“调用一次 LLM”，而在不同边界之间的数据一致性：Tool Call ID 必须与 Tool Result 对应；Session 必须隔离；Context 不能无限增长；Tool Failure 不能使 Runtime 崩溃。

因此项目先在 `models.py` 和 `protocols.py` 中固定输入输出，再实现 Tool、Provider、Runtime 和持久化。这样每层都可以通过 Fake 单独测试，避免真实 API 的随机性掩盖控制流错误。

### 2. 只为真正需要替换的实现建立接口

项目只保留四个主要可替换点：

- `LLMClient`：离线测试使用 Scripted Fake，正常运行使用 DeepSeek。
- `SessionStore`：Runtime 不应依赖 SQLite 细节。
- `TraceSink`：测试、JSON 日志和可读 CLI 使用不同输出目的地。
- `SearchBackend`：生产 Demo 使用确定性内存搜索，测试可注入自定义语料。

没有增加 DI Framework、Provider Registry、Plugin Loader 或多层 Base Class，因为它们不能帮助完成笔试要求，反而增加解释成本。

### 3. 持久化事实与模型上下文分离

SQLite 保存完整可见 History 和 Tool State；`ContextManager` 只选择本次 LLM 能承受的 Summary 与近期完整 Turn。完整事实用于恢复和审计，有界 Context 用于推理，二者职责不同。

## 二、工程骨架

### `pyproject.toml`

项目采用 Python 3.11+ 和 `src/` Layout。运行依赖只有：

- `openai`：通过兼容 Client 调用 DeepSeek Chat Completions。
- `pydantic`：校验 Provider-neutral Boundary Object 和 Tool Argument。
- `python-dotenv`：本地 CLI 自动加载项目根目录 `.env`。

测试依赖只有 `pytest` 和 `pytest-asyncio`。没有引入 Agent Framework，也没有为了 CLI 引入 Click、Typer 或 Rich。

选择 `src/` Layout 的理由是防止测试从仓库根目录意外导入未安装的 Package，从而更接近用户真实运行方式。

### `src/minimal_agent/config.py`

`Settings` 集中保存 Provider、Loop、SQLite 和 Context 配置。

| 方法 | 构建内容 | 设计理由 |
|---|---|---|
| `validate_context_budget()` | 校验 Response Reserve 小于 Trigger 和 Context Limit | 防止配置在运行时必然无法容纳输入 |
| `from_env()` | 加载 `.env` 和 `os.environ`，只读取白名单变量 | 避免任意环境变量进入配置；Process Variable 优先，便于部署覆盖 |

显式传入 `environ` Mapping 时不加载 `.env`，这是测试隔离 Seam；导入 Package 时不会自动要求 API Key，只有构造真实 `DeepSeekChatClient` 才检查凭据。

### `src/minimal_agent/errors.py`

错误分成两类：

- `MinimalAgentError` 子类表示可安全跨越应用边界的 Infrastructure/Protocol Error。
- 普通 Tool Failure 不抛到 Runtime，而是由 Registry 转成 `ToolResult`。

`LLMTimeoutError`、`LLMConnectionError`、`LLMRateLimitError`、`LLMProtocolError`、`SessionStoreError` 等使用稳定安全消息，避免把 Provider Raw Body、Header、Traceback 或内部 Exception Text 打印给用户。

## 三、领域模型与接口

### `src/minimal_agent/models.py`

此文件是整个项目的 Provider-neutral 数据中心。

#### Message 与 LLM Model

- `ToolCall` 保存 Provider 原始 `id`、工具名和原始 `arguments_json`。
- `ConversationMessage` 统一 User、Assistant、Tool Message。
- `LLMRequest` 保存 System Prompt、Messages、Tool Definition、Output Limit 和可选 Tool Choice。
- `SummaryRequest` 刻意没有 Tools 字段，从类型层面保证 Summary 不携带 Tool Schema。
- `LLMResult` 严格区分 `FINAL` 与 `TOOL_CALLS`。

`ConversationMessage.validate_role_fields()` 强制以下不变量：

- User 必须有非空 Content。
- Assistant 必须有可见 Content 或至少一个 Tool Call。
- Tool Message 必须有 `tool_call_id`、`tool_name` 和 Content。
- 只有 Assistant 能携带 Tool Call。

`LLMResult.validate_response_shape()` 确保 Final 有可见 Text 且没有 Tool Call，Tool Response 至少有一个 Call。把约束放在 Model Boundary，比在 Runtime 中到处写防御分支更容易测试。

#### Tool 与 Session Model

- `ToolDefinition` 是 Provider-neutral Schema。
- `ToolContext` 是 Dataclass，并直接引用当前 `SessionState.tool_state`。
- `ToolResult` 使用 `ok + output/error` 表达结果，并保留 `latency_ms`。
- `SessionState` 保存复合身份、完整 History、Tool State、Summary Boundary、UTC Timestamp 和 Version。

`ToolContext` 不复制字典，因为 Todo 的修改必须真实落入 Session，并随下一个 Checkpoint 持久化。

#### Trace Model

`TraceEvent` 使用一个带可选字段的 Model 表达 Run、LLM、Tool、Compression、Recovery、Error 和 Finish。对这个小项目而言，一个可检索的 Event Model 比一套 Event Class Hierarchy 更容易检查。

### `src/minimal_agent/protocols.py`

| Protocol | 方法 | 为什么需要替换 |
|---|---|---|
| `LLMClient` | `complete()`、`summarize()` | 离线测试不能访问真实 API |
| `SessionStore` | `create/get/save/delete()` | Runtime 不应知道 SQLite |
| `TraceSink` | `emit()` | 测试、日志、CLI 展示目的不同 |
| `Tool` | Schema 属性、`execute()` | Registry 需要统一分发三个工具 |

这里使用 `Protocol` 而非继承式 ABC：具体类只需满足结构契约，不需要为了框架继承无业务价值的 Base Class。

## 四、Tool System 的构建

### `src/minimal_agent/tools/registry.py`

`ToolRegistry` 是 LLM Tool Call 与 Python Tool 之间的唯一分发边界。

#### `register(tool)`

将 Tool 按 Name 保存，重复 Name 直接 `ValueError`。重复注册是开发配置错误，不是模型可恢复错误，因此无需包装成 Tool Result。

#### `definitions()`

把每个 Tool 的 Name、Description、Pydantic JSON Schema 转成 `ToolDefinition`。Registry 保持 Provider-neutral；DeepSeek 所需的 `{"type":"function"}` 格式由 Provider Adapter 负责。

#### `execute(call, context)`

执行顺序严格固定：

1. `json.loads()` 解析 `arguments_json`，并拒绝 NaN/Infinity。
2. 按 Name 查询 Tool。
3. 使用 `arguments_model.model_validate()` 校验参数。
4. 调用异步 `tool.execute()`。
5. 验证 Output 可以序列化为标准 JSON。
6. 返回关联同一 `call.id` 的 `ToolResult`。

四类错误分别标准化为 `invalid_json`、`unknown_tool`、`validation_error`、`execution_error`。Runtime 不需要 Calculator/Search/Todo 专用分支，且模型能在下一轮看到失败并修复。

### `calculator.py`

`CalculatorArguments` 只接收长度受限的 `expression`。

- `_parse()` 使用 `ast.parse(..., mode="eval")`，限制 AST Node 数和深度。
- `_evaluate()` 只允许 Numeric Constant、Unary `+/-` 和 Binary `+ - * /`。
- `_check_result()` 拒绝非有限数和超大结果。
- `_depth()` 防止构造过深语法树消耗资源。

不使用 `eval` 的理由是即使先做字符串过滤，也容易遗漏 Name、Call、Attribute 等 Python 执行能力；AST 白名单明确表达“只允许什么”。

### `search.py`

`SearchArguments` 限制 Query 长度、要求至少一个字母或数字，并把 `top_k` 限制为 1–10。

`SearchBackend` 提供注入 Seam；`InMemorySearchBackend.search()` 对 Query Token 做简单词法匹配，按 Score 降序和 Document ID 升序稳定排序。这里不引入 Embedding、BM25、Crawler 或 Vector DB，因为题目只要求证明 Schema → Registry → Tool → Result 链路，确定性比搜索质量更重要。

### `todo.py`

一个 `TodoTool` 通过 `TodoAction` 区分 add/list/complete/delete。

- `TodoArguments.validate_action_fields()` 根据 Action 校验 Text/Item ID 组合。
- `_state()` 在 `ToolContext.tool_state["todo"]` 中初始化并校验状态。
- `_find_index()` 查找稳定的 `todo-N` ID。
- `execute()` 修改当前 Session 引用的 Items 和 `next_id`。

没有拆成四个 Tool，是为了保持公共 Tool Set 小，并集中表达 Todo State Machine；没有使用模块级 List，是为了保证 Session 隔离和 SQLite 恢复。

## 五、真实 LLM Adapter

### `src/minimal_agent/llm/deepseek_client.py`

`DeepSeekChatClient` 是唯一 Provider-specific 文件。

#### `__init__()`

使用 `AsyncOpenAI`，配置 DeepSeek Base URL、Model、Timeout 和 `max_retries=0`。关闭 SDK 隐式 Retry，是为了保证“一次 Runtime Decision 等于一次 Provider Attempt”，避免未来具有 Side Effect 的 Tool 因隐藏重试产生歧义。

#### `complete(request)`

1. `_messages_to_chat()` 将标准化 Message 映射为 Chat Completions 格式。
2. `_tools_to_chat()` 将 Tool Definition 转为 Function Tool Schema。
3. 每次请求显式关闭 Thinking。
4. `_create_completion()` 调用真实 SDK。
5. `_extract_message()` 提取 Visible Text、Tool Call 和 `reasoning_present`。
6. 返回 Provider-neutral `LLMResult`。

Tool Call ID 原样保存并在 Tool Message 中重放，这是 Provider Protocol 正确性的关键。项目只检测 Reasoning 是否存在，不读取或持久化 `reasoning_content`。

#### `summarize(request)`

Summary Request 不发送 Tools、不允许 Tool Calling、只接受可见 Text。它复用同一 Client，但与普通 `complete()` 的协议边界分开，防止模型在压缩阶段调用工具。

#### `_create_completion()`

将 SDK Timeout、Connection、Rate Limit、Status 和一般 API Error 映射成安全 Domain Error。只允许 Status Code 和 Request ID 跨越 Adapter Boundary，不传 Raw Body。

#### `_canonical_tool_output()`

重新解析并 Canonicalize Tool JSON，拒绝非标准常量。这样发给 Provider 的 Tool Result 始终是稳定、标准的 JSON，而不是 Python `repr`。

## 六、Session 持久化

### `src/minimal_agent/sessions/sqlite.py`

`SQLiteSessionStore` 使用 `(user_id, session_id)` 复合主键，并在一个 Row 中保存完整 `SessionState` JSON。

| 方法 | 行为 | 理由 |
|---|---|---|
| `__init__()` | 创建父目录并初始化 Schema | CLI 首次运行即可使用 |
| `create()` | 插入空 Session；重复身份报错 | Create 与 Run 语义明确 |
| `get()` | 按复合键加载并重新执行 Pydantic 校验 | 防止损坏数据进入 Runtime |
| `save()` | 原子更新 JSON、Timestamp、Version | 一个 Session Checkpoint 是一个 Transaction |
| `delete()` | 按复合键删除 | 不允许跨 User 误删同名 Session |
| `_serialize()` | 使用 Pydantic JSON 序列化并包装错误 | Store Boundary 不泄漏底层异常 |

首版不把 Message、Todo、Summary 分表，是因为笔试规模下“一行一个 Session 文档”最容易解释和原子恢复；`SessionStore` Protocol 仍保留未来替换可能性。

## 七、Context Management

### `src/minimal_agent/context.py`

`ContextManager.build()` 只负责“第一轮 LLM Request 应看到什么”，不负责 Runtime Loop。

主要步骤：

1. `_messages_after_boundary()` 找到未被现有 Summary 覆盖的 History。
2. `_split_turns()` 按 User Message 切分 Turn，并保留 Tool Pair。
3. `estimate_request_tokens()` 同时估算 System Prompt、Tool Schema、Messages 和 Response Reserve。
4. 达到 Compression Trigger 时，`_summary_candidates()` 只选择旧的 Completed Turn。
5. 调用 `LLMClient.summarize()`，成功后推进 `summary_up_to_message_id`。
6. `_fit_to_limit()` 从最旧 Completed Turn 开始删除，必要时放弃 Summary。
7. 如果 System Prompt、Tools、Current Turn 和 Reserve 仍放不下，抛出 `ContextWindowExceededError`。

Token 估算使用 `CHARS_PER_TOKEN = 4`。它不追求精确 Tokenizer，而追求规则透明、无需 Provider-specific 依赖；安全性由保守 Limit 和真实 Integration Test 补充验证。

Compression Failure 不直接终止 User Turn：保留旧 Summary，并选择最大安全完整后缀。这是因为 Summary 是优化，不应成为基本对话的单点故障。

## 八、Agent Runtime Loop

### `src/minimal_agent/runtime.py`

`AgentRuntime.run()` 是项目唯一 Agent 控制循环，处理一个已加载 Session 的一个 User Turn。

#### 进入 Run

1. 创建 `turn_id` 并发出 `RUN_START`。
2. `_has_trailing_incomplete_turn()` 检测上次进程是否在非 Final 状态中断。
3. 如有中断，追加确定性 Marker，先 Checkpoint，再接收新消息；不重新执行旧 Tool。
4. 追加当前 User Message 并立即 Checkpoint。
5. 调用 `ContextManager.build()` 构造有界初始 Request Messages。
6. 创建直接引用 `session.tool_state` 的 `ToolContext`。

#### 有界决策循环

`for loop_step in range(1, max_steps + 1)` 精确定义最大 LLM 调用次数：

- 构造 `LLMRequest`，每轮都携带 System Prompt、Tools 和 Output Limit。
- 调用 `LLMClient.complete()` 并记录结构 Trace。
- 若为 Final：追加 Assistant Message、Checkpoint、发出 Finish、返回 `completed`。
- 若为 Tool Calls：先追加 Assistant Tool-call Message，然后按模型顺序执行全部 Call。
- 每个 `ToolResult` 经 `_tool_message()` 转成标准 JSON Tool Message，并保留 Call ID/Name。
- 一批 Tool Result 完成后只 Checkpoint 一次，再把 Assistant Call 与 Tool Messages 追加到活动 Request Replay。

同一个 Response 中多个 Tool 按顺序执行，但只算一个 Loop Step。采用顺序执行是因为 Todo 会修改状态；并行会引入不必要的顺序和事务问题。

#### Max Steps

最后一次允许的 LLM Response 即使仍有 Tool Call，也会执行并保存结果；循环结束后追加 `MAX_STEPS_MESSAGE`，不发起第 `max_steps + 1` 次请求。这样既保存已发生事实，又严格避免 Off-by-one。

#### Checkpoint 与 Trace

- `_checkpoint()` 接收由 Service 注入的异步 Callback，Runtime 不依赖 Store。
- `_emit_trace()` 采用 Best-effort；Trace Sink Failure 不改变业务结果。
- `_safe_error()` 将 Exception 映射为稳定 Code 和 Stage，不输出 Raw Provider Detail。
- `_tool_message()` 只给 LLM 发送 `ok/output` 或 `ok/error`，不发送 Latency、Traceback 或 Python `repr`。

## 九、Service、组装与 CLI

### `src/minimal_agent/service.py`

`AgentService` 是薄应用层：

- `create_session()`、`get_session()`、`delete_session()` 委托 Store。
- `send_message()` 先按复合身份加载 Session，再调用 Runtime，并把 `store.save` 作为 Checkpoint 传入。

这样 Runtime 只处理一个内存中的 Session Turn，Service 负责持久生命周期。

### `src/minimal_agent/app.py`

`build_service(settings, trace_sink=None)` 是唯一 Composition Root：

1. 创建 `DeepSeekChatClient`。
2. 注册 Calculator、Search、Todo。
3. 创建 `SQLiteSessionStore`。
4. 创建共享同一 LLM 的 `ContextManager`。
5. 创建 `AgentRuntime`。
6. 返回 `AgentService`。

所有生产组件只在这里组装，业务模块不自行读取全局配置，也不使用 DI Container。

### `src/minimal_agent/cli.py`

- `configure_console_output()` 防止 Windows Console 因不支持模型 Unicode 而崩溃。
- `build_parser()` 定义 User、Session、`--debug`、`--show-steps`；两种 Trace 模式互斥。
- `run_interactive()` 创建或恢复 Session，处理空输入和 Exit Command，并把消息交给 Service。
- `main()` 加载 Settings、检查 Key、选择 Trace Sink、组装 Service 并启动 Async Loop。

CLI 不实现 Agent Loop、Tool Dispatch、Persistence 或 Context Compression，因此以后增加 HTTP Adapter 时可复用同一 Service/Runtime。

## 十、可观察性

### `src/minimal_agent/tracing.py`

- `InMemoryTraceSink`：按顺序保存 Event，便于测试断言。
- `JsonLoggingTraceSink`：每条 Event 输出一行紧凑 JSON，便于开发排障。
- `ConsoleTraceSink`：为 `--show-steps` 展示 LLM Decision Type、Tool Args/Result、Compression/Recovery 和 Terminal Status。

`ConsoleTraceSink` 展示的是实际控制流证据，不是 Chain-of-thought。它从不读取 `reasoning_content`；Tool Args/Result 可能包含用户数据，因此由用户显式启用。

## 十一、端到端方法调用链

一次带工具的 CLI 消息按以下顺序运行：

```text
cli.main()
  -> Settings.from_env()
  -> app.build_service()
  -> cli.run_interactive()
  -> AgentService.send_message()
  -> SQLiteSessionStore.get()
  -> AgentRuntime.run()
       -> checkpoint(SQLiteSessionStore.save)
       -> ContextManager.build()
       -> DeepSeekChatClient.complete()
       -> ToolRegistry.execute()
            -> ConcreteTool.execute()
       -> AgentRuntime._tool_message()
       -> checkpoint(SQLiteSessionStore.save)
       -> DeepSeekChatClient.complete()
       -> checkpoint(SQLiteSessionStore.save)
  -> CLI 输出 AgentRunResult.final_answer
```

如果 Tool 参数错误，链路不会在 Registry 处中断，而是：

```text
ToolRegistry.execute()
  -> ToolResult(ok=false, error=validation_error)
  -> AgentRuntime._tool_message()
  -> 下一轮 DeepSeekChatClient.complete()
  -> LLM 修复调用或解释错误
```

## 十二、测试如何随构建推进

测试目录按生产边界组织：

- `tests/test_models.py`：Role 和 Result 不变量。
- `tests/tools/`：Registry 四类错误、Calculator Safety、Search Determinism、Todo State Isolation。
- `tests/llm/`：DeepSeek Request/Response Mapping、Call ID、Error Normalization。
- `tests/runtime/`：Direct Final、连续 Tool、多 Tool、Repair、Max Steps、Checkpoint、Interrupted Recovery。
- `tests/context/`：Budget、Follow-up、Compression 和 Fallback。
- `tests/sessions/`：复合身份、Round Trip、恢复和损坏数据。
- `tests/tracing/`：Event Order、Correlation、Redaction 和 Console Projection。
- `tests/test_cli.py`、`tests/test_app.py`、`tests/service/`：薄 Adapter 与生产 Wiring。
- `tests/integration/test_deepseek_real.py`：五个显式启用的真实 Provider Protocol Gate。

离线测试使用 `ScriptedFakeLLM`，它只按预设顺序返回结果，不根据关键词模拟智能。这样测试证明的是 Runtime 控制流，而不是 Fake 的路由逻辑。

## 十三、为什么没有构建更多功能

本项目没有 FastAPI、Streaming、Retry Framework、Parallel Tool、Vector Database、RAG、多 Agent、Long-term Memory 或 Background Worker。原因不是这些能力没有价值，而是它们都不是证明最小 Agent Runtime 所必需的，并会模糊以下核心证据：

- LLM 是否真的根据 Schema 发出 Tool Call。
- Tool Call/Result 是否按协议关联。
- Failure 是否能返回 LLM 自我修复。
- Session 是否隔离并可恢复。
- Context 是否有界。
- Loop 是否严格终止。

对于技术笔试，保持这些路径短、可读、可测试，比展示更多技术名词更重要。

## 十四、构建结果

最终项目形成清晰的单向依赖：CLI 调用 Service，Service 加载 Store 并调用 Runtime；Runtime 使用 Context、LLM、Registry 和 Trace；Provider 与 SQLite 细节停留在 Adapter 层。每个关键边界都有离线 Fake 或临时数据库测试，真实 DeepSeek 测试只作为显式 Protocol Integration Evidence。
