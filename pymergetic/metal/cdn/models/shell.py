"""Shell session tables + API schemas."""
from __future__ import annotations

from datetime import datetime
from typing import ClassVar
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import Column, DateTime
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel

from pymergetic.metal.cdn.models.common import utcnow


class ShellSession(SQLModel, table=True):
    """Browser REPL / CDN loader session (user or anonymous cookie principal)."""

    __tablename__: ClassVar[str] = "shell_sessions"

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    user_id: UUID | None = SQLField(default=None, foreign_key="users.id", index=True)
    anon_id: UUID | None = SQLField(default=None, index=True)
    cdn_base: str = SQLField(default="", max_length=512)
    channel: str = SQLField(default="lead", max_length=64)
    driver: str = SQLField(default="", max_length=64)
    hook_on: bool = SQLField(default=False)
    user_agent: str = SQLField(default="", max_length=512)
    created_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    last_activity_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )


class ShellSessionEvent(SQLModel, table=True):
    """CDN / REPL hit attributed to a shell session (last-30-min graphs)."""

    __tablename__: ClassVar[str] = "shell_session_events"

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    session_id: UUID = SQLField(foreign_key="shell_sessions.id", index=True)
    kind: str = SQLField(index=True, max_length=32)
    path: str = SQLField(default="", max_length=512)
    package: str | None = SQLField(default=None, max_length=128)
    created_at: datetime = SQLField(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )


class ShellSessionRead(SQLModel):
    id: UUID
    user_id: UUID | None
    anon_id: UUID | None
    cdn_base: str
    channel: str
    driver: str
    hook_on: bool
    user_agent: str
    created_at: datetime
    last_activity_at: datetime
    principal_label: str = ""


class ShellSessionEventRead(SQLModel):
    id: UUID
    session_id: UUID
    kind: str
    path: str
    package: str | None
    created_at: datetime


class ShellSessionEventCreate(SQLModel):
    kind: str = Field(min_length=1, max_length=32)
    path: str = Field(default="", max_length=512)
    package: str | None = Field(default=None, max_length=128)
    session_id: UUID | None = None


class ShellActivityBucket(SQLModel):
    minute: datetime
    count: int


class ShellActivityResponse(SQLModel):
    session_id: UUID
    window_minutes: int
    buckets: list[ShellActivityBucket]
    recent: list[ShellSessionEventRead]
