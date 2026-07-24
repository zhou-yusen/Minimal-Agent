from pathlib import Path

import pytest
from pydantic import ValidationError

from minimal_agent.config import Settings


def test_settings_do_not_require_api_key() -> None:
    settings = Settings.from_env({})

    assert settings.openai_api_key is None
    assert settings.context_token_limit == 8_000
    assert settings.response_token_reserve == 1_000


def test_settings_parse_documented_environment() -> None:
    settings = Settings.from_env(
        {
            "OPENAI_API_KEY": "test-secret",
            "OPENAI_MODEL": "test-model",
            "MAX_LOOP_STEPS": "9",
            "LLM_TIMEOUT_SECONDS": "12.5",
            "SESSION_DATABASE_PATH": "tmp/test.db",
            "CONTEXT_TOKEN_LIMIT": "12000",
            "CONTEXT_COMPRESSION_TRIGGER": "9000",
            "RESPONSE_TOKEN_RESERVE": "1500",
            "RECENT_TURNS_TO_KEEP": "6",
        }
    )

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "test-secret"
    assert settings.openai_model == "test-model"
    assert settings.max_loop_steps == 9
    assert settings.llm_timeout_seconds == 12.5
    assert settings.session_database_path == Path("tmp/test.db")
    assert settings.context_token_limit == 12_000
    assert settings.context_compression_trigger == 9_000
    assert settings.response_token_reserve == 1_500
    assert settings.recent_turns_to_keep == 6


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_loop_steps": 0},
        {"llm_timeout_seconds": 0},
        {"context_token_limit": 1_000, "response_token_reserve": 1_000},
        {"context_token_limit": 4_000, "context_compression_trigger": 4_001},
        {"context_compression_trigger": 500, "response_token_reserve": 500},
    ],
)
def test_settings_reject_invalid_limits(overrides: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        Settings(**overrides)
