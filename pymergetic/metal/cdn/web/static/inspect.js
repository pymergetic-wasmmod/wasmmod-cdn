/**
 * metal-cdn Inspect commander — dual-pane symbols / hex|asm|source.
 *
 * Site-wide: window.openInspect({
 *   filename, version?, package?, symbol?, addr?, sectionIndex?, mpyPath?, tab?
 * })
 */
(() => {
  const HEX_PREVIEW = 65536;
  const DISASM_LIMIT = 64;
  const MPY_DISASM_LIMIT = 96;

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
    );
  }

  function fmtSize(n) {
    const bytes = Number(n);
    if (!Number.isFinite(bytes) || bytes < 0) return "?";
    const exact = Math.trunc(bytes) + " B";
    if (bytes < 1024) return exact;
    const units = ["KiB", "MiB", "GiB", "TiB"];
    let value = bytes;
    let unit = units[0];
    for (let i = 0; i < units.length; i++) {
      value /= 1024;
      unit = units[i];
      if (value < 1024) break;
    }
    const pretty =
      value >= 100 ? value.toFixed(0) : value >= 10 ? value.toFixed(1) : value.toFixed(2);
    return pretty + " " + unit + " (" + exact + ")";
  }

  function paintHexByte(b) {
    const hx = b.toString(16).padStart(2, "0");
    if (b === 0) return `<span class="hx-null">${hx}</span>`;
    if (b >= 32 && b < 127) return `<span class="hx-print">${hx}</span>`;
    return `<span class="hx-hi">${hx}</span>`;
  }

  function hexdumpHtml(buf, limit, baseOffset) {
    const cap = limit == null ? HEX_PREVIEW : limit;
    const base = Number(baseOffset) || 0;
    const view = buf.byteLength > cap ? buf.slice(0, cap) : buf;
    const u8 = new Uint8Array(view);
    const width = 16;
    const lines = [];
    for (let i = 0; i < u8.length; i += width) {
      const chunk = u8.subarray(i, i + width);
      const hex = Array.from(chunk, paintHexByte).join(" ");
      const pad = "   ".repeat(width - chunk.length);
      let ascii = "";
      for (const b of chunk) ascii += b >= 32 && b < 127 ? String.fromCharCode(b) : ".";
      const addr = (base + i).toString(16).padStart(8, "0");
      lines.push(
        `<span class="hx-off">${addr}</span>  ${hex}${pad}  |${esc(ascii)}|`
      );
    }
    let html = lines.join("\n");
    if (buf.byteLength > cap) {
      html += `\n… showing ${cap} of ${buf.byteLength} bytes`;
    }
    return html;
  }

  function pickDefaultSymbol(syms) {
    if (!syms || !syms.length) return null;
    const prefer = (s) => s.kind === "export" || s.kind === "func";
    return syms.find(prefer) || syms[0];
  }

  function cdnPrefix() {
    const repl = document.getElementById("mpy-repl");
    if (repl && repl.dataset.cdnBase) {
      try {
        return new URL(repl.dataset.cdnBase).pathname.replace(/\/$/, "") || "";
      } catch (_) {
        /* fall through */
      }
    }
    const base = (document.body && document.body.dataset.basePath) || "";
    if (base) return base.replace(/\/$/, "");
    const m = window.location.pathname.match(/^(\/cdn)(?=\/|$)/);
    return (m && m[1]) || "";
  }

  function artifactRoot(opts) {
    const pref = cdnPrefix();
    const file = encodeURIComponent(opts.filename);
    if (opts.version) {
      return `${pref}/artifacts/pin/${encodeURIComponent(opts.version)}/${file}`;
    }
    return `${pref}/artifacts/lead/${file}`;
  }

  /** @type {{ dialog: HTMLDialogElement, els: Record<string, HTMLElement>, state: object } | null} */
  let ui = null;

  function ensureUi() {
    if (ui) return ui;
    const dialog = document.createElement("dialog");
    dialog.id = "inspect-dialog";
    dialog.className = "source-dialog inspect-dialog";
    dialog.innerHTML = `
      <form method="dialog" class="source-dialog-head">
        <strong id="inspect-dialog-title">Inspect</strong>
        <div class="source-dialog-actions">
          <button type="submit" class="icon-btn" value="close" aria-label="Close">✕</button>
        </div>
      </form>
      <div class="inspect-commander">
        <aside class="inspect-left">
          <label class="inspect-search">
            <span class="visually-hidden">Filter symbols</span>
            <input type="search" id="inspect-sym-filter" placeholder="Filter symbols…" autocomplete="off" spellcheck="false" />
          </label>
          <ul id="inspect-sym-list" class="inspect-sym-list"></ul>
        </aside>
        <div class="inspect-right">
          <div class="inspect-tabs" role="tablist">
            <button type="button" class="inspect-tab is-active" data-tab="hex" role="tab" aria-selected="true">hex</button>
            <button type="button" class="inspect-tab" data-tab="asm" role="tab" aria-selected="false">asm</button>
            <button type="button" class="inspect-tab" data-tab="source" role="tab" aria-selected="false">source</button>
          </div>
          <div id="inspect-loc-bar" class="inspect-loc-bar" hidden></div>
          <div id="inspect-meta" class="source-dialog-meta muted"></div>
          <pre id="inspect-body" class="source-body hex-body"></pre>
        </div>
      </div>`;
    document.body.appendChild(dialog);

    const els = {
      title: dialog.querySelector("#inspect-dialog-title"),
      filter: dialog.querySelector("#inspect-sym-filter"),
      list: dialog.querySelector("#inspect-sym-list"),
      locBar: dialog.querySelector("#inspect-loc-bar"),
      meta: dialog.querySelector("#inspect-meta"),
      body: dialog.querySelector("#inspect-body"),
      tabs: dialog.querySelectorAll(".inspect-tab"),
    };

    const state = {
      opts: null,
      symbols: [],
      selected: null,
      locations: [],
      locIndex: 0,
      tab: "hex",
      hexHtml: "",
      asmHtml: "",
      sourceHtml: "",
    };

    els.filter.addEventListener("input", () => renderSymList());
    els.list.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-sym]");
      if (!btn) return;
      const name = btn.dataset.sym;
      const sym = state.symbols.find((s) => s.name === name);
      if (sym) selectSymbol(sym);
    });
    els.tabs.forEach((tab) => {
      tab.addEventListener("click", () => setTab(tab.dataset.tab));
    });
    els.locBar.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-loc]");
      if (!btn) return;
      state.locIndex = Number(btn.dataset.loc);
      renderLocBar();
      loadSourceForLoc().then(paintActive);
    });

    ui = { dialog, els, state };
    return ui;
  }

  function setTab(tab) {
    const { els, state } = ensureUi();
    state.tab = tab;
    els.tabs.forEach((t) => {
      const on = t.dataset.tab === tab;
      t.classList.toggle("is-active", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
    });
    paintActive();
  }

  function paintActive() {
    const { els, state } = ensureUi();
    delete els.body.dataset.highlighted;
    if (state.tab === "hex") {
      els.body.className = "source-body hex-body";
      els.body.innerHTML = state.hexHtml || '<span class="muted">No hex loaded.</span>';
    } else if (state.tab === "asm") {
      els.body.className = "source-body hex-body";
      els.body.innerHTML = state.asmHtml || '<span class="muted">No disassembly.</span>';
    } else {
      els.body.className = "source-body";
      els.body.innerHTML = state.sourceHtml || '<span class="muted">No source location.</span>';
    }
  }

  function renderSymList() {
    const { els, state } = ensureUi();
    const q = (els.filter.value || "").trim().toLowerCase();
    const sel = state.selected && state.selected.name;
    let html = "";
    for (const s of state.symbols) {
      if (q && !String(s.name).toLowerCase().includes(q)) continue;
      const meta = [
        s.kind || "",
        s.binding || "",
        s.size != null ? fmtSize(s.size) : "",
      ]
        .filter(Boolean)
        .join(" · ");
      html += `<li>
        <button type="button" class="inspect-sym-btn${sel === s.name ? " is-active" : ""}" data-sym="${esc(s.name)}">
          <code>${esc(s.name)}</code>
          <span class="muted">${esc(meta)}</span>
        </button>
      </li>`;
    }
    if (!html) html = `<li class="muted">No symbols</li>`;
    els.list.innerHTML = html;
  }

  function renderLocBar() {
    const { els, state } = ensureUi();
    if (!state.locations || state.locations.length <= 1) {
      els.locBar.hidden = true;
      els.locBar.innerHTML = "";
      return;
    }
    els.locBar.hidden = false;
    let html = `<span class="muted">locations</span>`;
    state.locations.forEach((loc, i) => {
      const label = loc.path + (loc.line != null ? ":" + loc.line : "") + " (" + (loc.role || "?") + ")";
      html += `<button type="button" class="inspect-loc-btn${i === state.locIndex ? " is-active" : ""}" data-loc="${i}">${esc(label)}</button>`;
    });
    els.locBar.innerHTML = html;
  }

  function formatAsm(lines) {
    if (!lines || !lines.length) return '<span class="muted">empty</span>';
    return lines
      .map((ln) => {
        const addr = Number(ln.addr).toString(16).padStart(8, "0");
        const raw = ln.raw_hex ? `<span class="hx-null">${esc(ln.raw_hex)}</span>  ` : "";
        return `<span class="hx-off">${addr}</span>  ${raw}${esc(ln.text)}`;
      })
      .join("\n");
  }

  async function fetchJson(url) {
    const res = await fetch(url, { headers: { Accept: "application/json" } });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.status);
    return data;
  }

  async function loadHex(sectionIndex, offset) {
    const { state } = ensureUi();
    const root = artifactRoot(state.opts);
    const off = Number(offset) || 0;
    const url =
      `${root}/sections/raw?index=${encodeURIComponent(String(sectionIndex))}` +
      `&offset=${encodeURIComponent(String(off))}` +
      `&limit=${HEX_PREVIEW}`;
    const res = await fetch(url, { headers: { Accept: "application/octet-stream" } });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      state.hexHtml = esc(err.detail || "hex load failed (" + res.status + ")");
      return;
    }
    const buf = await res.arrayBuffer();
    state.hexHtml = hexdumpHtml(buf, HEX_PREVIEW, off);
  }

  async function loadAsm(sectionIndex, offset) {
    const { state } = ensureUi();
    const root = artifactRoot(state.opts);
    const url =
      `${root}/disasm?index=${encodeURIComponent(String(sectionIndex))}` +
      `&offset=${encodeURIComponent(String(offset || 0))}` +
      `&limit=${DISASM_LIMIT}`;
    try {
      const lines = await fetchJson(url);
      state.asmHtml = formatAsm(lines);
    } catch (err) {
      state.asmHtml = esc(err.message || err);
    }
  }

  async function loadMpyDisasm(path) {
    const { state } = ensureUi();
    const root = artifactRoot(state.opts);
    const url =
      `${root}/files/mpy-disasm?path=${encodeURIComponent(path)}` +
      `&limit=${MPY_DISASM_LIMIT}`;
    try {
      const lines = await fetchJson(url);
      state.asmHtml =
        `<div class="muted">mpy · ${esc(path)}</div>\n` + formatAsm(lines);
    } catch (err) {
      state.asmHtml = esc(err.message || err);
    }
  }

  async function loadSourceForLoc() {
    const { state } = ensureUi();
    const loc = state.locations[state.locIndex];
    if (!loc) {
      state.sourceHtml = '<span class="muted">No location.</span>';
      return;
    }
    const role = loc.role || "";
    // Only role=sym is a bare symbol name. Root twins like __init__.py (line null)
    // must still load via /files.
    if (role === "sym") {
      state.sourceHtml =
        `<span class="muted">symbol</span> <code>${esc(loc.path)}</code>` +
        (loc.line != null ? ` <span class="muted">line ${esc(loc.line)}</span>` : "");
      return;
    }
    const root = artifactRoot(state.opts);
    const path = loc.path;
    try {
      const meta = await fetchJson(
        `${root}/files?path=${encodeURIComponent(path)}`
      );
      if (meta.binary || meta.text == null) {
        state.sourceHtml = `<span class="muted">binary ${esc(path)}</span>`;
        return;
      }
      let text = String(meta.text);
      if (loc.line != null && loc.line > 0) {
        const lines = text.split("\n");
        const i = loc.line - 1;
        const start = Math.max(0, i - 8);
        const end = Math.min(lines.length, i + 12);
        const chunk = [];
        for (let n = start; n < end; n++) {
          const mark = n === i ? "›" : " ";
          const num = String(n + 1).padStart(4, " ");
          const cls = n === i ? "inspect-src-hit" : "";
          chunk.push(
            `<span class="${cls}"><span class="hx-off">${mark}${num}</span>  ${esc(lines[n])}</span>`
          );
        }
        state.sourceHtml =
          `<div class="muted">${esc(path)}:${esc(loc.line)} · ${esc(role)}</div>\n` +
          chunk.join("\n");
      } else {
        state.sourceHtml =
          `<div class="muted">${esc(path)} · ${esc(role)}</div>\n` + esc(text.slice(0, 12000));
      }
    } catch (err) {
      state.sourceHtml =
        `<code>${esc(loc.path)}</code>` +
        (loc.line != null ? `:${esc(loc.line)}` : "") +
        ` <span class="muted">(${esc(role)}) — ${esc(err.message || err)}</span>`;
    }
  }

  async function resolveCodeSectionIndex() {
    const { state } = ensureUi();
    if (state.codeSectionIndex != null && Number.isFinite(state.codeSectionIndex)) {
      return state.codeSectionIndex;
    }
    const root = artifactRoot(state.opts);
    try {
      const secs = await fetchJson(`${root}/sections`);
      const code = (secs || []).find(
        (s) =>
          s.role === "code" ||
          s.name === "code" ||
          s.name === ".text" ||
          Number(s.type_id) === 10
      );
      if (code && code.index != null && Number.isFinite(Number(code.index))) {
        state.codeSectionIndex = Number(code.index);
        return state.codeSectionIndex;
      }
    } catch (_) {
      /* fall through */
    }
    return null;
  }

  async function selectSymbol(sym) {
    const { els, state } = ensureUi();
    state.selected = sym;
    renderSymList();
    els.meta.textContent =
      (sym.kind || "sym") +
      (sym.binding ? " · " + sym.binding : "") +
      " · off=" +
      (sym.offset != null ? sym.offset : "?") +
      " · " +
      fmtSize(sym.size || 0) +
      (sym.section_index != null ? " · section " + sym.section_index : "");

    const root = artifactRoot(state.opts);
    try {
      state.locations = await fetchJson(
        `${root}/locations?name=${encodeURIComponent(sym.name)}`
      );
    } catch (_) {
      state.locations = [];
    }
    state.locIndex = 0;
    renderLocBar();

    // Use only this symbol's section — do not inherit openInspect's sectionIndex
    // (that stuck value made memory/other reuse a prior .text/code window).
    let sec =
      sym.section_index != null && Number.isFinite(Number(sym.section_index))
        ? Number(sym.section_index)
        : null;
    const kind = String(sym.kind || "");
    const wantsCode =
      kind === "export" || kind === "func" || kind === "data";
    // Func/export without section_index → code section. memory/global stay empty.
    if ((sec == null || !Number.isFinite(sec)) && wantsCode) {
      sec = await resolveCodeSectionIndex();
    }
    const off = Number(sym.offset) || 0;
    const jobs = [loadSourceForLoc()];
    if (sec != null && Number.isFinite(sec) && sec < 65500) {
      jobs.push(loadHex(sec, off), loadAsm(sec, off));
    } else {
      const msg = wantsCode
        ? '<span class="muted">No section index for hex/asm.</span>'
        : `<span class="muted">No code section for ${esc(kind || "this")} symbol.</span>`;
      state.hexHtml = msg;
      state.asmHtml = msg;
    }
    await Promise.all(jobs);
    paintActive();
  }

  async function openInspect(opts) {
    if (!opts || !opts.filename) {
      console.warn("openInspect requires { filename }");
      return;
    }
    const { dialog, els, state } = ensureUi();
    state.opts = {
      filename: opts.filename,
      version: opts.version || null,
      package: opts.package || null,
      sectionIndex: opts.sectionIndex != null ? Number(opts.sectionIndex) : null,
      mpyPath: opts.mpyPath || null,
    };
    state.symbols = [];
    state.selected = null;
    state.locations = [];
    state.locIndex = 0;
    state.codeSectionIndex = null;
    state.hexHtml = "";
    state.asmHtml = "";
    state.sourceHtml = "";
    state.tab =
      opts.tab ||
      (opts.mpyPath ? "asm" : opts.symbol || opts.addr != null ? "source" : "hex");
    els.filter.value = "";
    els.title.textContent =
      (opts.package ? opts.package + " · " : "") + opts.filename;
    els.meta.textContent = "Loading…";
    els.body.textContent = "Loading…";
    setTab(state.tab);
    dialog.showModal();

    const root = artifactRoot(state.opts);
    try {
      state.symbols = await fetchJson(`${root}/symbols`);
    } catch (err) {
      els.meta.textContent = "symbols: " + (err.message || err);
      state.symbols = [];
    }
    renderSymList();

    if (opts.mpyPath) {
      els.meta.textContent = "mpy " + opts.mpyPath;
      state.hexHtml =
        '<span class="muted">Embedded .mpy — use asm tab for mpy-dis.</span>';
      state.sourceHtml =
        `<span class="muted">path</span> <code>${esc(opts.mpyPath)}</code>` +
        ` <span class="muted">(prefer twin .py via source tree)</span>`;
      await loadMpyDisasm(opts.mpyPath);
      // Prefer .py twin in source pane when present.
      const twin = String(opts.mpyPath)
        .replace(/\.upy\.mpy\d+\.sib\d+\.mpy$/i, ".py")
        .replace(/\.mpy$/i, ".py");
      if (twin !== opts.mpyPath) {
        try {
          const meta = await fetchJson(
            `${root}/files?path=${encodeURIComponent(twin)}`
          );
          if (meta && meta.text != null) {
            state.locations = [{ path: twin, line: null, role: "twin" }];
            state.locIndex = 0;
            renderLocBar();
            await loadSourceForLoc();
          }
        } catch (_) {
          /* twin optional */
        }
      }
      paintActive();
      return;
    }

    if (opts.symbol) {
      const hit =
        state.symbols.find((s) => s.name === opts.symbol) || {
          name: opts.symbol,
          section_index: opts.sectionIndex,
          offset: opts.addr != null ? opts.addr : 0,
          size: 0,
          kind: "other",
        };
      await selectSymbol(hit);
      return;
    }

    if (opts.addr != null) {
      const addr = Number(opts.addr);
      els.meta.textContent = "addr=" + addr;
      try {
        state.locations = await fetchJson(
          `${root}/addr2line?addr=${encodeURIComponent(String(addr))}`
        );
      } catch (err) {
        state.locations = [];
        state.sourceHtml = esc(err.message || err);
      }
      state.locIndex = 0;
      renderLocBar();
      let sec = state.opts.sectionIndex;
      if (sec == null || !Number.isFinite(Number(sec))) {
        sec = await resolveCodeSectionIndex();
      }
      const jobs = [loadSourceForLoc()];
      if (sec != null && Number.isFinite(sec)) {
        jobs.push(loadHex(sec, addr), loadAsm(sec, addr));
      } else {
        jobs.push(loadAsm(0, addr));
        state.hexHtml =
          '<span class="muted">No section index (asm may still work for Wasm).</span>';
      }
      await Promise.all(jobs);
      paintActive();
      return;
    }

    if (state.opts.sectionIndex != null) {
      els.meta.textContent = "section " + state.opts.sectionIndex;
      await Promise.all([
        loadHex(state.opts.sectionIndex, 0),
        loadAsm(state.opts.sectionIndex, 0),
      ]);
      state.sourceHtml = '<span class="muted">Pick a symbol for source.</span>';
      setTab("hex");
      paintActive();
      return;
    }

    const def = pickDefaultSymbol(state.symbols);
    if (def) {
      await selectSymbol(def);
    } else {
      els.meta.textContent = "No symbols in this artifact.";
      state.sourceHtml = '<span class="muted">Empty.</span>';
      paintActive();
    }
  }

  window.openInspect = openInspect;
  window.MetalInspect = {
    openInspect,
    hexdumpHtml,
    esc,
    fmtSize,
    cdnPrefix,
    artifactRoot,
  };
})();
