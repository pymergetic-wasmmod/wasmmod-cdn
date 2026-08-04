"""Browser REPL / CDN loader shell sessions and hit events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import Request
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from pymergetic.metal.cdn.models import (
    ShellActivityBucket,
    ShellActivityResponse,
    ShellSession,
    ShellSessionEvent,
    ShellSessionEventRead,
    ShellSessionRead,
    UserRead,
    utcnow,
)

SESSION_ANON_KEY = "anon_id"
ACTIVE_WINDOW = timedelta(minutes=30)
DEFAULT_DRIVER = "metal-cdn"


def ensure_principal(
    request: Request,
    user: UserRead | None,
) -> tuple[UUID | None, UUID | None, str]:
    """Return (user_id, anon_id, principal_label); mint anon_id in cookie if needed.

    When a user is logged in, keep any existing anon_id in the cookie (history /
    claim continuity) but do not mint a new one solely for the user session.
    """
    raw = request.session.get(SESSION_ANON_KEY)
    anon: UUID | None = None
    if raw:
        try:
            anon = UUID(str(raw))
        except ValueError:
            anon = None
    if user is not None:
        return user.id, anon, str(user.email)
    if anon is None:
        anon = uuid4()
        request.session[SESSION_ANON_KEY] = str(anon)
    return None, anon, "anon"


class ShellSessionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _label(self, row: ShellSession, email: str | None = None) -> str:
        if row.user_id is not None:
            return email or str(row.user_id)
        return "anon"

    def _read(self, row: ShellSession, *, email: str | None = None) -> ShellSessionRead:
        data = ShellSessionRead.model_validate(row)
        data.principal_label = self._label(row, email=email)
        return data

    async def get_active(
        self,
        *,
        user_id: UUID | None,
        anon_id: UUID | None,
        now: datetime | None = None,
    ) -> ShellSession | None:
        now = now or utcnow()
        cutoff = now - ACTIVE_WINDOW
        stmt = select(ShellSession).where(ShellSession.last_activity_at >= cutoff)
        if user_id is not None:
            stmt = stmt.where(ShellSession.user_id == user_id)
        elif anon_id is not None:
            stmt = stmt.where(ShellSession.anon_id == anon_id)
        else:
            return None
        stmt = stmt.order_by(col(ShellSession.last_activity_at).desc()).limit(1)
        result = await self._session.exec(stmt)
        return result.first()

    async def ensure_session(
        self,
        *,
        user_id: UUID | None,
        anon_id: UUID | None,
        cdn_base: str = "",
        channel: str = "lead",
        driver: str = DEFAULT_DRIVER,
        hook_on: bool = True,
        user_agent: str = "",
        principal_label: str = "",
    ) -> ShellSessionRead:
        if user_id is None and anon_id is None:
            raise ValueError("user_id or anon_id required")
        active = await self.get_active(user_id=user_id, anon_id=anon_id)
        now = utcnow()
        if active is not None:
            if cdn_base:
                active.cdn_base = cdn_base[:512]
            if channel:
                active.channel = channel[:64]
            if driver:
                active.driver = driver[:64]
            active.hook_on = hook_on
            if user_agent:
                active.user_agent = user_agent[:512]
            active.last_activity_at = now
            self._session.add(active)
            await self._session.commit()
            await self._session.refresh(active)
            return self._read(active, email=principal_label if "@" in principal_label else None)

        row = ShellSession(
            user_id=user_id,
            anon_id=anon_id if user_id is None else None,
            cdn_base=(cdn_base or "")[:512],
            channel=(channel or "lead")[:64],
            driver=(driver or DEFAULT_DRIVER)[:64],
            hook_on=hook_on,
            user_agent=(user_agent or "")[:512],
            created_at=now,
            last_activity_at=now,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return self._read(row, email=principal_label if "@" in principal_label else None)

    async def get_owned(self, session_id: UUID, *, user_id: UUID | None, anon_id: UUID | None) -> ShellSession | None:
        row = await self._session.get(ShellSession, session_id)
        if row is None:
            return None
        if user_id is not None and row.user_id == user_id:
            return row
        if anon_id is not None and row.anon_id == anon_id and row.user_id is None:
            return row
        return None

    async def list_mine(
        self,
        *,
        user_id: UUID | None,
        anon_id: UUID | None,
        limit: int = 40,
        principal_label: str = "",
    ) -> list[ShellSessionRead]:
        stmt = select(ShellSession).order_by(col(ShellSession.last_activity_at).desc()).limit(limit)
        if user_id is not None:
            stmt = stmt.where(ShellSession.user_id == user_id)
        elif anon_id is not None:
            stmt = stmt.where(ShellSession.anon_id == anon_id)
        else:
            return []
        result = await self._session.exec(stmt)
        email = principal_label if "@" in principal_label else None
        return [self._read(r, email=email) for r in result.all()]

    async def claim_anon(self, anon_id: UUID, user_id: UUID) -> int:
        """Attach anon shell sessions to a user; keep anon_id for history."""
        stmt = select(ShellSession).where(ShellSession.anon_id == anon_id)
        result = await self._session.exec(stmt)
        n = 0
        for row in result.all():
            if row.user_id is None:
                row.user_id = user_id
                self._session.add(row)
                n += 1
        if n:
            await self._session.commit()
        return n

    async def record_event(
        self,
        session_id: UUID,
        *,
        kind: str,
        path: str = "",
        package: str | None = None,
        touch: bool = True,
    ) -> ShellSessionEventRead:
        row = ShellSessionEvent(
            session_id=session_id,
            kind=(kind or "other")[:32],
            path=(path or "")[:512],
            package=(package[:128] if package else None),
        )
        self._session.add(row)
        if touch:
            sess = await self._session.get(ShellSession, session_id)
            if sess is not None:
                sess.last_activity_at = utcnow()
                self._session.add(sess)
        await self._session.commit()
        await self._session.refresh(row)
        return ShellSessionEventRead.model_validate(row)

    async def activity(
        self,
        session_id: UUID,
        *,
        window_minutes: int = 30,
    ) -> ShellActivityResponse:
        window_minutes = max(1, min(int(window_minutes), 180))
        now = utcnow()
        start = now - timedelta(minutes=window_minutes)
        stmt = (
            select(ShellSessionEvent)
            .where(ShellSessionEvent.session_id == session_id)
            .where(ShellSessionEvent.created_at >= start)
            .order_by(col(ShellSessionEvent.created_at).asc())
        )
        result = await self._session.exec(stmt)
        events = list(result.all())

        # Floor to UTC minute.
        counts: dict[datetime, int] = {}
        for ev in events:
            ts = ev.created_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            minute = ts.replace(second=0, microsecond=0)
            counts[minute] = counts.get(minute, 0) + 1

        buckets: list[ShellActivityBucket] = []
        cursor = start.replace(second=0, microsecond=0)
        end_minute = now.replace(second=0, microsecond=0)
        while cursor <= end_minute:
            buckets.append(ShellActivityBucket(minute=cursor, count=counts.get(cursor, 0)))
            cursor = cursor + timedelta(minutes=1)

        recent = [
            ShellSessionEventRead.model_validate(ev)
            for ev in reversed(events[-40:])
        ]
        return ShellActivityResponse(
            session_id=session_id,
            window_minutes=window_minutes,
            buckets=buckets,
            recent=recent,
        )

    async def classify_and_record_http(
        self,
        *,
        user_id: UUID | None,
        anon_id: UUID | None,
        method: str,
        path: str,
        status: int,
        path_prefix: str = "",
    ) -> None:
        """Attribute a successful GET under packs/index/autoexec to the active session."""
        if method.upper() != "GET" or status >= 400:
            return
        if user_id is None and anon_id is None:
            return
        rel = path
        prefix = path_prefix.rstrip("/")
        if prefix and rel.startswith(prefix):
            rel = rel[len(prefix) :] or "/"
        kind = _classify_path(rel)
        if kind is None:
            return
        active = await self.get_active(user_id=user_id, anon_id=anon_id)
        if active is None:
            # create-on-miss so early index probes still attach
            read = await self.ensure_session(
                user_id=user_id,
                anon_id=anon_id,
                hook_on=False,
                driver="",
            )
            session_id = read.id
        else:
            session_id = active.id
        pkg = _package_from_path(rel) if kind == "pack" else None
        await self.record_event(session_id, kind=kind, path=rel[:512], package=pkg)


def _classify_path(rel: str) -> str | None:
    p = rel.split("?", 1)[0]
    # autoexec is recorded explicitly when the shell boots (avoid double-count).
    if "/index/" in p or p.startswith("/index/"):
        return "index"
    if "/artifacts/" in p or p.startswith("/artifacts/"):
        return "pack"
    return None


def _package_from_path(rel: str) -> str | None:
    # …/artifacts/lead/hello.wasm.zlib → hello
    parts = rel.rstrip("/").split("/")
    if not parts:
        return None
    name = parts[-1]
    for suffix in (".wasm.zlib", ".elf.zlib", ".aot.zlib", ".wasm", ".elf", ".zlib"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    # strip arch.aotN / arch.elf infix: hello.x86_64.aot6 / hello.x86_64
    if ".aot" in name:
        name = name.split(".aot", 1)[0]
        if "." in name:
            name = name.rsplit(".", 1)[0]
    elif "." in name:
        # possible arch tag left after .elf strip already handled above
        pass
    return name[:128] if name else None
