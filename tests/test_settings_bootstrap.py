"""Settings bootstrap email must survive pydantic EmailStr (no .local/.test)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pymergetic.wasmmod.cdn.settings import Settings

# Keep in sync with scripts/ensure-secrets.sh DEFAULT_EMAIL.
DEFAULT_BOOTSTRAP_EMAIL = "demo@cdn.pymergetic.com"


def test_bootstrap_admin_email_accepts_ensure_secrets_default(tmp_path: Path) -> None:
    s = Settings(
        data_dir=tmp_path / "data",
        storage_root=tmp_path / "packs",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'x.db'}",
        bootstrap_admin_email=DEFAULT_BOOTSTRAP_EMAIL,
        bootstrap_admin_password="x" * 16,
        session_secret="test-secret",
    )
    assert str(s.bootstrap_admin_email) == DEFAULT_BOOTSTRAP_EMAIL


@pytest.mark.parametrize(
    "bad",
    [
        "demo@cdn.pymergetic.local",
        "demo@cdn.pymergetic.test",
        "demo@localhost",
    ],
)
def test_bootstrap_admin_email_rejects_reserved_tlds(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValidationError) as ei:
        Settings(
            data_dir=tmp_path / "data",
            storage_root=tmp_path / "packs",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'x.db'}",
            bootstrap_admin_email=bad,  # type: ignore[arg-type]
            bootstrap_admin_password="x" * 16,
            session_secret="test-secret",
        )
    assert "bootstrap_admin_email" in str(ei.value)
