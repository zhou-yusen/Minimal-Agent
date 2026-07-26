"""Minimal trace sinks for tests, JSON logs, and readable CLI steps."""

import json
import logging
from collections.abc import Callable

from minimal_agent.models import LLMResponseType, TraceEvent, TraceEventType


class InMemoryTraceSink:
    """Collect trace events in emission order for deterministic tests."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    async def emit(self, event: TraceEvent) -> None:
        self.events.append(event)


class JsonLoggingTraceSink:
    """Write one compact JSON object per event using stdlib logging."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("minimal_agent.trace")

    async def emit(self, event: TraceEvent) -> None:
        self._logger.info(event.model_dump_json(exclude_none=True))


class ConsoleTraceSink:
    """Render safe observable agent events without exposing hidden reasoning."""

    def __init__(self, write: Callable[[str], None] = print) -> None:
        self._write = write

    async def emit(self, event: TraceEvent) -> None:
        if event.event_type is TraceEventType.LLM_REQUEST:
            self._write(f"[Step {event.loop_step}] 正在请求 LLM")
        elif event.event_type is TraceEventType.LLM_RESPONSE:
            if event.llm_response_type is LLMResponseType.TOOL_CALLS:
                self._write(
                    "[Decision] 模型请求调用 "
                    f"{event.tool_call_count or 0} 个工具"
                )
            else:
                self._write("[Decision] 模型生成最终回答")
        elif event.event_type is TraceEventType.TOOL_START:
            self._write(f"[Tool] {event.tool_name}")
            self._write(f"  arguments: {self._json(event.tool_args)}")
        elif event.event_type is TraceEventType.TOOL_RESULT:
            status = "success" if event.tool_ok else "failed"
            self._write(f"  status: {status}")
            self._write(f"  result: {self._json(event.tool_result)}")
        elif event.event_type is TraceEventType.COMPRESSION:
            status = (
                event.compression_status.value
                if event.compression_status is not None
                else "unknown"
            )
            self._write(f"[Context] compression={status}")
        elif event.event_type is TraceEventType.RECOVERY:
            self._write("[Recovery] 已封存上一次未完成的 Turn")
        elif event.event_type is TraceEventType.ERROR:
            error = event.error or {}
            self._write(
                "[Error] "
                f"code={error.get('code', 'unknown')} "
                f"stage={error.get('stage', 'unknown')}"
            )
        elif event.event_type is TraceEventType.RUN_FINISH:
            status = event.status.value if event.status is not None else "unknown"
            self._write(f"[Run] status={status} loop_steps={event.loop_steps or 0}")

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
