"""SQLite persistence for complete SessionState JSON documents."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from minimal_agent.errors import SessionNotFoundError, SessionStoreError
from minimal_agent.models import SessionState, utc_now


class SQLiteSessionStore:
    """Persist one validated SessionState per composite session identity."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        try:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize_schema()
        except (OSError, sqlite3.Error) as exc:
            raise SessionStoreError(
                "failed to initialize session database"
            ) from exc

    async def create(self, user_id: str, session_id: str) -> SessionState:
        now = utc_now()
        state = SessionState(
            user_id=user_id,
            session_id=session_id,
            created_at=now,
            updated_at=now,
        )
        state_json = self._serialize(state)

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO sessions (
                        user_id,
                        session_id,
                        state_json,
                        created_at,
                        updated_at,
                        version
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.user_id,
                        state.session_id,
                        state_json,
                        self._timestamp(state.created_at),
                        self._timestamp(state.updated_at),
                        state.version,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise SessionStoreError(
                "session already exists: "
                f"user_id={user_id!r}, session_id={session_id!r}"
            ) from exc
        except sqlite3.Error as exc:
            raise SessionStoreError("failed to create session") from exc

        return state

    async def get(self, user_id: str, session_id: str) -> SessionState:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT state_json
                    FROM sessions
                    WHERE user_id = ? AND session_id = ?
                    """,
                    (user_id, session_id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise SessionStoreError("failed to read session") from exc

        if row is None:
            raise SessionNotFoundError(user_id, session_id)

        try:
            state = SessionState.model_validate_json(row[0])
        except ValidationError as exc:
            raise SessionStoreError("stored session state is invalid") from exc

        if state.user_id != user_id or state.session_id != session_id:
            raise SessionStoreError(
                "stored session identity does not match its database key"
            )
        return state

    async def save(self, state: SessionState) -> None:
        updated_at = max(utc_now(), state.created_at)
        state_to_save = state.model_copy(update={"updated_at": updated_at})
        state_json = self._serialize(state_to_save)

        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE sessions
                    SET state_json = ?,
                        created_at = ?,
                        updated_at = ?,
                        version = ?
                    WHERE user_id = ? AND session_id = ?
                    """,
                    (
                        state_json,
                        self._timestamp(state_to_save.created_at),
                        self._timestamp(state_to_save.updated_at),
                        state_to_save.version,
                        state_to_save.user_id,
                        state_to_save.session_id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise SessionNotFoundError(
                        state.user_id,
                        state.session_id,
                    )
        except sqlite3.Error as exc:
            raise SessionStoreError("failed to save session") from exc

        state.updated_at = updated_at

    async def delete(self, user_id: str, session_id: str) -> None:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    DELETE FROM sessions
                    WHERE user_id = ? AND session_id = ?
                    """,
                    (user_id, session_id),
                )
                if cursor.rowcount == 0:
                    raise SessionNotFoundError(user_id, session_id)
        except sqlite3.Error as exc:
            raise SessionStoreError("failed to delete session") from exc

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    PRIMARY KEY (user_id, session_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._database_path)

    @staticmethod
    def _serialize(state: SessionState) -> str:
        try:
            return state.model_dump_json()
        except (PydanticSerializationError, TypeError, ValueError) as exc:
            raise SessionStoreError("session state is not serializable") from exc

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.isoformat()
