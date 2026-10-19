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
  const response = await fetch(u);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "HTTP " + response.status);
  return data;
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
let activeTab = "dashboard";
function switchTab(tab) {
  activeTab = tab;
  if (tab === "events" && !document.hidden) startEvents();
  else stopEvents();
  $("pageTitle").textContent = {
    dashboard: "Vista general",
    epg: "Canales y programación",
    newcamd: "Acceso condicional",
    m3u8: "Distribución de señal",
    events: "Eventos en vivo · sin historial",
  }[tab];
  document
    .querySelectorAll("#menu a")
    .forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
  ["dashboard", "epg", "newcamd", "m3u8", "events"].forEach((t) => {
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
  updateHealthBadges();
  // el formulario "Agregar" apunta por defecto al primer adapter libre
  const used = new Set(transponders.map((t) => t.adapter));
  const free = (adapters.find((a) => !used.has(a.adapter)) || { adapter: 0 })
    .adapter;
  $("tp_adapter").value = free;
}

function channelsTable(services, showTpCol) {
  if (!services || !services.length) return "";
  return `<div class="table-wrap"><table>
    <thead><tr>${showTpCol ? "<th>TP</th>" : ""}<th>SID</th><th>Canal</th><th>Prov.</th><th>Tipo</th><th>Ahora</th><th>Después</th><th>ECM/CW</th><th>Acciones</th></tr></thead>
    <tbody>${services
      .map(
        (s) => `<tr class="${s.enabled === false ? "channel-disabled" : ""}">
      ${showTpCol ? `<td>${esc(s._tp || "")}</td>` : ""}
      <td>${esc(s.sid)}</td><td data-channel-name>${esc(s.name)}</td><td>${esc(s.provider || "—")}</td>
      <td>${typeTag(s.type)}</td><td>${esc(s.now || "—")}</td><td>${esc(s.next || "—")}</td>
      <td><span class="ecm-badge ecm-idle" data-health="${esc(s.channel_id)}">Inactivo</span><br><small>Perfil: ${esc(s.profile_name || "Predeterminado")}</small></td>
      <td><button class="btn small" data-edit-channel="${esc(s.channel_id)}">Editar</button>
      <a class="btn small" href="/stream/channel/${encodeURIComponent(s.channel_id)}?profile=pass" target="_blank" rel="noopener">Abrir</a>
      <button class="btn small danger" data-stop-single="${esc(s.channel_id)}">Detener</button></td>
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
        <td><button class="btn small danger" data-delch="${esc(tp.transponder_id + ":" + s.sid)}" title="Borrar canal">🗑</button></td></tr>`);
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
  if (!confirm("¿Eliminar este canal de la lista y detener su sesión?")) return;
  await postJSON("/api/channels/" + sid + "/delete", {});
  loadTransponders();
  loadEpg();
}

/* ----------------------------- Newcamd ----------------------------- */
let newcamdProfiles = [];
let editingProfile = "default";
let editingChannel = null;
let streamHealth = new Map();

async function loadProfileForm(identity) {
  editingProfile = identity;
  const nc = identity
    ? await getJSON("/api/newcamd/profiles/" + encodeURIComponent(identity))
    : {};
  $("nc_name").value =
    nc.name || (identity === "default" ? "Predeterminado" : "");
  $("nc_host").value = nc.host || "";
  $("nc_port").value = nc.port || 5000;
  $("nc_user").value = nc.user || "";
  $("nc_pass").value = nc.pass || "";
  $("nc_deskey").value = nc.deskey || "";
  $("ncDelete").disabled = !identity || identity === "default";
  $("ncInfo").textContent = identity
    ? "Perfil guardado: " + (nc.name || identity)
    : "Perfil nuevo, pendiente de guardar";
}
async function loadNewcamd() {
  newcamdProfiles = await getJSON("/api/newcamd/profiles");
  $("nc_profile").innerHTML = newcamdProfiles
    .map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`)
    .join("");
  if (!newcamdProfiles.some((p) => p.id === editingProfile))
    editingProfile = "default";
  $("nc_profile").value = editingProfile;
  await loadProfileForm(editingProfile);
}
async function saveNewcamd(e) {
  e.preventDefault();
  if (
    !confirm(
      "Guardar el perfil detendrá los canales que lo utilizan. Reabre sus URLs para aplicar. ¿Continuar?",
    )
  )
    return;
  const r = await postJSON("/api/newcamd/profiles", {
    id: editingProfile,
    name: $("nc_name").value,
    host: $("nc_host").value,
    port: Number($("nc_port").value),
    user: $("nc_user").value,
    pass: $("nc_pass").value,
    deskey: $("nc_deskey").value.trim(),
  });
  if (!r.ok) throw new Error(r.error);
  editingProfile = r.id;
  await loadNewcamd();
  await loadTransponders();
  await loadEpg();
  await loadStream();
}
function updateHealthBadges(offline = false) {
  document.querySelectorAll("[data-health]").forEach((badge) => {
    const health = streamHealth.get(badge.dataset.health)?.ecm;
    badge.className = "ecm-badge ecm-" + (health?.state || "idle");
    const row = badge.closest("tr");
    if (row) {
      row.classList.toggle("channel-live", health?.state === "ok");
      row.classList.toggle("channel-fault", health?.state === "error");
    }
    badge.textContent =
      health?.label || (offline ? "Sin telemetría" : "Inactivo");
    badge.title = health
      ? `CW válidas en ${health.window_seconds}s: ${health.successes_15s} · Último resultado: ${health.last_result ?? "—"} · Latencia: ${health.latency_ms ?? "—"} ms. No certifica vídeo decodificado.`
      : "No hay una sesión activa. No se monitoriza sin abrir el canal.";
  });
}
async function editChannel(identity) {
  const channel = transponders
    .flatMap((tp) => tp.scan?.services || [])
    .find((s) => s.channel_id === identity);
  if (!channel) return;
  newcamdProfiles = await getJSON("/api/newcamd/profiles");
  editingChannel = identity;
  $("channelIdentity").textContent = identity + " · SID " + channel.sid;
  $("ch_name").value = channel.name || "";
  $("ch_group").value = channel.group || channel.provider || "";
  $("ch_number").value = channel.number || "";
  $("ch_logo").value = channel.logo || "";
  $("ch_enabled").checked = channel.enabled !== false;
  $("ch_profile").innerHTML = newcamdProfiles
    .map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`)
    .join("");
  $("ch_profile").value = channel.newcamd_profile || "default";
  $("channelEditor").showModal();
}
$("channelForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (
    !confirm("Se detendrá solo este canal para aplicar sus cambios. ¿Guardar?")
  )
    return;
  const r = await postJSON(
    "/api/channels/" + encodeURIComponent(editingChannel) + "/edit",
    {
      name: $("ch_name").value,
      group: $("ch_group").value,
      logo: $("ch_logo").value,
      number: $("ch_number").value ? Number($("ch_number").value) : null,
      enabled: $("ch_enabled").checked,
      newcamd_profile: $("ch_profile").value,
    },
  );
  if (!r.ok) throw new Error(r.error);
  $("channelEditor").close();
  await loadTransponders();
  await loadEpg();
  await loadStream();
});
$("chCancel").addEventListener("click", () => $("channelEditor").close());
$("nc_profile").addEventListener("change", () =>
  loadProfileForm($("nc_profile").value),
);
$("ncNew").addEventListener("click", async () => {
  $("nc_profile").value = "";
  await loadProfileForm(null);
  $("nc_name").focus();
});
$("ncDelete").addEventListener("click", async () => {
  if (
    !editingProfile ||
    editingProfile === "default" ||
    !confirm("¿Eliminar este perfil? No se permite si tiene canales asignados.")
  )
    return;
  const r = await postJSON(
    "/api/newcamd/profiles/" + encodeURIComponent(editingProfile) + "/delete",
    {},
  );
  if (!r.ok) throw new Error(r.error);
  editingProfile = "default";
  await loadNewcamd();
});
document.addEventListener("click", async (e) => {
  const edit = e.target.closest("[data-edit-channel]");
  if (edit) return editChannel(edit.dataset.editChannel);
  const stop = e.target.closest("[data-stop-single]");
  if (stop && confirm("¿Detener solo este canal?")) {
    await postJSON("/api/stream/stop", { channel_id: stop.dataset.stopSingle });
    await loadStream();
  }
});

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
  streamHealth = new Map((st.sessions || []).map((s) => [s.channel_id, s]));
  updateHealthBadges();
  $("metricStream").textContent = st.running ? "Activo" : "Detenido";
  $("stStart").disabled = false;
  $("stStop").disabled = !st.running;
  const sessions = st.sessions || [];
  $("stInfo").innerHTML = sessions.length
    ? sessions
        .map(
          (session) => `
    <div class="adapter">
      <span data-health="${esc(session.channel_id)}" class="ecm-badge ecm-${esc(session.ecm?.state || "idle")}">${esc(session.ecm?.label || "Sin telemetría")}</span>
      <strong>${esc(session.channel_id)}</strong> · adaptador ${esc(session.adapter)}
      <span class="${session.running ? "ok" : "err"}">${session.running ? "● Activo" : "○ Finalizado"}</span><br>
      Perfil: ${esc(session.profile_name || "Predeterminado")} · ECM: ${esc(session.ecm?.latency_ms ?? "—")} ms · CW recientes: ${esc(session.ecm?.successes_15s ?? 0)}/15 s<br>
      Mux PID ${esc(session.mux_pid)} · CA PID ${esc(session.ca_pid || "—")}<br>
      ${esc(session.clients)} clientes · ${session.pinned ? "Inicio manual" : "Bajo demanda"} ·
      TS recibido: ${(session.bytes_received / 1048576).toFixed(2)} MiB ·
      Cola: ${Math.round(session.queue_bytes / 1024)} KiB · Clientes lentos: ${esc(session.slow_clients)}<br>
      ${session.input_error ? `<span class="err">${esc(session.input_error)}</span><br>` : ""}
      <a class="btn small" href="/stream/channel/${encodeURIComponent(session.channel_id)}?profile=pass" target="_blank">URL TS</a>
      <button class="btn small" data-events-channel="${esc(session.channel_id)}">Eventos en vivo</button>
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
  if (
    !confirm(
      "¿Fijar este canal? Puede compartir el adaptador con otros canales del mismo transpondedor.",
    )
  )
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

/* Live-only event viewer. Never localStorage, sessionStorage or download. */
let eventSource = null;
let liveEvents = [];
let eventRenderTimer = null;
function renderEvents() {
  const channelNames = new Map(
    transponders.flatMap((tp) =>
      (tp.scan?.services || []).map((s) => [s.channel_id, s.name]),
    ),
  );
  const filter = $("eventsFilter").value.toLowerCase();
  const level = $("eventsLevel").value;
  const rows = liveEvents.filter(
    (event) =>
      (!level || event.level === level) &&
      (!filter ||
        `${event.channel || ""} ${channelNames.get(event.channel) || ""} adapter ${event.adapter ?? ""} ${event.message}`
          .toLowerCase()
          .includes(filter)),
  );
  $("eventsBody").innerHTML =
    rows
      .slice()
      .reverse()
      .map((event) => {
        const detail =
          event.kind === "ecm"
            ? `${event.latency_ms} ms · resultado ${event.result} · CW ${event.cw_available ? "disponible" : "no disponible"}`
            : event.kind === "stats"
              ? Object.entries(event.stats || {})
                  .map(([key, value]) => `${key}=${value}`)
                  .join(" · ")
              : "";
        return `<tr class="event-${esc(event.level)}"><td>${esc(new Date(event.time * 1000).toLocaleTimeString())}</td>
      <td>${esc(event.level)}</td><td>${esc(channelNames.has(event.channel) ? channelNames.get(event.channel) + " (" + event.channel + ")" : event.channel || event.kind)}${event.adapter != null ? " · adapter " + esc(event.adapter) : ""}</td>
      <td>${esc(event.message)}</td><td>${esc(detail)}</td></tr>`;
      })
      .join("") ||
    '<tr><td colspan="5">Esperando eventos nuevos (sin historial).</td></tr>';
}
function stopEvents() {
  if (eventRenderTimer !== null) clearTimeout(eventRenderTimer);
  eventRenderTimer = null;
  if (eventSource) eventSource.close();
  eventSource = null;
  liveEvents = [];
  $("eventsBody").replaceChildren();
  $("eventsState").textContent = "Desconectado · sin historial";
}
function startEvents() {
  if (eventSource) return;
  if (!window.EventSource) {
    $("eventsState").textContent = "Tu navegador no admite eventos en vivo";
    return;
  }
  liveEvents = [];
  renderEvents();
  $("eventsState").textContent = "Conectando…";
  const source = new EventSource("/api/events");
  eventSource = source;
  source.addEventListener("ready", () => {
    if (source !== eventSource) return;
    liveEvents = [];
    renderEvents();
    $("eventsState").textContent = "En vivo · sin grabación";
  });
  source.onmessage = (message) => {
    if (source !== eventSource || document.hidden || activeTab !== "events")
      return;
    try {
      const event = JSON.parse(message.data);
      if (
        !event ||
        typeof event.message !== "string" ||
        !["info", "warning", "error"].includes(event.level)
      )
        return;
      liveEvents.push(event);
      if (liveEvents.length > 500) liveEvents.shift();
      if (eventRenderTimer === null)
        eventRenderTimer = setTimeout(() => {
          eventRenderTimer = null;
          renderEvents();
        }, 100);
    } catch (_) {
      /* discard invalid data */
    }
  };
  source.onerror = () => {
    if (source !== eventSource) return;
    liveEvents = [];
    renderEvents();
    $("eventsState").textContent =
      "Reconectando… eventos durante el corte no se recuperan";
  };
}
$("eventsFilter").addEventListener("input", renderEvents);
$("eventsLevel").addEventListener("change", renderEvents);
$("eventsClear").addEventListener("click", () => {
  liveEvents = [];
  renderEvents();
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopEvents();
  else if (activeTab === "events") startEvents();
});
window.addEventListener("pagehide", stopEvents);
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-events-channel]");
  if (button) {
    $("eventsFilter").value = button.dataset.eventsChannel;
    switchTab("events");
    renderEvents();
  }
});

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
setInterval(
  () =>
    loadStream().catch((error) => {
      streamHealth.clear();
      updateHealthBadges(true);
      showError(error);
    }),
  2000,
);

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
    const result = await postJSON("/api/newcamd/test", {
      id: editingProfile || "default",
    });
    $("ncInfo").textContent = result.message || result.error;
  } catch (e) {
    showError(e);
  } finally {
    b.disabled = false;
  }
});
