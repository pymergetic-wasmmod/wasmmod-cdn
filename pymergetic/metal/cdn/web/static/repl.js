/**
 * metal-cdn browser MicroPython shell.
 * Expects static/repl/micropython.mjs (+ .wasm) from ports/webassembly.
 *
 * Warm-up: load µPy + autoexec in the background shortly after page load
 * (panel stays collapsed). First open/try waits if still starting; after that
 * the shell is instant and status shows "ready".
 */
(() => {
  const panel = document.getElementById("mpy-repl");
  if (!panel) return;

  const outEl = document.getElementById("mpy-repl-out");
  const inEl = document.getElementById("mpy-repl-in");
  const termEl = document.getElementById("mpy-repl-term");
  const toggleBtn = document.getElementById("mpy-repl-toggle");
  const statusEl = document.getElementById("mpy-repl-status");
  const basePath = panel.dataset.basePath || "";
  const mjsUrl = panel.dataset.mjsUrl || (basePath + "/static/repl/micropython.mjs");
  const autoexecUrl = panel.dataset.autoexecUrl || (basePath + "/repl/autoexec.py");

  let mp = null;
  let loading = null;
  let sessionReady = false;
  let sessionLoading = null;
  let shellSessionId = null;
  let firstOpenWait = false;

  /** autoexec URL with optional ?cdn= from data-cdn-base (set by base.html). */
  function sessionAutoexecUrl() {
    const cdn = (panel.dataset.cdnBase || "").trim().replace(/\/$/, "");
    if (!cdn) return autoexecUrl;
    try {
      const u = new URL(autoexecUrl, window.location.origin);
      u.searchParams.set("cdn", cdn);
      return u.pathname + u.search + u.hash;
    } catch (_) {
      return autoexecUrl;
    }
  }

  function scrollTerm() {
    const el = termEl || outEl;
    if (el) el.scrollTop = el.scrollHeight;
  }

  function append(line, cls) {
    if (!outEl) return;
    const span = document.createElement("div");
    if (cls) span.className = cls;
    span.textContent = line;
    outEl.appendChild(span);
    scrollTerm();
  }

  function setStatus(text) {
    if (!statusEl) return;
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

  function expand({ boot = true } = {}) {
    panel.classList.remove("is-mini");
    panel.classList.add("is-open");
    panel.setAttribute("aria-expanded", "true");
    focusInput();
    if (boot) {
      if (!sessionReady) {
        firstOpenWait = true;
        setStatus("starting…");
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
          session_id: shellSessionId,
        }),
      });
    } catch (_) {
      /* telemetry must not break the shell */
    }
  }

  async function ensureMp() {
    if (mp) return mp;
    if (loading) return loading;
    loading = (async () => {
      setStatus("loading…");
      const mod = await import(mjsUrl);
      const loadMicroPython = mod.loadMicroPython || mod.default?.loadMicroPython;
      if (!loadMicroPython) throw new Error("loadMicroPython missing from micropython.mjs");
      mp = await loadMicroPython({
        stdout: (line) => append(String(line).replace(/\n$/, ""), "mpy-out"),
        stderr: (line) => append(String(line).replace(/\n$/, ""), "mpy-err"),
      });
      // Stock µPy runPythonAsync omits ccall { async: true }; wasmmod js.fetch needs it.
      // Keep metalpython api.js untouched — wrap here via mp._module.
      patchRunPythonAsyncify(mp);
      if (!sessionReady) setStatus("warming…");
      return mp;
    })();
    try {
      return await loading;
    } catch (err) {
      loading = null;
      setStatus("unavailable");
      append("REPL load failed: " + (err && err.message ? err.message : err), "mpy-err");
      throw err;
    }
  }

  function patchRunPythonAsyncify(runtime) {
    const Module = runtime && runtime._module;
    if (!Module || typeof Module.ccall !== "function" || runtime.__metalAsyncify) return;
    // Same as ports/webassembly/proxy_js.js — exception payload in *out.
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
          // Stock api.js converts *value and throws PythonError; we print into the term.
          const kind = Module.getValue(value, "i32");
          if (kind === PROXY_KIND_MP_EXCEPTION) {
            const strLen = Module.getValue(value + 4, "i32");
            const strPtr = Module.getValue(value + 8, "i32");
            const raw = Module.UTF8ToString(strPtr, strLen);
            Module._free(strPtr);
            const parts = String(raw).split("\x04");
            const tb = (parts[1] || parts[0] || raw).replace(/\s+$/, "");
            tb.split("\n").forEach((ln) => append(ln, "mpy-err"));
          }
        } finally {
          Module._free(buf);
          Module._free(value);
        }
      });
    };
    runtime.__metalAsyncify = true;
  }

  async function run(code, { echo = true, bootstrap = false, quiet = false } = {}) {
    if (!quiet) expand({ boot: false });
    if (!bootstrap) {
      await ensureSession({ quiet: false });
    } else {
      await ensureMp();
    }
    const runtime = await ensureMp();
    const text = String(code || "").replace(/\s+$/, "");
    if (!text) return;
    if (echo) {
      text.split("\n").forEach((ln) => append(">>> " + ln, "mpy-in"));
    }
    try {
      if (typeof runtime.runPythonAsync === "function") {
        await runtime.runPythonAsync(text);
      } else {
        runtime.runPython(text);
      }
    } catch (err) {
      const msg =
        err && err.name === "PythonError"
          ? String(err.message || err)
          : String(err && err.message ? err.message : err);
      String(msg)
        .replace(/\s+$/, "")
        .split("\n")
        .forEach((ln) => append(ln, "mpy-err"));
    }
  }

  /**
   * Fetch server autoexec.py once: wasm.cdn + hook + intro (no pack load).
   * @param {{ quiet?: boolean }} opts quiet=true keeps the panel collapsed (background warm).
   */
  async function ensureSession({ quiet = false } = {}) {
    if (sessionReady) return;
    if (sessionLoading) {
      if (!quiet && !panelOpen()) expand({ boot: false });
      return sessionLoading;
    }
    sessionLoading = (async () => {
      if (!quiet) expand({ boot: false });
      await ensureMp();
      setStatus(firstOpenWait || !quiet ? "starting…" : "warming…");
      const res = await fetch(sessionAutoexecUrl(), {
        credentials: "same-origin",
        headers: { Accept: "text/x-python,text/plain" },
      });
      if (!res.ok) throw new Error("autoexec HTTP " + res.status);
      shellSessionId = res.headers.get("X-Shell-Session-Id") || shellSessionId;
      const code = await res.text();
      await run(code, { echo: false, bootstrap: true, quiet });
      sessionReady = true;
      firstOpenWait = false;
      setStatus("ready");
    })();
    try {
      await sessionLoading;
    } catch (err) {
      sessionLoading = null;
      firstOpenWait = false;
      setStatus("session failed");
      append(
        "Session bootstrap failed: " + (err && err.message ? err.message : err),
        "mpy-err",
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
    expand({ boot: false });
    if (!sessionReady) {
      firstOpenWait = true;
      setStatus("starting…");
    }
    await ensureSession({ quiet: false });
    void postSessionEvent("try_package", { package: pkg, path: "/try/" + pkg });
    const alias = "m";
    await run(
      [
        "try:",
        "  import wasm",
        "except ImportError as e:",
        "  print('Try needs wasmmod in the browser host:', e)",
        "else:",
        "  import " + pkg + " as " + alias,
        "  print(dir(" + alias + "))",
      ].join("\n"),
      { echo: true }
    );
  }

  if (toggleBtn) toggleBtn.addEventListener("click", () => toggle());

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

  /** Background warm: load µPy + autoexec without opening the panel. */
  function startBackgroundWarm() {
    const kick = () => {
      ensureSession({ quiet: true }).catch(() => {
        /* status already set */
      });
    };
    // Short delay so first paint isn't competing with wasm download.
    const delayMs = 450;
    if (typeof window.requestIdleCallback === "function") {
      window.requestIdleCallback(kick, { timeout: delayMs + 1200 });
    } else {
      setTimeout(kick, delayMs);
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
  };
})();
