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

app = FastAPI(title="Level")
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
            "outbox": db.rows("SELECT * FROM outbox ORDER BY id DESC"), "model": llm.model_name(), "settings": db.settings(),
            "responses": db.rows("SELECT vendor_id, status, error, model, extracted_at, email FROM responses"),
            "audit": db.rows("SELECT * FROM audit ORDER BY id DESC LIMIT 60")}


@app.post("/api/copilot")
def copilot_chat(m: Msg):
    return copilot.chat(m.message)


@app.post("/api/rfx/load_demo")
def load_demo():
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
    rfx["status"] = "sent"; db.put("rfx", rfx); db.audit("rfx_sent", f"to {len(rfx['vendors'])} vendors")
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


@app.post("/api/decision")
def decision(d: Decision):
    if d.action == "clear":
        db.run("DELETE FROM overrides WHERE vendor_id=? AND line_id=?", (d.vendor_id, d.line_id))
    else:
        db.run("INSERT INTO overrides(vendor_id,line_id,action,price,note,ts) VALUES(?,?,?,?,?,?) ON CONFLICT(vendor_id,line_id) DO UPDATE SET "
               "action=excluded.action, price=excluded.price, note=excluded.note, ts=excluded.ts",
               (d.vendor_id, d.line_id, d.action, d.price, d.note, time.time()))
    db.audit("buyer_decision", f"{d.vendor_id}/{d.line_id}: {d.action}" + (f" {d.price}" if d.price is not None else "") + (f" ({d.note})" if d.note else ""))
    return {"ok": True}


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
    unanswered = [q for q in v["questionnaire"] if q["mandatory"] and q["result"] == "unknown"]
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
    cells = {(c["vendor_id"], c["line_id"]): c for c in comp["cells"]}
    for l in comp["rfx"]["lines"]:
        ws.append([l["id"], l["description"], l["uom"], l["annual_qty"], l.get("last_year_price")])
        r = ws.max_row
        for i, v in enumerate(comp["vendors"]):
            c = cells[(v["id"], l["id"])]
            cell = ws.cell(row=r, column=6 + i, value=round(c["price"], 4) if c["price"] is not None else "not quoted")
            if fills.get(c["status"]):
                cell.fill = fills[c["status"]]
            notes = c["steps"] + [f"[{f['severity']}] {f['text']}" for f in c["flags"]]
            if notes:
                cell.comment = Comment("\n".join(notes)[:1500], "Level")
    ws.append([]); ws.append(["Yellow = depends on a stated assumption. Red = needs review. Grey = not quoted. Hover a cell for how the number was derived."])
    for col, w in zip("ABCDE", (7, 46, 8, 12, 14)):
        ws.column_dimensions[col].width = w
    for i in range(len(comp["vendors"])):
        ws.column_dimensions[ws.cell(row=1, column=6 + i).column_letter].width = 24
    fs = wb.create_sheet("Flags")
    fs.append(["Vendor", "Line", "Status", "Code", "What to know"])
    for c in comp["cells"]:
        for f in c["flags"]:
            fs.append([c["vendor_id"], c["line_id"], c["status"], f["code"], f["text"]])
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
