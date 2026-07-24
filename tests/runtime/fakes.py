from collections.abc import Iterable

from minimal_agent.models import LLMRequest, LLMResult, SummaryRequest


class ScriptedFakeLLM:
    """Return predefined normalized results and record every runtime request."""

    def __init__(
        self,
        results: Iterable[LLMResult],
        *,
        summary_results: Iterable[str | Exception] = (),
    ) -> None:
        self._results = list(results)
        self._summary_results = list(summary_results)
        self.requests: list[LLMRequest] = []
        self.summary_requests: list[SummaryRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResult:
        self.requests.append(request)
        if not self._results:
            raise AssertionError("ScriptedFakeLLM has no result left")
        return self._results.pop(0)

    async def summarize(self, request: SummaryRequest) -> str:
        self.summary_requests.append(request)
        if not self._summary_results:
            raise AssertionError("ScriptedFakeLLM has no summary result left")
        result = self._summary_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result
