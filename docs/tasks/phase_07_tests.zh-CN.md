# Phase 7 任务——必需测试与集成测试

## 解决的问题

将笔试要求转化为可复现的行为契约，并把真实 DeepSeek 边界与确定性测试分开验证。

## 必需离线测试矩阵

| # | 场景 | 主要断言 |
|---|---|---|
| 1 | 普通聊天 | 直接得到 Final Answer，Tool Event 为零 |
| 2 | Calculator | LLM 基于 Schema 选择工具，正确结果返回 LLM |
| 3 | Search | 确定性结果返回 LLM |
| 4 | Todo | 修改持久化到 Session Tool State |
| 5 | 两轮 Tool Call | 两轮 LLM Tool 后得到 Final Answer |
| 6 | 错误工具参数 | 结构化 Failure 到达 LLM |
| 7 | Tool Exception | `execution_error` 到达 LLM，Run 继续 |
| 8 | 最大轮数 | 调用次数精确等于配置，并返回 `max_steps` |
| 9 | Session 隔离 | History/Todo 不串线 |
| 10 | Session 恢复 | 新 Store/Runtime 实例恢复状态 |
| 11 | 普通追问 | 之前 User/Assistant Turn 出现在 LLM Context |
| 12 | Tool Result 追问 | 之前 Call/Result Pair 仍在 Context |
| 13 | Context 压缩 | Summary + 最近完整 Turn，保留 Raw History |
| 14 | LLM API Mock | 无网络验证 Timeout/Protocol/Provider 标准化 |

其他离线检查覆盖未知 Tool/Session、Compression Failure、Calculator Safety、Trace Redaction、同 Response 多 Tool Call 和持久恢复。

## 真实 DeepSeek 集成测试集

五个测试均位于 `tests/integration/test_deepseek_real.py`，带 `integration` Marker，访问真实 DeepSeek API，可能产生少量费用。只有设置 `RUN_LLM_INTEGRATION=1` 且配置加载后存在 `DEEPSEEK_API_KEY` 才运行。项目通过 `python-dotenv` 加载本地 `.env`，已有 Process Environment Variable 优先。

1. **Direct Final**——空 Tool、短 Prompt、可见 Final Text。
2. **Forced Tool Call**——只提供测试本地 `integration_echo` Schema，使用 `tool_choice="required"` 验证真实 Call 和可用 ID。
3. **Tool Result Round Trip**——使用相同真实 `tool_call_id` 返回 Canonical JSON，设置 `tool_choice="none"` 并要求可见 Final。
4. **AgentRuntime Calculator Smoke**——使用正常 Provider Routing（`tool_choice=None`）贯通真实 Client、Runtime、ContextManager、ToolRegistry、Calculator、标准化 History 和 Trace。
5. **Cross-Turn Local History Replay**——重建前一轮标准化 User/Call/Result/Final Message，加入新 User Turn，验证 DeepSeek 无需 Provider Continuation 或 Reasoning State 也能接受。

测试 2、3 是确定性的 Provider Protocol Gate；测试 5 是 Local History Replay Protocol Gate；测试 4 只是真实 Routing Smoke，不能作为唯一协议证据，因为模型自主路由可能漂移。测试不会故意提交错误 Call ID。

## 执行

默认离线运行，绝不访问网络：

```powershell
Remove-Item Env:RUN_LLM_INTEGRATION -ErrorAction SilentlyContinue
Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest
```

显式真实集成运行：

```powershell
$env:RUN_LLM_INTEGRATION="1"
.\.venv\Scripts\python.exe -m pytest tests\integration -m integration -v
```

Provider 设置冻结为 `deepseek-v4-flash`、`https://api.deepseek.com`、Thinking Disabled、SDK `max_retries=0`。

## 验收条件

- 所有必需离线场景无需凭据或网络即可通过。
- 五个真实测试可发现且默认安全 Skip。
- Phase 8 Ready 前，使用真实 API 验证 Direct、Forced-call、Same-ID Result Replay 和 Cross-turn Replay。
- 集成断言验证 Protocol Structure，而不是精确模型措辞或 JSON 空白/Property Order。
- Key、Authorization Header、Raw Provider Body 和 Reasoning Content 均不持久化、不记录 Trace、不写入文档、不打印。
