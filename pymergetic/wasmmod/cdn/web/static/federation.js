/** Admin federation link UI — session + CSRF against /admin/federation/*. */
(function () {
  const root = document.getElementById("fed-root");
  if (!root) return;

  const base = (root.dataset.base || "").replace(/\/$/, "");
  const msg = document.getElementById("fed-msg");

  function setMsg(text, isError) {
    if (!msg) return;
    msg.textContent = text || "";
    msg.classList.toggle("fed-error", Boolean(isError));
  }

  async function ensureAdmin() {
    const me = await fetch(base + "/auth/me", { credentials: "same-origin" });
    if (me.status === 401) {
      window.location.href =
        base + "/login?next=" + encodeURIComponent(base + "/federation");
      return null;
    }
    if (!me.ok) throw new Error("auth/me failed");
    const user = await me.json();
    if (!user.is_admin) {
      setMsg("Admin session required for federation controls.", true);
      return null;
    }
    return user;
  }

  async function csrfToken() {
    const res = await fetch(base + "/auth/csrf", { credentials: "same-origin" });
    if (!res.ok) throw new Error("CSRF prime failed");
    const data = await res.json();
    return data.csrf_token;
  }

  async function api(method, path, body) {
    const headers = { Accept: "application/json" };
    if (method !== "GET" && method !== "HEAD") {
      headers["X-CSRF-Token"] = await csrfToken();
      if (body !== undefined) headers["Content-Type"] = "application/json";
    }
    const res = await fetch(base + path, {
      method,
      credentials: "same-origin",
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (_) {
      data = { detail: text };
    }
    if (!res.ok) {
      const detail = data && data.detail;
      throw new Error(
        typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : res.statusText
      );
    }
    return data;
  }

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderStatus(st) {
    const el = document.getElementById("fed-status");
    if (!el || !st) return;
    el.innerHTML = [
      ["Peers", st.peers],
      ["Mounts", `${st.mounts_enabled} enabled / ${st.mounts_total}`],
      ["Grants active", st.grants_active],
      ["Max hops", st.max_hops],
      ["Proxy", st.proxy_ready ? "ready" : "not ready"],
      ["Detail", st.detail || "—"],
    ]
      .map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`)
      .join("");
  }

  function renderPeers(rows) {
    const el = document.getElementById("fed-peers");
    if (!el) return;
    if (!rows.length) {
      el.textContent = "No peers yet.";
      return;
    }
    el.innerHTML =
      "<table class=\"fed-table\"><thead><tr><th>Label</th><th>Base URL</th><th>Status</th><th></th></tr></thead><tbody>" +
      rows
        .map(
          (r) =>
            `<tr data-id="${esc(r.id)}"><td>${esc(r.label)}</td><td><code>${esc(
              r.base_url
            )}</code></td><td>${esc(r.status)}</td>` +
            `<td><button type="button" class="fed-del" data-kind="peer" data-id="${esc(
              r.id
            )}">Delete</button></td></tr>`
        )
        .join("") +
      "</tbody></table>";
  }

  function renderMounts(rows) {
    const el = document.getElementById("fed-mounts");
    if (!el) return;
    if (!rows.length) {
      el.textContent = "No mounts yet.";
      return;
    }
    el.innerHTML =
      "<table class=\"fed-table\"><thead><tr><th>Prefix</th><th>Peer</th><th>Dir</th><th>Cred</th><th>Enabled</th><th></th></tr></thead><tbody>" +
      rows
        .map(
          (r) =>
            `<tr><td><code>${esc(r.prefix)}</code></td><td>${esc(
              r.peer_label || r.peer_id
            )}</td><td>${esc(r.direction || "pull")}</td>` +
            `<td>${r.has_credential ? esc(r.credential_fingerprint || "yes") : "—"}</td>` +
            `<td>${r.enabled ? "yes" : "no"}</td>` +
            `<td><button type="button" class="fed-key" data-id="${esc(
              r.id
            )}">Ed25519</button> ` +
            `<button type="button" class="fed-del" data-kind="mount" data-id="${esc(
              r.id
            )}">Delete</button></td></tr>`
        )
        .join("") +
      "</tbody></table>";
  }

  function renderGrants(rows) {
    const el = document.getElementById("fed-grants");
    if (!el) return;
    if (!rows.length) {
      el.textContent = "No grants yet.";
      return;
    }
    el.innerHTML =
      "<table class=\"fed-table\"><thead><tr><th>Prefix</th><th>Parent</th><th>Status</th><th></th></tr></thead><tbody>" +
      rows
        .map(
          (r) =>
            `<tr><td><code>${esc(r.prefix)}</code></td><td>${esc(r.parent_label)}</td><td>${esc(
              r.status
            )}</td>` +
            `<td>${
              r.status === "active"
                ? `<button type="button" class="fed-del" data-kind="grant" data-id="${esc(
                    r.id
                  )}">Revoke</button>`
                : ""
            }</td></tr>`
        )
        .join("") +
      "</tbody></table>";
  }

  async function refresh() {
    const [st, peers, mounts, grants] = await Promise.all([
      api("GET", "/admin/federation/status"),
      api("GET", "/admin/federation/peers"),
      api("GET", "/admin/federation/mounts"),
      api("GET", "/admin/federation/grants"),
    ]);
    renderStatus(st);
    renderPeers(peers);
    renderMounts(mounts);
    renderGrants(grants);
  }

  document.getElementById("fed-accept")?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const form = ev.target;
    setMsg("Accepting grant…");
    try {
      const row = await api("POST", "/admin/federation/grants/accept", {
        prefix: form.prefix.value.trim(),
        parent_label: form.parent_label.value.trim(),
        parent_base_url: form.parent_base_url.value.trim() || null,
        key_name: form.key_name.value.trim() || "federation-parent",
        allow_publish: !!form.allow_publish?.checked,
        parent_public_key: form.parent_public_key?.value.trim() || null,
      });
      const out = document.getElementById("fed-accept-out");
      if (out) {
        out.hidden = false;
        out.textContent =
          "One-time API key (copy to parent mount bearer):\n" +
          row.api_key +
          "\n\nprefix=" +
          row.prefix +
          " key_prefix=" +
          row.api_key_prefix;
      }
      setMsg("Grant accepted — copy the API key now; it will not be shown again.");
      await refresh();
    } catch (err) {
      setMsg(String(err.message || err), true);
    }
  });

  document.getElementById("fed-link")?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const form = ev.target;
    setMsg("Creating peer + mount…");
    try {
      const peer = await api("POST", "/admin/federation/peers", {
        label: form.label.value.trim(),
        base_url: form.base_url.value.trim(),
        public_browse_url: form.public_browse_url.value.trim() || null,
        status: "active",
      });
      await api("POST", "/admin/federation/mounts", {
        prefix: form.prefix.value.trim(),
        peer_id: peer.id,
        bearer_token: form.bearer_token.value.trim(),
        direction: form.direction?.value || "pull",
        enabled: true,
      });
      form.reset();
      setMsg(`Linked peer “${peer.label}” with mount.`);
      await refresh();
    } catch (err) {
      setMsg(String(err.message || err), true);
    }
  });

  root.addEventListener("click", async (ev) => {
    const keyBtn = ev.target.closest(".fed-key");
    if (keyBtn) {
      const id = keyBtn.dataset.id;
      if (!id) return;
      if (!window.confirm("Generate Ed25519 key for this mount? Replaces existing credential."))
        return;
      setMsg("Generating fed key…");
      try {
        const row = await api("POST", `/admin/federation/mounts/${id}/fed-key`);
        const out = document.getElementById("fed-accept-out");
        if (out) {
          out.hidden = false;
          out.textContent =
            "Copy public key to child grant (parent_public_key):\n" +
            row.public_key +
            "\n\nkid=" +
            row.key_id;
        }
        setMsg("Ed25519 installed on mount — paste public key into child grant.");
        await refresh();
      } catch (err) {
        setMsg(String(err.message || err), true);
      }
      return;
    }
    const btn = ev.target.closest(".fed-del");
    if (!btn) return;
    const kind = btn.dataset.kind;
    const id = btn.dataset.id;
    if (!kind || !id) return;
    const label =
      kind === "grant" ? "Revoke this grant?" : `Delete this ${kind}?`;
    if (!window.confirm(label)) return;
    setMsg("Working…");
    try {
      if (kind === "peer") await api("DELETE", `/admin/federation/peers/${id}`);
      else if (kind === "mount") await api("DELETE", `/admin/federation/mounts/${id}`);
      else if (kind === "grant")
        await api("POST", `/admin/federation/grants/${id}/revoke`);
      setMsg("Done.");
      await refresh();
    } catch (err) {
      setMsg(String(err.message || err), true);
    }
  });

  (async () => {
    try {
      if (!(await ensureAdmin())) return;
      await refresh();
      setMsg("");
    } catch (err) {
      setMsg(String(err.message || err), true);
    }
  })();
})();
