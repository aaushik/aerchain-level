/* Level front end. No build step, no dependencies: one file the reviewer can read top to bottom. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = (d) => new Intl.NumberFormat("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const money = (x) => (x == null ? "" : "₹" + nf(Math.abs(x) >= 1000 ? 0 : 2).format(x));
const big = (x) => (x == null ? "" : Math.abs(x) >= 1e7 ? "₹" + nf(2).format(x / 1e7) + " Cr" : Math.abs(x) >= 1e5 ? "₹" + nf(2).format(x / 1e5) + " L" : money(x));
const S = { state: null, comp: null, busy: {}, filter: "all" };
const STEPS = [["draft", "Draft RFx"], ["inbox", "Responses"], ["compare", "Compare"], ["ask", "Ask"]];
const STATUS = { verified: "Verified", assumed: "Assumed", needs_review: "Needs review", not_quoted: "Not quoted", no_response: "No response" };
const TAG = { TOTAL_MISMATCH: "rate ≠ total", OUTLIER: "outlier", SPEC: "spec differs", UNIT: "unit?", NO_PRICE: "unreadable", EVIDENCE: "no evidence", READ: "unsure read",
  MATCH: "match?", SCOPE: "covered?", FX_MISSING: "no FX", NO_REFERENCE: "no ref", DUPLICATE: "duplicate", WEIGHT: "per kg", FX: "USD", LAST_YEAR: "last year", PHOTO: "photo",
  UNIT_INFERRED: "unit inferred", DISCOUNT_COND: "disc.", FREIGHT_EST: "+freight" };

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
        ${chat.map((m) => `<div class="msg ${m.role}">${esc(m.text)}${m.changes && m.changes.length ? `<span class="chg">Draft updated: ${esc(m.changes.join(", "))}</span>` : ""}</div>`).join("")}
        ${S.busy.copilot ? `<div class="msg assistant thinking">Working on the draft</div>` : ""}</div>
      <div class="chips">${DRAFT_CHIPS.map((c, i) => `<button class="chip" data-chip="${i}">${esc(c.length > 70 ? c.slice(0, 68) + "…" : c)}</button>`).join("")}</div>
      <form class="composer" id="cform"><textarea id="cin" placeholder="Describe what you need…"></textarea><button ${S.busy.copilot ? "disabled" : ""}>Send</button></form>
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
          ${r && r.model ? `<span class="muted" style="font-size:12px">read by ${esc(r.model)}${r.extracted_at ? " · " + new Date(r.extracted_at * 1000).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : ""}</span>` : ""}</div></div>`; }).join("")}</div>`;
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
const cellKey = (v, l) => v + "|" + l;
function viewCompare() {
  const c = S.comp;
  if (!c || !c.vendors.some((v) => v.response_status === "extracted")) return ($("#view").innerHTML = `<div class="card empty">No responses have been read yet. Go to <a href="#inbox">Responses</a>.</div>`);
  const cells = Object.fromEntries(c.cells.map((x) => [cellKey(x.vendor_id, x.line_id), x]));
  S.cells = cells;
  const q = { cleared: ["b-ok", "Quality: cleared"], failed: ["b-bad", "Quality: failed"], incomplete: ["b-warn", "Quality: not answered"], no_response: ["b-none", "No response"] };
  const attention = (l) => c.vendors.some((v) => ["needs_review"].includes(cells[cellKey(v.id, l.id)].status));
  const lines = c.rfx.lines.filter((l) => S.filter === "all" || attention(l));
  const open = c.cells.filter((x) => x.status === "needs_review").length, assumed = c.cells.filter((x) => x.status === "assumed").length;
  $("#view").innerHTML = `<div class="row" style="margin-bottom:12px"><div><h2>Side-by-side comparison</h2>
      <div class="muted">Every price is in ₹ per RFx unit, delivered basis where stated, excluding GST. Click any cell to see what the vendor wrote and how it became this number.</div></div><span class="spacer"></span>
      <label class="muted">USD → INR <input type="number" id="fx" step="0.05" value="${c.settings.fx.USD}" style="width:80px"></label>
      <a href="/api/export/comparison.xlsx"><button class="ghost">Export to Excel</button></a></div>
    <div class="vhead">${c.vendors.map((v) => {
      const n = c.rfx.lines.length, k = v.counts || {}; const t = v.terms || {}; const d = (t.discounts || [])[0];
      return `<div class="card vh"><div class="name">${esc(v.name)}${v.incumbent ? ' <span class="muted">(incumbent)</span>' : ""}</div>
        <div class="big">${v.lines_quoted ? big(v.total_value) : "–"}</div><div class="muted" style="font-size:12px">${v.lines_quoted || 0} of ${n} lines priced</div>
        <div class="bar" title="verified / assumed / needs review / not quoted">${["verified", "assumed", "needs_review", "not_quoted"].map((s) => `<i class="${s}" style="width:${100 * (k[s] || 0) / n}%"></i>`).join("")}</div>
        <span class="badge ${q[v.quality][0]}" data-q="${v.id}" style="cursor:pointer">${q[v.quality][1]}</span>
        <div class="kv">Freight: ${esc((t.freight || {}).status || "–")} · Pay: ${esc(t.payment_terms || "–")}</div>
        ${d ? `<label class="kv" style="display:block"><input type="checkbox" data-disc="${v.id}" ${v.settings.apply_discount ? "checked" : ""}> Apply ${d.pct}% discount${d.condition ? " (conditional)" : ""}</label>` : ""}
        ${(t.freight || {}).status === "extra" ? `<label class="kv" style="display:block">Freight estimate <input type="number" data-fr="${v.id}" value="${v.settings.freight_pct ?? ""}" placeholder="%" style="width:58px;padding:2px 5px"> %</label>` : ""}
        ${v.flags.map((f) => `<div class="vflag ${f.severity}">${esc(f.text)}</div>`).join("")}
        ${v.response_status === "extracted" ? `<div style="margin-top:8px"><button class="small ghost" data-clar="${v.id}">Draft clarification email</button></div>` : ""}</div>`; }).join("")}</div>
    <div class="row" style="margin-bottom:8px"><div class="legend"><span><i class="sw" style="background:#fff"></i>Verified</span><span><i class="sw" style="background:var(--warn-bg)"></i>Assumed: usable, rests on a stated assumption (${assumed})</span>
      <span><i class="sw" style="background:var(--bad-bg)"></i>Needs review: probably wrong or not like-for-like (${open})</span><span><i class="sw" style="background:#e3e5e8"></i>Not quoted</span><span><b style="text-decoration:underline">Underlined</b> = lowest usable price on the line</span></div>
      <span class="spacer"></span><button class="small ghost" id="flt">${S.filter === "all" ? `Show only lines needing review (${c.rfx.lines.filter(attention).length})` : "Show all lines"}</button></div>
    <div class="tablewrap grid"><table><tr><th>Line</th><th>Description</th><th class="num">Annual qty</th><th class="num">Last year</th>${c.vendors.map((v) => `<th class="vcol">${esc(v.name.split(" ").slice(0, 2).join(" "))}</th>`).join("")}</tr>
      ${lines.map((l) => `<tr><td>${l.id}</td><td>${esc(l.description)}</td><td class="num">${nf(0).format(l.annual_qty)} ${l.uom}</td><td class="num muted">${money(l.last_year_price)}</td>
        ${c.vendors.map((v) => { const x = cells[cellKey(v.id, l.id)]; const tag = x.flags.length ? TAG[[...x.flags].sort((a, b) => (b.severity === "needs_review") - (a.severity === "needs_review"))[0].code] || "" : "";
          const isBest = c.best[l.id] === v.id && x.price != null;
          return `<td class="cell ${x.status} ${isBest ? "best" : ""}" data-cell="${cellKey(v.id, l.id)}">${x.price != null ? `${tag && x.status !== "verified" ? `<span class="tag">${tag}</span>` : ""}<b>${nf(2).format(x.price)}</b>${x.decision ? '<span class="dec" title="Buyer decision recorded">✓</span>' : ""}` : x.status === "needs_review" ? `<span class="tag">${tag}</span>?` : (x.status === "no_response" ? "no response" : "not quoted")}</td>`; }).join("")}</tr>`).join("")}</table></div>
    <h3>Questionnaire</h3>
    <div class="tablewrap"><table><tr><th>Question</th><th>Pass rule</th>${c.vendors.map((v) => `<th>${esc(v.name.split(" ").slice(0, 2).join(" "))}</th>`).join("")}</tr>
      ${c.rfx.questionnaire.map((qq, i) => `<tr><td>${esc(qq.text)} ${qq.mandatory ? '<span class="badge b-bad">Mandatory</span>' : ""}</td><td class="muted">${esc(qq.pass_text || "")}</td>
        ${c.vendors.map((v) => { const a = v.questionnaire[i] || {}; const b = { pass: ["b-ok", "Pass"], fail: ["b-bad", "Fail"], unknown: ["b-warn", "No answer"], info: ["b-none", "Info"] }[a.result || "unknown"];
          return `<td class="qcell"><span class="badge ${b[0]}">${b[1]}</span><div class="ans">${esc(a.answer || "")}</div>${a.note ? `<div class="muted">${esc(a.note)}</div>` : ""}</td>`; }).join("")}</tr>`).join("")}</table></div>
    <h3>Attached documents</h3>
    <div class="tablewrap"><table><tr><th>Vendor</th><th>Document</th><th>Type</th><th>What it says</th><th>Valid until</th></tr>
      ${c.vendors.flatMap((v) => v.documents.map((d) => `<tr><td>${esc(v.name)}</td><td><a target="_blank" href="/api/file/${v.id}/${encodeURIComponent(d.filename)}">${esc(d.filename)}</a></td><td>${esc((d.doc_type || "").replace(/_/g, " "))}</td><td>${esc(d.summary)}</td><td>${esc(d.valid_until || "–")}</td></tr>`)).join("") || `<tr><td colspan="5" class="empty">No documents</td></tr>`}</table></div>
    <h3>Decision log</h3><div class="tablewrap" style="max-height:220px"><table>${S.state.audit.map((a) => `<tr><td class="muted" style="width:150px">${new Date(a.ts * 1000).toLocaleString("en-IN")}</td><td style="width:170px">${esc(a.event.replace(/_/g, " "))}</td><td>${esc(a.detail)}</td></tr>`).join("")}</table></div>`;
  document.querySelectorAll("[data-cell]").forEach((td) => (td.onclick = () => openCell(td.dataset.cell)));
  $("#flt").onclick = () => { S.filter = S.filter === "all" ? "attention" : "all"; viewCompare(); };
  const setting = async (body) => { await api("/api/settings", { body }); await refresh(true); viewCompare(); };
  $("#fx").onchange = (e) => setting({ fx: { USD: +e.target.value } });
  document.querySelectorAll("[data-disc]").forEach((el) => (el.onchange = () => setting({ vendor_id: el.dataset.disc, apply_discount: el.checked })));
  document.querySelectorAll("[data-fr]").forEach((el) => (el.onchange = () => setting({ vendor_id: el.dataset.fr, freight_pct: +el.value || 0 })));
  document.querySelectorAll("[data-clar]").forEach((el) => (el.onclick = async () => { const r = await api("/api/clarify/" + el.dataset.clar, { method: "POST" }); if (!r.ok) return toast(r.message); await refresh(); modal(`<h2>Clarification email drafted <span class="stub">saved to stubbed outbox</span></h2><pre>${esc(r.body)}</pre>`); }));
  document.querySelectorAll("[data-q]").forEach((el) => (el.onclick = () => { const v = c.vendors.find((x) => x.id === el.dataset.q);
    modal(`<h2>${esc(v.name)}: questionnaire</h2><div class="tablewrap"><table><tr><th>Question</th><th>Answer</th><th>Result</th><th>Evidence</th></tr>${v.questionnaire.map((a) => `<tr><td>${esc(a.question)}${a.mandatory ? " *" : ""}</td><td>${esc(a.answer || "–")}</td><td>${esc(a.result)}${a.note ? `<div class="muted">${esc(a.note)}</div>` : ""}</td><td class="muted">${esc(a.evidence || "")}${a.evidence_verified === true ? " ✓ found in source" : ""}</td></tr>`).join("")}</table></div><p class="muted">* mandatory. "No answer" is not a pass.</p>`); }));
}
async function openCell(key) {
  const x = S.cells[key]; const c = S.comp; const line = c.rfx.lines.find((l) => l.id === x.line_id); const v = c.vendors.find((y) => y.id === x.vendor_id);
  const d = $("#drawer"); const ev = x.evidence; const sug = x.flags.find((f) => f.suggested_price != null);
  d.innerHTML = `<button class="ghost small x" id="dx">Close</button>
    <div class="muted">${esc(v.name)} · ${line.id}</div><h2>${esc(line.description)}</h2><div class="muted">${esc(line.board_spec)} · ${nf(0).format(line.annual_qty)} ${line.uom}/yr · last year ${money(line.last_year_price)}</div>
    <div class="row" style="margin:14px 0 4px"><div class="price-big">${x.price != null ? money(x.price) : "No usable price"}</div>${x.price != null ? `<span class="muted">per ${line.uom} · ${big(x.annual_value)}/yr</span>` : ""}
      <span class="spacer"></span><span class="badge ${{ verified: "b-ok", assumed: "b-warn", needs_review: "b-bad" }[x.status] || "b-none"}">${STATUS[x.status]}</span></div>
    ${x.reason ? `<p>${esc(x.reason)}</p>` : ""}
    ${x.flags.length ? `<h3>Why this is not fully trusted</h3>${x.flags.map((f) => `<div class="flagbox ${f.severity}"><span class="code">${f.code.replace(/_/g, " ")}</span>${esc(f.text)}</div>`).join("")}` : x.raw ? `<p class="muted">Read cleanly: unit and currency match the RFx, the quoted text was found in the source, and the price is in line with the other vendors.</p>` : ""}
    ${x.steps.length ? `<h3>How we got this number</h3><ol class="steps">${x.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>` : ""}
    ${ev ? `<h3>What the vendor wrote</h3><blockquote>${esc(ev.quote)}</blockquote><div class="muted" style="font-size:12.5px">${esc(ev.location || "")}${ev.verified === true ? " · ✓ this text was found in the source" : ev.verified === false ? " · ✗ this text was NOT found in the source" : " · image source, cannot be searched"}
      ${ev.vendor_description ? `<br>Vendor's item: ${esc(ev.vendor_description)}` : ""}${ev.note ? `<br>Reader's note: ${esc(ev.note)}` : ""}</div><div id="srcview" style="margin-top:8px"></div>` : ""}
    ${x.raw ? `<h3>Your decision</h3><div class="row">
      <button class="small" id="dconf" ${x.price == null ? "disabled" : ""}>Confirm as is</button>
      ${sug ? `<button class="small" id="dsug">Use ${money(sug.suggested_price)} instead</button>` : ""}
      <input type="number" id="dprice" step="0.01" placeholder="Set price ₹" style="width:120px"><button class="small ghost" id="dset">Set</button>
      <button class="small danger" id="dexc">Exclude</button>${x.decision ? `<button class="small ghost" id="dclr">Undo decision</button>` : ""}</div>
      <input type="text" id="dnote" placeholder="Reason (kept in the decision log)" style="width:100%;margin-top:8px">
      ${x.decision ? `<p class="muted" style="font-size:12.5px">Recorded: ${esc(x.decision.action.replace("_", " "))}${x.decision.price != null ? " " + money(x.decision.price) : ""}${x.decision.note ? " — " + esc(x.decision.note) : ""}</p>` : ""}` : ""}`;
  d.hidden = false; $("#dx").onclick = () => (d.hidden = true);
  const decide = async (action, price) => { await api("/api/decision", { body: { vendor_id: x.vendor_id, line_id: x.line_id, action, price, note: $("#dnote") ? $("#dnote").value : null } }); await refresh(true); viewCompare(); openCell(key); };
  if ($("#dconf")) $("#dconf").onclick = () => decide("confirm");
  if ($("#dsug")) $("#dsug").onclick = () => decide("set_price", sug.suggested_price);
  if ($("#dset")) $("#dset").onclick = () => { const p = +$("#dprice").value; if (p > 0) decide("set_price", p); };
  if ($("#dexc")) $("#dexc").onclick = () => decide("exclude");
  if ($("#dclr")) $("#dclr").onclick = () => decide("clear");
  if (ev && $("#srcview")) {
    const f = ev.source_file || ""; const url = `/api/file/${x.vendor_id}/${encodeURIComponent(f)}`; const ext = f.split(".").pop().toLowerCase();
    if (["jpg", "jpeg", "png", "webp"].includes(ext)) { $("#srcview").innerHTML = `<img class="srcimg" src="${url}" title="Click to enlarge">`; $(".srcimg").onclick = () => modal(`<img src="${url}">`); }
    else {
      const s = await api("/api/source/" + x.vendor_id); const text = s.source[f] ?? s.source["email body"] ?? Object.values(s.source).join("\n");
      const needle = (ev.quote || "").trim(); let i = needle ? text.indexOf(needle) : -1; let len = needle.length;
      if (i < 0 && x.raw && x.raw.amount != null) { for (const cand of [x.raw.amount.toFixed(2), String(x.raw.amount), x.raw.amount.toFixed(4)]) { i = text.indexOf(cand); if (i >= 0) { len = cand.length; break; } } }
      const a = Math.max(0, i - 500), b = Math.min(text.length, (i < 0 ? 0 : i) + len + 500);
      $("#srcview").innerHTML = `<div class="src">${i < 0 ? esc(text.slice(0, 1200)) : esc(text.slice(a, i)) + "<mark>" + esc(text.slice(i, i + len)) + "</mark>" + esc(text.slice(i + len, b))}</div>
        ${f && f !== "email body" ? `<div style="margin-top:6px"><a target="_blank" href="${url}">Open ${esc(f)}</a></div>` : ""}`;
      const m = $("#srcview mark"); if (m) m.scrollIntoView({ block: "center" });
    }
  }
}

/* ---------- 4. Ask ---------- */
const ASK_CHIPS = [
  "Where do we stand? Who quoted what, and what can't I trust yet?",
  "What if we split it, cheapest per line, but only among vendors who cleared the quality questionnaire?",
  "What is that quality gate costing us versus a split across everyone? Who would be worth chasing?",
  "If I can only manage two vendors, which two, and how much do I give up versus the full split?",
  "How much of the best split rests on prices that aren't verified? Show it by vendor.",
  "Compare everyone to last year's prices on the lines all five quoted. Chart it.",
  "Export the award recommendation to Excel.",
];
function viewAsk() {
  const chat = S.state.analyst_chat;
  $("#view").innerHTML = `<div class="ask"><div class="row"><div><h2>Ask about the comparison</h2><div class="muted">Answers are computed by queries over the levelled data, not written from memory. Each answer shows its working.</div></div>
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
  const figs = t.figures || []; const bad = figs.filter((f) => !f.traced);
  return `<div class="turn"><div class="card answer">${md(t.text)}${(t.blocks || []).map(blockHtml).join("")}
    ${figs.length ? (bad.length ? `<div class="traced no">${figs.length - bad.length} of ${figs.length} figures in this answer match a query result. Not matched: ${bad.map((f) => esc(f.text)).join(", ")}. Check these against the tables.</div>` : `<div class="traced ok">✓ All ${figs.length} figures in this answer match a query result</div>`) : ""}
    ${(t.trace || []).length ? `<details class="how"><summary>How this was computed (${t.trace.length} step${t.trace.length === 1 ? "" : "s"}) · ${esc(t.model || "")}</summary>${t.trace.map((s) => `<div style="margin-top:6px"><b>${esc(s.tool)}</b> ${esc(s.title || "")}${s.rows != null ? ` · ${s.rows} rows` : ""}</div>${s.sql ? `<pre>${esc(s.sql)}</pre>` : s.args ? `<pre>${esc(JSON.stringify(s.args, null, 1))}</pre>` : ""}`).join("")}</details>` : ""}</div></div>`;
}
const fmtCell = (v, col) => (typeof v === "number" ? (/(inr|value|price|saving|total)/i.test(col) ? nf(Math.abs(v) >= 1000 ? 0 : 2).format(v) : nf(Number.isInteger(v) ? 0 : 2).format(v)) : esc(v ?? ""));
function tableHtml(cols, rows) {
  return `<div class="tablewrap" style="max-height:440px"><table><tr>${cols.map((c) => `<th>${esc(c.replace(/_/g, " "))}</th>`).join("")}</tr>${rows.map((r) => `<tr>${r.map((v, i) => `<td class="${typeof v === "number" ? "num" : ""}">${fmtCell(v, cols[i])}</td>`).join("")}</tr>`).join("")}</table></div>`;
}
function blockHtml(b) {
  if (b.type === "table") return `<div class="block"><div class="btitle">${esc(b.title || "")}</div>${tableHtml(b.columns, b.rows)}</div>`;
  if (b.type === "file") return `<div class="block"><a class="att" href="${b.url}">⬇ ${esc(b.name)}</a></div>`;
  if (b.type === "chart") return `<div class="block"><div class="btitle">${esc(b.title || "")}</div>${chartSvg(b)}</div>`;
  if (b.type === "scenario") {
    const s = b.scenario; const unv = s.value_on_assumed_prices_inr + s.value_on_needs_review_prices_inr;
    return `<div class="block"><div class="btitle">${esc(b.title || "Award scenario")}</div><div class="kpis">
      <div class="kpi"><div class="l">Annual value</div><div class="v">${big(s.total_annual_value_inr)}</div></div>
      <div class="kpi"><div class="l">vs last year, same lines</div><div class="v">${s.saving_vs_last_year_inr >= 0 ? "−" : "+"}${big(Math.abs(s.saving_vs_last_year_inr))} (${Math.abs(s.saving_vs_last_year_pct)}%)</div></div>
      <div class="kpi ${s.lines_uncovered.length ? "bad" : ""}"><div class="l">Lines covered</div><div class="v">${s.lines_covered} of ${s.lines_covered + s.lines_uncovered.length}</div></div>
      <div class="kpi ${s.value_on_needs_review_prices_inr ? "bad" : unv ? "warn" : ""}"><div class="l">Resting on unverified prices</div><div class="v">${big(unv)}</div></div></div>
      ${tableHtml(["vendor", "lines", "annual_value_inr", "share_pct"], s.by_vendor.map((v) => [v.vendor_name, v.lines, v.annual_value_inr, v.share_pct]))}
      ${(() => { const h = s.prices_held_back_pending_review || []; if (!h.length) return ""; const g = {};
        h.forEach((x) => { const k = x.vendor_name.split(" ").slice(0, 2).join(" "); (g[k] = g[k] || []).push(x); });
        return `<div class="flagbox needs_review" style="margin-top:8px">${h.length} price${h.length === 1 ? " was" : "s were"} held back from this award because ${h.length === 1 ? "it needs" : "they need"} review: ${Object.entries(g).map(([k, v]) => `<b>${esc(k)}</b> ${v.length <= 3 ? v.map((x) => `${x.line_id} at ₹${x.unit_price_inr}`).join(", ") : v.length + " lines"} (${esc([...new Set(v.flatMap((x) => x.why.split("; ")))].join(", ").toLowerCase().replace(/_/g, " "))})`).join("; ")}. Resolve them on the Compare screen and they compete.</div>`; })()}
      <details class="how"><summary>Line-by-line allocation (${s.allocation.length} lines)${s.lines_uncovered.length ? ` · uncovered: ${s.lines_uncovered.join(", ")}` : ""}</summary>
      ${tableHtml(["line", "description", "awarded_to", "unit_price_inr", "annual_value_inr", "price_status", "next_best", "next_best_price_inr"], s.allocation.map((a) => [a.line_id, a.description, a.vendor_name, a.unit_price_inr, a.annual_value_inr, a.status, a.next_best_vendor, a.next_best_price_inr]))}</details></div>`;
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
