"""Application settings (pydantic-settings)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import EmailStr, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pymergetic.metal.cdn.paths import normalize_base_path, path_prefix
from pymergetic.metal.cdn_client.verify import RequireSignedMode


class Settings(BaseSettings):
    """Runtime configuration for metal-cdn."""

    model_config = SettingsConfigDict(
        env_prefix="METAL_CDN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "metal-cdn"
    debug: bool = False
    data_dir: Path = Field(default=Path(".data"))
    storage_root: Path = Field(default=Path(".data/packs"))
    database_url: str = Field(
        default="sqlite+aiosqlite:///.data/metal_cdn.db",
        description="Async SQLAlchemy URL (sqlite+aiosqlite or postgresql+asyncpg)",
    )
    host: str = "0.0.0.0"
    port: int = 8000
    base_path: str = Field(
        default="/",
        description=(
            "URL prefix for the whole app so a reverse proxy can split hosts by "
            "subroute later. Use '/' or '/cdn' (no trailing slash except root)."
        ),
    )
    public_origin: str | None = Field(
        default=None,
        description="Optional public origin for absolute URLs, e.g. https://cdn.example.com",
    )
    behind_proxy: bool = Field(
        default=True,
        description="Trust X-Forwarded-* from the TLS-terminating proxy",
    )
    root_path: str = Field(
        default="",
        description=(
            "ASGI root_path when the reverse proxy strips base_path before "
            "forwarding. Leave empty when nginx proxies /cdn/ → app /cdn/."
        ),
    )
    trusted_hosts: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Allowed Host headers; ['*'] disables TrustedHostMiddleware",
    )
    session_secret: str = Field(
        default="dev-change-me-metal-cdn",
        description="Secret for signed session cookies",
    )
    session_https_only: bool | None = Field(
        default=None,
        description=(
            "Set Secure on the session cookie. None = auto (true when "
            "public_origin is an https:// URL). Tests should leave this None "
            "and avoid inheriting a production public_origin over http:// clients."
        ),
    )
    require_auth: bool = Field(
        default=False,
        description="When true, mutating routes require a session or API key",
    )
    allow_open_registration: bool | None = Field(
        default=None,
        description=(
            "Allow POST /auth/register. Default: True when require_auth is False, "
            "False when require_auth is True (prod-safe)."
        ),
    )
    bootstrap_admin_email: EmailStr | None = None
    bootstrap_admin_password: str | None = Field(default=None, min_length=8)
    auto_claim_on_publish: bool = Field(
        default=True,
        description="If authenticated and package unclaimed, claim as owner on publish",
    )
    pin_immutable: bool = Field(
        default=True,
        description="Reject republish that would overwrite an existing pin package entry",
    )
    csrf_enabled: bool = Field(default=True, description="Enforce CSRF for cookie sessions")
    rate_limit_enabled: bool = True
    rate_limit_login: int = Field(default=20, ge=1)
    rate_limit_publish: int = Field(default=60, ge=1)
    rate_limit_window_s: float = Field(default=60.0, gt=0)
    artifact_cache_lead_s: int = Field(default=60, ge=0)
    artifact_cache_pin_s: int = Field(default=86400, ge=0)

    # Storage backend: local filesystem or S3/MinIO.
    storage_backend: str = Field(default="local", description="local | s3 | minio")
    s3_endpoint: str | None = Field(default=None, description="e.g. http://127.0.0.1:9000")
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"
    s3_presign_expires_s: int = Field(default=3600, ge=60)

    # Optional HMAC key for signing index.json (hex or raw utf-8 secret).
    index_signing_key: str | None = None

    # Publish-time signature policy for uploaded artifacts.
    # off | present (must have wasmmod.sig) | verify (chain+ECDSA vs trust_roots)
    require_signed: RequireSignedMode = Field(
        default="off",
        description="off | present | verify — MPWS gate on POST /publish",
    )

    # Ops
    json_logs: bool = Field(default=False, description="Emit JSON structured logs")
    metrics_enabled: bool = Field(default=True, description="Expose GET /metrics")

    # Pre-live banner: warn that pack/index data may be wiped.
    experimental: bool = Field(
        default=True,
        description=(
            "When true, advertise an experimental/pre-live warning via API, UI, and CLIs "
            "(METAL_CDN_EXPERIMENTAL=0 to disable after go-live)"
        ),
    )
    experimental_message: str = Field(
        default=(
            "Experimental CDN: data will be wiped — often. "
            "Short tests only; do not run weekend-long experiments against it. "
            "Not for production."
        ),
        description="User-facing warning text when experimental=true",
    )
    experimental_repl: bool = Field(
        default=True,
        description=(
            "Show the minimizable MicroPython shell and package Try buttons "
            "(needs static/repl/micropython.mjs from ports/webassembly)"
        ),
    )

    @field_validator("base_path")
    @classmethod
    def _base_path_ok(cls, value: str) -> str:
        return normalize_base_path(value)

    @field_validator("require_signed")
    @classmethod
    def _require_signed_ok(cls, value: str) -> RequireSignedMode:
        v = (value or "off").strip().lower()
        if v not in ("off", "present", "verify"):
            raise ValueError("require_signed must be off|present|verify")
        return cast(RequireSignedMode, v)

    @model_validator(mode="after")
    def _registration_default(self) -> Settings:
        if self.allow_open_registration is None:
            self.allow_open_registration = not self.require_auth
        return self

    @property
    def path_prefix(self) -> str:
        return path_prefix(self.base_path)

    @property
    def registration_open(self) -> bool:
        return bool(self.allow_open_registration)

    @property
    def session_cookie_secure(self) -> bool:
        if self.session_https_only is not None:
            return self.session_https_only
        return bool(self.public_origin and self.public_origin.startswith("https://"))

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.storage_root.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite"):
            raw = self.database_url.split("///", 1)[-1]
            if raw and not raw.startswith(":memory:"):
                Path(raw).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()
