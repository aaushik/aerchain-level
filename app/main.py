"""HTTP surface. The mail server is stubbed (outbox table, inbox folder); everything a model does is real."""
import io
import json
import shutil
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill
from pydantic import BaseModel

from . import analyst, copilot, db, extract, llm, normalize

app = FastAPI(title="RFx Assistant")
WEB = Path(__file__).resolve().parent.parent / "web"


class Msg(BaseModel):
    message: str


class Decision(BaseModel):
    vendor_id: str
    line_id: str
    action: str                      # confirm | set_price | exclude | clear
    price: float | None = None
    note: str | None = None


class Settings(BaseModel):
    fx: dict | None = None
    vendor_id: str | None = None
    apply_discount: bool | None = None
    freight_pct: float | None = None


def need_rfx():
    rfx = db.get("rfx")
    if not rfx:
        raise HTTPException(400, "No RFx yet")
    return rfx


@app.get("/api/state")
def state():
    return {"rfx": db.get("rfx"), "copilot_chat": db.get("copilot_chat", []), "analyst_chat": db.get("analyst_chat", []),
            "outbox": db.rows("SELECT * FROM outbox ORDER BY id DESC"), "model": llm.primary(), "primary_model": llm.primary(), "settings": db.settings(),
            "replies": db.rows("SELECT * FROM replies ORDER BY id DESC"),
            "responses": db.rows("SELECT vendor_id, status, error, model, extracted_at, email FROM responses"),
            "audit": db.rows("SELECT * FROM audit ORDER BY id DESC LIMIT 60")}


@app.post("/api/copilot")
def copilot_chat(m: Msg):
    return copilot.chat(m.message)


@app.post("/api/rfx/load_demo")
def load_demo():
    if (db.get("rfx") or {}).get("status") == "sent":
        raise HTTPException(409, "This RFx has been sent. Reset to start a new draft.")
    rfx = db.seed_rfx(); rfx["status"] = "draft"
    db.put("rfx", rfx); db.audit("rfx_loaded", "Demo RFx loaded without the co-pilot")
    return rfx


SNAPSHOT = db.SEED / "snapshot.json"


@app.post("/api/demo/snapshot")
def save_snapshot():
    """Save the current real extraction run so the demo can open without waiting on (or paying for) model calls."""
    rows = db.rows("SELECT vendor_id, extraction, source_text, model, extracted_at FROM responses WHERE status='extracted'")
    SNAPSHOT.write_text(json.dumps({"saved_at": time.time(), "responses": rows}, ensure_ascii=False))
    return {"saved": len(rows)}


@app.post("/api/demo/restore")
def restore_snapshot():
    """Demo RFx, sent, with the five replies already read. Extractions are the cached output of a real run; 'Read again' redoes any of them live."""
    if not SNAPSHOT.exists():
        raise HTTPException(404, "No snapshot saved")
    db.reset()
    load_demo(); send(); simulate()
    snap = json.loads(SNAPSHOT.read_text())
    for r in snap["responses"]:
        db.run("UPDATE responses SET status='extracted', extraction=?, source_text=?, model=?, extracted_at=? WHERE vendor_id=?",
               (r["extraction"], r["source_text"], r["model"], r["extracted_at"], r["vendor_id"]))
    db.audit("snapshot_restored", f"{len(snap['responses'])} responses, extracted earlier by a live model run")
    return {"restored": len(snap["responses"])}


@app.post("/api/rfx/send")
def send():
    rfx = need_rfx()
    if rfx.get("status") == "sent":
        raise HTTPException(409, "Already sent")
    if not rfx["lines"] or not rfx["vendors"]:
        raise HTTPException(400, "Add line items and vendors before sending")
    lines = "\n".join(f"{l['id']}  {l['description']}  |  {l['board_spec']}  |  {l['annual_qty']:,} {l['uom']}/yr" for l in rfx["lines"])
    qs = "\n".join(f"{q['id']}. {q['text']}" for q in rfx["questionnaire"])
    terms = "\n".join(f"- {t}" for t in rfx["terms"])
    for v in rfx["vendors"]:
        body = (f"Dear {v['name']} team,\n\n{rfx['buyer']['company']} invites your quotation for: {rfx['title']}.\n\n{rfx['scope']}\n\n"
                f"Please reply to this email by {rfx.get('due_date')}. Use any format you like: we will read it.\n\n"
                f"LINE ITEMS\n{lines}\n\nQUESTIONNAIRE\n{qs}\n\nTERMS\n{terms}\n\nRegards,\n{rfx['buyer']['name']}\n{rfx['buyer']['role']}")
        db.run("INSERT INTO outbox(vendor_id,kind,subject,body,ts) VALUES(?,?,?,?,?)",
               (v["id"], "rfx", f"{rfx['id']}: {rfx['title']}", body, time.time()))
    rfx["status"] = "sent"; rfx["sent_at"] = time.strftime("%Y-%m-%d %H:%M"); db.put("rfx", rfx); db.audit("rfx_sent", f"to {len(rfx['vendors'])} vendors")
    return {"ok": True}


def _register(vendor_id, folder: Path):
    email = json.loads((folder / "email.json").read_text())
    db.run("INSERT INTO responses(vendor_id,email,dir,status) VALUES(?,?,?,'received') ON CONFLICT(vendor_id) DO UPDATE SET "
           "email=excluded.email, dir=excluded.dir, status='received', extraction=NULL, error=NULL", (vendor_id, json.dumps(email), str(folder)))


@app.post("/api/inbox/simulate")
def simulate():
    """Stub for the mail server: the five fabricated replies land in the inbox."""
    rfx = need_rfx()
    n = 0
    for v in rfx["vendors"]:
        folder = db.REPLIES / v["id"]
        if (folder / "email.json").exists() and not db.rows("SELECT 1 FROM responses WHERE vendor_id=?", (v["id"],)):
            _register(v["id"], folder); n += 1
    db.audit("replies_received", f"{n} vendor replies")
    return {"received": n}


@app.post("/api/inbox/upload")
async def upload(vendor_id: str = Form(...), body: str = Form(""), files: list[UploadFile] = File(default=[])):
    rfx = need_rfx()
    v = next((x for x in rfx["vendors"] if x["id"] == vendor_id), None)
    if not v:
        raise HTTPException(404, "Unknown vendor")
    folder = db.UPLOADS / f"{vendor_id}_{int(time.time())}"
    folder.mkdir(parents=True)
    names = []
    for f in files:
        name = Path(f.filename or "file").name
        (folder / name).write_bytes(await f.read()); names.append(name)
    (folder / "email.json").write_text(json.dumps({
        "vendor_id": vendor_id, "from_name": v["name"], "from": v["email"], "to": rfx["buyer"]["email"],
        "subject": f"Re: {rfx['id']}", "date": time.strftime("%Y-%m-%dT%H:%M:%S+05:30"), "body": body, "attachments": names}))
    _register(vendor_id, folder)
    db.run("DELETE FROM overrides WHERE vendor_id=?", (vendor_id,))
    db.run("DELETE FROM replies WHERE vendor_id=?", (vendor_id,))         # a new response starts a new thread
    rl = db.get("relevel", {}); rl.pop(vendor_id, None); db.put("relevel", rl)
    db.audit("reply_uploaded", f"{vendor_id}: {', '.join(names) or 'email text only'}")
    return {"ok": True}


@app.post("/api/extract/{vendor_id}")
def run_extract(vendor_id: str, force: bool = False):
    try:
        data = extract.extract_vendor(vendor_id, need_rfx(), force=force)
    except Exception as e:
        raise HTTPException(502, f"Extraction failed: {e}")
    return {"ok": True, "items": len(data["items"])}


@app.get("/api/comparison")
def comparison():
    return normalize.build()


class GroupDecision(BaseModel):
    vendor_id: str
    cause: str | None = None         # the headline reason code shared by the cells (WEIGHT, SCOPE, ...)
    action: str                      # confirm | use_suggested | undo
    note: str | None = None
    grp: str | None = None           # for undo
    lines: list[str] | None = None   # optional: only these lines of the group (the buyer's selection)


def _cell(comp, vendor_id, line_id):
    return next((c for c in comp["cells"] if c["vendor_id"] == vendor_id and c["line_id"] == line_id), None)


KEEP_NEEDS_REASON = "This price needs review. To keep it as it is, give a reason (it goes in the decision log)."


@app.post("/api/decision")
def decision(d: Decision):
    if d.action == "confirm":
        c = _cell(normalize.build(), d.vendor_id, d.line_id)
        if c and c.get("machine_status") == "needs_review" and not (d.note or "").strip():
            raise HTTPException(400, KEEP_NEEDS_REASON)
    if d.action == "clear":
        db.run("DELETE FROM overrides WHERE vendor_id=? AND line_id=?", (d.vendor_id, d.line_id))
    else:
        db.run("INSERT INTO overrides(vendor_id,line_id,action,price,note,ts,grp) VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(vendor_id,line_id) DO UPDATE SET "
               "action=excluded.action, price=excluded.price, note=excluded.note, ts=excluded.ts, grp=NULL",
               (d.vendor_id, d.line_id, d.action, d.price, d.note, time.time()))
    db.audit("buyer_decision", f"{d.vendor_id}/{d.line_id}: {d.action}" + (f" {d.price}" if d.price is not None else "") + (f" ({d.note})" if d.note else ""))
    return {"ok": True}


@app.post("/api/decision/group")
def group_decision(g: GroupDecision):
    """One decision for every open cell of a vendor that shares a leading reason. One transaction, one audit entry, one undo.
    Never bulk-keeps a price that has a suggested correction, and never keeps needs-review prices without a reason."""
    comp = normalize.build()
    v = next((x for x in comp["vendors"] if x["id"] == g.vendor_id), None)
    if not v:
        raise HTTPException(404, "Unknown vendor")
    if g.action == "undo":
        rows = db.rows("SELECT line_id FROM overrides WHERE grp=?", (g.grp,)) if g.grp else []
        if not rows:
            raise HTTPException(404, "Nothing to undo for that group")
        db.run("DELETE FROM overrides WHERE grp=?", (g.grp,))
        db.audit("group_decision_undone", f"{v['short']}: undid the group decision on {len(rows)} lines ({_span([r['line_id'] for r in rows])})")
        return {"ok": True, "undone": len(rows)}
    cells = [c for c in comp["cells"] if c["vendor_id"] == g.vendor_id and not c.get("decision") and c["price"] is not None
             and (c.get("headline") or {}).get("code") == g.cause and c["cell_status"] in ("assumed", "needs_review")]
    pool = len(cells)
    if g.lines is not None:                              # a selection must sit inside the group, every id of it
        ids = {c["line_id"] for c in cells}
        bad = [lid for lid in g.lines if lid not in ids]
        if bad or not g.lines:
            cause = normalize.CAUSE.get(g.cause, g.cause).lower()
            raise HTTPException(400, f"Not open lines of {v['short']} with this cause ({cause}): {', '.join(bad) or 'none selected'}. "
                                     "Choose from the lines listed under the issue.")
        cells = [c for c in cells if c["line_id"] in set(g.lines)]
    if g.action == "confirm":
        skipped = [c["line_id"] for c in cells if (c.get("headline") or {}).get("suggested_price") is not None]
        cells = [c for c in cells if c["line_id"] not in skipped]
        if any(c["machine_status"] == "needs_review" for c in cells) and not (g.note or "").strip():
            raise HTTPException(400, KEEP_NEEDS_REASON)
        rows = [(c["line_id"], "confirm", None) for c in cells]
        verb = "kept the price" if any(c["machine_status"] == "needs_review" for c in cells) else "accepted"
    elif g.action == "use_suggested":
        skipped = [c["line_id"] for c in cells if (c.get("headline") or {}).get("suggested_price") is None]
        rows = [(c["line_id"], "set_price", c["headline"]["suggested_price"]) for c in cells if c["line_id"] not in skipped]
        verb = "used the suggested price"
    else:
        raise HTTPException(400, "Unknown group action")
    if not rows:
        raise HTTPException(400, "No open lines for this vendor and reason" + (f" ({', '.join(skipped)} {'has a suggested price' if len(skipped) == 1 else 'have suggested prices'}: use the suggestion, or decide each on its own)" if skipped else ""))
    grp, now = f"g{time.time_ns()}", time.time()
    db.many([("INSERT INTO overrides(vendor_id,line_id,action,price,note,ts,grp) VALUES(?,?,?,?,?,?,?) ON CONFLICT(vendor_id,line_id) DO UPDATE SET "
              "action=excluded.action, price=excluded.price, note=excluded.note, ts=excluded.ts, grp=excluded.grp",
              (g.vendor_id, lid, act, price, g.note, now, grp)) for lid, act, price in rows])
    cause = normalize.CAUSE.get(g.cause, g.cause).lower()
    of = f" of {pool}" if len(rows) < pool else ""
    db.audit("group_decision", f"{v['short']}: {verb} ({cause}) for {len(rows)}{of} line{'s' if len(rows) + len(of) > 1 else ''} ({_span([r[0] for r in rows])})"
             + (f"; reason: {g.note}" if g.note else "") + (f"; skipped {', '.join(skipped)}" if skipped else ""))
    return {"ok": True, "grp": grp, "lines": [r[0] for r in rows], "skipped": skipped}


def _span(ids):
    """L01, L02, L03, L05 -> 'L01 to L03, L05': runs of consecutive lines shown as ranges."""
    ids = sorted(ids)
    num = lambda x: int(x[1:]) if x[1:].isdigit() else None
    out, run = [], []
    for x in ids:
        if run and num(x) is not None and num(run[-1]) is not None and num(x) == num(run[-1]) + 1:
            run.append(x); continue
        if run:
            out.append(run)
        run = [x]
    if run:
        out.append(run)
    return ", ".join(r[0] if len(r) == 1 else f"{r[0]}, {r[1]}" if len(r) == 2 else f"{r[0]} to {r[-1]}" for r in out)


@app.post("/api/settings")
def set_settings(s: Settings):
    cur = db.settings()
    if s.fx:
        cur["fx"].update({k: float(v) for k, v in s.fx.items()}); db.audit("fx_changed", json.dumps(s.fx))
    if s.vendor_id:
        vs = cur["vendors"].setdefault(s.vendor_id, {})
        if s.apply_discount is not None:
            vs["apply_discount"] = s.apply_discount
        if s.freight_pct is not None:
            vs["freight_pct"] = s.freight_pct or None
        db.audit("vendor_adjustment", f"{s.vendor_id}: {json.dumps(vs)}")
    db.put("settings", cur)
    return cur


@app.post("/api/clarify/{vendor_id}")
def clarify(vendor_id: str):
    """Drafts the chaser a buyer would otherwise write by hand, from the open flags. Lands in the (stubbed) outbox."""
    comp = normalize.build()
    v = next((x for x in comp["vendors"] if x["id"] == vendor_id), None)
    if not v:
        raise HTTPException(404, "Unknown vendor")
    lines = {l["id"]: l for l in comp["rfx"]["lines"]}
    pts = [a["question_for_vendor"] for a in v["ambiguities"] if a.get("question_for_vendor")]
    if any(f["code"] in ("FREIGHT", "FREIGHT_UNCLEAR") for f in v["flags"]):
        pts.append("Please state freight to our Bhiwandi FC as an amount or a percentage, or requote on a delivered basis, so your prices can be compared with the others.")
    by_code = {}
    for c in comp["cells"]:
        if c["vendor_id"] == vendor_id and not (c.get("decision") or {}).get("action"):
            for f in c["flags"]:
                by_code.setdefault(f["code"], []).append(c["line_id"])
    ask = {"WEIGHT": "You quoted per kg. Please confirm the weight per piece you will invoice on, or restate the price per piece, for lines: ",
           "LAST_YEAR": "You wrote \"same as last year\". Please restate the price per unit in writing for lines: ",
           "TOTAL_MISMATCH": "Your unit rate and your line total do not agree. Please confirm the rate and its unit basis for lines: ",
           "OUTLIER": "Your price is far from the other quotes received. Please confirm the rate and its unit basis for lines: ",
           "SCOPE": "It is not clear which items your group rate or \"same as last year\" covers. Please confirm the price for lines: ",
           "READ": "We could not read these prices with confidence. Please resend them as text for lines: ",
           "PHOTO": "Your rates reached us as a photograph. Please confirm them in text (a reply in the email body is enough) for lines: ",
           "SPEC": "You proposed a different specification. Please also quote the specification as requested for lines: ",
           "FX": "You quoted in foreign currency. Please confirm whether you can hold an INR price, or the rate basis for invoicing, for lines: "}
    for code, text in ask.items():
        if code in by_code:
            ids = by_code[code]
            pts.append(text + (", ".join(ids) if len(ids) <= 8 else f"{len(ids)} lines ({ids[0]} to {ids[-1]})") + ".")
    missing = [c["line_id"] for c in comp["cells"] if c["vendor_id"] == vendor_id and c["status"] == "not_quoted"]
    if missing and len(missing) <= 10:
        pts.append("You did not quote: " + ", ".join(f"{m} ({lines[m]['description']})" for m in missing) + ". Please confirm this is intended.")
    for q in v["questionnaire"]:
        if q.get("missing") == "certificate_copy":
            pts.append(f"For \"{q['question']}\" you answered yes" + (f" ({q['answer']})" if q.get("answer") else "")
                       + ". Please attach a copy of the certificate; we cannot clear this requirement on a statement alone.")
    unanswered = [q for q in v["questionnaire"] if q["mandatory"] and q["result"] == "unknown" and not q.get("missing")]
    if unanswered:
        pts.append("We need answers to these mandatory questions before we can consider your quote: " + " / ".join(q["question"] for q in unanswered))
    if not pts:
        return {"ok": False, "message": "Nothing to clarify with this vendor."}
    seen, uniq = set(), []
    for p in pts:
        if p not in seen:
            seen.add(p); uniq.append(p)
    body = (f"Dear {v['name']} team,\n\nThank you for your response to {comp['rfx']['id']}. Before we can evaluate it we need the following confirmed:\n\n"
            + "\n".join(f"{i}. {p}" for i, p in enumerate(uniq, 1)) +
            f"\n\nA reply in the body of this email is fine.\n\nRegards,\n{comp['rfx']['buyer']['name']}")
    db.run("INSERT INTO outbox(vendor_id,kind,subject,body,ts) VALUES(?,?,?,?,?)",
           (vendor_id, "clarification", f"{comp['rfx']['id']}: clarifications needed", body, time.time()))
    db.audit("clarification_drafted", f"{vendor_id}: {len(uniq)} points")
    return {"ok": True, "body": body, "points": len(uniq)}


@app.get("/api/reply/sample/{vendor_id}")
def sample_reply(vendor_id: str):
    """A ready-made vendor answer to the clarification email, for the demo. The stubbed inbox for follow-ups."""
    p = db.REPLIES / Path(vendor_id).name / "clarification_reply.json"
    if not p.exists():
        raise HTTPException(404, "No sample reply for this vendor")
    return json.loads(p.read_text())


@app.post("/api/reply/{vendor_id}")
def record_reply(vendor_id: str, m: Msg):
    """The vendor answered a clarification. Store it on the vendor's thread, have the model re-read the whole thread
    (first response + our questions + this reply), and re-level. What changed is kept so the grid can show it."""
    rfx = need_rfx()
    if not m.message.strip():
        raise HTTPException(400, "Paste the vendor's reply first")
    if not db.rows("SELECT 1 FROM responses WHERE vendor_id=? AND extraction IS NOT NULL", (vendor_id,)):
        raise HTTPException(400, "Read this vendor's first response before recording a reply")
    if not db.rows("SELECT 1 FROM outbox WHERE vendor_id=? AND kind='clarification'", (vendor_id,)):
        raise HTTPException(400, "Draft a clarification email to this vendor first")
    snap = lambda comp: ({c["line_id"]: c for c in comp["cells"] if c["vendor_id"] == vendor_id},
                         next(v for v in comp["vendors"] if v["id"] == vendor_id))
    before, bv = snap(normalize.build())
    db.run("INSERT INTO replies(vendor_id,body,ts) VALUES(?,?,?)", (vendor_id, m.message.strip(), time.time()))
    db.audit("vendor_reply_recorded", f"{vendor_id}: {len(m.message.strip())} characters, stubbed inbox")
    try:
        extract.extract_vendor(vendor_id, rfx, force=True)
    except Exception as e:
        raise HTTPException(502, f"Reply saved, but re-reading the thread failed: {e}. Use 'Read again' on the Responses screen.")
    after, av = snap(normalize.build())
    r4 = lambda x: None if x is None else round(x, 4)
    changed = {lid: {"before_price": r4(b["price"]), "before_status": b["status"], "after_price": r4(after[lid]["price"]), "after_status": after[lid]["status"]}
               for lid, b in before.items() if (r4(b["price"]), b["status"]) != (r4(after[lid]["price"]), after[lid]["status"])}
    rec = {"ts": time.time(), "changed": changed, "model": llm.model_name(),
           "needs_review": [bv["counts"]["needs_review"], av["counts"]["needs_review"]], "quality": [bv["quality"], av["quality"]],
           "freight_held": [bv["freight_held"], av["freight_held"]]}
    rl = db.get("relevel", {}); rl[vendor_id] = rec; db.put("relevel", rl)
    db.audit("relevelled", f"{vendor_id}: {len(changed)} prices changed after the vendor's reply; likely wrong {rec['needs_review'][0]} → {rec['needs_review'][1]}; "
             + (f"quality {rec['quality'][0]} → {rec['quality'][1]}" if rec["quality"][0] != rec["quality"][1] else f"quality still {rec['quality'][1]}") + ("; freight now stated, prices compete in award scenarios" if bv["freight_held"] and not av["freight_held"] else ""))
    return rec


@app.post("/api/analyst")
def analyst_ask(m: Msg):
    try:
        return analyst.ask(m.message)
    except Exception as e:
        raise HTTPException(502, f"Analyst failed: {e}")


@app.post("/api/analyst/reset")
def analyst_reset():
    db.put("analyst_chat", [])
    return {"ok": True}


@app.get("/api/file/{vendor_id}/{name}")
def vendor_file(vendor_id: str, name: str):
    r = db.rows("SELECT dir FROM responses WHERE vendor_id=?", (vendor_id,))
    if not r:
        raise HTTPException(404)
    p = Path(r[0]["dir"]) / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name, content_disposition_type="inline")


@app.get("/api/source/{vendor_id}")
def source_text(vendor_id: str):
    r = db.rows("SELECT source_text, email FROM responses WHERE vendor_id=?", (vendor_id,))
    if not r:
        raise HTTPException(404)
    return {"source": json.loads(r[0]["source_text"] or "{}"), "email": json.loads(r[0]["email"])}


@app.get("/api/export/comparison.xlsx")
def export_comparison():
    comp = normalize.build()
    if not comp:
        raise HTTPException(400, "No RFx yet")
    wb = Workbook(); ws = wb.active; ws.title = "Comparison"
    fills = {"verified": None, "assumed": PatternFill("solid", fgColor="FFF1C2"), "needs_review": PatternFill("solid", fgColor="F8C9C4"),
             "not_quoted": PatternFill("solid", fgColor="E6E6E6"), "no_response": PatternFill("solid", fgColor="E6E6E6")}
    head = ["Line", "Description", "UoM", "Annual qty", "Last year (INR)"] + [v["name"] for v in comp["vendors"]]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.append(["", "Applies to every price in the column", "", "", ""] + ["\n".join([f"[assumed] {w['text']}" for w in v.get("wide_assumptions", [])]
              + (["[held back from awards] Freight extra, no amount stated"] if v.get("freight_held") else [])) for v in comp["vendors"]])
    for c in ws[2]:
        c.font = Font(italic=True); c.fill = PatternFill("solid", fgColor="FFF1C2") if c.value and c.column > 5 else PatternFill()
    cells = {(c["vendor_id"], c["line_id"]): c for c in comp["cells"]}
    for l in comp["rfx"]["lines"]:
        ws.append([l["id"], l["description"], l["uom"], l["annual_qty"], l.get("last_year_price")])
        r = ws.max_row
        for i, v in enumerate(comp["vendors"]):
            c = cells[(v["id"], l["id"])]
            cell = ws.cell(row=r, column=6 + i, value=round(c["price"], 4) if c["price"] is not None else "not quoted")
            if fills.get(c.get("cell_status", c["status"])):
                cell.fill = fills[c.get("cell_status", c["status"])]
            notes = c["steps"] + [f"[{f['severity']}] {f['text']}" for f in c["flags"]]
            if notes:
                cell.comment = Comment("\n".join(notes)[:1500], "RFx Assistant")
    ws.append([]); ws.append(["Legend: white = Clear. Yellow = Check assumption (rests on an assumption about that price). Red = Likely wrong "
                              "(probably wrong or not like-for-like). Grey = Not quoted. Assumptions that apply to a vendor's whole column are in row 2, "
                              "not coloured per cell. Hover a cell for how the number was derived; the Flags sheet lists every assumption, vendor-wide ones included."])
    for col, w in zip("ABCDE", (7, 46, 8, 12, 14)):
        ws.column_dimensions[col].width = w
    for i in range(len(comp["vendors"])):
        ws.column_dimensions[ws.cell(row=1, column=6 + i).column_letter].width = 24
    fs = wb.create_sheet("Flags")
    fs.append(["Vendor", "Line", "Status", "Code", "Scope", "What to know"])
    names = {v["id"]: v["name"] for v in comp["vendors"]}
    for v in comp["vendors"]:
        for f in v["flags"]:
            fs.append([v["name"], "(all lines)", f["severity"], f["code"], "vendor terms", f["text"]])
    for c in comp["cells"]:
        for f in c["flags"]:
            fs.append([names[c["vendor_id"]], c["line_id"], c["status"], f["code"], "every price from this vendor" if f.get("vendor_wide") else "this cell", f["text"]])
    qs = wb.create_sheet("Questionnaire")
    qs.append(["Vendor", "Overall", "Question", "Mandatory", "Answer", "Result", "Note"])
    for v in comp["vendors"]:
        for q in v["questionnaire"]:
            qs.append([v["name"], v["quality"], q["question"], "yes" if q["mandatory"] else "no", q["answer"], q["result"], q["note"]])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": 'attachment; filename="comparison.xlsx"'})


@app.get("/exports/{name}")
def export_file(name: str):
    p = db.EXPORTS / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)


@app.post("/api/reset")
def reset():
    db.reset()
    shutil.rmtree(db.UPLOADS, ignore_errors=True)
    return {"ok": True}


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
