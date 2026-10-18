"use strict";

const $ = (id) => document.getElementById(id);

const TYPE_LABEL = { dvbt: "DVB-T", dvbt2: "DVB-T2", dvbc: "DVB-C", dvbs: "DVB-S", dvbs2: "DVB-S2" };

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function serviceTypeTag(t) {
  const radio = t === 2 || t === 10;
  return `<span class="tag ${radio ? "radio" : "tv"}">${radio ? "Radio" : "TV"}</span>`;
}

function renderAdapters(adapters) {
  const el = $("adapters");
  if (!adapters || !adapters.length) {
    el.textContent = "No hay adaptadores DVB detectados.";
    return;
  }
  el.innerHTML = adapters.map((a) => `
    <div class="adapter">
      <div class="path">${esc(a.path)}</div>
      <div class="fe">Frontends: ${esc((a.frontends || []).join(", ") || "—")}</div>
    </div>`).join("");
}

function renderSignal(signal) {
  if (signal == null) return "";
  const pct = Math.min(100, Math.round((signal / 65535) * 100));
  return `<div>Señal: ${pct}% (${signal})</div>
          <div class="signal-bar"><div style="width:${pct}%"></div></div>`;
}

function renderResult(data) {
  const el = $("scanResult");
  if (!data || !data.ok) {
    el.innerHTML = `<div class="err">Error: ${esc(data && data.error)}</div>`;
    return;
  }
  const n = (data.services || []).length;
  const meta = data.demo
    ? " (datos de demostración)"
    : data.command ? ` — <code>${esc(data.command)}</code>` : "";
  el.innerHTML = `
    <div class="ok">✔ Escaneo completado: ${n} servicio(s) en
      ${esc(data.network || "—")} @ ${(data.frequency / 1e6).toFixed(2)} MHz${meta}</div>
    ${renderSignal(data.signal)}`;
}

function renderChannels(scan) {
  const services = scan.services || [];
  $("channelCount").textContent = `(${services.length})`;
  const body = $("channelBody");
  if (!services.length) {
    body.innerHTML = '<tr><td colspan="6" class="muted">Sin canales</td></tr>';
    return;
  }
  const filter = ($("filter").value || "").toLowerCase();
  const rows = services
    .filter((s) => !filter ||
      (s.name || "").toLowerCase().includes(filter) ||
      (s.provider || "").toLowerCase().includes(filter))
    .map((s) => `
      <tr>
        <td>${esc(s.sid)}</td>
        <td>${esc(s.name)}</td>
        <td>${esc(s.provider || "—")}</td>
        <td>${serviceTypeTag(s.type)}</td>
        <td>${esc(s.now || "—")}</td>
        <td>${esc(s.next || "—")}</td>
      </tr>`).join("");
  body.innerHTML = rows;
}

async function loadStatus() {
  const r = await fetch("/api/status");
  const s = await r.json();
  const badge = $("statusBadge");
  if (s.demo) {
    badge.textContent = "DEMO · " + s.platform;
    badge.className = "status-badge demo";
  } else {
    badge.textContent = "EN VIVO · " + s.platform;
    badge.className = "status-badge live";
  }
  renderAdapters(s.adapters);
  $("footer").textContent =
    `Plataforma: ${s.platform} · dvb2m3u8: ${s.dvb2m3u8 || "no encontrado"}` +
    (s.last_scan_time ? ` · último escaneo: ${new Date(s.last_scan_time * 1000).toLocaleString()}` : "");
}

async function loadChannels() {
  const r = await fetch("/api/channels");
  renderChannels(await r.json());
}

async function onScan(e) {
  e.preventDefault();
  const btn = $("scanBtn");
  btn.disabled = true;
  btn.textContent = "Escaneando…";
  $("scanResult").innerHTML = "";
  const body = {
    type: $("type").value,
    freq_mhz: parseFloat($("freq_mhz").value),
    symbol_rate: $("symbol_rate").value ? parseInt($("symbol_rate").value, 10) : null,
    pol: $("pol").value,
    adapter: parseInt($("adapter").value, 10),
    timeout: parseInt($("timeout").value, 10),
  };
  try {
    const r = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    renderResult(data);
    if (data && data.ok) renderChannels(data);
    loadStatus();
  } catch (err) {
    $("scanResult").innerHTML = `<div class="err">Error de red: ${esc(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Escanear";
  }
}

$("scanForm").addEventListener("submit", onScan);
$("filter").addEventListener("input", () => loadChannels());

loadStatus();
loadChannels();
