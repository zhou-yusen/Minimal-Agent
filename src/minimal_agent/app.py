"""Composition root for the Minimal Agent application."""

from minimal_agent.config import Settings
from minimal_agent.context import ContextManager
from minimal_agent.llm.deepseek_client import DeepSeekChatClient
from minimal_agent.protocols import TraceSink
from minimal_agent.runtime import AgentRuntime
from minimal_agent.service import AgentService
from minimal_agent.sessions.sqlite import SQLiteSessionStore
from minimal_agent.tools.calculator import CalculatorTool
from minimal_agent.tools.registry import ToolRegistry
from minimal_agent.tools.search import SearchTool
from minimal_agent.tools.todo import TodoTool
from minimal_agent.tracing import JsonLoggingTraceSink


SYSTEM_PROMPT = (
    "You are a concise assistant. Use the supplied tools when they help answer "
    "the user. Preserve conversational context and explain tool failures safely."
)


def build_service(
    settings: Settings,
    *,
    trace_sink: TraceSink | None = None,
) -> AgentService:
    """Wire the single application graph from validated settings."""
    llm = DeepSeekChatClient(settings)
    tools = ToolRegistry()
    tools.register(CalculatorTool())
    tools.register(SearchTool())
    tools.register(TodoTool())
    store = SQLiteSessionStore(settings.session_database_path)
    context_manager = ContextManager(
        llm=llm,
        context_token_limit=settings.context_token_limit,
        compression_trigger=settings.context_compression_trigger,
        recent_turns_to_keep=settings.recent_turns_to_keep,
    )
    runtime = AgentRuntime(
        llm=llm,
        tools=tools,
        context_manager=context_manager,
        system_prompt=SYSTEM_PROMPT,
        max_steps=settings.max_loop_steps,
        max_output_tokens=settings.response_token_reserve,
        trace_sink=(
            trace_sink if trace_sink is not None else JsonLoggingTraceSink()
        ),
    )
    return AgentService(store=store, runtime=runtime)
