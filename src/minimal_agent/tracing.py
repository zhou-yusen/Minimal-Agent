"""Minimal trace sinks for tests and JSON development logs."""

import logging

from minimal_agent.models import TraceEvent


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
