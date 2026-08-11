# Netboot downloads (CDN UI)

First-stage iPXE NBPs + `metal.ipxe` offered on arch seat **Netboot** rows.

| File | Role |
|------|------|
| `undionly.kpxe` | BIOS NBP — **HTTPS-capable** Metal build (`bootserver/build-nbp.sh`) |
| `ipxe.efi` | UEFI NBP — same |
| `metal.ipxe` | Chains `https://cdn.pymergetic.com/cdn/artifacts/lead/pymergetic.metal.arch.*` |

Do **not** link `boot.ipxe.org` here — those builds often lack pcbios HTTPS and break CDN bootstrap.

Refresh from metalpython:

```bash
packages/metalpython/extmod/metal/deploy/bootserver/build-nbp.sh
# copies into this directory
```
