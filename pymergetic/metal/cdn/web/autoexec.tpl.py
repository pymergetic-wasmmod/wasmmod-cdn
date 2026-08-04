# metal-cdn REPL autoexec — session bootstrap (do not import packs here)
# Fetched from GET …/repl/autoexec.py
# Placeholders: quoted __TPL_NAME__ sentinels → JSON/Python literals via render_autoexec().
# pyright: ignore
# ruff: noqa

CDN = "__TPL_CDN__"
CHANNEL = "__TPL_CHANNEL__"
INDEX_URL = "__TPL_INDEX_URL__"
BROWSE_URL = "__TPL_BROWSE_URL__"
SERVER_VERSION = "__TPL_SERVER_VERSION__"
SESSION_ID = "__TPL_SESSION_ID__"
PRINCIPAL = "__TPL_PRINCIPAL__"
_DRIVER_HINT = "__TPL_DRIVER_HINT__"
_LEAD_PACKAGES = "__TPL_LEAD_PACKAGES__"

def _boot():
    import sys

    def _pad(s, n):
        # MicroPython str has no width-pad helpers — pad manually.
        s = str(s)
        if len(s) >= n:
            return s
        return s + (" " * (n - len(s)))

    def _row(branch, key, val=""):
        # branch: "├──" | "│   ├──" | "│   └──" | "└──"
        if val == "":
            print(branch, key)
        else:
            print(branch, _pad(key, 8), val)

    n_packs = len(_LEAD_PACKAGES)
    print("metal-cdn · MicroPython shell")
    _row("├──", "server", SERVER_VERSION)
    try:
        uv = ".".join(str(x) for x in sys.implementation.version)
    except Exception:
        uv = sys.version.split()[0]
    _row("├──", "runtime")
    _row("│   ├──", "upy", uv)
    try:
        wasm = __import__("wasm")  # µPy builtin; absent → ImportError (handled)
    except ImportError as e:
        _row("│   └──", "wasmmod", "missing — " + str(e))
        _row("├──", "session")
        _row("│   ├──", "id", SESSION_ID or "(none)")
        _row("│   └──", "principal", PRINCIPAL)
        _row("├──", "packs", str(n_packs) + " on " + CHANNEL + " (snapshot)")
        _row("└──", "ready", "stock REPL · no wasm · packages() still lists names")
        return False
    ver = getattr(wasm, "version", "?")
    aot = getattr(wasm, "AOT_VERSION", None)
    wm = str(ver)
    if aot is not None:
        wm = wm + "  AOT " + str(aot)
    wv = "?"
    try:
        fn = getattr(wasm, "wamr_version", None)
        if callable(fn):
            wv = fn()
    except Exception:
        pass
    _row("│   ├──", "wasmmod", wm)
    _row("│   └──", "wamr", wv)
    _row("├──", "session")
    _row("│   ├──", "id", SESSION_ID or "(none)")
    _row("│   └──", "principal", PRINCIPAL)
    _row("├──", "cdn")
    _row("│   ├──", "base", CDN)
    _row("│   ├──", "index", INDEX_URL)
    drv = wasm.cdn(CDN)
    wasm.install_hook()
    if SESSION_ID:
        try:
            wasm.session_id(SESSION_ID)
        except Exception:
            pass
    _row("│   └──", "driver", str(drv) + " · hook on")
    # Prefer live catalog count; fall back to autoexec snapshot.
    pack_n = n_packs
    pack_src = "snapshot"
    try:
        cat = getattr(wasm, "catalog", None)
        if callable(cat):
            live = cat(channel=CHANNEL)
            pack_n = len(live)
            pack_src = "live"
    except Exception:
        pass
    _row("├──", "packs", str(pack_n) + " on " + CHANNEL + " (" + pack_src + ")")
    if pack_n:
        _row("└──", "ready", "wasm ok · import a pack, or packages()  |  help()")
    else:
        _row("└──", "ready", "wasm ok · lead empty — publish packs or packages()")
    return True


def packages(limit=40):
    """List lead-channel package names (live catalog, else autoexec snapshot)."""
    names = None
    src = "snapshot"
    try:
        wasm = __import__("wasm")
        cat = getattr(wasm, "catalog", None)
        if callable(cat):
            names = cat(channel=CHANNEL)
            src = "live"
    except Exception:
        names = None
    if names is None:
        names = _LEAD_PACKAGES
        src = "snapshot"
    n = len(names)
    show = names[: max(0, int(limit))]
    for name in show:
        print(name)
    if n > len(show):
        print("…", n - len(show), "more (browse", BROWSE_URL + ")")
    elif n == 0:
        print("(no packages in index yet)")
    print("#", n, "package(s) · channel", CHANNEL, "·", src)


def exports(mod):
    """Print public names on a loaded pack/module (Try button / REPL)."""
    names = [n for n in dir(mod) if not n.startswith("_")]
    label = getattr(mod, "__name__", "?")
    print("loaded", label, "·", len(names), "public")
    for n in names:
        obj = getattr(mod, n)
        doc = getattr(obj, "__doc__", None)
        kind = type(obj).__name__
        if doc:
            line = str(doc).strip().split("\n", 1)[0][:72]
            print(" ", n, "(" + kind + "):", line)
        else:
            print(" ", n, "(" + kind + ")")


def help(topic=None):
    """REPL help. Topics: packages, import, cdn, index."""
    t = (topic or "").strip().lower() if topic else ""
    if t in ("", "help"):
        print("metal-cdn shell")
        print("├── packages()           list pack names (live catalog / snapshot)")
        print("├── exports(mod)         public attrs (+ docstring line)")
        print("├── help('import')       how to load a pack")
        print("├── help('cdn')          CDN / hook session")
        print("├── help('index')        HTTP index URLs")
        print("├── import hello         load pack from CDN")
        print("└── exports(hello)       inspect after import")
        return
    if t == "packages":
        print("packages(limit=40) prefers wasm.catalog() when online,")
        print("else the snapshot baked into autoexec. Browse UI:", BROWSE_URL)
        print("JSON index:", INDEX_URL)
        return
    if t == "import":
        print("After autoexec, the import hook is installed:")
        print("  import hello")
        print("  exports(hello)")
        print("  import test_a.test_b   # dotted FQN ok")
        print("  exports(test_a.test_b)")
        print("Packs resolve via wasm.cdn →", CDN)
        return
    if t in ("exports", "export"):
        print("exports(mod) — public names (no leading _), type, first docstring line.")
        print("  import hello; exports(hello)")
        return
    if t in ("cdn", "hook", "session"):
        print("SESSION_ID:", SESSION_ID or "(none)")
        print("principal:", PRINCIPAL)
        print("CDN base:", CDN)
        print("wasm.session_id:", end=" ")
        try:
            print(__import__("wasm").session_id())
        except Exception:
            print("(n/a)")
        print("Re-bind:  wasm.cdn(CDN); wasm.install_hook(); wasm.session_id(SESSION_ID)")
        print("Clear:    wasm.uninstall_hook()")
        return
    if t in ("index", "catalog"):
        print("Lead index JSON:", INDEX_URL)
        print("Browse UI:      ", BROWSE_URL)
        print("Pin index:      ", CDN + "/index/pin/<version>")
        print("Live names:     wasm.catalog()  or  packages()")
        return
    print("unknown topic", repr(topic), "— try help()")


_boot()
del _boot
