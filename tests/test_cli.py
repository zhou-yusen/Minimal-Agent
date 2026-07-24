from __future__ import annotations

from collections.abc import Iterable

import pytest

from minimal_agent import cli
from minimal_agent.context import ContextManager
from minimal_agent.errors import LLMTimeoutError, SessionNotFoundError
from minimal_agent.models import (
    AgentRunResult,
    AgentRunStatus,
    ConversationMessage,
    LLMResponseType,
    LLMResult,
    MessageRole,
    SessionState,
)
from minimal_agent.runtime import AgentRuntime
from minimal_agent.service import AgentService
from minimal_agent.sessions.sqlite import SQLiteSessionStore
from minimal_agent.tools.registry import ToolRegistry
from tests.runtime.fakes import ScriptedFakeLLM


def final(text: str) -> LLMResult:
    return LLMResult(
        assistant_message=ConversationMessage(
            role=MessageRole.ASSISTANT,
            content=text,
        ),
        response_type=LLMResponseType.FINAL,
    )


def service_with_fake(database, *answers: str) -> AgentService:
    llm = ScriptedFakeLLM([final(answer) for answer in answers])
    registry = ToolRegistry()
    return AgentService(
        store=SQLiteSessionStore(database),
        runtime=AgentRuntime(
            llm=llm,
            tools=registry,
            context_manager=ContextManager(
                llm=llm,
                context_token_limit=8_000,
                compression_trigger=6_000,
                recent_turns_to_keep=4,
            ),
            system_prompt="Answer briefly.",
            max_steps=3,
            max_output_tokens=200,
        ),
    )


def reader(values: Iterable[str]):
    remaining = iter(values)
    return lambda prompt: next(remaining)


@pytest.mark.asyncio
async def test_cli_creates_then_recovers_durable_session(tmp_path) -> None:
    database = tmp_path / "cli.db"
    first_output: list[str] = []
    await cli.run_interactive(
        service_with_fake(database, "first answer"),
        user_id="greg",
        session_id="demo",
        read_input=reader(["hello", "/exit"]),
        write_output=first_output.append,
    )

    second_output: list[str] = []
    resumed_service = service_with_fake(database, "second answer")
    await cli.run_interactive(
        resumed_service,
        user_id="greg",
        session_id="demo",
        read_input=reader(["follow up", "/quit"]),
        write_output=second_output.append,
    )
    state = await resumed_service.get_session("greg", "demo")

    assert "Agent> first answer" in first_output
    assert "Agent> second answer" in second_output
    assert [message.role for message in state.history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]


class RecordingService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.created: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str, str]] = []

    async def get_session(self, user_id: str, session_id: str) -> SessionState:
        raise SessionNotFoundError(user_id, session_id)

    async def create_session(
        self,
        user_id: str,
        session_id: str,
    ) -> SessionState:
        self.created.append((user_id, session_id))
        return SessionState(user_id=user_id, session_id=session_id)

    async def send_message(
        self,
        user_id: str,
        session_id: str,
        text: str,
    ) -> AgentRunResult:
        self.sent.append((user_id, session_id, text))
        if self.error is not None:
            raise self.error
        return AgentRunResult(
            user_id=user_id,
            session_id=session_id,
            turn_id="turn",
            status=AgentRunStatus.COMPLETED,
            final_answer="ok",
            loop_steps=1,
        )


@pytest.mark.asyncio
async def test_cli_exit_creates_session_without_sending_message() -> None:
    service = RecordingService()

    await cli.run_interactive(
        service,  # type: ignore[arg-type]
        user_id="u",
        session_id="s",
        read_input=reader(["/exit"]),
        write_output=lambda text: None,
    )

    assert service.created == [("u", "s")]
    assert service.sent == []


@pytest.mark.asyncio
async def test_cli_displays_safe_domain_error_and_continues() -> None:
    service = RecordingService(error=LLMTimeoutError())
    output: list[str] = []

    await cli.run_interactive(
        service,  # type: ignore[arg-type]
        user_id="u",
        session_id="s",
        read_input=reader(["hello", "/exit"]),
        write_output=output.append,
    )

    assert service.sent == [("u", "s", "hello")]
    assert "Agent error: LLM provider request timed out" in output


def test_main_requires_api_key(monkeypatch, capsys) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    exit_code = cli.main([])

    assert exit_code == 2
    assert capsys.readouterr().out == "DEEPSEEK_API_KEY is not set.\n"


def test_main_generates_short_session(monkeypatch) -> None:
    captured: dict[str, str] = {}
    service = RecordingService()

    async def capture_run(
        built_service,
        *,
        user_id: str,
        session_id: str,
    ) -> None:
        assert built_service is service
        captured["user_id"] = user_id
        captured["session_id"] = session_id

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(cli, "build_service", lambda settings: service)
    monkeypatch.setattr(cli, "run_interactive", capture_run)

    assert cli.main(["--user", "greg"]) == 0
    assert captured["user_id"] == "greg"
    assert len(captured["session_id"]) == 8
