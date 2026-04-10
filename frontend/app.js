/**
 * gruenbeck2lox – frontend application
 * Plain vanilla JS, no build step required.
 */

const API = "/api/v1";

// ── Utilities ──────────────────────────────────────────────────────────────

async function apiFetch(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

function toast(msg, type = "success") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

function escHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function formatDate(isoStr) {
  if (!isoStr) return "–";
  try {
    return new Date(isoStr).toLocaleString("de-DE");
  } catch {
    return isoStr;
  }
}

// ── Tab routing ────────────────────────────────────────────────────────────

const TAB_LOADERS = {
  dashboard: loadDashboard,
  devices: loadDevices,
  loxone: loadLoxone,
  logs: loadLogs,
};

document.querySelectorAll("a.tab-link").forEach((link) => {
  link.addEventListener("click", (e) => {
    e.preventDefault();
    switchTab(link.dataset.tab);
  });
});

function switchTab(name) {
  document.querySelectorAll("a.tab-link").forEach((l) =>
    l.classList.toggle("active", l.dataset.tab === name)
  );
  document.querySelectorAll(".tab-content").forEach((s) =>
    s.classList.toggle("hidden", s.id !== `tab-${name}`)
  );
  TAB_LOADERS[name]?.();
}

// ── Dialog helpers ─────────────────────────────────────────────────────────

document.querySelectorAll("[data-close-dialog]").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.getElementById(btn.dataset.closeDialog)?.close();
  });
});

function openDialog(id) {
  document.getElementById(id)?.showModal();
}

function closeDialog(id) {
  document.getElementById(id)?.close();
}

function resetForm(formId) {
  const form = document.getElementById(formId);
  if (form) form.reset();
  const hidden = form?.querySelector("[name=id]");
  if (hidden) hidden.value = "";
}

// ── Dashboard ──────────────────────────────────────────────────────────────

async function loadDashboard() {
  const grid = document.getElementById("dashboard-grid");
  const empty = document.getElementById("dashboard-empty");

  let devices;
  try {
    devices = await apiFetch("/devices");
  } catch (err) {
    toast("Fehler beim Laden der Geräte: " + err.message, "error");
    return;
  }

  if (!devices.length) {
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");

  // Fetch values for all devices concurrently
  const valueResults = await Promise.allSettled(
    devices.map((d) => apiFetch(`/devices/${d.id}/values`))
  );

  grid.innerHTML = "";
  devices.forEach((device, i) => {
    const values = valueResults[i].status === "fulfilled" ? valueResults[i].value : {};
    grid.appendChild(buildDeviceCard(device, values));
  });
}

function buildDeviceCard(device, values) {
  const card = document.createElement("div");
  card.className = "device-card";

  const hasValues = values && Object.keys(values).length > 0;
  const v = values || {};

  // ── Status pill ──────────────────────────────────────────────────────────
  const ec = v.error_code;
  const pillClass = !hasValues ? "warn" : (ec && String(ec) !== "0") ? "error" : "ok";
  const pillText  = !hasValues ? "Warte…"
                  : (ec && String(ec) !== "0") ? `Fehler ${escHtml(String(ec))}`
                  : "Online";

  if (!hasValues) {
    card.innerHTML = `
      <div class="card-head">
        <h3><span>${escHtml(device.name)}</span><span class="badge">${escHtml(device.type.toUpperCase())}</span></h3>
        <span class="status-pill ${pillClass}">${pillText}</span>
      </div>
      <div class="card-no-values">Warte auf erste Daten…</div>`;
    return card;
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  const fmtL   = (n) => n != null ? `${Math.round(n)} l` : "–";
  const fmtM3  = (n) => n != null ? `${n} m³` : null;
  const fmtPct = (n) => n != null ? `${Math.round(n)} %` : null;
  // Salt values from API are in grams; display as kg with 3 decimal places
  const fmtSalt = (n) => n != null ? `${(Number(n) / 1000).toFixed(3)} kg` : "–";

  // ── Weichwasserverbrauch section ──────────────────────────────────────────
  const waterSection = `
    <div class="card-section">
      <div class="cs-icon">💧</div>
      <div class="cs-content">
        <div class="cs-title">Weichwasserverbrauch</div>
        <div class="cs-cols">
          <div class="cs-col"><span class="cs-val">${escHtml(fmtL(v.waterToday))}</span><span class="cs-lbl">Heute</span></div>
          <div class="cs-col"><span class="cs-val">${escHtml(fmtL(v.waterMonth))}</span><span class="cs-lbl">Monat</span></div>
          <div class="cs-col"><span class="cs-val">${escHtml(fmtL(v.waterYear))}</span><span class="cs-lbl">Jahr</span></div>
        </div>
      </div>
    </div>`;

  // ── Restkapazität section ─────────────────────────────────────────────────
  const pct = v.residualCapacityPct != null ? v.residualCapacityPct
            : (v.residualCapacity != null && v.totalCapacity) ? Math.round(v.residualCapacity / v.totalCapacity * 100)
            : null;
  const m3label = fmtM3(v.residualCapacityM3) ?? (v.residualCapacity != null ? fmtM3(+(v.residualCapacity/1000).toFixed(2)) : null);
  const pctLabel = fmtPct(pct);
  const capLabel = [m3label, pctLabel].filter(Boolean).join(" · ") || "–";
  const pctClamped = pct != null ? Math.max(0, Math.min(100, pct)) : 0;
  const fillClass = pct == null ? "" : pct < 20 ? " low" : pct < 40 ? " warn" : "";

  // Hardness flow inline
  const hIn  = v.water_hardness_in  != null ? `${v.water_hardness_in} °dH` : "–";
  const hOut = v.water_hardness_out != null ? `${v.water_hardness_out} °dH` : "–";

  const capSection = `
    <div class="card-section">
      <div class="cs-icon">🔋</div>
      <div class="cs-content">
        <div class="cs-title" style="display:flex;justify-content:space-between">
          <span>Restkapazität</span>
          <span style="font-weight:700;color:var(--pico-color)">${escHtml(capLabel)}</span>
        </div>
        <div class="cap-bar"><div class="cap-bar-fill${fillClass}" style="width:${pctClamped}%"></div></div>
        <div class="hardness-row" style="margin-top:0.4rem">
          <div class="hardness-val"><div class="hval">${escHtml(hIn)}</div><div class="hlabel">Eingang</div></div>
          <div class="hardness-arrow">→</div>
          <div class="hardness-val"><div class="hval">${escHtml(hOut)}</div><div class="hlabel">Ausgang</div></div>
        </div>
      </div>
    </div>`;

  // ── Nächste Regeneration section ─────────────────────────────────────────
  const regenSection = v.next_regeneration ? `
    <div class="card-section">
      <div class="cs-icon">🔄</div>
      <div class="cs-content">
        <div class="cs-title">Nächste Regeneration</div>
        <div class="cs-single">${escHtml(formatDate(v.next_regeneration))}</div>
      </div>
    </div>` : "";

  // ── Salzverbrauch section ─────────────────────────────────────────────────
  const saltSection = `
    <div class="card-section">
      <div class="cs-icon">🧂</div>
      <div class="cs-content">
        <div class="cs-title">Salzverbrauch</div>
        <div class="cs-cols">
          <div class="cs-col"><span class="cs-val">${escHtml(fmtSalt(v.saltToday))}</span><span class="cs-lbl">Heute</span></div>
          <div class="cs-col"><span class="cs-val">${escHtml(fmtSalt(v.saltMonth))}</span><span class="cs-lbl">Monat</span></div>
          <div class="cs-col"><span class="cs-val">${escHtml(fmtSalt(v.saltYear))}</span><span class="cs-lbl">Jahr</span></div>
        </div>
      </div>
    </div>`;

  // ── Nächste Wartung section ───────────────────────────────────────────────
  const maintSection = v.maintenanceDays != null ? `
    <div class="card-section">
      <div class="cs-icon">🔧</div>
      <div class="cs-content">
        <div class="cs-title">Nächste Wartung</div>
        <div class="cs-single">${escHtml(String(v.maintenanceDays))} Tage</div>
      </div>
    </div>` : "";

  // ── Fehler section (nur wenn aktiv) ──────────────────────────────────────
  const errActive = v.hasError || v.lastErrorMsg;
  const errorSection = errActive ? `
    <div class="card-section cs-error">
      <div class="cs-icon">⚠️</div>
      <div class="cs-content">
        <div class="cs-title">Aktiver Fehler</div>
        <div class="cs-single">${escHtml(v.lastErrorMsg || "Fehler aktiv")}</div>
      </div>
    </div>` : "";

  card.innerHTML = `
    <div class="card-head">
      <h3>
        <span>${escHtml(device.name)}</span>
        <span class="badge">${escHtml(device.type.toUpperCase())}</span>
      </h3>
      <div style="display:flex;align-items:center;gap:0.5rem">
        <span class="status-pill ${pillClass}">${pillText}</span>
        <button class="outline secondary" style="font-size:0.72rem;padding:0.2rem 0.55rem;margin:0;line-height:1.4"
                onclick="showDeviceRaw(${device.id})">Details ▸</button>
      </div>
    </div>
    <div class="card-body">
      ${waterSection}
      ${capSection}
      ${regenSection}
      ${saltSection}
      ${maintSection}
      ${errorSection}
    </div>`;
  return card;
}

// Auto-refresh dashboard every 30 s while visible
let _dashTimer = null;
function startDashRefresh() {
  stopDashRefresh();
  _dashTimer = setInterval(() => {
    const dash = document.getElementById("tab-dashboard");
    if (!dash.classList.contains("hidden")) loadDashboard();
  }, 30_000);
}
function stopDashRefresh() {
  if (_dashTimer) clearInterval(_dashTimer);
}
startDashRefresh();

// ── Device raw-detail dialog ────────────────────────────────────────────────

async function showDeviceRaw(deviceId) {
  const titleEl = document.getElementById("raw-detail-title");
  const bodyEl  = document.getElementById("raw-detail-body");
  titleEl.textContent = "Lade …";
  bodyEl.innerHTML = "";
  openDialog("dialog-device-raw");
  try {
    const result = await apiFetch(`/devices/${deviceId}/raw`);
    const ts = result.updated_at ? result.updated_at.slice(0, 19).replace("T", " ") + " UTC" : "";
    titleEl.textContent = `Rohdaten${ts ? " – " + ts : ""}`;
    const rows = Object.entries(result.data || {})
      .filter(([, val]) => val !== null && val !== undefined)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, val]) => {
        const display = typeof val === "object" ? JSON.stringify(val) : String(val);
        return `<tr><td>${escHtml(k)}</td><td>${escHtml(display)}</td></tr>`;
      })
      .join("");
    bodyEl.innerHTML = `<table class="raw-detail-table"><tbody>${rows}</tbody></table>`;
  } catch (err) {
    bodyEl.innerHTML = `<p class="status-error">${escHtml(err.message)}</p>`;
  }
}

// ── Devices ────────────────────────────────────────────────────────────────

async function loadDevices() {
  const list = document.getElementById("devices-list");
  list.innerHTML = '<p class="muted">Lade …</p>';
  try {
    const devices = await apiFetch("/devices");
    if (!devices.length) {
      list.innerHTML = '<p class="muted">Noch keine Geräte konfiguriert.</p>';
      return;
    }
    list.innerHTML = devices.map(renderDeviceItem).join("");
    list.querySelectorAll("[data-edit-device]").forEach((btn) =>
      btn.addEventListener("click", () => editDevice(Number(btn.dataset.editDevice)))
    );
    list.querySelectorAll("[data-delete-device]").forEach((btn) =>
      btn.addEventListener("click", () => deleteDevice(Number(btn.dataset.deleteDevice), btn.dataset.name))
    );
    list.querySelectorAll("[data-test-device]").forEach((btn) =>
      btn.addEventListener("click", () => testDevice(Number(btn.dataset.testDevice), btn))
    );
  } catch (err) {
    list.innerHTML = `<p class="status-error">Fehler: ${escHtml(err.message)}</p>`;
  }
}

function renderDeviceItem(d) {
  const connInfo = (d.type === "sd" && d.has_cloud_credentials)
    ? `Cloud: <strong>${escHtml(d.cloud_email || "–")}</strong>`
    : `Host: <strong>${escHtml(d.host)}:${d.port}</strong>`;
  return `
    <div class="list-item">
      <div class="list-item-header">
        <h3>${escHtml(d.name)}</h3>
        <div class="item-actions">
          <button data-test-device="${d.id}">Verbindungstest</button>
          <button data-edit-device="${d.id}" class="secondary">Bearbeiten</button>
          <button data-delete-device="${d.id}" data-name="${escHtml(d.name)}" class="secondary contrast">Löschen</button>
        </div>
      </div>
      <p class="list-item-meta">
        Typ: <strong>${escHtml(d.type.toUpperCase())}</strong> ·
        ${connInfo} ·
        Intervall: ${d.poll_interval}s ·
        Status: <span class="${d.enabled ? "status-ok" : "muted"}">${d.enabled ? "Aktiv" : "Inaktiv"}</span>
      </p>
    </div>`;
}

function _toggleDeviceFields(type) {
  const isCloud = type === "sd";
  document.getElementById("local-fields").classList.toggle("hidden", isCloud);
  document.getElementById("cloud-fields").classList.toggle("hidden", !isCloud);
}

document.getElementById("device-type-select").addEventListener("change", (e) => {
  _toggleDeviceFields(e.target.value);
});

document.getElementById("btn-add-device").addEventListener("click", () => {
  document.getElementById("dialog-device-title").textContent = "Gerät hinzufügen";
  resetForm("form-device");
  _toggleDeviceFields("sc");  // default to SC (local)
  openDialog("dialog-device");
});

document.getElementById("form-device").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const id = fd.get("id");
  const type = fd.get("type");
  const body = {
    name: fd.get("name"),
    type,
    host: type === "sd" ? "" : (fd.get("host") || ""),
    port: Number(fd.get("port")) || 80,
    poll_interval: Number(fd.get("poll_interval")),
    enabled: fd.get("enabled") === "on",
    ...(type === "sd" ? {
      cloud_email: fd.get("cloud_email") || null,
      cloud_password: fd.get("cloud_password") || null,
    } : {}),
  };
  try {
    if (id) {
      await apiFetch(`/devices/${id}`, { method: "PUT", body: JSON.stringify(body) });
      toast("Gerät aktualisiert");
    } else {
      await apiFetch("/devices", { method: "POST", body: JSON.stringify(body) });
      toast("Gerät hinzugefügt");
    }
    closeDialog("dialog-device");
    loadDevices();
  } catch (err) {
    toast("Fehler: " + err.message, "error");
  }
});

async function editDevice(id) {
  try {
    const d = await apiFetch(`/devices/${id}`);
    const form = document.getElementById("form-device");
    form.querySelector("[name=id]").value = d.id;
    form.querySelector("[name=name]").value = d.name;
    form.querySelector("[name=type]").value = d.type;
    form.querySelector("[name=host]").value = d.host || "";
    form.querySelector("[name=port]").value = d.port;
    form.querySelector("[name=poll_interval]").value = d.poll_interval;
    form.querySelector("[name=enabled]").checked = d.enabled;
    form.querySelector("[name=cloud_email]").value = d.cloud_email || "";
    form.querySelector("[name=cloud_password]").value = "";
    _toggleDeviceFields(d.type);
    document.getElementById("dialog-device-title").textContent = "Gerät bearbeiten";
    openDialog("dialog-device");
  } catch (err) {
    toast("Fehler: " + err.message, "error");
  }
}

async function deleteDevice(id, name) {
  if (!confirm(`Gerät "${name}" wirklich löschen?`)) return;
  try {
    await apiFetch(`/devices/${id}`, { method: "DELETE" });
    toast("Gerät gelöscht");
    loadDevices();
  } catch (err) {
    toast("Fehler: " + err.message, "error");
  }
}

async function testDevice(id, btn) {
  btn.disabled = true;
  btn.textContent = "Teste …";
  try {
    const res = await apiFetch(`/devices/${id}/test`, { method: "POST" });
    if (res.reachable) {
      const detail = res.mode === "cloud" ? res.email : `${res.host}:${res.port}`;
      toast(`✓ Verbindung OK (${detail})`, "success");
    } else {
      const detail = res.mode === "cloud" ? `Cloud: ${res.error}` : `${res.host}:${res.port}`;
      toast(`✗ Nicht erreichbar – ${detail}`, "error");
    }
  } catch (err) {
    toast("Fehler: " + err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Verbindungstest";
  }
}

// ── Loxone ─────────────────────────────────────────────────────────────────

const FIELDS = [
  { key: "residualCapacity",    label: "Restkapazität (l)",       grp: "Kapazität" },
  { key: "residualCapacityM3",  label: "Restkapazität (m³)",      grp: "Kapazität" },
  { key: "residualCapacityPct", label: "Restkapazität (%)",       grp: "Kapazität" },
  { key: "totalCapacity",       label: "Gesamtkapazität (l)",     grp: "Kapazität" },
  { key: "waterToday",          label: "Wasser heute (l)",        grp: "Verbrauch" },
  { key: "waterMonth",          label: "Wasser Monat (l)",        grp: "Verbrauch" },
  { key: "waterYear",           label: "Wasser Jahr (l)",         grp: "Verbrauch" },
  { key: "saltToday",           label: "Salz heute (g)",          grp: "Salz" },
  { key: "saltMonth",           label: "Salz Monat (kg)",         grp: "Salz" },
  { key: "saltYear",            label: "Salz Jahr (kg)",          grp: "Salz" },
  { key: "saltRange",           label: "Salzreichweite (Tage)",   grp: "Salz" },

  { key: "water_hardness_in",   label: "Eingangshärte (°dH)",     grp: "Härte" },
  { key: "water_hardness_out",  label: "Ausgangshärte (°dH)",     grp: "Härte" },
  { key: "next_regeneration",   label: "Nächste Regeneration",    grp: "Regeneration" },
  { key: "last_regeneration",   label: "Letzte Regeneration",     grp: "Regeneration" },
  { key: "maintenanceDays",     label: "Tage bis Wartung",        grp: "Status" },
  { key: "hasError",            label: "Fehler aktiv (0/1)",      grp: "Status" },
  { key: "currentFlow",         label: "Durchfluss (l/Min)",       grp: "Status" },
  { key: "error_code",          label: "Fehlercode",              grp: "Status" },
];

async function loadLoxone() {
  const list = document.getElementById("loxone-list");
  list.innerHTML = '<p class="muted">Lade …</p>';
  try {
    const [servers, devices] = await Promise.all([
      apiFetch("/loxone"),
      apiFetch("/devices"),
    ]);
    if (!servers.length) {
      list.innerHTML = '<p class="muted">Noch keine Miniserver konfiguriert.</p>';
      return;
    }
    const subResults = await Promise.allSettled(
      servers.map((s) => apiFetch(`/loxone/${s.id}/subscriptions`))
    );
    list.innerHTML = "";
    servers.forEach((srv, i) => {
      const subs = subResults[i].status === "fulfilled" ? subResults[i].value : [];
      list.appendChild(buildServerItem(srv, subs, devices));
    });
    list.querySelectorAll("[data-edit-server]").forEach((btn) =>
      btn.addEventListener("click", () => editServer(Number(btn.dataset.editServer)))
    );
    list.querySelectorAll("[data-delete-server]").forEach((btn) =>
      btn.addEventListener("click", () => deleteServer(Number(btn.dataset.deleteServer), btn.dataset.name))
    );
    list.querySelectorAll("[data-test-server]").forEach((btn) =>
      btn.addEventListener("click", () => testServer(Number(btn.dataset.testServer), btn))
    );
    list.querySelectorAll("[data-add-sub]").forEach((btn) =>
      btn.addEventListener("click", () => openAddSubscription(Number(btn.dataset.addSub), devices))
    );
    list.querySelectorAll("[data-edit-sub]").forEach((btn) =>
      btn.addEventListener("click", () =>
        editSubscription(Number(btn.dataset.serverId), Number(btn.dataset.editSub), devices)
      )
    );
    list.querySelectorAll("[data-delete-sub]").forEach((btn) =>
      btn.addEventListener("click", () =>
        deleteSubscription(Number(btn.dataset.serverId), Number(btn.dataset.deleteSub))
      )
    );
    list.querySelectorAll("[data-xml-sub]").forEach((btn) =>
      btn.addEventListener("click", () =>
        downloadSubXml(Number(btn.dataset.serverId), Number(btn.dataset.xmlSub))
      )
    );
  } catch (err) {
    list.innerHTML = `<p class="status-error">Fehler: ${escHtml(err.message)}</p>`;
  }
}

function buildServerItem(srv, subs, devices) {
  const el = document.createElement("div");
  el.className = "list-item";
  const deviceMap = Object.fromEntries(devices.map((d) => [d.id, d.name]));

  const subRows = subs.map((sub) => {
    const devName = escHtml(deviceMap[sub.device_id] ?? String(sub.device_id));
    const preview = sub.fields.slice(0, 3).join(", ") + (sub.fields.length > 3 ? ` (+${sub.fields.length - 3})` : "");
    return `
    <div class="sub-row">
      <div class="sub-info">
        <strong>${devName}</strong>
        <span class="muted" style="font-size:0.78rem">${escHtml(preview || "Keine Felder")}</span>
      </div>
      <div class="item-actions">
        <button data-xml-sub="${sub.id}" data-server-id="${srv.id}" title="Loxone XML herunterladen">⬇ XML</button>
        <button data-edit-sub="${sub.id}" data-server-id="${srv.id}" class="secondary">✎</button>
        <button data-delete-sub="${sub.id}" data-server-id="${srv.id}" class="secondary contrast" style="padding:0.25rem 0.5rem">×</button>
      </div>
    </div>`;
  }).join("");

  const pushInfo = `${srv.push_on_change ? "Bei Änderung" : "Nur Intervall"} · alle ${srv.push_interval_sec}s`;

  el.innerHTML = `
    <div class="list-item-header">
      <h3>${escHtml(srv.name)}</h3>
      <div class="item-actions">
        <button data-test-server="${srv.id}">Test-Push</button>
        <button data-edit-server="${srv.id}" class="secondary">Bearbeiten</button>
        <button data-delete-server="${srv.id}" data-name="${escHtml(srv.name)}" class="secondary contrast">Löschen</button>
      </div>
    </div>
    <p class="list-item-meta">${escHtml(srv.host)}:${srv.port} · UDP · ${escHtml(pushInfo)}</p>
    <div class="subs-list">
      <strong style="font-size:0.85rem">UDP-Pakete</strong>
      ${subRows || '<p class="muted" style="font-size:0.85rem;margin:0.3rem 0">Noch keine UDP-Pakete konfiguriert.</p>'}
      <button data-add-sub="${srv.id}" style="margin-top:0.5rem;padding:0.3rem 0.7rem;font-size:0.8rem">+ Gerät hinzufügen</button>
    </div>`;
  return el;
}

// ── Server form ────────────────────────────────────────────────────────────

document.getElementById("btn-add-server").addEventListener("click", () => {
  document.getElementById("dialog-server-title").textContent = "Server hinzufügen";
  resetForm("form-server");
  openDialog("dialog-server");
});

document.getElementById("form-server").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const id = fd.get("id");
  const body = {
    name: fd.get("name"),
    host: fd.get("host"),
    port: Number(fd.get("port")),
    push_on_change: fd.has("push_on_change"),
    push_interval_sec: Number(fd.get("push_interval_sec")),
  };
  try {
    if (id) {
      await apiFetch(`/loxone/${id}`, { method: "PUT", body: JSON.stringify(body) });
      toast("Server aktualisiert");
    } else {
      await apiFetch("/loxone", { method: "POST", body: JSON.stringify(body) });
      toast("Server hinzugefügt");
    }
    closeDialog("dialog-server");
    loadLoxone();
  } catch (err) {
    toast("Fehler: " + err.message, "error");
  }
});

async function editServer(id) {
  try {
    const s = await apiFetch(`/loxone/${id}`);
    const form = document.getElementById("form-server");
    form.querySelector("[name=id]").value = s.id;
    form.querySelector("[name=name]").value = s.name;
    form.querySelector("[name=host]").value = s.host;
    form.querySelector("[name=port]").value = s.port;
    form.querySelector("[name=push_on_change]").checked = s.push_on_change;
    form.querySelector("[name=push_interval_sec]").value = s.push_interval_sec;
    document.getElementById("dialog-server-title").textContent = "Server bearbeiten";
    openDialog("dialog-server");
  } catch (err) {
    toast("Fehler: " + err.message, "error");
  }
}

async function deleteServer(id, name) {
  if (!confirm(`Server "${name}" wirklich löschen?`)) return;
  try {
    await apiFetch(`/loxone/${id}`, { method: "DELETE" });
    toast("Server gelöscht");
    loadLoxone();
  } catch (err) {
    toast("Fehler: " + err.message, "error");
  }
}

async function testServer(id, btn) {
  btn.disabled = true;
  btn.textContent = "Sende …";
  try {
    const res = await apiFetch(`/loxone/${id}/test`, { method: "POST" });
    if (res.success) {
      toast("✓ Test-Push erfolgreich", "success");
    } else {
      toast("✗ Test-Push fehlgeschlagen: " + (res.error ?? ""), "error");
    }
  } catch (err) {
    toast("Fehler: " + err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Test-Push";
  }
}

// ── Subscription form ──────────────────────────────────────────────────────

function buildFieldPicker(checkedFields = []) {
  const picker = document.getElementById("field-picker");
  picker.innerHTML = FIELDS.map((f) => {
    const checked = checkedFields.includes(f.key) ? "checked" : "";
    return `<label><input type="checkbox" name="fields" value="${f.key}" ${checked}> ${escHtml(f.label)}</label>`;
  }).join("");
}

document.getElementById("btn-check-all").addEventListener("click", () => {
  document.querySelectorAll("#field-picker input").forEach((cb) => (cb.checked = true));
});
document.getElementById("btn-check-none").addEventListener("click", () => {
  document.querySelectorAll("#field-picker input").forEach((cb) => (cb.checked = false));
});

function openAddSubscription(serverId, devices) {
  const form = document.getElementById("form-subscription");
  form.querySelector("[name=server_id]").value = serverId;
  form.querySelector("[name=sub_id]").value = "";
  const devSel = document.getElementById("sub-device-select");
  devSel.innerHTML = devices.map((d) => `<option value="${d.id}">${escHtml(d.name)}</option>`).join("");
  devSel.disabled = false;
  buildFieldPicker([]);
  document.getElementById("dialog-sub-title").textContent = "UDP-Paket konfigurieren";
  openDialog("dialog-subscription");
}

async function editSubscription(serverId, subId, devices) {
  try {
    const subs = await apiFetch(`/loxone/${serverId}/subscriptions`);
    const sub = subs.find((s) => s.id === subId);
    if (!sub) { toast("Subscription nicht gefunden", "error"); return; }
    const form = document.getElementById("form-subscription");
    form.querySelector("[name=server_id]").value = serverId;
    form.querySelector("[name=sub_id]").value = subId;
    const devSel = document.getElementById("sub-device-select");
    devSel.innerHTML = devices.map((d) => `<option value="${d.id}">${escHtml(d.name)}</option>`).join("");
    devSel.value = sub.device_id;
    devSel.disabled = true;
    buildFieldPicker(sub.fields);
    document.getElementById("dialog-sub-title").textContent = "UDP-Paket bearbeiten";
    openDialog("dialog-subscription");
  } catch (err) {
    toast("Fehler: " + err.message, "error");
  }
}

document.getElementById("form-subscription").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const serverId = fd.get("server_id");
  const subId = fd.get("sub_id");
  const fields = fd.getAll("fields");
  if (!fields.length) { toast("Bitte mindestens ein Feld auswählen", "error"); return; }
  const body = {
    device_id: Number(document.getElementById("sub-device-select").value),
    fields,
  };
  try {
    if (subId) {
      await apiFetch(`/loxone/${serverId}/subscriptions/${subId}`, { method: "PUT", body: JSON.stringify(body) });
      toast("Paket aktualisiert");
    } else {
      await apiFetch(`/loxone/${serverId}/subscriptions`, { method: "POST", body: JSON.stringify(body) });
      toast("Paket hinzugefügt");
    }
    closeDialog("dialog-subscription");
    loadLoxone();
  } catch (err) {
    toast("Fehler: " + err.message, "error");
  }
});

async function deleteSubscription(serverId, subId) {
  if (!confirm("UDP-Paket wirklich löschen?")) return;
  try {
    await apiFetch(`/loxone/${serverId}/subscriptions/${subId}`, { method: "DELETE" });
    toast("Paket gelöscht");
    loadLoxone();
  } catch (err) {
    toast("Fehler: " + err.message, "error");
  }
}

function downloadSubXml(serverId, subId) {
  window.location.href = `/api/v1/loxone/${serverId}/subscriptions/${subId}/template.xml`;
}

// ── Logs ───────────────────────────────────────────────────────────────────

async function loadLogs() {
  const tbody = document.getElementById("logs-body");
  tbody.innerHTML = '<tr><td colspan="6" class="muted">Lade …</td></tr>';
  try {
    const logs = await apiFetch("/logs?limit=200");
    if (!logs.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="muted">Keine Einträge vorhanden.</td></tr>';
      return;
    }
    tbody.innerHTML = logs.map((l) => `
      <tr>
        <td>${escHtml(formatDate(l.created_at))}</td>
        <td>${escHtml(l.device_id ?? "–")}</td>
        <td><code>${escHtml(l.gruenbeck_key ?? "–")}</code></td>
        <td><code>${escHtml(l.loxone_input ?? "–")}</code></td>
        <td>${escHtml(l.value ?? "–")}</td>
        <td class="${l.status === "ok" ? "status-ok" : "status-error"}">${escHtml(l.status)}</td>
      </tr>`).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="status-error">Fehler: ${escHtml(err.message)}</td></tr>`;
  }
}

document.getElementById("btn-refresh-logs").addEventListener("click", loadLogs);

// ── Init ───────────────────────────────────────────────────────────────────

switchTab("dashboard");
