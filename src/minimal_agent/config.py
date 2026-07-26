"""Central configuration for the Minimal Agent Runtime."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class Settings(BaseModel):
    """Validated application settings loaded explicitly from the environment."""

    model_config = ConfigDict(extra="forbid")

    deepseek_api_key: SecretStr | None = None
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_loop_steps: int = Field(default=6, ge=1, le=50)

    session_database_path: Path = Path("data/minimal_agent.db")

    context_token_limit: int = Field(default=8_000, ge=1_000)
    context_compression_trigger: int = Field(default=6_000, ge=1)
    response_token_reserve: int = Field(default=1_000, ge=1)
    recent_turns_to_keep: int = Field(default=4, ge=1, le=50)

    _ENV_TO_FIELD: ClassVar[dict[str, str]] = {
        "DEEPSEEK_API_KEY": "deepseek_api_key",
        "DEEPSEEK_MODEL": "deepseek_model",
        "DEEPSEEK_BASE_URL": "deepseek_base_url",
        "LLM_TIMEOUT_SECONDS": "llm_timeout_seconds",
        "MAX_LOOP_STEPS": "max_loop_steps",
        "SESSION_DATABASE_PATH": "session_database_path",
        "CONTEXT_TOKEN_LIMIT": "context_token_limit",
        "CONTEXT_COMPRESSION_TRIGGER": "context_compression_trigger",
        "RESPONSE_TOKEN_RESERVE": "response_token_reserve",
        "RECENT_TURNS_TO_KEEP": "recent_turns_to_keep",
    }

    @model_validator(mode="after")
    def validate_context_budget(self) -> Settings:
        """Ensure fixed output capacity leaves room for an input request."""
        if self.response_token_reserve >= self.context_token_limit:
            raise ValueError(
                "response_token_reserve must be smaller than context_token_limit"
            )
        if self.context_compression_trigger > self.context_token_limit:
            raise ValueError(
                "context_compression_trigger cannot exceed context_token_limit"
            )
        if self.response_token_reserve >= self.context_compression_trigger:
            raise ValueError(
                "response_token_reserve must be smaller than "
                "context_compression_trigger"
            )
        return self

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Load documented settings from ``.env`` and the process environment.

        An explicitly supplied mapping remains isolated for deterministic tests.
        Existing process variables take precedence over values in ``.env``.
        """
        if environ is None:
            dotenv_path = find_dotenv(usecwd=True)
            if dotenv_path:
                load_dotenv(dotenv_path, override=False)
        source = os.environ if environ is None else environ
        values = {
            field_name: source[env_name]
            for env_name, field_name in cls._ENV_TO_FIELD.items()
            if env_name in source and source[env_name] != ""
        }
        return cls.model_validate(values)
