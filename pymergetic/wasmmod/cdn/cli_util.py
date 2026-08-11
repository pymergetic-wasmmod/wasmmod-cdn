"""Shared CLI helpers."""

from __future__ import annotations

from pymergetic.wasmmod.cdn_client import CdnClient, ClientError, report, report_client_error

PROG = "wasmmod-cdn"


def fail(exc: ClientError, *, force: bool = False) -> int:
    return report_client_error(exc, prog=PROG, force=force)


def warn_experimental(client: CdnClient) -> None:
    try:
        status = client.status()
    except ClientError:
        return
    if not status.get("experimental"):
        return
    msg = status.get("experimental_message") or (
        "Experimental CDN: data will be wiped — often. "
        "Short tests only; do not run weekend-long experiments against it. Not for production."
    )
    report(PROG, msg, "Set WASMMOD_CDN_EXPERIMENTAL=false after go-live")


def client_from_config() -> CdnClient:
    return CdnClient.from_config()
