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


class LLMTimeoutError(MinimalAgentError):
    """Raised when the configured LLM request timeout is exceeded."""


class LLMProtocolError(MinimalAgentError):
    """Raised when an LLM response cannot be normalized safely."""


class ContextCompressionError(MinimalAgentError):
    """Raised internally when summary generation fails."""


class ContextWindowExceededError(MinimalAgentError):
    """Raised when mandatory request content cannot fit the context window."""


class ToolExecutionError(MinimalAgentError):
    """A deliberately safe tool error whose message may be returned to the LLM."""
