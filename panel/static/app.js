"use strict";

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
}
async function getJSON(u) {
  return (await fetch(u)).json();
}
async function postJSON(u, b) {
  const r = await fetch(u, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(b || {}),
  });
  return r.json();
}

function typeTag(t) {
  const radio = t === 2 || t === 10;
  return `<span class="tag ${radio ? "radio" : "tv"}">${radio ? "Radio" : "TV"}</span>`;
}
function tpInfo(t) {
  return `${(t.type || "").toUpperCase()} · ${t.freq_mhz} MHz · SR ${t.symbol_rate} · ${t.pol} · LNB ${t.lnb_lo} · adap ${t.adapter}`;
}

/* ------------------------------ Tabs ------------------------------ */
function switchTab(tab) {
  $("pageTitle").textContent = {
    dashboard: "Vista general",
    epg: "Canales y programación",
    newcamd: "Acceso condicional",
    m3u8: "Distribución de señal",
  }[tab];
  document
    .querySelectorAll("#menu a")
    .forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  ["dashboard", "epg", "newcamd", "m3u8"].forEach((t) => {
    $("tab-" + t).classList.toggle("hidden", t !== tab);
  });
}
document.querySelectorAll("#menu a").forEach((a) =>
  a.addEventListener("click", (e) => {
    e.preventDefault();
    switchTab(a.dataset.tab);
  }),
);

/* ---------------------------- Estado ---------------------------- */
async function loadStatus() {
  const s = await getJSON("/api/status");
  const live = s.linux && s.dvb2m3u8 && s.adapters?.length;
  const badge = $("statusBadge");
  badge.textContent = live
    ? "● HARDWARE DISPONIBLE · " + s.platform
    : "○ SIN HARDWARE / BINARIOS · " + s.platform;
  badge.className = "status-badge " + (live ? "live" : "demo");
  const el = $("adapters");
  el.innerHTML =
    s.adapters && s.adapters.length
      ? s.adapters
          .map(
            (a) =>
              `<div class="adapter"><div class="path">${esc(a.path)}</div><div class="fe">${esc((a.frontends || []).join(", "))}</div></div>`,
          )
          .join("")
      : "No hay adaptadores DVB.";
  $("bins").textContent =
    `dvb2m3u8: ${s.dvb2m3u8 || "no"} · tbsstream: ${s.tbsstream || "no"}`;
  adapters = s.adapters || [];
  $("metricAdapters").textContent = adapters.length;
  $("footer").textContent = `Plataforma: ${s.platform}`;
}

/* ------------------------- Transponders (dashboard) ------------------------- */
let transponders = [];
let adapters = [];

async function loadTransponders() {
  transponders = await getJSON("/api/transponders");
  $("metricTp").textContent = transponders.length;
  $("metricChannels").textContent = transponders.reduce(
    (n, t) => n + (t.scan?.services?.length || 0),
    0,
  );
  renderTransponders();
  renderSelects();
  // el formulario "Agregar" apunta por defecto al primer adapter libre
  const used = new Set(transponders.map((t) => t.adapter));
  const free = (adapters.find((a) => !used.has(a.adapter)) || { adapter: 0 })
    .adapter;
  $("tp_adapter").value = free;
}

function channelsTable(services, showTpCol) {
  if (!services || !services.length) return "";
  return `<div class="table-wrap"><table>
    <thead><tr>${showTpCol ? "<th>TP</th>" : ""}<th>SID</th><th>Canal</th><th>Prov.</th><th>Tipo</th><th>Ahora</th><th>Después</th></tr></thead>
    <tbody>${services
      .map(
        (s) => `<tr>
      ${showTpCol ? `<td>${esc(s._tp || "")}</td>` : ""}
      <td>${esc(s.sid)}</td><td>${esc(s.name)}</td><td>${esc(s.provider || "—")}</td>
      <td>${typeTag(s.type)}</td><td>${esc(s.now || "—")}</td><td>${esc(s.next || "—")}</td>
    </tr>`,
      )
      .join("")}</tbody></table></div>`;
}

function renderTransponders() {
  const list = $("tpList");
  // cada adapter = un transponder; se muestra adapter -> transponder -> canales
  const ads = [
    ...transponders.map((t) => ({
      adapter: t.adapter,
      path: "/dev/dvb/adapter" + t.adapter,
      tp: t,
    })),
    ...adapters.filter(
      (a) => !transponders.some((t) => t.adapter === a.adapter),
    ),
  ];
  if (!ads.length) {
    list.innerHTML =
      '<div class="card muted">Aún no hay transponders. Agrega uno para comenzar el escaneo.</div>';
    return;
  }
  list.innerHTML = ads
    .map((a) => {
      const tp = a.tp;
      if (!tp) {
        return `<div class="card tp-card">
        <div class="card-head">
          <h2>📡 Adapter ${esc(a.adapter)}</h2>
          <span class="muted small">${esc(a.path || "")} · sin transponder</span>
        </div>
        <div class="muted small">Agrega un transponder con Adaptador=${esc(a.adapter)} (formulario de arriba).</div>
      </div>`;
      }
      const scan = tp.scan;
      const n = scan && scan.services ? scan.services.length : 0;
      const info = scan
        ? `Red: <b>${esc(scan.network || "—")}</b> · ${n} canales` +
          (scan.signal !== undefined ? ` · señal ${esc(scan.signal)}` : "") +
          (scan.time ? ` · ${new Date(scan.time * 1000).toLocaleString()}` : "")
        : "No escaneado";
      return `<div class="card tp-card">
      <div class="card-head">
        <h2>📡 Adapter ${esc(a.adapter)} · ${esc(tp.name)}</h2>
        <div class="actions">
          <span class="muted small">${esc(tpInfo(tp))}</span>
          <button class="btn small primary" data-scan="${esc(tp.id)}">Escanear</button>
          <button class="btn small danger" data-del="${esc(tp.id)}">Borrar</button>
        </div>
      </div>
      <div class="muted small">${info}</div>
      ${scan ? channelsTable(scan.services, false) : '<div class="muted small">Pulsa "Escanear" para listar los canales de este transponder.</div>'}
    </div>`;
    })
    .join("");

  list
    .querySelectorAll("[data-scan]")
    .forEach((b) =>
      b.addEventListener("click", () => scanTransponder(b.dataset.scan, b)),
    );
  list
    .querySelectorAll("[data-del]")
    .forEach((b) =>
      b.addEventListener("click", () => deleteTransponder(b.dataset.del)),
    );
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
  if (!r.ok) {
    alert("Error: " + r.error);
    return;
  }
  $("tp_name").value = "";
  loadTransponders();
}

async function deleteTransponder(id) {
  if (!confirm("¿Borrar este transponder y sus canales?")) return;
  await postJSON("/api/transponders/" + id + "/delete", {});
  loadTransponders();
}

async function scanTransponder(id, btn) {
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Escaneando…";
  }
  const r = await postJSON("/api/scan", { id });
  if (btn) {
    btn.disabled = false;
    btn.textContent = "Escanear";
  }
  if (!r.ok) {
    alert("Error: " + r.error);
    return;
  }
  loadTransponders();
  loadEpg();
}

/* ------------------------------ EPG ------------------------------ */
let epg = [];

async function loadEpg() {
  epg = await getJSON("/api/epg");
  renderEpg();
}

function renderEpg() {
  const body = $("epgBody");
  const filter = ($("epgFilter").value || "").toLowerCase();
  let rows = [];
  for (const tp of epg) {
    for (const s of tp.services || []) {
      if (
        filter &&
        !((s.name || "") + " " + (s.provider || ""))
          .toLowerCase()
          .includes(filter)
      )
        continue;
      rows.push(`<tr>
        <td>${esc(tp.transponder)}</td><td>${esc(s.sid)}</td><td>${esc(s.name)}</td>
        <td>${esc(s.provider || "—")}</td><td>${typeTag(s.type)}</td>
        <td>${esc(s.now || "—")}</td><td>${esc(s.next || "—")}</td>
        <td><button class="btn small danger" data-delch="${esc(s.sid)}" title="Borrar canal">🗑</button></td></tr>`);
    }
  }
  $("epgCount").textContent = rows.length ? `(${rows.length})` : "";
  body.innerHTML =
    rows.join("") || '<tr><td colspan="8" class="muted">Sin EPG</td></tr>';
  body
    .querySelectorAll("[data-delch]")
    .forEach((b) =>
      b.addEventListener("click", () => deleteChannel(b.dataset.delch)),
    );
}

async function deleteChannel(sid) {
  if (!confirm("¿Eliminar este canal del EPG?")) return;
  await postJSON("/api/channels/" + sid + "/delete", {});
  loadTransponders();
  loadEpg();
}

/* ----------------------------- Newcamd ----------------------------- */
async function loadNewcamd() {
  const nc = await getJSON("/api/newcamd");
  $("nc_host").value = nc.host || "";
  $("nc_port").value = nc.port || 5000;
  $("nc_user").value = nc.user || "";
  $("nc_pass").value = nc.pass || "";
  $("nc_deskey").value = nc.deskey || "";
  $("ncInfo").textContent = `Guardado: ${nc.host}:${nc.port} · ${nc.user}`;
}
async function saveNewcamd(e) {
  e.preventDefault();
  const r = await postJSON("/api/newcamd", {
    host: $("nc_host").value,
    port: parseInt($("nc_port").value, 10) || 5000,
    user: $("nc_user").value,
    pass: $("nc_pass").value,
    deskey: $("nc_deskey").value.trim(),
  });
  if (!r.ok) {
    alert("Error: " + r.error);
    return;
  }
  loadNewcamd();
}

/* --------------------------- M3U8 / Stream --------------------------- */
function renderSelects() {
  const opts = transponders
    .map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`)
    .join("");
  $("st_tp").innerHTML =
    opts || '<option value="">— sin transponders —</option>';
  $("m3u_tp").innerHTML = '<option value="">— todos —</option>' + opts;
}

async function downloadM3u8(e) {
  e.preventDefault();
  const tid = $("m3u_tp").value;
  const base = $("m3u_base").value.trim();
  const url =
    "/api/playlist.m3u8?transponder=" +
    tid +
    "&base_url=" +
    encodeURIComponent(base);
  window.open(url, "_blank");
}

async function loadStream() {
  const st = await getJSON("/api/stream/status");
  $("metricStream").textContent = st.running ? "Activo" : "Detenido";
  $("stStart").disabled = false;
  $("stStop").disabled = !st.running;
  const sessions = st.sessions || [];
  $("stInfo").innerHTML = sessions.length
    ? sessions
        .map(
          (session) => `
    <div class="adapter">
      <strong>${esc(session.channel_id)}</strong> · adaptador ${esc(session.adapter)}
      <span class="${session.running ? "ok" : "err"}">${session.running ? "● Activo" : "○ Finalizado"}</span><br>
      Mux PID ${esc(session.mux_pid)} · CA PID ${esc(session.ca_pid || "—")}<br>
      ${esc(session.clients)} clientes · ${session.pinned ? "Inicio manual" : "Bajo demanda"} ·
      TS recibido: ${(session.bytes_received / 1048576).toFixed(2)} MiB ·
      Cola: ${Math.round(session.queue_bytes / 1024)} KiB · Clientes lentos: ${esc(session.slow_clients)}<br>
      ${session.input_error ? `<span class="err">${esc(session.input_error)}</span><br>` : ""}
      <a class="btn small" href="/stream/channel/${encodeURIComponent(session.channel_id)}?profile=pass" target="_blank">URL TS</a>
      <a class="btn small" href="/api/stream/log?channel=${encodeURIComponent(session.channel_id)}" target="_blank">Registro del canal</a>
      <a class="btn small" href="/api/stream/log?channel=${encodeURIComponent(session.channel_id)}&scope=mux" target="_blank">Registro del mux</a>
      <a class="btn small" href="/api/stream/log?channel=${encodeURIComponent(session.channel_id)}&scope=ca" target="_blank">Registro Newcamd</a>
      <button class="btn small danger" data-stop-channel="${esc(session.channel_id)}">Detener este canal</button>
    </div>`,
        )
        .join("")
    : "Sin sesiones. Abrir un canal del M3U inicia una sesión compartida.";
  $("stInfo")
    .querySelectorAll("[data-stop-channel]")
    .forEach((button) => {
      button.addEventListener("click", async () => {
        if (
          !confirm(
            "¿Detener solo este canal y desconectar sus clientes: " +
              button.dataset.stopChannel +
              "?",
          )
        )
          return;
        button.disabled = true;
        try {
          const result = await postJSON("/api/stream/stop", {
            channel_id: button.dataset.stopChannel,
          });
          if (!result.ok) throw new Error(result.error);
          await loadStream();
        } catch (error) {
          showError(error);
        } finally {
          button.disabled = false;
        }
      });
    });
}
async function startStream(e) {
  e.preventDefault();
  if (!confirm("¿Fijar este canal? Puede compartir el adaptador con otros canales del mismo transpondedor."))
    return;
  const r = await postJSON("/api/stream/start", {
    sid: $("st_sid").value ? Number($("st_sid").value) : null,
    id: $("st_tp").value,
    udp: [$("st_udp").value].filter(Boolean),
    http_port: Number($("st_http").value),
  });
  if (!r.ok) {
    alert("Error: " + r.error);
    return;
  }
  loadStream();
}
async function stopStream() {
  if (!confirm("¿Detener todas las sesiones y desconectar sus reproductores?"))
    return;
  await postJSON("/api/stream/stop", {});
  loadStream();
}

/* ------------------------------ Init ------------------------------ */
$("tpForm").addEventListener("submit", addTransponder);
$("ncForm").addEventListener("submit", saveNewcamd);
$("m3uForm").addEventListener("submit", downloadM3u8);
$("stForm").addEventListener("submit", startStream);
$("stStop").addEventListener("click", stopStream);
$("epgFilter").addEventListener("input", renderEpg);
$("toggleTpForm").addEventListener("click", () => {
  const f = $("tpForm");
  f.style.display = f.style.display === "none" ? "grid" : "none";
  $("toggleTpForm").textContent =
    f.style.display === "none" ? "mostrar" : "ocultar";
});

(async () => {
  await loadStatus();
  await loadTransponders();
  await loadEpg();
  await loadNewcamd();
  await loadStream();
})().catch(showError);
setInterval(() => loadStream().catch(showError), 5000);

function showError(error) {
  $("notice").classList.remove("hidden");
  $("notice").textContent =
    "No se pudo completar la operación: " + error.message;
}
window.addEventListener("unhandledrejection", (event) => {
  showError(event.reason);
  event.preventDefault();
});
$("ncTest").addEventListener("click", async () => {
  const b = $("ncTest");
  b.disabled = true;
  $("ncInfo").textContent = "Comprobando configuración guardada…";
  try {
    const result = await postJSON("/api/newcamd/test", {});
    $("ncInfo").textContent = result.message || result.error;
  } catch (e) {
    showError(e);
  } finally {
    b.disabled = false;
  }
});
