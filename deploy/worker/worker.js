/**
 * Follow the Duck — mission dashboard (Cloudflare Worker).
 *
 * The robot POSTs mission events (camera frames, AI answers, movement
 * decisions); this Worker stores them in KV and serves a live web UI so a
 * classroom can watch what the robot saw and why it moved.
 *
 * Deploy: ./deploy.sh (first time; creates KV, sets secret, prints robot
 * .env lines). Redeploys / CI pipelines: npm run deploy (= wrangler deploy).
 * Manual dashboard paste also works: create a Worker with this file, add
 * secret FTD_TOKEN and a KV binding named FTD_LOG.
 *
 * Robot .env:
 *   FTD_TELEMETRY_URL=https://<worker-name>.<account>.workers.dev
 *   FTD_TELEMETRY_TOKEN=<same value as FTD_TOKEN>
 *
 * API (all require Bearer token or ?token=):
 *   POST /api/events   {"events":[{kind, mission_id, t, ...}]}  (max 50/batch)
 *   GET  /api/missions                 -> [{mission_id, target, state, ...}]
 *   GET  /api/events?mission=ID        -> [events, oldest first]
 *   GET  /api/health                   -> {ok:true}
 *
 * Events expire from KV after 7 days (no long-term retention by design).
 */

const TTL_S = 7 * 24 * 60 * 60;
const MAX_BATCH = 50;
const MAX_EVENT_BYTES = 400_000; // one 640x480 JPEG in base64 fits easily

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function authorized(request, url, env) {
  const header = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  const token = header || url.searchParams.get("token") || "";
  return Boolean(env.FTD_TOKEN) && token === env.FTD_TOKEN;
}

async function handleIngest(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON" }, 400);
  }
  const events = Array.isArray(body.events) ? body.events : [];
  if (events.length === 0 || events.length > MAX_BATCH) {
    return json({ error: `need 1..${MAX_BATCH} events` }, 400);
  }
  let stored = 0;
  const metaUpdates = {};
  for (const ev of events) {
    if (typeof ev !== "object" || ev === null) continue;
    const mission = String(ev.mission_id || "");
    if (!/^[a-z0-9-]{4,40}$/.test(mission)) continue;
    const value = JSON.stringify(ev);
    if (value.length > MAX_EVENT_BYTES) continue;
    const t = Math.max(0, Math.round((Number(ev.t) || 0) * 1000));
    const key = `ev:${mission}:${String(t).padStart(13, "0")}:${crypto.randomUUID().slice(0, 8)}`;
    await env.FTD_LOG.put(key, value, { expirationTtl: TTL_S });
    stored++;
    // Merge meta fields across the whole batch (target rides on start and
    // observation events; state on start/end).
    const update = (metaUpdates[mission] = metaUpdates[mission] || {});
    if (ev.target) update.target = String(ev.target).slice(0, 80);
    if (ev.kind === "mission_start") update.state = "RUNNING";
    if (ev.kind === "mission_end") update.state = String(ev.state || "").slice(0, 20);
  }
  // One meta write per mission per batch, kept in KV *metadata* so the
  // mission list never needs per-key gets.
  for (const [mission, update] of Object.entries(metaUpdates)) {
    const metaKey = `mission:${mission}`;
    const existing = await env.FTD_LOG.getWithMetadata(metaKey);
    const meta = Object.assign({}, (existing && existing.metadata) || {}, update);
    if (!meta.first_seen) meta.first_seen = Date.now();
    meta.last_seen = Date.now();
    await env.FTD_LOG.put(metaKey, "1", { metadata: meta, expirationTtl: TTL_S });
  }
  return json({ stored });
}

async function handleMissions(env) {
  const list = await env.FTD_LOG.list({ prefix: "mission:", limit: 200 });
  const missions = list.keys.map((k) => ({
    mission_id: k.name.slice("mission:".length),
    ...(k.metadata || {}),
  }));
  missions.sort((a, b) => (b.first_seen || 0) - (a.first_seen || 0));
  return json(missions.slice(0, 50));
}

async function handleEvents(url, env) {
  const mission = url.searchParams.get("mission") || "";
  if (!/^[a-z0-9-]{4,40}$/.test(mission)) return json({ error: "bad mission" }, 400);
  const list = await env.FTD_LOG.list({ prefix: `ev:${mission}:`, limit: 500 });
  const events = [];
  // KV list is lexicographic; zero-padded timestamps keep order. Fetch in
  // small parallel chunks.
  const keys = list.keys.map((k) => k.name);
  for (let i = 0; i < keys.length; i += 20) {
    const chunk = await Promise.all(keys.slice(i, i + 20).map((k) => env.FTD_LOG.get(k)));
    for (const value of chunk) {
      if (!value) continue;
      try { events.push(JSON.parse(value)); } catch {}
    }
  }
  return json(events);
}

async function handleDeleteMission(url, env) {
  const mission = url.searchParams.get("mission") || "";
  if (!/^[a-z0-9-]{4,40}$/.test(mission)) return json({ error: "bad mission" }, 400);
  let deleted = 0;
  let cursor;
  do {
    const list = await env.FTD_LOG.list({ prefix: `ev:${mission}:`, limit: 1000, cursor });
    for (const k of list.keys) {
      await env.FTD_LOG.delete(k.name);
      deleted++;
    }
    cursor = list.list_complete ? undefined : list.cursor;
  } while (cursor);
  await env.FTD_LOG.delete(`mission:${mission}`);
  return json({ deleted });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/" || url.pathname === "/index.html") {
      return new Response(HTML, { headers: { "content-type": "text/html;charset=utf-8" } });
    }
    if (!url.pathname.startsWith("/api/")) return json({ error: "not found" }, 404);
    // Unauthenticated liveness probe so the UI can distinguish "worker is
    // down" from "token mismatch". Reveals nothing but existence.
    if (url.pathname === "/api/ping") {
      return json({ ok: true, worker: "follow-the-duck", hasToken: Boolean(env.FTD_TOKEN) });
    }
    if (!authorized(request, url, env)) return json({ error: "unauthorized" }, 401);
    if (url.pathname === "/api/health") return json({ ok: true });
    if (url.pathname === "/api/events" && request.method === "POST") {
      return handleIngest(request, env);
    }
    if (url.pathname === "/api/missions" && request.method === "GET") {
      return handleMissions(env);
    }
    if (url.pathname === "/api/events" && request.method === "GET") {
      return handleEvents(url, env);
    }
    if (url.pathname === "/api/mission" && request.method === "DELETE") {
      return handleDeleteMission(url, env);
    }
    return json({ error: "not found" }, 404);
  },
};

const HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Follow the Duck — Mission Log</title>
<style>
  :root { --bg:#0e1116; --card:#161b24; --line:#232b38; --text:#dbe4f0;
          --dim:#8694a8; --accent:#ffc53d; --ok:#4ade80; --bad:#f87171; }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--bg); color:var(--text);
         font:15px/1.5 system-ui, -apple-system, sans-serif; }
  header { display:flex; gap:10px; align-items:center; flex-wrap:wrap;
           padding:12px 18px; border-bottom:1px solid var(--line);
           position:sticky; top:0; background:var(--bg); z-index:2; }
  h1 { font-size:17px; margin-right:auto; } h1 .duck { margin-right:6px; }
  select, input, button { background:var(--card); color:var(--text);
    border:1px solid var(--line); border-radius:8px; padding:7px 10px;
    font:inherit; }
  select { max-width:320px; }
  button { cursor:pointer; }
  #status { display:inline-flex; align-items:center; gap:7px; padding:5px 12px;
    border-radius:99px; border:1px solid var(--line); font-size:13px; }
  #status .dot { width:9px; height:9px; border-radius:99px; background:var(--dim); }
  #status.ok .dot { background:var(--ok); }
  #status.bad .dot { background:var(--bad); }
  #status.wait .dot { background:var(--accent); animation:pulse 1s infinite; }
  @keyframes pulse { 50% { opacity:.3; } }
  #banner { display:none; margin:14px auto 0; max-width:860px;
    background:#3a1d1d; border:1px solid #6b2f2f; color:#ffd9d9;
    border-radius:10px; padding:12px 16px; font-size:14px; white-space:pre-line; }
  #banner.show { display:block; }
  #banner code { background:#00000040; padding:1px 6px; border-radius:5px; }
  main { max-width:860px; margin:0 auto; padding:18px; display:flex;
         flex-direction:column; gap:12px; }
  .card { background:var(--card); border:1px solid var(--line);
          border-radius:12px; padding:12px 14px; }
  .row { display:flex; gap:14px; flex-wrap:wrap; }
  .card img { max-width:320px; width:100%; border-radius:8px;
              border:1px solid var(--line); }
  .k { color:var(--dim); } .chip { display:inline-block; padding:2px 9px;
    border-radius:99px; font-size:12px; font-weight:600;
    background:var(--line); margin-right:6px; }
  .chip.present { background:#14432a; color:var(--ok); }
  .chip.absent { background:#432121; color:var(--bad); }
  .chip.uncertain { background:#3c3420; color:var(--accent); }
  .chip.end { background:#2a2f58; }
  .bars { display:flex; gap:10px; margin-top:6px; }
  .bar { flex:1; } .bar .k { font-size:12px; }
  .bar .track { height:7px; background:var(--line); border-radius:4px; }
  .bar .fill { height:7px; border-radius:4px; background:var(--accent); }
  .move-ok { color:var(--ok); } .move-no { color:var(--bad); }
  table { border-collapse:collapse; margin-top:6px; width:100%; font-size:13px; }
  td, th { padding:3px 10px 3px 0; text-align:left; color:var(--dim); }
  td.chosen { color:var(--accent); font-weight:600; }
  .t { color:var(--dim); font-size:12px; float:right; }
  #empty { color:var(--dim); text-align:center; padding:48px 20px;
           line-height:2; }
  #empty code { background:var(--card); border:1px solid var(--line);
                padding:2px 8px; border-radius:6px; }
  #meta { color:var(--dim); font-size:12px; text-align:center; padding:4px 0 24px; }
</style>
</head>
<body>
<header>
  <h1><span class="duck">&#129414;</span>Follow the Duck</h1>
  <span id="status" class="wait"><span class="dot"></span><span id="statustext">connecting&hellip;</span></span>
  <select id="missions" style="display:none"></select>
  <button id="export" style="display:none" title="Download this mission's events as JSON (for debugging)">&#11015; Export</button>
  <button id="del" style="display:none" title="Delete this mission's logs from the dashboard">&#128465; Delete</button>
  <label style="font-size:13px;color:var(--dim)"><input type="checkbox" id="live" checked> live</label>
  <input id="token" type="password" placeholder="access token" size="16">
  <button id="connect">Connect</button>
</header>
<div id="banner"></div>
<main id="feed"></main>
<div id="meta"></div>
<script>
const $ = (id) => document.getElementById(id);
let current = "", lastCount = -1, lastFetch = 0, authed = false;
$("token").value = localStorage.getItem("ftd_token") || "";

function setStatus(cls, text) {
  $("status").className = cls;
  $("statustext").textContent = text;
}
function banner(text) {
  $("banner").textContent = text || "";
  $("banner").className = text ? "show" : "";
}
function empty(html) { $("feed").innerHTML = '<div id="empty">' + html + "</div>"; }

async function api(path) {
  const sep = path.includes("?") ? "&" : "?";
  const resp = await fetch(path + sep + "token=" +
    encodeURIComponent($("token").value.trim()));
  if (!resp.ok) { const e = new Error("HTTP " + resp.status); e.status = resp.status; throw e; }
  return resp.json();
}

async function connect() {
  localStorage.setItem("ftd_token", $("token").value.trim());
  authed = false;
  banner("");
  setStatus("wait", "connecting\\u2026");
  let ping;
  try {
    ping = await (await fetch("/api/ping")).json();
  } catch (e) {
    setStatus("bad", "worker unreachable");
    banner("Could not reach the Worker at all. Check your network, then reload.");
    return;
  }
  if (!ping.hasToken) {
    setStatus("bad", "no secret on Worker");
    banner('This Worker has NO FTD_TOKEN secret, so it rejects everyone.\\n' +
      "Fix: Cloudflare dashboard \\u2192 Workers & Pages \\u2192 follow-the-duck \\u2192 " +
      "Settings \\u2192 Variables and Secrets \\u2192 add secret FTD_TOKEN with the value from " +
      "the robot's .env (FTD_TELEMETRY_TOKEN). Or from the Mac: " +
      "cd deploy/worker && printf '%s' '<token>' | npx wrangler secret put FTD_TOKEN");
    return;
  }
  if (!$("token").value.trim()) {
    setStatus("bad", "token needed");
    empty("Enter the access token (the robot's <code>FTD_TELEMETRY_TOKEN</code>) and press Connect.");
    return;
  }
  try {
    await api("/api/health");
  } catch (e) {
    if (e.status === 401) {
      setStatus("bad", "wrong token");
      banner("The Worker is up, but this token does not match its FTD_TOKEN secret.\\n" +
        "On the robot: grep '^FTD_TELEMETRY_TOKEN=' ~/follow-the-duck/.env \\u2014 paste that " +
        "exact value here AND into the Worker secret (Settings \\u2192 Variables and Secrets). " +
        "Watch for stray spaces/newlines when pasting.");
    } else {
      setStatus("bad", "error " + (e.status || ""));
      banner("Unexpected error talking to the Worker: " + e.message);
    }
    return;
  }
  authed = true;
  setStatus("ok", "connected");
  await loadMissions(true);
}

async function loadMissions(selectLatest) {
  if (!authed) return;
  let missions;
  try { missions = await api("/api/missions"); }
  catch (e) { setStatus("bad", "lost connection"); return; }
  setStatus("ok", "connected");
  const sel = $("missions");
  sel.style.display = "";
  $("export").style.display = missions.length ? "" : "none";
  $("del").style.display = missions.length ? "" : "none";
  const prev = current;
  sel.innerHTML = "";
  for (const m of missions) {
    const opt = document.createElement("option");
    opt.value = m.mission_id;
    const when = m.first_seen ? new Date(m.first_seen).toLocaleTimeString() : "";
    opt.textContent = (m.target || "?") + " \\u2014 " + (m.state || "\\u2026") + " \\u2014 " + when;
    sel.appendChild(opt);
  }
  if (!missions.length) {
    empty("Connected \\u2014 no missions logged yet.<br>" +
      "On the robot, run <code>ftd find \\u0022yellow duck\\u0022</code> and this page will " +
      "fill in. (The feed can lag the robot by up to a minute \\u2014 storage syncs " +
      "across Cloudflare's network.)");
    return;
  }
  const keepSelection = prev && missions.some((m) => m.mission_id === prev);
  current = (selectLatest || !keepSelection) ? missions[0].mission_id : prev;
  sel.value = current;
  await loadEvents();
}

function bar(label, p) {
  const pct = Math.round((p || 0) * 100);
  return '<div class="bar"><span class="k">' + label + " " + pct +
    '%</span><div class="track"><div class="fill" style="width:' + pct + '%"></div></div></div>';
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
}

function render(events) {
  if (events.length === lastCount) return; // nothing new: no flicker
  lastCount = events.length;
  if (!events.length) {
    empty("Mission selected, but its events have not synced here yet.<br>" +
      "They usually appear within a minute \\u2014 leave live mode on.");
    return;
  }
  const parts = [];
  for (const ev of events) {
    const t = '<span class="t">t+' + (ev.t || 0).toFixed(1) + "s</span>";
    if (ev.kind === "mission_start") {
      parts.push('<div class="card">' + t + "&#127919; Mission started: <b>find the " +
        esc(ev.target) + "</b></div>");
    } else if (ev.kind === "observation") {
      const img = ev.image_b64 ?
        '<img loading="lazy" src="data:image/jpeg;base64,' + ev.image_b64 + '">' : "";
      parts.push('<div class="card">' + t +
        '<span class="chip ' + ev.presence + '">' + ev.presence + " " +
        Math.round((ev.confidence || 0) * 100) + "%</span>" +
        '<span class="chip">pan ' + ev.pan_deg + "&deg;</span>" +
        '<span class="chip">' + esc(ev.bearing || "") + "</span>" +
        '<span class="chip">' + esc(ev.proximity || "") + "</span>" +
        '<div class="row" style="margin-top:8px">' + img +
        '<div style="flex:1;min-width:200px"><div class="k">Is the floor clear?</div>' +
        '<div class="bars">' + bar("left", (ev.floor_clear || {}).left) +
        bar("center", (ev.floor_clear || {}).center) +
        bar("right", (ev.floor_clear || {}).right) + "</div>" +
        '<div class="k" style="margin-top:8px">AI time: ' +
        ((ev.latency || {}).inference_s || 0).toFixed(2) + "s &middot; ambiguity " +
        Math.round((ev.ambiguity || 0) * 100) + "%</div></div></div></div>");
    } else if (ev.kind === "decision") {
      let rows = "";
      for (const c of ev.candidates || []) {
        rows += "<tr><td" + (c.direction === ev.chosen ? ' class="chosen"' : "") + ">" +
          c.direction + "</td><td>" + (c.floor_p * 100).toFixed(0) + "%</td><td>" +
          c.score.toFixed(2) + "</td><td>" + (c.feasible ? "yes" : "no") + "</td></tr>";
      }
      parts.push('<div class="card">' + t + "&#129504; Where should I go? chosen: <b>" +
        esc(ev.chosen || "nowhere \\u2014 blocked") + "</b>" +
        "<table><tr><th>direction</th><th>open floor</th><th>score</th><th>feasible</th></tr>" +
        rows + "</table></div>");
    } else if (ev.kind === "move") {
      parts.push('<div class="card">' + t + (ev.ok ?
        '<span class="move-ok">&#9654; moved</span>' :
        '<span class="move-no">&#9632; refused</span>') +
        " steer " + ev.steer_deg + "&deg; @ power " + ev.speed +
        ' <span class="k">(' + esc(ev.reason) +
        (ev.range_cm != null ? ", sonar " + Math.round(ev.range_cm) + " cm" : ", sonar invalid") +
        ")</span></div>");
    } else if (ev.kind === "state") {
      parts.push('<div class="card">' + t + "&#128064; <b>Target acquired!</b> bearing " +
        esc(ev.bearing || "?") + " at pan " + (ev.pan_deg || 0) + "&deg;</div>");
    } else if (ev.kind === "mission_end") {
      parts.push('<div class="card">' + t + '<span class="chip end">' + esc(ev.state) +
        "</span> " + esc(ev.message) + ' <div class="k">steps ' + ev.steps +
        " &middot; vision calls " + ev.vision_calls + " &middot; " + ev.elapsed_s + "s</div></div>");
    }
  }
  $("feed").innerHTML = parts.join("");
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}

async function loadEvents() {
  if (!current || !authed) return;
  try {
    const events = await api("/api/events?mission=" + current);
    lastFetch = Date.now();
    render(events);
  } catch (e) { /* transient; next tick retries */ }
}

$("connect").onclick = connect;
$("token").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
$("missions").onchange = () => { current = $("missions").value; lastCount = -1; loadEvents(); };

$("export").onclick = async () => {
  if (!current) return;
  try {
    const events = await api("/api/events?mission=" + current);
    const blob = new Blob([JSON.stringify(events, null, 2)],
                          { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "ftd-mission-" + current + ".json";
    a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) { banner("Export failed: " + e.message); }
};

$("del").onclick = async () => {
  if (!current) return;
  if (!confirm("Delete this mission's logs from the dashboard? (The robot is unaffected.)")) return;
  try {
    const resp = await fetch("/api/mission?mission=" + current + "&token=" +
      encodeURIComponent($("token").value.trim()), { method: "DELETE" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    current = "";
    lastCount = -1;
    await loadMissions(true);
  } catch (e) { banner("Delete failed: " + e.message); }
};

setInterval(() => {
  if (authed && $("live").checked && !document.hidden) loadMissions(false);
  if (lastFetch) {
    $("meta").textContent = "last updated " +
      Math.round((Date.now() - lastFetch) / 1000) + "s ago \\u00b7 feed may lag the robot by up to ~1 min";
  }
}, 4000);
connect();
</script>
</body>
</html>`;
