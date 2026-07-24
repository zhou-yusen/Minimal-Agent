import pytest

from minimal_agent.app import build_service
from minimal_agent.config import Settings


@pytest.mark.asyncio
async def test_build_service_wires_store_and_production_tools(tmp_path) -> None:
    database = tmp_path / "agent.db"
    service = build_service(
        Settings(
            deepseek_api_key="test-key",
            session_database_path=database,
        )
    )

    state = await service.create_session("user", "session")

    assert state.user_id == "user"
    assert database.exists()
    definitions = service._runtime._tools.definitions()  # type: ignore[attr-defined]
    assert [definition.name for definition in definitions] == [
        "calculator",
        "search",
        "todo",
    ]
