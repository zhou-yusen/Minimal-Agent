from collections.abc import Iterable

from minimal_agent.models import LLMRequest, LLMResult, SummaryRequest


class ScriptedFakeLLM:
    """Return predefined normalized results and record every runtime request."""

    def __init__(self, results: Iterable[LLMResult]) -> None:
        self._results = list(results)
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResult:
        self.requests.append(request)
        if not self._results:
            raise AssertionError("ScriptedFakeLLM has no result left")
        return self._results.pop(0)

    async def summarize(self, request: SummaryRequest) -> str:
        del request
        raise AssertionError("AgentRuntime must not call summarize")
