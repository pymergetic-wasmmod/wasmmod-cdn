"""Human-readable size + hex dump helpers (CLI + shared)."""

from __future__ import annotations


def human_size(n: float | None, *, binary: bool = True) -> str:
    """Format byte counts as ``9.5 KiB (9755 B)`` (or just ``512 B`` under 1 KiB)."""
    if n is None:
        return "?"
    try:
        nbytes = int(n)
    except (TypeError, ValueError):
        return "?"
    if nbytes < 0:
        return "?"
    exact = f"{nbytes} B"
    base = 1024 if binary else 1000
    if nbytes < base:
        return exact
    units = ("KiB", "MiB", "GiB", "TiB") if binary else ("KB", "MB", "GB", "TB")
    value = float(nbytes)
    unit = units[0]
    for unit in units:
        value /= base
        if value < base:
            break
    if value >= 100:
        pretty = f"{value:.0f}"
    elif value >= 10:
        pretty = f"{value:.1f}"
    else:
        pretty = f"{value:.2f}"
    return f"{pretty} {unit} ({exact})"


def hexdump(
    data: bytes,
    *,
    width: int = 16,
    limit: int | None = None,
    color: bool = False,
    offset: int = 0,
) -> str:
    """xxd-style dump. When ``color``, ANSI-tint null / printable / high bytes."""
    blob = data if limit is None else data[:limit]
    reset = "\033[0m" if color else ""
    c_null = "\033[90m" if color else ""
    c_print = "\033[36m" if color else ""
    c_hi = "\033[33m" if color else ""

    def paint(b: int) -> str:
        hx = f"{b:02x}"
        if not color:
            return hx
        if b == 0:
            return f"{c_null}{hx}{reset}"
        if 32 <= b < 127:
            return f"{c_print}{hx}{reset}"
        return f"{c_hi}{hx}{reset}"

    lines: list[str] = []
    for i in range(0, len(blob), width):
        chunk = blob[i : i + width]
        hex_part = " ".join(paint(b) for b in chunk)
        hex_part = hex_part.ljust(width * 3 - 1)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset + i:08x}  {hex_part}  |{ascii_part}|")
    if limit is not None and len(data) > limit:
        lines.append(f"… showing {limit} of {len(data)} bytes")
    return "\n".join(lines)
