/* RFx Assistant front end. No build step, no dependencies: one file the reviewer can read top to bottom. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = (d) => new Intl.NumberFormat("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const money = (x) => (x == null ? "" : "₹" + nf(Math.abs(x) >= 1000 ? 0 : 2).format(x));
const big = (x) => (x == null ? "" : Math.abs(x) >= 1e7 ? "₹" + nf(2).format(x / 1e7) + " Cr" : Math.abs(x) >= 1e5 ? "₹" + nf(2).format(x / 1e5) + " L" : money(x));
const S = { state: null, comp: null, busy: {}, filter: "all" };
const STEPS = [["draft", "Draft RFx"], ["inbox", "Responses"], ["compare", "Compare"], ["ask", "Ask"]];
/* the buyer's words for a cell's status; the stored values (verified / assumed / needs_review) do not change */
const STATUS = { verified: "Clear", assumed: "Check assumption", needs_review: "Likely wrong", not_quoted: "Not quoted", no_response: "No response" };

/* which model answered; a fallback is said out loud, since the configured model is the one that was evaluated */
const modelNote = (m) => !m ? "" : m !== S.state.primary_model ? `<span class="fallback" title="Configured model: ${esc(S.state.primary_model)}">answered by fallback model ${esc(m)}</span>` : `<span class="muted">${esc(m)}</span>`;
async function api(path, opts = {}) {
  const o = { method: opts.method || (opts.body ? "POST" : "GET"), headers: {} };
  if (opts.form) o.body = opts.form;
  else if (opts.body) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(opts.body); }
  const r = await fetch(path, o);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch {} throw new Error(m); }
  return r.json();
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 3200); }
const route = () => (location.hash.replace("#", "") || "draft");
async function refresh(withComp) {
  S.state = await api("/api/state");
  if (withComp && S.state.rfx) S.comp = await api("/api/comparison");
}
async function render() {
  const r = route();
  $("#nav").innerHTML = STEPS.map(([k, l], i) => `<a href="#${k}" class="${k === r ? "on" : ""}"><span class="n">${i + 1}</span>${l}</a>`).join("");
  $("#model").textContent = S.state.model;
  $("#rfxid").textContent = S.state.rfx ? `${S.state.rfx.id} · ${S.state.rfx.status}` : "";
  $("#drawer").hidden = true;
  ({ draft: viewDraft, inbox: viewInbox, compare: viewCompare, ask: viewAsk }[r] || viewDraft)();
}
window.addEventListener("hashchange", async () => { await refresh(["compare", "inbox"].includes(route())); render(); });
$("#reset").onclick = async () => { if (confirm("Clear the RFx, responses, decisions and chats?")) { await api("/api/reset", { method: "POST" }); S.comp = null; await refresh(); location.hash = "#draft"; render(); } };

/* ---------- minimal markdown (bold, code, lists, pipe tables) ---------- */
function md(src) {
  const inline = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = []; const lines = String(src || "").split("\n"); let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^\s*\|.*\|\s*$/.test(l) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] || "")) {
      const row = (x) => x.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = row(l); i += 2; const body = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) body.push(row(lines[i++]));
      out.push(`<div class="tablewrap"><table><tr>${head.map((h) => `<th>${inline(h)}</th>`).join("")}</tr>${body.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</table></div>`);
    } else if (/^\s*([-*]|\d+\.)\s+/.test(l)) {
      const ord = /^\s*\d+\./.test(l); const items = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""));
      out.push(`<${ord ? "ol" : "ul"}>${items.map((x) => `<li>${inline(x)}</li>`).join("")}</${ord ? "ol" : "ul"}>`);
    } else if (/^#{1,4}\s/.test(l)) { out.push(`<p><b>${inline(l.replace(/^#+\s/, ""))}</b></p>`); i++; }
    else if (l.trim() === "") i++;
    else { const p = []; while (i < lines.length && lines[i].trim() !== "" && !/^\s*([-*]|\d+\.)\s+/.test(lines[i]) && !/^\s*\|.*\|\s*$/.test(lines[i])) p.push(lines[i++]); out.push(`<p>${inline(p.join(" "))}</p>`); }
  }
  return out.join("");
}

/* ---------- 1. Draft ---------- */
const DRAFT_CHIPS = [
  "I need to run the annual corrugated packaging RFQ for the Bhiwandi FC. Start from last year's contract, volumes are up about 10%.",
  "Add a quality questionnaire. ISO 9001 and an in-house lab are must-haves, rejection rate must be 2% or less, capacity at least 300 MT a month.",
  "Send it to all five approved vendors. Quotes due 10 November, award on the 20th.",
];
function viewDraft() {
  const rfx = S.state.rfx; const chat = S.state.copilot_chat;
  $("#view").innerHTML = `<div class="split">
    <section class="card chat">
      <div class="msgs" id="msgs">${chat.length ? "" : `<div class="muted">Tell the co-pilot what you need to buy. It builds the RFx on the right as you talk.</div>`}
        ${chat.map((m) => `<div class="msg ${m.role}">${esc(m.text)}${m.changes && m.changes.length ? `<span class="chg">Draft updated: ${esc(m.changes.join(", "))}</span>` : ""}${m.locked ? `<span class="chg">RFx locked: nothing was changed</span>` : ""}${m.role === "assistant" && m.model && m.model !== S.state.primary_model ? `<span class="chg">${modelNote(m.model)}</span>` : ""}</div>`).join("")}
        ${S.busy.copilot ? `<div class="msg assistant thinking">Working on the draft</div>` : ""}</div>
      ${rfx && rfx.status === "sent" ? `<div class="locked">Sent to vendors${rfx.sent_at ? " on " + esc(rfx.sent_at) : ""}. The RFx is locked: the co-pilot can answer questions about it but will not change it. To change it, Reset and start a new draft.</div>` : ""}
      <div class="chips" ${rfx && rfx.status === "sent" ? "hidden" : ""}>${DRAFT_CHIPS.map((c, i) => `<button class="chip" data-chip="${i}">${esc(c.length > 70 ? c.slice(0, 68) + "…" : c)}</button>`).join("")}</div>
      <form class="composer" id="cform"><textarea id="cin" placeholder="${rfx && rfx.status === "sent" ? "Ask about the sent RFx…" : "Describe what you need…"}"></textarea><button ${S.busy.copilot ? "disabled" : ""}>Send</button></form>
    </section>
    <section class="card" id="draft">${rfx ? draftDoc(rfx) : `<div class="empty"><p>No draft yet.</p><p>Talk to the co-pilot, or <button class="ghost" id="demo">load the finished demo RFx</button> to skip to vendor responses.</p><p>Short on time? <button class="ghost" id="snap">Open the demo with all five responses already read</button><br><span style="font-size:12px">Uses the saved output of a real extraction run. Any response can be re-read live.</span></p></div>`}</section>
  </div>`;
  const msgs = $("#msgs"); msgs.scrollTop = msgs.scrollHeight;
  const send = async (text) => {
    if (!text.trim() || S.busy.copilot) return;
    S.state.copilot_chat.push({ role: "user", text }); S.busy.copilot = true; viewDraft();
    try { await api("/api/copilot", { body: { message: text } }); } catch (e) { toast(e.message); }
    S.busy.copilot = false; await refresh(); if (route() === "draft") render();
  };
  $("#cform").onsubmit = (e) => { e.preventDefault(); send($("#cin").value); };
  $("#cin").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send($("#cin").value); } };
  document.querySelectorAll("[data-chip]").forEach((b) => (b.onclick = () => { $("#cin").value = DRAFT_CHIPS[b.dataset.chip]; $("#cin").focus(); }));
  if ($("#demo")) $("#demo").onclick = async () => { await api("/api/rfx/load_demo", { method: "POST" }); await refresh(); render(); };
  if ($("#snap")) $("#snap").onclick = async () => { try { await api("/api/demo/restore", { method: "POST" }); await refresh(true); location.hash = "#compare"; } catch (e) { toast(e.message); } };
  if ($("#sendrfx")) $("#sendrfx").onclick = async () => { try { await api("/api/rfx/send", { method: "POST" }); toast("RFx sent to vendors (stubbed outbox)"); await refresh(); location.hash = "#inbox"; } catch (e) { toast(e.message); } };
}
function draftDoc(r) {
  const total = r.lines.reduce((a, l) => a + (l.last_year_price || 0) * l.annual_qty, 0);
  return `<div class="row"><div><h2>${esc(r.title || "Untitled RFx")}</h2><div class="muted">${esc(r.id)} · quotes due ${esc(r.due_date || "not set")} · award ${esc(r.award_date || "not set")}
      ${total ? ` · last year's value on these lines ${big(total)}` : ""}</div></div><span class="spacer"></span>
      ${r.status === "sent" ? `<span class="badge b-ok">Sent to ${r.vendors.length} vendors</span>` : `<button id="sendrfx" ${r.lines.length && r.vendors.length ? "" : "disabled"}>Send to vendors</button>`}</div>
    <h3>Scope</h3><div>${esc(r.scope) || '<span class="muted">Not written yet</span>'}</div>
    <h3>Line items (${r.lines.length})</h3>
    <div class="tablewrap" style="max-height:420px"><table><tr><th>Line</th><th>Description</th><th>Board</th><th>Print</th><th>Unit</th><th class="num">Annual qty</th><th class="num" title="Calculated from dimensions and board grammage. Used only to convert per-kg quotes.">Est. weight</th></tr>
      ${r.lines.map((l) => `<tr><td>${l.id}</td><td>${esc(l.description)}</td><td class="muted">${esc(l.board_spec)}</td><td class="muted">${esc(l.print)}</td><td>${l.uom}</td><td class="num">${nf(0).format(l.annual_qty)}</td><td class="num muted">${l.est_weight_g ? l.est_weight_g + " g" : ""}</td></tr>`).join("") || `<tr><td colspan="7" class="empty">No lines yet</td></tr>`}</table></div>
    <h3>Vendor questionnaire</h3>
    ${r.questionnaire.length ? `<div class="tablewrap"><table><tr><th>#</th><th>Question</th><th>Must pass?</th><th>Pass rule</th></tr>${r.questionnaire.map((q) => `<tr><td>${q.id}</td><td>${esc(q.text)}</td><td>${q.mandatory ? '<span class="badge b-bad">Mandatory</span>' : '<span class="muted">Info</span>'}</td><td>${esc(q.pass_text || "")}</td></tr>`).join("")}</table></div>` : `<div class="muted">None yet</div>`}
    <h3>Terms</h3>${r.terms.length ? `<ul style="margin:0;padding-left:18px">${r.terms.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : `<div class="muted">None yet</div>`}
    <h3>Vendors</h3>${r.vendors.length ? r.vendors.map((v) => `<span class="att">${esc(v.name)}${v.incumbent ? ' <span class="muted">(incumbent)</span>' : ""}</span>`).join("") : `<div class="muted">None selected</div>`}`;
}

/* ---------- 2. Responses ---------- */
function viewInbox() {
  const rfx = S.state.rfx;
  if (!rfx) return ($("#view").innerHTML = `<div class="card empty">Draft and send an RFx first.</div>`);
  const resp = Object.fromEntries(S.state.responses.map((r) => [r.vendor_id, r]));
  const unread = rfx.vendors.filter((v) => resp[v.id] && resp[v.id].status !== "extracted");
  $("#view").innerHTML = `<div class="row" style="margin-bottom:14px"><div><h2>Vendor responses</h2>
      <div class="muted">Vendors reply in whatever shape they like. Each response is read by the model once; you check it on the Compare screen.</div></div><span class="spacer"></span>
      <button class="ghost" id="sim">Receive vendor replies<span class="stub">stubbed mailbox</span></button>
      <button id="readall" ${unread.length ? "" : "disabled"}>Read ${unread.length || ""} response${unread.length === 1 ? "" : "s"}</button>
      <button class="ghost" id="outbox">Sent mail (${S.state.outbox.length})</button></div>
    <div class="vendors">${rfx.vendors.map((v) => {
      const r = resp[v.id]; const em = r ? JSON.parse(r.email) : null; const busy = S.busy["x" + v.id];
      const badge = busy ? `<span class="badge b-warn thinking">Reading</span>` : !r ? `<span class="badge b-none">Awaiting reply</span>` : r.status === "extracted" ? `<span class="badge b-ok">Read</span>` : r.status === "error" ? `<span class="badge b-bad">Failed</span>` : `<span class="badge b-warn">Received, unread</span>`;
      return `<div class="card vcard"><div class="row"><h2>${esc(v.name)}</h2><span class="spacer"></span>${badge}</div>
        <div class="muted">${esc(v.city || "")}${v.incumbent ? " · incumbent" : ""}</div>
        ${em ? `<div class="mail"><b>${esc(em.subject)}</b> <span class="muted">· ${esc(em.date.slice(0, 16).replace("T", " "))}</span>\n${esc(em.body)}</div>
          <div>${em.attachments.map((a) => `<a class="att" target="_blank" href="/api/file/${v.id}/${encodeURIComponent(a)}">📎 ${esc(a)}</a>`).join("") || '<span class="muted">No attachments</span>'}</div>` : `<div class="mail muted">Nothing received yet.</div>`}
        ${r && r.error ? `<div class="flagbox needs_review">${esc(r.error)}</div>` : ""}
        <div class="row" style="margin-top:8px">${r ? `<button class="small" data-x="${v.id}" ${busy ? "disabled" : ""}>${r.status === "extracted" ? "Read again" : "Read response"}</button>` : ""}
          <button class="small ghost" data-up="${v.id}">Add a response by hand</button>
          ${r && r.model ? `<span class="muted" style="font-size:12px">read by ${modelNote(r.model)}${r.extracted_at ? " · " + new Date(r.extracted_at * 1000).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : ""}</span>` : ""}</div></div>`; }).join("")}</div>`;
  $("#sim").onclick = async () => { const r = await api("/api/inbox/simulate", { method: "POST" }); toast(r.received ? `${r.received} replies arrived` : "No new replies"); await refresh(); render(); };
  const readOne = async (vid, force) => {
    S.busy["x" + vid] = true; if (route() === "inbox") viewInbox();
    try { await api(`/api/extract/${vid}?force=${force ? "true" : "false"}`, { method: "POST" }); } catch (e) { toast(e.message); }
    S.busy["x" + vid] = false; await refresh(); if (route() === "inbox") viewInbox();
  };
  $("#readall").onclick = () => unread.forEach((v) => readOne(v.id, false));
  document.querySelectorAll("[data-x]").forEach((b) => (b.onclick = () => readOne(b.dataset.x, true)));
  document.querySelectorAll("[data-up]").forEach((b) => (b.onclick = () => uploadModal(b.dataset.up)));
  $("#outbox").onclick = () => modal(`<h2>Sent mail <span class="stub">stubbed: nothing leaves this machine</span></h2>${S.state.outbox.map((o) => `<h3>${esc(o.kind)} → ${esc(o.vendor_id)} · ${esc(o.subject)}</h3><pre>${esc(o.body)}</pre>`).join("") || '<p class="muted">Nothing sent yet.</p>'}`);
}
function uploadModal(vid) {
  modal(`<h2>Add a response from ${esc(vid)}</h2><p class="muted">Paste email text, attach files (Excel, PDF, Word, image), or both. It replaces this vendor's current response.</p>
    <textarea id="ubody" rows="5" placeholder="Email text…"></textarea><p><input type="file" id="ufiles" multiple></p><button id="ugo">Add response</button>`);
  $("#ugo").onclick = async () => {
    const f = new FormData(); f.append("vendor_id", vid); f.append("body", $("#ubody").value);
    for (const x of $("#ufiles").files) f.append("files", x);
    try { await api("/api/inbox/upload", { form: f, method: "POST" }); $("#modal").hidden = true; toast("Response added. Read it to update the comparison."); await refresh(); render(); } catch (e) { toast(e.message); }
  };
}
function modal(html) { const m = $("#modal"); m.innerHTML = `<div class="box">${html}<p style="text-align:right;margin-bottom:0"><button class="ghost" id="mclose">Close</button></p></div>`; m.hidden = false; $("#mclose").onclick = () => (m.hidden = true); m.onclick = (e) => { if (e.target === m) m.hidden = true; }; }

/* ---------- 3. Compare ---------- */
/* Progressive disclosure: the grid says where to look, the drawer says why and what to do, Details holds the rest. */
const cellKey = (v, l) => v + "|" + l;
const LEGEND = [["verified", "Nothing about this price is in doubt (a whole-column assumption such as a currency rate may still apply: see the vendor)."],
  ["assumed", "Usable, but rests on an assumption about this price, such as a calculated box weight."],
  ["needs_review", "Probably wrong or not like-for-like. Cannot win a line in an award until you resolve it."],
  ["not_quoted", "The vendor did not price this line, or you excluded it."]];
const QUALITY = { cleared: ["b-ok", "Quality cleared"], failed: ["b-bad", "Quality failed"], incomplete: ["b-warn", "Quality incomplete"], no_response: ["b-none", "No response"] };
const DONE = { confirm: "Kept as is", set_price: "Price set to", exclude: "Excluded" };
const doneLabel = (x) => (x.decision.action === "confirm" ? (x.machine_status === "needs_review" ? "Kept as is" : "Accepted") : DONE[x.decision.action] || x.decision.action);
const sevClass = (s) => ({ needs_review: "b-bad", assumed: "b-warn", verified: "b-ok" }[s] || "b-none");
function cellTitle(x) {
  if (x.decision) return `${doneLabel(x)}${x.decision.note ? ": " + x.decision.note : ""}`;
  const label = STATUS[x.cell_status || x.status] || "";
  if (x.headline) return `${label}: ${x.headline.text}`;
  return x.reason || (x.raw ? `${label}: read cleanly` : label);
}
function viewCompare() {
  const c = S.comp;
  if (!c || !c.vendors.some((v) => v.response_status === "extracted")) return ($("#view").innerHTML = `<div class="card empty">No responses have been read yet. Go to <a href="#inbox">Responses</a>.</div>`);
  const cells = Object.fromEntries(c.cells.map((x) => [cellKey(x.vendor_id, x.line_id), x]));
  S.cells = cells;
  const attention = (l) => c.vendors.some((v) => cells[cellKey(v.id, l.id)].cell_status === "needs_review");
  const lines = c.rfx.lines.filter((l) => S.filter === "all" || attention(l));
  const n = c.rfx.lines.length;
  $("#view").innerHTML = `<div class="row" style="margin-bottom:12px"><div><h2>Comparison</h2><div class="muted">₹ per RFx unit, excluding GST. Click a cell or a vendor for the evidence.</div></div><span class="spacer"></span>
      <label class="muted">USD → INR <input type="number" id="fx" step="0.05" value="${c.settings.fx.USD}" style="width:80px"></label>
      <a href="/api/export/comparison.xlsx"><button class="ghost">Export to Excel</button></a></div>
    <div class="vhead">${c.vendors.map((v) => {
      const k = (v.causes || []).length; const bad = (v.causes || []).some((x) => x.severity === "needs_review"); const q = QUALITY[v.quality] || QUALITY.no_response;
      const fb = v.model && v.model !== S.state.primary_model;
      return `<div class="card vh" data-vendor="${v.id}" title="${esc(v.name)}"><div class="name">${esc(v.short)}${v.incumbent ? ' <span class="inc" title="Incumbent">incumbent</span>' : ""}${fb ? ' <span class="fbdot" title="Read by a fallback model">●</span>' : ""}</div>
        <div class="big">${v.lines_quoted ? big(v.total_value) : "–"} <span class="muted lines">${v.lines_quoted || 0}/${n} lines</span></div>
        <div class="chips2"><span class="badge ${q[0]}">${q[1]}</span>${v.response_status !== "extracted" ? "" : k ? `<span class="badge ${bad ? "b-bad" : "b-warn"}">${k} open issue${k === 1 ? "" : "s"}</span>` : `<span class="badge b-ok">No open issues</span>`}</div></div>`; }).join("")}</div>
    <div class="row legendrow"><div class="legend">${LEGEND.map(([s, t]) => `<span title="${esc(t)}"><i class="sw ${s}"></i>${STATUS[s]}</span>`).join("")}<span title="Read from a photograph of the quote"><i class="sw photo"></i>Photo</span><span title="Lowest price on the line that can win it in an award"><b style="text-decoration:underline">1.23</b> Lowest that can win</span></div>
      <span class="spacer"></span><button class="small ghost" id="flt">${S.filter === "all" ? `Only lines likely wrong (${c.rfx.lines.filter(attention).length})` : "All lines"}</button></div>
    <div class="tablewrap grid"><table><tr><th>Line</th><th>Description</th><th class="num">Annual qty</th><th class="num">Last year</th>${c.vendors.map((v) => `<th class="vcol" data-vendor="${v.id}">${esc(v.short)}</th>`).join("")}</tr>
      ${lines.map((l) => `<tr><td>${l.id}</td><td>${esc(l.description)}</td><td class="num">${nf(0).format(l.annual_qty)} ${l.uom}</td><td class="num muted">${money(l.last_year_price)}</td>
        ${c.vendors.map((v) => { const x = cells[cellKey(v.id, l.id)]; const cs = x.cell_status || x.status;
          const isBest = c.best[l.id] === v.id && x.price != null; const photo = x.flags.some((f) => f.code === "PHOTO");
          const marks = (x.decision ? '<span class="dec">✓</span>' : "") + (x.changed_by_reply ? '<span class="chg-r">↻</span>' : "");
          const body = x.price != null ? `<b>${nf(2).format(x.price)}</b>${marks}` : x.status === "needs_review" ? `<span class="noprice">no usable price</span>${marks}` : (x.status === "no_response" ? "no response" : "not quoted");
          return `<td class="cell ${cs} ${isBest ? "best" : ""} ${photo ? "photo" : ""}" title="${esc(cellTitle(x))}" data-cell="${cellKey(v.id, l.id)}">${body}</td>`; }).join("")}</tr>`).join("")}</table></div>
    <details class="how" style="margin-top:16px"><summary>Decision log (${S.state.audit.length})</summary><div class="tablewrap" style="max-height:260px"><table>${S.state.audit.map((a) => `<tr><td class="muted" style="width:150px">${new Date(a.ts * 1000).toLocaleString("en-IN")}</td><td style="width:170px">${esc(a.event.replace(/_/g, " "))}</td><td>${esc(a.detail)}</td></tr>`).join("")}</table></div></details>`;
  document.querySelectorAll("[data-cell]").forEach((td) => (td.onclick = () => openCell(td.dataset.cell)));
  document.querySelectorAll("[data-vendor]").forEach((el) => (el.onclick = () => openVendor(el.dataset.vendor)));
  $("#flt").onclick = () => { S.filter = S.filter === "all" ? "attention" : "all"; viewCompare(); };
  $("#fx").onchange = async (e) => { await api("/api/settings", { body: { fx: { USD: +e.target.value } } }); await refresh(true); viewCompare(); };
}
async function rerender(reopen) { await refresh(true); if (route() === "compare") { viewCompare(); if (reopen) await reopen(); } }
async function groupAct(vid, cause, action, note, reopen, lines) {
  try { const r = await api("/api/decision/group", { body: { vendor_id: vid, cause, action, note, lines: lines || null } });
    toast(`${r.lines.length} line${r.lines.length === 1 ? "" : "s"} decided${r.skipped && r.skipped.length ? `; ${r.skipped.join(", ")} left for one-by-one review (suggested price)` : ""}`); } catch (e) { return toast(e.message); }
  rerender(reopen);
}
/* the buttons that resolve every open cell of a vendor sharing one reason; reason typed only where it is required */
/* L01, L02, L03, L09 -> "L01 to L03, L09" */
function span(ids) {
  const n = (x) => +x.slice(1); const out = [];
  [...ids].sort().forEach((x) => { const r = out[out.length - 1]; if (r && n(x) === n(r[r.length - 1]) + 1) r.push(x); else out.push([x]); });
  return out.map((r) => (r.length === 1 ? r[0] : r.length === 2 ? r.join(", ") : `${r[0]} to ${r[r.length - 1]}`)).join(", ");
}
/* the one fact that matters for a cause, per line, so the buyer can choose which lines to approve */
function lineFact(code, x, line) {
  const h = x.headline || {};
  if (h.suggested_price != null) return `suggested ${money(h.suggested_price)}`;
  if (code === "WEIGHT") return line.est_weight_g ? `${line.est_weight_g} g` : "";
  if (code === "LAST_YEAR") return `last year ${money(line.last_year_price)}`;
  return "";
}
/* group actions for one issue. In the vendor panel (pick=true) the lines can be chosen; in the cell drawer it is all or one. */
function groupButtons(v, g, id, pick) {
  if (!g || g.kind !== "cells" || g.lines.length < (pick ? 1 : 2)) return "";
  const plain = g.lines.length - g.suggested; const nr = g.severity === "needs_review";
  const open = pick && S.pick && S.pick.vid === v.id && S.pick.code === g.code;
  const rows = pick ? g.lines.map((lid) => { const x = S.cells[cellKey(v.id, lid)]; const line = S.comp.rfx.lines.find((l) => l.id === lid);
    return `<tr><td><input type="checkbox" value="${lid}" data-sug="${x.headline && x.headline.suggested_price != null ? 1 : 0}" checked></td><td>${lid}</td><td>${esc(line.description)}</td>
      <td class="num">${x.price != null ? money(x.price) : ""}</td><td class="muted">${esc(lineFact(g.code, x, line))}</td></tr>`; }).join("") : "";
  return `<div class="grp" data-g="${id}" data-code="${g.code}" data-n="${g.lines.length}" data-nr="${nr ? 1 : 0}" data-short="${esc(v.short)}">
    ${pick ? `<details class="pick" ${open ? "open" : ""}><summary>Choose lines</summary>
      <div class="row small pickbar"><label><input type="checkbox" class="pall" checked> All</label><span class="pcount muted">${g.lines.length} of ${g.lines.length} selected</span></div>
      <div class="tablewrap"><table class="picktbl">${rows}</table></div></details>` : ""}
    <div class="row gbtns">
      ${plain >= (pick ? 1 : 2) ? `<button class="small ${nr ? "ghost" : ""}" data-gact="${nr ? "keep" : "confirm"}"></button>` : ""}
      <button class="small" data-gact="use_suggested" hidden></button></div>
    <div class="reason" hidden><input type="text" placeholder="Why keep these prices? (kept in the decision log)"><button class="small">Keep with this reason</button></div></div>`;
}
function wireGroups(root, vid, reopen) {
  root.querySelectorAll(".grp").forEach((el) => {
    const code = el.dataset.code; const box = el.querySelector(".reason"); const n = +el.dataset.n; const nr = el.dataset.nr === "1"; const short = el.dataset.short;
    const det = el.querySelector("details.pick"); const boxes = [...el.querySelectorAll(".picktbl input")];
    /* the selection only counts while the list is open; collapsed means the whole group */
    const chosen = () => (det && det.open ? boxes.filter((b) => b.checked) : boxes.length ? boxes : null);
    const sel = () => { const c = chosen(); return { all: !c || c.length === n, plain: c ? c.filter((b) => b.dataset.sug === "0").length : n - (+el.dataset.sugN || 0), sug: c ? c.filter((b) => b.dataset.sug === "1").length : 0, ids: c && c.length !== n ? c.map((b) => b.value) : null }; };
    const label = () => {
      const s = sel(); const btn = el.querySelector(`[data-gact="${nr ? "keep" : "confirm"}"]`); const us = el.querySelector('[data-gact="use_suggested"]');
      if (btn) { btn.hidden = !s.plain; btn.textContent = s.all ? (nr ? `Keep for all ${s.plain} ${short} lines…` : `Accept for all ${s.plain} ${short} lines with this assumption`) : `${nr ? "Keep" : "Accept"} ${s.plain} of ${n} lines${nr ? "…" : ""}`; }
      us.hidden = !(s.sug > 1 || (s.sug && det)); us.textContent = s.all ? `Use suggested price for all ${s.sug}` : `Use suggested for the selected lines (${s.sug})`;
      const cnt = el.querySelector(".pcount"); if (cnt) cnt.textContent = `${boxes.filter((b) => b.checked).length} of ${n} selected`;
      const all = el.querySelector(".pall"); if (all) all.checked = boxes.every((b) => b.checked);
    };
    el.dataset.sugN = boxes.length ? boxes.filter((b) => b.dataset.sug === "1").length : (S.comp.vendors.find((x) => x.id === vid).causes.find((g) => g.kind === "cells" && g.code === code) || {}).suggested || 0;
    boxes.forEach((b) => (b.onchange = label));
    if (el.querySelector(".pall")) el.querySelector(".pall").onchange = (e) => { boxes.forEach((b) => (b.checked = e.target.checked)); label(); };
    if (det) det.ontoggle = () => { S.pick = det.open ? { vid, code } : null; label(); };
    label();
    const go = (action, note) => { const s = sel(); if (s.ids && !s.ids.length) return toast("Select at least one line"); groupAct(vid, code, action, note, reopen, s.ids); };
    el.querySelectorAll("[data-gact]").forEach((b) => (b.onclick = () => {
      if (b.dataset.gact === "keep") { box.hidden = false; box.querySelector("input").focus(); return; }
      go(b.dataset.gact, null); }));
    box.querySelector("button").onclick = () => { const note = box.querySelector("input").value.trim(); if (!note) return toast("Give a reason to keep these prices"); go("confirm", note); };
  });
}
async function openVendor(vid, expand) {
  if (expand) S.pick = { vid, code: expand };
  const c = S.comp; const v = c.vendors.find((x) => x.id === vid); const d = $("#drawer"); const t = v.terms || {}; const disc = (t.discounts || [])[0];
  const q = QUALITY[v.quality] || QUALITY.no_response; const fr = t.freight || {};
  const asked = S.state.outbox.some((o) => o.vendor_id === vid && o.kind === "clarification");
  const groups = {}; c.cells.filter((x) => x.vendor_id === vid && x.decision && x.decision.grp).forEach((x) => (groups[x.decision.grp] = groups[x.decision.grp] || []).push(x));
  const fb = v.model && v.model !== S.state.primary_model;
  d.innerHTML = `<button class="ghost small x" id="dx">Close</button>
    <div class="muted small dhead">${esc(v.city || "")}${v.incumbent ? " · incumbent" : ""}</div><h2>${esc(v.name)}</h2>
    <div class="row" style="margin:8px 0"><div class="price-big">${v.lines_quoted ? big(v.total_value) : "–"}</div><span class="muted">${v.lines_quoted || 0} of ${c.rfx.lines.length} lines priced</span><span class="spacer"></span><span class="badge ${q[0]}">${q[1]}</span></div>
    <h3>Open issues (${(v.causes || []).length})</h3>
    ${(v.causes || []).map((g, i) => `<div class="issue ${g.severity}"><div>${g.kind === "cells" ? `<span class="itag">${STATUS[g.severity] || ""}</span> ` : ""}${esc(g.short)}${g.lines && g.lines.length ? ` <span class="muted">· ${g.lines.length} line${g.lines.length === 1 ? "" : "s"}</span>` : ""}</div>${g.items ? `<ul class="qitems">${g.items.map((q) => `<li>${esc(q)}</li>`).join("")}</ul>` : ""}${groupButtons(v, g, i, true)}</div>`).join("") || `<p class="muted">Nothing open.</p>`}
    ${v.response_status === "extracted" ? `<div class="row" style="margin-top:8px"><button class="small" id="vask">Ask vendor (draft clarification)</button>${asked ? `<button class="small ghost" id="vreply" ${S.busy["r" + vid] ? "disabled" : ""}>${S.busy["r" + vid] ? "Re-reading thread…" : "Record vendor reply"}</button>` : ""}</div>` : ""}
    ${Object.keys(groups).length ? `<h3>Decided as a group</h3>${Object.entries(groups).map(([g, xs]) => `<div class="row small">${esc(doneLabel(xs[0]))}${xs[0].decision.note ? ` (${esc(xs[0].decision.note)})` : ""}: ${xs.length} line${xs.length === 1 ? "" : "s"} <span class="muted">(${esc(span(xs.map((x) => x.line_id)))})</span><span class="spacer"></span><button class="small ghost" data-undo="${g}">Undo</button></div>`).join("")}` : ""}
    ${v.relevel ? `<div class="flagbox relevel" style="margin-top:12px">↻ Re-levelled after the vendor's reply: ${Object.keys(v.relevel.changed).length} prices changed, likely wrong ${v.relevel.needs_review[0]} → ${v.relevel.needs_review[1]}${v.relevel.freight_held[0] && !v.relevel.freight_held[1] ? ", freight now stated" : ""}${v.relevel.quality[0] !== v.relevel.quality[1] ? `, quality ${esc(v.relevel.quality[0])} → ${esc(v.relevel.quality[1])}` : `, quality still ${esc(v.relevel.quality[1])}`}.</div>` : ""}
    <h3>Terms</h3><div class="kvs">
      <div>Freight: ${esc(fr.status || "–")}${v.freight_pct && v.freight_pct[0] ? ` · ${v.freight_pct[0]}% ${v.freight_pct[1] === "vendor" ? "stated by vendor" : "your estimate"}` : ""}${fr.status === "extra" ? ` <label>· estimate <input type="number" id="vfr" value="${v.settings.freight_pct ?? ""}" placeholder="%" style="width:58px;padding:2px 5px"> %</label>` : ""}</div>
      ${disc ? `<label><input type="checkbox" id="vdisc" ${v.settings.apply_discount ? "checked" : ""}> Apply ${disc.pct}% discount${disc.condition ? ` (conditional: ${esc(disc.condition)})` : ""}</label>` : ""}
      <div>Payment: ${esc(t.payment_terms || "–")}</div>${t.lead_time_days ? `<div>Lead time: ${t.lead_time_days} days</div>` : ""}${t.validity ? `<div>Validity: ${esc(t.validity)}</div>` : ""}</div>
    <details class="how"><summary>Questionnaire, documents, model</summary>
      <table class="small">${v.questionnaire.map((a) => `<tr><td>${esc(a.question)}${a.mandatory ? " *" : ""}</td><td><span class="badge ${a.missing ? "b-warn" : { pass: "b-ok", fail: "b-bad", unknown: "b-warn", info: "b-none" }[a.result]}">${a.missing ? "Copy not attached" : { pass: "Pass", fail: "Fail", unknown: "No answer", info: "Info" }[a.result]}</span><div class="muted">${esc(a.answer || "")}${a.note ? " " + esc(a.note) : ""}</div></td></tr>`).join("")}</table>
      <p class="muted small">* mandatory. "No answer" is not a pass.</p>
      ${v.documents.map((dd) => `<div class="small"><a target="_blank" href="/api/file/${v.id}/${encodeURIComponent(dd.filename)}">${esc(dd.filename)}</a> · ${esc((dd.doc_type || "").replace(/_/g, " "))}${dd.valid_until ? " · valid until " + esc(dd.valid_until) : ""}<div class="muted">${esc(dd.summary || "")}</div></div>`).join("") || `<p class="muted small">No documents attached.</p>`}
      ${v.model ? `<p class="small">Read by ${modelNote(v.model)}${fb ? ` (configured: ${esc(S.state.primary_model)})` : ""}</p>` : ""}
    </details>`;
  d.hidden = false; $("#dx").onclick = () => (d.hidden = true);
  const reopen = () => openVendor(vid);
  wireGroups(d, vid, reopen);
  d.querySelectorAll("[data-undo]").forEach((b) => (b.onclick = async () => { await api("/api/decision/group", { body: { vendor_id: vid, action: "undo", grp: b.dataset.undo } }); toast("Group decision undone"); rerender(reopen); }));
  const setting = async (body) => { await api("/api/settings", { body }); rerender(reopen); };
  if ($("#vfr")) $("#vfr").onchange = (e) => setting({ vendor_id: vid, freight_pct: +e.target.value || 0 });
  if ($("#vdisc")) $("#vdisc").onchange = (e) => setting({ vendor_id: vid, apply_discount: e.target.checked });
  if ($("#vask")) $("#vask").onclick = async () => { const r = await api("/api/clarify/" + vid, { method: "POST" }); if (!r.ok) return toast(r.message); await refresh(true); openVendor(vid); modal(`<h2>Clarification email drafted <span class="stub">saved to stubbed outbox</span></h2><pre>${esc(r.body)}</pre>`); };
  if ($("#vreply")) $("#vreply").onclick = () => replyModal(vid);
}
async function replyModal(vid) {
  const v = S.comp.vendors.find((x) => x.id === vid); let sample = null;
  try { sample = await api("/api/reply/sample/" + vid); } catch {}
  const ask = S.state.outbox.find((o) => o.vendor_id === vid && o.kind === "clarification");
  modal(`<h2>Record ${esc(v.name)}'s reply <span class="stub">stubbed inbox: paste the email text</span></h2>
    <p class="muted">The reply joins this vendor's thread. The model re-reads the first response, your clarification email and this reply together (the reply wins where it restates a price or answers a question), and the comparison re-levels.</p>
    ${ask ? `<details class="how"><summary>Your clarification email</summary><pre>${esc(ask.body)}</pre></details>` : ""}
    <textarea id="rbody" rows="12" placeholder="Vendor's reply…"></textarea>
    <p class="row">${sample ? `<button class="ghost small" id="rsample">Paste the sample reply from ${esc(v.name)}</button>` : ""}<span class="spacer"></span><button id="rgo">Record reply and re-level</button></p>`);
  if ($("#rsample")) $("#rsample").onclick = () => { $("#rbody").value = sample.body; };
  $("#rgo").onclick = async () => {
    const text = $("#rbody").value; if (!text.trim()) return toast("Paste the vendor's reply first");
    $("#modal").hidden = true; S.busy["r" + vid] = true; viewCompare(); toast("Re-reading the thread with the model…");
    try { const r = await api("/api/reply/" + vid, { body: { message: text } }); toast(`${Object.keys(r.changed).length} prices changed; likely wrong ${r.needs_review[0]} → ${r.needs_review[1]}`); } catch (e) { toast(e.message); }
    S.busy["r" + vid] = false; await refresh(true); if (route() === "compare") viewCompare();
  };
}
/* a spreadsheet row as the vendor laid it out: header names over the row's values, the price cell marked */
function sheetRow(text, quote, amount) {
  const lines = text.split("\n"); const parse = (l) => l.split(" | ").map((p) => p.match(/^([A-Z]{1,3})(\d+)=(.*)$/s)).filter(Boolean).map((m) => ({ col: m[1], row: m[2], v: m[3] }));
  const isNum = (v) => /^-?[\d,]*\.?\d+$/.test(String(v).trim());
  const hits = lines.map((l, i) => i).filter((i) => amount != null && parse(lines[i]).some((c) => isNum(c.v) && Math.abs(parseFloat(c.v.replace(/,/g, "")) - amount) < 1e-9));
  const qi = quote ? lines.findIndex((l) => l.includes(quote)) : -1;
  const at = hits.includes(qi) ? qi : qi >= 0 && parse(lines[qi]).length ? qi : hits[0];
  if (at == null || at < 0) return null;
  const cells = parse(lines[at]); if (cells.length < 2) return null;
  let head = null;
  for (let i = at - 1; i >= 0 && !lines[i].startsWith("###"); i--) { const h = parse(lines[i]); if (h.length >= 3 && h.every((c) => !isNum(c.v))) { head = Object.fromEntries(h.map((c) => [c.col, c.v])); break; } }
  const mark = (c) => amount != null && isNum(c.v) && Math.abs(parseFloat(c.v.replace(/,/g, "")) - amount) < 1e-9;
  /* one row of the sheet, turned on its side so it fits the drawer: column heading, value, cell address */
  return `<div class="tablewrap sheetrow"><table>${cells.map((c) => `<tr class="${mark(c) ? "hit" : ""}"><th>${esc(head ? head[c.col] || "" : "")}</th><td>${esc(c.v)}</td><td class="addr">${c.col}${c.row}</td></tr>`).join("")}</table></div>`;
}
/* the few lines around the price in the vendor's own text */
function snippet(text, needle, alt) {
  let i = needle ? text.indexOf(needle) : -1, len = (needle || "").length;
  for (const cand of alt) { if (i >= 0) break; i = text.indexOf(cand); len = cand.length; }
  if (i < 0) return null;
  const ls = text.lastIndexOf("\n", text.lastIndexOf("\n", i - 1) - 1); const le1 = text.indexOf("\n", i + len); const le = le1 < 0 ? -1 : text.indexOf("\n", le1 + 1);
  let a = ls < 0 ? 0 : ls + 1, b = le < 0 ? text.length : le;
  a = Math.max(a, i - 220); b = Math.min(b, i + len + 220);
  return `${a > 0 ? "…" : ""}${esc(text.slice(a, i))}<mark>${esc(text.slice(i, i + len))}</mark>${esc(text.slice(i + len, b))}${b < text.length ? "…" : ""}`;
}
async function openCell(key) {
  const x = S.cells[key]; const c = S.comp; const line = c.rfx.lines.find((l) => l.id === x.line_id); const v = c.vendors.find((y) => y.id === x.vendor_id);
  const d = $("#drawer"); const ev = x.evidence; const h = x.headline; const sug = h && h.suggested_price != null ? h.suggested_price : (x.flags.find((f) => f.suggested_price != null) || {}).suggested_price;
  const ms = x.machine_status || x.status; const g = h && (v.causes || []).find((k) => k.kind === "cells" && k.code === h.code && k.lines.includes(x.line_id));
  const src = ev && ev.source_file ? ev.source_file : null;
  const verdict = x.decision ? (h ? `<p class="verdict col"><span class="muted small">Flagged before your decision · </span>${esc(h.text)}</p>` : "") : h ? `<p class="verdict ${h.vendor_wide ? "col" : h.severity}">${h.vendor_wide ? '<span class="muted small">Applies to every price from this vendor · </span>' : ""}${esc(h.text)}</p>` : x.raw ? `<p class="verdict ok">Read cleanly from ${esc(src || "the response")}.</p>` : x.reason ? `<p class="verdict">${esc(x.reason)}</p>` : "";
  const dec = x.decision ? `<div class="decided">${esc(doneLabel(x))}${x.decision.action === "set_price" && x.decision.price != null ? " " + money(x.decision.price) : ""}${x.decision.action !== "set_price" ? ` <span class="muted">(${x.decision.note ? esc(x.decision.note) : "no reason given"})</span>` : ""}
      ${x.decision.grp ? '<span class="muted"> · part of a group decision</span>' : ""}<span class="spacer"></span><button class="small ghost" id="dclr">Undo</button></div>` : "";
  const keepNeeds = ms === "needs_review" && x.price != null;
  const actions = !x.raw || x.decision ? "" : `<div class="actions">
      ${sug != null ? `<button id="dsug">Use ${money(sug)} instead</button>` : ""}
      ${keepNeeds ? `<button class="ghost small" id="dkeep">Keep ${money(x.price)}…</button>` : ms === "assumed" && x.price != null && x.cell_status !== "verified" ? `<button id="dconf">Accept</button>` : ""}
      <div class="reason" id="dreason" hidden><input type="text" id="dnote" placeholder="Why keep this price? (kept in the decision log)"><button class="small" id="dkeepgo">Keep with this reason</button></div>
      ${groupButtons(v, g, "c")}${g && g.kind === "cells" && g.lines.length > 1 ? `<a href="#" class="small" id="dchoose">Choose lines…</a>` : ""}
      <details class="more"><summary>Other options</summary><div class="row" style="margin-top:6px"><input type="number" id="dprice" step="0.01" placeholder="Set price ₹" style="width:120px"><button class="small ghost" id="dset">Set</button><button class="small danger" id="dexc">Exclude this line</button></div></details></div>`;
  const others = x.flags.filter((f) => !h || f.code !== h.code || x.decision);
  d.innerHTML = `<button class="ghost small x" id="dx">Close</button>
    <div class="muted small dhead">${esc(v.short)} · ${line.id} · ${esc(line.description)}</div>
    <div class="row" style="margin:6px 0 2px"><div class="price-big">${x.price != null ? money(x.price) : "No usable price"}</div>${x.price != null ? `<span class="muted">per ${line.uom}</span>` : ""}<span class="spacer"></span><span class="badge ${sevClass(x.cell_status || x.status)}">${STATUS[x.cell_status || x.status]}</span></div>
    ${verdict}${dec}
    ${ev ? `<div id="srcview" class="proof"></div>` : ""}
    ${actions}
    <details class="how details"><summary>Details</summary>
      ${others.length ? others.map((f) => `<div class="flagbox ${f.severity}">${esc(f.text)}${f.vendor_wide ? ' <span class="muted">(applies to every price from this vendor)</span>' : ""}</div>`).join("") : ""}
      ${h && !x.decision ? `<div class="flagbox ${h.severity}">${esc((x.flags.find((f) => f.code === h.code) || {}).text || "")}</div>` : ""}
      ${x.steps.length ? `<div class="small"><b>How we got this number</b><ol class="steps">${x.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol></div>` : ""}
      ${ev ? `<div class="small muted">${ev.vendor_description ? `Vendor's item: ${esc(ev.vendor_description)}<br>` : ""}${ev.note ? `Reader's note: ${esc(ev.note)}<br>` : ""}Cited: “${esc(ev.quote || "")}” · ${esc(ev.location || "")}${ev.verified === true ? " · found in the source" : ev.verified === false ? " · NOT found in the source" : " · image, cannot be searched"}</div>` : ""}
      ${x.changed_by_reply ? `<div class="small">↻ Before the vendor's reply: ${x.changed_by_reply.before_price != null ? money(x.changed_by_reply.before_price) : "no usable price"} (${STATUS[x.changed_by_reply.before_status] || ""}).</div>` : ""}
      ${x.annual_value != null ? `<div class="small muted">Annual value ${big(x.annual_value)} · last year ${money(line.last_year_price)}</div>` : ""}
    </details>`;
  d.hidden = false; $("#dx").onclick = () => (d.hidden = true);
  const reopen = () => openCell(key);
  const decide = async (action, price, note) => { try { await api("/api/decision", { body: { vendor_id: x.vendor_id, line_id: x.line_id, action, price, note } }); } catch (e) { return toast(e.message); } rerender(reopen); };
  if ($("#dsug")) $("#dsug").onclick = () => decide("set_price", sug, "Used the suggested price");
  if ($("#dconf")) $("#dconf").onclick = () => decide("confirm");
  if ($("#dkeep")) $("#dkeep").onclick = () => { $("#dreason").hidden = false; $("#dnote").focus(); };
  if ($("#dkeepgo")) $("#dkeepgo").onclick = () => { const n = $("#dnote").value.trim(); if (!n) return toast("Give a reason to keep this price"); decide("confirm", null, n); };
  if ($("#dset")) $("#dset").onclick = () => { const p = +$("#dprice").value; if (p > 0) decide("set_price", p); };
  if ($("#dexc")) $("#dexc").onclick = () => decide("exclude");
  if ($("#dclr")) $("#dclr").onclick = () => decide("clear");
  wireGroups(d, x.vendor_id, reopen);
  if ($("#dchoose")) $("#dchoose").onclick = (e) => { e.preventDefault(); openVendor(x.vendor_id, h.code); };
  if (ev && $("#srcview")) {
    const url = `/api/file/${x.vendor_id}/${encodeURIComponent(src || "")}`; const ext = (src || "").split(".").pop().toLowerCase();
    if (["jpg", "jpeg", "png", "webp"].includes(ext)) { $("#srcview").innerHTML = `<img class="srcimg" src="${url}" title="Click to enlarge">`; $(".srcimg").onclick = () => modal(`<img src="${url}">`); return; }
    const s = await api("/api/source/" + x.vendor_id); const text = s.source[src] ?? s.source["email body"] ?? Object.values(s.source).join("\n");
    const alt = x.raw && x.raw.amount != null ? [x.raw.amount.toFixed(2), String(x.raw.amount), x.raw.amount.toFixed(4)] : [];
    const row = /\.xls[xm]$/i.test(src || "") ? sheetRow(text, (ev.quote || "").trim(), x.raw ? x.raw.amount : null) : null;
    const snip = row || snippet(text, (ev.quote || "").trim(), alt);
    const full = src && src.includes(".") ? `<a target="_blank" href="${url}">Open ${esc(src)}</a>` : `<a href="#" id="fullsrc">Open the full ${esc(src || "response")}</a>`;
    $("#srcview").innerHTML = row ? `${row}<div class="small" style="margin-top:4px">${full}</div>` : `<div class="src">${snip || `<span class="muted">The cited text was not found in the source.</span>`}</div><div class="small" style="margin-top:4px">${full}</div>`;
    const mk = $("#srcview mark"); if (mk) mk.parentElement.scrollTop = Math.max(0, mk.offsetTop - mk.parentElement.offsetTop - 30);
    if ($("#fullsrc")) $("#fullsrc").onclick = (e) => { e.preventDefault(); modal(`<h2>${esc(v.name)}: ${esc(src || "response")}</h2><div class="src" style="max-height:none">${esc(text)}</div>`); };
  }
}

/* ---------- 4. Ask ---------- */
const ASK_CHIPS = [
  "What if we split it, cheapest per line, but only among vendors who cleared the quality questionnaire?",
  "What is that quality gate costing us versus a split across everyone? Who would be worth chasing?",
  "Export the award recommendation to Excel.",
]
function viewAsk() {
  const chat = S.state.analyst_chat;
  $("#view").innerHTML = `<div class="ask"><div class="row"><div><h2>Ask about the comparison</h2></div>
      <span class="spacer"></span>${chat.length ? `<button class="ghost small" id="aclr">New conversation</button>` : ""}</div>
    <div id="turns">${chat.map(turnHtml).join("")}${S.busy.ask ? `<div class="turn"><div class="card thinking">Querying the comparison</div></div>` : ""}</div>
    <div class="askbar">${chat.length < 2 ? `<div class="chips" style="padding:0 0 10px">${ASK_CHIPS.map((c, i) => `<button class="chip" data-ask="${i}">${esc(c)}</button>`).join("")}</div>` : ""}
      <form class="composer" id="aform"><textarea id="ain" placeholder="Ask anything about these quotes…"></textarea><button ${S.busy.ask ? "disabled" : ""}>Ask</button></form></div></div>`;
  const send = async (text) => {
    if (!text.trim() || S.busy.ask) return;
    S.state.analyst_chat.push({ role: "user", text }); S.busy.ask = true; viewAsk(); window.scrollTo(0, document.body.scrollHeight);
    try { await api("/api/analyst", { body: { message: text } }); } catch (e) { toast(e.message); }
    S.busy.ask = false; await refresh(); if (route() === "ask") { viewAsk(); const t = document.querySelectorAll(".turn.user"); if (t.length) t[t.length - 1].scrollIntoView({ behavior: "smooth", block: "start" }); }
  };
  $("#aform").onsubmit = (e) => { e.preventDefault(); send($("#ain").value); };
  $("#ain").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send($("#ain").value); } };
  document.querySelectorAll("[data-ask]").forEach((b) => (b.onclick = () => send(ASK_CHIPS[b.dataset.ask])));
  if ($("#aclr")) $("#aclr").onclick = async () => { await api("/api/analyst/reset", { method: "POST" }); await refresh(); viewAsk(); };
}
function turnHtml(t) {
  if (t.role === "user") return `<div class="turn user"><div>${esc(t.text)}</div></div>`;
  const figs = t.figures || []; const bad = figs.filter((f) => !f.traced); const vi = t.vendor_issues || [];
  const vf = t.vendor_flags || vi.map((text) => ({ kind: "mixup", vendors: [], text }));
  const fb = t.fallback || (t.model && t.primary_model && t.model !== t.primary_model);
  const primary = t.primary_model || S.state.primary_model;
  const fallbacks = (t.models || [t.model]).filter((m) => m && m !== primary);
  /* failed checks, in a buyer's words; the technical detail is in the working */
  const mix = vf.filter((d) => d.kind === "mixup"); const unref = vf.filter((d) => d.kind === "unreferenced");
  const names = (ds) => { const u = [...new Set(ds.flatMap((d) => d.vendors || []))]; return u.length > 1 ? u.slice(0, -1).join(", ") + " and " + u[u.length - 1] : u[0] || ""; };
  const warn = [];
  if (mix.length) warn.push(`This answer may have mixed up ${names(mix.slice(0, 1)) || "vendors"}. Do not rely on its figures; ask again or check the working.`);
  else if (unref.length) warn.push(`This answer names ${names(unref)}, but none of its data was about ${unref.length > 1 || (unref[0].vendors || []).length > 1 ? "them" : "that vendor"}. Do not rely on what it says there; ask again or check the working.`);
  if (bad.length) warn.push(`${bad.length} figure${bad.length === 1 ? "" : "s"} in this answer could not be matched to the data: ${bad.map((f) => esc(f.text)).join(", ")}.`);
  const checked = figs.length || t.vendors_checked;
  const ok = checked && !warn.length;
  const scen = (t.blocks || []).filter((b) => b.type === "scenario");
  const working = `
    <div class="wsec"><b>Checks</b> ${figs.length ? `${figs.length - bad.length} of ${figs.length} figures matched the data${figs.length ? ": " + figs.map((f) => `${esc(f.text)}${f.traced ? "" : " ✗"}`).join(" · ") : ""}.` : "No figures in the prose."}
      ${t.vendors_checked != null ? ` ${t.vendors_checked} vendor${t.vendors_checked === 1 ? "" : "s"} named, checked against the queries.` : ""}
      ${vf.map((d) => `<div class="muted">${esc(d.text)}</div>`).join("")}</div>
    ${scen.map((b) => allocationHtml(b.scenario)).join("")}
    ${(t.trace || []).map((s) => `<div class="wsec"><b>${esc(s.tool)}</b> ${esc(s.title || "")}${s.rows != null ? ` · ${s.rows} rows` : ""}${s.sql ? `<pre>${esc(s.sql)}</pre>` : s.args ? `<pre>${esc(JSON.stringify(s.args, null, 1))}</pre>` : ""}</div>`).join("")}
    ${t.model ? `<div class="wsec muted">Answered by ${esc((t.models || [t.model]).join(", "))}${fb ? ` (configured: ${esc(primary)})` : ""}</div>` : ""}`;
  return `<div class="turn"><div class="card answer">${md(t.text)}${(t.blocks || []).map(blockHtml).join("")}
    ${warn.map((w) => `<div class="traced no">${w}</div>`).join("")}
    <div class="wrow">${fb ? `<span class="fallback" title="The configured model ${esc(primary)} did not answer">fallback model: ${esc(fallbacks.join(", ") || t.model)}</span>` : ""}
    <details class="how working"><summary>Show working${ok ? ' · <span class="okmark">✓ checked</span>' : ""}</summary>${working}</details></div></div></div>`;
}
/* Ask tables: plain headers, Indian money, % shares; the data and SQL are untouched */
const colName = (c) => { const s = String(c).replace(/_inr$/i, "").replace(/_pct$/i, " %").replace(/^pct$/i, "%").replace(/_/g, " ").trim(); return s.charAt(0).toUpperCase() + s.slice(1); };
const isMoney = (c) => /(inr|price|value|saving|total|cost|amount)/i.test(c) && !/(pct|count|lines|qty)/i.test(c);
const isPct = (c) => /(pct|percent|share)/i.test(c);
const fmtCell = (v, col) => {
  if (typeof v !== "number") return esc(STATUS[v] && /status/i.test(col) ? STATUS[v] : v ?? "");
  if (isPct(col)) return nf(1).format(v) + "%";
  if (isMoney(col)) return /price/i.test(col) || Math.abs(v) < 1000 ? "₹" + nf(2).format(v) : big(v);
  return nf(Number.isInteger(v) ? 0 : 2).format(v);
};
function tableHtml(cols, rows, max = 440) {
  const numCol = cols.map((_, i) => rows.some((r) => typeof r[i] === "number"));
  return `<div class="tablewrap" style="max-height:${max}px"><table><tr>${cols.map((c, i) => `<th class="${numCol[i] ? "num" : ""}">${esc(colName(c))}</th>`).join("")}</tr>${rows.map((r) => `<tr>${r.map((v, i) => `<td class="${typeof v === "number" ? "num" : ""}">${fmtCell(v, cols[i])}</td>`).join("")}</tr>`).join("")}</table></div>`;
}
function allocationHtml(s) {
  const h = s.prices_held_back_pending_review || [];
  return `<div class="wsec"><b>Line-by-line allocation</b>${s.lines_uncovered.length ? ` · not covered: ${s.lines_uncovered.join(", ")}` : ""}
    ${tableHtml(["line", "description", "awarded_to", "unit_price_inr", "annual_value_inr", "price_status", "next_best", "next_best_price_inr"], s.allocation.map((a) => [a.line_id, a.description, a.vendor_name, a.unit_price_inr, a.annual_value_inr, a.status, a.next_best_vendor, a.next_best_price_inr]), 320)}</div>
    ${h.length ? `<div class="wsec"><b>Held back</b>${tableHtml(["vendor", "line", "unit_price_inr", "why"], h.map((x) => [x.vendor_name, x.line_id, x.unit_price_inr, x.why.split("; ").map(whyText).join(", ")]), 240)}</div>` : ""}`;
}
const whyText = (w) => ({ FREIGHT_EXTRA: "freight not stated" }[w] || (/^status /.test(w) ? w : "likely wrong"));
function blockHtml(b) {
  if (b.type === "table") return `<div class="block"><div class="btitle">${esc(b.title || "")}</div>${tableHtml(b.columns, b.rows)}</div>`;
  if (b.type === "file") return `<div class="block"><a class="att" href="${b.url}">⬇ ${esc(b.name)}</a></div>`;
  if (b.type === "chart") return `<div class="block"><div class="btitle">${esc(b.title || "")}</div>${chartSvg(b)}</div>`;
  if (b.type === "scenario") {
    const s = b.scenario; const n = s.lines_covered + s.lines_uncovered.length; const sv = s.saving_vs_last_year_inr;
    const chips = [];
    if (s.lines_uncovered.length) chips.push(`<span class="pchip bad">${s.lines_uncovered.length} line${s.lines_uncovered.length === 1 ? "" : "s"} not covered: ${esc(s.lines_uncovered.join(", "))}</span>`);
    if (s.value_on_needs_review_prices_inr) chips.push(`<span class="pchip bad">${big(s.value_on_needs_review_prices_inr)} of this rests on prices that are likely wrong</span>`);
    if (s.value_on_assumed_prices_inr) chips.push(`<span class="pchip warn">${big(s.value_on_assumed_prices_inr)} of this rests on assumed prices</span>`);
    const h = s.prices_held_back_pending_review || []; const g = {};
    h.forEach((x) => (g[x.vendor_name] = g[x.vendor_name] || []).push(x));
    const held = Object.entries(g).map(([k, v]) => `${esc(k)}${v.length <= 2 ? " " + v.map((x) => x.line_id).join(", ") : ""} (${esc([...new Set(v.flatMap((x) => x.why.split("; ")).filter(Boolean).map(whyText))].join(", "))})`).join(", ");
    const add = (s.if_one_excluded_vendor_were_added || []).map((d) => `${esc(d.vendor_name)} ${d.not_computable ? "unknown (freight not stated)" : big(d.saving_if_added_inr)}`).join(" · ");
    return `<div class="block scen"><div class="btitle">${esc(b.title || "Award scenario")}</div>
      <div class="sline"><b>${big(s.total_annual_value_inr)}</b> a year · ${sv >= 0 ? `${big(sv)} (${Math.abs(s.saving_vs_last_year_pct)}%) below` : `${big(-sv)} (${Math.abs(s.saving_vs_last_year_pct)}%) above`} last year on the same lines${s.lines_uncovered.length ? ` · ${s.lines_covered} of ${n} lines covered` : ""}</div>
      ${chips.length ? `<div class="pchips">${chips.join("")}</div>` : ""}
      ${tableHtml(["vendor", "lines", "annual_value_inr", "share_pct"], s.by_vendor.map((v) => [v.vendor_name, v.lines, v.annual_value_inr, v.share_pct]))}
      ${h.length ? `<div class="sub">${h.length} price${h.length === 1 ? "" : "s"} held back: ${held}</div>` : ""}
      ${add ? `<div class="sub">Saving if added back: ${add}</div>` : ""}</div>`;
  }
  return "";
}
function chartSvg(b) {
  const W = 980, H = 320, L = 64, R = 12, T = 12, B = 78; const pal = ["#0f5c57", "#d98a2b", "#4b6cb7", "#b5483a", "#7a8a2a", "#7b5aa6"];
  const series = b.series.map((s) => ({ ...s, data: s.data.map((v) => (typeof v === "number" ? v : parseFloat(v) || 0)) }));
  const n = b.labels.length; const stacked = b.kind === "stacked_bar";
  const max = Math.max(1e-9, ...(stacked ? b.labels.map((_, i) => series.reduce((a, s) => a + s.data[i], 0)) : series.flatMap((s) => s.data)));
  const min = Math.min(0, ...series.flatMap((s) => s.data)); const span = max - min;
  const y = (v) => T + (H - T - B) * (1 - (v - min) / span); const bw = (W - L - R) / n;
  const short = (v) => (Math.abs(v) >= 1e7 ? (v / 1e7).toFixed(2) + " Cr" : Math.abs(v) >= 1e5 ? (v / 1e5).toFixed(1) + " L" : Math.abs(v) >= 1000 ? (v / 1000).toFixed(0) + "k" : +v.toFixed(2));
  let g = "";
  for (let i = 0; i <= 4; i++) { const v = min + (span * i) / 4; g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="#e7e9ec"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${short(v)}</text>`; }
  b.labels.forEach((lab, i) => { const cx = L + bw * (i + 0.5); g += `<text transform="translate(${cx},${H - B + 12}) rotate(${n > 6 ? -32 : 0})" text-anchor="${n > 6 ? "end" : "middle"}">${esc(lab.length > 22 ? lab.slice(0, 21) + "…" : lab)}</text>`; });
  if (b.kind === "line") series.forEach((s, k) => { g += `<polyline fill="none" stroke="${pal[k % 6]}" stroke-width="2" points="${s.data.map((v, i) => `${L + bw * (i + 0.5)},${y(v)}`).join(" ")}"/>` + s.data.map((v, i) => `<circle cx="${L + bw * (i + 0.5)}" cy="${y(v)}" r="3" fill="${pal[k % 6]}"><title>${esc(s.name)}: ${nf(2).format(v)}</title></circle>`).join(""); });
  else b.labels.forEach((_, i) => { let acc = 0; const inner = bw * 0.72; series.forEach((s, k) => { const v = s.data[i];
    if (stacked) { g += `<rect x="${L + bw * i + bw * 0.14}" width="${inner}" y="${y(acc + v)}" height="${Math.max(0, y(acc) - y(acc + v))}" fill="${pal[k % 6]}"><title>${esc(s.name)}: ${nf(2).format(v)}</title></rect>`; acc += v; }
    else { const w = inner / series.length; g += `<rect x="${L + bw * i + bw * 0.14 + w * k}" width="${Math.max(1, w - 1)}" y="${Math.min(y(v), y(0))}" height="${Math.abs(y(0) - y(v))}" fill="${pal[k % 6]}"><title>${esc(s.name)}: ${nf(2).format(v)}</title></rect>`; } }); });
  return `<div class="card" style="padding:10px"><svg class="chart" viewBox="0 0 ${W} ${H}" width="100%">${g}</svg>
    <div class="chartlegend">${series.map((s, k) => `<span><i class="sw" style="background:${pal[k % 6]}"></i>${esc(s.name.replace(/_/g, " "))}</span>`).join("")}${b.y_label ? `<span class="muted">${esc(b.y_label)}</span>` : ""}</div></div>`;
}

refresh(["compare", "inbox"].includes(route())).then(render);
