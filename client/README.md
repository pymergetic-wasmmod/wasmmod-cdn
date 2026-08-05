# pymergetic-metal-cdn-client

Thin HTTP client for [metal-cdn](https://github.com/pymergetic/metal-cdn)
(urllib + Pydantic models for typed package ``contents``).

PyPI: **`pymergetic-metal-cdn-client`**  
Import: **`pymergetic.metal.cdn_client`**

Use this from wasmmod / CI. Do **not** install `pymergetic-metal-cdn` just to publish — that pulls the FastAPI server stack.

## Install

```sh
# alphas need --pre until a stable release
pip install --pre pymergetic-metal-cdn-client
# from this monorepo (sources under ../pymergetic/metal/cdn_client):
pip install --pre 'pymergetic-wasmmod-tools>=0.1.0a1'
pip install -e ./client --config-settings editable_mode=compat
```

## Auth contract

All authenticated calls use `Authorization: Bearer <token>`.

Today the token is an API key from `POST /auth/token` (password login).  
OIDC / passkeys are **not implemented** in this client; when the server adds them they will mint or refresh a Bearer token. Config reserves `token_source` (`api_key` now, `oidc` later). Publish/claim stay token-agnostic.

Config file: `~/.config/metal-cdn/config.json`

```json
{
  "url": "http://127.0.0.1:8000/cdn",
  "token": "mcdn_…",
  "email": "you@example.com",
  "token_source": "api_key"
}
```

## Usage

```python
from pathlib import Path
from pymergetic.metal.cdn_client import CdnClient, save_config

client = CdnClient("http://127.0.0.1:8000/cdn")
created = client.create_api_key_with_password("you@example.com", "secret", name="cli")
save_config({
    "url": "http://127.0.0.1:8000/cdn",
    "token": created["key"],
    "email": "you@example.com",
})

authed = CdnClient.from_config()
authed.claim("hello")
authed.publish(
    package="hello",
    version="0.1.0",
    files=[Path("hello.wasm")],
)
entry = authed.get_package("hello")
blob = authed.download_artifact("hello.wasm")
```

## wasmmod

wasmmod owns pack → AOT → sign → zlib. This library is only the upload/download step:

`pack → aot → sign → zlib → CdnClient.publish(...)`
