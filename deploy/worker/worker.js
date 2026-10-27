/**
 * Follow the Duck — mission dashboard (Cloudflare Worker).
 *
 * The robot POSTs mission events (camera frames, AI answers, movement
 * decisions); this Worker stores them in KV and serves a live web UI so a
 * classroom can watch what the robot saw and why it moved.
 *
 * Deploy (dashboard): Workers & Pages -> Create Worker -> paste this file.
 *   1. Settings -> Variables -> add secret FTD_TOKEN (any long random string)
 *   2. Settings -> Bindings -> KV namespace, variable name FTD_LOG
 * Deploy (CLI): cd deploy/worker && npx wrangler deploy  (see wrangler.toml)
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

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/" || url.pathname === "/index.html") {
      return new Response(HTML, { headers: { "content-type": "text/html;charset=utf-8" } });
    }
    if (!url.pathname.startsWith("/api/")) return json({ error: "not found" }, 404);
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
  header { display:flex; gap:12px; align-items:center; flex-wrap:wrap;
           padding:14px 18px; border-bottom:1px solid var(--line);
           position:sticky; top:0; background:var(--bg); z-index:2; }
  h1 { font-size:18px; } h1 .duck { margin-right:6px; }
  select, input, button { background:var(--card); color:var(--text);
    border:1px solid var(--line); border-radius:8px; padding:7px 10px;
    font:inherit; }
  button { cursor:pointer; } button.live { border-color:var(--accent); }
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
  #empty { color:var(--dim); text-align:center; padding:40px; }
</style>
</head>
<body>
<header>
  <h1><span class="duck">&#129414;</span>Follow the Duck — Mission Log</h1>
  <select id="missions"></select>
  <button id="refresh">Refresh</button>
  <button id="live" class="live">Live: on</button>
  <input id="token" type="password" placeholder="access token" size="14">
</header>
<main id="feed"><div id="empty">Enter the access token to load missions.</div></main>
<script>
const $ = (id) => document.getElementById(id);
let live = true, current = "";
$("token").value = localStorage.getItem("ftd_token") || "";
$("token").addEventListener("change", () => {
  localStorage.setItem("ftd_token", $("token").value); loadMissions(true);
});
$("refresh").onclick = () => loadMissions(false);
$("live").onclick = () => { live = !live; $("live").textContent = "Live: " + (live ? "on" : "off"); };
$("missions").onchange = () => { current = $("missions").value; loadEvents(); };

async function api(path) {
  const token = $("token").value.trim();
  if (!token) return null;
  const sep = path.includes("?") ? "&" : "?";
  const resp = await fetch(path + sep + "token=" + encodeURIComponent(token));
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  return resp.json();
}

async function loadMissions(selectLatest) {
  try {
    const missions = await api("/api/missions");
    if (!missions) return;
    const sel = $("missions");
    const prev = current;
    sel.innerHTML = "";
    for (const m of missions) {
      const opt = document.createElement("option");
      opt.value = m.mission_id;
      const when = m.first_seen ? new Date(m.first_seen).toLocaleTimeString() : "";
      opt.textContent = (m.target || "?") + " — " + (m.state || "…") + " — " + when;
      sel.appendChild(opt);
    }
    if (missions.length) {
      current = (selectLatest || !prev) ? missions[0].mission_id : prev;
      sel.value = current;
      loadEvents();
    }
  } catch (e) { $("feed").innerHTML = '<div id="empty">' + e + "</div>"; }
}

function bar(label, p) {
  const pct = Math.round((p || 0) * 100);
  return '<div class="bar"><span class="k">' + label + " " + pct +
    '%</span><div class="track"><div class="fill" style="width:' + pct + '%"></div></div></div>';
}

function render(events) {
  const feed = $("feed");
  if (!events.length) { feed.innerHTML = '<div id="empty">No events yet.</div>'; return; }
  const parts = [];
  for (const ev of events) {
    const t = '<span class="t">t+' + (ev.t || 0).toFixed(1) + 's</span>';
    if (ev.kind === "mission_start") {
      parts.push('<div class="card">' + t + '&#127919; Mission started: <b>find the ' +
        esc(ev.target) + "</b></div>");
    } else if (ev.kind === "observation") {
      const img = ev.image_b64 ?
        '<img src="data:image/jpeg;base64,' + ev.image_b64 + '">' : "";
      parts.push('<div class="card">' + t +
        '<span class="chip ' + ev.presence + '">' + ev.presence + " " +
        Math.round((ev.confidence||0)*100) + "%</span>" +
        '<span class="chip">pan ' + ev.pan_deg + "&deg;</span>" +
        '<span class="chip">' + esc(ev.bearing||"") + "</span>" +
        '<span class="chip">' + esc(ev.proximity||"") + "</span>" +
        '<div class="row" style="margin-top:8px">' + img +
        '<div style="flex:1;min-width:200px"><div class="k">Is the floor clear?</div>' +
        '<div class="bars">' + bar("left", (ev.floor_clear||{}).left) +
        bar("center", (ev.floor_clear||{}).center) +
        bar("right", (ev.floor_clear||{}).right) + "</div>" +
        '<div class="k" style="margin-top:8px">AI time: ' +
        ((ev.latency||{}).inference_s||0).toFixed(2) + "s &middot; ambiguity " +
        Math.round((ev.ambiguity||0)*100) + "%</div></div></div></div>");
    } else if (ev.kind === "decision") {
      let rows = "";
      for (const c of ev.candidates || []) {
        rows += "<tr><td" + (c.direction === ev.chosen ? ' class="chosen"' : "") + ">" +
          c.direction + "</td><td>" + (c.floor_p*100).toFixed(0) + "%</td><td>" +
          c.score.toFixed(2) + "</td><td>" + (c.feasible ? "yes" : "no") + "</td></tr>";
      }
      parts.push('<div class="card">' + t + "&#129504; Where should I go? chosen: <b>" +
        esc(ev.chosen || "nowhere — blocked") + "</b>" +
        '<table><tr><th>direction</th><th>open floor</th><th>score</th><th>feasible</th></tr>' +
        rows + "</table></div>");
    } else if (ev.kind === "move") {
      parts.push('<div class="card">' + t + (ev.ok ?
        '<span class="move-ok">&#9654; moved</span>' :
        '<span class="move-no">&#9632; refused</span>') +
        ' steer ' + ev.steer_deg + "&deg; @ power " + ev.speed +
        ' <span class="k">(' + esc(ev.reason) +
        (ev.range_cm != null ? ", sonar " + Math.round(ev.range_cm) + " cm" : ", sonar invalid") +
        ")</span></div>");
    } else if (ev.kind === "state") {
      parts.push('<div class="card">' + t + "&#128064; <b>Target acquired!</b> bearing " +
        esc(ev.bearing || "?") + " at pan " + (ev.pan_deg||0) + "&deg;</div>");
    } else if (ev.kind === "mission_end") {
      parts.push('<div class="card">' + t + '<span class="chip end">' + esc(ev.state) +
        "</span> " + esc(ev.message) + ' <div class="k">steps ' + ev.steps +
        " &middot; vision calls " + ev.vision_calls + " &middot; " + ev.elapsed_s + "s</div></div>");
    }
  }
  feed.innerHTML = parts.join("");
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]));
}

async function loadEvents() {
  if (!current) return;
  try { render(await api("/api/events?mission=" + current) || []); } catch {}
}

setInterval(() => { if (live) loadMissions(false); }, 5000);
loadMissions(true);
</script>
</body>
</html>`;
