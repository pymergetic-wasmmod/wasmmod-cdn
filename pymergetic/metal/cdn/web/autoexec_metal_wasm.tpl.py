"""Browser session epilogue (post-ready).

C already ran live boot + bound the baked home CDN (+ extras).
This script ADDs the session CDN (prepend) — never replaces home —
then install_hook + helpers. Matches DHCP opt-224 add semantics.
"""

CDN = "__TPL_CDN__"
CHANNEL = "__TPL_CHANNEL__"
INDEX_URL = "__TPL_INDEX_URL__"
BROWSE_URL = "__TPL_BROWSE_URL__"
SERVER_VERSION = "__TPL_SERVER_VERSION__"
SESSION_ID = "__TPL_SESSION_ID__"
PRINCIPAL = "__TPL_PRINCIPAL__"
_DRIVER_HINT = "__TPL_DRIVER_HINT__"
_LEAD_PACKAGES = "__TPL_LEAD_PACKAGES__"
_PACKAGES_SRC = "snapshot"


def _cdn_configured():
    return isinstance(CDN, str) and CDN and not CDN.startswith("__TPL_")


def run():
    global _LEAD_PACKAGES, _PACKAGES_SRC

    if not _cdn_configured():
        return True

    try:
        import pymergetic.wasmmod as wmod
    except ImportError as e:
        print("wasmmod missing —", e)
        return False

    # Session shelf ADDs (prepend). Home stays from C pm_metal_cdn_bind().
    prepend = getattr(wmod, "cdn_prepend", None)
    if callable(prepend):
        prepend(CDN)
    else:
        # Old seat without cdn_prepend — last resort replace (lab only).
        wmod.cdn(CDN)
    wmod.install_hook()
    if SESSION_ID and not str(SESSION_ID).startswith("__TPL_"):
        try:
            wmod.session_id(SESSION_ID)
        except Exception:
            pass
    try:
        cat = getattr(wmod, "catalog", None)
        if callable(cat):
            _LEAD_PACKAGES = list(cat(channel=CHANNEL))
            _PACKAGES_SRC = "live"
    except Exception:
        pass
    n = len(_LEAD_PACKAGES) if isinstance(_LEAD_PACKAGES, list) else 0
    print("cdn packs: %d on %s (%s)" % (n, CHANNEL, _PACKAGES_SRC))
    print("import a guest pack, or packages() | help()")
    return True


def packages(limit=40, refresh=False):
    global _LEAD_PACKAGES, _PACKAGES_SRC
    names = _LEAD_PACKAGES if isinstance(_LEAD_PACKAGES, list) else []
    if refresh and _cdn_configured():
        try:
            import pymergetic.wasmmod as wmod

            cat = getattr(wmod, "catalog", None)
            if callable(cat):
                names = list(cat(channel=CHANNEL))
                _LEAD_PACKAGES = names
                _PACKAGES_SRC = "live"
        except Exception as e:
            print("catalog refresh failed:", e)
    for i, n in enumerate(names[:limit]):
        print(n)
    if len(names) > limit:
        print("… %d more" % (len(names) - limit))
    return names


def help():
    print("metal — CDN guests after ready")
    print("  packages()  list lead packs")
    print("  import <guest>")
