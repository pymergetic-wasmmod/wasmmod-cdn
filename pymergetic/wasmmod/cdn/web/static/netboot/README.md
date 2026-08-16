# Netboot downloads (CDN UI)

First-stage iPXE NBPs + `metal.ipxe` offered on arch seat **Netboot** rows.
The blobs in this directory are what the UI serves.

| File | Role |
|------|------|
| `undionly.kpxe` | BIOS NBP — HTTPS-capable iPXE |
| `ipxe.efi` | UEFI NBP — same |
| `metal.ipxe` | Chains `https://cdn.pymergetic.com/cdn/artifacts/lead/pymergetic.metal.arch.*` |

Do **not** link `boot.ipxe.org` here — those builds often lack pcbios HTTPS
and break CDN bootstrap.

There is no `extmod/metal/deploy/bootserver/` on the card tree (`metal.git`
**main**). Preview PXE is the CMake runtime
([`scripts/upload-pxe`](https://github.com/pymergetic/metal/blob/preview/scripts/upload-pxe)).
To refresh these NBPs, rebuild iPXE with HTTPS and replace the three files
here — do not clone a top-level `packages/metal`.
