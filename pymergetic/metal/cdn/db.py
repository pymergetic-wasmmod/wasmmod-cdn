"""Async SQLModel / SQLAlchemy engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from pymergetic.metal.cdn.settings import Settings

# Legacy additive patches for DBs created before Alembic baseline.
# Prefer: ``metal-cdn db upgrade`` (see alembic/).
_USER_COLUMNS: dict[str, str] = {
    "password_hash": "ALTER TABLE users ADD COLUMN password_hash VARCHAR(128)",
    "is_admin": "ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0 NOT NULL",
}
_API_KEY_COLUMNS: dict[str, str] = {
    "scopes": "ALTER TABLE api_keys ADD COLUMN scopes VARCHAR(512) DEFAULT '' NOT NULL",
}


class Database:
    """Owns the async engine and session maker."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        connect_args: dict[str, object] = {}
        if settings.database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            echo=settings.debug,
            connect_args=connect_args,
        )
        self.session_maker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def create_all(self) -> None:
        # Register federation tables on SQLModel.metadata.
        import pymergetic.metal.cdn.services.federation.tables  # noqa: F401

        async with self.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
            await conn.run_sync(_ensure_user_columns)
            await conn.run_sync(_ensure_api_key_columns)

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_maker() as session:
            yield session


def _ensure_user_columns(sync_conn) -> None:  # type: ignore[no-untyped-def]
    inspector = inspect(sync_conn)
    if "users" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("users")}
    for name, ddl in _USER_COLUMNS.items():
        if name not in existing:
            sync_conn.execute(text(ddl))


def _ensure_api_key_columns(sync_conn) -> None:  # type: ignore[no-untyped-def]
    inspector = inspect(sync_conn)
    if "api_keys" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("api_keys")}
    for name, ddl in _API_KEY_COLUMNS.items():
        if name not in existing:
            sync_conn.execute(text(ddl))
