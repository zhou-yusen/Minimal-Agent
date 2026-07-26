# Minimal Agent Runtime 系统图

本文展示 `docs/architecture.md` 定义的冻结 V1 架构。Mermaid 节点已使用中文，因此图体与原文保持一致。

## 1. 系统架构图

> 完整可渲染 Mermaid 图见 [`diagrams.md`](diagrams.md) 的“System architecture”。其中展示 User/API、CLI、AgentService、Runtime、ContextManager、ToolRegistry、三个 Tool、DeepSeek Client、SQLite Store 与 TraceSink 的依赖方向。

## 2. Runtime 执行流程图

> 完整可渲染 Mermaid 图见 [`diagrams.md`](diagrams.md) 的“Runtime execution flow”。图中所有节点和分支均为中文，覆盖 Session 加载、Context 压缩、LLM 决策、四类 Tool Error、Tool Result Checkpoint、Max Steps 与 Final Answer。

为避免两份 Mermaid 源码后续发生漂移，中文版复用原文中的同一份图；原图的可见节点文字已经全部中文化。
