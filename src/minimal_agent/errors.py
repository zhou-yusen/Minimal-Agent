"""Domain errors that may cross a runtime or adapter boundary."""


class MinimalAgentError(Exception):
    """Base class for expected application boundary errors."""


class SessionNotFoundError(MinimalAgentError):
    def __init__(self, user_id: str, session_id: str) -> None:
        self.user_id = user_id
        self.session_id = session_id
        super().__init__(f"session not found: user_id={user_id!r}, session_id={session_id!r}")


class SessionStoreError(MinimalAgentError):
    """Raised when durable session I/O fails."""


class LLMProviderError(MinimalAgentError):
    """Safe provider failure with optional diagnostic correlation metadata."""

    def __init__(
        self,
        message: str = "OpenAI provider request failed",
        *,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.request_id = request_id
        super().__init__(message)


class LLMTimeoutError(LLMProviderError):
    def __init__(self) -> None:
        super().__init__("OpenAI request timed out")


class LLMConnectionError(LLMProviderError):
    def __init__(self) -> None:
        super().__init__("OpenAI connection failed")


class LLMRateLimitError(LLMProviderError):
    def __init__(
        self,
        *,
        status_code: int = 429,
        request_id: str | None = None,
    ) -> None:
        super().__init__(
            "OpenAI rate limit exceeded",
            status_code=status_code,
            request_id=request_id,
        )


class LLMProtocolError(MinimalAgentError):
    """Raised when an LLM response cannot be normalized safely."""


class ContextCompressionError(MinimalAgentError):
    """Raised internally when summary generation fails."""


class ContextWindowExceededError(MinimalAgentError):
    """Raised when mandatory request content cannot fit the context window."""


class ToolExecutionError(MinimalAgentError):
    """A deliberately safe tool error whose message may be returned to the LLM."""
