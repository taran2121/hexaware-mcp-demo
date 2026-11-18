// Tracker agent UI. Plain JS, no build step, no external scripts (works offline at the venue).
const $ = (s) => document.querySelector(s);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sessionId = crypto.randomUUID();
let info = null;
let wireSeq = 0;
let busy = false;

const SUGGESTIONS = [
  { tag: "Demo 4", text: "Which client needs me most this week?" },
  {
    tag: "Lab 05 · two servers, one task",
    text: "Using the files and tracker servers:\n1. Read inbox/northwind-call.md.\n2. Add each action item for client Northwind with its owner and due date. Infer priority from wording.\n3. Skip actions already added and tell me which.\n4. Write reports/northwind-status.md: open actions by owner, anything due this week, and an overall Red, Amber or Green with one line of reasoning.\n5. Rebuild the dashboard.\nAsk before writing any file outside reports/.",
  },
  { tag: "Security · slide 77", text: "Draft the weekly status for Northwind." },
  { tag: "New client · step 1", text: "Add a new client: Litware, account lead Meera, programme: mobile banking app rollout." },
  { tag: "New client · step 2", text: "Add an action for Litware: Arjun shares the release plan by Friday, high priority." },
  { tag: "New client · step 3", text: "Add call notes for Litware: Kick-off call went well and the sponsor is positive. Risk: app-store approval may take two weeks. Decision: beta release to staff in January." },
  { tag: "New client · step 4", text: "Draft the weekly status for Litware and save it to reports/." },
  { tag: "Jira · remote server with OAuth", text: "Log a defect in Jira: the Northwind status report shows the wrong due date for ACT-002. Priority high." },
  { tag: "Capstone · M5", text: "Prepare Friday's client review: list overdue actions, check each client's status, draft a short status email per client and rebuild the dashboard." },
];

function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 2600); }

// ---------- tiny markdown renderer (headings, lists, bold, code, links, tables) ----------
function md(src) {
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<i>$2</i>")
    .replace(/(^|\s)_([^_]+)_/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank">$1</a>');
  const lines = src.split("\n");
  let out = "", list = null, table = [];
  const flushList = () => { if (list) { out += `</${list}>`; list = null; } };
  const flushTable = () => {
    if (!table.length) return;
    const rows = table.filter((r) => !/^\s*\|?\s*:?-+/.test(r));
    out += "<table>" + rows.map((r, i) => "<tr>" + r.replace(/^\||\|$/g, "").split("|").map((c) => `<${i ? "td" : "th"}>${inline(c.trim())}</${i ? "td" : "th"}>`).join("") + "</tr>").join("") + "</table>";
    table = [];
  };
  for (const line of lines) {
    if (/^\s*\|.*\|\s*$/.test(line)) { flushList(); table.push(line.trim()); continue; }
    flushTable();
    let m;
    if ((m = line.match(/^(#{1,3})\s+(.*)/))) { flushList(); out += `<h${m[1].length + 1}>${inline(m[2])}</h${m[1].length + 1}>`; }
    else if ((m = line.match(/^\s*[-*]\s+(.*)/))) { if (list !== "ul") { flushList(); out += "<ul>"; list = "ul"; } out += `<li>${inline(m[1])}</li>`; }
    else if ((m = line.match(/^\s*\d+\.\s+(.*)/))) { if (list !== "ol") { flushList(); out += "<ol>"; list = "ol"; } out += `<li>${inline(m[1])}</li>`; }
    else if (line.trim() === "") { flushList(); }
    else { flushList(); out += `<p>${inline(line)}</p>`; }
  }
  flushList(); flushTable();
  return out;
}

// ---------- left panel ----------
async function loadInfo() {
  const firstLoad = !info;
  info = await (await fetch("/api/info")).json();
  if (firstLoad) {
    const brain = $("#brain");
    brain.innerHTML = "";
    for (const [key, b] of Object.entries(info.brains)) {
      const o = el("option", null, `${esc(b.label)}${b.model !== "rules" ? " · " + esc(b.model) : ""}${b.available ? "" : " (no API key)"}`);
      o.value = key;
      brain.append(o);
    }
    brain.value = info.brains.claude.available ? "claude" : "offline";
  }

  const box = $("#servers");
  box.innerHTML = "";
  for (const s of info.servers) {
    const d = el("details", "server");
    d.open = true;
    const state = s.connected ? "" : s.needs_sign_in ? "auth" : "off";
    const hidden = s.hidden_tools ? `<div class="hidden-note">+${s.hidden_tools} more tools on this server, not given to the LLM (allow-list)</div>` : "";
    d.innerHTML = `<summary><span class="dot ${state}"></span><span class="name">${esc(s.name)}</span>
      <span class="chip kind">${esc(s.transport)}</span><span class="count">${s.tools.length} tools · ${s.resources.length} res · ${s.prompts.length} prompts</span></summary>
      <div class="cmd">${s.transport === "stdio" ? "$ " : ""}${esc(s.command)}</div>
      ${s.needs_sign_in ? `<div class="signin"><span>Needs sign-in (OAuth 2.1)</span><button class="btn small" data-connect="${esc(s.name)}">Connect</button></div>` : ""}
      ${s.error ? `<div class="err">${esc(s.error)}</div>` : ""}
      <ul>${s.tools.map((t) => `<li title="${esc(t.description)}"><span class="chip ${t.read_only ? "ro" : "rw"}">${t.read_only ? "read" : "write"}</span>${esc(t.name)}</li>`).join("")}</ul>${hidden}`;
    box.append(d);
  }
  box.querySelectorAll("[data-connect]").forEach((b) => (b.onclick = () => connectServer(b.dataset.connect, b)));
  buildInspector();
}

// Slide 69: knock (401) -> find the identity provider -> you sign in -> call with a token.
async function connectServer(name, button) {
  const tab = window.open("about:blank", "_blank"); // opened now, inside the click, so it isn't blocked
  button.disabled = true; button.textContent = "Starting…";
  const r = await (await fetch(`/api/servers/${encodeURIComponent(name)}/connect`, { method: "POST" })).json();
  if (r.connected) { tab?.close(); toast(`${name} connected.`); return loadInfo(); }
  if (!r.sign_in_url) { tab?.close(); toast(r.error || "Could not start sign-in."); return loadInfo(); }
  if (tab) tab.location = r.sign_in_url; else window.open(r.sign_in_url, "_blank");
  button.textContent = "Waiting for sign-in…";
  toast(`Approve access in the ${name} tab that just opened.`);
  for (let i = 0; i < 300; i++) {
    await new Promise((res) => setTimeout(res, 2000));
    const now = await (await fetch("/api/info")).json();
    const s = now.servers.find((x) => x.name === name);
    if (s?.connected) { toast(`${name} connected: ${s.tools.length} tools.`); return loadInfo(); }
    if (s?.error && !s.needs_sign_in) { toast(s.error); return loadInfo(); }
  }
  loadInfo();
}

async function loadClients(flash = false) {
  try {
    const data = await (await fetch("/api/status")).json();
    const box = $("#clients");
    const prev = Object.fromEntries([...box.querySelectorAll(".client")].map((c) => [c.dataset.client, c.dataset.status]));
    const opened = new Set([...box.querySelectorAll(".client.open")].map((c) => c.dataset.client));
    box.innerHTML = "";
    for (const c of data.clients) {
      const k = c.status.toLowerCase();
      const d = el("div", `client ${k}${opened.has(c.client) ? " open" : ""}`);
      d.dataset.client = c.client; d.dataset.status = c.status;
      d.innerHTML = `<div class="row"><b>${esc(c.client)}</b><span class="rag ${k}">${c.status}</span></div>
        <div class="meta">${c.open} open · ${c.overdue} overdue · ${c.done} done</div>
        <ul class="reasons">${c.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`;
      d.onclick = () => d.classList.toggle("open");
      if (flash && prev[c.client] && prev[c.client] !== c.status) d.classList.add("flash");
      box.append(d);
    }
    if (!data.clients.length) box.innerHTML = '<div class="empty">No actions yet.</div>';
  } catch { $("#clients").innerHTML = '<div class="empty">Tracker server not reachable.</div>'; }
}

// ---------- right panel ----------
async function loadWire() {
  const items = await (await fetch(`/api/wire?since=${wireSeq}`)).json();
  const box = $("#wire");
  for (const w of items) {
    wireSeq = Math.max(wireSeq, w.seq);
    const d = el("details", "wire-item new");
    const err = w.response.result?.isError;
    d.innerHTML = `<summary><span class="srv ${esc(w.server)}">${esc(w.server)}</span><span class="method">${esc(w.request.method)}</span>
      <span>${esc(w.request.params?.name || w.request.params?.uri || "")}</span>${err ? '<span class="chip rw">isError</span>' : ""}
      <span class="t">#${w.seq} · ${w.at}${w.ms != null ? " · " + w.ms + " ms" : ""}</span></summary>
      <div class="lbl">Request · client → server</div><pre>${esc(JSON.stringify(w.request, null, 2))}</pre>
      <div class="lbl">Response · server → client</div><pre>${esc(JSON.stringify(w.response, null, 2))}</pre>`;
    box.prepend(d);
    setTimeout(() => d.classList.remove("new"), 1300);
  }
}

function buildInspector() {
  const sel = $("#insp-tool");
  sel.innerHTML = "";
  for (const s of info.servers) for (const t of s.tools) { const o = el("option", null, `${t.qualified}${t.read_only ? "" : "  (write)"}`); o.value = t.qualified; sel.append(o); }
  const sample = {
    tracker__add_client: { name: "Litware", account_lead: "Meera", programme: "Mobile banking app rollout" },
    tracker__list_clients: {},
    tracker__add_call_notes: { client: "Litware", notes: "- Kick-off went well.\n- Risk: app-store approval may take two weeks." },
    tracker__get_call_notes: { client: "Northwind" },
    tracker__add_action: { client: "Northwind", title: "Send revised SOW", owner: "Priya", due: "2026-11-20", priority: "urgent" },
    tracker__client_status: {}, tracker__list_actions: { client: "Northwind" }, tracker__overdue_actions: {},
    tracker__update_action: { id: "ACT-001", status: "in_progress", note: "Draft with legal" },
    files__read_text_file: { path: "/etc/hosts" }, files__list_directory: { path: "inbox" },
    files__write_file: { path: "../outside.md", content: "test" },
  };
  const show = () => {
    const t = info.servers.flatMap((s) => s.tools).find((t) => t.qualified === sel.value);
    $("#insp-desc").textContent = t ? `${t.description}\n\ninputSchema: ${JSON.stringify(t.input_schema, null, 2)}` : "";
    $("#insp-args").value = JSON.stringify(sample[sel.value] ?? {}, null, 2);
  };
  sel.onchange = show; show();
  const res = $("#insp-resources");
  res.innerHTML = "";
  for (const s of info.servers) {
    for (const r of s.resources) {
      const uris = r.template ? ["northwind", "contoso", "fabrikam"].map((c) => r.uri.replace("{client}", c)) : [r.uri];
      for (const uri of uris) {
        const b = el("button", null, esc(uri));
        b.onclick = async () => {
          const out = await fetch(`/api/resource?server=${encodeURIComponent(s.name)}&uri=${encodeURIComponent(uri)}`);
          $("#res-out").textContent = out.ok ? (await out.json()).text : (await out.json()).detail;
          loadWire();
        };
        res.append(b);
      }
    }
    for (const p of s.prompts) {
      const b = el("button", null, esc(`prompt: ${p.name}(${p.arguments.join(", ")})`));
      b.onclick = async () => {
        const out = await fetch("/api/prompt", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ server: s.name, name: p.name, arguments: { client: "Northwind" } }) });
        $("#res-out").textContent = (await out.json()).text;
        loadWire();
      };
      res.append(b);
    }
  }
}

$("#insp-run").onclick = async () => {
  let args;
  try { args = JSON.parse($("#insp-args").value || "{}"); } catch { $("#insp-out").textContent = "Arguments are not valid JSON."; return; }
  const r = await (await fetch("/api/tool", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("#insp-tool").value, arguments: args }) })).json();
  $("#insp-out").textContent = `isError: ${r.is_error}\n\n${r.structured ? JSON.stringify(r.structured, null, 2) : r.text}`;
  loadWire(); loadClients(true); loadFiles();
};

async function loadFiles() {
  for (const folder of ["reports", "inbox"]) {
    const names = await (await fetch(`/api/files?folder=${folder}`)).json();
    $(`#files-${folder}`).innerHTML = names.map((n) => folder === "reports" ? `<li><a href="/reports/${encodeURIComponent(n)}" target="_blank">${esc(n)}</a></li>` : `<li>${esc(n)}</li>`).join("") || "<li class='empty'>empty</li>";
  }
}

document.querySelectorAll(".tabs button").forEach((b) => b.onclick = () => {
  document.querySelectorAll(".tabs button, .tab").forEach((x) => x.classList.remove("active"));
  b.classList.add("active"); $(`#tab-${b.dataset.tab}`).classList.add("active");
  if (b.dataset.tab === "files") loadFiles();
});

// ---------- chat ----------
function scrollDown() { const m = $("#messages"); m.scrollTop = m.scrollHeight; }

function addUser(text) {
  $(".welcome")?.remove();
  $("#messages").append(el("div", "msg user", esc(text)));
  scrollDown();
}

function agentBubble() {
  const wrap = el("div", "msg agent");
  const brain = $("#brain").selectedOptions[0].textContent;
  wrap.append(el("div", "agent-head", `<span class="avatar">AI</span><span>Tracker agent · ${esc(brain)} · ${esc($("#role").selectedOptions[0].textContent.split("·")[0])}</span>`));
  $("#messages").append(wrap);
  return wrap;
}

async function send(text) {
  if (busy || !text.trim()) return;
  busy = true; $("#send").disabled = true;
  addUser(text);
  const wrap = agentBubble();
  const calls = {};
  let thinkingBody = null, answer = null, answerText = "", phase = null;

  const setPhase = (step, p) => {
    if (!phase || phase.dataset.step !== String(step)) {
      phase = el("div", "phase", `<span>Step ${step}</span><span class="p" data-p="think">Think</span><span class="p" data-p="act">Act</span><span class="p" data-p="observe">Observe</span>`);
      phase.dataset.step = step; wrap.append(phase); thinkingBody = null; answer = null; answerText = "";
    }
    phase.querySelectorAll(".p").forEach((x) => x.classList.toggle("on", x.dataset.p === p));
  };

  const handle = (ev) => {
    switch (ev.type) {
      case "step": setPhase(ev.step, ev.phase); break;
      case "thinking_start": {
        const d = el("details", "thinking", `<summary>Thinking</summary><div class="body"></div>`);
        wrap.append(d); thinkingBody = d.querySelector(".body"); break;
      }
      case "thinking": if (!thinkingBody) handle({ type: "thinking_start" }); thinkingBody.textContent += ev.text; break;
      case "text":
        if (!answer) { answer = el("div", "answer"); wrap.append(answer); answerText = ""; }
        answerText += ev.text; answer.innerHTML = md(answerText); break;
      case "tool_call": {
        const c = el("div", `call ${ev.status}`);
        const status = { auto: ["auto-approved · read-only", "ok"], pending: ["waiting for approval", "wait"], blocked: ["blocked by role policy", "bad"] }[ev.status];
        c.innerHTML = `<div class="call-head"><span class="srv ${esc(ev.server)}">${esc(ev.server)}</span><span class="call-name">${esc(ev.tool)}</span>
          <span class="chip ${ev.read_only ? "ro" : "rw"}">${ev.read_only ? "read" : "write"}</span><span class="call-status ${status[1]}">${status[0]}</span></div>
          <pre>${esc(JSON.stringify(ev.args, null, 2))}</pre>`;
        wrap.append(c); calls[ev.id] = c; answer = null; break;
      }
      case "approval_request": {
        const c = calls[ev.id];
        const bar = el("div", "approval", `<span>This tool changes data. Check the arguments above.</span>`);
        const yes = el("button", "btn approve small", "Approve"), no = el("button", "btn deny small", "Deny");
        const decide = async (ok) => {
          yes.disabled = no.disabled = true;
          await fetch("/api/approve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: ev.id, approved: ok }) });
          bar.remove(); c.classList.remove("pending");
          const s = c.querySelector(".call-status"); s.textContent = ok ? "approved by you" : "denied by you"; s.className = `call-status ${ok ? "ok" : "bad"}`;
        };
        yes.onclick = () => decide(true); no.onclick = () => decide(false);
        bar.append(yes, no); c.append(bar); yes.focus(); break;
      }
      case "tool_result": {
        const c = calls[ev.id]; if (!c) break;
        if (ev.is_error) { c.classList.add(ev.denied ? "denied" : "error"); const s = c.querySelector(".call-status"); if (!ev.denied) { s.textContent = "error"; s.className = "call-status bad"; } }
        const body = ev.structured ? JSON.stringify(ev.structured, null, 2) : ev.text;
        const d = el("details", null, `<summary>Result${ev.ms != null ? " · " + ev.ms + " ms" : ""}${ev.is_error ? " · isError" : ""}</summary><pre>${esc(body)}</pre>`);
        if (ev.is_error) d.open = true;
        c.append(d);
        loadWire(); loadClients(true); break;
      }
      case "usage": wrap.append(el("div", "usage", `${esc(ev.model)} · in ${ev.input} / out ${ev.output} tokens`)); break;
      case "error": wrap.append(el("div", "err-box", esc(ev.message))); break;
    }
    scrollDown();
  };

  try {
    const res = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text, brain: $("#brain").value, role: $("#role").value }),
    });
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i; while ((i = buf.indexOf("\n")) >= 0) { const line = buf.slice(0, i); buf = buf.slice(i + 1); if (line.trim()) handle(JSON.parse(line)); }
    }
  } catch (e) { handle({ type: "error", message: String(e) }); }
  phase?.querySelectorAll(".p").forEach((x) => x.classList.remove("on"));
  busy = false; $("#send").disabled = false;
  loadClients(true); loadWire(); loadFiles();
}

$("#composer").onsubmit = (e) => { e.preventDefault(); const t = $("#input").value; $("#input").value = ""; send(t); };
$("#input").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); } };

$("#btn-prompt").onclick = async () => {
  const client = prompt("weekly_status: which client?", "Northwind");
  if (!client) return;
  const r = await fetch("/api/prompt", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ server: "tracker", name: "weekly_status", arguments: { client } }) });
  $("#input").value = (await r.json()).text; $("#input").rows = 8; loadWire();
  toast("Prompt template loaded from the tracker server (prompts/get). Review, then Send.");
};

$("#btn-reset").onclick = async () => {
  await fetch("/api/demo/reset", { method: "POST" });
  $("#messages").innerHTML = ""; $("#messages").append(welcome());
  loadClients(); loadFiles(); toast("Demo reset: Northwind Red, Contoso Amber, Fabrikam Green.");
};
$("#btn-injection").onclick = async () => {
  const r = await (await fetch("/api/demo/injection", { method: "POST" })).json();
  loadClients(true); loadWire();
  toast(r.ok ? `Planted ${r.item.id}: "${r.item.title}"` : "Could not plant the injection item.");
};

function welcome() {
  const w = el("div", "welcome", `<h2>Ask the agent something that needs tools</h2><p>It plans, calls tools through the MCP client, reads the results and repeats. <b>Think → Act → Observe.</b></p><div class="suggestions"></div>`);
  const box = w.querySelector(".suggestions");
  for (const s of SUGGESTIONS) {
    const b = el("button", "suggestion", `<small>${esc(s.tag)}</small>${esc(s.text.split("\n")[0])}${s.text.includes("\n") ? " …" : ""}`);
    b.onclick = () => send(s.text);
    box.append(b);
  }
  return w;
}

(async function init() {
  $(".welcome").replaceWith(welcome());
  await loadInfo();
  loadClients(); loadWire(); loadFiles();
})();
