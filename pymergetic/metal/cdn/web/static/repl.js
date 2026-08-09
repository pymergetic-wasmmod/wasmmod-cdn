/**
 * metal-cdn browser MicroPython shell — one panel, N engine *instances*.
 *
 * Engines: mp | mpwm | upy (vanilla) — each keeps its own runtime + transcript.
 * Pill click = focus that instance (others stay warm).
 */
(() => {
  const panel = document.getElementById("mpy-repl");
  if (!panel) return;

  const termEl = document.getElementById("mpy-repl-term");
  const inEl = document.getElementById("mpy-repl-in");
  const toggleBtn = document.getElementById("mpy-repl-toggle");
  const statusEl = document.getElementById("mpy-repl-status");
  const heapSel = document.getElementById("mpy-repl-heap");
  const basePath = panel.dataset.basePath || "";
  const assetV = panel.dataset.replAssetV || "";
  const autoexecUrl = panel.dataset.autoexecUrl || (basePath + "/repl/autoexec.py");
  let engineId = panel.dataset.replEngine || "mp";

  const HEAP_PRESETS = [4194304, 8388608, 16777216, 33554432, 67108864, 134217728];
  const HEAP_DEFAULT = 4194304;
  const HEAP_STORE_PREFIX = "metal-cdn.mpy.heapsize.";

  /** @type {Map<string, EngineInst>} */
  const engines = new Map();

  /**
   * @typedef {{
   *   id: string,
   *   mjsUrl: string,
   *   outEl: HTMLElement,
   *   mp: any,
   *   loading: Promise<any>|null,
   *   sessionReady: boolean,
   *   sessionLoading: Promise<void>|null,
   *   shellSessionId: string|null,
   *   firstOpenWait: boolean,
   *   status: string,
   *   heapsize: number,
   * }} EngineInst
   */

  function readHeapPref(id) {
    try {
      const raw = localStorage.getItem(HEAP_STORE_PREFIX + id);
      const n = raw != null ? parseInt(raw, 10) : NaN;
      if (HEAP_PRESETS.indexOf(n) >= 0) return n;
    } catch (_) {
      /* private mode */
    }
    return HEAP_DEFAULT;
  }

  function writeHeapPref(id, bytes) {
    try {
      localStorage.setItem(HEAP_STORE_PREFIX + id, String(bytes));
    } catch (_) {
      /* ignore */
    }
  }

  function syncHeapSelect(id) {
    if (!heapSel) return;
    const e = engines.get(id);
    const v = e ? e.heapsize : readHeapPref(id);
    heapSel.value = String(v);
  }

  function disposeEngine(e) {
    if (!e) return;
    e.mp = null;
    e.loading = null;
    e.sessionReady = false;
    e.sessionLoading = null;
    e.shellSessionId = null;
    e.firstOpenWait = false;
    if (e.outEl) e.outEl.textContent = "";
    e.status = "idle";
  }

  function bustUrl(url) {
    if (!assetV) return url;
    try {
      const u = new URL(url, window.location.href);
      u.searchParams.set("v", assetV);
      return u.href;
    } catch (_) {
      return url + (url.includes("?") ? "&" : "?") + "v=" + encodeURIComponent(assetV);
    }
  }

  function sessionAutoexecUrl() {
    try {
      const u = new URL(autoexecUrl, window.location.origin);
      const cdn = (panel.dataset.cdnBase || "").trim().replace(/\/$/, "");
      if (cdn) u.searchParams.set("cdn", cdn);
      u.searchParams.set("engine", engineId || "mp");
      return u.pathname + u.search + u.hash;
    } catch (_) {
      const sep = autoexecUrl.includes("?") ? "&" : "?";
      return autoexecUrl + sep + "engine=" + encodeURIComponent(engineId || "mp");
    }
  }

  function cur() {
    return engines.get(engineId) || null;
  }

  function ensureOutEl(id) {
    let el = panel.querySelector('[data-engine-out="' + id + '"]');
    if (el) return el;
    el = document.createElement("div");
    el.className = "mpy-repl-out";
    el.dataset.engineOut = id;
    el.hidden = true;
    el.setAttribute("aria-live", "polite");
    const line = termEl && termEl.querySelector(".mpy-repl-line");
    if (termEl && line) termEl.insertBefore(el, line);
    else if (termEl) termEl.appendChild(el);
    return el;
  }

  function getOrCreateEngine(id, mjsUrl) {
    let e = engines.get(id);
    if (e) {
      if (mjsUrl && !e.mjsUrl) e.mjsUrl = mjsUrl;
      return e;
    }
    e = {
      id,
      mjsUrl: mjsUrl || "",
      outEl: ensureOutEl(id),
      mp: null,
      loading: null,
      sessionReady: false,
      sessionLoading: null,
      shellSessionId: null,
      firstOpenWait: false,
      status: "idle",
      heapsize: readHeapPref(id),
    };
    engines.set(id, e);
    return e;
  }

  function scrollTerm() {
    const el = termEl;
    if (el) el.scrollTop = el.scrollHeight;
  }

  /** Minimal ANSI → HTML (SGR + truecolor) for boot tree / FIGlet. */
  function ansiToHtml(raw) {
    const s = String(raw ?? "");
    if (!/\x1b\[/.test(s)) {
      const t = document.createElement("span");
      t.textContent = s;
      return t;
    }
    const root = document.createElement("span");
    const re = /\x1b\[([\d;]*)m/g;
    let last = 0;
    let open = null;
    let m;
    const flushText = (text) => {
      if (!text) return;
      const node = document.createTextNode(text);
      (open || root).appendChild(node);
    };
    const close = () => {
      open = null;
    };
    const styleFrom = (codes) => {
      const parts = String(codes || "0").split(";").map((x) => parseInt(x, 10) || 0);
      let color = "";
      let opacity = "";
      let bold = false;
      for (let i = 0; i < parts.length; i++) {
        const c = parts[i];
        if (c === 0) {
          color = "";
          opacity = "";
          bold = false;
        } else if (c === 1) bold = true;
        else if (c === 2) opacity = "0.65";
        else if (c === 31) color = "#ff7b72";
        else if (c === 32) color = "#3fb950";
        else if (c === 33) color = "#d29922";
        else if (c === 35) color = "#d2a8ff";
        else if (c === 36) color = "#39c5cf";
        else if (c === 38 && parts[i + 1] === 2 && i + 4 < parts.length) {
          color = `rgb(${parts[i + 2]},${parts[i + 3]},${parts[i + 4]})`;
          i += 4;
        }
      }
      const st = [];
      if (color) st.push("color:" + color);
      if (opacity) st.push("opacity:" + opacity);
      if (bold) st.push("font-weight:600");
      return st.join(";");
    };
    while ((m = re.exec(s))) {
      flushText(s.slice(last, m.index));
      last = m.index + m[0].length;
      const codes = m[1];
      if (!codes || codes === "0") {
        close();
        continue;
      }
      close();
      open = document.createElement("span");
      const st = styleFrom(codes);
      if (st) open.setAttribute("style", st);
      root.appendChild(open);
    }
    flushText(s.slice(last));
    return root;
  }

  function append(line, cls, eng) {
    const e = eng || cur();
    if (!e || !e.outEl) return;
    const span = document.createElement("div");
    if (cls) span.className = cls;
    span.appendChild(ansiToHtml(line));
    e.outEl.appendChild(span);
    if (e.id === engineId) scrollTerm();
  }

  function setStatus(text, eng) {
    const e = eng || cur();
    if (e) e.status = text || "";
    if (!statusEl || (e && e.id !== engineId)) return;
    statusEl.textContent = text || "";
    statusEl.classList.toggle("is-ready", text === "ready");
    statusEl.classList.toggle("is-busy", /…$|\.\.\.$/.test(text || "") || text === "starting");
  }

  function focusInput() {
    if (inEl && !inEl.disabled) inEl.focus();
  }

  function panelOpen() {
    return panel.classList.contains("is-open");
  }

  function showEngineOut(id) {
    panel.querySelectorAll("[data-engine-out]").forEach((el) => {
      el.hidden = el.dataset.engineOut !== id;
    });
  }

  function markEngineActive(id) {
    panel.querySelectorAll("[data-mpy-engine]").forEach((btn) => {
      btn.classList.toggle("is-active", btn.getAttribute("data-mpy-engine") === id);
    });
    panel.dataset.replEngine = id;
  }

  function focusEngine(id, mjsHref) {
    const next = String(id || "").trim();
    if (!next) return;
    const href = String(mjsHref || "").trim();
    const e = getOrCreateEngine(next, href);
    if (href) e.mjsUrl = href;
    engineId = next;
    markEngineActive(next);
    showEngineOut(next);
    syncHeapSelect(next);
    setStatus(e.status || (e.sessionReady ? "ready" : "idle"), e);
    scrollTerm();
    // Boot this instance if needed; leave others alone.
    void ensureSession({ quiet: !panelOpen() }).catch(() => {});
  }

  function expand({ boot = true } = {}) {
    panel.classList.remove("is-mini");
    panel.classList.add("is-open");
    panel.setAttribute("aria-expanded", "true");
    focusInput();
    if (boot) {
      const e = cur();
      if (e && !e.sessionReady) {
        e.firstOpenWait = true;
        setStatus("starting…", e);
      }
      void ensureSession({ quiet: false }).catch(() => {});
    }
  }

  function collapse() {
    panel.classList.add("is-mini");
    panel.classList.remove("is-open");
    panel.setAttribute("aria-expanded", "false");
  }

  function toggle() {
    if (panel.classList.contains("is-mini")) expand();
    else collapse();
  }

  async function csrfToken() {
    const res = await fetch(basePath + "/auth/csrf", { credentials: "same-origin" });
    if (!res.ok) return "";
    const data = await res.json().catch(() => ({}));
    return data.csrf_token || "";
  }

  async function postSessionEvent(kind, { package: pkg = null, path = "" } = {}) {
    const e = cur();
    try {
      const token = await csrfToken();
      const headers = {
        Accept: "application/json",
        "Content-Type": "application/json",
      };
      if (token) headers["X-CSRF-Token"] = token;
      await fetch(basePath + "/api/sessions/events", {
        method: "POST",
        credentials: "same-origin",
        headers,
        body: JSON.stringify({
          kind,
          path,
          package: pkg,
          session_id: e && e.shellSessionId,
          engine: engineId,
        }),
      });
    } catch (_) {
      /* telemetry must not break the shell */
    }
  }

  function patchRunPythonAsyncify(runtime, eng) {
    const Module = runtime && runtime._module;
    if (!Module || typeof Module.ccall !== "function" || runtime.__metalAsyncify) return;
    const PROXY_KIND_MP_EXCEPTION = -1;
    runtime.runPythonAsync = (code) => {
      const src = String(code ?? "");
      const len = Module.lengthBytesUTF8(src);
      const buf = Module._malloc(len + 1);
      Module.stringToUTF8(src, buf, len + 1);
      const value = Module._malloc(3 * 4);
      return Module.ccall(
        "mp_js_do_exec_async",
        "number",
        ["pointer", "number", "pointer"],
        [buf, len, value],
        { async: true },
      ).then(() => {
        try {
          const kind = Module.getValue(value, "i32");
          if (kind === PROXY_KIND_MP_EXCEPTION) {
            const strLen = Module.getValue(value + 4, "i32");
            const strPtr = Module.getValue(value + 8, "i32");
            const raw = Module.UTF8ToString(strPtr, strLen);
            Module._free(strPtr);
            const parts = String(raw).split("\x04");
            const tb = (parts[1] || parts[0] || raw).replace(/\s+$/, "");
            tb.split("\n").forEach((ln) => append(ln, "mpy-err", eng));
          }
        } finally {
          Module._free(buf);
          Module._free(value);
        }
      });
    };
    runtime.__metalAsyncify = true;
  }

  async function ensureMp(eng) {
    const e = eng || cur();
    if (!e) throw new Error("no engine");
    if (e.mp) return e.mp;
    if (e.loading) return e.loading;
    if (!e.mjsUrl) throw new Error("engine " + e.id + " has no mjs");
    e.loading = (async () => {
      setStatus("loading…", e);
      const heap = e.heapsize || HEAP_DEFAULT;
      /* Bust module cache so a heap resize gets a fresh Emscripten Module. */
      let mjsImport = e.mjsUrl;
      try {
        const u = new URL(e.mjsUrl, window.location.href);
        u.searchParams.set("heap", String(heap));
        if (assetV) u.searchParams.set("v", assetV);
        mjsImport = u.href;
      } catch (_) {
        mjsImport =
          e.mjsUrl +
          (e.mjsUrl.includes("?") ? "&" : "?") +
          "heap=" +
          encodeURIComponent(String(heap));
      }
      const mod = await import(mjsImport);
      const loadMicroPython = mod.loadMicroPython || mod.default?.loadMicroPython;
      if (!loadMicroPython) throw new Error("loadMicroPython missing from micropython.mjs");
      const loadOpts = {
        heapsize: heap,
        stdout: (line) => append(String(line).replace(/\n$/, ""), "mpy-out", e),
        stderr: (line) => append(String(line).replace(/\n$/, ""), "mpy-err", e),
      };
      // Sibling .wasm of whatever .mjs we imported (CDN arch.wasm or static micropython.*).
      try {
        const u = new URL(e.mjsUrl, window.location.href);
        u.pathname = u.pathname.replace(/\.mjs$/i, ".wasm");
        u.searchParams.set("heap", String(heap));
        loadOpts.url = bustUrl(u.href);
      } catch (_) {
        loadOpts.url = bustUrl(
          String(e.mjsUrl).replace(/\.mjs(\?.*)?$/i, ".wasm$1"),
        );
      }
      e.mp = await loadMicroPython(loadOpts);
      patchRunPythonAsyncify(e.mp, e);
      if (!e.sessionReady) setStatus("warming…", e);
      return e.mp;
    })();
    try {
      return await e.loading;
    } catch (err) {
      e.loading = null;
      setStatus("unavailable", e);
      append("REPL load failed: " + (err && err.message ? err.message : err), "mpy-err", e);
      throw err;
    }
  }

  function withReplDisplay(src) {
    const text = String(src ?? "").replace(/\s+$/, "");
    if (!text) return text;
    if (text.includes("\n") || /^(import\s|from\s)/.test(text)) {
      return text;
    }
    // Bare calls (packages(), import-driven helpers): exec as a statement.
    // eval()+Asyncify(js.fetch) nests too deep → RuntimeError: unreachable.
    if (/^[A-Za-z_][\w.]*\(.*\)\s*$/.test(text)) {
      return text;
    }
    const quoted = JSON.stringify(text);
    return (
      "_g = globals()\n" +
      "try:\n" +
      "    _metal_v = eval(" +
      quoted +
      ", _g, _g)\n" +
      "except SyntaxError:\n" +
      "    exec(" +
      quoted +
      ", _g, _g)\n" +
      "else:\n" +
      "    if _metal_v is not None:\n" +
      "        print(repr(_metal_v))\n"
    );
  }

  async function run(code, { echo = true, bootstrap = false, quiet = false } = {}) {
    const e = cur();
    if (!e) return;
    if (!quiet) expand({ boot: false });
    if (!bootstrap) {
      await ensureSession({ quiet: false });
    } else {
      await ensureMp(e);
    }
    const runtime = await ensureMp(e);
    const text = String(code || "").replace(/\s+$/, "");
    if (!text) return;
    if (echo) {
      text.split("\n").forEach((ln) => append(">>> " + ln, "mpy-in", e));
    }
    const execText = bootstrap ? text : withReplDisplay(text);
    try {
      if (typeof runtime.runPythonAsync === "function") {
        await runtime.runPythonAsync(execText);
      } else {
        runtime.runPython(execText);
      }
    } catch (err) {
      const msg =
        err && err.name === "PythonError"
          ? String(err.message || err)
          : String(err && err.message ? err.message : err);
      String(msg)
        .replace(/\s+$/, "")
        .split("\n")
        .forEach((ln) => append(ln, "mpy-err", e));
    }
  }

  async function ensureSession({ quiet = false } = {}) {
    const e = cur();
    if (!e) return;
    if (e.sessionReady) return;
    if (e.sessionLoading) {
      if (!quiet && !panelOpen()) expand({ boot: false });
      return e.sessionLoading;
    }
    e.sessionLoading = (async () => {
      if (!quiet) expand({ boot: false });
      await ensureMp(e);
      setStatus(e.firstOpenWait || !quiet ? "starting…" : "warming…", e);
      const res = await fetch(sessionAutoexecUrl(), {
        credentials: "same-origin",
        headers: { Accept: "text/x-python,text/plain" },
      });
      if (!res.ok) throw new Error("autoexec HTTP " + res.status);
      e.shellSessionId = res.headers.get("X-Shell-Session-Id") || e.shellSessionId;
      const code = await res.text();
      await run(code, { echo: false, bootstrap: true, quiet });
      e.sessionReady = true;
      e.firstOpenWait = false;
      setStatus("ready", e);
    })();
    try {
      await e.sessionLoading;
    } catch (err) {
      e.sessionLoading = null;
      e.firstOpenWait = false;
      setStatus("session failed", e);
      append(
        "Session bootstrap failed: " + (err && err.message ? err.message : err),
        "mpy-err",
        e,
      );
      throw err;
    }
  }

  async function bootCdn() {
    await ensureSession({ quiet: false });
  }

  async function tryPackage(name) {
    const pkg = String(name || "").trim();
    if (!pkg) return;
    if (!/^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$/.test(pkg)) {
      append("Try: invalid package name " + JSON.stringify(pkg), "mpy-err");
      return;
    }
    if (
      pkg === "pymergetic.wasmmod" ||
      pkg === "pymergetic.upy" ||
      pkg === "pymergetic.metal" ||
      pkg === "pymergetic.metal.inspect" ||
      pkg.startsWith("pymergetic.metal.arch.")
    ) {
      append(
        pkg.startsWith("pymergetic.metal.arch.")
          ? "Try: " + pkg + " is an architecture image — use Inspect / iPXE, not Play."
          : "Try: " + pkg + " is a host/platform module — use Inspect, not Play.",
        "mpy-err",
      );
      return;
    }
    // Prefer mp/mpwm for pack try; vanilla upy can't import wasm packs.
    if (engineId === "upy") {
      const mpBtn = panel.querySelector('[data-mpy-engine="mp"]:not([disabled])');
      const wm = panel.querySelector('[data-mpy-engine="mpwm"]:not([disabled])');
      const btn = mpBtn || wm;
      if (btn) {
        focusEngine(btn.getAttribute("data-mpy-engine"), btn.getAttribute("data-mjs-href"));
      }
    }
    expand({ boot: false });
    const e = cur();
    if (e && !e.sessionReady) {
      e.firstOpenWait = true;
      setStatus("starting…", e);
    }
    await ensureSession({ quiet: false });
    void postSessionEvent("try_package", { package: pkg, path: "/try/" + pkg });
    await run("import " + pkg, { echo: true });
    await run("exports(" + pkg + ")", { echo: true });
  }

  if (toggleBtn) toggleBtn.addEventListener("click", () => toggle());

  if (heapSel) {
    heapSel.addEventListener("change", () => {
      const e = cur();
      if (!e) return;
      const n = parseInt(heapSel.value, 10);
      if (HEAP_PRESETS.indexOf(n) < 0) return;
      if (n === e.heapsize && !e.mp && !e.loading) {
        writeHeapPref(e.id, n);
        return;
      }
      e.heapsize = n;
      writeHeapPref(e.id, n);
      const wasOpen = panelOpen();
      disposeEngine(e);
      setStatus("restarting…", e);
      append(
        "heap " + e.id + " → " + n / (1024 * 1024) + " MiB (engine restart)",
        "mpy-out",
        e,
      );
      void ensureSession({ quiet: !wasOpen }).catch(() => {});
    });
  }

  // Register all engine pills as separate instances.
  panel.querySelectorAll("[data-mpy-engine]").forEach((btn) => {
    const id = btn.getAttribute("data-mpy-engine");
    const href = btn.getAttribute("data-mjs-href") || "";
    if (id && !btn.disabled && href) getOrCreateEngine(id, href);
    btn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (btn.disabled) return;
      focusEngine(id, href);
      if (panel.classList.contains("is-mini")) expand({ boot: true });
    });
  });

  // Seed default / legacy single-engine.
  const defHref =
    panel.dataset.mjsUrl ||
    (basePath + "/static/repl/micropython.mjs");
  getOrCreateEngine(engineId, defHref);
  markEngineActive(engineId);
  showEngineOut(engineId);
  syncHeapSelect(engineId);

  if (termEl) {
    termEl.addEventListener("click", (ev) => {
      if (ev.target === inEl) return;
      focusInput();
    });
  }

  if (inEl) {
    inEl.addEventListener("keydown", async (ev) => {
      if (ev.key !== "Enter" || ev.shiftKey) return;
      ev.preventDefault();
      const line = inEl.value;
      inEl.value = "";
      try {
        await run(line);
      } catch (_) {
        /* already printed */
      }
      focusInput();
    });
  }

  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-mpy-try]");
    if (!btn) return;
    ev.preventDefault();
    const name = btn.getAttribute("data-mpy-try");
    try {
      await tryPackage(name);
    } catch (err) {
      append("Try failed: " + (err && err.message ? err.message : err), "mpy-err");
    }
  });

  function startBackgroundWarm() {
    // Only warm the focused engine; others boot on first pill click (own instance).
    const kick = () => {
      ensureSession({ quiet: true }).catch(() => {});
    };
    if (typeof window.requestIdleCallback === "function") {
      window.requestIdleCallback(kick, { timeout: 1600 });
    } else {
      setTimeout(kick, 450);
    }
  }

  startBackgroundWarm();

  window.MetalRepl = {
    expand,
    collapse,
    toggle,
    run,
    tryPackage,
    bootCdn,
    ensureSession,
    ensureMp,
    focusEngine,
    switchEngine: focusEngine,
    get engine() {
      return engineId;
    },
  };
})();
