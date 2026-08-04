"""Channel path helpers (lead vs @version pins)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_PIN_RE = re.compile(r"^@(?P<ver>[0-9A-Za-z][0-9A-Za-z._+-]*)$")
# Python dotted ``test_a.test_b.test_c``, flat ``hello``, or legacy ``org/pkg``.
_SEG = r"[A-Za-z_][A-Za-z0-9_]*"
_PKG_RE = re.compile(rf"^{_SEG}([.]{_SEG})*$|^{_SEG}/{_SEG}$")


@dataclass(frozen=True, slots=True)
class ChannelRef:
    """Logical channel id: ``lead`` or ``@0.1.0``."""

    name: str

    @property
    def is_lead(self) -> bool:
        return self.name == "lead"

    @property
    def pin_version(self) -> str | None:
        match = _PIN_RE.match(self.name)
        return match.group("ver") if match else None

    def index_key(self) -> str:
        if self.is_lead:
            return "index.json"
        return f"{self.name}/index.json"

    def artifact_key(self, filename: str) -> str:
        filename = filename.lstrip("/")
        if "/" in filename or filename in (".", ".."):
            raise ValueError(f"invalid artifact filename: {filename}")
        if self.is_lead:
            return filename
        return f"{self.name}/{filename}"


class ChannelLayout:
    """Pure helpers for channel / package naming."""

    @staticmethod
    def lead() -> ChannelRef:
        return ChannelRef("lead")

    @staticmethod
    def pin(version: str) -> ChannelRef:
        version = version.lstrip("@")
        ref = ChannelRef(f"@{version}")
        if ref.pin_version is None:
            raise ValueError(f"invalid pin version: {version}")
        return ref

    @staticmethod
    def validate_package_name(name: str) -> str:
        if not _PKG_RE.match(name):
            raise ValueError(f"invalid package name: {name}")
        return name

    @staticmethod
    def classify_artifact(filename: str) -> tuple[str, str | None, int | None, str]:
        """Return (kind, arch, aot_version, encoding) from a filename.

        kind: wasm|aot ; encoding: raw|mpzl
        """
        name = filename
        encoding = "mpzl" if name.endswith(".zlib") else "raw"
        if encoding == "mpzl":
            name = name[: -len(".zlib")]

        aot_version: int | None = None
        arch: str | None = None
        kind = "wasm"

        if name.endswith(".wasm"):
            kind = "wasm"
        else:
            # hello.aot6 / hello.x86_64.aot6 / hello.aot
            m = re.match(
                r"^(?P<stem>.+?)(?:\.(?P<arch>[A-Za-z0-9_]+))?\.aot(?P<ver>\d*)$",
                name,
            )
            if not m:
                raise ValueError(f"unrecognized artifact name: {filename}")
            kind = "aot"
            arch = m.group("arch")
            ver = m.group("ver")
            aot_version = int(ver) if ver else None
        return kind, arch, aot_version, encoding
