"use strict";
const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

/* ---------------- procedural pixel textures (original, generated at runtime) ---------------- */
function rng(seed) { return () => ((seed = (seed * 16807) % 2147483647) / 2147483647); }
function tile(colors, seed, size = 16) {
  const c = document.createElement("canvas"); c.width = c.height = size;
  const g = c.getContext("2d"), r = rng(seed);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    g.fillStyle = colors[Math.floor(r() * colors.length)]; g.fillRect(x, y, 1, 1);
  }
  return `url(${c.toDataURL()})`;
}
function drawCube() {
  const g = $("logoCube").getContext("2d"), r = rng(7);
  const top = ["#86c24c", "#6fae3b", "#4f8a2b"], side = ["#6b4a2f", "#5a3d26", "#7a5636"];
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
    const pal = y < 4 || (y < 6 && r() < 0.5) ? top : side;
    g.fillStyle = pal[Math.floor(r() * pal.length)]; g.fillRect(x, y, 1, 1);
  }
  g.fillStyle = "rgba(0,0,0,.25)"; g.fillRect(12, 0, 4, 16);
}
document.documentElement.style.setProperty("--dirt-tex", tile(["#1b140e", "#21180f", "#271c12", "#1e160d", "#2c2015"], 11));
document.documentElement.style.setProperty("--stone-tex", tile(["#565659", "#5c5c60", "#505053", "#616165", "#4b4b4e"], 23));
drawCube();

/* ---------------- console ---------------- */
function line(text, level = "info") {
  const el = $("console"), atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  const d = document.createElement("div");
  d.className = "l-" + level; d.textContent = text;
  el.appendChild(d);
  while (el.childNodes.length > 1500) el.removeChild(el.firstChild);
  if (atBottom) el.scrollTop = el.scrollHeight;
}
const ago = (s) => s == null ? "-" : s < 90 ? `${s}s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;

/* ---------------- modal ---------------- */
function modal(title, html, buttons) {
  $("mTitle").textContent = title;
  $("mBody").innerHTML = html;
  const box = $("mButtons"); box.innerHTML = "";
  for (const b of buttons) {
    const el = document.createElement("button");
    el.className = "btn " + (b.cls || ""); el.textContent = b.text;
    el.onclick = () => { if (!b.keep) closeModal(); b.do && b.do(); };
    box.appendChild(el);
  }
  $("modal").hidden = false;
  box.lastChild && box.lastChild.focus();
}
const closeModal = () => { $("modal").hidden = true; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* ---------------- status ---------------- */
let phase = "idle", lastStatus = null, beatOk = null;

function renderIps(ips, port) {
  const box = $("ipList"); box.innerHTML = "";
  for (const ip of ips || []) {
    const addr = port && port !== 25565 ? `${ip}:${port}` : ip;
    const b = document.createElement("button"); b.className = "ip"; b.textContent = addr; b.title = "Click to copy";
    b.onclick = () => { navigator.clipboard.writeText(addr); b.classList.add("copied"); setTimeout(() => b.classList.remove("copied"), 1500); };
    box.appendChild(b);
  }
}

function render(s) {
  if (s) lastStatus = s;
  s = lastStatus || {};
  const lamp = $("lamp"), title = $("stateTitle"), sub = $("stateSub");
  const show = (id, on) => { $(id).hidden = !on; };
  let lampCls = "lamp-off", t = "CHECKING...", st = "";

  if (s.error && phase === "idle") { t = "OFFLINE"; st = "Can't reach Google Drive: " + s.error; lampCls = "lamp-red"; }
  else if (phase === "starting") { t = "STARTING..."; st = "Claiming the server and loading the world."; lampCls = "lamp-gold"; }
  else if (phase === "stopping") { t = "STOPPING..."; st = "Saving the world to Drive. Don't close the laptop."; lampCls = "lamp-gold"; }
  else if (phase === "saving") { t = "SAVE FAILED"; st = "World is safe on this laptop. Retry when online."; lampCls = "lamp-red"; }
  else if (phase === "running" || s.mine) { t = "YOU ARE HOSTING"; st = "Keep this window open while people play."; lampCls = "lamp-green"; }
  else if (s.state === "running") { t = "HOSTED BY"; st = s.host || "someone"; lampCls = "lamp-green"; }
  else if (s.state === "stale") { t = "HOST NOT RESPONDING"; st = `${s.host || "Someone"} stopped sending heartbeats ${ago(s.heartbeat_age)}.`; lampCls = "lamp-red"; }
  else if (s.state === "idle") { t = "IDLE"; st = "Nobody is hosting. You can start the server."; lampCls = "lamp-idle"; }

  lamp.className = "lamp " + lampCls; title.textContent = t; sub.textContent = st;

  const hostingHere = phase === "running";
  const someoneElse = s.state === "running" && !s.mine && phase === "idle";
  show("joinBox", (hostingHere || someoneElse) && (s.ip || []).length);
  renderIps(s.ip, s.port);

  $("fWorld").textContent = s.world ? `save #${s.world.gen} by ${s.world.saved_by}` : (s.state ? "new world (none saved yet)" : "-");
  $("fSaved").textContent = s.world ? ago(s.world.age) : "-";
  $("fVer").textContent = s.mc_version || "-";
  $("fPlayers").textContent = (s.players || []).length ? s.players.join(", ") : (s.state === "running" ? "nobody online" : "-");
  $("fBeat").textContent = hostingHere ? (beatOk === false ? "trouble (game unaffected)" : beatOk ? "ok" : "starting") :
    s.state === "running" ? ago(s.heartbeat_age) : "-";

  show("btnStart", phase === "idle");
  $("btnStart").disabled = someoneElse || !!s.error;
  $("btnStart").textContent = s.state === "stale" ? "TAKE OVER" : someoneElse ? "JOIN THEM INSTEAD" : "START HOSTING";
  show("btnStop", phase === "running");
  show("btnSave", phase === "running");
  show("btnRetry", phase === "saving");
  show("cmdForm", phase === "running");
  $("btnSettings").disabled = phase !== "idle";
  if (s.new_world && phase === "idle" && s.state !== "running") {
    $("stateSub").textContent += ` NEW WORLD queued (seed: ${s.new_world.seed || "random"}).`;
  }
}

async function poll() {
  try { render(await api().status()); } catch (e) { /* ignore */ }
}

/* ---------------- events from Python ---------------- */
window.onEvent = (kind, d) => {
  switch (kind) {
    case "log": line(d.text, d.level); break;
    case "console": line(d.line, "srv"); break;
    case "phase": phase = d.phase; render(); poll(); break;
    case "heartbeat": beatOk = d.ok; if (!d.ok) line("Heartbeat hiccup: " + d.err + " (game unaffected)", "warn"); render(); break;
    case "busy": $("busyText").textContent = d.text || ""; if (!d.text) $("progress").hidden = true; break;
    case "progress": {
      $("progress").hidden = false;
      const pct = d.total ? Math.round((d.done / d.total) * 100) : 0;
      $("progressBar").style.width = pct + "%";
      $("progressText").textContent = `${pct}%  ${(d.done / 1e6).toFixed(1)} / ${(d.total / 1e6).toFixed(1)} MB`;
      if (pct >= 100) setTimeout(() => { $("progress").hidden = true; }, 800);
      break;
    }
    case "saved": poll(); break;
    case "error": line(d.text, "error"); poll(); break;
    case "host_busy": hostBusy(d); break;
    case "need_java": modal("JAVA NEEDED", `<p>This Minecraft version needs <b>Java ${d.major}</b>, which isn't on this laptop.</p><p class="hint">BlockHost can install the free Eclipse Temurin build for you (a few minutes, may ask for permission).</p>`,
      [{ text: "LATER" }, { text: "INSTALL JAVA", cls: "btn-green", do: () => api().install_java(d.major) }]); break;
    case "fenced": modal("LOCK LOST", `<p>Someone else took over the server while yours was running (your laptop probably lost internet for a while).</p><p class="hint">To protect their world, your saves go to a separate CONFLICT file in Drive. Please stop your server.</p>`,
      [{ text: "OK" }]); break;
    case "confirm_close": confirmClose(d.phase); break;
    case "need_eula": phase = "idle"; render();
      modal("MINECRAFT EULA", `<p>To run a Minecraft server you must accept Mojang's End User License Agreement.</p>
        <p><a href="#" onclick="api().open_url('https://aka.ms/MinecraftEULA'); return false;" style="color:var(--grass-300)">Read the EULA</a></p>`,
        [{ text: "CANCEL" }, { text: "I AGREE", cls: "btn-green", do: async () => { await api().accept_eula(); phase = "starting"; render(); api().start(false); } }]);
      break;
  }
};

function hostBusy(d) {
  const ips = (d.ip || []).map((ip) => `<div class="ip">${esc(ip)}</div>`).join("");
  if (d.race) {
    modal("TOO SLOW", `<p>${esc(d.host)} clicked START at the same moment and got the server.</p>${ips}<p class="hint">Join their server instead.</p>`, [{ text: "OK" }]);
  } else if (d.stale) {
    modal("HOST NOT RESPONDING", `<p><b>${esc(d.host)}</b> claimed the server but hasn't sent a heartbeat for ${Math.round(d.age / 60)} min. Their laptop probably crashed or lost internet.</p>
      <p class="hint">Taking over uses the last world saved to Drive. Anything they played since their last auto-save is lost.</p>`,
      [{ text: "CANCEL" }, { text: "TAKE OVER", cls: "btn-red", do: () => api().start(true) }]);
  } else {
    modal("ALREADY RUNNING", `<p><b>${esc(d.host)}</b> is hosting right now.</p>${ips}<p class="hint">Join them in Minecraft: Multiplayer &gt; Add Server.</p>`, [{ text: "OK" }]);
  }
  poll();
}

function confirmClose(ph) {
  if (ph === "saving") {
    modal("WORLD NOT SAVED", `<p>The last save to Drive failed. If you quit now, others will play an <b>older</b> world until you open BlockHost again on this laptop.</p>`,
      [{ text: "QUIT ANYWAY", cls: "btn-red", do: () => api().force_quit() }, { text: "RETRY SAVE", cls: "btn-green", do: () => api().stop_and_quit() }]);
  } else if (ph === "running") {
    modal("STOP SERVER?", `<p>You're hosting. BlockHost will stop the server, save the world to Drive, then close.</p>`,
      [{ text: "CANCEL" }, { text: "STOP, SAVE & QUIT", cls: "btn-green", do: () => api().stop_and_quit() }]);
  } else {
    modal("PLEASE WAIT", `<p>BlockHost is busy (${esc(ph)}). Wait for it to finish, then quit.</p>`, [{ text: "OK" }]);
  }
}

/* ---------------- setup screens ---------------- */
function showSetup(which) {
  for (const id of ["setupPick", "setupJoin", "setupCreate"]) $(id).hidden = id !== which;
}
document.querySelectorAll("[data-back]").forEach((b) => (b.onclick = () => showSetup("setupPick")));
$("goJoin").onclick = () => showSetup("setupJoin");
$("goCreate").onclick = () => showSetup("setupCreate");
$("joinBrowse").onclick = async () => { const p = await api().pick_group_file(); if (p) $("joinPath").value = p; };
$("joinGo").onclick = async () => {
  $("joinErr").textContent = "Checking...";
  const r = await api().join_group($("joinPath").value, $("joinPass").value);
  if (r.ok) location.reload(); else $("joinErr").textContent = r.error;
};
$("createGo").onclick = async () => {
  $("createErr").textContent = "A browser tab opened. Sign in with Google and allow access...";
  $("createGo").disabled = true;
  const r = await api().create_group($("cId").value, $("cSecret").value, $("cName").value, $("cPass").value, $("cVer").value, $("cOnline").checked);
  $("createGo").disabled = false;
  if (!r.ok) { $("createErr").textContent = r.error; return; }
  modal("GROUP CREATED", `<p>Your group file was saved to:</p><p><code>${esc(r.path)}</code></p><p class="hint">Send that file and the password to your friends (separately, e.g. file on Drive, password by message). Anyone with both can host.</p>`,
    [{ text: "DONE", cls: "btn-green", do: () => location.reload() }]);
};

/* ---------------- main buttons ---------------- */
$("btnStart").onclick = () => {
  if (lastStatus && lastStatus.state === "stale") return hostBusy({ host: lastStatus.host, stale: true, age: lastStatus.heartbeat_age });
  phase = "starting"; render(); api().start(false);
};
$("btnStop").onclick = () => api().stop();
$("btnSave").onclick = () => api().save_now();
$("btnRetry").onclick = () => api().retry_save();
$("btnQuit").onclick = () => api().quit();
$("cmdForm").onsubmit = (e) => {
  e.preventDefault();
  const v = $("cmdInput").value.trim(); if (!v) return;
  line("> " + v, "warn"); api().command(v); $("cmdInput").value = "";
};

/* ---------------- settings ---------------- */
$("btnSettings").onclick = async () => {
  const b = await api().boot();
  $("sRam").value = b.settings.ram_gb; $("sAuto").value = b.settings.autosave_min;
  $("sVer").value = (lastStatus && lastStatus.mc_version) || "";
  $("sErr").textContent = ""; $("settingsModal").hidden = false;
};
$("sClose").onclick = () => { $("settingsModal").hidden = true; };
$("sSave").onclick = async () => {
  const s = await api().save_settings({ ram_gb: $("sRam").value, autosave_min: $("sAuto").value });
  $("sRam").value = s.ram_gb; $("sAuto").value = s.autosave_min; $("sErr").textContent = "Saved.";
};
$("sVerGo").onclick = async () => { const r = await api().set_version($("sVer").value); $("sErr").textContent = r.ok ? "Version changed." : r.error; poll(); };
$("sRestore").onclick = async () => { const r = await api().restore_previous(); $("sErr").textContent = r.ok ? "Previous save restored." : r.error; poll(); };
$("sFolder").onclick = () => api().open_folder();
$("sLeave").onclick = async () => { const r = await api().leave_group(); if (r.ok) location.reload(); else $("sErr").textContent = r.error; };
/* ---------------- world screen ---------------- */
const TYPE_NAMES = { normal: "Normal", large_biomes: "Large Biomes", amplified: "Amplified", flat: "Flat" };

function fillWorld(s) {
  const w = s.world_settings || {};
  $("wDifficulty").value = w.difficulty || "easy";
  $("wGamemode").value = w.gamemode || "survival";
  $("wPvp").checked = !!w.pvp; $("wKeepInv").checked = !!w.keep_inventory; $("wHardcore").checked = !!w.hardcore;
  $("wMotd").value = w.motd || ""; $("wMax").value = w.max_players ?? 20;
  $("wSpawn").value = w.spawn_protection ?? 16; $("wView").value = w.view_distance ?? 10;
  $("wWhitelist").checked = !!w.whitelist;
  $("wSeed").textContent = s.seed || "unknown (host once to see it)";
  const busy = s.state === "running" || phase !== "idle";
  $("wLocked").hidden = !busy;
  for (const id of ["wSave", "wNew", "wArchive"]) $(id).disabled = busy;
  const nw = s.new_world;
  $("wPending").hidden = !nw; $("wNewForm").hidden = !!nw;
  if (nw) {
    $("wPending").innerHTML = `NEW WORLD QUEUED: seed <b>${esc(nw.seed || "random")}</b>, type <b>${esc(TYPE_NAMES[nw.level_type] || nw.level_type)}</b>.<br>It is created the next time anyone hosts.<br>`;
    const b = document.createElement("button"); b.className = "btn"; b.textContent = "CANCEL NEW WORLD"; b.disabled = busy;
    b.onclick = async () => worldResult(await api().cancel_new_world(), "New world cancelled.");
    $("wPending").appendChild(b);
  }
  $("wArchiveRow").hidden = !s.has_archive;
}

async function openWorld() {
  $("wErr").textContent = "Loading...";
  $("worldModal").hidden = false;
  const s = await api().status();
  if (s.error) { $("wErr").textContent = s.error; return; }
  lastStatus = s; fillWorld(s); $("wErr").textContent = "";
}

async function worldResult(r, okText) {
  $("wErr").textContent = r.ok ? okText : r.error;
  if (r.ok) { const s = await api().status(); lastStatus = s; fillWorld(s); render(); }
}

$("btnWorld").onclick = openWorld;
$("wClose").onclick = () => { $("worldModal").hidden = true; };
$("wSave").onclick = async () => worldResult(await api().set_world_settings({
  difficulty: $("wDifficulty").value, gamemode: $("wGamemode").value,
  pvp: $("wPvp").checked, keep_inventory: $("wKeepInv").checked, hardcore: $("wHardcore").checked,
  motd: $("wMotd").value, max_players: $("wMax").value, spawn_protection: $("wSpawn").value,
  view_distance: $("wView").value, whitelist: $("wWhitelist").checked,
}), "Saved. Applies the next time anyone hosts.");
$("wNew").onclick = () => {
  const seed = $("wNewSeed").value.trim(), type = $("wType").value;
  $("worldModal").hidden = true;
  modal("START A NEW WORLD?", `<p>The next host gets a fresh <b>${esc(TYPE_NAMES[type])}</b> world with seed <b>${esc(seed || "random")}</b>.</p>
    <p class="hint">Your current world is kept as the OLD WORLD. WORLD &gt; SWITCH BACK TO OLD WORLD brings it back. Only one old world is kept: starting another new world later replaces it.</p>`,
    [{ text: "CANCEL", do: () => { $("worldModal").hidden = false; } },
     { text: "YES, NEW WORLD", cls: "btn-red", do: async () => { $("worldModal").hidden = false; worldResult(await api().request_new_world(seed, type), "New world queued."); } }]);
};
$("wArchive").onclick = () => {
  $("worldModal").hidden = true;
  modal("SWITCH WORLDS?", `<p>The OLD WORLD becomes the current world again. The world you have now is kept as the old world, so you can switch back any time.</p>`,
    [{ text: "CANCEL", do: () => { $("worldModal").hidden = false; } },
     { text: "SWITCH", cls: "btn-green", do: async () => { $("worldModal").hidden = false; worldResult(await api().restore_archive(), "Switched. The old world is back."); } }]);
};

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeModal(); $("settingsModal").hidden = true; $("worldModal").hidden = true; }
});

/* ---------------- boot ---------------- */
async function boot() {
  const b = await api().boot();
  if (!b.has_group) { $("setup").hidden = false; showSetup("setupPick"); return; }
  $("groupName").textContent = "/ " + b.group_name;
  $("main").hidden = false; $("btnSettings").hidden = false; $("btnWorld").hidden = false;
  line("BlockHost ready. Checking who is hosting...", "info");
  render(); poll();
  setInterval(() => { if (phase === "idle" || phase === "running") poll(); }, 20000);
}
window.addEventListener("pywebviewready", boot);
