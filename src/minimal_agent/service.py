"""Thin application service for durable session lifecycle operations."""

from minimal_agent.models import AgentRunResult, SessionState
from minimal_agent.protocols import SessionStore
from minimal_agent.runtime import AgentRuntime


class AgentService:
    """Load and checkpoint sessions while delegating agent behavior to runtime."""

    def __init__(self, *, store: SessionStore, runtime: AgentRuntime) -> None:
        self._store = store
        self._runtime = runtime

    async def create_session(self, user_id: str, session_id: str) -> SessionState:
        return await self._store.create(user_id, session_id)

    async def get_session(self, user_id: str, session_id: str) -> SessionState:
        return await self._store.get(user_id, session_id)

    async def delete_session(self, user_id: str, session_id: str) -> None:
        await self._store.delete(user_id, session_id)

    async def send_message(
        self,
        user_id: str,
        session_id: str,
        text: str,
    ) -> AgentRunResult:
        session = await self._store.get(user_id, session_id)
        return await self._runtime.run(
            session,
            text,
            checkpoint=self._store.save,
        )
