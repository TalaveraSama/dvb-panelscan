"use strict";

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function getJSON(url) {
  const r = await fetch(url);
  return r.json();
}
async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function typeTag(t) {
  const radio = t === 2 || t === 10;
  return `<span class="tag ${radio ? "radio" : "tv"}">${radio ? "Radio" : "TV"}</span>`;
}

function fmtMHz(mhz) {
  if (!mhz) return "—";
  return (Number(mhz) / 1e6).toFixed(3) + " GHz";
}

/* ------------------------- Estado / adaptadores ------------------------- */
async function loadStatus() {
  const s = await getJSON("/api/status");
  const badge = $("statusBadge");
  const live = s.linux && s.dvb2m3u8;
  badge.textContent = live ? "EN VIVO · " + s.platform : "DEMO · " + s.platform;
  badge.className = "status-badge " + (live ? "live" : "demo");

  const el = $("adapters");
  if (!s.adapters || !s.adapters.length) {
    el.textContent = "No hay adaptadores DVB detectados.";
  } else {
    el.innerHTML = s.adapters.map((a) => `
      <div class="adapter">
        <div class="path">${esc(a.path)}</div>
        <div class="fe">${esc((a.frontends || []).join(", ") || "—")}</div>
      </div>`).join("");
  }
  $("bins").textContent = `dvb2m3u8: ${s.dvb2m3u8 || "no encontrado"} · tbsstream: ${s.tbsstream || "no encontrado"}`;
  $("footer").textContent = `Plataforma: ${s.platform}`;
}

/* ------------------------------ Transponders ---------------------------- */
let transponders = [];

async function loadTransponders() {
  transponders = await getJSON("/api/transponders");
  renderTransponders();
  renderStreamSelect();
}

function renderTransponders() {
  const body = $("tpBody");
  if (!transponders.length) {
    body.innerHTML = '<tr><td colspan="8" class="muted">Sin transponders guardados</td></tr>';
    return;
  }
  body.innerHTML = transponders.map((t) => `
    <tr>
      <td>${esc(t.name)}</td>
      <td>${esc(t.type.toUpperCase())}</td>
      <td>${esc(t.freq_mhz)} MHz</td>
      <td>${esc(t.symbol_rate)}</td>
      <td>${esc(t.pol)}</td>
      <td>${esc(t.lnb_lo)}</td>
      <td>${esc(t.adapter)}</td>
      <td class="actions-cell">
        <button class="btn small" data-scan="${esc(t.id)}">Escanear</button>
        <button class="btn small danger" data-del="${esc(t.id)}">Borrar</button>
      </td>
    </tr>`).join("");
  body.querySelectorAll("[data-scan]").forEach((b) =>
    b.addEventListener("click", () => scanTransponder(b.dataset.scan)));
  body.querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", () => deleteTransponder(b.dataset.del)));
}

async function addTransponder(e) {
  e.preventDefault();
  const tp = {
    name: $("tp_name").value || null,
    type: $("tp_type").value,
    freq_mhz: parseFloat($("tp_freq").value),
    lnb_lo: parseFloat($("tp_lnb").value),
    symbol_rate: parseInt($("tp_sr").value, 10) || 0,
    pol: $("tp_pol").value,
    adapter: parseInt($("tp_adapter").value, 10) || 0,
    timeout: parseInt($("tp_timeout").value, 10) || 15,
  };
  const r = await postJSON("/api/transponders", tp);
  if (!r.ok) { alert("Error: " + r.error); return; }
  $("tp_name").value = "";
  loadTransponders();
}

async function deleteTransponder(id) {
  if (!confirm("¿Borrar este transponder?")) return;
  await postJSON("/api/transponders/" + id + "/delete", {});
  loadTransponders();
}

async function scanTransponder(id) {
  const info = $("scanInfo");
  info.textContent = "Escaneando transponder… (puede tardar unos segundos)";
  const r = await postJSON("/api/scan", { id });
  if (!r.ok) {
    info.innerHTML = `<span class="err">Error: ${esc(r.error)}</span>`;
    return;
  }
  renderScan(r);
}

/* ------------------------------ Canales / EPG --------------------------- */
function renderScan(scan) {
  const services = scan.services || [];
  $("channelCount").textContent = `(${services.length})`;
  const info = $("scanInfo");
  if (scan.network || scan.signal !== undefined) {
    info.innerHTML = `Red: <b>${esc(scan.network || "—")}</b> · Canales: ${services.length}` +
      (scan.signal !== undefined ? ` · Señal: ${esc(scan.signal)}` : "");
  } else {
    info.textContent = "";
  }
  renderChannels(scan);
}

function renderChannels(scan) {
  const services = (scan && scan.services) || [];
  const body = $("channelBody");
  if (!services.length) {
    body.innerHTML = '<tr><td colspan="6" class="muted">Sin canales (escanea un transponder)</td></tr>';
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
        <td>${typeTag(s.type)}</td>
        <td>${esc(s.now || "—")}</td>
        <td>${esc(s.next || "—")}</td>
      </tr>`).join("");
  body.innerHTML = rows;
}

async function loadChannels() {
  const scan = await getJSON("/api/channels");
  renderScan(scan);
}

/* ------------------------------- Newcamd -------------------------------- */
async function loadNewcamd() {
  const nc = await getJSON("/api/newcamd");
  $("nc_host").value = nc.host || "";
  $("nc_port").value = nc.port || 5000;
  $("nc_user").value = nc.user || "";
  $("nc_pass").value = nc.pass || "";
  $("nc_deskey").value = nc.deskey || "";
  $("ncInfo").textContent = "Guardado: " + (nc.host || "—") + ":" + (nc.port || "—") +
    " · usuario " + (nc.user || "—");
}

async function saveNewcamd(e) {
  e.preventDefault();
  const nc = {
    host: $("nc_host").value,
    port: parseInt($("nc_port").value, 10) || 5000,
    user: $("nc_user").value,
    pass: $("nc_pass").value,
    deskey: $("nc_deskey").value.replace(/[^0-9a-fA-F]/g, ""),
  };
  const r = await postJSON("/api/newcamd", nc);
  if (!r.ok) { alert("Error: " + r.error); return; }
  loadNewcamd();
}

/* ------------------------------- Streaming ------------------------------ */
function renderStreamSelect() {
  const sel = $("st_tp");
  sel.innerHTML = transponders.map((t) =>
    `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("")
    || '<option value="">— sin transponders —</option>';
}

async function loadStream() {
  const st = await getJSON("/api/stream/status");
  $("stInfo").innerHTML = st.running
    ? `<span class="ok">● STREAMING</span> (pid ${esc(st.pid)}) · UDP: ${esc(st.udp || "—")} · HTTP: ${esc(st.http_port || "—")}`
    : "Streaming detenido.";
}

async function startStream(e) {
  e.preventDefault();
  const tp = transponders.find((t) => t.id === $("st_tp").value);
  if (!tp) { alert("Agrega un transponder primero"); return; }
  if (!confirm("¿Iniciar el stream (descifrar + salida UDP)?")) return;
  const r = await postJSON("/api/stream/start", {
    transponder: tp,
    newcamd: null,
    udp: [$("st_udp").value].filter(Boolean),
    http_port: parseInt($("st_http").value, 10) || 9981,
  });
  if (!r.ok) { alert("Error: " + r.error); return; }
  loadStream();
}

async function stopStream() {
  const r = await postJSON("/api/stream/stop", {});
  if (!r.ok) { alert("Error: " + r.error); return; }
  loadStream();
}

/* -------------------------------- Init ---------------------------------- */
$("tpForm").addEventListener("submit", addTransponder);
$("ncForm").addEventListener("submit", saveNewcamd);
$("stForm").addEventListener("submit", startStream);
$("stStop").addEventListener("click", stopStream);
$("filter").addEventListener("input", loadChannels);

loadStatus();
loadTransponders();
loadChannels();
loadNewcamd();
loadStream();
setInterval(loadStream, 5000);
